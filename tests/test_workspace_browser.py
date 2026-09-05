from __future__ import annotations

import json
import os
from pathlib import Path
import re
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
from app.web.themes import WEB_FONT_SIZES, WEB_THEMES


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
        "node": {"detected_platform": "ubuntu"},
        "system": {
            "operating_system": "Linux",
            "operating_system_version": "test",
            "python_version": "3.12",
            "cpu_percent": 12.5,
            "memory_percent": 25.0,
            "disk_percent": 40.0,
        },
        "tailnet": {"state": "unknown", "endpoints": []},
    },
}

WORKER_RESPONSE = {
    "success": True,
    "data": {
        "state": "ready",
        "message": "Quick Worker 已就绪。",
        "runtime_state": "available",
        "runtime_message": "Codex 可用。",
        "runtimes": [],
        "can_restart": True,
        "operation": None,
    },
}

UPGRADE_RESPONSE = {
    "success": True,
    "data": {
        "state": "idle",
        "message": "当前无需升级。",
        "can_start": False,
        "operation": None,
        "plan": None,
    },
}

OPENCLAW_RESPONSE = {
    "success": True,
    "data": {
        "installed": True,
        "configured": True,
        "state": "running",
        "message": "Gateway 正常。",
        "channel_state": "running",
        "channel_message": "微信通道已连接。",
        "owner_message": "Owner 已配置。",
    },
}

WEIXIN_LOGIN_RESPONSE = {
    "success": True,
    "data": {
        "state": "idle",
        "message": "微信通道已连接。",
        "qr_available": False,
        "updated_at": "2026-08-15T10:00:00Z",
    },
}

OPENCLAW_INTEGRATION_RESPONSE = {
    "success": True,
    "data": {
        "weixin_adapter": {
            "version": "2.4.8",
            "expected_version": "2.4.8",
            "state": "verified",
            "message": "微信 ClawBot 适配器已匹配。",
        },
        "chub_plugin": {
            "version": "0.1.1",
            "expected_version": "0.1.1",
            "state": "verified",
            "message": "Chub 插件已匹配。",
        },
        "patches": [
            {
                "identifier": "weixin-chub-compatibility",
                "version": "1.0.0",
                "scope": "source-and-runtime",
                "state": "declared",
            }
        ],
        "message": "插件配置和安装元数据已匹配。",
        "checked_at": "2026-09-05T10:00:00Z",
    },
}


def _browser_settings(root: Path) -> Settings:
    return Settings.model_validate(
        {
            "app": {"name": "Chub", "version": "0.1.0"},
            "node": {"id": "browser-test-node", "name": "Browser Test Node", "type": "ubuntu"},
            "server": {"port": 8080},
            "security": {"allow_tailscale": False},
            "logs": {
                "file": root / "hub.log",
                "operations_file": root / "operations.log",
                "worker_operations_file": root / "worker-operations.log",
                "level": "ERROR",
                "max_lines": 100,
            },
            "ai_runtime": {"codex": {
                "enabled": True,
                "workspace": root / "workspace",
                "data_file": root / "codex-sessions.json",
                "runtime_dir": root / "codex-runtime",
            }},
            "automations": {
                "shared_config_file": root / "automations.yaml",
                "local_config_file": root / "automations.local.yaml",
                "state_dir": root / "automation-state",
                "runtime_dir": root / "automation-runtime",
                "artifacts_dir": root / "automation-artifacts",
            },
            "project_documents": {"state_file": root / "project-documents.json"},
            "requests": {"state_file": root / "requests.json"},
            "notifications": {
                "enabled": False,
                "registry_file": root / "notifications.yaml",
                "secrets_dir": root / "notification-secrets",
            },
            "openclaw": {
                "quick_interaction_completion": {"enabled": False},
                "weixin_chub_mode": {"state_file": root / "weixin-chub-mode.json"},
            },
        }
    )


