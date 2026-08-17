from __future__ import annotations

import asyncio
import logging
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
from app.api.maintenance import router as maintenance_router
from app.api.notifications import router as notifications_router
from app.api.openclaw import router as openclaw_router
from app.api.openclaw_wechat_chub_mode import router as openclaw_wechat_chub_mode_router
from app.api.project_documents import router as project_documents_router
from app.api.settings import router as settings_router
from app.api.status import router as status_router
from app.codex.connections import TerminalConnectionRegistry
from app.codex.manager import CodexPtyManager
from app.codex.quick_interactions import QuickInteractionManager
from app.codex.rate_limits import CodexRateLimitService
from app.codex.routes import api_router as codex_api_router
from app.codex.routes import web_router as codex_web_router
from app.codex.tickets import TerminalTicketStore
from app.automations.manager import AutomationManager
from app.core.config import PROJECT_ROOT, Settings, load_settings
from app.core.logger import configure_logging
from app.core.network import is_tailscale_ip
from app.core.platform import detect_platform
from app.core.response import (
    ApiError,
    SECURITY_HEADERS,
    api_error_handler,
    http_error_handler,
    internal_error_handler,
    validation_error_handler,
)
from app.services.openclaw import OpenClawManager
from app.services.openclaw_completion_notifications import OpenClawCompletionNotifier
from app.services.openclaw_weixin_chub_messages import usage_message
from app.services.deferred_restart import DeferredRestartCoordinator
from app.services.openclaw_weixin_chub_mode import WeixinChubModeManager
from app.services.restart_command import RestartProcess, launch_restart_process
from app.services.system_status import collect_system_status
from app.services.weixin_translation import WeixinTranslationManager
from app.notifications import NotificationService
from app.web.routes import STATIC_DIR, router as web_router


async def _confirm_healthy_instance(
    settings: Settings,
    instance_id: str,
) -> None:
    logger = logging.getLogger("hub.deferred_restart")
    host = settings.server.host
    if host == "0.0.0.0":
        host = "127.0.0.1"
    elif host == "::":
        host = "::1"
    health_url = httpx.URL(
        scheme="http",
        host=host,
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


def create_app(settings: Settings | None = None) -> FastAPI:
    resolved_settings = settings or load_settings()
    instance_id = uuid4().hex
    configure_logging(resolved_settings.logs)

    detected_platform = detect_platform()
    logger = logging.getLogger("hub.startup")
    tailscale_access_available = (
        resolved_settings.security.allow_tailscale
        and is_tailscale_ip(resolved_settings.server.host)
    )
    if not resolved_settings.security.token and not tailscale_access_available:
        logger.warning(
            "HUB_TOKEN is not set; health check remains available but protected APIs are disabled"
        )
    codex_pty_available = is_tailscale_ip(resolved_settings.server.host)
    if not codex_pty_available:
        logger.warning(
            "server host %s is not a Tailscale IP; Codex PTY is disabled",
            resolved_settings.server.host,
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

    codex_pty_manager = CodexPtyManager(resolved_settings)
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
    terminal_tickets = TerminalTicketStore(
        resolved_settings.codex_pty.ticket_ttl_seconds
    )
    terminal_connections = TerminalConnectionRegistry()
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

    def archive_weixin_session(session_id: str) -> None:
        with quick_interactions.destructive_operation_guard(session_id):
            session = codex_pty_manager.get_session(session_id)
            if session.activity in {"working", "unknown"}:
                raise ApiError(
                    409,
                    "quick_interaction_terminal_working",
                    "Codex session is active or its state is unknown",
                )
            if (
                session.codex_session_id
                and codex_pty_manager.has_active_writer(session.codex_session_id)
            ):
                raise ApiError(
                    409,
                    "quick_interaction_writer_active",
                    "Codex session still has an active writer",
                )
            terminal_tickets.revoke_session(session_id)
            terminal_connections.close_session(session_id)
            codex_pty_manager.archive_session(session_id)

    def stop_weixin_session(session_id: str):
        with quick_interactions.stop_operation_guard(session_id):
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
        system_status_reader=lambda: collect_system_status(
            resolved_settings,
            detected_platform,
        ),
        restart_coordinator=deferred_restart,
        restart_notifier=completion_notifier.notify_weixin_restart_command,
        ai_usage_reader=ai_usage,
        session_stopper=stop_weixin_session,
        session_stop_notifier=completion_notifier.notify_weixin_command_result,
        translation_result_notifier=(
            completion_notifier.notify_weixin_optimized_task
        ),
    )
    weixin_translation.set_completion_handler(
        weixin_chub_mode.complete_optimized_task
    )
    weixin_translation.set_notification_handler(
        weixin_chub_mode.notify_optimized_task_outcome
    )
    quick_interactions.set_task_finished_handler(
        weixin_chub_mode.record_request_task_completion
    )

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
    completion_notifier.session_slot_validator = (
        weixin_chub_mode.session_slot_matches
    )
    completion_notifier.session_current_validator = (
        weixin_chub_mode.session_slot_is_current
    )
    completion_notifier.codex_status_reader = weixin_chub_mode.codex_status_message
    completion_notifier.completion_usage_reader = lambda: usage_message(
        ai_usage.read(force=False)
    )
    completion_notifier.request_slot_validator = (
        weixin_chub_mode.request_backlog.slot_matches
    )
    codex_pty_manager.set_quick_interaction_checker(quick_interactions.is_running)
    openclaw_manager = OpenClawManager()
    notification_service = NotificationService(resolved_settings.notifications)

    @asynccontextmanager
    async def lifespan(_application: FastAPI):
        restart_recovery_task = None
        await asyncio.to_thread(quick_interactions.start_worker_reconciliation)
        await asyncio.to_thread(weixin_chub_mode.reconcile_request_runs)
        weixin_chub_mode.start_status_cache()
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
            if restart_recovery_task is not None:
                restart_recovery_task.cancel()
                with suppress(asyncio.CancelledError):
                    await restart_recovery_task
            weixin_chub_mode.close()
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
    application.state.codex_pty_manager = codex_pty_manager
    application.state.codex_rate_limits = codex_rate_limits
    application.state.ai_usage = ai_usage
    application.state.quick_interactions = quick_interactions
    application.state.deferred_restart = deferred_restart
    application.state.weixin_chub_mode = weixin_chub_mode
    application.state.weixin_translation = weixin_translation
    application.state.terminal_tickets = terminal_tickets
    application.state.terminal_connections = terminal_connections
    application.state.automation_manager = AutomationManager(resolved_settings)
    application.state.openclaw_manager = openclaw_manager
    application.state.notification_service = notification_service
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
    application.include_router(settings_router)
    application.include_router(status_router)
    application.include_router(codex_api_router)
    application.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
    application.include_router(codex_web_router)
    application.include_router(web_router)
    return application
