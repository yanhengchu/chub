from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from uuid import uuid4

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from app.codex.models import (
    CodexModelCatalogData,
    CodexQuotaData,
    QuickInteractionData,
    QuickInteractionListData,
    QuickInteractionOrder,
    QuickInteractionRequest,
    RuntimeEnablementUpdateRequest,
    RuntimeManagementData,
    RuntimeImplementationData,
    RuntimeImplementationEnabledUpdateRequest,
    RuntimeDefaultImplementationUpdateRequest,
    SessionCreationAvailability,
    SessionCreateRequest,
    SessionConfigurationUpdateRequest,
    SessionInfo,
    SessionListData,
    NativeSessionInfo,
    SessionRuntimeGroup,
    SessionRenameRequest,
)
from app.ai_session.operations import (
    archive_session as archive_session_operation,
    delete_session as delete_session_operation,
    forget_session as forget_session_operation,
)
from app.core.response import ApiError, ApiResponse
from app.core.security import require_trusted_network
from app.services.operation_log import log_operation, write_operation
from app.web.routes import WEB_DIR
from app.web.themes import configure_theme_templates


LOGGER = logging.getLogger("hub.codex")
api_router = APIRouter(
    prefix="/api/codex",
    tags=["codex"],
    dependencies=[Depends(require_trusted_network)],
)
web_router = APIRouter(tags=["codex-web"])
templates = Jinja2Templates(directory=WEB_DIR / "templates")
configure_theme_templates(templates)


def _last_session_activity_at(
    session: SessionInfo,
    quick_activity_times: dict[str, datetime],
) -> datetime | None:
    quick_activity_at = quick_activity_times.get(session.id)
    if quick_activity_at is not None:
        return quick_activity_at
    return session.last_activity_at


@api_router.get("/sessions", response_model=ApiResponse[SessionListData])
def list_sessions(
    request: Request,
) -> ApiResponse[SessionListData]:
    manager = request.app.state.ai_session_manager
    runtime_registered = (
        not isinstance(getattr(manager, "runtime_id", None), str)
        or manager.runtime_id in manager.runtime_plugins.runtime_ids()
    )
    weixin_chub_mode = request.app.state.weixin_chub_mode
    session_slots = weixin_chub_mode.session_slots_snapshot()
    quick_sessions: dict[str, datetime] = (
        request.app.state.quick_interactions.active_sessions()
    )
    quick_activity_times: dict[str, datetime] = (
        request.app.state.quick_interactions.session_activity_times()
    )
    listed_sessions: list[SessionInfo] = []
    native_sessions: list[NativeSessionInfo] = []
    if runtime_registered:
        try:
            show_internal_native_session = (
                request.app.state.weixin_translation.status().show_internal_native_session
            )
        except OSError:
            show_internal_native_session = False
        combined_sessions = manager.list_sessions_with_native_sessions(
            include_internal_translation_native_sessions=show_internal_native_session,
        )
        if (
            isinstance(combined_sessions, tuple)
            and len(combined_sessions) == 2
            and isinstance(combined_sessions[0], list)
            and isinstance(combined_sessions[1], list)
            and all(isinstance(item, NativeSessionInfo) for item in combined_sessions[1])
        ):
            listed_sessions, native_sessions = combined_sessions
        else:
            listed_sessions = manager.list_sessions()
    sessions = [
        session.model_copy(
            update={
                "quick_interaction_running": session.id in quick_sessions,
                "quick_interaction_updated_at": quick_sessions.get(session.id),
                "last_activity_at": _last_session_activity_at(
                    session,
                    quick_activity_times,
                ),
                "weixin_session_slot": session_slots.get(session.id),
            }
        )
        for session in listed_sessions
        if session.workspace_id != "weixin-translation"
    ]
    available, unavailable_reason = manager.submission_available()
    if available:
        available, unavailable_reason = (
            request.app.state.quick_interactions.quick_session_creation_availability()
        )
    runtime_groups = [
        SessionRuntimeGroup(runtime_id=item.runtime_id, name=item.name)
        for item in manager.read_runtime_management().runtimes
        if item.enabled
    ]
    return ApiResponse(
        data=SessionListData(
            available=available,
            unavailable_reason=unavailable_reason,
            runtime_registered=runtime_registered,
            quick_creation=SessionCreationAvailability(
                available=available,
                reason=unavailable_reason,
            ),
            dependencies=manager.dependencies(),
            workspaces=manager.workspaces(),
            sessions=sessions,
            native_sessions=native_sessions,
            runtime_groups=runtime_groups,
        )
    )


