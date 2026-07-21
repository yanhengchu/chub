from __future__ import annotations

import asyncio
import logging
from urllib.parse import urlsplit

import httpx
import websockets
from fastapi import APIRouter, Depends, Request, Response, WebSocket
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from starlette.websockets import WebSocketDisconnect

from app.codex.models import (
    SessionAccessData,
    SessionCreateRequest,
    SessionInfo,
    SessionListData,
)
from app.core.response import ApiError, ApiResponse, error_response
from app.core.security import require_token
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
    return ApiResponse(
        data=SessionListData(
            available=manager.available(),
            unavailable_reason=manager.unavailable_reason(),
            dependencies=manager.dependencies(),
            workspaces=manager.workspaces(),
            sessions=manager.list_sessions(),
        )
    )


@api_router.post("/sessions", response_model=ApiResponse[SessionInfo])
def create_session(
    payload: SessionCreateRequest,
    request: Request,
) -> ApiResponse[SessionInfo]:
    return ApiResponse(
        data=request.app.state.codex_pty_manager.create_session(payload.workspace_id)
    )


@api_router.post(
    "/sessions/{session_id}/access",
    response_model=ApiResponse[SessionAccessData],
)
def access_session(
    session_id: str,
    request: Request,
    response: Response,
) -> ApiResponse[SessionAccessData]:
    request.app.state.codex_pty_manager.ensure_terminal(session_id)
    request.app.state.terminal_tickets.revoke_session(session_id)
    ticket = request.app.state.terminal_tickets.issue(session_id)
    response.set_cookie(
        COOKIE_NAME,
        ticket,
        max_age=request.app.state.terminal_tickets.ttl_seconds,
        httponly=True,
        samesite="strict",
        secure=False,
        path=f"/codex/{session_id}",
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
    request.app.state.terminal_tickets.revoke_session(session_id)
    request.app.state.terminal_connections.close_session(session_id)
    data = await asyncio.to_thread(
        request.app.state.codex_pty_manager.stop_session,
        session_id,
    )
    return ApiResponse(data=data)


@api_router.post("/sessions/{session_id}/archive", response_model=ApiResponse[None])
async def archive_session(session_id: str, request: Request) -> ApiResponse[None]:
    request.app.state.terminal_tickets.revoke_session(session_id)
    request.app.state.terminal_connections.close_session(session_id)
    await asyncio.to_thread(
        request.app.state.codex_pty_manager.archive_session,
        session_id,
    )
    return ApiResponse(data=None)


@api_router.delete("/sessions/{session_id}", response_model=ApiResponse[None])
async def delete_session(session_id: str, request: Request) -> ApiResponse[None]:
    request.app.state.terminal_tickets.revoke_session(session_id)
    request.app.state.terminal_connections.close_session(session_id)
    await asyncio.to_thread(
        request.app.state.codex_pty_manager.delete_session,
        session_id,
    )
    return ApiResponse(data=None)


def _terminal_authorized(connection: Request | WebSocket, session_id: str) -> bool:
    return connection.app.state.terminal_tickets.valid(
        connection.cookies.get(COOKIE_NAME),
        session_id,
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
    await websocket.accept(subprotocol="tty")
    manager = websocket.app.state.codex_pty_manager
    ticket = websocket.cookies[COOKIE_NAME]
    connection, released = await websocket.app.state.terminal_connections.claim(
        session_id,
        ticket,
    )
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
    except (
        OSError,
        RuntimeError,
        WebSocketDisconnect,
        websockets.WebSocketException,
    ):
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
    except (httpx.HTTPError, ApiError):
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


@api_router.post("/restart", response_model=ApiResponse[dict[str, str]])
def restart_hub() -> ApiResponse[dict[str, str]]:
    import shutil
    import subprocess

    command = shutil.which("chub")
    if command is None:
        return error_response(503, "command_not_found", "找不到 chub 命令，无法重启")

    try:
        subprocess.Popen(
            [command, "restart"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except OSError:
        return error_response(500, "restart_failed", "启动重启命令失败")

    return ApiResponse(data={"status": "restarting"})
