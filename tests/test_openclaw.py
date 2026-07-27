from unittest.mock import MagicMock

import pytest

from app.core.response import ApiError
from app.services.openclaw import OpenClawManager, OpenClawStatus


def status_payload(
    *,
    configured: bool = True,
    service_installed: bool = True,
    loaded: bool = False,
    ready: bool = False,
    port_status: str = "free",
) -> dict:
    return {
        "cli": {"version": "2026.7.1-2"},
        "service": {
            "label": "LaunchAgent",
            "loaded": loaded,
            "command": (
                {
                    "programArguments": ["/usr/local/bin/node", "openclaw"],
                    "sourcePath": "/tmp/ai.openclaw.gateway.plist",
                }
                if service_installed
                else {}
            ),
            "runtime": {"status": "running" if loaded else "unknown"},
        },
        "config": {
            "cli": {"exists": configured, "valid": configured},
        },
        "gateway": {
            "bindMode": "loopback",
            "port": 18789,
        },
        "port": {"status": port_status},
        "rpc": {"ok": ready},
    }


def openclaw_status(state: str) -> OpenClawStatus:
    return OpenClawManager._parse_status(
        {
            "stopped": status_payload(),
            "running": status_payload(loaded=True, ready=True, port_status="listening"),
            "degraded": status_payload(
                loaded=True,
                ready=False,
                port_status="listening",
            ),
        }[state]
    )


@pytest.mark.parametrize(
    ("payload", "expected_state"),
    [
        (status_payload(configured=False), "unconfigured"),
        (status_payload(service_installed=False), "service_missing"),
        (status_payload(), "stopped"),
        (
            status_payload(loaded=True, ready=False, port_status="listening"),
            "degraded",
        ),
        (
            status_payload(loaded=True, ready=True, port_status="listening"),
            "running",
        ),
    ],
)
def test_openclaw_status_is_derived_from_curated_json(
    payload: dict,
    expected_state: str,
) -> None:
    status = OpenClawManager._parse_status(payload)

    assert status.state == expected_state
    assert status.version == "2026.7.1-2"
    assert status.port == 18789
    assert status.service_manager == "LaunchAgent"
    assert status.model_dump().keys() == {
        "state",
        "installed",
        "configured",
        "service_installed",
        "service_loaded",
        "ready",
        "version",
        "service_manager",
        "bind_mode",
        "port",
        "access_url",
        "message",
        "checked_at",
    }


def test_tailscale_access_url_requires_https_ts_net_proxy_to_gateway() -> None:
    payload = {
        "Web": {
            "chuyh-macbook.tail71831.ts.net:443": {
                "Handlers": {
                    "/": {"Proxy": "http://127.0.0.1:18789"},
                }
            },
            "malicious.example.com:443": {
                "Handlers": {
                    "/": {"Proxy": "http://127.0.0.1:18789"},
                }
            },
        }
    }

    assert OpenClawManager._parse_tailscale_access_url(payload, 18789) == (
        "https://chuyh-macbook.tail71831.ts.net/"
    )
    assert OpenClawManager._parse_tailscale_access_url(payload, 19000) is None


def test_tailscale_access_url_rejects_non_root_or_non_loopback_proxy() -> None:
    assert OpenClawManager._parse_tailscale_access_url(
        {
            "Web": {
                "chuyh-macbook.tail71831.ts.net:443": {
                    "Handlers": {
                        "/chat": {"Proxy": "http://127.0.0.1:18789"},
                    }
                }
            }
        },
        18789,
    ) is None
    assert OpenClawManager._parse_tailscale_access_url(
        {
            "Web": {
                "chuyh-macbook.tail71831.ts.net:443": {
                    "Handlers": {
                        "/": {"Proxy": "http://192.168.1.2:18789"},
                    }
                }
            }
        },
        18789,
    ) is None


def test_openclaw_status_reports_missing_cli(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = OpenClawManager()
    monkeypatch.setattr(manager, "_resolve_executable", lambda: None)

    status = manager.status()

    assert status.state == "unavailable"
    assert status.installed is False


def test_openclaw_start_waits_for_ready_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = OpenClawManager()
    stopped = openclaw_status("stopped")
    running = openclaw_status("running")
    status = MagicMock(side_effect=[stopped, running])
    run_json = MagicMock(return_value={})
    monkeypatch.setattr(manager, "status", status)
    monkeypatch.setattr(manager, "_resolve_executable", lambda: "/usr/bin/openclaw")
    monkeypatch.setattr(manager, "_run_json", run_json)

    result = manager.control("start")

    assert result.state == "running"
    run_json.assert_called_once_with(
        "/usr/bin/openclaw",
        ["gateway", "start", "--json"],
        timeout=45,
    )


def test_openclaw_stop_requires_running_gateway(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = OpenClawManager()
    monkeypatch.setattr(manager, "status", lambda: openclaw_status("stopped"))

    with pytest.raises(ApiError) as error:
        manager.control("stop")

    assert error.value.code == "openclaw_not_running"


def test_openclaw_control_rejects_unknown_status(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = OpenClawManager()
    unknown = openclaw_status("stopped").model_copy(
        update={"state": "unknown", "configured": False}
    )
    monkeypatch.setattr(manager, "status", lambda: unknown)

    with pytest.raises(ApiError) as error:
        manager.control("start")

    assert error.value.status_code == 503
    assert error.value.code == "openclaw_status_unavailable"


def test_openclaw_operation_lock_rejects_concurrent_control() -> None:
    manager = OpenClawManager()
    manager._operation_lock.acquire()
    try:
        with pytest.raises(ApiError) as error:
            manager.control("start")
    finally:
        manager._operation_lock.release()

    assert error.value.code == "openclaw_operation_in_progress"