@api_router.get("/runtimes", response_model=ApiResponse[RuntimeManagementData])
def read_runtime_management(request: Request) -> ApiResponse[RuntimeManagementData]:
    return ApiResponse(data=request.app.state.ai_session_manager.read_runtime_management())


@api_router.get("/runtime-implementations", response_model=ApiResponse[RuntimeImplementationData])
def read_runtime_implementations(request: Request) -> ApiResponse[RuntimeImplementationData]:
    return ApiResponse(data=request.app.state.ai_session_manager.read_runtime_implementations())


@api_router.put(
    "/runtime-implementations/{implementation_id}/enabled",
    response_model=ApiResponse[RuntimeImplementationData],
)
def update_runtime_implementation_enabled(
    implementation_id: str,
    payload: RuntimeImplementationEnabledUpdateRequest,
    request: Request,
) -> ApiResponse[RuntimeImplementationData]:
    return ApiResponse(data=request.app.state.ai_session_manager.update_runtime_implementation_enabled(implementation_id, payload.enabled))


@api_router.put(
    "/runtime-implementations/default",
    response_model=ApiResponse[RuntimeImplementationData],
)
def update_default_runtime_implementation(
    payload: RuntimeDefaultImplementationUpdateRequest,
    request: Request,
) -> ApiResponse[RuntimeImplementationData]:
    return ApiResponse(data=request.app.state.ai_session_manager.update_default_implementation(payload.implementation_id))


@api_router.put(
    "/runtimes/{runtime_id}",
    response_model=ApiResponse[RuntimeManagementData],
)
def update_runtime_enablement(
    runtime_id: str,
    payload: RuntimeEnablementUpdateRequest,
    request: Request,
) -> ApiResponse[RuntimeManagementData]:
    try:
        data = request.app.state.ai_session_manager.update_runtime_enabled(
            runtime_id,
            payload.enabled,
        )
    except Exception:
        log_operation(
            request,
            action="update_ai_runtime_enablement",
            status="failed",
            target=runtime_id,
        )
        raise
    log_operation(
        request,
        action="update_ai_runtime_enablement",
        status="succeeded",
        target=runtime_id,
    )
    return ApiResponse(data=data)


@api_router.get("/sessions/{session_id}", response_model=ApiResponse[SessionInfo])
def read_session(session_id: str, request: Request) -> ApiResponse[SessionInfo]:
    session = request.app.state.ai_session_manager.read_session(session_id)
    quick_sessions: dict[str, datetime] = (
        request.app.state.quick_interactions.active_sessions()
    )
    quick_activity_times: dict[str, datetime] = (
        request.app.state.quick_interactions.session_activity_times()
    )
    return ApiResponse(
        data=_with_weixin_session_slot(
            request,
            session.model_copy(
                update={
                    "quick_interaction_running": session.id in quick_sessions,
                    "quick_interaction_updated_at": quick_sessions.get(session.id),
                    "last_activity_at": _last_session_activity_at(
                        session,
                        quick_activity_times,
                    ),
                }
            ),
        )
    )


@api_router.get("/models", response_model=ApiResponse[CodexModelCatalogData])
def list_models(request: Request) -> ApiResponse[CodexModelCatalogData]:
    return ApiResponse(data=request.app.state.ai_session_manager.read_model_catalog())


@api_router.get("/quota", response_model=ApiResponse[CodexQuotaData])
def read_quota(
    request: Request,
    refresh: bool = Query(default=False),
) -> ApiResponse[CodexQuotaData]:
    return ApiResponse(data=request.app.state.codex_rate_limits.read(force=refresh))


@api_router.post("/sessions", response_model=ApiResponse[SessionInfo])
def create_session(
    payload: SessionCreateRequest,
    request: Request,
) -> ApiResponse[SessionInfo]:
    try:
        with request.app.state.quick_interactions.session_creation_guard():
            session = request.app.state.ai_session_manager.create_session(
                payload.workspace_id,
                payload.permission_mode,
                payload.model,
                payload.reasoning_effort,
            )
    except Exception:
        log_operation(
            request,
            action="create_codex_session",
            status="failed",
            target=payload.workspace_id,
        )
        raise
    log_operation(
        request,
        action="create_codex_session",
        status="succeeded",
        target=session.id,
    )
    return ApiResponse(data=_with_weixin_session_slot(request, session))



