from unittest.mock import ANY, MagicMock, call

import httpx
import pytest

from app.application import create_app
from app.core.config import Settings
from app.services.openclaw import OpenClawManager


def authorization(settings: Settings) -> dict[str, str]:
    token = settings.security.token
    assert token is not None
    return {"Authorization": f"Bearer {token.get_secret_value()}"}


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
        unauthorized = await client.get("/api/openclaw/status")
        response = await client.get(
            "/api/openclaw/status",
            headers=authorization(settings),
        )

    assert unauthorized.status_code == 401
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
