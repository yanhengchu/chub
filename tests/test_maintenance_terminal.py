from unittest.mock import MagicMock

import httpx
import pytest

from app.application import create_app
from app.core.config import PROJECT_ROOT, Settings
from app.services.maintenance_terminal import MaintenanceTerminalManager


def test_maintenance_terminal_starts_zsh_in_project_root(
    settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = MaintenanceTerminalManager(settings)
    process = MagicMock()
    process.poll.return_value = None
    popen = MagicMock(return_value=process)
    monkeypatch.setattr(
        "app.services.maintenance_terminal.shutil.which",
        lambda name: f"/{name}",
    )
    monkeypatch.setattr("app.services.maintenance_terminal.subprocess.Popen", popen)
    monkeypatch.setattr(manager, "_available_port", lambda: 45678)
    monkeypatch.setattr(manager, "_wait_for_port", lambda _process, _port: None)

    ticket = manager.open()

    assert manager.tickets.valid(ticket, manager.terminal_id)
    assert popen.call_args.args[0] == [
        "/ttyd",
        "-W",
        "-O",
        "-m",
        "1",
        "-i",
        "127.0.0.1",
        "-p",
        "45678",
        "-b",
        "/maintenance-terminal/terminal",
        "/zsh",
    ]
    assert popen.call_args.kwargs["cwd"] == PROJECT_ROOT
    assert (
        manager.backend_ws_url()
        == "ws://127.0.0.1:45678/maintenance-terminal/terminal/ws"
    )


@pytest.mark.anyio
async def test_maintenance_terminal_access_issues_page_ticket(settings: Settings) -> None:
    app = create_app(settings)
    manager = app.state.maintenance_terminal
    manager.open = MagicMock(return_value=manager.tickets.issue(manager.terminal_id))
    transport = httpx.ASGITransport(app=app, client=("127.0.0.1", 50000))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        access = await client.post("/api/maintenance-terminal/access")
        page = await client.get("/maintenance-terminal")

    assert access.status_code == 200
    assert access.json()["data"]["terminal_url"] == "/maintenance-terminal"
    assert "chub_maintenance_terminal" in access.headers["set-cookie"]
    assert page.status_code == 200
    assert 'title="Chub 维护终端"' in page.text
    assert 'src="/maintenance-terminal/terminal/?page_id=' in page.text


@pytest.mark.anyio
async def test_maintenance_terminal_page_rejects_missing_ticket(settings: Settings) -> None:
    app = create_app(settings)
    transport = httpx.ASGITransport(app=app, client=("127.0.0.1", 50000))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/maintenance-terminal")

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "maintenance_terminal_access_required"
