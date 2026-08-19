from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from uuid import uuid4
from urllib.parse import urlsplit

import httpx
import websockets
from fastapi import APIRouter, Depends, Query, Request, Response, WebSocket
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from starlette.websockets import WebSocketDisconnect

from app.codex.models import (
    CodexModelCatalogData,
    CodexQuotaData,
    QuickInteractionData,
    QuickInteractionListData,
    QuickInteractionOrder,
    QuickInteractionRequest,
    SessionAccessData,
    SessionCreateRequest,
    SessionInfo,
    SessionListData,
    SessionRenameRequest,
)
from app.codex.quick_interactions import build_task_summary
from app.core.response import ApiError, ApiResponse, error_response
from app.core.security import require_token
from app.services.operation_log import log_operation, write_operation
from app.services.system_upgrade import SystemUpgradeBusy
from app.web.routes import WEB_DIR


COOKIE_NAME = "chub_terminal"
LOGGER = logging.getLogger("hub.codex.terminal")
api_router = APIRouter(
    prefix="/api/codex",
    tags=["codex"],
    dependencies=[Depends(require_token)],
)
web_router = APIRouter(tags=["codex-web"])
templates = Jinja2Templates(directory=WEB_DIR / "templates")


@api_router.get("/sessions", response_model=ApiResponse[SessionListData])
def list_sessions(
    request: Request,
    include_translation: bool = Query(default=False),
) -> ApiResponse[SessionListData]:
    manager = request.app.state.codex_pty_manager
    weixin_chub_mode = request.app.state.weixin_chub_mode
    session_slots = weixin_chub_mode.session_slots_snapshot()
    quick_sessions: dict[str, datetime] = (
        request.app.state.quick_interactions.active_sessions()
    )
    sessions = [
        session.model_copy(
            update={
                "quick_interaction_running": session.id in quick_sessions,
                "quick_interaction_updated_at": quick_sessions.get(session.id),
                "weixin_session_slot": session_slots.get(session.id),
            }
        )
        for session in manager.list_sessions()
        if include_translation or session.workspace_id != "weixin-translation"
    ]
    return ApiResponse(
        data=SessionListData(
            available=manager.available(),
            unavailable_reason=manager.unavailable_reason(),
            dependencies=manager.dependencies(),
            workspaces=manager.workspaces(),
            sessions=sessions,
        )
    )


@api_router.get("/sessions/{session_id}", response_model=ApiResponse[SessionInfo])
def read_session(session_id: str, request: Request) -> ApiResponse[SessionInfo]:
    session = request.app.state.codex_pty_manager.read_session(session_id)
    quick_sessions: dict[str, datetime] = (
        request.app.state.quick_interactions.active_sessions()
    )
    return ApiResponse(
        data=_with_weixin_session_slot(
            request,
            session.model_copy(
                update={
                    "quick_interaction_running": session.id in quick_sessions,
                    "quick_interaction_updated_at": quick_sessions.get(session.id),
                }
            ),
        )
    )


@api_router.get("/models", response_model=ApiResponse[CodexModelCatalogData])
def list_models(request: Request) -> ApiResponse[CodexModelCatalogData]:
    return ApiResponse(data=request.app.state.codex_pty_manager.read_model_catalog())


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
            session = request.app.state.codex_pty_manager.create_session(
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
    "/sessions/{session_id}/access",
    response_model=ApiResponse[SessionAccessData],
)
def access_session(
    session_id: str,
    request: Request,
    response: Response,
) -> ApiResponse[SessionAccessData]:
    try:
        with request.app.state.quick_interactions.terminal_access_guard(session_id):
            request.app.state.codex_pty_manager.ensure_terminal(session_id)
        request.app.state.terminal_tickets.revoke_session(session_id)
        ticket = request.app.state.terminal_tickets.issue(session_id)
    except Exception:
        log_operation(
            request,
            action="access_codex_session",
            status="failed",
            target=session_id,
        )
        raise
    response.set_cookie(
        COOKIE_NAME,
        ticket,
        max_age=request.app.state.terminal_tickets.ttl_seconds,
        httponly=True,
        samesite="strict",
        secure=False,
        path=f"/codex/{session_id}",
    )
    log_operation(
        request,
        action="access_codex_session",
        status="succeeded",
        target=session_id,
    )
    return ApiResponse(
        data=SessionAccessData(
            terminal_url=f"/codex/{session_id}",
            expires_in=request.app.state.terminal_tickets.ttl_seconds,
        )
    )


