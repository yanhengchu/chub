import httpx
import pytest

from app.application import _confirm_healthy_instance, create_app
from app.core.build_info import SESSION_SCHEMA_VERSION, WEB_CODE_VERSION
from app.core.config import Settings


@pytest.mark.anyio
async def test_health_is_public(settings: Settings) -> None:
    app = create_app(settings)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/api/health")

    assert response.status_code == 200
    assert response.json() == {
        "success": True,
        "data": {
            "service": "hub",
            "status": "ok",
            "version": "0.1.0",
            "instance_id": app.state.instance_id,
            "quick_worker_ready": False,
            "code_version": WEB_CODE_VERSION,
            "session_schema_version": SESSION_SCHEMA_VERSION,
        },
    }


@pytest.mark.anyio
async def test_restart_recovery_waits_for_matching_healthy_instance(
    settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    responses = ["old-instance", "new-instance"]
    requested_urls = []

    class Response:
        status_code = 200

        def json(self):
            return {
                "data": {
                    "status": "ok",
                    "instance_id": responses.pop(0),
                }
            }

    class Client:
        def __init__(self, **_kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def get(self, url):
            requested_urls.append(str(url))
            return Response()

    async def no_delay(_seconds):
        return None

    monkeypatch.setattr("app.application.httpx.AsyncClient", Client)
    monkeypatch.setattr("app.application.asyncio.sleep", no_delay)

    await _confirm_healthy_instance(settings, "new-instance")

    assert len(requested_urls) == 2
    assert requested_urls[0].endswith("/api/health")


def test_local_and_tailnet_access_requires_no_credential(settings: Settings) -> None:
    application = create_app(settings)

    assert application.state.settings.security.allow_tailscale is True


def test_codex_pty_requires_tailscale_listener(
    settings: Settings, capsys: pytest.CaptureFixture[str]
) -> None:
    settings.server.tailnet_host = "0.0.0.0"

    application = create_app(settings)

    assert application.state.codex_pty_available is False
    assert "Codex PTY is disabled" in capsys.readouterr().err


def test_codex_pty_is_available_on_tailscale_listener(
    settings: Settings,
) -> None:
    settings.server.tailnet_host = "100.100.100.100"

    application = create_app(settings)

    assert application.state.codex_pty_available is True
