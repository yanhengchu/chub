from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import httpx
import pytest

from app.application import create_app
from app.codex.models import QuickInteractionWeixinRoute
from app.core.config import Settings


def tailscale_client(app):
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, client=("100.64.0.21", 12345)),
        base_url="http://test",
    )


def same_node_tailscale_client(app):
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, client=("100.64.0.20", 12345)),
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
    settings.openclaw.quick_interaction_completion.enabled = True
    settings.openclaw.quick_interaction_completion.weixin_recipient = "recipient"
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


@pytest.mark.anyio
async def test_wechat_chub_mode_submit_accepts_only_bounded_fixed_fields(
    settings: Settings,
) -> None:
    settings.server.host = "100.64.0.20"
    app = create_app(settings)
    app.state.weixin_chub_mode.submit = MagicMock(
        return_value=SimpleNamespace(
            model_dump=lambda: {
                "accepted": True,
                "duplicate": False,
                "new_session": True,
                "code": "submitted",
                "message": "任务已提交。",
            }
        )
    )

    async with same_node_tailscale_client(app) as client:
        response = await client.post(
            "/api/openclaw/wechat-chub-mode/submit",
            json={
                "message_id": " message-1 ",
                "prompt": " 检查设备状态 ",
                "correlation_id": " correlation-1 ",
                "reply_account_id": " weixin-account ",
                "reply_recipient": " owner@im.wechat ",
            },
        )

    assert response.status_code == 200
    assert response.json() == {
        "success": True,
        "data": {
            "accepted": True,
            "duplicate": False,
            "new_session": True,
            "code": "submitted",
            "message": "任务已提交。",
        },
    }
    app.state.weixin_chub_mode.submit.assert_called_once_with(
        message_id="message-1",
        prompt="检查设备状态",
        correlation_id="correlation-1",
        source_ip="100.64.0.20",
        delivery_route=QuickInteractionWeixinRoute(
            account_id="weixin-account",
            recipient="owner@im.wechat",
        ),
    )
    assert "session_id" not in response.text
    assert "task_id" not in response.text


@pytest.mark.anyio
async def test_wechat_chub_mode_submit_rejects_injected_configuration(
    settings: Settings,
) -> None:
    settings.server.host = "100.64.0.20"
    app = create_app(settings)
    app.state.weixin_chub_mode.submit = MagicMock()

    async with same_node_tailscale_client(app) as client:
        response = await client.post(
            "/api/openclaw/wechat-chub-mode/submit",
            json={
                "message_id": "message-1",
                "prompt": "检查设备状态",
                "session_id": "arbitrary-session",
                "model": "arbitrary-model",
            },
        )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "invalid_request"
    app.state.weixin_chub_mode.submit.assert_not_called()


@pytest.mark.anyio
async def test_wechat_chub_mode_submit_requires_direct_tailscale_source(
    settings: Settings,
) -> None:
    settings.server.host = "100.64.0.20"
    app = create_app(settings)
    token = settings.security.token
    assert token is not None
    transport = httpx.ASGITransport(app=app, client=("127.0.0.1", 12345))

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/api/openclaw/wechat-chub-mode/submit",
            headers={
                "Authorization": f"Bearer {token.get_secret_value()}",
                "X-Forwarded-For": "100.64.0.21",
            },
            json={"message_id": "message-1", "prompt": "检查设备状态"},
        )

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "tailscale_required"


@pytest.mark.anyio
async def test_wechat_chub_mode_submit_rejects_other_tailscale_node(
    settings: Settings,
) -> None:
    settings.server.host = "100.64.0.20"
    app = create_app(settings)
    app.state.weixin_chub_mode.submit = MagicMock()

    async with tailscale_client(app) as client:
        response = await client.post(
            "/api/openclaw/wechat-chub-mode/submit",
            json={"message_id": "message-1", "prompt": "检查设备状态"},
        )

    assert response.status_code == 403
    assert response.json()["error"]["code"] == (
        "weixin_chub_mode_source_required"
    )
    app.state.weixin_chub_mode.submit.assert_not_called()
