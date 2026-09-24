from __future__ import annotations

import asyncio
import logging
import os
import threading
import time
from contextlib import asynccontextmanager
from contextlib import suppress
from datetime import datetime
from uuid import uuid4

import httpx
from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from app.api.ai_usage import router as ai_usage_router
from app.api.health import router as health_router
from app.api.ai_search import router as ai_search_router
from app.api.automations import router as automations_router
from app.api.logs import router as logs_router
from app.api.maintenance import (
    router as maintenance_router,
    start_system_upgrade_for_source,
)
from app.api.maintenance_terminal import (
    api_router as maintenance_terminal_api_router,
    web_router as maintenance_terminal_web_router,
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
from app.api.plugins import router as plugins_router
from app.plugin_lifecycle import PluginLifecycleService, PromptOptimizerSettingsStore
from app.api.status import router as status_router
from app.ai_session import AiSessionManager
from app.ai_session.operations import archive_session, delete_session
from app.ai_interactions.quick_interactions import QuickInteractionManager
from app.ai_interactions.task_orchestration import (
    TaskOrchestrationDispatcher,
    retire_weixin_refinement_state,
)
from app.quick_worker_tasks import worker_restart_request_dir
from app.ai_runtime import RuntimeOperationError
from app.ai_runtime.usage import RuntimeUsageService
from app.api.ai import (
    api_router as ai_session_api_router,
)
from app.api.ai import web_router as ai_web_router
from app.automations.manager import AutomationManager
from app.automations.models import RuntimeAccountEnvironmentState
from app.core.config import PROJECT_ROOT, Settings, load_settings, log_local_config_fallback
from app.core.business_modules import load_business_modules
from app.core.logger import configure_logging
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
from app.services.network_recovery import NetworkRecoveryError, restart_network
from app.services.maintenance_terminal import MaintenanceTerminalManager
from app.services.deferred_restart import (
    MANUAL_RESTART_TASK_ID,
    DeferredRestartCoordinator,
)
from app.services.openclaw_weixin_chub_mode import WeixinChubModeManager
from app.services.restart_command import RestartProcess
from app.services.web_restart import WebRestartUseCase
from app.services.quick_worker_maintenance import (
    QuickWorkerMaintenanceUseCase,
    QuickWorkerReloadCoordinator,
    inspect_quick_worker,
)
from app.services.system_status import collect_system_status
from app.services.weekly_report_generation import WeeklyReportGenerationService
from app.services.system_upgrade import (
    SystemUpgradeBusy,
    SystemUpgradeCoordinator,
    SystemUpgradeSession,
    runtime_cleanup_readiness,
    runtime_recovery_plan,
)
from app.services.system_upgrade_maintenance import (
    SystemUpgradeMaintenanceUnavailableError,
    SystemUpgradeMaintenanceUseCase,
)
from app.services.workstation_rebuild import WorkstationRebuildCoordinator
from app.services.workstation_rebuild_maintenance import WorkstationRebuildMaintenanceUseCase
from app.services.deployment_package import DeploymentPackageService
from app.quick_worker import (
    clear_runtime_state,
    read_health,
    read_health_sync,
    request_drain,
    resume_after_drain,
)
from app.notifications.feishu.service import NotificationService
from app.web.routes import (
    STATIC_DIR,
    configure_business_module_templates,
    router as web_router,
)
from app.ai_search import AiSearchService


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
            or request.url.path.startswith("/settings")
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
        elif request.url.path.startswith("/ai/sessions/"):
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
            elif "/quick-interactions/conversation" in request.url.path:
                response.headers["Content-Security-Policy"] = (
                    "default-src 'self'; "
                    "style-src 'self'; "
                    "frame-src 'self'; "
                    "frame-ancestors 'self'; "
                    "object-src 'none'; "
                    "base-uri 'none'"
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
        path.startswith("/api/ai/")
        or path.startswith("/api/runtime-modules/")
        or path.startswith("/api/plugins/runtime/")
        or path.startswith("/api/ai/runtimes/")
        or path == "/api/ai/settings"
        or path.startswith("/api/today-focus/")
        or path.startswith("/api/weekly-reports/")
    )


def create_app(settings: Settings | None = None) -> FastAPI:
    resolved_settings = settings or load_settings()
    instance_id = uuid4().hex
    configure_logging(resolved_settings.logs)
    log_local_config_fallback(resolved_settings)

    detected_platform = detect_platform()
    logger = logging.getLogger("hub.startup")
    if resolved_settings.node.type != detected_platform:
        logger.warning(
            "configured_platform=%s detected_platform=%s",
            resolved_settings.node.type,
            detected_platform,
        )
    logger.info(
        "node_id=%s node_name=%s platform=%s version=%s instance_id=%s",
        resolved_settings.node.id,
        resolved_settings.node.name,
        detected_platform,
        resolved_settings.app.version,
        instance_id,
    )

    # The AI Session Manager is the sole production owner.  The old
    # Retired Session state is cleaned by the fixed upgrade flow and is never
    # used as a startup-time compatibility switch.
    ai_session_manager = AiSessionManager(resolved_settings)
    class CurrentCodexRateLimits:
        def read(self, *, force: bool = False):
            return ai_session_manager.codex_rate_limits.read(force=force)

        def read_account_status(self, *, force: bool = False):
            return ai_session_manager.codex_rate_limits.read_account_status(force=force)

    codex_rate_limits = CurrentCodexRateLimits()
    ai_usage = RuntimeUsageService(
        lambda: ai_session_manager.runtime_registry,
        default_runtime_id=ai_session_manager.default_submission_implementation_id,
    )
    completion_notifier = OpenClawCompletionNotifier(
        resolved_settings.openclaw.quick_interaction_completion
    )

    web_restart = WebRestartUseCase()

    def start_deferred_restart() -> RestartProcess:
        return web_restart.launch()

    deferred_restart = DeferredRestartCoordinator(
        resolved_settings.ai_runtime.shared.state_dir / "deferred-restart.json",
        instance_id,
        start_deferred_restart,
    )
    quick_interactions = QuickInteractionManager(
        resolved_settings.ai_runtime.shared.state_dir / "quick-interactions.json",
        worker_restart_request_dir(resolved_settings),
        ai_session_manager,
        completion_notifier.notify,
        deferred_restart,
        restart_notifier=completion_notifier.notify_restart,
        timeout_seconds=resolved_settings.ai_runtime.shared.quick_interaction_timeout_seconds,
        worker_settings=resolved_settings,
    )
    try:
        retire_weixin_refinement_state(
            weixin_state_file=resolved_settings.openclaw.weixin_chub_mode.state_file,
            translation_state_file=(
                resolved_settings.openclaw.weixin_chub_mode.state_file.with_name(
                    "weixin-translation.json"
                )
            ),
            orchestration_modules_dir=(
                PROJECT_ROOT / "data/local/runtime/openclaw/weixin-orchestration-modules"
            ),
            plugin_lifecycle_file=resolved_settings.business_modules.state_file,
            retirement_marker_file=(
                resolved_settings.ai_runtime.shared.state_dir
                / "weixin-refinement-retired-v1.json"
            ),
        )
    except OSError:
        logging.getLogger("hub.startup").warning(
            "Retired Weixin refinement state is unavailable; keeping its cleanup isolated",
            exc_info=True,
        )
    task_orchestrator = TaskOrchestrationDispatcher(
        resolved_settings.ai_runtime.shared.state_dir / "task-orchestration.json",
        quick_interactions,
    )
    weekly_report_generation = WeeklyReportGenerationService(
        resolved_settings.ai_runtime.shared.state_dir / "weekly-report-generation.json",
        ai_session_manager,
        quick_interactions,
    )
    quick_worker_maintenance = QuickWorkerReloadCoordinator(
        resolved_settings.ai_runtime.shared.state_dir / "quick-worker-maintenance.json",
        QuickWorkerMaintenanceUseCase(),
    )
    system_upgrade = SystemUpgradeCoordinator(
        resolved_settings.ai_runtime.shared.state_dir / "system-upgrade.json",
        PROJECT_ROOT / "config" / "system-upgrade.json",
        instance_id,
    )
    quick_interactions.set_maintenance_window_checker(
        lambda: (
            system_upgrade.in_progress()
            or quick_worker_maintenance.in_progress()
        )
    )
    ai_session_manager.set_system_upgrade_checker(system_upgrade.writes_blocked)
    maintenance_terminal = MaintenanceTerminalManager(resolved_settings)
    deployment_package = DeploymentPackageService(
        resolved_settings,
        ai_session_manager,
        quick_interactions,
    )
    def reclaim_weixin_session(session_id: str):
        return ai_session_manager.stop_session(session_id)

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
            manager=ai_session_manager,
            quick_interactions=quick_interactions,
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
            manager=ai_session_manager,
            quick_interactions=quick_interactions,
            release_slot=release_weixin_session_slot_for_delete,
        )

    def stop_weixin_session(session_id: str):
        with quick_interactions.stop_operation_guard(session_id):
            ai_session_manager.ensure_stop_allowed(session_id)
            quick_interactions.cancel_session_interactions(session_id)
            return ai_session_manager.stop_session(session_id)

    def weixin_system_upgrade_check_status():
        try:
            loaded = system_upgrade.plan()
        except OSError:
            status = system_upgrade.status_data(
                runtime_recovery_plan(),
                session_count=0,
            )
            status.plan_unavailable = True
            return status
        return system_upgrade.status_data(loaded, session_count=0)

    weixin_chub_mode = WeixinChubModeManager(
        resolved_settings,
        ai_session_manager,
        quick_interactions,
        completion_notifier.validate_weixin_route,
        session_reclaimer=reclaim_weixin_session,
        codex_account_reader=codex_rate_limits,
        task_orchestrator=task_orchestrator,
        session_archiver=archive_weixin_session,
        session_deleter=delete_weixin_session,
        system_status_reader=lambda: collect_system_status(
            resolved_settings,
            detected_platform,
        ),
        worker_health_reader=lambda: read_health_sync(resolved_settings),
        system_upgrade_status_reader=weixin_system_upgrade_check_status,
        restart_coordinator=deferred_restart,
        restart_notifier=completion_notifier.notify_weixin_restart_command,
        ai_usage_reader=ai_usage,
        session_stopper=stop_weixin_session,
        session_stop_notifier=completion_notifier.notify_weixin_command_result,
        last_result_notifier=completion_notifier.resend_weixin_task_result,
    )
    plugin_lifecycle = PluginLifecycleService(
        resolved_settings,
        ai_session_manager,
    )
    prompt_optimizer_settings = PromptOptimizerSettingsStore(
        resolved_settings.business_modules.state_file.parent.parent,
    )
    orchestration_plugins = plugin_lifecycle.assemble_orchestration_plugins()
    business_modules = load_business_modules(resolved_settings)
    configure_business_module_templates(resolved_settings)
    ai_session_manager.set_runtime_plugin_lifecycle_state_reader(
        plugin_lifecycle.runtime_implementation_lifecycle_state
    )
    def record_quick_task_finished(task) -> None:
        try:
            deployment_package.record_release_note_task_finished(task)
        except Exception:
            logger.warning(
                "Unable to persist deployment package release-note task result",
                exc_info=True,
            )
        task_orchestrator.record_task_finished(task)

    quick_interactions.set_task_finished_handler(record_quick_task_finished)
    task_orchestrator.reconcile()

    system_upgrade_maintenance = SystemUpgradeMaintenanceUseCase(detected_platform)
    workstation_rebuild = WorkstationRebuildCoordinator()
    workstation_rebuild_maintenance = WorkstationRebuildMaintenanceUseCase()
    weixin_chub_mode.system_upgrade_status_reader = workstation_rebuild.status_data

    def restart_environment_readiness() -> str | None:
        try:
            system_upgrade_maintenance.ensure_available()
        except SystemUpgradeMaintenanceUnavailableError as error:
            return str(error) or "系统升级服务切换脚本不可用。"
        return None

    def launch_system_upgrade_restart(operation_id: str):
        system_upgrade_maintenance.start(operation_id)

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
        return system_upgrade_maintenance.launch_worker_recovery(operation_id)

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
            message="正在确认新 Web 和 Quick Worker 的健康状态。",
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
                    and quick_interactions.recovery_ready
                ):
                    system_upgrade.record_component(
                        operation_id,
                        "chub_web",
                        "succeeded",
                        "新 Chub Web 实例已确认运行目标版本",
                    )
                    system_upgrade.record_component(
                        operation_id,
                        "quick_worker",
                        "succeeded",
                        "Quick Worker 已确认目标协议和空闲健康状态",
                    )
                    verifier = getattr(
                        ai_session_manager,
                        "verify_system_upgrade_readiness",
                        None,
                    )
                    if callable(verifier):
                        await asyncio.to_thread(verifier)
                    else:
                        await asyncio.to_thread(ai_session_manager.list_sessions)
                    if quick_interactions.system_upgrade_readiness() is not None:
                        raise OSError("Quick Worker 恢复状态尚未满足最终验收条件。")
                    try:
                        runtime_management = await asyncio.to_thread(
                            ai_session_manager.read_runtime_management
                        )
                        enabled = [
                            item
                            for item in runtime_management.runtimes
                            if item.enabled
                        ]
                        available = [item for item in enabled if item.healthy]
                        if available:
                            runtime_message = "AI Runtime 可用：" + "、".join(
                                item.name for item in available
                            )
                            runtime_status = "checked"
                        elif not runtime_management.runtimes:
                            runtime_message = "未配置 AI Runtime，不影响核心服务恢复"
                            runtime_status = "checked"
                        elif not enabled:
                            runtime_message = "AI Runtime 已在设置中停用，不影响核心服务恢复"
                            runtime_status = "checked"
                        else:
                            runtime_message = "已启用的 AI Runtime 当前不可用，不影响核心服务恢复"
                            runtime_status = "checked"
                    except ApiError:
                        runtime_message = "AI Runtime 可用性暂无法确认，不影响核心服务恢复"
                        runtime_status = "checked"
                    system_upgrade.record_component(
                        operation_id,
                        "ai_runtime",
                        runtime_status,
                        runtime_message,
                    )
                    quick_worker_maintenance.clear_completed_operation()
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
                message="运行状态已清理，正在启动服务恢复流程。",
            )
            launch_system_upgrade_restart(operation_id)
            system_upgrade.update(
                operation_id,
                stage="restarting_services",
                restart_launch_state="launched",
                message="正在重启 Chub Web 和 Quick Worker。",
            )
        except Exception as exc:
            logging.getLogger("hub.system_upgrade").warning(
                "System upgrade service switch failed",
                exc_info=True,
            )
            try:
                recovery_error = None
                if state is not None:
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

    def run_system_upgrade(operation_id: str) -> None:
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
            try:
                loaded = system_upgrade.plan()
            except OSError:
                loaded = runtime_recovery_plan()
            loaded = loaded or runtime_recovery_plan()
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
                message="正在停止 Quick Worker；在途任务将终止。",
                worker_drain_started=True,
            )
            if state.destructive_started:
                sessions = state.sessions
            else:
                try:
                    sessions = [
                        SystemUpgradeSession(
                            session_id=session.id,
                            native_session_id=session.native_session_id,
                        )
                        for session in ai_session_manager.system_upgrade_sessions()
                    ]
                except (OSError, ValueError):
                    # The fixed restart helper removes the local Store after the
                    # Worker is stopped, so a corrupt mapping remains recoverable.
                    sessions = []
            system_upgrade.update(
                operation_id,
                stage="cleaning_state",
                destructive_started=True,
                message="正在清理 Chub AI Session 关联和 Worker 运行状态。",
                sessions=sessions,
            )
            for session in sessions:
                if session.status == "discarded":
                    continue
                ai_session_manager.discard_session_for_system_upgrade(
                    session.session_id
                )
                session.status = "discarded"
                system_upgrade.update(operation_id, sessions=sessions)
            quick_interactions.reset_for_system_upgrade(force=True)
            launch_system_upgrade_services(operation_id)
        except Exception as exc:
            logging.getLogger("hub.system_upgrade").warning(
                "System upgrade failed",
                exc_info=True,
            )
            try:
                system_upgrade.fail(
                    operation_id,
                    str(exc) or "升级与恢复失败。",
                )
            except Exception:
                logging.getLogger("hub.system_upgrade").warning(
                    "Unable to persist system upgrade failure",
                    exc_info=True,
                )
    def deferred_restart_ready(request):
        if request.requested_task_id == MANUAL_RESTART_TASK_ID:
            return "ready"
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
        if task_id == MANUAL_RESTART_TASK_ID:
            return
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
        if task_id == MANUAL_RESTART_TASK_ID:
            return
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
    ai_session_manager.set_quick_interaction_checker(quick_interactions.is_running)

    def cleanup_passively_removed_session(session_id: str) -> bool:
        return quick_interactions.try_remove_session_tasks(
            session_id
        ) and weixin_chub_mode.try_release_session_slot(session_id)

    ai_session_manager.set_passive_session_cleanup(cleanup_passively_removed_session)
    openclaw_manager = OpenClawManager(resolved_settings.openclaw)
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

        if target == "network":
            def restart_configured_network() -> None:
                try:
                    result = restart_network(
                        resolved_settings,
                        operation_id=operation_id,
                        source_ip=source_ip,
                    )
                    message = f"Restart Network: Completed. {result.message}"
                except NetworkRecoveryError as exc:
                    message = f"Restart Network: Failed. {exc}"
                except Exception:
                    logging.getLogger("hub.network_recovery").warning(
                        "Unable to complete Weixin network restart",
                        exc_info=True,
                    )
                    write_operation(
                        operation_id=operation_id,
                        action="network_restart",
                        status="failed",
                        target="networkmanager:configured-wifi-vpn",
                        source_ip=source_ip,
                        reason="network restart final state could not be confirmed",
                    )
                    message = (
                        "Restart Network: Failed. The final state could not be confirmed."
                    )
                completion_notifier.notify_weixin_command_result(
                    route,
                    lambda: message,
                )

            # Let the current OpenClaw hook send its fixed receipt before Wi-Fi drops.
            timer = threading.Timer(1.0, restart_configured_network)
            timer.daemon = True
            timer.name = f"weixin-network-restart-{operation_id[:8]}"
            timer.start()
            return "Restart Network: Scheduled. The result will be sent when completed."

        raise ApiError(400, "maintenance_target_invalid", "维护目标无效。")

    weixin_chub_mode.maintenance_command_starter = start_weixin_maintenance

    @asynccontextmanager
    async def lifespan(_application: FastAPI):
        restart_recovery_task = None
        worker_maintenance_recovery_task = None
        system_upgrade_recovery_task = None
        runtime_plugin_recovery_task = None
        runtime_state_cleanup_task = None
        runtime_plugin_recovery = ai_session_manager.runtime_plugin_recovery
        if runtime_plugin_recovery is not None:
            write_operation(
                operation_id=runtime_plugin_recovery.operation_id,
                action=runtime_plugin_recovery.action,
                status="failed",
                target=runtime_plugin_recovery.module_id,
                source_ip="127.0.0.1",
                reason="recovered incomplete Runtime plugin operation before confirmation",
            )

            async def restore_runtime_plugin_worker() -> None:
                drain_id = f"runtime-module-recovery:{runtime_plugin_recovery.operation_id}"
                try:
                    drained = await request_drain(
                        resolved_settings,
                        operation_id=drain_id,
                        wait_seconds=7200,
                    )
                    if drained.get("success") is not True:
                        raise OSError("Quick Worker drain could not be confirmed")
                    health = await read_health(resolved_settings)
                    data = health.get("data") if health.get("success") is True else None
                    generation = data.get("generation") if isinstance(data, dict) else None
                    if not isinstance(generation, str) or not quick_worker_maintenance.begin(
                        generation,
                        "127.0.0.1",
                    ):
                        raise OSError("Quick Worker reload could not be started")
                    while quick_worker_maintenance.in_progress():
                        await asyncio.sleep(0.2)
                    operation = quick_worker_maintenance.operation()
                    if operation is None or operation.status != "succeeded":
                        raise OSError("Quick Worker reload could not be confirmed")
                    write_operation(
                        operation_id=f"runtime-module-recovery:{runtime_plugin_recovery.operation_id}",
                        action=(
                            "recover_runtime_plugin_removal"
                            if runtime_plugin_recovery.action == "remove_runtime_module"
                            else "recover_runtime_plugin_activation"
                        ),
                        status="succeeded",
                        target=runtime_plugin_recovery.module_id,
                        source_ip="127.0.0.1",
                    )
                except Exception:
                    logging.getLogger("hub.runtime_plugins").warning(
                        "Unable to reconcile incomplete Runtime plugin activation",
                        exc_info=True,
                    )
                    write_operation(
                        operation_id=f"runtime-module-recovery:{runtime_plugin_recovery.operation_id}",
                        action=(
                            "recover_runtime_plugin_removal"
                            if runtime_plugin_recovery.action == "remove_runtime_module"
                            else "recover_runtime_plugin_activation"
                        ),
                        status="failed",
                        target=runtime_plugin_recovery.module_id,
                        source_ip="127.0.0.1",
                        reason="quick worker registry recovery could not be confirmed",
                    )

            runtime_plugin_recovery_task = asyncio.create_task(
                restore_runtime_plugin_worker()
            )
            await asyncio.sleep(0)

        async def recover_runtime_plugin_state_cleanup() -> None:
            """Retry only the deferred local state cleanup for one Runtime."""
            reported_failure = False
            while True:
                try:
                    cleanup = ai_session_manager.runtime_plugin_service.pending_state_cleanup()
                except Exception:
                    logging.getLogger("hub.runtime_plugins").warning(
                        "Unable to read deferred Runtime plugin state cleanup",
                        exc_info=True,
                    )
                    return
                if cleanup is None:
                    reported_failure = False
                    await asyncio.sleep(5)
                    continue
                try:
                    cleared = await clear_runtime_state(
                        resolved_settings,
                        runtime_id=cleanup.runtime_id,
                    )
                    if cleared.get("success") is not True:
                        raise OSError("Quick Worker Runtime state cleanup was not confirmed")
                    for session_id in cleanup.session_ids:
                        quick_interactions.remove_session_tasks(session_id)
                    ai_session_manager.clear_runtime_plugin_state(cleanup.runtime_id)
                    ai_session_manager.runtime_plugin_service.complete_state_cleanup(
                        cleanup.operation_id
                    )
                    write_operation(
                        operation_id=f"runtime-module-state-cleanup:{cleanup.operation_id}",
                        action="recover_runtime_plugin_state_cleanup",
                        status="succeeded",
                        target=cleanup.module_id,
                        source_ip="127.0.0.1",
                    )
                    reported_failure = False
                    continue
                except Exception:
                    if not reported_failure:
                        logging.getLogger("hub.runtime_plugins").warning(
                            "Deferred Runtime plugin state cleanup is pending",
                            exc_info=True,
                        )
                        write_operation(
                            operation_id=f"runtime-module-state-cleanup:{cleanup.operation_id}",
                            action="recover_runtime_plugin_state_cleanup",
                            status="failed",
                            target=cleanup.module_id,
                            source_ip="127.0.0.1",
                            reason="runtime plugin state cleanup remains pending",
                        )
                        reported_failure = True
                    await asyncio.sleep(5)

        runtime_state_cleanup_task = asyncio.create_task(
            recover_runtime_plugin_state_cleanup()
        )
        await asyncio.to_thread(quick_interactions.start_worker_reconciliation)
        weixin_chub_mode.start_status_cache()
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
            )
        ):
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
            if runtime_plugin_recovery_task is not None:
                runtime_plugin_recovery_task.cancel()
                with suppress(asyncio.CancelledError):
                    await runtime_plugin_recovery_task
            if runtime_state_cleanup_task is not None:
                runtime_state_cleanup_task.cancel()
                with suppress(asyncio.CancelledError):
                    await runtime_state_cleanup_task
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
            await quick_interactions.aclose()
            await notification_service.close()
            maintenance_terminal.close()
            ai_session_manager.close()
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
    application.state.deployment_package = deployment_package
    application.state.instance_id = instance_id
    application.state.detected_platform = detected_platform
    application.state.tailnet_listener_available = None
    application.state.tailnet_listener_hosts = ()
    application.state.ai_session_manager = ai_session_manager
    application.state.codex_rate_limits = codex_rate_limits
    application.state.ai_usage = ai_usage
    application.state.quick_interactions = quick_interactions
    application.state.task_orchestrator = task_orchestrator
    application.state.weekly_report_generation = weekly_report_generation
    application.state.quick_worker_maintenance = quick_worker_maintenance
    application.state.system_upgrade = system_upgrade
    application.state.system_upgrade_maintenance = system_upgrade_maintenance
    application.state.run_system_upgrade = run_system_upgrade
    application.state.system_upgrade_restart_readiness = (
        restart_environment_readiness
    )
    application.state.workstation_rebuild = workstation_rebuild
    application.state.workstation_rebuild_maintenance = workstation_rebuild_maintenance
    application.state.web_restart = web_restart
    application.state.deferred_restart = deferred_restart
    application.state.maintenance_lock = threading.RLock()
    application.state.weixin_chub_mode = weixin_chub_mode
    application.state.plugin_lifecycle = plugin_lifecycle
    application.state.prompt_optimizer_settings = prompt_optimizer_settings
    application.state.orchestration_plugins = orchestration_plugins
    application.state.business_modules = business_modules
    for module in business_modules:
        if module.initialize is not None:
            module.initialize(application, resolved_settings)
    application.state.ai_search = AiSearchService(
        resolved_settings.ai_runtime.shared.state_dir / "ai-search.json"
    )
    application.state.maintenance_terminal = maintenance_terminal
    def check_codex_runtime_account() -> RuntimeAccountEnvironmentState:
        try:
            implementation_id = ai_session_manager.default_submission_implementation_id(
                "codex"
            )
            usage = ai_usage.read(force=True, runtime_id=implementation_id)
        except (ApiError, RuntimeOperationError):
            return RuntimeAccountEnvironmentState(
                state="failed",
                message="登录状态暂不可用",
                checked_at=datetime.now().astimezone(),
            )
        quota = {
            "quota_state": "available" if usage.status == "available" else "unavailable",
            "quota_message": (
                usage.message
                if usage.source == "sub2api" and usage.status != "available"
                else None
            ),
            "five_hour_remaining_percent": (
                usage.five_hour.remaining_percent
                if usage.status == "available" and usage.five_hour is not None
                else None
            ),
            "weekly_remaining_percent": (
                usage.weekly.remaining_percent
                if usage.status == "available" and usage.weekly is not None
                else None
            ),
            "checked_at": usage.checked_at or datetime.now().astimezone(),
        }
        if usage.source == "account_login":
            return RuntimeAccountEnvironmentState(
                state="available",
                auth_mode="account",
                message="ChatGPT 账户已登录",
                **quota,
            )
        if usage.source == "sub2api":
            return RuntimeAccountEnvironmentState(
                state="available",
                auth_mode="api",
                message="API Key 模式已启用",
                **quota,
            )
        return RuntimeAccountEnvironmentState(
            state="failed",
            auth_mode="unknown",
            message="登录状态暂不可用",
            **quota,
        )

    def open_codex_runtime_login_page() -> None:
        implementation_id = ai_session_manager.default_submission_implementation_id(
            "codex"
        )
        ai_usage.open_login_page(runtime_id=implementation_id)

    def codex_runtime_account_available() -> bool:
        try:
            # Codex authentication and usage are Runtime-private.  Their
            # availability must not follow the global default Runtime, which
            # may legitimately point to another implementation.
            implementation_id = ai_session_manager.default_submission_implementation_id(
                "codex"
            )
        except ApiError:
            return False
        imported, enabled = plugin_lifecycle.runtime_implementation_lifecycle_state(
            implementation_id
        )
        if not imported or not enabled:
            return False
        try:
            ai_session_manager.require_implementation_submission(implementation_id)
        except ApiError:
            return False
        return True

    def codex_weixin_command_parser():
        implementation_id = ai_session_manager.default_submission_implementation_id(
            "codex"
        )
        module = ai_session_manager.runtime_plugins.require(implementation_id)
        parser = getattr(module, "parse_weixin_command", None)
        if not callable(parser):
            raise ApiError(
                503,
                "codex_runtime_command_unavailable",
                "Codex Runtime 指令当前不可用。",
            )
        return parser

    application.state.automation_manager = AutomationManager(
        resolved_settings,
        detected_platform=detected_platform,
        codex_account_checker=check_codex_runtime_account,
        codex_account_login_opener=open_codex_runtime_login_page,
        codex_runtime_available=codex_runtime_account_available,
    )
    weixin_chub_mode.codex_auth_reader = (
        application.state.automation_manager.check_codex_runtime_account
    )
    weixin_chub_mode.codex_auth_switcher = (
        application.state.automation_manager.switch_codex_runtime_authentication
    )
    weixin_chub_mode.codex_auth_notifier = (
        completion_notifier.notify_weixin_command_result
    )
    weixin_chub_mode.codex_command_parser = codex_weixin_command_parser
    application.state.openclaw_manager = openclaw_manager
    application.state.notification_service = notification_service

    def start_weixin_workstation_rebuild(source_ip: str) -> object:
        with application.state.maintenance_lock:
            operation, created = workstation_rebuild.begin(source_ip)
            if created:
                try:
                    workstation_rebuild_maintenance.start(operation.operation_id)
                    workstation_rebuild.mark_executor_started(operation.operation_id)
                except Exception as exc:
                    workstation_rebuild.fail(operation.operation_id, str(exc))
        return workstation_rebuild.status_data()

    # The existing fixed-command seam remains the compatibility dispatch path;
    # it now starts the single workstation-rebuild executor.
    weixin_chub_mode.system_upgrade_starter = start_weixin_workstation_rebuild
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
    application.include_router(ai_search_router)
    application.include_router(ai_usage_router)
    application.include_router(automations_router)
    application.include_router(logs_router)
    application.include_router(maintenance_router)
    application.include_router(notifications_router)
    application.include_router(openclaw_router)
    application.include_router(openclaw_wechat_chub_mode_router)
    application.include_router(project_documents_router)
    for module in business_modules:
        if module.api_router is not None:
            application.include_router(module.api_router)
    application.include_router(weekly_reports_router)
    application.include_router(settings_router)
    application.include_router(plugins_router)
    application.include_router(status_router)
    application.include_router(ai_session_api_router)
    application.include_router(maintenance_terminal_api_router)
    for module in business_modules:
        application.mount(
            f"/static/modules/{module.module_id}",
            StaticFiles(directory=module.static_dir),
            name=f"{module.module_id}-static",
        )
    application.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
    application.include_router(ai_web_router)
    application.include_router(maintenance_terminal_web_router)
    application.include_router(web_router)
    return application
