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
        "channel_state",
        "channel_count",
        "channel_running_count",
        "channel_message",
        "owner_state",
        "owner_count",
        "owner_message",
        "message",
        "checked_at",
    }


@pytest.mark.parametrize(
    ("payload", "expected_state", "expected_running", "expected_total"),
    [
        ({"channelAccounts": {}}, "not_configured", 0, 0),
        (
            {
                "channelAccounts": {
                    "openclaw-weixin": [
                        {
                            "enabled": True,
                            "configured": True,
                            "running": True,
                            "restartPending": False,
                            "lastError": None,
                        }
                    ]
                }
            },
            "running",
            1,
            1,
        ),
        (
            {
                "channelAccounts": {
                    "openclaw-weixin": [
                        {
                            "enabled": True,
                            "configured": True,
                            "running": False,
                        }
                    ]
                }
            },
            "stopped",
            0,
            1,
        ),
        (
            {
                "channelAccounts": {
                    "one": [
                        {
                            "enabled": True,
                            "configured": True,
                            "running": True,
                        }
                    ],
                    "two": [
                        {
                            "enabled": True,
                            "configured": True,
                            "running": False,
                        }
                    ],
                }
            },
            "degraded",
            1,
            2,
        ),
        ({}, "unknown", 0, 0),
    ],
)
def test_openclaw_channel_status_is_aggregated(
    payload: dict,
    expected_state: str,
    expected_running: int,
    expected_total: int,
) -> None:
    status, channel_ids = OpenClawManager._parse_channel_status(payload)

    assert status["channel_state"] == expected_state
    assert status["channel_running_count"] == expected_running
    assert status["channel_count"] == expected_total
    assert len(channel_ids) <= expected_total


@pytest.mark.parametrize(
    ("payload", "expected_state", "expected_count"),
    [
        ({"native": "auto"}, "not_configured", 0),
        ({"ownerAllowFrom": ["openclaw-weixin:user@im.wechat"]}, "configured", 1),
        ({"ownerAllowFrom": []}, "not_configured", 0),
        ({"ownerAllowFrom": "invalid"}, "unknown", 0),
    ],
)
def test_openclaw_owner_status_only_exposes_summary(
    payload: dict,
    expected_state: str,
    expected_count: int,
) -> None:
    status = OpenClawManager._parse_owner_status(payload)

    assert status["owner_state"] == expected_state
    assert status["owner_count"] == expected_count
    assert "user@im.wechat" not in str(status)


def test_openclaw_owner_status_requires_owner_for_configured_channel() -> None:
    status = OpenClawManager._parse_owner_status(
        {"ownerAllowFrom": ["telegram:123", "openclaw-weixin:user@im.wechat"]},
        {"openclaw-weixin"},
    )
    unrelated = OpenClawManager._parse_owner_status(
        {"ownerAllowFrom": ["telegram:123"]},
        {"openclaw-weixin"},
    )

    assert status["owner_state"] == "configured"
    assert status["owner_count"] == 1
    assert unrelated["owner_state"] == "not_configured"
    assert unrelated["owner_count"] == 0


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


def test_openclaw_status_includes_channel_summary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = OpenClawManager()
    run_json = MagicMock(
        side_effect=[
            status_payload(loaded=True, ready=True, port_status="listening"),
            {
                "channelAccounts": {
                    "openclaw-weixin": [
                        {
                            "enabled": True,
                            "configured": True,
                            "running": True,
                        }
                    ]
                }
            },
            {"ownerAllowFrom": ["openclaw-weixin:user@im.wechat"]},
        ]
    )
    monkeypatch.setattr(manager, "_resolve_executable", lambda: "/usr/bin/openclaw")
    monkeypatch.setattr(manager, "_run_json", run_json)
    monkeypatch.setattr(manager, "_tailscale_access_url", lambda port: None)

    status = manager.status()

    assert status.state == "running"
    assert status.channel_state == "running"
    assert status.channel_running_count == 1
    assert status.channel_count == 1
    assert status.owner_state == "configured"
    assert status.owner_count == 1
    assert run_json.call_args_list[1].args[1] == ["channels", "status", "--json"]
    assert run_json.call_args_list[2].args[1] == [
        "config",
        "get",
        "commands",
        "--json",
    ]


def test_openclaw_channel_check_failure_preserves_gateway_status(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = OpenClawManager()
    run_json = MagicMock(
        side_effect=[
            status_payload(loaded=True, ready=True, port_status="listening"),
            ApiError(502, "openclaw_command_failed", "failed"),
            {"native": "auto"},
        ]
    )
    monkeypatch.setattr(manager, "_resolve_executable", lambda: "/usr/bin/openclaw")
    monkeypatch.setattr(manager, "_run_json", run_json)
    monkeypatch.setattr(manager, "_tailscale_access_url", lambda port: None)

    status = manager.status()

    assert status.state == "running"
    assert status.channel_state == "unknown"
    assert status.channel_message == "消息通道状态检查失败，请刷新后重试。"
    assert status.owner_state == "not_configured"


def test_openclaw_start_waits_for_ready_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = OpenClawManager()
    stopped = openclaw_status("stopped")
    running = openclaw_status("running")
    status = MagicMock(side_effect=[stopped, running])
    gateway_status = MagicMock(return_value=(running, "/usr/bin/openclaw"))
    run_json = MagicMock(return_value={})
    monkeypatch.setattr(manager, "status", status)
    monkeypatch.setattr(manager, "_gateway_status", gateway_status)
    monkeypatch.setattr(manager, "_resolve_executable", lambda: "/usr/bin/openclaw")
    monkeypatch.setattr(manager, "_run_json", run_json)

    result = manager.control("start")

    assert result.state == "running"
    gateway_status.assert_called_once_with()
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