@pytest.fixture(scope="module")
def workspace_browser_server(tmp_path_factory: pytest.TempPathFactory) -> str:
    root = tmp_path_factory.mktemp("workspace-browser")
    application = create_app(_browser_settings(root))
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind(("127.0.0.1", 0))
    listener.listen(128)
    port = listener.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(application, log_level="critical", lifespan="off"))
    thread = Thread(target=server.run, kwargs={"sockets": [listener]}, daemon=True)
    thread.start()
    deadline = time.monotonic() + 10
    while not server.started and thread.is_alive() and time.monotonic() < deadline:
        time.sleep(0.01)
    if not server.started:
        server.should_exit = True
        thread.join(timeout=5)
        listener.close()
        pytest.fail("isolated Chub workspace browser test server did not start")
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        thread.join(timeout=10)
        listener.close()
        if thread.is_alive():
            pytest.fail("isolated Chub workspace browser test server did not stop")


async def _mock_workspace_api(route) -> None:
    path = urlsplit(route.request.url).path
    payload = {
        "/api/status": STATUS_RESPONSE,
        "/api/maintenance/quick-worker": WORKER_RESPONSE,
        "/api/maintenance/system-upgrade": UPGRADE_RESPONSE,
        "/api/openclaw/status": OPENCLAW_RESPONSE,
        "/api/openclaw/integration": OPENCLAW_INTEGRATION_RESPONSE,
        "/api/openclaw/weixin/login": WEIXIN_LOGIN_RESPONSE,
        "/api/codex/sessions": {"success": True, "data": {"available": False, "sessions": []}},
    }.get(path)
    if payload is None:
        payload = {
            "success": False,
            "error": {"code": "browser_test_unavailable", "message": "Not required by this test."},
        }
        status = 503
    else:
        status = 200
    await route.fulfill(status=status, content_type="application/json", body=json.dumps(payload))


async def test_openclaw_settings_only_requests_integration_metadata(
    workspace_browser_server: str,
) -> None:
    browser_session = session_factory()
    async with browser_session(ensure_page=False) as chrome:
        context = await chrome.browser.new_context(viewport={"width": 1280, "height": 900})
        requested_paths: list[str] = []

        async def route_workspace_api(route) -> None:
            requested_paths.append(urlsplit(route.request.url).path)
            await _mock_workspace_api(route)

        try:
            await context.route(
                f"{workspace_browser_server}/api/**",
                route_workspace_api,
            )
            page = await context.new_page()
            page_errors: list[str] = []
            page.on("pageerror", lambda error: page_errors.append(str(error)))

            response = await page.goto(
                f"{workspace_browser_server}/settings/openclaw",
                wait_until="domcontentloaded",
            )

            assert response is not None and response.status == 200
            await expect(page.locator("#settings-openclaw-integration-list")).to_contain_text(
                "微信 ClawBot 适配器",
            )
            await expect(page.locator("#settings-openclaw-patch-list")).to_contain_text(
                "已登记",
            )
        finally:
            await context.close()

    assert requested_paths == ["/api/openclaw/integration"]
    assert page_errors == []


async def _mock_workspace_api_with_quick_sessions(route) -> None:
    path = urlsplit(route.request.url).path
    if path != "/api/codex/sessions":
        await _mock_workspace_api(route)
        return
    payload = {
        "success": True,
        "data": {
            "available": True,
            "quick_creation": {"available": True},
            "terminal_creation": {"available": True},
            "workspaces": [],
            "sessions": [
                {
                    "id": "quick-slot",
                    "title": "带槽位的快速会话",
                    "created_at": "2026-08-15T09:00:00Z",
                    "session_mode": "quick",
                    "status": "stopped",
                    "activity": "idle",
                    "quick_interaction_running": False,
                    "weixin_session_slot": 1,
                },
                {
                    "id": "quick-unassigned",
                    "title": "未分配槽位的快速会话",
                    "created_at": "2026-08-15T08:00:00Z",
                    "session_mode": "quick",
                    "status": "stopped",
                    "activity": "idle",
                    "quick_interaction_running": True,
                    "weixin_session_slot": None,
                },
                {
                    "id": "terminal-session",
                    "title": "实时会话",
                    "created_at": "2026-08-15T07:00:00Z",
                    "session_mode": "terminal",
                    "status": "stopped",
                    "activity": "idle",
                },
            ],
        },
    }
    await route.fulfill(status=200, content_type="application/json", body=json.dumps(payload))


