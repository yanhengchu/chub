from __future__ import annotations

import asyncio
import logging
import os
import signal
import stat
import subprocess
import threading
import time
from contextlib import asynccontextmanager
from contextlib import suppress
from uuid import uuid4

import httpx
from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from app.ai_usage.service import AiUsageService
from app.api.ai_usage import router as ai_usage_router
from app.api.health import router as health_router
from app.api.automations import router as automations_router
from app.api.logs import router as logs_router
from app.api.maintenance import (
    router as maintenance_router,
    start_system_upgrade_for_source,
)
from app.api.notifications import router as notifications_router
from app.api.openclaw import router as openclaw_router
from app.api.openclaw_wechat_chub_mode import (
    require_local_openclaw,
    router as openclaw_wechat_chub_mode_router,
)
from app.api.project_documents import router as project_documents_router
from app.api.weekly_reports import router as weekly_reports_router
from app.api.settings import router as settings_router
from app.api.status import router as status_router
from app.ai_session import AiSessionManager
from app.ai_session.operations import archive_session, delete_session
from app.codex.quick_interactions import QuickInteractionManager
from app.codex.rate_limits import CodexRateLimitService
from app.codex.routes import api_router as codex_api_router
from app.codex.routes import web_router as codex_web_router
from app.automations.manager import AutomationManager
from app.core.config import PROJECT_ROOT, Settings, load_settings
from app.core.logger import configure_logging
from app.core.network import is_tailscale_ip
from app.core.security import require_trusted_network
from app.core.platform import detect_platform
from app.core.response import (
    ApiError,
    SECURITY_HEADERS,
    api_error_handler,
    http_error_handler,
    internal_error_handler,
    validation_error_handler,
    error_response,
)
from app.core.build_info import SESSION_SCHEMA_VERSION, WEB_CODE_VERSION
from app.services.openclaw import OpenClawManager
from app.services.openclaw_completion_notifications import OpenClawCompletionNotifier
from app.services.openclaw_weixin_chub_messages import usage_message
from app.services.operation_log import write_operation
from app.services.deferred_restart import DeferredRestartCoordinator
from app.services.openclaw_weixin_chub_mode import WeixinChubModeManager
from app.services.restart_command import RestartProcess, launch_restart_process
from app.services.quick_worker_maintenance import (
    QuickWorkerReloadCoordinator,
    inspect_quick_worker,
)
from app.services.system_status import collect_system_status
from app.services.weixin_translation import WeixinTranslationManager
from app.services.system_upgrade import (
    SystemUpgradeBusy,
    SystemUpgradeCoordinator,
    SystemUpgradeSession,
    runtime_cleanup_readiness,
    runtime_recovery_plan,
    system_upgrade_restart_readiness,
)
from app.quick_worker import read_health, read_health_sync, resume_after_drain
from app.notifications import NotificationService
from app.web.routes import STATIC_DIR, router as web_router


async def _confirm_healthy_instance(
    settings: Settings,
    instance_id: str,
) -> None:
    logger = logging.getLogger("hub.deferred_restart")
    health_url = httpx.URL(
        scheme="http",
        host="127.0.0.1",
        port=settings.server.port,
        path="/api/health",
    )
    attempts = 0
    async with httpx.AsyncClient(timeout=2) as client:
        while True:
            attempts += 1
            try:
                response = await client.get(health_url)
                payload = response.json()
                if (
                    response.status_code == 200
                    and isinstance(payload, dict)
                    and isinstance(payload.get("data"), dict)
                    and payload["data"].get("status") == "ok"
                    and payload["data"].get("instance_id") == instance_id
                ):
                    return
            except (httpx.HTTPError, ValueError):
                pass
            if attempts % 40 == 0:
                logger.warning(
                    "Waiting for healthy Chub instance before completing deferred restart"
                )
            await asyncio.sleep(0.25)


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next) -> Response:
        response = await call_next(request)
        response.headers.update(SECURITY_HEADERS)
        if (
            request.url.path == "/"
            or request.url.path.startswith("/static/")
            or request.url.path.startswith("/project-docs")
            or request.url.path.startswith("/automations")
            or request.url.path == "/settings"
        ):
            response.headers["Content-Security-Policy"] = (
                "default-src 'self'; "
                "script-src 'self'; "
                "style-src 'self'; "
                "connect-src 'self'; "
                "img-src 'self' data: blob:; "
                "object-src 'none'; "
                "base-uri 'none'; "
                "frame-ancestors 'none'"
            )
        elif request.url.path.startswith("/codex/"):
            if "/terminal" in request.url.path:
                response.headers["Content-Security-Policy"] = (
                    "default-src 'self' data: blob:; "
                    "script-src 'self' 'unsafe-inline'; "
                    "style-src 'self' 'unsafe-inline'; "
                    "connect-src 'self' ws: wss:; "
                    "img-src 'self' data:; "
                    "font-src 'self' data:; "
                    "frame-ancestors 'self'; "
                    "object-src 'none'"
                )
            else:
                response.headers["Content-Security-Policy"] = (
                    "default-src 'self'; "
                    "style-src 'self'; "
                    "frame-src 'self'; "
                    "frame-ancestors 'none'; "
                    "object-src 'none'; "
                    "base-uri 'none'"
                )
        return response


class SystemUpgradeGateMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next) -> Response:
        if not _is_ai_runtime_mutation(request):
            return await call_next(request)
        try:
            with request.app.state.system_upgrade.mutation_guard():
                return await call_next(request)
        except SystemUpgradeBusy:
            try:
                if request.url.path.startswith(
                    "/api/openclaw/wechat-chub-mode/"
                ):
                    require_local_openclaw(request)
                else:
                    require_trusted_network(request)
            except ApiError as error:
                return await api_error_handler(request, error)
            return error_response(
                409,
                "system_upgrade_in_progress",
                "系统升级期间暂不接受新的写入操作。",
            )


def _is_ai_runtime_mutation(request: Request) -> bool:
    if request.method not in {"POST", "PUT", "PATCH", "DELETE"}:
        return False
    path = request.url.path
    return (
        path.startswith("/api/codex/")
        or path.startswith("/api/openclaw/wechat-chub-mode/")
    )


def create_app(settings: Settings | None = None) -> FastAPI:
    resolved_settings = settings or load_settings()
    instance_id = uuid4().hex
    configure_logging(resolved_settings.logs)

    detected_platform = detect_platform()
    logger = logging.getLogger("hub.startup")
    codex_pty_available = is_tailscale_ip(resolved_settings.server.tailnet_host or "")
    if not codex_pty_available:
        logger.warning(
            "server.tailnet_host is not configured; Codex PTY is disabled",
        )
    if resolved_settings.node.type != detected_platform:
        logger.warning(
            "configured_platform=%s detected_platform=%s",
            resolved_settings.node.type,
            detected_platform,
        )
    logger.info(
        "node_id=%s node_name=%s platform=%s version=%s",
        resolved_settings.node.id,
        resolved_settings.node.name,
        detected_platform,
        resolved_settings.app.version,
    )

    # The AI Session Manager is the sole production owner.  The old
    # Codex Session Store is cleaned by the fixed upgrade flow and is never
    # used as a startup-time compatibility switch.
    codex_pty_manager = AiSessionManager(resolved_settings)
    codex_rate_limits = CodexRateLimitService()
    ai_usage = AiUsageService(resolved_settings, codex_rate_limits)
    completion_notifier = OpenClawCompletionNotifier(
        resolved_settings.openclaw.quick_interaction_completion
    )

    def start_deferred_restart() -> RestartProcess:
        command = PROJECT_ROOT / "scripts" / "chub-web-restart"
        if not command.is_file():
            raise OSError("Chub restart command is unavailable")
        return launch_restart_process(command)

    deferred_restart = DeferredRestartCoordinator(
        resolved_settings.codex_pty.data_file.with_name("deferred-restart.json"),
        instance_id,
        start_deferred_restart,
    )
    quick_interactions = QuickInteractionManager(
        resolved_settings.codex_pty.data_file,
        resolved_settings.codex_pty.runtime_dir,
        codex_pty_manager,
        completion_notifier.notify,
        deferred_restart,
        restart_notifier=completion_notifier.notify_restart,
        timeout_seconds=resolved_settings.codex_pty.quick_interaction_timeout_seconds,
        worker_settings=resolved_settings,
    )
    quick_interactions.configure_translation_worker_queue(
        limit=resolved_settings.openclaw.weixin_chub_mode.translation_queue_limit,
        wait_seconds=(
            resolved_settings.openclaw.weixin_chub_mode.translation_max_wait_seconds
        ),
    )
    quick_worker_maintenance = QuickWorkerReloadCoordinator(
        resolved_settings.codex_pty.data_file.with_name(
            "quick-worker-maintenance.json"
        ),
        PROJECT_ROOT / "scripts" / "chub",
    )
    system_upgrade = SystemUpgradeCoordinator(
        resolved_settings.codex_pty.data_file.with_name("system-upgrade.json"),
        PROJECT_ROOT / "config" / "system-upgrade.json",
        instance_id,
    )
    codex_pty_manager.set_system_upgrade_checker(system_upgrade.writes_blocked)
    terminal_tickets = codex_pty_manager.supervisor.tickets
    terminal_connections = codex_pty_manager.supervisor.connections
    weixin_translation = WeixinTranslationManager(
        resolved_settings.openclaw.weixin_chub_mode,
        codex_pty_manager,
        quick_interactions,
    )
    quick_interactions.set_recovery_ready_handler(
        weixin_translation.start_worker_recovery
    )
    def reclaim_weixin_terminal(session_id: str):
        terminal_tickets.revoke_session(session_id)
        terminal_connections.close_session(session_id)
        return codex_pty_manager.stop_session(session_id)

    def release_weixin_session_slot_for_archive(session_id: str) -> bool:
        try:
            # A missing slot is already in the desired state; only an
            # exception means that the release could not be confirmed.
            weixin_chub_mode.release_session_slot(session_id)
        except Exception as exc:
            raise ApiError(
                503,
                "weixin_chub_mode_slot_release_unknown",
                "Session 已完成原生归档，但关联槽位释放状态无法确认，请稍后重试。",
            ) from exc
        return True

    def archive_weixin_session(session_id: str) -> None:
        archive_session(
            session_id,
            manager=codex_pty_manager,
            quick_interactions=quick_interactions,
            terminal_tickets=terminal_tickets,
            terminal_connections=terminal_connections,
            release_slot=release_weixin_session_slot_for_archive,
        )

    def release_weixin_session_slot_for_delete(session_id: str) -> bool:
        try:
            weixin_chub_mode.release_session_slot(session_id)
        except Exception as exc:
            raise ApiError(
                503,
                "weixin_chub_mode_slot_release_unknown",
                "Session 已完成原生删除，但关联槽位释放状态无法确认，请稍后重试。",
            ) from exc
        return True

    def delete_weixin_session(session_id: str) -> None:
        delete_session(
            session_id,
            manager=codex_pty_manager,
            quick_interactions=quick_interactions,
            terminal_tickets=terminal_tickets,
            terminal_connections=terminal_connections,
            release_slot=release_weixin_session_slot_for_delete,
        )

    def stop_weixin_session(session_id: str):
        with quick_interactions.stop_operation_guard(session_id):
            codex_pty_manager.ensure_stop_allowed(session_id)
            quick_interactions.cancel_codex_session(session_id)
            terminal_tickets.revoke_session(session_id)
            terminal_connections.close_session(session_id)
            return codex_pty_manager.stop_session(session_id)

    weixin_chub_mode = WeixinChubModeManager(
        resolved_settings,
        codex_pty_manager,
        quick_interactions,
        completion_notifier.validate_weixin_route,
        reclaim_weixin_terminal,
        codex_rate_limits,
        translation_manager=weixin_translation,
        session_archiver=archive_weixin_session,
        session_deleter=delete_weixin_session,
        system_status_reader=lambda: collect_system_status(
            resolved_settings,
            detected_platform,
        ),
        worker_health_reader=lambda: read_health_sync(resolved_settings),
        restart_coordinator=deferred_restart,
        restart_notifier=completion_notifier.notify_weixin_restart_command,
        ai_usage_reader=ai_usage,
        session_stopper=stop_weixin_session,
        session_stop_notifier=completion_notifier.notify_weixin_command_result,
        translation_result_notifier=(
            completion_notifier.notify_weixin_optimized_task
        ),
        translation_confirmation_notifier=(
            completion_notifier.notify_weixin_translation_confirmation
        ),
    )
    weixin_translation.set_completion_handler(
        weixin_chub_mode.complete_optimized_task
    )
    weixin_translation.set_notification_handler(
        weixin_chub_mode.notify_optimized_task_outcome
    )
    weixin_translation.set_confirmed_handler(
        weixin_chub_mode.retry_confirmed_optimized_task
    )

    def restart_environment_readiness() -> str | None:
        return system_upgrade_restart_readiness(
            PROJECT_ROOT,
            detected_platform,
        )

    def launch_system_upgrade_restart(operation_id: str):
        command = PROJECT_ROOT / "scripts" / "chub-system-upgrade-restart"
        readiness = restart_environment_readiness()
        if readiness is not None:
            raise OSError(readiness)
        flags = (
            os.O_APPEND
            | os.O_CREAT
            | os.O_WRONLY
            | getattr(os, "O_NOFOLLOW", 0)
        )
        descriptor = os.open(resolved_settings.logs.file, flags, 0o600)
        try:
            metadata = os.fstat(descriptor)
            if (
                not stat.S_ISREG(metadata.st_mode)
                or metadata.st_uid != os.getuid()
                or stat.S_IMODE(metadata.st_mode) & 0o077
            ):
                raise OSError("系统升级运行日志不安全。")
            os.fchmod(descriptor, 0o600)
            return subprocess.Popen(
                [str(command), operation_id],
                stdout=descriptor,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
        finally:
            os.close(descriptor)

    def recover_drained_worker(operation_id: str, protocol_version: int) -> str | None:
        try:
            resumed = asyncio.run(
                resume_after_drain(
                    resolved_settings,
                    operation_id=f"system-upgrade:{operation_id}",
                    protocol_version=protocol_version,
                )
            )
            if resumed.get("success") is not True:
                raise OSError("Quick Worker did not accept drain recovery")
        except Exception:
            logging.getLogger("hub.system_upgrade").warning(
                "Unable to resume drained Quick Worker; restarting it instead",
                exc_info=True,
            )
            try:
                recovery = launch_system_upgrade_worker_recovery(operation_id)
                if recovery.wait() == 0:
                    return None
            except Exception:
                logging.getLogger("hub.system_upgrade").warning(
                    "Unable to restart drained Quick Worker", exc_info=True
                )
            return "Quick Worker 未能恢复接收新任务。"
        return None

    def launch_system_upgrade_worker_recovery(operation_id: str):
        command = PROJECT_ROOT / "scripts" / "chub-system-upgrade-restart"
        return subprocess.Popen(
            [str(command), operation_id, "--recover-worker"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )

    async def verify_system_upgrade_new_instance(operation_id: str) -> None:
        operation = system_upgrade.operation()
        if (
            operation is None
            or operation.operation_id != operation_id
            or operation.status != "started"
        ):
            return
        system_upgrade.update(
            operation_id,
            stage="verifying_new_instance",
            message="新 Chub 实例已启动，正在确认目标协议和 Worker 健康状态。",
        )
        try:
            if instance_id == operation.old_instance_id:
                raise OSError("无法确认新的 Chub Web 实例。")
            if (
                WEB_CODE_VERSION != operation.plan.target_code_version
                or SESSION_SCHEMA_VERSION != operation.plan.target_session_schema
            ):
                raise OSError("新 Chub 实例与升级方案目标版本不匹配。")
            deadline = asyncio.get_running_loop().time() + 30
            while True:
                try:
                    health = await read_health(resolved_settings)
                except OSError:
                    health = None
                data = health.get("data") if isinstance(health, dict) else None
                if (
                    isinstance(data, dict)
                    and data.get("status") == "ready"
                    and data.get("protocol_version")
                    == operation.plan.target_worker_protocol
                    and (
                        operation.old_worker_generation is None
                        or data.get("generation") != operation.old_worker_generation
                    )
                    and data.get("active_tasks") == 0
                    and data.get("queued_tasks") == 0
                    and data.get("uncertain_tasks") == 0
                    and data.get("corrupt_tasks") == 0
                    and "codex" in data.get("available_runtime_ids", [])
                    and quick_interactions.recovery_ready
                ):
                    rebind = getattr(
                        codex_pty_manager,
                        "rebind_upgrade_terminal_carriers",
                        None,
                    )
                    if callable(rebind):
                        await asyncio.to_thread(
                            rebind,
                            [
                                (item.session_id, item.native_session_id)
                                for item in operation.sessions
                            ],
                        )
                    verifier = getattr(
                        codex_pty_manager,
                        "verify_system_upgrade_readiness",
                        None,
                    )
                    if callable(verifier):
                        await asyncio.to_thread(verifier)
                    else:
                        await asyncio.to_thread(codex_pty_manager.list_sessions)
                    await asyncio.to_thread(
                        weixin_chub_mode.verify_system_upgrade_readiness
                    )
                    if quick_interactions.system_upgrade_readiness() is not None:
                        raise OSError("Quick Worker 恢复状态尚未满足最终验收条件。")
                    system_upgrade.succeed(operation_id)
                    return
                if asyncio.get_running_loop().time() >= deadline:
                    raise TimeoutError("新服务未能在限定时间内完成健康接管。")
                await asyncio.sleep(0.5)
        except Exception as exc:
            system_upgrade.fail(
                operation_id,
                str(exc) or "新服务最终状态无法确认。",
            )

    def launch_system_upgrade_services(operation_id: str) -> None:
        state = system_upgrade.operation()
        process = None
        try:
            if (
                state is None
                or state.operation_id != operation_id
                or state.status != "started"
                or not state.destructive_started
            ):
                raise OSError("系统升级状态与服务切换请求不匹配。")
            system_upgrade.update(
                operation_id,
                stage="launching_services",
                restart_launch_state="launching",
                message="数据清理已完成，正在启动固定服务切换程序。",
            )
            process = launch_system_upgrade_restart(operation_id)
            system_upgrade.update(
                operation_id,
                stage="restarting_services",
                restart_launch_state="launched",
                restart_process_id=process.pid,
                message="服务切换程序已启动，正在切换 Quick Worker 和 Chub Web。",
            )
        except Exception as exc:
            logging.getLogger("hub.system_upgrade").warning(
                "System upgrade service switch failed",
                exc_info=True,
            )
            try:
                recovery_error = None
                if process is None and state is not None:
                    recovery_error = recover_drained_worker(
                        operation_id,
                        state.old_worker_protocol or state.plan.source_worker_protocol,
                    )
                system_upgrade.fail(
                    operation_id,
                    (str(exc) or "服务切换程序未能启动。")
                    + (f" {recovery_error}" if recovery_error else ""),
                    restart_launch_failed=True,
                )
            except Exception:
                logging.getLogger("hub.system_upgrade").warning(
                    "Unable to persist system upgrade service switch failure",
                    exc_info=True,
                )
            return

        def monitor_restart() -> None:
            try:
                return_code = process.wait()
            except Exception:
                return
            if return_code not in {0, -signal.SIGTERM}:
                recovery_error = None
                try:
                    recovery = launch_system_upgrade_worker_recovery(operation_id)
                    if recovery.wait() != 0:
                        recovery_error = "Quick Worker 自动恢复失败，请检查服务状态。"
                except Exception:
                    recovery_error = "Quick Worker 自动恢复失败，请检查服务状态。"
                try:
                    system_upgrade.fail(
                        operation_id,
                        "服务切换脚本失败；" + (
                            recovery_error or "Quick Worker 已恢复，Chub 可继续使用。"
                        ),
                        restart_launch_failed=True,
                    )
                except Exception:
                    logging.getLogger("hub.system_upgrade").warning(
                        "Unable to record system upgrade restart failure",
                        exc_info=True,
                    )

        threading.Thread(
            target=monitor_restart,
            daemon=True,
            name=f"chub-system-upgrade-monitor-{operation_id[:8]}",
        ).start()

    def run_system_upgrade(operation_id: str) -> None:
        translation_upgrade_guard = False
        state = None
        try:
            state = system_upgrade.operation()
            if state is None or state.operation_id != operation_id:
                raise OSError("系统升级状态与执行请求不匹配。")
            if state.destructive_started and state.stage == "verifying_new_instance":
                asyncio.run(verify_system_upgrade_new_instance(operation_id))
                return
            if state.destructive_started and state.stage in {
                "launching_services",
                "restarting_services",
            }:
                launch_system_upgrade_services(operation_id)
                return
            loaded = system_upgrade.plan() or runtime_recovery_plan()
            if loaded.fingerprint != state.fingerprint:
                raise OSError("系统升级方案已经变化，本次升级已停止。")
            system_upgrade.mark_started(operation_id)
            if not system_upgrade.wait_for_writes(30):
                raise TimeoutError("已有写入未能在限定时间内结束。")
            restart_error = restart_environment_readiness()
            if restart_error is not None:
                raise OSError(restart_error)
            cleanup_error = runtime_cleanup_readiness(resolved_settings)
            if cleanup_error is not None:
                raise OSError(cleanup_error)
            system_upgrade.update(
                operation_id,
                stage="draining_worker",
                message="正在停止 Quick Worker，在途任务将终止并清理运行态。",
                worker_drain_started=True,
            )
            weixin_translation.acquire_system_upgrade_guard(force=True)
            translation_upgrade_guard = True
            if state.destructive_started:
                sessions = state.sessions
            else:
                try:
                    sessions = [
                        SystemUpgradeSession(
                            session_id=session.id,
                            native_session_id=session.native_session_id,
                        )
                        for session in codex_pty_manager.system_upgrade_sessions()
                    ]
                except (OSError, ValueError):
                    # The fixed restart helper removes the local Store after the
                    # Worker is stopped, so a corrupt mapping remains recoverable.
                    sessions = []
            system_upgrade.update(
                operation_id,
                stage="cleaning_state",
                destructive_started=True,
                message="正在清理 Chub Session 关联和旧运行状态。",
                sessions=sessions,
            )
            for session in sessions:
                if session.status == "discarded":
                    continue
                terminal_tickets.revoke_session(session.session_id)
                terminal_connections.close_session(session.session_id)
                codex_pty_manager.discard_session_for_system_upgrade(
                    session.session_id
                )
                session.status = "discarded"
                system_upgrade.update(operation_id, sessions=sessions)
            quick_interactions.reset_for_system_upgrade(force=True)
            weixin_translation.reset_for_system_upgrade(force=True)
            weixin_chub_mode.reset_for_system_upgrade(operation_id, force=True)
            weixin_translation.release_system_upgrade_guard()
            translation_upgrade_guard = False
            launch_system_upgrade_services(operation_id)
        except Exception as exc:
            logging.getLogger("hub.system_upgrade").warning(
                "System upgrade failed",
                exc_info=True,
            )
            try:
                system_upgrade.fail(
                    operation_id,
                    str(exc) or "系统升级与恢复失败。",
                )
            except Exception:
                logging.getLogger("hub.system_upgrade").warning(
                    "Unable to persist system upgrade failure",
                    exc_info=True,
                )
        finally:
            if translation_upgrade_guard:
                weixin_translation.release_system_upgrade_guard()

    def deferred_restart_ready(request):
        fixed_readiness = weixin_chub_mode.deferred_restart_readiness(request)
        has_quick_context = quick_interactions.has_deferred_restart_context(
            request.operation_id,
            request.requested_task_id,
        )
        if fixed_readiness is not None and has_quick_context:
            quick_readiness = quick_interactions.deferred_restart_ready(request)
            if "sensitive_task_failed" in {fixed_readiness, quick_readiness}:
                return "sensitive_task_failed"
            if "waiting" in {fixed_readiness, quick_readiness}:
                return "waiting"
            return "ready"
        if fixed_readiness is not None:
            return fixed_readiness
        return quick_interactions.deferred_restart_ready(request)

    def record_deferred_restart_started(
        operation_id,
        task_id,
        started_at,
    ) -> None:
        if quick_interactions.has_deferred_restart_context(operation_id, task_id):
            quick_interactions.record_deferred_restart_started(
                operation_id,
                task_id,
                started_at,
            )
        weixin_chub_mode.record_deferred_restart_started(
            operation_id,
            task_id,
            started_at,
        )

    def record_deferred_restart_completion(
        operation_id,
        task_id,
        outcome,
        completed_at,
        failure_reason=None,
    ) -> None:
        if quick_interactions.has_deferred_restart_context(operation_id, task_id):
            quick_interactions.record_deferred_restart_completion(
                operation_id,
                task_id,
                outcome,
                completed_at,
                failure_reason,
            )
        weixin_chub_mode.record_deferred_restart_completion(
            operation_id,
            task_id,
            outcome,
            completed_at,
            failure_reason,
        )

    deferred_restart.set_ready_check(deferred_restart_ready)
    deferred_restart.set_started_handler(record_deferred_restart_started)
    deferred_restart.set_completion_handler(record_deferred_restart_completion)
    quick_worker_maintenance.set_completion_handler(deferred_restart.maybe_schedule)
    completion_notifier.session_slot_validator = (
        weixin_chub_mode.session_slot_matches
    )
    completion_notifier.session_context_reader = weixin_chub_mode.session_context
    completion_notifier.session_current_validator = (
        weixin_chub_mode.session_slot_is_current
    )
    completion_notifier.codex_status_reader = weixin_chub_mode.codex_status_message
    completion_notifier.completion_usage_reader = lambda: usage_message(
        ai_usage.read(force=False)
    )
    codex_pty_manager.set_quick_interaction_checker(quick_interactions.is_running)
    openclaw_manager = OpenClawManager()
    notification_service = NotificationService(resolved_settings.notifications)

    def start_weixin_maintenance(
        target: str,
        operation_id: str,
        route,
        source_ip: str,
    ) -> str:
        """Start a fixed maintenance target without blocking the OpenClaw hook."""
        if target == "worker":
            if system_upgrade.in_progress():
                raise ApiError(
                    409,
                    "system_upgrade_in_progress",
                    "系统升级期间暂不接受新的 Worker 重启操作。",
                )
            if not quick_worker_maintenance.begin(None, source_ip):
                raise ApiError(
                    409,
                    "quick_worker_operation_in_progress",
                    "Quick Worker 正在执行其他维护操作。",
                )

            def notify_worker_result() -> None:
                deadline = time.monotonic() + 180
                while time.monotonic() < deadline:
                    operation = quick_worker_maintenance.operation()
                    if operation is not None and operation.status != "restarting":
                        status = "Completed" if operation.status == "succeeded" else "Failed"
                        write_operation(
                            operation_id=operation_id,
                            action="weixin_restart_worker",
                            status=(
                                "succeeded"
                                if operation.status == "succeeded"
                                else "failed"
                            ),
                            target="quick-worker",
                            source_ip=source_ip,
                        )
                        completion_notifier.notify_weixin_command_result(
                            route,
                            lambda: f"Restart Worker: {status}. {operation.message}",
                        )
                        return
                    time.sleep(0.5)
                completion_notifier.notify_weixin_command_result(
                    route,
                    lambda: "Restart Worker: Failed. The final state could not be confirmed.",
                )

            threading.Thread(
                target=notify_worker_result,
                daemon=True,
                name=f"weixin-worker-restart-{operation_id[:8]}",
            ).start()
            return "Restart Worker: Scheduled. The result will be sent when completed."

        if target == "clawbot":
            def restart_clawbot() -> None:
                try:
                    result = openclaw_manager.control("restart")
                    message = f"Restart ClawBot: Completed. {result.message}"
                except Exception:
                    logging.getLogger("hub.openclaw").warning(
                        "Unable to complete Weixin ClawBot restart",
                        exc_info=True,
                    )
                    message = "Restart ClawBot: Failed. Check the OpenClaw status and logs."
                    write_operation(
                        operation_id=operation_id,
                        action="weixin_restart_clawbot",
                        status="failed",
                        target="openclaw-gateway",
                        source_ip=source_ip,
                    )
                else:
                    write_operation(
                        operation_id=operation_id,
                        action="weixin_restart_clawbot",
                        status="succeeded",
                        target="openclaw-gateway",
                        source_ip=source_ip,
                    )
                completion_notifier.notify_weixin_command_result(
                    route,
                    lambda: message,
                )

            # Let the current OpenClaw hook return before restarting its Gateway.
            timer = threading.Timer(1.0, restart_clawbot)
            timer.daemon = True
            timer.name = f"weixin-clawbot-restart-{operation_id[:8]}"
            timer.start()
            return "Restart ClawBot: Scheduled. The result will be sent when completed."

        raise ApiError(400, "maintenance_target_invalid", "维护目标无效。")

    weixin_chub_mode.maintenance_command_starter = start_weixin_maintenance

    @asynccontextmanager
    async def lifespan(_application: FastAPI):
        restart_recovery_task = None
        worker_maintenance_recovery_task = None
        system_upgrade_recovery_task = None
        await asyncio.to_thread(quick_interactions.start_worker_reconciliation)
        weixin_chub_mode.start_status_cache()
        upgrade_operation = system_upgrade.operation()
        if (
            upgrade_operation is not None
            and upgrade_operation.status == "failed"
            and upgrade_operation.destructive_started
            and upgrade_operation.failed_stage == "verifying_new_instance"
        ):
            system_upgrade.rebase_failed_verification(runtime_recovery_plan())
            upgrade_operation = system_upgrade.operation()
        if (
            upgrade_operation is not None
            and upgrade_operation.status in {"requested", "started"}
            and upgrade_operation.stage
            not in {"restarting_services", "verifying_new_instance"}
        ):
            system_upgrade.resume(run_system_upgrade)
        elif (
            upgrade_operation is not None
            and (
                (
                    upgrade_operation.status in {"requested", "started"}
                    and upgrade_operation.stage
                    in {"restarting_services", "verifying_new_instance"}
                )
                or (
                    upgrade_operation.status == "failed"
                    and upgrade_operation.destructive_started
                    and (
                        upgrade_operation.failed_stage == "verifying_new_instance"
                        or (
                            upgrade_operation.failed_stage == "restarting_services"
                            and upgrade_operation.restart_launch_state == "launched"
                        )
                    )
                )
            )
        ):
            if upgrade_operation.status == "failed":
                upgrade_operation = system_upgrade.resume_verification()
            system_upgrade_recovery_task = asyncio.create_task(
                verify_system_upgrade_new_instance(upgrade_operation.operation_id)
            )
        if quick_worker_maintenance.in_progress():
            async def finish_worker_maintenance_recovery() -> None:
                while quick_worker_maintenance.in_progress():
                    await inspect_quick_worker(
                        resolved_settings,
                        quick_interactions.recovery_ready,
                        quick_worker_maintenance,
                    )
                    if quick_worker_maintenance.in_progress():
                        await asyncio.sleep(1)

            worker_maintenance_recovery_task = asyncio.create_task(
                finish_worker_maintenance_recovery()
            )
        if (
            deferred_restart.requires_service_confirmation()
            or quick_interactions.has_pending_deferred_restart_notifications()
        ):
            async def finish_restart_recovery() -> None:
                await _confirm_healthy_instance(resolved_settings, instance_id)
                while deferred_restart.requires_service_confirmation():
                    consumed = await asyncio.to_thread(
                        deferred_restart.service_started
                    )
                    if consumed:
                        break
                    await asyncio.sleep(1)
                quick_interactions.resume_pending_deferred_restart_notifications()

            restart_recovery_task = asyncio.create_task(finish_restart_recovery())
        try:
            yield
        finally:
            if worker_maintenance_recovery_task is not None:
                worker_maintenance_recovery_task.cancel()
                with suppress(asyncio.CancelledError):
                    await worker_maintenance_recovery_task
            if restart_recovery_task is not None:
                restart_recovery_task.cancel()
                with suppress(asyncio.CancelledError):
                    await restart_recovery_task
            if system_upgrade_recovery_task is not None:
                system_upgrade_recovery_task.cancel()
                with suppress(asyncio.CancelledError):
                    await system_upgrade_recovery_task
            await asyncio.to_thread(weixin_translation.close)
            await quick_interactions.aclose()
            await notification_service.close()
            codex_pty_manager.close()
            openclaw_manager.close()

    application = FastAPI(
        title=resolved_settings.app.name,
        version=resolved_settings.app.version,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        lifespan=lifespan,
    )
    application.state.settings = resolved_settings
    application.state.instance_id = instance_id
    application.state.detected_platform = detected_platform
    application.state.codex_pty_available = codex_pty_available
    application.state.ai_session_manager = codex_pty_manager
    application.state.codex_pty_manager = codex_pty_manager
    application.state.codex_rate_limits = codex_rate_limits
    application.state.ai_usage = ai_usage
    application.state.quick_interactions = quick_interactions
    application.state.quick_worker_maintenance = quick_worker_maintenance
    application.state.system_upgrade = system_upgrade
    application.state.run_system_upgrade = run_system_upgrade
    application.state.system_upgrade_restart_readiness = (
        restart_environment_readiness
    )
    application.state.deferred_restart = deferred_restart
    application.state.maintenance_lock = threading.RLock()
    application.state.weixin_chub_mode = weixin_chub_mode
    application.state.weixin_translation = weixin_translation
    application.state.terminal_tickets = terminal_tickets
    application.state.terminal_connections = terminal_connections
    application.state.automation_manager = AutomationManager(resolved_settings)
    application.state.openclaw_manager = openclaw_manager
    application.state.notification_service = notification_service

    def start_weixin_system_upgrade(source_ip: str) -> object:
        return asyncio.run(
            start_system_upgrade_for_source(
                application,
                source_ip=source_ip,
                fingerprint=None,
            )
        )

    weixin_chub_mode.system_upgrade_starter = start_weixin_system_upgrade
    application.add_middleware(SystemUpgradeGateMiddleware)
    application.add_middleware(SecurityHeadersMiddleware)
    application.add_exception_handler(ApiError, api_error_handler)
    application.add_exception_handler(
        RequestValidationError,
        validation_error_handler,
    )
    application.add_exception_handler(StarletteHTTPException, http_error_handler)
    application.add_exception_handler(Exception, internal_error_handler)
    application.include_router(health_router)
    application.include_router(ai_usage_router)
    application.include_router(automations_router)
    application.include_router(logs_router)
    application.include_router(maintenance_router)
    application.include_router(notifications_router)
    application.include_router(openclaw_router)
    application.include_router(openclaw_wechat_chub_mode_router)
    application.include_router(project_documents_router)
    application.include_router(weekly_reports_router)
    application.include_router(settings_router)
    application.include_router(status_router)
    application.include_router(codex_api_router)
    application.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
    application.include_router(codex_web_router)
    application.include_router(web_router)
    return application
