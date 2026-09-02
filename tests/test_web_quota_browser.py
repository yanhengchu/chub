from __future__ import annotations

import json
import os
from pathlib import Path
import socket
from threading import Thread
import time
from urllib.parse import urlsplit

import pytest
import uvicorn
from playwright.async_api import expect

from app.application import create_app
from app.automations.browser import session_factory
from app.core.config import Settings


RUN_BROWSER_TESTS = os.getenv("CHUB_BROWSER_TESTS") == "1"

pytestmark = [
    pytest.mark.anyio,
    pytest.mark.browser,
    pytest.mark.skipif(
        not RUN_BROWSER_TESTS,
        reason="set CHUB_BROWSER_TESTS=1 to run managed Chrome regression tests",
    ),
]

STATUS_RESPONSE = {
    "success": True,
    "data": {
        "node": {
            "id": "browser-test-node",
            "name": "Browser Test Node",
            "configured_platform": "ubuntu",
            "detected_platform": "ubuntu",
        },
        "system": {
            "hostname": "browser-test",
            "operating_system": "Linux",
            "operating_system_version": "test",
            "python_version": "3.12",
            "cpu_percent": 12.5,
            "memory_total_bytes": 1,
            "memory_used_bytes": 1,
            "memory_percent": 25.0,
            "disk_total_bytes": 1,
            "disk_used_bytes": 1,
            "disk_percent": 40.0,
            "boot_time": "2026-08-15T00:00:00Z",
            "uptime_seconds": 1,
        },
        "hub": {
            "version": "0.1.0",
            "current_time": "2026-08-15T10:00:00Z",
        },
    },
}

USAGE_RESPONSES = {
    "complete": {
        "status": "available",
        "provider": "openai",
        "source": "sub2api",
        "timezone": "Asia/Shanghai",
        "stale": False,
        "message": None,
        "checked_at": "2026-08-15T10:00:00+08:00",
        "weekly": {
            "remaining_percent": 78,
            "used_usd": "218.08",
            "remaining_usd": "781.92",
            "limit_usd": 1000.0,
            "window_duration_minutes": 10080,
            "resets_at": "2026-08-20T15:45:00+08:00",
        },
        "today": {
            "date": "2026-08-15",
            "used_usd": 181.02,
            "tokens": 100000000,
            "tokens_scope": "account",
        },
        "display": {
            "short": "Weekly 78% · 8/20 · Today 100M",
            "long": (
                "Weekly $781.92 left (78%) · Limit $1,000"
                " · Reset 8/20 15:45"
                " · Today $181.02 used 100M tokens"
            ),
        },
    },
    "compact": {
        "status": "available",
        "provider": "openai",
        "source": "account_login",
        "timezone": "Asia/Shanghai",
        "stale": False,
        "message": None,
        "checked_at": "2026-08-15T10:00:00+08:00",
        "weekly": {
            "remaining_percent": 78,
            "used_usd": None,
            "remaining_usd": None,
            "limit_usd": None,
            "window_duration_minutes": 10080,
            "resets_at": "2026-08-20T14:44:00+08:00",
        },
        "today": {
            "date": "2026-08-15",
            "used_usd": None,
            "tokens": 5600000,
            "tokens_scope": "account",
        },
        "display": {
            "short": "Weekly 78% · 8/20 · Today 5.6M",
            "long": "Weekly 78% left · Reset 8/20 14:44 · Today 5.6M tokens",
        },
    },
    "compact-local": {
        "status": "available",
        "provider": "openai",
        "source": "account_login",
        "timezone": "Asia/Shanghai",
        "stale": False,
        "message": None,
        "checked_at": "2026-08-15T10:00:00+08:00",
        "weekly": {
            "remaining_percent": 78,
            "used_usd": None,
            "remaining_usd": None,
            "limit_usd": None,
            "window_duration_minutes": 10080,
            "resets_at": "2026-08-20T14:44:00+08:00",
        },
        "today": {
            "date": "2026-08-15",
            "used_usd": None,
            "tokens": 5600000,
            "tokens_scope": "local_device",
        },
        "display": {
            "short": "Weekly 78% · 8/20 · Today 5.6M (local)",
            "long": (
                "Weekly 78% left · Reset 8/20 14:44"
                " · Today 5.6M tokens (local)"
            ),
        },
    },
}