@api_router.post(
    "/sessions/{session_id}/stop",
    response_model=ApiResponse[SessionInfo],
)
async def stop_session(session_id: str, request: Request) -> ApiResponse[SessionInfo]:
    try:
        def stop_with_guard() -> SessionInfo:
            with request.app.state.quick_interactions.stop_operation_guard(session_id):
                request.app.state.quick_interactions.cancel_codex_session(session_id)
                request.app.state.terminal_tickets.revoke_session(session_id)
                request.app.state.terminal_connections.close_session(session_id)
                return request.app.state.codex_pty_manager.stop_session(session_id)

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
            request.app.state.codex_pty_manager.rename_session,
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
        manager = request.app.state.codex_pty_manager
        session_slot = request.app.state.weixin_chub_mode.session_slot(session_id)

        def submit_codex():
            with quick_interactions.session_operation_guard(session_id):
                session = manager.get_session(session_id)

                def submit_with_session_context():
                    session_title = (
                        build_task_summary(session.title or payload.prompt)
                        if session_slot is not None
                        else None
                    )
                    return quick_interactions.submit(
                        session_id,
                        payload.prompt,
                        operation_id=operation_id,
                        source_ip=source_ip,
                        weixin_session_slot=session_slot,
                        weixin_session_title=session_title,
                    )

                def take_over_idle_terminal() -> None:
                    """Release Chub's idle terminal before Quick Worker becomes writer."""
                    current = manager.get_session(session_id)
                    if current.status != "running":
                        return
                    if current.activity == "working":
                        raise ApiError(
                            409,
                            "quick_interaction_terminal_working",
                            "实时终端正在执行，请等待当前任务结束。",
                        )
                    if (
                        current.activity == "unknown"
                        and not payload.confirm_stop_unknown_terminal
                    ):
                        raise ApiError(
                            409,
                            "quick_interaction_terminal_confirmation_required",
                            "当前实时终端状态无法确认，请确认停止后再执行。",
                        )
                    if current.activity not in {"idle", "unknown"}:
                        raise ApiError(
                            409,
                            "quick_interaction_terminal_active",
                            "当前实时终端状态不允许快速交互。",
                        )
                    native_session_id = current.native_session_id
                    request.app.state.terminal_tickets.revoke_session(session_id)
                    request.app.state.terminal_connections.close_session(session_id)
                    manager.stop_session(session_id)
                    if (
                        native_session_id
                        and not manager.wait_for_writer_release(native_session_id)
                    ):
                        raise ApiError(
                            409,
                            "quick_interaction_writer_active",
                            "Codex Session 正在由其他进程使用，请等待任务结束或停止实时终端。",
                        )

                if session.status == "running":
                    if session.activity == "working":
                        raise ApiError(
                            409,
                            "quick_interaction_terminal_working",
                            "实时终端正在执行，请等待当前任务结束。",
                        )
                    if (
                        session.activity == "unknown"
                        and not payload.confirm_stop_unknown_terminal
                    ):
                        raise ApiError(
                            409,
                            "quick_interaction_terminal_confirmation_required",
                            "当前实时终端状态无法确认，请确认停止后再执行。",
                        )
                    if session.activity == "idle":
                        take_over_idle_terminal()
                        return submit_with_session_context()
                    session = manager.get_session(session_id)
                    if session.status == "running" and session.activity == "working":
                        raise ApiError(
                            409,
                            "quick_interaction_terminal_working",
                            "实时终端正在执行，请等待当前任务结束。",
                        )
                    if (
                        session.status == "running"
                        and session.activity == "unknown"
                        and not payload.confirm_stop_unknown_terminal
                    ):
                        raise ApiError(
                            409,
                            "quick_interaction_terminal_confirmation_required",
                            "当前实时终端状态无法确认，请确认停止后再执行。",
                        )
                    take_over_idle_terminal()
                return submit_with_session_context()

        task = await asyncio.to_thread(submit_codex)
    except ApiError as exc:
        if exc.code != "quick_interaction_terminal_confirmation_required":
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
    return ApiResponse(
        data=QuickInteractionData(task=request.app.state.quick_interactions.get(task_id))
    )


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
    try:
        def archive_with_guard() -> None:
            with request.app.state.quick_interactions.destructive_operation_guard(session_id):
                request.app.state.terminal_tickets.revoke_session(session_id)
                request.app.state.terminal_connections.close_session(session_id)
                request.app.state.codex_pty_manager.archive_session(session_id)

        await asyncio.to_thread(archive_with_guard)
    except Exception:
        log_operation(
            request,
            action="archive_codex_session",
            status="failed",
            target=session_id,
        )
        raise
    await asyncio.to_thread(_release_weixin_session_slot, request, session_id)
    log_operation(
        request,
        action="archive_codex_session",
        status="succeeded",
        target=session_id,
    )
    return ApiResponse(data=None)