@api_router.post(
    "/sessions/{session_id}/stop",
    response_model=ApiResponse[SessionInfo],
)
async def stop_session(session_id: str, request: Request) -> ApiResponse[SessionInfo]:
    try:
        def stop_with_guard() -> SessionInfo:
            with request.app.state.quick_interactions.stop_operation_guard(session_id):
                request.app.state.ai_session_manager.ensure_stop_allowed(session_id)
                request.app.state.quick_interactions.cancel_codex_session(session_id)
                return request.app.state.ai_session_manager.stop_session(session_id)

        data = await asyncio.to_thread(stop_with_guard)
    except Exception:
        log_operation(
            request,
            action="stop_codex_session",
            status="failed",
            target=session_id,
        )
        raise
    log_operation(
        request,
        action="stop_codex_session",
        status="succeeded",
        target=session_id,
    )
    return ApiResponse(data=_with_weixin_session_slot(request, data))


@api_router.patch(
    "/sessions/{session_id}/title",
    response_model=ApiResponse[SessionInfo],
)
async def rename_session(
    session_id: str,
    payload: SessionRenameRequest,
    request: Request,
) -> ApiResponse[SessionInfo]:
    operation_id = uuid4().hex
    for status in ("requested", "started"):
        log_operation(
            request,
            action="rename_codex_session",
            status=status,
            target=session_id,
            operation_id=operation_id,
        )
    try:
        data = await asyncio.to_thread(
            request.app.state.ai_session_manager.rename_session,
            session_id,
            payload.title,
        )
    except Exception:
        log_operation(
            request,
            action="rename_codex_session",
            status="failed",
            target=session_id,
            operation_id=operation_id,
        )
        raise
    log_operation(
        request,
        action="rename_codex_session",
        status="succeeded",
        target=session_id,
        operation_id=operation_id,
    )
    return ApiResponse(data=_with_weixin_session_slot(request, data))


@api_router.patch(
    "/sessions/{session_id}/configuration",
    response_model=ApiResponse[SessionInfo],
)
async def update_session_configuration(
    session_id: str,
    payload: SessionConfigurationUpdateRequest,
    request: Request,
) -> ApiResponse[SessionInfo]:
    try:
        data = await asyncio.to_thread(
            request.app.state.quick_interactions.update_session_configuration,
            session_id,
            payload.permission_mode,
            payload.model,
            payload.reasoning_effort,
        )
    except Exception:
        log_operation(
            request,
            action="update_codex_session_configuration",
            status="failed",
            target=session_id,
        )
        raise
    log_operation(
        request,
        action="update_codex_session_configuration",
        status="succeeded",
        target=session_id,
    )
    return ApiResponse(data=_with_weixin_session_slot(request, data))


@api_router.post(
    "/sessions/{session_id}/quick-interactions",
    response_model=ApiResponse[QuickInteractionData],
)
async def submit_quick_interaction(
    session_id: str,
    payload: QuickInteractionRequest,
    request: Request,
) -> ApiResponse[QuickInteractionData]:
    operation_id = uuid4().hex
    source_ip = request.client.host if request.client else "unknown"
    try:
        quick_interactions = request.app.state.quick_interactions
        manager = request.app.state.ai_session_manager
        manager.require_session_access(session_id)

        def submit_codex():
            with quick_interactions.session_operation_guard(session_id):
                session = manager.get_session(session_id)

                return quick_interactions.submit(
                    session_id,
                    payload.prompt,
                    operation_id=operation_id,
                    source_ip=source_ip,
                )

        task = await asyncio.to_thread(submit_codex)
    except ApiError:
        write_operation(
            operation_id=operation_id,
            action="quick_interaction",
            status="failed",
            target=session_id,
            source_ip=source_ip,
        )
        raise
    except Exception:
        write_operation(
            operation_id=operation_id,
            action="quick_interaction",
            status="failed",
            target=session_id,
            source_ip=source_ip,
        )
        raise
    return ApiResponse(data=QuickInteractionData(task=task))


@api_router.get(
    "/quick-interactions/{task_id}",
    response_model=ApiResponse[QuickInteractionData],
)
def get_quick_interaction(task_id: str, request: Request) -> ApiResponse[QuickInteractionData]:
    task = request.app.state.quick_interactions.get(task_id)
    request.app.state.ai_session_manager.require_session_access(task.session_id)
    return ApiResponse(data=QuickInteractionData(task=task))


