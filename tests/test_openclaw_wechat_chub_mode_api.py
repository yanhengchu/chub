from __future__ import annotations

from unittest.mock import MagicMock

import httpx
import pytest

from app.application import create_app
from app.core.config import Settings


def tailscale_client(app):
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, client=("100.64.0.21", 12345)),
        base_url="http://test",
    )


@pytest.mark.anyio
async def test_wechat_chub_mode_status_reports_disabled_from_tailscale(
    settings: Settings,
) -> None:
    settings.server.host = "100.64.0.20"
    app = create_app(settings)

    async with tailscale_client(app) as client:
        response = await client.get("/api/openclaw/wechat-chub-mode/status")

    assert response.status_code == 200
    assert response.json() == {
        "success": True,
        "data": {"enabled": False, "ready": False, "code": "disabled"},
    }


@pytest.mark.anyio
async def test_wechat_chub_mode_status_reports_ready(
    settings: Settings,
) -> None:
    settings.server.host = "100.64.0.20"
    settings.openclaw.weixin_chub_mode.enabled = True
    app = create_app(settings)
    app.state.codex_pty_manager.available = MagicMock(return_value=True)

    async with tailscale_client(app) as client:
        response = await client.get("/api/openclaw/wechat-chub-mode/status")

    assert response.status_code == 200
    assert response.json() == {
        "success": True,
        "data": {"enabled": True, "ready": True, "code": "ready"},
    }


@pytest.mark.anyio
async def test_wechat_chub_mode_status_reports_invalid_workspace(
    settings: Settings,
) -> None:
    settings.server.host = "100.64.0.20"
    settings.openclaw.weixin_chub_mode.enabled = True
    settings.openclaw.weixin_chub_mode.workspace_id = "workspace"
    app = create_app(settings)

    async with tailscale_client(app) as client:
        response = await client.get("/api/openclaw/wechat-chub-mode/status")

    assert response.status_code == 200
    assert response.json()["data"] == {
        "enabled": True,
        "ready": False,
        "code": "configuration_invalid",
    }


@pytest.mark.anyio
async def test_wechat_chub_mode_status_reports_unavailable_codex(
    settings: Settings,
) -> None:
    settings.server.host = "100.64.0.20"
    settings.openclaw.weixin_chub_mode.enabled = True
    app = create_app(settings)
    app.state.codex_pty_manager.available = MagicMock(return_value=False)

    async with tailscale_client(app) as client:
        response = await client.get("/api/openclaw/wechat-chub-mode/status")

    assert response.status_code == 200
    assert response.json()["data"] == {
        "enabled": True,
        "ready": False,
        "code": "codex_unavailable",
    }


@pytest.mark.anyio
async def test_wechat_chub_mode_status_rejects_token_and_forwarded_source(
    settings: Settings,
) -> None:
    settings.server.host = "100.64.0.20"
    app = create_app(settings)
    token = settings.security.token
    assert token is not None
    transport = httpx.ASGITransport(
        app=app,
        client=("127.0.0.1", 12345),
    )

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(
            "/api/openclaw/wechat-chub-mode/status",
            headers={
                "Authorization": f"Bearer {token.get_secret_value()}",
                "X-Forwarded-For": "100.64.0.21",
            },
        )

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "tailscale_required"