WINDOWED_USAGE = {
    "status": "available",
    "provider": "openai",
    "source": "account_login",
    "timezone": "Asia/Shanghai",
    "stale": False,
    "message": None,
    "checked_at": "2026-08-15T10:00:00+08:00",
    "weekly": {
        "remaining_percent": 78,
        "used_usd": None,
        "remaining_usd": None,
        "limit_usd": None,
        "window_duration_minutes": 10080,
        "resets_at": "2026-08-20T14:44:00+08:00",
    },
    "five_hour": {
        "remaining_percent": 42,
        "window_duration_minutes": 300,
        "resets_at": "2026-08-15T18:20:00+08:00",
    },
    "today": {
        "date": "2026-08-15",
        "used_usd": None,
        "tokens": 5600000,
        "tokens_scope": "account",
    },
    "display": {
        "short": "5h 42% · 18:20 · Today 5.6M",
        "long": "5h 42% left · Reset 8/15 18:20 · Weekly 78% left · Reset 8/20 14:44 · Today 5.6M tokens",
        "home": [
            {"kind": "five_hour", "text": "5h 42% left"},
            {"kind": "reset", "text": "Reset 8/15 18:20"},
            {"kind": "weekly", "text": "Weekly 78% left"},
            {"kind": "reset", "text": "Reset 8/20 14:44"},
            {"kind": "today", "text": "Today 5.6M tokens"},
        ],
    },
}

EXPECTED_PARTS = {
    "complete": [
        "Weekly $781.92 left (78%) · Limit $1,000",
        "Reset 8/20 15:45",
        "Today $181.02 used 100M tokens",
    ],
    "compact": [
        "Weekly 78% left",
        "Reset 8/20 14:44",
        "Today 5.6M tokens",
    ],
    "compact-local": [
        "Weekly 78% left",
        "Reset 8/20 14:44",
        "Today 5.6M tokens (local)",
    ],
}

VIEWPORTS = {
    "android-compact": (
        (360, 800),
        {
            "complete": (0, 1, 2),
            "compact": (0, 0, 1),
            "compact-local": (0, 0, 1),
        },
    ),
    "phone": (
        (390, 844),
        {
            "complete": (0, 1, 2),
            "compact": (0, 0, 1),
            "compact-local": (0, 0, 1),
        },
    ),
    "phone-boundary": (
        (420, 900),
        {
            "complete": (0, 1, 2),
            "compact": (0, 0, 1),
            "compact-local": (0, 0, 1),
        },
    ),
    "android-large": (
        (412, 915),
        {
            "complete": (0, 1, 2),
            "compact": (0, 0, 1),
            "compact-local": (0, 0, 1),
        },
    ),
    "above-phone-boundary": (
        (421, 900),
        {
            "complete": (0, 1, 1),
            "compact": (0, 0, 0),
            "compact-local": (0, 0, 1),
        },
    ),
    "tablet": (
        (720, 1024),
        {
            "complete": (0, 0, 0),
            "compact": (0, 0, 0),
            "compact-local": (0, 0, 0),
        },
    ),
    "desktop": (
        (1280, 900),
        {
            "complete": (0, 1, 1),
            "compact": (0, 0, 0),
            "compact-local": (0, 0, 0),
        },
    ),
}


