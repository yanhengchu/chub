from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import httpx
import pytest
from pydantic import ValidationError

from app.api.openclaw_wechat_chub_mode import WeixinChubModeDispatchData
from app.application import create_app
from app.codex.models import QuickInteractionWeixinRoute
from app.core.config import Settings


def tailscale_client(app):
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, client=("100.64.0.21", 12345)),
        base_url="http://test",
    )


def local_openclaw_client(app):
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, client=("127.0.0.1", 12345)),
        base_url="http://test",
    )


def dispatch_result(
    *,
    disposition: str = "reply",
    message: str | None = "任务已提交。",
):
    return SimpleNamespace(
        model_dump=lambda: {
            "protocol_version": 3,
            "disposition": disposition,
            "message": message,
        }
    )


@pytest.mark.parametrize(
    ("disposition", "message"),
    [
        ("pass", "不应携带回复"),
        ("handled", "不应携带回复"),
        ("reply", None),
        ("reply", "   "),
    ],
)
def test_dispatch_response_rejects_invalid_message_combinations(
    disposition: str,
    message: str | None,
) -> None:
    with pytest.raises(ValidationError):
        WeixinChubModeDispatchData.model_validate(
            {
                "protocol_version": 3,
                "disposition": disposition,
                "message": message,
            }
        )


@pytest.mark.anyio
async def test_dispatch_accepts_only_bounded_fixed_fields(
    settings: Settings,
) -> None:
    settings.server.tailnet_host = "100.64.0.20"
    app = create_app(settings)
    app.state.weixin_chub_mode.dispatch = MagicMock(return_value=dispatch_result())

    async with local_openclaw_client(app) as client:
        response = await client.post(
            "/api/openclaw/wechat-chub-mode/dispatch",
            json={
                "protocol_version": 3,
                "message_id": " message-1 ",
                "content": " 检查设备状态 ",
                "message_type": "text",
                "correlation_id": " correlation-1 ",
                "reply_account_id": " weixin-account ",
                "reply_recipient": " owner@im.wechat ",
            },
        )

    assert response.status_code == 200
    assert response.json() == {
        "success": True,
        "data": {
            "protocol_version": 3,
            "disposition": "reply",
            "message": "任务已提交。",
        },
    }
    app.state.weixin_chub_mode.dispatch.assert_called_once_with(
        message_id="message-1",
        prompt=" 检查设备状态 ",
        message_type="text",
        correlation_id="correlation-1",
        source_ip="127.0.0.1",
        delivery_route=QuickInteractionWeixinRoute(
            account_id="weixin-account",
            recipient="owner@im.wechat",
        ),
    )
    assert "session_id" not in response.text
    assert "task_id" not in response.text


@pytest.mark.anyio
async def test_dispatch_returns_pass_without_exposing_internal_state(
    settings: Settings,
) -> None:
    settings.server.tailnet_host = "100.64.0.20"
    app = create_app(settings)
    app.state.weixin_chub_mode.dispatch = MagicMock(
        return_value=dispatch_result(
            disposition="pass",
            message=None,
        )
    )

    async with local_openclaw_client(app) as client:
        response = await client.post(
            "/api/openclaw/wechat-chub-mode/dispatch",
            json={
                "protocol_version": 3,
                "message_id": "message-1",
                "content": "检查设备状态",
                "message_type": "text",
                "reply_account_id": "weixin-account",
                "reply_recipient": "owner@im.wechat",
            },
        )

    assert response.status_code == 200
    assert response.json()["data"] == {
        "protocol_version": 3,
        "disposition": "pass",
        "message": None,
    }


@pytest.mark.anyio
async def test_dispatch_rejects_protocol_mismatch_before_routing(
    settings: Settings,
) -> None:
    settings.server.tailnet_host = "100.64.0.20"
    app = create_app(settings)
    app.state.weixin_chub_mode.dispatch = MagicMock()

    async with local_openclaw_client(app) as client:
        response = await client.post(
            "/api/openclaw/wechat-chub-mode/dispatch",
            json={
                "protocol_version": 1,
                "message_id": "message-1",
                "content": "检查设备状态",
                "message_type": "text",
                "reply_account_id": "weixin-account",
                "reply_recipient": "owner@im.wechat",
            },
        )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == (
        "weixin_chub_mode_protocol_mismatch"
    )
    app.state.weixin_chub_mode.dispatch.assert_not_called()


@pytest.mark.anyio
async def test_dispatch_rejects_injected_configuration(
    settings: Settings,
) -> None:
    settings.server.tailnet_host = "100.64.0.20"
    app = create_app(settings)
    app.state.weixin_chub_mode.dispatch = MagicMock()

    async with local_openclaw_client(app) as client:
        response = await client.post(
            "/api/openclaw/wechat-chub-mode/dispatch",
            json={
                "protocol_version": 3,
                "message_id": "message-1",
                "content": "检查设备状态",
                "message_type": "text",
                "reply_account_id": "weixin-account",
                "reply_recipient": "owner@im.wechat",
                "session_id": "arbitrary-session",
                "model": "arbitrary-model",
            },
        )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "invalid_request"
    app.state.weixin_chub_mode.dispatch.assert_not_called()


@pytest.mark.anyio
async def test_dispatch_requires_local_openclaw_source(
    settings: Settings,
) -> None:
    settings.server.tailnet_host = "100.64.0.20"
    app = create_app(settings)
    app.state.weixin_chub_mode.dispatch = MagicMock()

    async with tailscale_client(app) as client:
        response = await client.post(
            "/api/openclaw/wechat-chub-mode/dispatch",
            json={
                "protocol_version": 3,
                "message_id": "message-1",
                "content": "检查设备状态",
                "message_type": "text",
                "reply_account_id": "weixin-account",
                "reply_recipient": "owner@im.wechat",
            },
        )

    assert response.status_code == 403
    assert response.json()["error"]["code"] == (
        "weixin_chub_mode_source_required"
    )
    app.state.weixin_chub_mode.dispatch.assert_not_called()


@pytest.mark.anyio
async def test_old_status_and_submit_endpoints_are_removed(
    settings: Settings,
) -> None:
    settings.server.tailnet_host = "100.64.0.20"
    app = create_app(settings)

    async with local_openclaw_client(app) as client:
        status = await client.get("/api/openclaw/wechat-chub-mode/status")
        submit = await client.post("/api/openclaw/wechat-chub-mode/submit", json={})

    assert status.status_code == 404
    assert submit.status_code == 404
