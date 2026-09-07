from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import httpx
import pytest

from app.application import create_app
from app.ai_usage.models import AiUsageData
from app.automations.models import (
    AutomationListData,
    AutomationRunAccepted,
    AccountLoginPageResult,
    BrowserControlResult,
    BrowserInitializationAccepted,
    FeishuEnvironmentState,
    RuntimeAccountEnvironmentState,
)
from app.core.config import Settings


AUTH = {"Authorization": "Bearer test-token-that-is-long-enough-for-tests"}


@pytest.mark.anyio
async def test_automations_require_trusted_network(settings: Settings) -> None:
    transport = httpx.ASGITransport(
        app=create_app(settings),
        client=("192.0.2.1", 12345),
    )
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        listing = await client.get("/api/automations")
        run = await client.post("/api/automations/task/run")
        check_feishu = await client.post("/api/automations/environment/feishu/check")
        check_codex = await client.post("/api/automations/environment/codex/check")
        open_feishu_login = await client.post(
            "/api/automations/environment/feishu/login-page"
        )
        open_codex_login = await client.post(
            "/api/automations/environment/codex/login-page"
        )
        retired_qr = await client.get("/api/automations/environment/feishu/qr")
        initialize_browser = await client.post(
            "/api/automations/browser/initialize",
            json={"profile_id": "Profile 2", "mode": "headed"},
        )

    assert listing.status_code == 403
    assert run.status_code == 403
    assert check_feishu.status_code == 403
    assert check_codex.status_code == 403
    assert open_feishu_login.status_code == 403
    assert open_codex_login.status_code == 403
    assert retired_qr.status_code == 404
    assert initialize_browser.status_code == 403


@pytest.mark.anyio
async def test_automation_list_and_background_acceptance(
    settings: Settings,
    tmp_path: Path,
) -> None:
    settings.automations.state_dir = tmp_path / "automation-state"
    settings.automations.runtime_dir = tmp_path / "automation-runtime"
    settings.automations.artifacts_dir = tmp_path / "automation-artifacts"
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
        mode="有界面",
        message="Debug Chrome 已启动",
    )
    manager.initialize_browser.return_value = BrowserInitializationAccepted(
        profile_id="Profile 2",
    )
    manager.check_feishu_environment.return_value = FeishuEnvironmentState(
        state="available",
        message="登录有效",
    )
    manager.check_codex_runtime_account.return_value = RuntimeAccountEnvironmentState(
        state="available",
        message="ChatGPT 登录有效",
    )
    manager.open_feishu_login_page.return_value = AccountLoginPageResult(
        message="飞书登录页面已打开"
    )
    manager.open_codex_runtime_login_page.return_value = AccountLoginPageResult(
        message="Codex Runtime 登录页面已打开"
    )
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
        restart_browser = await client.post("/api/automations/browser/restart")
        check_feishu = await client.post("/api/automations/environment/feishu/check")
        check_codex = await client.post("/api/automations/environment/codex/check")
        open_feishu_login = await client.post(
            "/api/automations/environment/feishu/login-page"
        )
        open_codex_login = await client.post(
            "/api/automations/environment/codex/login-page"
        )
        initialize_browser = await client.post(
            "/api/automations/browser/initialize",
            json={"profile_id": "Profile 2", "mode": "headed"},
        )

    assert listing.status_code == 200
    assert listing.json()["data"]["browser_state"] == "running"
    assert run.status_code == 202
    assert run.json()["data"]["status"] == "queued"
    assert start_browser.status_code == 200
    assert start_browser.json()["data"]["state"] == "running"
    assert restart_browser.status_code == 200
    assert restart_browser.json()["data"]["state"] == "running"
    assert check_feishu.status_code == 200
    assert check_feishu.json()["data"]["state"] == "available"
    assert check_codex.status_code == 200
    assert check_codex.json()["data"]["state"] == "available"
    assert open_feishu_login.status_code == 200
    assert open_feishu_login.json()["data"]["state"] == "opened"
    assert open_codex_login.status_code == 200
    assert open_codex_login.json()["data"]["state"] == "opened"
    assert initialize_browser.status_code == 202
    manager.list.assert_called_once_with(home_only=False)
    manager.start.assert_called_once()
    assert manager.start.call_args.args == ("monthly-report",)
    assert len(manager.start.call_args.kwargs["operation_id"]) == 32
    assert manager.start.call_args.kwargs["source_ip"] == "127.0.0.1"
    assert [call.args for call in manager.control_browser.call_args_list] == [
        ("start", "headless"),
        ("restart", "headless"),
    ]
    manager.check_feishu_environment.assert_called_once_with()
    manager.check_codex_runtime_account.assert_called_once_with()
    manager.open_feishu_login_page.assert_called_once_with()
    manager.open_codex_runtime_login_page.assert_called_once_with()
    manager.initialize_browser.assert_called_once()


@pytest.mark.anyio
async def test_codex_runtime_account_check_requires_available_ai_usage(
    settings: Settings,
) -> None:
    app = create_app(settings)
    app.state.ai_usage.read = MagicMock(
        return_value=AiUsageData(
            status="unavailable",
            provider="OpenAI",
            source="sub2api",
            timezone="Asia/Shanghai",
            message="AI API 额度账户未登录。",
        )
    )
    app.state.ai_usage.login_page_available = MagicMock(return_value=True)
    transport = httpx.ASGITransport(app=app)

    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://test",
        headers=AUTH,
    ) as client:
        response = await client.post("/api/automations/environment/codex/check")

    assert response.status_code == 200
    assert response.json()["data"]["state"] == "failed"
    assert response.json()["data"]["message"] == "API Key 已配置，但 AI 额度账户未登录"
    assert response.json()["data"]["checked_at"]
    assert response.json()["data"]["login_page_available"] is True
    app.state.ai_usage.read.assert_called_once_with(force=True)
    app.state.ai_usage.login_page_available.assert_called_once_with()


@pytest.mark.anyio
async def test_codex_runtime_account_check_logs_account_state(
    settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = create_app(settings)
    app.state.automation_manager = MagicMock()
    app.state.automation_manager.check_codex_runtime_account.return_value = (
        RuntimeAccountEnvironmentState(
            state="failed",
            message="Codex Runtime 未安装",
        )
    )
    operations: list[dict[str, object]] = []

    def record_operation(_request, **payload):
        operations.append(payload)
        return payload.get("operation_id") or "operation-id"

    monkeypatch.setattr("app.api.automations.log_operation", record_operation)
    transport = httpx.ASGITransport(app=app)

    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://test",
        headers=AUTH,
    ) as client:
        response = await client.post("/api/automations/environment/codex/check")

    assert response.status_code == 200
    assert [operation["status"] for operation in operations] == [
        "requested",
        "started",
        "succeeded",
    ]
    assert operations[-1]["reason"] == "account_state=failed"


@pytest.mark.anyio
async def test_browser_profile_rejects_client_paths(settings: Settings) -> None:
    transport = httpx.ASGITransport(app=create_app(settings))
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://test",
        headers=AUTH,
    ) as client:
        response = await client.post(
            "/api/automations/browser/initialize",
            json={"profile_id": "../Default", "mode": "headed"},
        )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "invalid_request"