@api_router.get(
    "/sessions/{session_id}/quick-interactions",
    response_model=ApiResponse[QuickInteractionListData],
)
def list_quick_interactions(
    session_id: str,
    request: Request,
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=5, ge=1, le=20),
    order: QuickInteractionOrder = Query(default="task"),
    before_created_at: datetime | None = Query(default=None),
    before_id: str | None = Query(default=None, min_length=1, max_length=100),
) -> ApiResponse[QuickInteractionListData]:
    has_created_at = before_created_at is not None
    has_id = before_id is not None
    if has_created_at != has_id:
        raise ApiError(
            422,
            "invalid_quick_interaction_cursor",
            "时间线游标必须同时包含创建时间和任务 ID。",
        )
    if (
        before_created_at is not None
        and before_created_at.utcoffset() is None
    ):
        raise ApiError(
            422,
            "invalid_quick_interaction_cursor",
            "时间线游标的创建时间必须包含时区。",
        )
    if order == "timeline" and offset != 0:
        raise ApiError(
            422,
            "invalid_quick_interaction_cursor",
            "timeline 排序必须使用时间线游标，不能使用非零 offset。",
        )
    if has_created_at and order != "timeline":
        raise ApiError(
            422,
            "invalid_quick_interaction_cursor",
            "时间线游标只能用于 timeline 排序。",
        )
    request.app.state.ai_session_manager.require_session_access(session_id)
    tasks = request.app.state.quick_interactions.list_for_session(
        session_id,
        order=order,
    )
    if before_created_at is not None and before_id is not None:
        eligible = [
            task
            for task in tasks
            if (task.created_at, task.id) < (before_created_at, before_id)
        ]
        page = eligible[:limit]
        has_more = len(eligible) > len(page)
    else:
        page = tasks[offset : offset + limit]
        has_more = offset + len(page) < len(tasks)
    return ApiResponse(
        data=QuickInteractionListData(
            tasks=page,
            total=len(tasks),
            has_more=has_more,
        )
    )


@api_router.post("/sessions/{session_id}/archive", response_model=ApiResponse[None])
async def archive_session(session_id: str, request: Request) -> ApiResponse[None]:
    operation_id = uuid4().hex
    for status in ("requested", "started"):
        log_operation(
            request,
            action="archive_codex_session",
            status=status,
            target=session_id,
            operation_id=operation_id,
        )
    try:
        await asyncio.to_thread(
            archive_session_operation,
            session_id,
            manager=request.app.state.ai_session_manager,
            quick_interactions=request.app.state.quick_interactions,
            release_slot=lambda target_id: _release_weixin_session_slot(
                request, target_id
            ),
        )
    except Exception:
        log_operation(
            request,
            action="archive_codex_session",
            status="failed",
            target=session_id,
            operation_id=operation_id,
        )
        raise
    log_operation(
        request,
        action="archive_codex_session",
        status="succeeded",
        target=session_id,
        operation_id=operation_id,
    )
    return ApiResponse(data=None)


@api_router.delete("/sessions/{session_id}", response_model=ApiResponse[None])
async def delete_session(session_id: str, request: Request) -> ApiResponse[None]:
    operation_id = uuid4().hex
    for status in ("requested", "started"):
        log_operation(
            request,
            action="delete_codex_session",
            status=status,
            target=session_id,
            operation_id=operation_id,
        )
    try:
        await asyncio.to_thread(
            delete_session_operation,
            session_id,
            manager=request.app.state.ai_session_manager,
            quick_interactions=request.app.state.quick_interactions,
            release_slot=lambda target_id: _release_weixin_session_slot(
                request, target_id
            ),
        )
    except Exception:
        log_operation(
            request,
            action="delete_codex_session",
            status="failed",
            target=session_id,
            operation_id=operation_id,
        )
        raise
    log_operation(
        request,
        action="delete_codex_session",
        status="succeeded",
        target=session_id,
        operation_id=operation_id,
    )
    return ApiResponse(data=None)


