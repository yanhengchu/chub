from __future__ import annotations

import asyncio

import uvicorn

from app.application import create_app
from app.core.config import get_settings
from app.server import listen_sockets


settings = get_settings()
app = create_app(settings)


def _set_tailnet_listener_available(available: bool | None) -> None:
    app.state.tailnet_listener_available = available


def _set_tailnet_listener_hosts(hosts: tuple[str, ...]) -> None:
    app.state.tailnet_listener_hosts = hosts


async def _serve() -> None:
    listeners = listen_sockets(
        settings,
        set_tailnet_listener_available=_set_tailnet_listener_available,
        set_tailnet_listener_hosts=_set_tailnet_listener_hosts,
    )
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