def _browser_settings(root: Path) -> Settings:
    return Settings.model_validate(
        {
            "app": {"name": "Chub", "version": "0.1.0"},
            "node": {
                "id": "browser-test-node",
                "name": "Browser Test Node",
                "type": "ubuntu",
            },
            "server": {"port": 8080},
            "security": {"allow_tailscale": False},
            "logs": {
                "file": root / "hub.log",
                "operations_file": root / "operations.log",
                "worker_operations_file": root / "worker-operations.log",
                "level": "ERROR",
                "max_lines": 100,
            },
            "codex_pty": {
                "enabled": True,
                "workspace": root / "workspace",
                "data_file": root / "codex-sessions.json",
                "runtime_dir": root / "codex-runtime",
            },
            "automations": {
                "shared_config_file": root / "automations.yaml",
                "local_config_file": root / "automations.local.yaml",
                "state_dir": root / "automation-state",
                "runtime_dir": root / "automation-runtime",
                "artifacts_dir": root / "automation-artifacts",
            },
            "project_documents": {
                "state_file": root / "project-documents.json",
            },
            "requests": {"state_file": root / "requests.json"},
            "notifications": {
                "enabled": False,
                "registry_file": root / "notifications.yaml",
                "secrets_dir": root / "notification-secrets",
            },
            "openclaw": {
                "quick_interaction_completion": {"enabled": False},
                "weixin_chub_mode": {
                    "state_file": root / "weixin-chub-mode.json",
                },
            },
        }
    )


@pytest.fixture(scope="module")
def quota_browser_server(tmp_path_factory: pytest.TempPathFactory) -> str:
    root = tmp_path_factory.mktemp("quota-browser")
    application = create_app(_browser_settings(root))
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind(("127.0.0.1", 0))
    listener.listen(128)
    port = listener.getsockname()[1]
    server = uvicorn.Server(
        uvicorn.Config(
            application,
            log_level="critical",
            lifespan="off",
        )
    )
    thread = Thread(
        target=server.run,
        kwargs={"sockets": [listener]},
        name="quota-browser-test-server",
        daemon=True,
    )
    thread.start()
    deadline = time.monotonic() + 10
    while not server.started and thread.is_alive() and time.monotonic() < deadline:
        time.sleep(0.01)
    if not server.started:
        server.should_exit = True
        thread.join(timeout=5)
        listener.close()
        pytest.fail("isolated Chub browser test server did not start")

    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        thread.join(timeout=10)
        listener.close()
        if thread.is_alive():
            pytest.fail("isolated Chub browser test server did not stop")


async def _mock_protected_api(
    route,
    usage: dict[str, object],
    usage_requests: list[str],
) -> None:
    path = urlsplit(route.request.url).path
    if path == "/api/status":
        status = 200
        payload = STATUS_RESPONSE
    elif path == "/api/ai/usage":
        usage_requests.append(route.request.url)
        status = 200
        payload = {"success": True, "data": usage}
    else:
        status = 503
        payload = {
            "success": False,
            "error": {
                "code": "browser_test_unavailable",
                "message": "Not required by the quota browser test",
            },
        }
    await route.fulfill(
        status=status,
        content_type="application/json",
        body=json.dumps(payload),
    )


def _layout_cases() -> list[object]:
    cases = []
    for theme in ("standard", "cyber"):
        for shape in ("complete", "compact", "compact-local"):
            for viewport_name, (viewport, expected_by_shape) in VIEWPORTS.items():
                cases.append(
                    pytest.param(
                        theme,
                        shape,
                        viewport,
                        expected_by_shape[shape],
                        id=f"{theme}-{shape}-{viewport_name}",
                    )
                )
    return cases