async def _mock_workspace_api_with_task_orchestration(route) -> None:
    path = urlsplit(route.request.url).path
    payload = {
        "/api/settings/weixin-translation": {
            "success": True,
            "data": {
                "mode": "auto",
                "enabled": True,
                "model": None,
                "reasoning_effort": None,
                "queued": 0,
                "running": 0,
                "weixin_chub_mode_enabled": True,
            },
        },
        "/api/codex/models": {
            "success": True,
            "data": {
                "default_model": "gpt-test",
                "default_reasoning_effort": "medium",
                "models": [{
                    "id": "gpt-test",
                    "name": "GPT Test",
                    "description": "用于微信任务润色的测试模型。",
                    "default_level": "medium",
                    "levels": [
                        {"id": "low", "description": "更快返回结果。"},
                        {"id": "medium", "description": "平衡速度与质量。"},
                        {"id": "high", "description": "适合复杂润色。"},
                    ],
                }],
            },
        },
    }.get(path)
    if payload is not None:
        await route.fulfill(status=200, content_type="application/json", body=json.dumps(payload))
        return
    await _mock_workspace_api(route)


@pytest.mark.parametrize("viewport", [(390, 844), (1280, 900)], ids=["phone", "desktop"])
async def test_task_orchestration_opens_as_workspace_subpage_from_ai_session_group(
    workspace_browser_server: str,
    viewport: tuple[int, int],
) -> None:
    browser_session = session_factory()
    async with browser_session(ensure_page=False) as chrome:
        context = await chrome.browser.new_context(
            viewport={"width": viewport[0], "height": viewport[1]},
            reduced_motion="reduce",
        )
        try:
            await context.route(
                f"{workspace_browser_server}/api/**",
                _mock_workspace_api_with_task_orchestration,
            )
            page = await context.new_page()
            page_errors: list[str] = []
            page.on("pageerror", lambda error: page_errors.append(str(error)))
            response = await page.goto(workspace_browser_server, wait_until="domcontentloaded")
            assert response is not None and response.status == 200
            if viewport[0] < 760:
                await page.locator("#workspace-sidebar-toggle").click()
            await page.get_by_role("link", name="微信任务润色").click()
            await expect(page).to_have_url(re.compile(r"[?&]section=task-orchestration"))
            await expect(page.get_by_role("heading", name="微信任务润色")).to_be_visible()
            await expect(page.locator("#workspace-task-processing-value")).to_have_text(
                "自动润色后执行",
            )
            await page.locator("#workspace-task-processing-trigger").click()
            await expect(page.locator("#workspace-task-processing-menu")).to_contain_text(
                "先润色文本，再自动提交。",
            )
            async def assert_menu_is_anchored(menu_id: str, trigger_id: str) -> None:
                menu_position = await page.locator(menu_id).evaluate(
                    """(menu, triggerId) => {
                    const trigger = document.getElementById(triggerId);
                    const menuRect = menu.getBoundingClientRect();
                    const triggerRect = trigger.getBoundingClientRect();
                    return {
                        menuTop: menuRect.top,
                        menuBottom: menuRect.bottom,
                        menuRight: menuRect.right,
                        triggerTop: triggerRect.top,
                        triggerBottom: triggerRect.bottom,
                        triggerRight: triggerRect.right,
                    };
                    }""",
                    trigger_id,
                )
                assert (
                    menu_position["menuTop"] >= menu_position["triggerBottom"]
                    or menu_position["menuBottom"] <= menu_position["triggerTop"]
                )
                assert abs(menu_position["menuRight"] - menu_position["triggerRight"]) <= 1

            await assert_menu_is_anchored(
                "#workspace-task-processing-menu",
                "workspace-task-processing-trigger",
            )
            await page.keyboard.press("Escape")
            await expect(page.locator("#workspace-task-model-value")).to_have_text(
                re.compile("跟随 Codex 默认"),
            )
            await page.locator("#workspace-task-model-trigger").click()
            await expect(page.locator("#workspace-task-model-menu")).to_contain_text(
                "用于微信任务润色的测试模型。",
            )
            await assert_menu_is_anchored(
                "#workspace-task-model-menu",
                "workspace-task-model-trigger",
            )
            await page.keyboard.press("Escape")
            await page.locator("#workspace-task-reasoning-trigger").click()
            await expect(page.locator("#workspace-task-reasoning-menu")).to_contain_text(
                "平衡速度与质量。",
            )
            await assert_menu_is_anchored(
                "#workspace-task-reasoning-menu",
                "workspace-task-reasoning-trigger",
            )
            bounds = await page.locator(".workspace-task-orchestration-panel").evaluate(
                """(element) => {
                    const rect = element.getBoundingClientRect();
                    return { left: rect.left, right: rect.right, top: rect.top, bottom: rect.bottom };
                }""",
            )
            assert bounds["left"] >= 0
            assert bounds["right"] <= viewport[0]
            assert bounds["top"] >= 0
            assert bounds["bottom"] <= viewport[1]
            assert bounds["bottom"] <= viewport[1]
        finally:
            await context.close()

    assert page_errors == []


