import httpx
import pytest

from app.application import create_app
from app.core.config import Settings


@pytest.mark.anyio
async def test_health_is_public(settings: Settings) -> None:
    transport = httpx.ASGITransport(app=create_app(settings))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/api/health")

    assert response.status_code == 200
    assert response.json() == {
        "success": True,
        "data": {
            "service": "hub",
            "status": "ok",
            "version": "0.1.0",
        },
    }


def test_missing_token_logs_warning(
    settings: Settings, capsys: pytest.CaptureFixture[str]
) -> None:
    settings.security.token = None

    create_app(settings)

    assert "HUB_TOKEN is not set" in capsys.readouterr().err


def test_codex_pty_requires_tailscale_listener(
    settings: Settings, capsys: pytest.CaptureFixture[str]
) -> None:
    settings.server.host = "0.0.0.0"

    application = create_app(settings)

    assert application.state.codex_pty_available is False
    assert "Codex PTY is disabled" in capsys.readouterr().err


def test_codex_pty_is_available_on_tailscale_listener(
    settings: Settings,
) -> None:
    settings.server.host = "100.100.100.100"

    application = create_app(settings)

    assert application.state.codex_pty_available is True