@api_router.delete("/sessions/{session_id}", response_model=ApiResponse[None])
async def delete_session(session_id: str, request: Request) -> ApiResponse[None]:
    try:
        def delete_with_guard() -> None:
            with request.app.state.quick_interactions.destructive_operation_guard(session_id):
                request.app.state.terminal_tickets.revoke_session(session_id)
                request.app.state.terminal_connections.close_session(session_id)
                request.app.state.codex_pty_manager.delete_session(session_id)

        await asyncio.to_thread(delete_with_guard)
    except Exception:
        log_operation(
            request,
            action="delete_codex_session",
            status="failed",
            target=session_id,
        )
        raise
    await asyncio.to_thread(_release_weixin_session_slot, request, session_id)
    log_operation(
        request,
        action="delete_codex_session",
        status="succeeded",
        target=session_id,
    )
    return ApiResponse(data=None)


def _release_weixin_session_slot(request: Request, session_id: str) -> None:
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
        return
    write_operation(
        operation_id=operation_id,
        action="weixin_chub_mode_session_slot_release",
        status="succeeded",
        target=session_id,
        source_ip=source_ip,
    )


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


def _terminal_authorized(connection: Request | WebSocket, session_id: str) -> bool:
    return connection.app.state.terminal_tickets.valid(
        connection.cookies.get(COOKIE_NAME),
        session_id,
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
    return templates.TemplateResponse(
        request=request,
        name="quick_interaction_conversation.html",
        context={"session_id": session_id},
    )


@web_router.get(
    "/codex/{session_id}",
    response_class=HTMLResponse,
    include_in_schema=False,
)
async def terminal_page(request: Request, session_id: str) -> HTMLResponse:
    if not _terminal_authorized(request, session_id):
        raise ApiError(401, "terminal_access_required", "Terminal access expired")
    try:
        with request.app.state.system_upgrade.mutation_guard():
            session = request.app.state.codex_pty_manager.require_terminal_access(
                session_id
            )
            page = request.app.state.terminal_connections.open_page(
                session_id,
                request.cookies[COOKIE_NAME],
            )
    except SystemUpgradeBusy as exc:
        raise ApiError(
            409,
            "system_upgrade_in_progress",
            "系统升级期间暂不建立新的终端连接。",
        ) from exc
    return templates.TemplateResponse(
        request=request,
        name="terminal.html",
        context={
            "session": session,
            "page": page,
        },
    )


@web_router.get(
    "/codex/{session_id}/connection/{page_id}",
    include_in_schema=False,
)
async def terminal_connection_status(
    request: Request,
    session_id: str,
    page_id: str,
) -> JSONResponse:
    state = request.app.state.terminal_connections.page_state(session_id, page_id)
    if state is None:
        return JSONResponse({"state": "unknown"}, status_code=404)
    return JSONResponse({"state": state})


@web_router.websocket("/codex/{session_id}/terminal/ws")
async def terminal_websocket(websocket: WebSocket, session_id: str) -> None:
    if not _terminal_authorized(websocket, session_id) or not _valid_origin(websocket):
        await websocket.close(code=4401)
        return
    offered = websocket.headers.get("sec-websocket-protocol", "")
    if "tty" not in {item.strip() for item in offered.split(",")}:
        await websocket.close(code=4400)
        return
    page_id = websocket.query_params.get("page_id")
    if not page_id:
        await websocket.close(code=4401)
        return
    manager = websocket.app.state.codex_pty_manager
    ticket = websocket.cookies[COOKIE_NAME]
    connection = None
    try:
        with websocket.app.state.system_upgrade.mutation_guard():
            await websocket.accept(subprotocol="tty")
            LOGGER.info(
                "terminal_websocket_accepted session_id=%s page_id=%s",
                session_id,
                page_id,
            )
            connection, released = await websocket.app.state.terminal_connections.claim(
                session_id,
                ticket,
                page_id,
            )
            if not released:
                LOGGER.warning(
                    "session_id=%s old terminal connection did not release; recycling ttyd",
                    session_id,
                )
                await asyncio.to_thread(manager.restart_terminal_backend, session_id)
            backend_url = manager.backend_ws_url(session_id)
            session = manager.get_session(session_id)
    except SystemUpgradeBusy:
        await websocket.close(code=4412, reason="System upgrade in progress")
        return
    except ValueError:
        await websocket.close(code=4401, reason="Terminal page access expired")
        return
    except (ApiError, OSError, RuntimeError) as exc:
        if connection is not None:
            websocket.app.state.terminal_connections.release(connection)
        LOGGER.warning(
            "terminal_websocket_setup_failed session_id=%s page_id=%s error_type=%s",
            session_id,
            page_id,
            type(exc).__name__,
        )
        try:
            await websocket.close(code=1011, reason="Terminal backend unavailable")
        except RuntimeError:
            pass
        return
    try:
        async with websockets.connect(
            backend_url,
            origin=manager.backend_origin(session_id),
            subprotocols=["tty"],
        ) as backend:
            if not websocket.app.state.terminal_connections.activate(connection):
                await websocket.close(code=4410, reason="Terminal connection superseded")
                return

            async def client_to_backend() -> None:
                while True:
                    message = await websocket.receive()
                    if message.get("type") == "websocket.disconnect":
                        return
                    try:
                        with websocket.app.state.system_upgrade.mutation_guard():
                            with websocket.app.state.quick_interactions.terminal_input_guard(
                                session_id
                            ) as allowed:
                                if not allowed:
                                    continue
                                if message.get("bytes") is not None:
                                    await backend.send(message["bytes"])
                                elif message.get("text") is not None:
                                    await backend.send(message["text"])
                    except SystemUpgradeBusy:
                        await websocket.close(
                            code=4412,
                            reason="System upgrade in progress",
                        )
                        return

            async def backend_to_client() -> None:
                async for message in backend:
                    if isinstance(message, bytes):
                        await websocket.send_bytes(message)
                    else:
                        await websocket.send_text(message)

            tasks = [
                asyncio.create_task(client_to_backend()),
                asyncio.create_task(backend_to_client()),
                asyncio.create_task(connection.takeover.wait()),
            ]
            done, pending = await asyncio.wait(
                tasks,
                return_when=asyncio.FIRST_COMPLETED,
            )
            for task in pending:
                task.cancel()
            await asyncio.gather(*pending, return_exceptions=True)
            for task in done:
                if not task.cancelled():
                    task.result()
    except WebSocketDisconnect as exc:
        LOGGER.info(
            "terminal_websocket_disconnected session_id=%s page_id=%s code=%s",
            session_id,
            page_id,
            exc.code,
        )
        return
    except (OSError, RuntimeError, websockets.WebSocketException) as exc:
        LOGGER.warning(
            "terminal_websocket_failed session_id=%s page_id=%s error_type=%s",
            session_id,
            page_id,
            type(exc).__name__,
        )
        return
    finally:
        if connection is not None:
            websocket.app.state.terminal_connections.release(connection)
        if connection is not None and connection.takeover.is_set():
            try:
                await websocket.close(code=4409, reason="Terminal opened elsewhere")
            except RuntimeError:
                pass


@web_router.api_route(
    "/codex/{session_id}/terminal",
    methods=["GET", "HEAD"],
    include_in_schema=False,
)
@web_router.api_route(
    "/codex/{session_id}/terminal/{path:path}",
    methods=["GET", "POST", "HEAD"],
    include_in_schema=False,
)
async def terminal_http(
    request: Request,
    session_id: str,
    path: str = "",
) -> Response:
    if not _terminal_authorized(request, session_id):
        return error_response(401, "terminal_access_required", "Terminal access expired")
    if not path and request.url.path.endswith("/terminal"):
        return RedirectResponse(
            url=f"/codex/{session_id}/terminal/",
            status_code=307,
        )
    manager = request.app.state.codex_pty_manager
    try:
        with request.app.state.system_upgrade.mutation_guard():
            backend_url = manager.backend_url(
                session_id,
                path,
                request.url.query,
            )
        headers = {
            key: value
            for key, value in request.headers.items()
            if key.lower() not in {"host", "content-length", "accept-encoding", "cookie"}
        }
        async with httpx.AsyncClient(timeout=10) as client:
            upstream = await client.request(
                request.method,
                backend_url,
                headers=headers,
                content=await request.body(),
            )
    except SystemUpgradeBusy:
        return error_response(
            409,
            "system_upgrade_in_progress",
            "系统升级期间暂不建立新的终端连接。",
        )
    except httpx.HTTPError as exc:
        LOGGER.warning(
            "terminal_http_proxy_failed session_id=%s error_type=%s",
            session_id,
            type(exc).__name__,
        )
        return error_response(502, "terminal_proxy_failed", "Terminal proxy failed")
    except ApiError as exc:
        LOGGER.warning(
            "terminal_http_backend_unavailable session_id=%s error_code=%s",
            session_id,
            exc.code,
        )
        return error_response(502, "terminal_proxy_failed", "Terminal proxy failed")
    response_headers = {
        key: value
        for key, value in upstream.headers.items()
        if key.lower()
        not in {
            "connection",
            "content-encoding",
            "content-length",
            "content-security-policy",
            "location",
            "transfer-encoding",
            "x-frame-options",
        }
    }
    return Response(
        content=upstream.content,
        status_code=upstream.status_code,
        headers=response_headers,
    )


def _valid_origin(websocket: WebSocket) -> bool:
    origin = websocket.headers.get("origin")
    if not origin:
        return False
    parsed = urlsplit(origin)
    return parsed.scheme in {"http", "https"} and parsed.netloc == websocket.headers.get(
        "host"
    )