@pytest.mark.parametrize("theme", [theme.id for theme in WEB_THEMES])
@pytest.mark.parametrize("viewport", [(390, 844), (1280, 900)], ids=["phone", "desktop"])
async def test_workspace_layout_in_managed_chrome(
    workspace_browser_server: str,
    theme: str,
    viewport: tuple[int, int],
) -> None:
    browser_session = session_factory()
    async with browser_session(ensure_page=False) as chrome:
        context = await chrome.browser.new_context(
            viewport={"width": viewport[0], "height": viewport[1]},
            reduced_motion="reduce",
        )
        try:
            await context.route(f"{workspace_browser_server}/api/**", _mock_workspace_api)
            page = await context.new_page()
            page_errors: list[str] = []
            page.on("pageerror", lambda error: page_errors.append(str(error)))
            await page.add_init_script(
                script=(
                    "localStorage.clear();sessionStorage.clear();"
                    f"localStorage.setItem('hub.uiStyle.v1', {json.dumps(theme)});"
                )
            )
            response = await page.goto(workspace_browser_server, wait_until="domcontentloaded")
            assert response is not None and response.status == 200
            await expect(page.locator("#workspace-chub-detail")).to_contain_text("Linux test")
            await expect(page.locator("#workspace-worker-detail")).to_have_text("Quick Worker 已就绪。")
            await expect(page.locator("#workspace-openclaw-detail")).to_have_text(
                "Gateway 运行正常并已通过连接探测。"
            )
            layout = await page.evaluate(
                """() => ({
                    theme: document.documentElement.dataset.uiStyle,
                    overflow: document.documentElement.scrollWidth - innerWidth,
                    summaryCount: document.querySelectorAll('.workspace-preview-summary article').length,
                    colorScheme: getComputedStyle(document.documentElement).colorScheme,
                })"""
            )
        finally:
            await context.close()

    assert layout["theme"] == theme
    assert layout["overflow"] <= 1
    assert layout["summaryCount"] == 3
    assert layout["colorScheme"] == next(item.color_scheme for item in WEB_THEMES if item.id == theme)
    assert page_errors == []


