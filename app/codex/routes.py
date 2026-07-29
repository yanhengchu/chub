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
    QuickInteractionData,
    QuickInteractionListData,
    QuickInteractionPinRequest,
    QuickInteractionRequest,
    SessionAccessData,
    SessionCreateRequest,
    SessionInfo,
    SessionListData,
    SessionPermissionData,
    SessionPermissionRequest,
)
from app.core.response import ApiError, ApiResponse, error_response
from app.core.security import require_token
from app.services.operation_log import log_operation, write_operation
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
def list_sessions(request: Request) -> ApiResponse[SessionListData]:
    manager = request.app.state.codex_pty_manager
    quick_sessions: dict[str, datetime] = (
        request.app.state.quick_interactions.active_sessions()
    )
    llm_sessions: dict[str, datetime] = (
        request.app.state.quick_interactions.llm_active_sessions()
    )
    sessions = [
        session.model_copy(
            update={
                "quick_interaction_running": session.id in quick_sessions,
                "quick_interaction_updated_at": quick_sessions.get(session.id),
                "llm_interaction_running": session.id in llm_sessions,
                "llm_interaction_updated_at": llm_sessions.get(session.id),
            }
        )
        for session in manager.list_sessions()
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


@api_router.post("/sessions", response_model=ApiResponse[SessionInfo])
def create_session(
    payload: SessionCreateRequest,
    request: Request,
) -> ApiResponse[SessionInfo]:
    try:
        session = request.app.state.codex_pty_manager.create_session(payload.workspace_id)
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
    return ApiResponse(data=session)


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
            with request.app.state.quick_interactions.session_operation_guard(session_id):
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
    return ApiResponse(data=data)


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
        if payload.engine == "bedrock_api":
            task = quick_interactions.submit_llm(
                session_id,
                payload.prompt,
                operation_id=operation_id,
                source_ip=source_ip,
            )
            return ApiResponse(data=QuickInteractionData(task=task))
        manager = request.app.state.codex_pty_manager

        def submit_codex():
            with quick_interactions.session_operation_guard(session_id):
                session = manager.get_session(session_id)
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
                        return quick_interactions.submit(
                            session_id,
                            payload.prompt,
                            operation_id=operation_id,
                            source_ip=source_ip,
                        )
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
                    request.app.state.terminal_tickets.revoke_session(session_id)
                    request.app.state.terminal_connections.close_session(session_id)
                    if session.status == "running":
                        manager.stop_session(session_id)
                return quick_interactions.submit(
                    session_id,
                    payload.prompt,
                    operation_id=operation_id,
                    source_ip=source_ip,
                )

        task = await asyncio.to_thread(submit_codex)
    except ApiError as exc:
        if exc.code != "quick_interaction_terminal_confirmation_required":
            write_operation(
                operation_id=operation_id,
                action=(
                    "bedrock_quick_interaction"
                    if payload.engine == "bedrock_api"
                    else "quick_interaction"
                ),
                status="failed",
                target=session_id,
                source_ip=source_ip,
            )
        raise
    except Exception:
        write_operation(
            operation_id=operation_id,
            action=(
                "bedrock_quick_interaction"
                if payload.engine == "bedrock_api"
                else "quick_interaction"
            ),
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
) -> ApiResponse[QuickInteractionListData]:
    tasks = request.app.state.quick_interactions.list_for_session(session_id)
    page = tasks[offset : offset + limit]
    return ApiResponse(
        data=QuickInteractionListData(
            tasks=page,
            total=len(tasks),
            has_more=offset + len(page) < len(tasks),
        )
    )


@api_router.patch(
    "/sessions/{session_id}/quick-interactions/{task_id}/pin",
    response_model=ApiResponse[QuickInteractionData],
)
def set_quick_interaction_pinned(
    session_id: str,
    task_id: str,
    payload: QuickInteractionPinRequest,
    request: Request,
) -> ApiResponse[QuickInteractionData]:
    task = request.app.state.quick_interactions.set_pinned(
        session_id,
        task_id,
        payload.pinned,
    )
    return ApiResponse(data=QuickInteractionData(task=task))


@api_router.patch(
    "/sessions/{session_id}/permission",
    response_model=ApiResponse[SessionPermissionData],
)
async def update_session_permission(
    session_id: str,
    payload: SessionPermissionRequest,
    request: Request,
) -> ApiResponse[SessionPermissionData]:
    try:
        def update_with_guard() -> tuple[SessionInfo, bool]:
            with request.app.state.quick_interactions.session_operation_guard(session_id):
                return request.app.state.codex_pty_manager.update_permission_and_stop(
                    session_id,
                    payload.permission_mode,
                )

        session, auto_stopped = await asyncio.to_thread(update_with_guard)
    except Exception:
        log_operation(
            request,
            action="update_codex_session_permission",
            status="failed",
            target=session_id,
        )
        raise
    log_operation(
        request,
        action="update_codex_session_permission",
        status="succeeded",
        target=session_id,
    )
    if auto_stopped:
        request.app.state.terminal_tickets.revoke_session(session_id)
        request.app.state.terminal_connections.close_session(session_id)
        log_operation(
            request,
            action="stop_codex_session_for_permission",
            status="succeeded",
            target=session_id,
        )
    application = (
        "stopped"
        if auto_stopped
        else "pending"
        if session.permission_pending
        else "saved"
    )
    return ApiResponse(
        data=SessionPermissionData(
            session=session,
            application=application,
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
    log_operation(
        request,
        action="delete_codex_session",
        status="succeeded",
        target=session_id,
    )
    return ApiResponse(data=None)


def _terminal_authorized(connection: Request | WebSocket, session_id: str) -> bool:
    return connection.app.state.terminal_tickets.valid(
        connection.cookies.get(COOKIE_NAME),
        session_id,
    )


@web_router.get(
    "/codex/{session_id}/quick-interactions",
    response_class=HTMLResponse,
    include_in_schema=False,
)
async def quick_interaction_page(request: Request, session_id: str) -> HTMLResponse:
    return templates.TemplateResponse(
        request=request,
        name="quick_interactions.html",
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
    session = request.app.state.codex_pty_manager.get_session(session_id)
    page = request.app.state.terminal_connections.open_page(
        session_id,
        request.cookies[COOKIE_NAME],
    )
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
    await websocket.accept(subprotocol="tty")
    LOGGER.info("terminal_websocket_accepted session_id=%s page_id=%s", session_id, page_id)
    manager = websocket.app.state.codex_pty_manager
    ticket = websocket.cookies[COOKIE_NAME]
    try:
        connection, released = await websocket.app.state.terminal_connections.claim(
            session_id,
            ticket,
            page_id,
        )
    except ValueError:
        await websocket.close(code=4401, reason="Terminal page access expired")
        return
    try:
        if not released:
            LOGGER.warning(
                "session_id=%s old terminal connection did not release; recycling ttyd",
                session_id,
            )
            await asyncio.to_thread(manager.restart_terminal_backend, session_id)
        backend_url = manager.backend_ws_url(session_id)
        session = manager.get_session(session_id)
        async with websockets.connect(
            backend_url,
            origin=f"http://127.0.0.1:{session.ttyd_port}",
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
                    with websocket.app.state.quick_interactions.terminal_input_guard(
                        session_id
                    ) as allowed:
                        if not allowed:
                            continue
                        if message.get("bytes") is not None:
                            await backend.send(message["bytes"])
                        elif message.get("text") is not None:
                            await backend.send(message["text"])

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
        websocket.app.state.terminal_connections.release(connection)
        if connection.takeover.is_set():
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
