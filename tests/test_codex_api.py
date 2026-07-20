from pathlib import Path
from unittest.mock import MagicMock

import httpx
import pytest

from app.application import create_app
from app.codex.models import CodexSession, SessionInfo, SessionListData, WorkspaceInfo
from app.core.config import Settings


def authorization(settings: Settings) -> dict[str, str]:
    token = settings.security.token
    assert token is not None
    return {"Authorization": f"Bearer {token.get_secret_value()}"}


@pytest.mark.anyio
async def test_codex_sessions_require_authentication(settings: Settings) -> None:
    transport = httpx.ASGITransport(app=create_app(settings))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/api/codex/sessions")

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "authentication_required"


@pytest.mark.anyio
async def test_codex_session_list_reports_workspaces(settings: Settings) -> None:
    app = create_app(settings)
    manager = MagicMock()
    manager.available.return_value = False
    manager.unavailable_reason.return_value = "Codex PTY requires Tailscale"
    manager.dependencies.return_value = {"codex": True, "ttyd": True, "tmux": False}
    manager.workspaces.return_value = [
        WorkspaceInfo(id="home", name="用户目录", path="/home/test", available=True)
    ]
    manager.list_sessions.return_value = []
    app.state.codex_pty_manager = manager
    transport = httpx.ASGITransport(app=app)

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(
            "/api/codex/sessions",
            headers=authorization(settings),
        )

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["available"] is False
    assert data["workspaces"][0]["id"] == "home"
    assert data["dependencies"]["tmux"] is False


@pytest.mark.anyio
async def test_access_issues_scoped_http_only_cookie(settings: Settings) -> None:
    app = create_app(settings)
    manager = MagicMock()
    manager.ensure_terminal.return_value = CodexSession(
        id="session-1",
        workspace_id="chub",
        workspace_name="Chub",
        cwd=Path("/workspace/chub"),
    )
    app.state.codex_pty_manager = manager
    transport = httpx.ASGITransport(app=app)

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/api/codex/sessions/session-1/access",
            headers=authorization(settings),
        )

    assert response.status_code == 200
    assert response.json()["data"]["terminal_url"] == "/codex/session-1"
    cookie = response.headers["set-cookie"]
    assert "HttpOnly" in cookie
    assert "SameSite=strict" in cookie
    assert "Path=/codex/session-1" in cookie


@pytest.mark.anyio
async def test_archive_session_revokes_access_and_calls_manager(
    settings: Settings,
) -> None:
    app = create_app(settings)
    manager = MagicMock()
    tickets = MagicMock()
    app.state.codex_pty_manager = manager
    app.state.terminal_tickets = tickets
    transport = httpx.ASGITransport(app=app)

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/api/codex/sessions/session-1/archive",
            headers=authorization(settings),
        )

    assert response.status_code == 200
    tickets.revoke_session.assert_called_once_with("session-1")
    manager.archive_session.assert_called_once_with("session-1")
