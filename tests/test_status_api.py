from __future__ import annotations

import httpx
import pytest

from app.application import create_app
from app.core.config import Settings


@pytest.mark.anyio
async def test_status_accepts_loopback_source(settings: Settings) -> None:
    app = create_app(settings)
    transport = httpx.ASGITransport(app=app, client=("127.0.0.1", 12345))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/api/status")
    assert response.status_code == 200
    assert response.json()["data"]["authentication_method"] == "loopback"


@pytest.mark.anyio
async def test_status_accepts_tailnet_source(settings: Settings) -> None:
    settings.server.tailnet_host = "100.64.0.20"
    app = create_app(settings)
    transport = httpx.ASGITransport(app=app, client=("100.64.0.21", 12345))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/api/status")
    assert response.status_code == 200
    assert response.json()["data"]["authentication_method"] == "tailscale"


@pytest.mark.anyio
async def test_status_rejects_other_network_even_with_forwarded_header(settings: Settings) -> None:
    settings.server.tailnet_host = "100.64.0.20"
    app = create_app(settings)
    transport = httpx.ASGITransport(app=app, client=("192.168.1.20", 12345))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/api/status", headers={"X-Forwarded-For": "100.64.0.21"})
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "trusted_network_required"
