from __future__ import annotations

import asyncio
import logging
from urllib.parse import urlsplit

import httpx
import websockets
from fastapi import APIRouter, Depends, Request, Response, WebSocket
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
from starlette.websockets import WebSocketDisconnect

from app.core.response import ApiError, ApiResponse, error_response
from app.core.security import require_trusted_network
from app.services.operation_log import log_operation
from app.web.routes import WEB_DIR


COOKIE_NAME = "chub_maintenance_terminal"
LOGGER = logging.getLogger("hub.maintenance_terminal")
api_router = APIRouter(
    prefix="/api/maintenance-terminal",
    tags=["maintenance-terminal"],
    dependencies=[Depends(require_trusted_network)],
)
web_router = APIRouter(tags=["maintenance-terminal-web"])
templates = Jinja2Templates(directory=WEB_DIR / "templates")


class MaintenanceTerminalAccessData(BaseModel):
    terminal_url: str
    expires_in: int


def _manager(connection: Request | WebSocket):
    return connection.app.state.maintenance_terminal


def _authorized(connection: Request | WebSocket) -> bool:
    manager = _manager(connection)
    return manager.tickets.valid(
        connection.cookies.get(COOKIE_NAME),
        manager.terminal_id,
    )


@api_router.post("/access", response_model=ApiResponse[MaintenanceTerminalAccessData])
def access_terminal(request: Request, response: Response) -> ApiResponse[MaintenanceTerminalAccessData]:
    manager = _manager(request)
    try:
        ticket = manager.open()
    except Exception:
        log_operation(
            request,
            action="access_maintenance_terminal",
            status="failed",
            target="project-shell",
        )
        raise
    response.set_cookie(
        COOKIE_NAME,
        ticket,
        max_age=manager.tickets.ttl_seconds,
        httponly=True,
        samesite="strict",
        secure=False,
        path="/maintenance-terminal",
    )
    log_operation(
        request,
        action="access_maintenance_terminal",
        status="succeeded",
        target="project-shell",
    )
    return ApiResponse(
        data=MaintenanceTerminalAccessData(
            terminal_url="/maintenance-terminal",
            expires_in=manager.tickets.ttl_seconds,
        )
    )


@web_router.get("/maintenance-terminal", response_class=HTMLResponse, include_in_schema=False)
async def terminal_page(request: Request) -> HTMLResponse:
    if not _authorized(request):
        raise ApiError(401, "maintenance_terminal_access_required", "维护终端访问已过期。")
    manager = _manager(request)
    page = manager.connections.open_page(
        manager.terminal_id,
        request.cookies[COOKIE_NAME],
    )
    return templates.TemplateResponse(
        request=request,
        name="maintenance_terminal.html",
        context={"page": page},
    )


@web_router.get("/maintenance-terminal/connection/{page_id}", include_in_schema=False)
async def terminal_connection_status(request: Request, page_id: str) -> JSONResponse:
    manager = _manager(request)
    state = manager.connections.page_state(manager.terminal_id, page_id)
    if state is None:
        return JSONResponse({"state": "unknown"}, status_code=404)
    return JSONResponse({"state": state})


@web_router.websocket("/maintenance-terminal/terminal/ws")
async def terminal_websocket(websocket: WebSocket) -> None:
    manager = _manager(websocket)
    if not _authorized(websocket) or not _valid_origin(websocket):
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
    connection = None
    try:
        await websocket.accept(subprotocol="tty")
        connection, released = await manager.connections.claim(
            manager.terminal_id,
            websocket.cookies[COOKIE_NAME],
            page_id,
        )
        if not released:
            await websocket.close(code=4410, reason="Terminal opened elsewhere")
            return
        backend_url = manager.backend_ws_url()
    except (ApiError, ValueError):
        if connection is not None:
            manager.connections.release(connection)
        await websocket.close(code=1011, reason="Terminal backend unavailable")
        return
    try:
        async with websockets.connect(
            backend_url,
            origin=manager.backend_origin(),
            subprotocols=["tty"],
        ) as backend:
            if not manager.connections.activate(connection):
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
            done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            for task in pending:
                task.cancel()
            await asyncio.gather(*pending, return_exceptions=True)
            for task in done:
                if not task.cancelled():
                    task.result()
    except WebSocketDisconnect:
        return
    except (OSError, RuntimeError, websockets.WebSocketException):
        LOGGER.warning("maintenance_terminal_websocket_failed", exc_info=True)
    finally:
        if connection is not None:
            manager.connections.release(connection)
        if connection is not None and connection.takeover.is_set():
            try:
                await websocket.close(code=4409, reason="Terminal opened elsewhere")
            except RuntimeError:
                pass


@web_router.api_route("/maintenance-terminal/terminal", methods=["GET", "HEAD"], include_in_schema=False)
@web_router.api_route("/maintenance-terminal/terminal/{path:path}", methods=["GET", "POST", "HEAD"], include_in_schema=False)
async def terminal_http(request: Request, path: str = "") -> Response:
    if not _authorized(request):
        return error_response(401, "maintenance_terminal_access_required", "维护终端访问已过期。")
    if not path and request.url.path.endswith("/terminal"):
        return RedirectResponse(url="/maintenance-terminal/terminal/", status_code=307)
    manager = _manager(request)
    try:
        backend_url = manager.backend_url(path, request.url.query)
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
    except (ApiError, httpx.HTTPError):
        return error_response(502, "maintenance_terminal_proxy_failed", "维护终端连接失败。")
    response_headers = {
        key: value
        for key, value in upstream.headers.items()
        if key.lower()
        not in {"connection", "content-encoding", "content-length", "content-security-policy", "location", "transfer-encoding", "x-frame-options"}
    }
    return Response(content=upstream.content, status_code=upstream.status_code, headers=response_headers)


def _valid_origin(websocket: WebSocket) -> bool:
    origin = websocket.headers.get("origin")
    if not origin:
        return False
    parsed = urlsplit(origin)
    return parsed.scheme in {"http", "https"} and parsed.netloc == websocket.headers.get("host")