async def test_workspace_toolbar_feedback_in_managed_chrome(
    workspace_browser_server: str,
) -> None:
    browser_session = session_factory()
    async with browser_session(ensure_page=False) as chrome:
        context = await chrome.browser.new_context(viewport={"width": 1280, "height": 900})
        try:
            await context.route(f"{workspace_browser_server}/api/**", _mock_workspace_api)
            page = await context.new_page()
            page_errors: list[str] = []
            page.on("pageerror", lambda error: page_errors.append(str(error)))
            response = await page.goto(workspace_browser_server, wait_until="domcontentloaded")
            assert response is not None and response.status == 200
            await page.evaluate(
                'window.showWorkspaceToolbarFeedback("快速交互提交需要再次确认。", "warning")'
            )
            toolbar_feedback = page.locator("#workspace-toolbar-error")
            await expect(toolbar_feedback).to_be_visible()
            await expect(toolbar_feedback).to_have_text("快速交互提交需要再次确认。")
            assert await toolbar_feedback.evaluate(
                "node => node.classList.contains('workspace-preview-toolbar-error-warning')"
            )
            assert await page.evaluate(
                """() => {
                    const originalSetTimeout = window.setTimeout;
                    const originalClearTimeout = window.clearTimeout;
                    const timers = new Map();
                    let nextTimerId = 1;
                    window.setTimeout = (callback) => {
                      const timerId = nextTimerId++;
                      timers.set(timerId, callback);
                      return timerId;
                    };
                    window.clearTimeout = (timerId) => timers.delete(timerId);
                    window.showWorkspaceToolbarFeedback("短时警告", "warning");
                    window.setWorkspaceToolbarError("持续错误");
                    const toolbar = document.getElementById("workspace-toolbar-error");
                    const persistentErrorTakesOver = toolbar?.textContent === "持续错误"
                      && timers.size === 0;
                    window.setWorkspaceToolbarError("既有持续错误");
                    window.showWorkspaceToolbarFeedback("短时警告", "warning");
                    const [cleanup] = timers.values();
                    cleanup();
                    const persistentErrorRestored = toolbar?.textContent === "既有持续错误";
                    window.setTimeout = originalSetTimeout;
                    window.clearTimeout = originalClearTimeout;
                    return persistentErrorTakesOver && persistentErrorRestored;
                }"""
            )
        finally:
            await context.close()

    assert page_errors == []


@pytest.mark.parametrize("theme", [theme.id for theme in WEB_THEMES])
async def test_collapsed_sidebar_shows_quick_sessions_in_toolbar(
    workspace_browser_server: str,
    theme: str,
) -> None:
    browser_session = session_factory()
    async with browser_session(ensure_page=False) as chrome:
        context = await chrome.browser.new_context(viewport={"width": 1280, "height": 900})
        try:
            await context.route(
                f"{workspace_browser_server}/api/**",
                _mock_workspace_api_with_quick_sessions,
            )
            page = await context.new_page()
            page_errors: list[str] = []
            page.on("pageerror", lambda error: page_errors.append(str(error)))
            await page.add_init_script(
                script=(
                    "localStorage.clear();sessionStorage.clear();"
                    f"localStorage.setItem('hub.uiStyle.v1', {json.dumps(theme)});"
                )
            )
            response = await page.goto(workspace_browser_server, wait_until="domcontentloaded")
            assert response is not None and response.status == 200
            toolbar = page.locator("#workspace-quick-session-toolbar")
            await expect(toolbar).to_be_hidden()
            await expect(toolbar.locator('[data-session-id="quick-slot"]')).to_have_count(1)
            await page.locator("#workspace-sidebar-toggle").click()
            await expect(toolbar).to_be_visible()
            await page.wait_for_timeout(220)
            placement = await toolbar.evaluate(
                """(element) => ({
                    previousClass: element.previousElementSibling?.className,
                    toolbarLeft: element.getBoundingClientRect().left,
                    navigationRight: element.previousElementSibling?.getBoundingClientRect().right,
                    gridColumns: getComputedStyle(document.querySelector('.workspace-preview-shell')).gridTemplateColumns,
                })"""
            )
            assert placement["previousClass"] == "workspace-preview-compact-nav"
            assert placement["toolbarLeft"] >= placement["navigationRight"]
            assert placement["gridColumns"].split()[0] == "0px"
            slot_button = toolbar.locator('[data-session-id="quick-slot"]')
            unassigned_button = toolbar.locator('[data-session-id="quick-unassigned"]')
            await expect(slot_button).to_have_text("S1")
            await expect(unassigned_button).to_have_text("S")
            await expect(slot_button).to_have_attribute("title", re.compile("带槽位的快速会话"))
            await expect(unassigned_button).to_have_class(re.compile("is-running"))
            await expect(toolbar.locator('[data-session-id="terminal-session"]')).to_have_count(0)
            await slot_button.click()
            await expect(page).to_have_url(re.compile(r"[?&]session=quick-slot"))
            await expect(slot_button).to_have_class(re.compile("is-current"))
            await page.set_viewport_size({"width": 390, "height": 844})
            await expect(toolbar).to_be_hidden()
        finally:
            await context.close()

    assert page_errors == []


