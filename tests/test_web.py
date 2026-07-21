from pathlib import Path
from unittest.mock import MagicMock

import httpx
import pytest

from app.application import create_app
from app.codex.models import CodexSession
from app.core.config import Settings


@pytest.mark.anyio
async def test_home_page_is_public_and_contains_no_token(settings: Settings) -> None:
    transport = httpx.ASGITransport(app=create_app(settings))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/")

    assert response.status_code == 200
    assert 'type="password"' in response.text
    assert 'src="/static/app.js"' in response.text
    assert 'href="/static/app.css"' in response.text
    assert 'id="connected-bar"' in response.text
    assert "更换凭证" not in response.text
    assert "清除凭证" not in response.text
    assert 'id="refresh-status"' in response.text
    assert "节点任务" not in response.text
    assert 'id="codex-card-host"' in response.text
    assert "节点维护" in response.text
    assert "维护检查" in response.text
    assert "服务操作" in response.text
    assert "重启 Chub" in response.text
    assert "退出" in response.text
    assert 'id="task-list"' in response.text
    assert "maintenance-logs" in response.text
    assert '<h3 id="logs-title">近期日志</h3>' in response.text
    assert 'id="status-details"' not in response.text
    assert "展开详情" not in response.text
    assert settings.security.token.get_secret_value() not in response.text


@pytest.mark.anyio
async def test_web_assets_are_available(settings: Settings) -> None:
    transport = httpx.ASGITransport(app=create_app(settings))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        script = await client.get("/static/app.js")
        stylesheet = await client.get("/static/app.css")
        terminal_stylesheet = await client.get("/static/terminal.css")
        terminal_script = await client.get("/static/terminal.js")

    assert script.status_code == 200
    assert stylesheet.status_code == 200
    assert terminal_stylesheet.status_code == 200
    assert terminal_script.status_code == 200
    assert "innerHTML" not in script.text
    assert "sessionStorage" in script.text
    assert "localStorage" in script.text
    assert "Authorization" in script.text
    assert "accessVersion" in script.text
    assert "connectWithToken" in script.text
    assert "确定退出当前节点吗" in script.text
    assert "createTaskCard" in script.text
    assert "createCodexCard" in script.text
    assert "createCodexSession" in script.text
    assert "enterCodexSession" in script.text
    assert "stopCodexSession" in script.text
    assert "archiveCodexSession" in script.text
    assert "removeCodexSession" in script.text
    assert "renderCodexWorkspaces" in script.text
    assert "renderCodexSessions" in script.text
    assert "formatSessionTime" in script.text
    assert "dependencyMessage" in script.text
    assert "task-card-result" in script.text
    assert "远程开发" in script.text
    assert "Codex PTY" in script.text
    assert "管理并接管本机 Codex CLI 会话。" in script.text
    assert "版本信息" in script.text
    assert "task-button-running" in script.text
    assert "task-button-paused" in script.text
    assert "showCodexPanel" in script.text
    assert "restartHub" in script.text
    assert "scrollCodexPanelIntoView" in script.text
    assert "/api/maintenance/restart" in script.text
    assert "/api/codex/restart" not in script.text
    assert "window.scrollTo" in script.text
    assert "scrollIntoView" not in script.text
    assert "/api/codex/sessions" in script.text
    assert "connection" in terminal_script.text


@pytest.mark.anyio
async def test_terminal_page_uses_session_title(settings: Settings) -> None:
    app = create_app(settings)
    manager = MagicMock()
    manager.get_session.return_value = CodexSession(
        id="session-1",
        workspace_id="codex",
        workspace_name="chub",
        cwd=Path("/workspace/chub"),
        title="真实会话标题",
        codex_session_id="11111111-1111-4111-8111-111111111111",
    )
    tickets = MagicMock()
    tickets.valid.return_value = True
    connections = MagicMock()
    connections.open_page.return_value.id = "page-1"
    app.state.codex_pty_manager = manager
    app.state.terminal_tickets = tickets
    app.state.terminal_connections = connections
    transport = httpx.ASGITransport(app=app)

    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://test",
        cookies={"chub_terminal": "ticket"},
    ) as client:
        response = await client.get("/codex/session-1")

    assert response.status_code == 200
    assert "真实会话标题 · Codex PTY" in response.text
    assert 'src="/static/terminal.js"' in response.text
    assert 'data-page-id="page-1"' in response.text


@pytest.mark.anyio
async def test_terminal_page_detects_when_another_device_takes_over(
    settings: Settings,
) -> None:
    app = create_app(settings)
    connections = MagicMock()
    connections.page_state.return_value = "displaced"
    app.state.terminal_connections = connections
    transport = httpx.ASGITransport(app=app)

    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://test",
        cookies={"chub_terminal": "old-ticket"},
    ) as client:
        response = await client.get("/codex/session-1/connection/page-1")

    assert response.status_code == 200
    assert response.json() == {"state": "displaced"}
    connections.page_state.assert_called_once_with("session-1", "page-1")


@pytest.mark.anyio
async def test_security_headers_apply_to_page_assets_and_api(
    settings: Settings,
) -> None:
    transport = httpx.ASGITransport(app=create_app(settings))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        page = await client.get("/")
        asset = await client.get("/static/app.js")
        api = await client.get("/api/health")

    for response in [page, asset, api]:
        assert response.headers["cache-control"] == "no-store"
        assert response.headers["x-content-type-options"] == "nosniff"
        assert response.headers["referrer-policy"] == "no-referrer"
    for response in [page, asset]:
        assert "default-src 'self'" in response.headers["content-security-policy"]
        assert "frame-ancestors 'none'" in response.headers["content-security-policy"]
    assert "content-security-policy" not in api.headers


@pytest.mark.anyio
async def test_security_headers_apply_to_unhandled_errors(
    settings: Settings,
) -> None:
    app = create_app(settings)

    @app.get("/test-error")
    def test_error() -> None:
        raise RuntimeError("test error")

    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/test-error")

    assert response.status_code == 500
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["referrer-policy"] == "no-referrer"
    assert response.json()["error"]["code"] == "internal_error"


@pytest.mark.anyio
async def test_page_uses_external_script_only(settings: Settings) -> None:
    transport = httpx.ASGITransport(app=create_app(settings))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/")

    assert response.text.count("<script") == 1
    assert "<script src=\"/static/app.js\" defer></script>" in response.text


@pytest.mark.anyio
async def test_api_documentation_is_not_public(settings: Settings) -> None:
    transport = httpx.ASGITransport(app=create_app(settings))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        responses = [
            await client.get("/docs"),
            await client.get("/redoc"),
            await client.get("/openapi.json"),
        ]

    for response in responses:
        assert response.status_code == 404
        assert response.json()["error"]["code"] == "not_found"
