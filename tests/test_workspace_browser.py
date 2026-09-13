from __future__ import annotations

import asyncio
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
        "hub": {"version": "0.1.0"},
        "tailnet": {"state": "unknown", "endpoints": []},
    },
}

WORKER_RESPONSE = {
    "success": True,
    "data": {
        "state": "ready",
        "message": "Quick Worker 已就绪。",
        "worker_version": "quick-worker-11-runtime-maintenance",
        "protocol_version": 11,
        "expected_protocol_version": 11,
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
        "version": "2026.8.1",
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
            "business_modules": {
                "install_dir": root / "business-modules",
                "state_file": root / "business-modules.json",
                "deliveryline_requirements_dir": root / "deliveryline-requirements",
                "deliveryline_state_dir": root / "deliveryline-state",
            },
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
        "/api/codex/runtime-implementations": {
            "success": True,
            "data": {
                "runtime_id": "codex",
                "default_implementation_id": "builtin-dev",
                "implementations": [
                    {"implementation_id": "builtin-dev", "name": "Codex", "version": "dev"},
                    {"implementation_id": "codex-010001", "name": "Codex", "version": "1.0.1"},
                ],
            },
        },
        "/api/codex/sessions": {"success": True, "data": {"available": False, "sessions": []}},
        "/api/codex/runtimes": {
            "success": True,
            "data": {
                "basic_mode": False,
                "runtimes": [{
                    "runtime_id": "codex",
                    "name": "Codex",
                    "enabled": True,
                    "healthy": True,
                    "reason": None,
                }],
            },
        },
        "/api/ai/settings": {
            "success": True,
            "data": {
                "sections": [{
                    "id": "session-defaults",
                    "title": "会话默认配置",
                    "description": "用于之后新建的 Chub Session 和未指定专属配置的自动化任务；已有 Session 保持创建时快照。",
                    "fields": [
                        {"id": "session-default-runtime", "label": "默认 Runtime", "description": "当前可用于新建 Chub Session 的 AI Runtime。", "input_type": "select", "value": "codex", "options": [{"value": "codex", "label": "Codex", "description": "Codex Runtime", "disabled": False}]},
                        {"id": "session-default-permission", "label": "默认权限", "description": "用于未在创建时明确选择权限的新 Session。", "input_type": "select", "value": "full-access", "options": []},
                        {"id": "session-default-model", "label": "默认模型", "description": "未明确指定模型时使用；可选择跟随 Runtime 默认。", "input_type": "select", "value": "__default__", "options": []},
                        {"id": "session-default-reasoning", "label": "默认推理等级", "description": "未明确指定推理等级时使用；可选择跟随 Runtime 默认。", "input_type": "select", "value": "__default__", "options": []},
                    ],
                }],
            },
        },
        "/api/automations/environment/codex/switch-authentication": {
            "success": True,
            "data": {
                "mode": "api",
                "message": "已切换到 API Key 模式",
                "account": {
                    "state": "available",
                    "message": "API Key 已配置，AI 额度可用",
                    "quota_state": "available",
                    "five_hour_remaining_percent": 42,
                    "weekly_remaining_percent": 78,
                    "checked_at": "2026-09-10T12:00:00+08:00",
                    "login_page_available": False,
                },
            },
        },
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
            await expect(page.get_by_text("核对微信 ClawBot 适配器与 Chub 插件的本机安装元数据。")).to_be_visible()
            await expect(page.get_by_text("查看当前已登记的兼容补丁基线。")).to_be_visible()
            await expect(page.locator("#settings-openclaw-patch-list")).to_contain_text(
                "已登记",
            )
        finally:
            await context.close()

    assert requested_paths == ["/api/openclaw/integration"]
    assert page_errors == []


async def test_settings_navigation_rebinds_confirmation_dialog(
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
            response = await page.goto(
                f"{workspace_browser_server}/settings/runtime",
                wait_until="domcontentloaded",
            )
            assert response is not None and response.status == 200
            await page.get_by_role("link", name="外观").click()
            await expect(page).to_have_url(re.compile(r"/settings/appearance"))
            await page.get_by_role("link", name="会话").click()
            await expect(page).to_have_url(re.compile(r"/settings/session"))
            await expect(page.get_by_role("heading", name="会话默认配置")).to_have_count(0)
            await expect(page.get_by_text("默认 Runtime", exact=True)).to_be_visible()
            await expect(page.get_by_text("默认权限", exact=True)).to_be_visible()
            await expect(page.get_by_text("默认模型", exact=True)).to_be_visible()
            await expect(page.get_by_text("默认推理等级", exact=True)).to_be_visible()
            await page.get_by_role("link", name="插件管理").click()
            await expect(page).to_have_url(re.compile(r"/settings/runtime"))
            await page.evaluate("""() => {
                void window.showConfirmationDialog({
                  title: "移除插件",
                  description: "测试确认弹窗在设置页切换后仍绑定当前页面。",
                  onConfirm: async () => {},
                });
            }""")
            await expect(page.locator("#confirmation-dialog")).to_be_visible()
            await page.locator("#confirmation-dialog-cancel").click()
        finally:
            await context.close()

    assert page_errors == []


async def test_plugin_lifecycle_controls_visibility_and_disabled_version_selection(
    workspace_browser_server: str,
) -> None:
    browser_session = session_factory()
    async with browser_session(ensure_page=False) as chrome:
        context = await chrome.browser.new_context(viewport={"width": 1280, "height": 900})
        try:
            page = await context.new_page()
            page_errors: list[str] = []
            page.on("pageerror", lambda error: page_errors.append(str(error)))
            response = await page.goto(
                f"{workspace_browser_server}/settings/runtime",
                wait_until="domcontentloaded",
            )
            assert response is not None and response.status == 200
            await page.evaluate("""async () => {
                await fetch("/api/plugins/deliveryline/imports", {
                  method: "POST",
                  headers: { "Content-Type": "application/json" },
                  body: JSON.stringify({ artifact_id: "development:deliveryline" }),
                });
            }""")
            await page.reload(wait_until="domcontentloaded")
            await expect(page.get_by_role("link", name="Deliveryline")).to_be_visible()
            row = page.locator("#plugin-lifecycle-list .runtime-module-row").filter(
                has_text="Deliveryline",
            ).first
            await row.get_by_role("button", name="启用").click()
            await expect(row.get_by_role("button", name="禁用")).to_be_visible()
            await page.get_by_role("link", name="Deliveryline").click()
            await expect(page.locator("#deliveryline-plugin-version")).to_be_enabled()
            await page.get_by_role("link", name="插件管理").click()
            row = page.locator("#plugin-lifecycle-list .runtime-module-row").filter(
                has_text="Deliveryline",
            ).first
            await row.get_by_role("button", name="禁用").click()
            await page.get_by_role("link", name="Deliveryline").click()
            await expect(page.locator("#deliveryline-plugin-version")).to_be_disabled()
            await page.get_by_role("link", name="插件管理").click()
            row = page.locator("#plugin-lifecycle-list .runtime-module-row").filter(
                has_text="Deliveryline",
            ).first
            await row.get_by_role("button", name="移除").click()
            await page.locator("#confirmation-dialog-confirm").click()
            await expect(page).to_have_url(re.compile(r"/settings/runtime"))
            await expect(page.get_by_role("link", name="Deliveryline")).to_have_count(0)
        finally:
            await context.close()

    assert page_errors == []


async def test_deliveryline_workspace_navigation_follows_import_lifecycle(
    workspace_browser_server: str,
) -> None:
    browser_session = session_factory()
    async with browser_session(ensure_page=False) as chrome:
        context = await chrome.browser.new_context(viewport={"width": 1280, "height": 900})
        try:
            page = await context.new_page()
            page_errors: list[str] = []
            page.on("pageerror", lambda error: page_errors.append(str(error)))
            response = await page.goto(workspace_browser_server, wait_until="domcontentloaded")
            assert response is not None and response.status == 200
            await expect(page.locator('[aria-label="业务导航"]')).to_have_count(0)
            await page.evaluate("""async () => {
                await fetch("/api/plugins/deliveryline/imports", {
                  method: "POST",
                  headers: { "Content-Type": "application/json" },
                  body: JSON.stringify({ artifact_id: "development:deliveryline" }),
                });
            }""")
            await page.reload(wait_until="domcontentloaded")
            await expect(page.locator('[aria-label="业务导航"]')).to_have_count(0)
            await page.evaluate("""async () => {
                await fetch("/api/plugins/deliveryline/enabled", {
                  method: "PUT",
                  headers: { "Content-Type": "application/json" },
                  body: JSON.stringify({ artifact_id: "development:deliveryline", enabled: true }),
                });
            }""")
            await page.reload(wait_until="domcontentloaded")
            await expect(page.locator('[aria-label="业务导航"]')).to_be_visible()
            await page.locator('[aria-label="业务导航"] a').click()
            await expect(page).to_have_url(re.compile(r"\?section=deliveryline"))
            await expect(
                page.get_by_role("heading", name="Deliveryline", exact=True)
            ).to_be_visible()
            await expect(page.get_by_role("heading", name="进行中需求")).to_be_visible()
            await expect(page.get_by_text("待我处理", exact=True)).to_be_visible()
            await expect(page.get_by_text("存在风险", exact=True)).to_be_visible()
            await expect(page.get_by_text("已交付", exact=True)).to_be_visible()
            await page.locator("#deliveryline-create").click()
            await expect(
                page.get_by_text("记录希望解决的问题、机会或想法；一句话即可开始，后续再逐步补充。")
            ).to_be_visible()
            editor_layout = await page.locator("#deliveryline-editor-form").evaluate("""(form) => {
                const dialog = document.querySelector("#deliveryline-editor");
                const header = form.querySelector(".codex-workspace-dialog-header");
                const description = document.querySelector("#deliveryline-editor-description");
                const field = document.querySelector("#deliveryline-create-description");
                const formStyle = getComputedStyle(form);
                const fieldStyle = getComputedStyle(field);
                const root = document.documentElement;
                const initialStyle = root.dataset.uiStyle;
                const themes = ["standard", "code-dark", "studio-cyan"];
                const themeFields = Object.fromEntries(themes.map((theme) => {
                  root.dataset.uiStyle = theme;
                  const probe = document.createElement("span");
                  probe.style.background = "var(--color-surface-field)";
                  document.body.append(probe);
                  const expectedBackground = getComputedStyle(probe).backgroundColor;
                  probe.remove();
                  return [theme, {
                    background: getComputedStyle(field).backgroundColor,
                    expectedBackground,
                    color: getComputedStyle(field).color,
                  }];
                }));
                if (initialStyle) {
                  root.dataset.uiStyle = initialStyle;
                } else {
                  delete root.dataset.uiStyle;
                }
                return {
                  dialogWidth: dialog.getBoundingClientRect().width,
                  formDisplay: formStyle.display,
                  formGap: Number.parseFloat(formStyle.gap),
                  textareaHeight: field.getBoundingClientRect().height,
                  textareaResize: fieldStyle.resize,
                  descriptionColor: getComputedStyle(description).color,
                  descriptionFontSize: Number.parseFloat(getComputedStyle(description).fontSize),
                  textareaFontSize: Number.parseFloat(fieldStyle.fontSize),
                  descriptionAfterHeader: description.getBoundingClientRect().top - header.getBoundingClientRect().bottom,
                  fieldAfterDescription: field.getBoundingClientRect().top - description.getBoundingClientRect().bottom,
                  themeFields,
                };
            }""")
            assert editor_layout["dialogWidth"] > 400
            assert editor_layout["formDisplay"] == "grid"
            assert editor_layout["formGap"] >= 16
            assert editor_layout["textareaHeight"] >= 150
            assert editor_layout["textareaResize"] == "vertical"
            assert editor_layout["descriptionColor"] != "rgba(0, 0, 0, 0)"
            assert editor_layout["textareaFontSize"] <= editor_layout["descriptionFontSize"] + 1
            assert editor_layout["descriptionAfterHeader"] >= 8
            assert editor_layout["fieldAfterDescription"] >= 12
            for theme_field in editor_layout["themeFields"].values():
                assert theme_field["background"] == theme_field["expectedBackground"]
                assert theme_field["color"] != "rgba(0, 0, 0, 0)"
            await page.locator("#deliveryline-create-description").fill("希望能够管理需求交付。")
            await page.locator("#deliveryline-editor-submit").click()
            await expect(page.get_by_text("希望能够管理需求交付。", exact=True)).to_be_visible()
            await expect(page.get_by_role("heading", name="评审准备度")).to_be_visible()
            await expect(page.get_by_role("heading", name="活动记录")).to_be_visible()
            await page.get_by_role("button", name="归档", exact=True).click()
            await page.locator("#confirmation-dialog-confirm").click()
            await expect(page.get_by_role("heading", name="已归档需求")).to_be_visible()
            await page.get_by_text("希望能够管理需求交付。", exact=True).last.click()
            await expect(page.locator("#deliveryline-detail [data-deliveryline-archive]")).to_have_count(0)
            section_gaps = await page.evaluate("""() => {
                const gapAfter = (header, content) => {
                  const headerRect = document.querySelector(header).getBoundingClientRect();
                  const contentRect = document.querySelector(content).getBoundingClientRect();
                  return contentRect.top - headerRect.bottom;
                };
                return {
                  queue: gapAfter(
                    '.deliveryline-workbench-section > .deliveryline-preview-section-heading',
                    '.deliveryline-workbench-list',
                  ),
                  detail: gapAfter(
                    '.deliveryline-detail-preview > .deliveryline-preview-section-heading',
                    '.deliveryline-detail-preview-content',
                  ),
                };
            }""")
            assert section_gaps["queue"] >= 12
            assert section_gaps["detail"] >= 12
            await page.locator("#workspace-sidebar-toggle").click()
            await expect(
                page.locator('.workspace-preview-compact-nav a[aria-label="Deliveryline"]')
            ).to_be_visible()
            await page.set_viewport_size({"width": 390, "height": 844})
            await expect(
                page.locator('.workspace-preview-compact-nav a[aria-label="Deliveryline"]')
            ).to_be_visible()
            await page.evaluate("""async () => {
                await fetch("/api/plugins/deliveryline/imports/development%3Adeliveryline", {
                  method: "DELETE",
                });
            }""")
            await page.goto(workspace_browser_server, wait_until="domcontentloaded")
            await expect(page.locator('[aria-label="业务导航"]')).to_have_count(0)
            await expect(
                page.locator('.workspace-preview-compact-nav a[aria-label="Deliveryline"]')
            ).to_have_count(0)
        finally:
            await context.close()

    assert page_errors == []


async def test_plugin_removal_failure_keeps_the_confirmation_dialog_open(
    workspace_browser_server: str,
) -> None:
    browser_session = session_factory()
    async with browser_session(ensure_page=False) as chrome:
        context = await chrome.browser.new_context(viewport={"width": 1280, "height": 900})
        try:
            page = await context.new_page()
            page_errors: list[str] = []
            page.on("pageerror", lambda error: page_errors.append(str(error)))
            response = await page.goto(
                f"{workspace_browser_server}/settings/runtime",
                wait_until="domcontentloaded",
            )
            assert response is not None and response.status == 200
            await page.evaluate("""async () => {
                await fetch("/api/plugins/deliveryline/imports", {
                  method: "POST",
                  headers: { "Content-Type": "application/json" },
                  body: JSON.stringify({ artifact_id: "development:deliveryline" }),
                });
            }""")
            await page.reload(wait_until="domcontentloaded")

            async def fail_removal(route) -> None:
                if route.request.method == "DELETE":
                    await route.fulfill(
                        status=409,
                        content_type="application/json",
                        body=json.dumps({
                            "success": False,
                            "error": {"code": "plugin_busy", "message": "插件当前不能移除。"},
                        }),
                    )
                    return
                await route.continue_()

            await context.route(
                f"{workspace_browser_server}/api/plugins/deliveryline/imports/**",
                fail_removal,
            )
            row = page.locator("#plugin-lifecycle-list .runtime-module-row").filter(
                has_text="Deliveryline",
            ).first
            await row.get_by_role("button", name="移除").click()
            await page.locator("#confirmation-dialog-confirm").click()
            await expect(page.locator("#confirmation-dialog")).to_be_visible()
            await expect(page.locator("#confirmation-dialog-message")).to_have_text("插件当前不能移除。")
            await page.locator("#confirmation-dialog-cancel").click()
            await context.unroute(f"{workspace_browser_server}/api/plugins/deliveryline/imports/**")
            await page.evaluate("""async () => {
                await fetch("/api/plugins/deliveryline/imports/development%3Adeliveryline", { method: "DELETE" });
            }""")
        finally:
            await context.close()

    assert page_errors == []


@pytest.mark.parametrize(
    ("plugin_id", "artifact_id", "navigation_name", "version_selector"),
    [
        ("codex-runtime", "development:codex-runtime", "Codex", "#codex-default-runtime-implementation"),
        ("weixin-orchestration", "development:weixin-orchestration", "微信任务润色", "#workspace-task-implementation-trigger"),
    ],
)
async def test_plugin_lifecycle_keeps_codex_and_weixin_navigation_and_versions_in_sync(
    workspace_browser_server: str,
    plugin_id: str,
    artifact_id: str,
    navigation_name: str,
    version_selector: str,
) -> None:
    browser_session = session_factory()
    async with browser_session(ensure_page=False) as chrome:
        context = await chrome.browser.new_context(viewport={"width": 1280, "height": 900})
        try:
            page = await context.new_page()
            page_errors: list[str] = []
            page.on("pageerror", lambda error: page_errors.append(str(error)))
            await page.goto(f"{workspace_browser_server}/settings/runtime", wait_until="domcontentloaded")
            await page.evaluate(
                """async ({ pluginId, artifactId }) => {
                    await fetch(`/api/plugins/${pluginId}/imports`, {
                      method: "POST",
                      headers: { "Content-Type": "application/json" },
                      body: JSON.stringify({ artifact_id: artifactId }),
                    });
                }""",
                {"pluginId": plugin_id, "artifactId": artifact_id},
            )
            await page.reload(wait_until="domcontentloaded")
            await expect(page.get_by_role("link", name=navigation_name)).to_be_visible()
            row = page.locator("#plugin-lifecycle-list .runtime-module-row").filter(
                has_text=navigation_name,
            ).first
            await row.get_by_role("button", name="启用").click()
            await page.get_by_role("link", name=navigation_name).click()
            await expect(page.locator(version_selector)).to_be_enabled()
            await page.get_by_role("link", name="插件管理").click()
            row = page.locator("#plugin-lifecycle-list .runtime-module-row").filter(
                has_text=navigation_name,
            ).first
            await row.get_by_role("button", name="禁用").click()
            await page.get_by_role("link", name=navigation_name).click()
            await expect(page.locator(version_selector)).to_be_disabled()
            await page.get_by_role("link", name="插件管理").click()
            await page.evaluate(
                """async ({ pluginId, artifactId }) => {
                    await fetch(`/api/plugins/${pluginId}/imports/${encodeURIComponent(artifactId)}`, {
                      method: "DELETE",
                    });
                }""",
                {"pluginId": plugin_id, "artifactId": artifact_id},
            )
            await page.reload(wait_until="domcontentloaded")
            await expect(page.get_by_role("link", name=navigation_name)).to_have_count(0)
        finally:
            await context.close()

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
            "runtime_registered": True,
            "quick_creation": {"available": True},
            "workspaces": [
                {"id": "home", "name": "用户目录", "available": True},
                {"id": "workspace", "name": "Workspace", "available": True},
                {"id": "chub", "name": "Chub", "available": True},
            ],
            "sessions": [
                {
                    "id": "quick-slot",
                    "title": "带槽位的Chub Session",
                    "created_at": "2026-08-15T09:00:00Z",
                                        "status": "stopped",
                    "activity": "idle",
                    "quick_interaction_running": False,
                    "weixin_session_slot": 1,
                },
                {
                    "id": "quick-unassigned",
                    "title": "未分配槽位的Chub Session",
                    "created_at": "2026-08-15T08:00:00Z",
                                        "status": "stopped",
                    "activity": "idle",
                    "quick_interaction_running": True,
                    "weixin_session_slot": None,
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
                "runtime_id": "codex",
                "model": "gpt-test",
                "reasoning_effort": "medium",
                "queued": 0,
                "running": 0,
                "weixin_chub_mode_enabled": True,
            },
        },
        "/api/plugins": {
            "success": True,
            "data": {
                "plugins": [{
                    "plugin_id": "weixin-orchestration",
                    "enabled_artifact_ids": ["development:weixin-orchestration"],
                    "artifacts": [
                        {
                            "artifact_id": "development:weixin-orchestration",
                            "available": True,
                            "version": "dev",
                        },
                        {
                            "artifact_id": "orchestration:weixin-refinement@test",
                            "available": True,
                            "version": "test",
                        },
                    ],
                }],
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
async def test_task_orchestration_execution_settings_render_on_supported_viewports(
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
            response = await page.goto(
                f"{workspace_browser_server}/settings/task-orchestration",
                wait_until="domcontentloaded",
            )
            assert response is not None and response.status == 200
            await expect(
                page.get_by_role("region", name="微信任务润色"),
            ).to_be_visible()
            order = await page.locator(".workspace-task-orchestration-list").evaluate(
                """(list) => Array.from(list.querySelectorAll('.workspace-task-orchestration-field')).map((row) => (
                    row.querySelector('input')?.id || row.querySelector('button')?.id || ''
                ))""",
            )
            assert order.index("workspace-task-runtime-trigger") < order.index(
                "workspace-task-model-trigger",
            ) < order.index("workspace-task-reasoning-trigger")
            await expect(page.locator("#workspace-task-runtime-trigger")).to_have_text("Codex")
            await expect(page.locator('[aria-label="翻译权限：Read Only"]')).to_have_count(1)
            await expect(page.locator("#workspace-task-processing-value")).to_have_text(
                "自动润色后执行",
            )
            await expect(page.locator("#workspace-task-implementation-value")).to_have_text(
                "微信任务润色 · 开发实现",
            )
            await page.locator("#workspace-task-implementation-trigger").click()
            await expect(page.locator("#workspace-task-implementation-menu")).to_contain_text(
                "微信任务润色 · 正式版 vtest",
            )
            await page.keyboard.press("Escape")
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
                re.compile("GPT Test"),
            )
            await expect(page.locator("#workspace-task-reasoning-trigger")).to_have_text("Medium")
            await page.locator("#workspace-task-model-trigger").click()
            await expect(page.locator("#workspace-task-model-menu")).to_contain_text(
                "用于微信任务润色的测试模型。",
            )
            await assert_menu_is_anchored(
                "#workspace-task-model-menu",
                "workspace-task-model-trigger",
            )
            await page.keyboard.press("Escape")
            await page.locator(".workspace-task-orchestration-panel").scroll_into_view_if_needed()
            bounds = await page.locator(".workspace-task-orchestration-panel").evaluate(
                """(element) => {
                    const rect = element.getBoundingClientRect();
                    return { left: rect.left, right: rect.right, top: rect.top, bottom: rect.bottom };
                }""",
            )
            assert bounds["left"] >= 0
            assert bounds["right"] <= viewport[0]
            assert await page.evaluate("document.documentElement.scrollWidth - innerWidth") == 0
        finally:
            await context.close()

    assert page_errors == []


async def test_task_orchestration_execution_settings_persist_across_page_reload(
    workspace_browser_server: str,
) -> None:
    state = {
        "runtime_id": "codex",
        "model": "gpt-test",
        "reasoning_effort": "medium",
        "artifact_id": "development:weixin-orchestration",
    }
    execution_saved = asyncio.Event()
    implementation_saved = asyncio.Event()

    def translation_response() -> dict[str, object]:
        return {
            "success": True,
            "data": {
                "mode": "auto",
                "enabled": True,
                "runtime_id": state["runtime_id"],
                "model": state["model"],
                "reasoning_effort": state["reasoning_effort"],
                "queued": 0,
                "running": 0,
                "weixin_chub_mode_enabled": True,
            },
        }

    async def route_workspace_api(route) -> None:
        path = urlsplit(route.request.url).path
        if path == "/api/settings/weixin-translation":
            if route.request.method == "PUT":
                update = json.loads(route.request.post_data or "{}")
                assert update == {
                    "runtime_id": "codex",
                    "model": "gpt-next",
                    "reasoning_effort": "high",
                }
                state.update(update)
                execution_saved.set()
            await route.fulfill(
                status=200,
                content_type="application/json",
                body=json.dumps(translation_response()),
            )
            return
        if path == "/api/plugins":
            artifact_id = state["artifact_id"]
            await route.fulfill(
                status=200,
                content_type="application/json",
                body=json.dumps({
                    "success": True,
                    "data": {
                        "plugins": [{
                            "plugin_id": "weixin-orchestration",
                            "enabled_artifact_ids": [artifact_id],
                            "artifacts": [
                                {
                                    "artifact_id": "development:weixin-orchestration",
                                    "available": True,
                                    "version": "dev",
                                },
                                {
                                    "artifact_id": "orchestration:weixin-refinement@test",
                                    "available": True,
                                    "version": "test",
                                },
                            ],
                        }],
                    },
                }),
            )
            return
        if path == "/api/plugins/weixin-orchestration/enabled":
            update = json.loads(route.request.post_data or "{}")
            assert update == {
                "artifact_id": "orchestration:weixin-refinement@test",
                "enabled": True,
            }
            state["artifact_id"] = update["artifact_id"]
            implementation_saved.set()
            await route.fulfill(
                status=200,
                content_type="application/json",
                body=json.dumps({"success": True, "data": {}}),
            )
            return
        if path == "/api/codex/models":
            await route.fulfill(
                status=200,
                content_type="application/json",
                body=json.dumps({
                    "success": True,
                    "data": {
                        "default_model": "gpt-test",
                        "default_reasoning_effort": "medium",
                        "models": [
                            {
                                "id": "gpt-test",
                                "name": "GPT Test",
                                "description": "用于微信任务润色的测试模型。",
                                "default_level": "medium",
                                "levels": [{"id": "medium", "description": "平衡速度与质量。"}],
                            },
                            {
                                "id": "gpt-next",
                                "name": "GPT Next",
                                "description": "用于验证专属设置保存。",
                                "default_level": "high",
                                "levels": [{"id": "high", "description": "适合复杂润色。"}],
                            },
                        ],
                    },
                }),
            )
            return
        await _mock_workspace_api(route)

    browser_session = session_factory()
    async with browser_session(ensure_page=False) as chrome:
        context = await chrome.browser.new_context(
            viewport={"width": 1280, "height": 900},
            reduced_motion="reduce",
        )
        try:
            await context.route(f"{workspace_browser_server}/api/**", route_workspace_api)
            page = await context.new_page()
            page_errors: list[str] = []
            page.on("pageerror", lambda error: page_errors.append(str(error)))
            response = await page.goto(
                f"{workspace_browser_server}/settings/task-orchestration",
                wait_until="domcontentloaded",
            )
            assert response is not None and response.status == 200

            await page.locator("#workspace-task-model-trigger").click()
            await page.get_by_role("option", name=re.compile("GPT Next")).click()
            await asyncio.wait_for(execution_saved.wait(), timeout=1)
            await page.locator("#workspace-task-implementation-trigger").click()
            await page.get_by_role("option", name=re.compile("微信任务润色 · 正式版 vtest")).click()
            await asyncio.wait_for(implementation_saved.wait(), timeout=1)

            await page.reload(wait_until="domcontentloaded")
            await expect(page.locator("#workspace-task-model-value")).to_have_text("GPT Next")
            await expect(page.locator("#workspace-task-reasoning-trigger")).to_have_text("High")
            await expect(page.locator("#workspace-task-implementation-value")).to_have_text(
                "微信任务润色 · 正式版 vtest",
            )
        finally:
            await context.close()

    assert page_errors == []


async def test_codex_default_runtime_selection_persists_the_selected_implementation(
    workspace_browser_server: str,
) -> None:
    browser_session = session_factory()
    selected_implementation = "builtin-dev"
    updates: list[str] = []

    def implementations_response() -> dict[str, object]:
        return {
            "success": True,
            "data": {
                "default_implementation_id": selected_implementation,
                "implementations": [
                    {
                        "implementation_id": "builtin-dev",
                        "name": "Codex",
                        "version": "dev",
                        "description": "开发实现。",
                        "enabled": True,
                        "healthy": True,
                        "is_default": selected_implementation == "builtin-dev",
                        "compatibility_id": "codex-v1",
                        "removable": False,
                        "reason": None,
                    },
                    {
                        "implementation_id": "codex-010001",
                        "name": "Codex",
                        "version": "1.0.1",
                        "description": "正式版本。",
                        "enabled": True,
                        "healthy": True,
                        "is_default": selected_implementation == "codex-010001",
                        "compatibility_id": "codex-v1",
                        "removable": True,
                        "reason": None,
                    },
                ],
            },
        }

    async def route_runtime_selection(route) -> None:
        nonlocal selected_implementation
        request = route.request
        path = urlsplit(request.url).path
        if path == "/api/codex/runtime-implementations/default":
            if request.method == "PUT":
                payload = json.loads(request.post_data or "{}")
                selected_implementation = payload["implementation_id"]
                updates.append(selected_implementation)
            await route.fulfill(
                status=200,
                content_type="application/json",
                body=json.dumps(implementations_response()),
            )
            return
        if path == "/api/codex/runtime-implementations":
            await route.fulfill(
                status=200,
                content_type="application/json",
                body=json.dumps(implementations_response()),
            )
            return
        if path == "/api/runtime-modules":
            await route.fulfill(
                status=200,
                content_type="application/json",
                body=json.dumps({"success": True, "data": {"modules": []}}),
            )
            return
        if path == "/api/runtime-modules/builtin-dev/refresh-availability":
            await route.fulfill(
                status=200,
                content_type="application/json",
                body=json.dumps({"success": True, "data": {"available": True, "reason": None}}),
            )
            return
        await _mock_workspace_api(route)

    async with browser_session(ensure_page=False) as chrome:
        context = await chrome.browser.new_context(
            viewport={"width": 1280, "height": 900},
            reduced_motion="reduce",
        )
        try:
            await context.route(
                f"{workspace_browser_server}/api/**",
                route_runtime_selection,
            )
            page = await context.new_page()
            page_errors: list[str] = []
            page.on("pageerror", lambda error: page_errors.append(str(error)))
            response = await page.goto(
                f"{workspace_browser_server}/settings/runtime/codex",
                wait_until="domcontentloaded",
            )
            assert response is not None and response.status == 200
            await expect(page.locator("#codex-runtime-version-list")).to_have_count(0)
            await expect(page.locator("#codex-builtin-runtime-refresh")).to_have_count(0)
            select = page.locator("#codex-default-runtime-implementation")
            await expect(select).to_have_value("builtin-dev")
            await page.locator(".settings-choice-picker-trigger").click()
            await page.locator(
                ".settings-choice-picker-menu [role='option']",
            ).filter(has_text="Codex · 正式版 v1.0.1").click()
            await expect(select).to_have_value("codex-010001")
            await page.reload(wait_until="domcontentloaded")
            await expect(select).to_have_value("codex-010001")
        finally:
            await context.close()

    assert updates == ["codex-010001"]
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
            await expect(page.locator("#workspace-chub-detail")).to_have_text(
                "Chub v0.1.0 · Linux test · Python 3.12"
            )
            await expect(page.locator("#workspace-worker-detail")).to_have_text(
                "Worker v11 · 协议 v11 · Quick Worker 已就绪。"
            )
            await expect(page.locator("#workspace-openclaw-detail")).to_have_text(
                "OpenClaw / Gateway v2026.8.1 · Gateway 运行正常并已通过连接探测。"
            )
            await expect(page.locator("#workspace-openclaw-weixin-detail")).to_have_text(
                "微信 ClawBot v2.4.8 · 微信通道已连接。"
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
            await expect(slot_button).to_have_attribute("title", re.compile("带槽位的Chub Session"))
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


async def test_workspace_new_session_dialog_focuses_create_button(
    workspace_browser_server: str,
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
            response = await page.goto(workspace_browser_server, wait_until="domcontentloaded")
            assert response is not None and response.status == 200
            create_button = page.locator("#workspace-session-create")
            await expect(create_button).to_be_enabled()
            await create_button.click()
            await expect(page.locator("#workspace-session-create-dialog")).to_be_visible()
            await expect(page.locator("#workspace-session-workspace")).to_have_value("chub")
            await expect(page.locator("#workspace-session-create-confirm")).to_be_focused()
            dialog_layout = await page.locator("#workspace-session-create-form").evaluate("""(form) => {
                const dialog = document.querySelector("#workspace-session-create-dialog");
                const header = form.querySelector(".codex-workspace-dialog-header");
                const description = form.querySelector(".workspace-session-create-description");
                const picker = form.querySelector(".workspace-session-choice-picker");
                const formStyle = getComputedStyle(form);
                return {
                  dialogWidth: dialog.getBoundingClientRect().width,
                  formDisplay: formStyle.display,
                  formGap: Number.parseFloat(formStyle.gap),
                  descriptionAfterHeader: description.getBoundingClientRect().top - header.getBoundingClientRect().bottom,
                  pickerAfterDescription: picker.getBoundingClientRect().top - description.getBoundingClientRect().bottom,
                };
            }""")
            assert dialog_layout["dialogWidth"] >= 560
            assert dialog_layout["formDisplay"] == "grid"
            assert dialog_layout["formGap"] >= 16
            assert dialog_layout["descriptionAfterHeader"] >= 8
            assert dialog_layout["pickerAfterDescription"] >= 12
            await page.keyboard.press("Escape")
            await expect(page.locator("#workspace-session-create-dialog")).not_to_be_visible()
            await expect(create_button).to_be_focused()
        finally:
            await context.close()

    assert page_errors == []


async def test_workspace_worker_restart_recovers_from_a_failed_refresh(
    workspace_browser_server: str,
) -> None:
    browser_session = session_factory()
    worker_reads = 0

    async def route_workspace_api(route) -> None:
        nonlocal worker_reads
        if urlsplit(route.request.url).path == "/api/maintenance/quick-worker":
            worker_reads += 1
            if worker_reads > 1:
                await route.fulfill(
                    status=503,
                    content_type="application/json",
                    body=json.dumps({
                        "success": False,
                        "error": {"code": "worker_unavailable", "message": "Worker status unavailable."},
                    }),
                )
                return
        await _mock_workspace_api(route)

    async with browser_session(ensure_page=False) as chrome:
        context = await chrome.browser.new_context(viewport={"width": 1280, "height": 900})
        try:
            await context.route(f"{workspace_browser_server}/api/**", route_workspace_api)
            page = await context.new_page()
            page_errors: list[str] = []
            page.on("pageerror", lambda error: page_errors.append(str(error)))
            response = await page.goto(workspace_browser_server, wait_until="domcontentloaded")
            assert response is not None and response.status == 200
            worker_restart = page.locator("#workspace-worker-restart")
            await expect(worker_restart).to_be_enabled()
            await page.locator("#workspace-workstation-refresh").click()
            await expect(page.locator("#workspace-workstation-refresh")).to_be_enabled()
            await expect(worker_restart).to_be_enabled()
        finally:
            await context.close()

    assert worker_reads >= 2
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
            await page.locator("#workspace-automation-codex-account-detail").evaluate(
                "(element) => { element.dataset.authMode = 'api'; }",
            )
            await page.get_by_role("button", name="切换", exact=True).click()
            await expect(page.get_by_role("heading", name="切换 Codex Runtime 账户")).to_be_visible()
            await expect(page.get_by_role("button", name="切换到ChatGPT 账户模式", exact=True)).to_be_focused()
            await page.get_by_role("button", name="切换到ChatGPT 账户模式", exact=True).click()
            await expect(page.get_by_role("heading", name="切换 Codex Runtime 账户")).to_be_visible()
            feedback_font_size = await page.locator(
                "#workspace-automation-codex-account-switch-feedback",
            ).evaluate(
                "(element) => parseFloat(getComputedStyle(element).fontSize)",
            )
            body_font_size = await page.locator("body").evaluate(
                "(element) => parseFloat(getComputedStyle(element).fontSize)",
            )
            assert feedback_font_size < body_font_size
            await expect(page.locator("#workspace-automation-codex-account-detail")).to_contain_text(
                "API Key 已配置，AI 额度可用",
            )
            await expect(page.locator("#workspace-automation-codex-account-detail")).to_contain_text(
                "5h 42% · Weekly 78% · 检查于 09-10 12:00",
            )
            await page.get_by_role("button", name="完成", exact=True).click()
            assert await page.locator(".workspace-automations").evaluate(
                "(element) => getComputedStyle(element).borderTopStyle",
            ) == "none"
            assert await page.locator(".workspace-automation-details").evaluate(
                "(element) => getComputedStyle(element).borderTopStyle",
            ) == "none"
            assert await page.locator(
                ".workspace-automations > .workspace-preview-work-section",
            ).evaluate_all(
                "(elements) => elements.every((element) => getComputedStyle(element).paddingTop === '0px')",
            )
            for selector in [
                ".automation-environment > .workstation-status-list",
                ".automation-account-environment > .workstation-status-list",
            ]:
                assert await page.locator(selector).evaluate(
                    "(element) => getComputedStyle(element).borderTopStyle",
                ) == "solid"
            for selector in [
                ".automation-account-environment",
                ".workspace-automation-details > .workstation-group:last-child",
            ]:
                assert await page.locator(selector).evaluate(
                    "(element) => getComputedStyle(element, '::before').display",
                ) == "none"
            dispose_calls = await page.evaluate("window.__workstationDisposeCalls")
        finally:
            await context.close()

    assert dispose_calls >= 1
    assert page_errors == []


async def test_workspace_codex_account_switch_waits_for_account_check(
    workspace_browser_server: str,
) -> None:
    check_started = asyncio.Event()
    release_check = asyncio.Event()

    async def route_workspace_api(route) -> None:
        path = urlsplit(route.request.url).path
        if path != "/api/automations/environment/codex/check":
            await _mock_workspace_api(route)
            return
        check_started.set()
        await release_check.wait()
        await route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps({
                "success": True,
                "data": {
                    "state": "available",
                    "auth_mode": "api",
                    "message": "API Key 模式已启用",
                    "quota_state": "available",
                    "login_page_available": False,
                },
            }),
        )

    browser_session = session_factory()
    async with browser_session(ensure_page=False) as chrome:
        context = await chrome.browser.new_context(viewport={"width": 1280, "height": 900})
        try:
            await context.route(f"{workspace_browser_server}/api/**", route_workspace_api)
            page = await context.new_page()
            page_errors: list[str] = []
            page.on("pageerror", lambda error: page_errors.append(str(error)))
            response = await page.goto(workspace_browser_server, wait_until="domcontentloaded")
            assert response is not None and response.status == 200
            await page.get_by_role("link", name="自动化").click()
            switch = page.locator("#workspace-automation-codex-account-switch")
            await check_started.wait()
            await expect(switch).to_be_disabled()
            await expect(switch).to_have_text("检查中…")
            await expect(switch).to_have_attribute("title", "正在检查 Codex Runtime 账户状态")
            await expect(page.locator("#workspace-automation-codex-account-switch-dialog")).to_be_hidden()
            release_check.set()
            await expect(switch).to_be_enabled()
            await expect(switch).to_have_text("切换")
        finally:
            release_check.set()
            await context.close()

    assert page_errors == []


async def test_workspace_codex_auth_switch_can_be_stopped(
    workspace_browser_server: str,
) -> None:
    browser_session = session_factory()
    switch_started = asyncio.Event()
    release_switch = asyncio.Event()
    requested_paths: list[str] = []

    async def route_workspace_api(route) -> None:
        path = urlsplit(route.request.url).path
        if path == "/api/automations/environment/codex/switch-authentication":
            requested_paths.append(path)
            switch_started.set()
            await release_switch.wait()
            await route.fulfill(
                status=409,
                content_type="application/json",
                body=json.dumps({
                    "success": False,
                    "error": {
                        "code": "codex_auth_switch_cancelled",
                        "message": "Codex 认证切换已停止",
                    },
                }),
            )
            return
        if path == "/api/automations/environment/codex/switch-authentication/stop":
            requested_paths.append(path)
            release_switch.set()
            await route.fulfill(
                status=200,
                content_type="application/json",
                body=json.dumps({
                    "success": True,
                    "data": {
                        "state": "checking",
                        "message": "正在停止 Codex Runtime 认证切换",
                        "switching": True,
                    },
                }),
            )
            return
        await _mock_workspace_api(route)

    async with browser_session(ensure_page=False) as chrome:
        context = await chrome.browser.new_context(viewport={"width": 1280, "height": 900})
        try:
            await context.route(f"{workspace_browser_server}/api/**", route_workspace_api)
            page = await context.new_page()
            page_errors: list[str] = []
            page.on("pageerror", lambda error: page_errors.append(str(error)))
            response = await page.goto(
                f"{workspace_browser_server}/?section=automations",
                wait_until="domcontentloaded",
            )
            assert response is not None and response.status == 200
            await page.locator("#workspace-automation-codex-account-detail").evaluate(
                "(element) => { element.dataset.authMode = 'api'; }",
            )
            await page.get_by_role("button", name="切换", exact=True).click()
            await page.get_by_role("button", name="切换到ChatGPT 账户模式", exact=True).click()
            await switch_started.wait()
            stop = page.locator("#workspace-automation-codex-account-switch-stop")
            await expect(stop).to_be_visible()
            await stop.click()
            await expect(
                page.locator("#workspace-automation-codex-account-switch-feedback"),
            ).to_contain_text("Codex 认证切换已停止")
        finally:
            await context.close()

    assert requested_paths == [
        "/api/automations/environment/codex/switch-authentication",
        "/api/automations/environment/codex/switch-authentication/stop",
    ]
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
            theme_heading_layout = await page.evaluate(
                """() => {
                    const heading = document.querySelector('.theme-settings-heading');
                    const copy = heading.querySelector('div');
                    const button = heading.querySelector('button');
                    return {
                        centerOffset: Math.abs(
                            (button.getBoundingClientRect().top + button.getBoundingClientRect().height / 2)
                            - (copy.getBoundingClientRect().top + copy.getBoundingClientRect().height / 2),
                        ),
                        buttonRight: Math.abs(
                            heading.getBoundingClientRect().right - button.getBoundingClientRect().right,
                        ),
                    };
                }""",
            )
            theme_groups = await page.evaluate(
                """() => Object.fromEntries([...document.querySelectorAll('.theme-option-group')].map(group => [
                    group.querySelector('h4')?.textContent.trim(),
                    [...group.querySelectorAll('[data-style-option]')].map(option => option.dataset.styleOption),
                ]))""",
            )
            theme_group_divider = await page.evaluate(
                """() => getComputedStyle(document.querySelector('.theme-option-group + .theme-option-group')).borderTopWidth""",
            )
            font_size_section_gap = await page.evaluate(
                """() => {
                    const themes = document.querySelector('#theme-option-grid');
                    const title = document.querySelector('#font-size-settings-title');
                    return title.getBoundingClientRect().top - themes.getBoundingClientRect().bottom;
                }""",
            )
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
    assert theme_groups == {
        "亮色系主题": ["standard", "studio-cyan"],
        "暗色系主题": ["code-dark"],
    }
    assert theme_group_divider == "0px"
    assert theme_heading_layout["centerOffset"] <= 0.5
    assert theme_heading_layout["buttonRight"] <= 0.5
    assert font_size_section_gap >= 20
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