async def test_workspace_section_switch_disposes_workstation_controller(
    workspace_browser_server: str,
) -> None:
    browser_session = session_factory()
    async with browser_session(ensure_page=False) as chrome:
        context = await chrome.browser.new_context(viewport={"width": 1280, "height": 900})
        try:
            await context.route(f"{workspace_browser_server}/api/**", _mock_workspace_api)
            page = await context.new_page()
            page_errors: list[str] = []
            page.on("pageerror", lambda error: page_errors.append(str(error)))
            response = await page.goto(workspace_browser_server, wait_until="domcontentloaded")
            assert response is not None and response.status == 200
            await expect(page.locator("#workspace-workstation-refresh")).to_be_visible()
            await page.evaluate(
                """() => {
                    const dispose = window.disposeWorkspaceWorkstation;
                    window.__workstationDisposeCalls = 0;
                    window.disposeWorkspaceWorkstation = () => {
                        window.__workstationDisposeCalls += 1;
                        dispose?.();
                    };
                }"""
            )
            await page.get_by_role("link", name="自动化").click()
            await expect(page.get_by_role("heading", name="自动化环境")).to_be_visible()
            dispose_calls = await page.evaluate("window.__workstationDisposeCalls")
        finally:
            await context.close()

    assert dispose_calls >= 1
    assert page_errors == []


@pytest.mark.parametrize(
    "viewport",
    [(360, 800), (412, 915), (1280, 900)],
    ids=["android-compact", "android-large", "desktop"],
)
async def test_appearance_theme_previews_keep_their_own_token_packages(
    workspace_browser_server: str,
    viewport: tuple[int, int],
) -> None:
    browser_session = session_factory()
    async with browser_session(ensure_page=False) as chrome:
        context = await chrome.browser.new_context(
            viewport={"width": viewport[0], "height": viewport[1]},
            reduced_motion="reduce",
        )
        try:
            page = await context.new_page()
            page_errors: list[str] = []
            page.on("pageerror", lambda error: page_errors.append(str(error)))
            response = await page.goto(
                f"{workspace_browser_server}/settings/appearance",
                wait_until="domcontentloaded",
            )
            assert response is not None and response.status == 200
            preview_colors = await page.evaluate(
                """() => Object.fromEntries([...document.querySelectorAll('[data-style-option]')].map(option => {
                    const id = option.dataset.styleOption;
                    return [id, {
                        package: getComputedStyle(option).getPropertyValue('--theme-preview-background').trim(),
                        muted: getComputedStyle(option).getPropertyValue('--theme-preview-muted').trim(),
                        surface: getComputedStyle(option.querySelector('.theme-option-preview-surface')).backgroundColor,
                        swatch: getComputedStyle(option.querySelector('.theme-option-colors i:nth-child(2)')).backgroundColor,
                    }];
                }))"""
            )
            await page.locator("#theme-studio-cyan").check(force=True)
            await expect(page.locator("html")).to_have_attribute("data-ui-style", "studio-cyan")
            await page.get_by_role("button", name="显示文字层级示例").click()
            await expect(page.locator("#theme-standard-details")).to_be_visible()
            await expect(page.locator("#theme-code-dark-details")).to_be_visible()
            await expect(page.locator("#theme-studio-cyan-details")).to_be_visible()
            overflow = await page.evaluate("document.documentElement.scrollWidth - innerWidth")
        finally:
            await context.close()

    assert preview_colors == {
        "standard": {
            "package": "#eef2ec", "muted": "#647543", "surface": "rgb(251, 252, 248)", "swatch": "rgb(100, 117, 67)",
        },
        "code-dark": {
            "package": "#1e1e1e", "muted": "#9d9d9d", "surface": "rgb(37, 37, 38)", "swatch": "rgb(157, 157, 157)",
        },
        "studio-cyan": {
            "package": "#f2f6f8", "muted": "#1f7489", "surface": "rgb(255, 255, 255)", "swatch": "rgb(31, 116, 137)",
        },
    }
    assert overflow == 0
    assert page_errors == []


