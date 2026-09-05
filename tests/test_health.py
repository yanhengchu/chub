import httpx
import pytest

from app.application import _confirm_healthy_instance, create_app
from app.core.build_info import SESSION_SCHEMA_VERSION, WEB_CODE_VERSION
from app.core.config import Settings
from app.server import listen_sockets


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


def test_tailnet_discovery_does_not_disable_local_runtime(
    settings: Settings,
) -> None:
    application = create_app(settings)

    assert application.state.codex_pty_manager.runtime_adapter.status().reason is None


def test_unavailable_tailnet_listener_keeps_loopback_available(
    settings: Settings,
    caplog: pytest.LogCaptureFixture,
) -> None:
    loopback_listener = object()

    def listen(host: str, _port: int) -> object:
        if host == "127.0.0.1":
            return loopback_listener
        raise OSError(49, "Can't assign requested address")

    availability = []

    with caplog.at_level("WARNING", logger="hub.startup"):
        listeners = listen_sockets(
            settings,
            set_tailnet_listener_available=availability.append,
            bind_listener=listen,
            find_tailnet_hosts=lambda: ("100.64.0.20",),
        )

    assert listeners == [loopback_listener]
    assert availability == [False]
    assert "continuing with loopback only" in caplog.text


def test_auto_tailnet_listener_binds_discovered_host(
    settings: Settings,
) -> None:
    loopback_listener = object()
    tailnet_listener = object()
    availability = []
    hosts = []

    listeners = listen_sockets(
        settings,
        set_tailnet_listener_available=availability.append,
        set_tailnet_listener_hosts=hosts.append,
        bind_listener=lambda host, _port: (
            loopback_listener if host == "127.0.0.1" else tailnet_listener
        ),
        find_tailnet_hosts=lambda: ("100.64.0.20",),
    )

    assert listeners == [loopback_listener, tailnet_listener]
    assert availability == [True]
    assert hosts == [("100.64.0.20",)]


def test_unavailable_loopback_listener_still_fails(
    settings: Settings,
) -> None:
    with pytest.raises(OSError, match="Address already in use"):
        listen_sockets(
            settings,
            set_tailnet_listener_available=lambda _available: None,
            bind_listener=lambda _host, _port: (_ for _ in ()).throw(
                OSError(48, "Address already in use")
            ),
        )