@pytest.mark.parametrize(
    ("theme", "shape", "viewport", "expected_lines"),
    _layout_cases(),
)
async def test_home_quota_layout_matrix_in_managed_chrome(
    quota_browser_server: str,
    theme: str,
    shape: str,
    viewport: tuple[int, int],
    expected_lines: tuple[int, int, int],
) -> None:
    usage = USAGE_RESPONSES[shape]
    usage_requests: list[str] = []
    browser_session = session_factory()
    async with browser_session(ensure_page=False) as chrome:
        context = await chrome.browser.new_context(
            viewport={"width": viewport[0], "height": viewport[1]},
            reduced_motion="reduce",
        )
        try:
            await context.route(
                f"{quota_browser_server}/api/**",
                lambda route: _mock_protected_api(route, usage, usage_requests),
            )
            page = await context.new_page()
            page_errors: list[str] = []
            page.on("pageerror", lambda error: page_errors.append(str(error)))
            await page.add_init_script(
                script=(
                    "localStorage.clear();"
                    "sessionStorage.clear();"
                    f"localStorage.setItem('hub.uiStyle.v1', {json.dumps(theme)});"
                )
            )
            response = await page.goto(quota_browser_server, wait_until="domcontentloaded")
            assert response is not None and response.status == 200
            quota = page.locator("#codex-quota")
            await expect(quota).to_contain_text("Resets")
            layout = await quota.evaluate(
                """quota => {
                    const parts = Array.from(
                        quota.querySelectorAll(":scope > .codex-quota-part, "
                            + ":scope > .codex-quota-group > .codex-quota-part")
                    );
                    const tops = parts.map(node => node.getBoundingClientRect().top);
                    const lineTops = [];
                    const lines = tops.map(top => {
                        let line = lineTops.findIndex(value => Math.abs(value - top) < 1);
                        if (line < 0) {
                            lineTops.push(top);
                            line = lineTops.length - 1;
                        }
                        return line;
                    });
                    const lineHeight = Number.parseFloat(getComputedStyle(quota).lineHeight);
                    return {
                        theme: document.documentElement.dataset.uiStyle,
                        text: quota.textContent,
                        lines,
                        partTexts: parts.map(node => node.textContent),
                        partRectCounts: parts.map(node => node.getClientRects().length),
                        height: quota.getBoundingClientRect().height,
                        lineHeight,
                        quotaOverflow: quota.scrollWidth - quota.clientWidth,
                        pageOverflow: document.documentElement.scrollWidth - innerWidth,
                    };
                }"""
            )
        finally:
            await context.close()

    long_text = usage["display"]["long"]
    assert layout["theme"] == theme
    assert layout["text"] == long_text
    assert layout["partTexts"] == EXPECTED_PARTS[shape]
    assert layout["lines"] == list(expected_lines)
    assert layout["partRectCounts"] == [1, 1, 1]
    assert layout["height"] <= layout["lineHeight"] * (max(expected_lines) + 1) + 1
    assert layout["quotaOverflow"] <= 1
    assert layout["pageOverflow"] <= 1
    assert page_errors == []
    assert len(usage_requests) == 1


async def test_home_quota_windowed_display_in_managed_chrome(
    quota_browser_server: str,
) -> None:
    usage_requests: list[str] = []
    browser_session = session_factory()
    async with browser_session(ensure_page=False) as chrome:
        context = await chrome.browser.new_context(
            viewport={"width": 390, "height": 844},
            reduced_motion="reduce",
        )
        try:
            await context.route(
                f"{quota_browser_server}/api/**",
                lambda route: _mock_protected_api(
                    route,
                    WINDOWED_USAGE,
                    usage_requests,
                ),
            )
            page = await context.new_page()
            page_errors: list[str] = []
            page.on("pageerror", lambda error: page_errors.append(str(error)))
            response = await page.goto(
                quota_browser_server,
                wait_until="domcontentloaded",
            )
            assert response is not None and response.status == 200
            quota = page.locator("#codex-quota")
            await expect(quota).to_contain_text("5h 42% left")
            layout = await quota.evaluate(
                """quota => {
                    const groups = Array.from(
                        quota.querySelectorAll(":scope > .codex-quota-home-group")
                    );
                    const parts = groups.flatMap(group => Array.from(
                        group.querySelectorAll(":scope > .codex-quota-home-part")
                    ));
                    return {
                        text: quota.textContent,
                        parts: parts.map(node => node.textContent),
                        groupLines: groups.map(node => node.getBoundingClientRect().top),
                        quotaOverflow: quota.scrollWidth - quota.clientWidth,
                        pageOverflow: document.documentElement.scrollWidth - innerWidth,
                    };
                }"""
            )
        finally:
            await context.close()

    assert layout["parts"] == [part["text"] for part in WINDOWED_USAGE["display"]["home"]]
    assert len({round(value) for value in layout["groupLines"]}) == 3
    assert layout["quotaOverflow"] <= 1
    assert layout["pageOverflow"] <= 1
    assert page_errors == []
    assert len(usage_requests) == 1
    assert urlsplit(usage_requests[0]).query == ""
