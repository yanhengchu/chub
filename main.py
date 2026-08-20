from __future__ import annotations

import asyncio
import socket

import uvicorn

from app.application import create_app
from app.core.config import get_settings


settings = get_settings()
app = create_app(settings)


def _listen_socket(host: str, port: int) -> socket.socket:
    family = socket.AF_INET6 if ":" in host else socket.AF_INET
    listener = socket.socket(family, socket.SOCK_STREAM)
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    if family == socket.AF_INET6:
        listener.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 1)
    listener.bind((host, port))
    listener.listen(socket.SOMAXCONN)
    listener.set_inheritable(True)
    return listener


async def _serve() -> None:
    hosts = tuple(
        host
        for host in ("127.0.0.1", settings.server.tailnet_host)
        if host is not None
    )
    listeners: list[socket.socket] = []
    try:
        for host in hosts:
            listeners.append(_listen_socket(host, settings.server.port))
    except BaseException:
        for listener in listeners:
            listener.close()
        raise
    server = uvicorn.Server(
        uvicorn.Config(app, log_level="info", proxy_headers=False)
    )
    try:
        await server.serve(sockets=listeners)
    finally:
        for listener in listeners:
            listener.close()


if __name__ == "__main__":
    asyncio.run(_serve())
