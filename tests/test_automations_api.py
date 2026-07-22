from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import httpx
import pytest

from app.application import create_app
from app.automations.models import (
    AutomationListData,
    AutomationRunAccepted,
    BrowserControlResult,
    FeishuEnvironmentState,
)
from app.core.config import Settings


AUTH = {"Authorization": "Bearer test-token-that-is-long-enough-for-tests"}


@pytest.mark.anyio
async def test_automations_require_authentication(settings: Settings) -> None:
    transport = httpx.ASGITransport(app=create_app(settings))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        listing = await client.get("/api/automations")
        run = await client.post("/api/automations/task/run")
        check_feishu = await client.post("/api/automations/environment/feishu/check")
        qr = await client.get("/api/automations/environment/feishu/qr")

    assert listing.status_code == 401
    assert run.status_code == 401
    assert check_feishu.status_code == 401
    assert qr.status_code == 401


@pytest.mark.anyio
async def test_automation_list_and_background_acceptance(
    settings: Settings,
    tmp_path: Path,
) -> None:
    settings.automations.data_dir = tmp_path / "automations"
    app = create_app(settings)
    manager = MagicMock()
    manager.list.return_value = AutomationListData(
        enabled=True,
        browser_state="running",
        browser_message="Debug Chrome 已连接",
        tasks=[],
    )
    manager.start.return_value = AutomationRunAccepted(
        task_id="monthly-report",
        run_id="run-1",
    )
    manager.control_browser.return_value = BrowserControlResult(
        state="running",
        mode="有界面模式",
        message="Debug Chrome 已启动",
    )
    manager.check_feishu_environment.return_value = FeishuEnvironmentState(
        state="available",
        message="登录有效",
    )
    qr_path = settings.automations.data_dir / "runtime" / "feishu-login-qr.png"
    qr_path.parent.mkdir(parents=True)
    qr_path.write_bytes(b"\x89PNG\r\n\x1a\ncontent")
    manager.feishu_qr_content.return_value = qr_path.read_bytes()
    app.state.automation_manager = manager
    transport = httpx.ASGITransport(app=app)

    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://test",
        headers=AUTH,
    ) as client:
        listing = await client.get("/api/automations?all_tasks=true")
        run = await client.post("/api/automations/monthly-report/run")
        start_browser = await client.post(
            "/api/automations/browser/start",
            json={"mode": "headless"},
        )
        check_feishu = await client.post("/api/automations/environment/feishu/check")
        qr = await client.get("/api/automations/environment/feishu/qr")

    assert listing.status_code == 200
    assert listing.json()["data"]["browser_state"] == "running"
    assert run.status_code == 202
    assert run.json()["data"]["status"] == "queued"
    assert start_browser.status_code == 200
    assert start_browser.json()["data"]["state"] == "running"
    assert check_feishu.status_code == 200
    assert check_feishu.json()["data"]["state"] == "available"
    assert qr.status_code == 200
    assert qr.headers["content-type"] == "image/png"
    assert "no-store" in qr.headers["cache-control"]
    manager.list.assert_called_once_with(home_only=False)
    manager.start.assert_called_once()
    assert manager.start.call_args.args == ("monthly-report",)
    assert len(manager.start.call_args.kwargs["operation_id"]) == 32
    assert manager.start.call_args.kwargs["source_ip"] == "127.0.0.1"
    manager.control_browser.assert_called_once_with("start", "headless")
    manager.check_feishu_environment.assert_called_once_with()
    manager.feishu_qr_content.assert_called_once_with()