@api_router.delete(
    "/sessions/{session_id}/management",
    response_model=ApiResponse[None],
)
async def forget_session(session_id: str, request: Request) -> ApiResponse[None]:
    operation_id = uuid4().hex
    log_operation(
        request,
        action="forget_codex_session",
        status="requested",
        target=session_id,
        operation_id=operation_id,
    )
    log_operation(
        request,
        action="forget_codex_session",
        status="started",
        target=session_id,
        operation_id=operation_id,
    )
    try:
        await asyncio.to_thread(
            forget_session_operation,
            session_id,
            manager=request.app.state.ai_session_manager,
            quick_interactions=request.app.state.quick_interactions,
            release_slot=lambda target_id: _release_weixin_session_slot(
                request, target_id
            ),
        )
    except Exception:
        log_operation(
            request,
            action="forget_codex_session",
            status="failed",
            target=session_id,
            operation_id=operation_id,
        )
        raise
    log_operation(
        request,
        action="forget_codex_session",
        status="succeeded",
        target=session_id,
        operation_id=operation_id,
    )
    return ApiResponse(data=None)


@api_router.post("/native-sessions/{native_action_ref}/archive", response_model=ApiResponse[None])
async def archive_native_session(native_action_ref: str, request: Request) -> ApiResponse[None]:
    operation_id = uuid4().hex
    target = request.app.state.ai_session_manager.native_action_audit_target(native_action_ref)
    log_operation(
        request,
        action="archive_native_session",
        status="requested",
        target=target,
        operation_id=operation_id,
    )
    log_operation(
        request,
        action="archive_native_session",
        status="started",
        target=target,
        operation_id=operation_id,
    )
    try:
        await asyncio.to_thread(
            request.app.state.ai_session_manager.run_discovered_native_action,
            "archive",
            native_action_ref,
        )
    except Exception:
        log_operation(
            request,
            action="archive_native_session",
            status="failed",
            target=target,
            operation_id=operation_id,
        )
        raise
    log_operation(
        request,
        action="archive_native_session",
        status="succeeded",
        target=target,
        operation_id=operation_id,
    )
    return ApiResponse(data=None)


@api_router.delete("/native-sessions/{native_action_ref}", response_model=ApiResponse[None])
async def delete_native_session(native_action_ref: str, request: Request) -> ApiResponse[None]:
    operation_id = uuid4().hex
    target = request.app.state.ai_session_manager.native_action_audit_target(native_action_ref)
    log_operation(
        request,
        action="delete_native_session",
        status="requested",
        target=target,
        operation_id=operation_id,
    )
    log_operation(
        request,
        action="delete_native_session",
        status="started",
        target=target,
        operation_id=operation_id,
    )
    try:
        await asyncio.to_thread(
            request.app.state.ai_session_manager.run_discovered_native_action,
            "delete",
            native_action_ref,
        )
    except Exception:
        log_operation(
            request,
            action="delete_native_session",
            status="failed",
            target=target,
            operation_id=operation_id,
        )
        raise
    log_operation(
        request,
        action="delete_native_session",
        status="succeeded",
        target=target,
        operation_id=operation_id,
    )
    return ApiResponse(data=None)


def _release_weixin_session_slot(request: Request, session_id: str) -> bool:
    operation_id = uuid4().hex
    source_ip = request.client.host if request.client else "unknown"
    for status in ("requested", "started"):
        write_operation(
            operation_id=operation_id,
            action="weixin_chub_mode_session_slot_release",
            status=status,
            target=session_id,
            source_ip=source_ip,
        )
    try:
        request.app.state.weixin_chub_mode.release_session_slot(session_id)
    except Exception:
        write_operation(
            operation_id=operation_id,
            action="weixin_chub_mode_session_slot_release",
            status="failed",
            target=session_id,
            source_ip=source_ip,
        )
        LOGGER.warning("Unable to release Weixin Session slot", exc_info=True)
        return False
    write_operation(
        operation_id=operation_id,
        action="weixin_chub_mode_session_slot_release",
        status="succeeded",
        target=session_id,
        source_ip=source_ip,
    )
    return True


def _with_weixin_session_slot(
    request: Request,
    session: SessionInfo,
) -> SessionInfo:
    return session.model_copy(
        update={
            "weixin_session_slot": request.app.state.weixin_chub_mode.session_slot(
                session.id
            )
        }
    )


@web_router.get(
    "/codex/{session_id}/quick-interactions/conversation",
    response_class=HTMLResponse,
    include_in_schema=False,
)
async def quick_interaction_conversation_page(
    request: Request,
    session_id: str,
) -> HTMLResponse:
    request.app.state.ai_session_manager.require_session_access(session_id)
    return templates.TemplateResponse(
        request=request,
        name="quick_interaction_conversation.html",
        context={"session_id": session_id},
    )