async def test_appearance_theme_applies_when_browser_cannot_persist_preference(
    workspace_browser_server: str,
) -> None:
    browser_session = session_factory()
    async with browser_session(ensure_page=False) as chrome:
        context = await chrome.browser.new_context(viewport={"width": 1280, "height": 900})
        try:
            page = await context.new_page()
            page_errors: list[str] = []
            page.on("pageerror", lambda error: page_errors.append(str(error)))
            await page.add_init_script(
                script="""
                    const originalSetItem = Storage.prototype.setItem;
                    Storage.prototype.setItem = function(key, value) {
                      if (key === "hub.uiStyle.v1" || key === "hub.uiFontSize.v1") {
                        throw new DOMException("blocked", "SecurityError");
                      }
                      return originalSetItem.call(this, key, value);
                    };
                """,
            )
            response = await page.goto(
                f"{workspace_browser_server}/settings/appearance",
                wait_until="domcontentloaded",
            )
            assert response is not None and response.status == 200
            await page.locator("#theme-code-dark").check(force=True)
            await expect(page.locator("html")).to_have_attribute("data-ui-style", "code-dark")
            await expect(page.locator("#settings-message")).to_have_text(
                "当前浏览器无法保存主题偏好，已仅在本页临时应用。",
            )
            await page.locator("#font-size-small").check(force=True)
            await expect(page.locator("html")).to_have_attribute("data-ui-font-size", "small")
            await expect(page.locator("#settings-message")).to_have_text(
                "当前浏览器无法保存文字大小偏好，已仅在本页临时应用。",
            )
        finally:
            await context.close()

    assert page_errors == []


@pytest.mark.parametrize("font_size", [item.id for item in WEB_FONT_SIZES])
@pytest.mark.parametrize(
    "viewport",
    [(360, 800), (500, 900), (1280, 900)],
    ids=["android-compact", "mid-width", "desktop"],
)
async def test_appearance_font_size_is_persistent_and_keeps_layout_usable(
    workspace_browser_server: str,
    font_size: str,
    viewport: tuple[int, int],
) -> None:
    browser_session = session_factory()
    async with browser_session(ensure_page=False) as chrome:
        context = await chrome.browser.new_context(
            viewport={"width": viewport[0], "height": viewport[1]},
            reduced_motion="reduce",
        )
        try:
            page = await context.new_page()
            page_errors: list[str] = []
            page.on("pageerror", lambda error: page_errors.append(str(error)))
            response = await page.goto(
                f"{workspace_browser_server}/settings/appearance",
                wait_until="domcontentloaded",
            )
            assert response is not None and response.status == 200
            await page.locator(f"#font-size-{font_size}").check(force=True)
            await expect(page.locator("html")).to_have_attribute("data-ui-font-size", font_size)
            font_size_pixels = await page.evaluate("getComputedStyle(document.documentElement).fontSize")
            settings_title_pixels = await page.locator("#settings-title").evaluate(
                "element => getComputedStyle(element).fontSize",
            )
            overflow = await page.evaluate("document.documentElement.scrollWidth - innerWidth")
            await page.reload(wait_until="domcontentloaded")
            await expect(page.locator("html")).to_have_attribute("data-ui-font-size", font_size)
        finally:
            await context.close()

    expected_scale = next(item.scale for item in WEB_FONT_SIZES if item.id == font_size)
    assert float(font_size_pixels.removesuffix("px")) == pytest.approx(16 * expected_scale)
    assert float(settings_title_pixels.removesuffix("px")) == pytest.approx(
        16 * expected_scale * 1.35,
    )
    assert overflow <= 1
    assert page_errors == []


