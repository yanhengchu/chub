from unittest.mock import ANY, MagicMock, call

import httpx
import pytest

from app.application import create_app
from app.core.config import Settings
from app.services.openclaw import OpenClawManager
from app.services.openclaw_weixin import WeixinLoginStatus
from app.codex.models import utc_now


def authorization(settings: Settings) -> dict[str, str]:
    return {}


def running_status():
    return OpenClawManager._parse_status(
        {
            "cli": {"version": "2026.7.1-2"},
            "service": {
                "label": "LaunchAgent",
                "loaded": True,
                "command": {"sourcePath": "/tmp/ai.openclaw.gateway.plist"},
                "runtime": {"status": "running"},
            },
            "config": {"cli": {"exists": True, "valid": True}},
            "gateway": {"bindMode": "loopback", "port": 18789},
            "port": {"status": "listening"},
            "rpc": {"ok": True},
        }
    )


@pytest.mark.anyio
async def test_openclaw_status_is_protected(settings: Settings) -> None:
    app = create_app(settings)
    manager = MagicMock()
    manager.status.return_value = running_status()
    app.state.openclaw_manager = manager
    transport = httpx.ASGITransport(app=app)

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(
            "/api/openclaw/status",
            headers=authorization(settings),
        )

    assert response.status_code == 200
    assert response.json()["data"]["state"] == "running"
    assert "sourcePath" not in response.text
    manager.status.assert_called_once_with()


@pytest.mark.anyio
async def test_openclaw_control_logs_full_lifecycle(
    settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    log_operation = MagicMock(return_value="operation-id")
    monkeypatch.setattr("app.api.openclaw.log_operation", log_operation)
    app = create_app(settings)
    manager = MagicMock()
    manager.control.return_value = running_status()
    app.state.openclaw_manager = manager
    transport = httpx.ASGITransport(app=app)

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/api/openclaw/start",
            headers=authorization(settings),
        )

    assert response.status_code == 200
    manager.control.assert_called_once_with("start")
    assert log_operation.call_args_list == [
        call(
            ANY,
            action="start_openclaw_gateway",
            status="requested",
            target="openclaw-gateway",
        ),
        call(
            ANY,
            action="start_openclaw_gateway",
            status="started",
            target="openclaw-gateway",
            operation_id="operation-id",
        ),
        call(
            ANY,
            action="start_openclaw_gateway",
            status="succeeded",
            target="openclaw-gateway",
            operation_id="operation-id",
        ),
    ]


@pytest.mark.anyio
async def test_openclaw_control_logs_failed_lifecycle(
    settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    log_operation = MagicMock(return_value="operation-id")
    monkeypatch.setattr("app.api.openclaw.log_operation", log_operation)
    app = create_app(settings)
    manager = MagicMock()
    manager.control.side_effect = RuntimeError("test failure")
    app.state.openclaw_manager = manager
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/api/openclaw/restart",
            headers=authorization(settings),
        )

    assert response.status_code == 500
    assert [item.kwargs["status"] for item in log_operation.call_args_list] == [
        "requested",
        "started",
        "failed",
    ]
    operation_ids = [
        item.kwargs.get("operation_id") for item in log_operation.call_args_list
    ]
    assert operation_ids == [None, "operation-id", "operation-id"]


@pytest.mark.anyio
async def test_weixin_login_endpoints_are_protected_and_bounded(
    settings: Settings,
) -> None:
    app = create_app(settings)
    manager = MagicMock()
    login_status = WeixinLoginStatus(
        state="waiting_scan",
        message="请扫码。",
        qr_available=True,
        created_at=utc_now(),
        updated_at=utc_now(),
    )
    manager.start_weixin_login.return_value = login_status
    manager.weixin_login.status.return_value = login_status
    manager.weixin_login.qr_content.return_value = b"\x89PNG\r\n\x1a\n"
    app.state.openclaw_manager = manager
    transport = httpx.ASGITransport(app=app)

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        started = await client.post(
            "/api/openclaw/weixin/login",
            headers=authorization(settings),
        )
        current = await client.get(
            "/api/openclaw/weixin/login",
            headers=authorization(settings),
        )
        qr = await client.get(
            "/api/openclaw/weixin/login/qr",
            headers=authorization(settings),
        )

    assert started.status_code == 202
    assert current.json()["data"]["state"] == "waiting_scan"
    assert qr.headers["content-type"] == "image/png"
    assert qr.headers["cache-control"].startswith("no-store")
    assert "secret" not in started.text