@pytest.mark.parametrize("theme", [item.id for item in WEB_THEMES])
@pytest.mark.parametrize("font_size", [item.id for item in WEB_FONT_SIZES])
async def test_markdown_code_blocks_keep_theme_tokens_and_narrow_screen_boundary(
    workspace_browser_server: str,
    theme: str,
    font_size: str,
) -> None:
    expected_colors = {
        "standard": {
            "background": "rgb(245, 247, 244)",
            "border": "rgb(215, 222, 215)",
            "text": "rgb(58, 80, 27)",
        },
        "code-dark": {
            "background": "rgb(30, 30, 30)",
            "border": "rgb(60, 60, 60)",
            "text": "rgb(204, 204, 204)",
        },
        "studio-cyan": {
            "background": "rgb(237, 243, 245)",
            "border": "rgb(201, 215, 221)",
            "text": "rgb(0, 84, 110)",
        },
    }
    browser_session = session_factory()
    async with browser_session(ensure_page=False) as chrome:
        context = await chrome.browser.new_context(
            viewport={"width": 360, "height": 800},
            reduced_motion="reduce",
        )
        try:
            page = await context.new_page()
            page_errors: list[str] = []
            page.on("pageerror", lambda error: page_errors.append(str(error)))
            await page.add_init_script(
                script=(
                    "localStorage.clear(); sessionStorage.clear();"
                    f"localStorage.setItem('hub.uiStyle.v1', {json.dumps(theme)});"
                    f"localStorage.setItem('hub.uiFontSize.v1', {json.dumps(font_size)});"
                ),
            )
            response = await page.goto(
                f"{workspace_browser_server}/project-docs/project-readme",
                wait_until="domcontentloaded",
            )
            assert response is not None and response.status == 200
            code_blocks = page.locator("article.markdown-body pre")
            code_block = code_blocks.filter(
                has_text=".venv/bin/python -m pytest",
            ).first
            await expect(code_block).to_be_visible()
            await expect(page.locator("html")).to_have_attribute("data-ui-style", theme)
            await expect(page.locator("html")).to_have_attribute("data-ui-font-size", font_size)
            code_block_style = await code_block.evaluate(
                """element => {
                    const code = element.querySelector("code");
                    const style = getComputedStyle(element);
                    return {
                        background: style.backgroundColor,
                        border: style.borderTopColor,
                        overflowX: style.overflowX,
                        scrollWidth: element.scrollWidth,
                        clientWidth: element.clientWidth,
                        text: getComputedStyle(code).color,
                        pageOverflow: document.documentElement.scrollWidth - innerWidth,
                    };
                }""",
            )
        finally:
            await context.close()

    assert code_block_style["background"] == expected_colors[theme]["background"]
    assert code_block_style["border"] == expected_colors[theme]["border"]
    assert code_block_style["text"] == expected_colors[theme]["text"]
    assert code_block_style["overflowX"] == "auto"
    assert code_block_style["scrollWidth"] >= code_block_style["clientWidth"]
    assert code_block_style["pageOverflow"] <= 1
    assert page_errors == []
