import asyncio
from datetime import datetime, timezone
import re
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import httpx
import pytest

from app.automations.models import (
    AutomationListData,
    AutomationState,
    AutomationTaskPublic,
    BrowserProfilePublic,
    FeishuEnvironmentState,
    RuntimeAccountEnvironmentState,
)
from app.ai_runtime import BuiltinRuntimeModuleRegistry, RuntimeDescriptor
from app.application import create_app
from app.codex.models import RuntimeManagementData, RuntimeManagementItem
from app.core.config import Settings
import app.services.weekly_reports as weekly_report_service
import app.web.routes as web_routes
from app.web.themes import WEB_FONT_SIZES, WEB_THEMES


def _theme_hex_color(tokens: str, theme_id: str, suffix: str) -> str:
    match = re.search(
        rf"^\s*--{re.escape(theme_id)}-{re.escape(suffix)}:\s*(#[0-9a-fA-F]{{6}});",
        tokens,
        flags=re.MULTILINE,
    )
    assert match is not None
    return match.group(1)


def _contrast_ratio(foreground: str, background: str) -> float:
    def relative_luminance(value: str) -> float:
        channels = [int(value[index:index + 2], 16) / 255 for index in (1, 3, 5)]
        linear = [
            channel / 12.92 if channel <= 0.04045 else ((channel + 0.055) / 1.055) ** 2.4
            for channel in channels
        ]
        return sum(weight * channel for weight, channel in zip((0.2126, 0.7152, 0.0722), linear))

    lighter, darker = sorted(
        (relative_luminance(foreground), relative_luminance(background)),
        reverse=True,
    )
    return (lighter + 0.05) / (darker + 0.05)


@pytest.fixture
def weekly_reports_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Path:
    root = tmp_path / "weekly-reports"
    period = "2026-08-03至2026-08-09"
    output = root / period / "output"
    output.mkdir(parents=True)
    (output / f"本期工作重点确认清单-{period}.md").write_text(
        "# 本期工作重点确认清单\n\n## 重点\n\n- 版本发布",
        encoding="utf-8",
    )
    monkeypatch.setattr(weekly_report_service, "WEEKLY_REPORTS_ROOT", root)
    monkeypatch.setattr(
        weekly_report_service,
        "_today",
        lambda: weekly_report_service.date(2026, 8, 5),
    )
    return root




@pytest.mark.anyio
async def test_removed_cyber_style_falls_back_to_standard_before_assets_load(settings: Settings) -> None:
    transport = httpx.ASGITransport(app=create_app(settings))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        client.cookies.set("hub_ui_style", "cyber")
        pages = await asyncio.gather(
            client.get("/"),
            client.get("/settings/runtime"),
                client.get("/logs"),
                client.get("/project-docs"),
        )

    assert all(page.status_code == 200 for page in pages)
    for page in pages:
        assert '<html lang="zh-CN" data-ui-style="standard"' in page.text
        assert '<meta name="color-scheme" content="light">' in page.text


@pytest.mark.anyio
async def test_invalid_font_size_falls_back_to_default_before_assets_load(
    settings: Settings,
) -> None:
    transport = httpx.ASGITransport(app=create_app(settings))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        client.cookies.set("hub_ui_font_size", "extra-large")
        pages = await asyncio.gather(
            client.get("/"),
            client.get("/settings/appearance"),
            client.get("/logs"),
            client.get("/project-docs"),
        )

    assert all(page.status_code == 200 for page in pages)
    for page in pages:
        assert 'data-ui-font-size="default"' in page.text
        assert 'data-ui-font-size-default="default"' in page.text
        assert 'data-ui-font-size-scales="small:0.9,default:1,large:1.1"' in page.text


@pytest.mark.anyio
async def test_registered_font_size_is_applied_before_assets_load(settings: Settings) -> None:
    transport = httpx.ASGITransport(app=create_app(settings))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        client.cookies.set("hub_ui_font_size", "large")
        pages = await asyncio.gather(
            client.get("/"),
            client.get("/settings/appearance"),
            client.get("/logs"),
            client.get("/project-docs"),
        )

    assert all(page.status_code == 200 for page in pages)
    for page in pages:
        assert 'data-ui-font-size="large"' in page.text
        assert 'data-ui-font-size-default="default"' in page.text
        assert 'data-ui-font-size-scales="small:0.9,default:1,large:1.1"' in page.text


@pytest.mark.anyio
async def test_registered_theme_is_applied_before_assets_load(settings: Settings) -> None:
    transport = httpx.ASGITransport(app=create_app(settings))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        client.cookies.set("hub_ui_style", "studio-cyan")
        pages = await asyncio.gather(
            client.get("/"),
            client.get("/settings/appearance"),
            client.get("/logs"),
            client.get("/project-docs"),
        )

    assert all(page.status_code == 200 for page in pages)
    for page in pages:
        assert '<html lang="zh-CN" data-ui-style="studio-cyan"' in page.text
        assert 'data-ui-theme-schemes="standard:light,code-dark:dark,studio-cyan:light"' in page.text
        assert '<meta name="color-scheme" content="light">' in page.text


@pytest.mark.anyio
async def test_theme_packages_are_complete_and_component_css_uses_semantic_tokens(
    settings: Settings,
) -> None:
    transport = httpx.ASGITransport(app=create_app(settings))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        tokens = await client.get("/static/css/tokens.css")
        base = await client.get("/static/css/base.css")
        components = await client.get("/static/css/components.css")
        responsive = await client.get("/static/css/responsive.css")

    assert tokens.status_code == 200
    required_suffixes = (
        "canvas",
        "surface",
        "surface-raised",
        "surface-field",
        "surface-subtle",
        "surface-hover",
        "surface-selected",
        "surface-disabled",
        "surface-code",
        "text",
        "text-muted",
        "text-disabled",
        "text-inverse",
        "border",
        "border-strong",
        "accent",
        "accent-hover",
        "accent-text",
        "accent-inverse",
        "focus-ring",
        "shadow",
        "overlay",
        "success",
        "success-background",
        "success-border",
        "warning",
        "warning-background",
        "warning-border",
        "danger",
        "danger-background",
        "danger-border",
        "info",
        "info-background",
        "info-border",
    )
    for theme in WEB_THEMES:
        for suffix in required_suffixes:
            assert f"--{theme.id}-{suffix}:" in tokens.text
        surface = _theme_hex_color(tokens.text, theme.id, "surface")
        for suffix in ("text", "text-muted", "accent-text"):
            assert _contrast_ratio(_theme_hex_color(tokens.text, theme.id, suffix), surface) >= 4.5
        assert _contrast_ratio(
            _theme_hex_color(tokens.text, theme.id, "text"),
            _theme_hex_color(tokens.text, theme.id, "surface-code"),
        ) >= 4.5
        assert _contrast_ratio(
            _theme_hex_color(tokens.text, theme.id, "text-inverse"),
            _theme_hex_color(tokens.text, theme.id, "accent"),
        ) >= 4.5

    for stylesheet in (base, components, responsive):
        assert stylesheet.status_code == 200
        assert "var(--ink)" not in stylesheet.text
        assert "var(--muted)" not in stylesheet.text
        assert "var(--line)" not in stylesheet.text
        assert "var(--paper)" not in stylesheet.text
        assert "var(--accent)" not in stylesheet.text
        assert "var(--accent-dark)" not in stylesheet.text
        assert "var(--text)" not in stylesheet.text
        assert re.search(r"#[0-9a-fA-F]{3,8}\b", stylesheet.text) is None

    preview_suffixes = (
        "background",
        "surface",
        "field",
        "ink",
        "muted",
        "accent",
        "accent-ink",
        "line",
        "success",
        "success-background",
        "warning",
        "warning-background",
        "danger",
        "danger-background",
    )
    for theme in WEB_THEMES:
        assert f'.theme-option[data-style-option="{theme.id}"]' in tokens.text
        for suffix in preview_suffixes:
            assert f"--theme-preview-{suffix}: var(--{theme.id}-" in tokens.text
    assert "--theme-preview-background:" not in components.text
    assert ':root[data-ui-style="code-dark"]' not in components.text
    assert ':root[data-ui-style="studio-cyan"]' not in components.text
    assert ".markdown-body pre {" in components.text
    assert "background: var(--color-surface-code);" in components.text
    assert "border: 1px solid var(--color-border);" in components.text
    assert "overflow-x: auto;" in components.text
    assert ".markdown-body pre code {\n  padding: 0;\n  color: var(--color-text);" in components.text
    assert "--font-size-scale: 1;" in tokens.text
    for font_size in WEB_FONT_SIZES:
        expected_selector = (
            rf':root\[data-ui-font-size="{re.escape(font_size.id)}"\] '
            rf'\{{\s*--font-size-scale: {font_size.scale:g};'
        )
        assert re.search(expected_selector, tokens.text) is not None
    assert "font-size: clamp(" not in base.text
    assert "letter-spacing: -0.055em" not in base.text


@pytest.mark.anyio
async def settings_page_removes_quick_interaction_page_size_preference(
    settings: Settings,
) -> None:
    transport = httpx.ASGITransport(app=create_app(settings))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/settings")
        script = await client.get("/static/settings.js")
        sidebar_script = await client.get("/static/settings-sidebar.js")
        sidebar_bootstrap_script = await client.get("/static/settings-sidebar-bootstrap.js")
        theme_script = await client.get("/static/theme.js")
        stylesheet = await client.get("/static/css/components.css")

    assert response.status_code == 200
    assert 'return `当前默认 ${modelName}`;' in script.text
    assert "当前 Codex 默认 ·" not in script.text
    assert "设置 · Hub" in response.text
    assert 'class="settings-workspace-shell"' in response.text
    assert 'id="settings-sidebar"' in response.text
    assert 'id="settings-sidebar-resizer"' in response.text
    assert 'role="separator"' in response.text
    assert 'aria-valuemin="225"' in response.text
    assert 'aria-valuemax="360"' in response.text
    assert 'id="settings-return-application" class="settings-workspace-return" href="/"' in response.text
    assert "返回应用" in response.text
    assert 'src="/static/settings-sidebar-bootstrap.js"' in response.text
    assert response.text.index('src="/static/settings-sidebar-bootstrap.js"') < response.text.index(
        '/static/css/tokens.css',
    )
    assert 'src="/static/settings-sidebar.js"' in response.text
    assert 'class="settings-mobile-nav" aria-label="设置导航"' in response.text
    assert 'class="settings-mobile-nav-external" href="/"' in response.text
    assert 'data-settings-url="/settings/quick-interaction"' not in response.text
    assert "history.replaceState(history.state, \"\", targetUrl.href);" in sidebar_script.text
    assert 'target.closest("a.settings-navigation-link")' in sidebar_script.text
    assert 'target.closest("button.settings-mobile-nav-link")' in sidebar_script.text
    assert "const replaceSettingsPage" in sidebar_script.text
    assert 'document.addEventListener("click", replaceSettingsPage);' in sidebar_script.text
    assert 'new URL(item.dataset.settingsUrl || "", window.location.href).href' in sidebar_script.text
    assert 'targetUrl.searchParams.set("return_to", returnUrl);' in sidebar_script.text
    assert "const hasWorkspaceReturnHistory = (link) =>" in sidebar_script.text
    assert "window.history.back();" in sidebar_script.text
    assert "navigationController?.abort();" in sidebar_script.text
    assert "if (requestId !== navigationRequestId) return;" in sidebar_script.text
    assert "window.initializeSettingsPage = () =>" in script.text
    assert "window.disposeSettingsPage = () =>" in script.text
    assert 'if (settingsPage === "openclaw") {' in script.text
    assert "openclaw-weixin-dialog" not in script.text
    assert 'fetchSettingsApi("/api/openclaw/status")' not in script.text
    assert 'fetchSettingsApi("/api/openclaw/weixin/login")' not in script.text
    assert "快速交互" in response.text
    assert '<h3 id="core-settings-title" class="settings-layer-title">Chub 核心</h3>' in response.text
    assert "调整会话历史记录的加载方式。" in response.text
    assert "按 Chub 核心、AI Runtime 与第三方服务查看现有配置。" in response.text
    assert "界面风格" in response.text
    assert "微信任务文本优化" in response.text
    assert '<h3 id="runtime-settings-title">Runtime 管理</h3>' in response.text
    assert 'id="runtime-management-list"' in response.text
    assert 'id="runtime-management-description"' in response.text
    assert 'id="runtime-management-message"' not in response.text
    assert "关闭不会中断已受理任务。" in response.text
    assert "新建 Session 默认权限" in response.text
    assert "Codex 会话默认项" not in response.text
    assert "Chub 核心" in response.text
    assert "AI Runtime" in response.text
    assert "第三方服务" in response.text
    assert 'id="settings-category"' not in response.text
    assert 'href="#quick-interaction-settings" aria-current="true"' in response.text
    assert response.text.count('class="settings-navigation-link"') == 7
    assert response.text.count('class="settings-navigation-icon" aria-hidden="true"') == 7
    assert 'href="#utility-settings"' in response.text
    assert 'href="#openclaw-settings"' in response.text
    assert 'href="#openclaw-weixin-settings"' not in response.text
    assert '<h3 id="openclaw-settings-title">OpenClaw</h3>' in response.text
    assert '<h4 id="openclaw-gateway-settings-title">Gateway</h4>' in response.text
    assert '<h4 id="openclaw-clawbot-settings-title">微信 ClawBot</h4>' in response.text
    assert 'id="settings-openclaw-badge"' in response.text
    assert 'id="settings-openclaw-detail"' in response.text
    assert 'id="settings-openclaw-open" class="settings-utility-row settings-integration-row"' in response.text
    assert 'id="settings-openclaw-open-label"' in response.text
    assert 'id="settings-openclaw-bind-weixin"' in response.text
    assert 'class="settings-field settings-integration-row"' in response.text
    assert "微信 ClawBot" in response.text
    assert '<h3 id="openclaw-weixin-settings-title">OpenClaw</h3>' not in response.text
    assert 'id="openclaw-weixin-dialog"' in response.text
    assert 'id="openclaw-weixin-account-summary"' in response.text
    assert 'id="openclaw-weixin-owner-summary"' in response.text
    assert 'id="openclaw-weixin-qr"' in response.text
    assert 'id="openclaw-weixin-verify-form"' in response.text
    assert 'id="weixin-processing-mode"' in response.text
    assert 'class="settings-choice-list"' in response.text
    assert '<h4 id="weixin-processing-mode-title">正文处理方式</h4>' in response.text
    assert '<h4 id="weixin-translation-model-title">文本优化运行参数</h4>' in response.text
    assert 'aria-labelledby="weixin-processing-mode-title"' in response.text
    assert 'value="direct"' in response.text
    assert 'value="auto"' in response.text
    assert 'value="confirm"' in response.text
    assert "自动润色后执行" in response.text
    assert "查看处理规则" not in response.text
    assert "Standard" in response.text
    assert "Code Dark" in response.text
    assert "当前风格" in response.text
    assert 'href="/settings/styles/standard"' in response.text
    assert 'href="/settings/styles/code-dark"' in response.text
    assert 'href="/settings/workspace-preview"' not in response.text
    assert "工作台交互预览" not in response.text
    assert 'id="cyber-rain-speed"' not in response.text
    assert 'id="cyber-rain-brightness"' not in response.text
    assert 'id="cyber-rain-density"' not in response.text
    assert "风格选择保存在当前浏览器" in response.text
    assert '<h3 id="utility-settings-title">诊断与关于</h3>' in response.text
    assert 'class="settings-utility-row" href="/logs"' in response.text
    assert 'id="settings-maintenance-terminal" class="settings-utility-row" type="button"' in response.text
    assert 'id="maintenance-terminal-dialog" class="codex-workspace-dialog confirmation-dialog"' in response.text
    assert "打开维护终端" in response.text
    assert "Chub 版本" in response.text
    assert 'data-cyber-style-details' not in response.text
    assert 'data-style-apply="standard"' in response.text
    assert 'data-style-apply="cyber"' not in response.text
    assert 'name="quick-interaction-view"' not in response.text
    assert "任务视图" not in response.text
    assert 'id="quick-interaction-page-size"' not in response.text
    assert 'id="codex-default-full-access"' not in response.text
    assert "模型和推理等级默认跟随 AI" in response.text
    assert 'id="codex-show-translation-session"' not in response.text
    assert 'id="codex-default-model"' not in response.text
    assert 'id="codex-default-reasoning-effort"' not in response.text
    assert 'id="weixin-translation-model-field"' in response.text
    assert 'id="weixin-translation-reasoning-effort-field"' in response.text
    assert 'id="weixin-translation-model-description"' in response.text
    assert 'id="weixin-translation-reasoning-effort-description"' in response.text
    assert 'id="weixin-translation-model-field"' in response.text
    assert 'id="weixin-translation-reasoning-effort-field"' in response.text
    assert response.text.count('id="weixin-translation-model-field"') == 1
    assert response.text.count('id="weixin-translation-reasoning-effort-field"') == 1
    assert "默认使用 Full access" in response.text
    assert "关闭后使用 Read Only" in response.text
    assert "尚未开放" not in response.text
    assert f"v{settings.app.version}" in response.text
    assert "返回首页" not in response.text
    assert script.status_code == 200
    assert sidebar_script.status_code == 200
    assert sidebar_bootstrap_script.status_code == 200
    assert "chub.sidebarWidth" in sidebar_script.text
    assert "chub.settings.sidebarWidth" not in sidebar_script.text
    assert "minimumSidebarWidth = 225" in sidebar_script.text
    assert "maximumSidebarWidth = 360" in sidebar_script.text
    assert "Number.isFinite(value)" in sidebar_script.text
    assert 'resizer.addEventListener("pointerdown"' in sidebar_script.text
    assert "collapsed" not in sidebar_script.text
    assert 'event.key.toLowerCase() !== "b"' not in sidebar_script.text
    assert 'event.key !== "Escape"' not in sidebar_script.text
    assert "window.location.assign" not in sidebar_script.text
    assert "settings-sidebar-preload-width" in sidebar_bootstrap_script.text
    assert "chub.sidebarWidth" in sidebar_bootstrap_script.text
    assert "initializeSettingsChoicePickers" in script.text
    assert "loadRuntimeManagement" in script.text
    assert "saveRuntimeEnablement" in script.text
    assert 'aria-haspopup", "listbox"' in script.text
    assert "closeSettingsChoicePicker" in script.text
    assert "defaultReasoningDescription" in script.text
    assert 'return "跟随模型默认"' in script.text
    assert ":not(.settings-choice-picker-trigger):not(.settings-choice-picker-option)" in stylesheet.text
    assert ':root[data-ui-style="code-dark"] .settings-choice-picker-option {' in stylesheet.text
    assert ':root[data-ui-style="code-dark"] .automation-browser-panel,' in stylesheet.text
    assert ".settings-integration-row > span:first-child" in stylesheet.text
    assert ".settings-workspace-shell" in stylesheet.text
    assert "--settings-sidebar-width: var(--settings-sidebar-preload-width, 225px);" in stylesheet.text
    assert stylesheet.text.count("overscroll-behavior-y: contain;") >= 2
    assert ".settings-workspace-return {\n  display: flex;\n  height: 2.25rem;" in stylesheet.text
    assert "  border-radius: 8px;\n  padding: 0 0.2rem;\n  color: var(--color-text-muted);" in stylesheet.text
    assert ".settings-workspace-return:hover,\n.settings-workspace-return:active {\n  color: var(--color-accent-text);\n  background: color-mix(in srgb, var(--color-accent) 8%, transparent);" in stylesheet.text
    assert "grid-template-columns: var(--settings-sidebar-width) minmax(0, 1fr);" in stylesheet.text
    assert ".settings-mobile-nav {" in stylesheet.text
    assert ".settings-workspace-shell.is-sidebar-resizing" in stylesheet.text
    assert ".settings-navigation-link[aria-current=\"true\"]" in stylesheet.text
    assert "border: 1px solid transparent;" in stylesheet.text
    assert "border-left: 2px solid var(--line);" not in stylesheet.text
    assert ".settings-workspace-page {\n  width: 100%;\n  height: 100dvh;" in stylesheet.text
    assert ".settings-workspace-main {\n  display: grid;" in stylesheet.text
    assert "overflow-y: auto;" in stylesheet.text
    assert "settingsWorkspaceMain.addEventListener(\"scroll\"" in script.text
    assert "hub.quickInteractionView.v1" not in script.text
    assert "hub.quickInteractionPageSize.v1" not in script.text
    assert "hub.codexDefaultPermission.v1" not in script.text
    assert "hub.codexDefaultModel.v1" not in script.text
    assert "hub.codexDefaultReasoningEffort.v1" not in script.text
    assert "hub.weixinTranslationSettingsCache" in script.text
    assert "hub.openclawWeixinSettingsCache.v1" in script.text
    assert "hub.codexShowTranslationSession.v1" not in script.text
    assert "/api/codex/models" in script.text
    assert "/api/codex/session-defaults" not in script.text
    assert "/api/settings/weixin-translation" in script.text
    assert "native_cleanup_pending" in script.text
    assert "当前 Codex 默认 ·" in script.text
    assert "defaultReasoningDescription(" in script.text
    assert "/api/openclaw/status" in script.text
    assert "local_access_url" in script.text
    assert "localOpenClawAccessUrl" in script.text
    assert "/api/maintenance-terminal/access" in script.text
    assert 'const terminalWindow = window.open("", "_blank");' in script.text
    assert "terminalWindow.opener = null;" in script.text
    assert "terminalWindow.location.replace(data.terminal_url);" in script.text
    assert "/api/openclaw/weixin/login" in script.text
    assert "settingsOpenClawWeixinPollFailures" in script.text
    assert "pollOpenClawWeixinLogin" in script.text
    assert "微信通道已连接" in script.text
    assert "微信通道未配置" in script.text
    assert '"重新绑定微信"' in script.text
    assert 'idle: ["未绑定"' not in script.text
    assert "当前展示上次检测结果" in script.text
    assert "WEIXIN_TRANSLATION_SETTINGS_CACHE_KEY" in script.text
    assert "项文本优化仍在处理中" in script.text
    assert "已开启，将从下一条微信普通任务开始处理" not in script.text
    assert "已关闭，新任务不再翻译" not in script.text
    assert "设置结果未知，请稍后刷新页面重试" in script.text
    assert "暂时无法刷新文本优化任务状态，正在重试" in script.text
    assert "window.setTimeout" in script.text
    assert 'id="weixin-translation-status"' not in response.text
    assert "之后新建的 Session 将使用该权限" not in script.text
    assert "之后新建的 Session 将使用该模型与等级" not in script.text
    assert "localStorage.setItem" in script.text
    assert "hub.cyberRainSpeed.v1" not in script.text
    assert "hub.cyberRainBrightness.v1" not in script.text
    assert "hub.cyberRainDensity.v1" not in script.text
    assert "ChubTheme.applyStyle" in script.text
    assert theme_script.status_code == 200
    assert "hub.uiStyle.v1" in theme_script.text
    assert "下次进入快速交互时生效" in response.text
    assert "scrollToSettingsSection" in script.text
    assert "settingsWorkspaceMain.scrollTo({" in script.text
    assert "requestAnimationFrame(updateActiveSettingsSection)" in script.text
    assert response.headers["content-security-policy"] == (
        "default-src 'self'; "
        "script-src 'self'; "
        "style-src 'self'; "
        "connect-src 'self'; "
        "img-src 'self' data: blob:; "
        "object-src 'none'; "
        "base-uri 'none'; "
        "frame-ancestors 'none'"
    )


@pytest.mark.anyio
async def test_settings_pages_use_independent_routes_and_page_scoped_content(
    settings: Settings,
) -> None:
    transport = httpx.ASGITransport(app=create_app(settings))
    paths = {
        "appearance": "/settings/appearance",
        "diagnostics": "/settings/diagnostics",
        "runtime": "/settings/runtime",
        "runtime-detail": "/settings/runtime/codex",
        "task-orchestration": "/settings/task-orchestration",
        "openclaw": "/settings/openclaw",
    }
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        root = await client.get("/settings", follow_redirects=False)
        removed_quick_interaction = await client.get("/settings/quick-interaction")
        removed_session_defaults = await client.get("/settings/session-defaults")
        legacy_weixin_text = await client.get("/settings/weixin-text", follow_redirects=False)
        legacy_gateway = await client.get("/settings/openclaw/gateway", follow_redirects=False)
        legacy_clawbot = await client.get("/settings/openclaw/clawbot", follow_redirects=False)
        unknown_runtime = await client.get("/settings/runtime/unknown")
        pages = {
            page: await client.get(path)
            for page, path in paths.items()
        }
        script = await client.get("/static/settings.js")
        workspace_script = await client.get(
            "/static/js/features/workspace-task-orchestration.js",
        )
        home = await client.get("/")
        stylesheet = await client.get("/static/css/components.css")

    assert root.status_code == 307
    assert root.headers["location"] == "/settings/appearance"
    assert removed_quick_interaction.status_code == 404
    assert removed_session_defaults.status_code == 404
    assert "通用设置" in pages["appearance"].text
    assert "会话与偏好" not in pages["appearance"].text
    assert legacy_weixin_text.status_code == 307
    assert legacy_weixin_text.headers["location"] == "/settings/task-orchestration"
    assert legacy_gateway.status_code == 307
    assert legacy_gateway.headers["location"] == "/settings/openclaw"
    assert legacy_clawbot.status_code == 307
    assert legacy_clawbot.headers["location"] == "/settings/openclaw"
    assert unknown_runtime.status_code == 404
    assert all(response.status_code == 200 for response in pages.values())
    for page, response in pages.items():
        assert f'data-settings-page="{page}"' in response.text
        assert 'id="settings-workspace-main"' in response.text
        assert 'id="settings-page-dialogs"' in response.text
        assert 'href="#' not in response.text
        assert 'id="settings-return-application" class="settings-workspace-return" href="/"' in response.text
        assert response.headers["content-security-policy"] == (
            "default-src 'self'; "
            "script-src 'self'; "
            "style-src 'self'; "
            "connect-src 'self'; "
            "img-src 'self' data: blob:; "
            "object-src 'none'; "
            "base-uri 'none'; "
            "frame-ancestors 'none'"
        )

    assert 'href="/settings/session-defaults"' not in pages["appearance"].text
    assert 'id="runtime-management-list"' not in pages["runtime"].text
    assert 'id="runtime-general-settings-title"' not in pages["runtime"].text
    assert "此处控制 Runtime 是否接收后续新 AI 任务" not in pages["runtime"].text
    assert 'id="codex-default-runtime-implementation"' not in pages["runtime"].text
    assert "Runtime 模块导入" in pages["runtime"].text
    assert "可在对应 Runtime 设置页选择当前使用版本。" in pages["runtime"].text
    assert 'class="runtime-module-install-heading"' in pages["runtime"].text
    assert "能力模块导入" in pages["runtime"].text
    assert 'id="orchestration-module-file"' in pages["runtime"].text
    assert 'id="orchestration-module-list" class="settings-divided-list runtime-module-list"' in pages["runtime"].text
    assert 'id="ai-runtime-general-settings"' in pages["runtime"].text
    assert 'href="/settings/runtime" aria-current="page"' in pages["runtime"].text
    assert 'id="runtime-module-list" class="settings-divided-list runtime-module-list"' in pages["runtime"].text
    assert 'href="/settings/runtime/codex"' in pages["runtime"].text
    assert 'id="quick-interaction-page-size"' not in pages["runtime"].text
    assert 'id="codex-default-full-access"' not in pages["runtime"].text
    assert "new-session-permission" in script.text
    assert 'data-settings-page="runtime-detail"' in pages["runtime-detail"].text
    assert 'id="runtime-management-list"' in pages["runtime-detail"].text
    assert 'id="codex-default-runtime-implementation" data-settings-picker disabled' in pages["runtime-detail"].text
    assert "当前使用版本" in pages["runtime-detail"].text
    assert "Runtime 运行状态" not in pages["runtime-detail"].text
    assert 'id="codex-runtime-version-list"' not in pages["runtime-detail"].text
    assert 'id="codex-builtin-runtime-refresh"' not in pages["runtime-detail"].text
    assert 'id="codex-runtime-versions-message"' not in pages["runtime-detail"].text
    assert 'id="codex-runtime-settings-message"' in pages["runtime-detail"].text
    assert 'id="runtime-settings-panel"' not in pages["runtime-detail"].text
    assert "控制是否接收新任务" not in pages["runtime-detail"].text
    assert "ai_runtime.{{ settings_runtime_id }}" not in pages["runtime-detail"].text
    assert "ai_runtime.codex" not in pages["runtime-detail"].text
    assert "usage-timezone" not in pages["runtime-detail"].text
    assert '.settings-field input[type="text"]' in stylesheet.text
    assert 'background: var(--color-surface-field);' in stylesheet.text
    assert '.settings-divided-list .settings-field + .settings-field' in stylesheet.text
    assert 'border-top: 1px solid var(--color-border);' in stylesheet.text
    assert '.settings-divided-list,\n.workspace-task-orchestration-list {' in stylesheet.text
    assert 'margin-top: 0.5rem;' in stylesheet.text
    assert '.settings-divided-list .settings-field > span' in stylesheet.text
    assert '.settings-divided-list.runtime-module-list' in stylesheet.text
    assert '.runtime-module-empty-row > span' in stylesheet.text
    assert '.settings-divided-list .settings-utility-row + .settings-utility-row' in stylesheet.text
    assert '.settings-utility-list {' in stylesheet.text
    assert 'border-radius: 12px;' in stylesheet.text
    assert '.runtime-settings-form {\n  display: grid;\n  gap: 0.75rem;\n  margin-top: 0;' in stylesheet.text
    assert 'class="settings-divided-list runtime-detail-settings-list"' in pages["runtime-detail"].text
    assert 'data-runtime-id="codex"' in pages["runtime-detail"].text
    assert 'id="runtime-management-description"' not in pages["runtime-detail"].text
    assert pages["runtime-detail"].text.count('id="runtime-management-status"') == 1
    assert 'id="runtime-management-message"' not in pages["runtime-detail"].text
    assert 'const field = document.createElement("section");' in script.text
    assert 'input.id = `runtime-enabled-${runtime.runtime_id}`;' in script.text
    assert 'control.htmlFor = input.id;' in script.text
    assert 'moduleEmptyRow("尚未导入 Runtime 模块。")' in script.text
    assert 'moduleEmptyRow("尚未导入能力模块。")' in script.text
    assert 'formalImplementationTitle(module.name || "Runtime", module.version)' in script.text
    assert 'badge.textContent = module.status === "active" ? "已启用" : "不可用";' in script.text
    assert 'Runtime 模块已导入并启用。' in script.text
    assert 'formalImplementationTitle(module.name || "能力模块", module.version)' in script.text
    assert 'remove.addEventListener("click", clearSelectedRuntimeModule);' in script.text
    assert 'remove.addEventListener("click", clearSelectedOrchestrationModule);' in script.text
    assert 'href="/settings/runtime/codex" aria-current="page"' in pages["runtime-detail"].text
    assert '新建 Session 默认项由 Chub 安全保存。' not in pages["openclaw"].text
    assert '浏览器拒绝保存时，主题和文字大小仅在当前页临时应用。' in pages["openclaw"].text
    assert 'href="/settings/task-orchestration"' in pages["runtime"].text
    assert 'href="/settings/task-orchestration" aria-current="page"' in pages["task-orchestration"].text
    assert 'id="workspace-task-processing-trigger"' in pages["task-orchestration"].text
    assert 'aria-label="微信任务润色"><section class="workstation-group workspace-task-orchestration-group"' in pages["task-orchestration"].text
    assert 'id="workspace-task-show-internal-native-session"' in pages["task-orchestration"].text
    assert 'id="workspace-task-module-file"' not in pages["task-orchestration"].text
    assert 'id="workspace-task-module-list"' not in pages["task-orchestration"].text
    assert pages["task-orchestration"].text.index(
        'id="workspace-task-show-internal-native-session"'
    ) < pages["task-orchestration"].text.index('id="workspace-task-processing-trigger"')
    assert 'label class="settings-switch" for="workspace-task-show-internal-native-session"' in pages["task-orchestration"].text
    assert 'enabledTitle.textContent = "启用文本优化";' in workspace_script.text
    assert 'enabledInput.id = "workspace-task-enabled";' in workspace_script.text
    assert 'orchestrationList.insertBefore(internalSessionRow, implementationRow);' in workspace_script.text
    assert 'id="workspace-task-orchestration-title"' not in pages["task-orchestration"].text
    assert 'class="theme-option-groups" role="radiogroup" aria-label="主题选择"' in pages["appearance"].text
    assert 'id="theme-option-group-light-title">亮色系主题</h4>' in pages["appearance"].text
    assert 'id="theme-option-group-dark-title">暗色系主题</h4>' in pages["appearance"].text
    assert pages["appearance"].text.index('data-style-option="standard"') < pages["appearance"].text.index('data-style-option="studio-cyan"') < pages["appearance"].text.index('data-style-option="code-dark"')
    assert '<title>外观 · 设置 ·' in pages["appearance"].text
    assert '<span>外观</span></a>' in pages["appearance"].text
    assert 'name="ui-style" value="standard"' in pages["appearance"].text
    assert 'name="ui-style" value="code-dark"' in pages["appearance"].text
    assert 'name="ui-style" value="studio-cyan"' in pages["appearance"].text
    assert '<strong>Standard</strong><small>亮色主题</small>' in pages["appearance"].text
    assert '<strong>Code Dark</strong><small>暗色主题</small>' in pages["appearance"].text
    assert '<strong>Studio Cyan</strong><small>冷静浅色主题</small>' in pages["appearance"].text
    assert "theme-option-indicator" not in pages["appearance"].text
    assert 'aria-label="Standard 的主文字、次文字和主强调色"' in pages["appearance"].text
    assert 'aria-label="Code Dark 的主文字、次文字和主强调色"' in pages["appearance"].text
    assert 'aria-label="Studio Cyan 的主文字、次文字和主强调色"' in pages["appearance"].text
    assert 'data-theme-details-toggle aria-expanded="false"' in pages["appearance"].text
    assert 'data-theme-details-label>显示文字层级示例</span>' in pages["appearance"].text
    assert '<div class="theme-settings-heading"><div><h3 id="style-settings-title">主题</h3><p class="settings-subsection-description">比较文字、状态与控件效果。</p></div><button class="theme-details-toggle"' in pages["appearance"].text
    assert pages["appearance"].text.count('<strong>标题文本</strong><small>描述文案</small><p>正文内容用于展示主要阅读层级。</p></span><span class="theme-option-preview-selected">当前选中</span>') == len(WEB_THEMES)
    assert pages["appearance"].text.count('<span class="theme-option-preview-status is-success">已完成</span><span class="theme-option-preview-status">处理中</span><span class="theme-option-preview-status is-failed">需处理</span>') == len(WEB_THEMES)
    assert pages["appearance"].text.count('<em class="is-secondary">次要操作</em><em>主要操作</em>') == len(WEB_THEMES)
    assert 'href="/settings/styles/standard"' not in pages["appearance"].text
    assert 'href="/settings/styles/code-dark"' not in pages["appearance"].text
    assert 'class="font-size-option-grid" role="radiogroup" aria-label="文字大小选择"' in pages["appearance"].text
    assert 'name="ui-font-size" value="small"' in pages["appearance"].text
    assert 'name="ui-font-size" value="default"' in pages["appearance"].text
    assert 'name="ui-font-size" value="large"' in pages["appearance"].text
    assert '<strong>小</strong><small>90%</small>' in pages["appearance"].text
    assert '<strong>默认</strong><small>100%</small>' in pages["appearance"].text
    assert '<strong>大</strong><small>110%</small>' in pages["appearance"].text
    assert 'id="maintenance-terminal-dialog"' in pages["diagnostics"].text
    assert 'id="settings-openclaw-integration-list"' in pages["openclaw"].text
    assert 'id="settings-openclaw-patch-list"' in pages["openclaw"].text
    assert "核对微信 ClawBot 适配器与 Chub 插件的本机安装元数据。" in pages["openclaw"].text
    assert "查看当前已登记的兼容补丁基线。" in pages["openclaw"].text
    assert 'id="settings-openclaw-open"' not in pages["openclaw"].text
    assert 'id="settings-openclaw-bind-weixin"' not in pages["openclaw"].text
    assert 'id="weixin-processing-mode"' not in pages["openclaw"].text
    assert 'id="weixin-translation-model-field"' not in pages["openclaw"].text
    assert pages["openclaw"].text.index('id="openclaw-integration-settings-title"') < pages["openclaw"].text.index('id="openclaw-patch-settings-title"')
    assert pages["openclaw"].text.index('id="openclaw-integration-settings-title"') < pages["openclaw"].text.index('id="settings-openclaw-integration-message"') < pages["openclaw"].text.index('id="settings-openclaw-integration-list"')
    assert "第三方服务配置由 Chub 安全保存" not in pages["openclaw"].text
    assert 'href="/settings/openclaw" aria-current="page"' in pages["openclaw"].text
    assert 'href="/settings/weixin-text"' not in pages["openclaw"].text
    assert "settings-subnavigation" not in pages["openclaw"].text
    assert 'href="/settings/openclaw/gateway"' not in pages["openclaw"].text
    assert 'href="/settings/openclaw/clawbot"' not in pages["openclaw"].text
    assert script.status_code == 200
    assert 'OPENCLAW_INTEGRATION_CACHE_KEY' not in script.text
    assert '"当前展示上次检查结果，正在重新核验。"' not in script.text
    assert 'data.message?.includes("均已确认")' not in script.text
    assert 'settingsPage === "task-orchestration"' in script.text
    assert 'row.classList.toggle("is-selected", selected);' in script.text
    assert 'input.addEventListener("change", () => {' in script.text
    assert 'const THEME_DETAILS_EXPANDED_KEY = "hub.themeDetailsExpanded.v1";' in script.text
    assert 'detailsToggle.addEventListener("click", () => setDetailsExpanded(!detailsExpanded));' in script.text
    assert 'detailsToggleLabel.textContent = toggleLabel;' in script.text
    assert 'setDetailsExpanded(detailsExpanded, { persist: false, animate: false });' in script.text
    assert 'const settingsPage = document.body.dataset.settingsPage || "";' in script.text
    assert 'settingsPage === "runtime-detail"' in script.text
    assert 'loadRuntimeSettings();' not in script.text
    assert 'settingsPage === "runtime"' in script.text
    assert 'loadGeneralRuntimeSettings();' in script.text
    assert "scrollToSettingsSection" not in script.text
    assert "settingsWorkspaceMain.scrollTo" not in script.text
    assert '.settings-navigation-link[aria-current="page"]' in stylesheet.text
    assert "min-height: 34px;" in stylesheet.text
    assert ".settings-choice-picker-trigger:hover:not(:disabled)" in stylesheet.text
    assert "background: color-mix(in srgb, var(--color-accent) 3%, var(--color-surface-raised));" in stylesheet.text
    assert ".settings-choice-picker-option.is-selected" in stylesheet.text
    assert ".theme-option-grid" in stylesheet.text
    assert ".theme-option-groups" in stylesheet.text
    assert ".theme-option-group + .theme-option-group" in stylesheet.text
    theme_group_rules = stylesheet.text[
        stylesheet.text.index(".theme-option-group + .theme-option-group"):stylesheet.text.index(".theme-option-group h4")
    ]
    assert "border-top" not in theme_group_rules
    assert ".theme-option.is-selected" in stylesheet.text
    assert "min-height: 72px;" in stylesheet.text
    assert "grid-template-rows: minmax(3.1rem, auto) auto;" in stylesheet.text
    assert ".theme-option:has(input:focus-visible)" in stylesheet.text
    assert ".theme-option:focus-within" not in stylesheet.text
    assert "--theme-preview-background:" not in stylesheet.text
    assert "background: var(--theme-preview-background);" in stylesheet.text
    assert ':root[data-ui-style="code-dark"]' not in stylesheet.text
    assert "background: var(--color-surface-selected);" in stylesheet.text
    assert "style-preview" not in stylesheet.text
    assert ".theme-option-preview" in stylesheet.text
    assert ".theme-option-preview-surface" in stylesheet.text
    assert ".theme-option-preview-selected" in stylesheet.text
    assert ".theme-option-preview-statuses" in stylesheet.text
    assert ".theme-details-toggle" in stylesheet.text
    assert ".theme-settings-heading" in stylesheet.text
    assert ".theme-settings-heading > div" in stylesheet.text
    assert ".theme-settings-heading .settings-subsection-description" in stylesheet.text
    assert ".appearance-font-size-settings" in stylesheet.text
    assert "margin-top: 0.5rem;" in stylesheet.text
    assert ".theme-option-preview-copy" in stylesheet.text
    assert "max-height 180ms ease 140ms" in stylesheet.text
    assert ".theme-option.is-expanded .theme-option-preview" in stylesheet.text
    assert ".theme-option-preview-status" in stylesheet.text
    assert ".theme-option-preview-field" in stylesheet.text
    assert "border-radius: 50%;" in stylesheet.text
    assert ".settings-subnavigation" not in stylesheet.text
    assert home.status_code == 200
    assert 'id="workspace-task-orchestration-dialog"' not in home.text
    assert 'workspace-preview-task-orchestration' not in home.text
    assert 'src="/static/js/features/workspace-task-orchestration.js"' not in home.text
    assert 'src="/static/js/features/workspace-task-orchestration.js"' in pages["task-orchestration"].text
    assert workspace_script.status_code == 200
    assert '"/api/settings/weixin-translation"' in workspace_script.text
    assert 'show_internal_native_session' in workspace_script.text
    assert '"/api/codex/models"' in workspace_script.text
    assert 'window.initializeWorkspaceTaskOrchestration' in workspace_script.text
    assert 'window.disposeWorkspaceTaskOrchestration' in workspace_script.text
    assert '.workspace-preview-session-group + .workspace-preview-session-group' in stylesheet.text
    session_group_rules = stylesheet.text[
        stylesheet.text.index('.workspace-preview-session-group + .workspace-preview-session-group'):stylesheet.text.index('.workspace-preview-session-group-title')
    ]
    assert 'border-top: 1px solid var(--color-border);' not in session_group_rules


@pytest.mark.anyio
async def test_runtime_settings_navigation_lists_each_registered_runtime(
    settings: Settings,
) -> None:
    app = create_app(settings)
    runtime_modules = MagicMock(spec=BuiltinRuntimeModuleRegistry)
    codex_runtime = SimpleNamespace(
        runtime_id="codex",
        name="Codex Runtime",
        descriptor=RuntimeDescriptor(
            runtime_id="codex",
            capabilities=frozenset({"runtime_status"}),
        ),
        display_name="Codex Runtime",
        description="Codex Runtime description",
    )
    local_runtime = SimpleNamespace(
        runtime_id="local",
        name="Local Runtime",
        descriptor=RuntimeDescriptor(
            runtime_id="local",
            capabilities=frozenset({"runtime_status"}),
        ),
        display_name="Local Runtime",
        description="Local Runtime description",
    )
    runtime_modules.navigation.return_value = (
        SimpleNamespace(
            runtime_id="codex",
            name="Codex Runtime",
            description="Codex Runtime description",
        ),
        SimpleNamespace(
            runtime_id="local",
            name="Local Runtime",
            description="Local Runtime description",
        ),
    )
    runtime_modules.require_navigation.side_effect = {
        "codex": codex_runtime,
        "local": local_runtime,
    }.__getitem__
    app.state.ai_session_manager.runtime_modules = runtime_modules
    app.state.ai_session_manager.read_runtime_management = MagicMock(
        return_value=RuntimeManagementData(
            basic_mode=False,
            runtimes=[
                RuntimeManagementItem(
                    runtime_id="codex",
                    name="Codex Runtime",
                    enabled=True,
                    healthy=True,
                ),
                RuntimeManagementItem(
                    runtime_id="local",
                    name="Local Runtime",
                    enabled=False,
                    healthy=False,
                    reason="Local Runtime is unavailable",
                ),
            ],
        )
    )
    transport = httpx.ASGITransport(app=app)

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        general = await client.get("/settings/runtime")
        local = await client.get("/settings/runtime/local")

    assert general.status_code == 200
    assert local.status_code == 200
    assert 'href="/settings/runtime/codex"' in general.text
    assert 'href="/settings/runtime/local"' in general.text
    assert 'data-settings-url="/settings/runtime/local"' in general.text
    assert 'href="/settings/runtime/local" aria-current="page"' in local.text
    assert 'data-runtime-id="local"' in local.text
    assert "Local Runtime description" in local.text


@pytest.mark.anyio
async def test_legacy_style_preview_routes_return_to_theme_settings(settings: Settings) -> None:
    transport = httpx.ASGITransport(app=create_app(settings))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        standard_response = await client.get("/settings/styles/standard", follow_redirects=False)
        code_dark_response = await client.get("/settings/styles/code-dark", follow_redirects=False)
        removed_response = await client.get("/settings/styles/cyber")

    assert standard_response.status_code == 307
    assert standard_response.headers["location"] == "/settings/appearance"
    assert code_dark_response.status_code == 307
    assert code_dark_response.headers["location"] == "/settings/appearance"
    assert removed_response.status_code == 404




@pytest.mark.anyio
async def test_root_page_is_the_workspace_and_legacy_workspace_redirects(
    settings: Settings,
) -> None:
    transport = httpx.ASGITransport(app=create_app(settings))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        home = await client.get("/")
        selected_session = await client.get("/?session=session-123")
        automations = await client.get("/?section=automations")
        project_documents = await client.get("/?section=project-docs")
        settings_redirect = await client.get(
            "/settings?return_to=%2F%3Fsession%3Dsession-123",
            follow_redirects=False,
        )
        settings_with_return_target = await client.get(
            "/settings/runtime?return_to=%2F%3Fsession%3Dsession-123"
        )
        invalid_settings_return_target = await client.get(
            "/settings/runtime?return_to=https%3A%2F%2Fexample.invalid"
        )
        legacy_workspace = await client.get("/workspace", follow_redirects=False)
        legacy_automations = await client.get(
            "/workspace?section=automations",
            follow_redirects=False,
        )
        removed_assets = await asyncio.gather(
            client.get("/static/app.js"),
            client.get("/static/codex_polling.js"),
            client.get("/static/js/core/dashboard-core.js"),
            client.get("/static/js/features/codex-sessions.js"),
        )

    assert home.status_code == 200
    assert selected_session.status_code == 200
    assert automations.status_code == 200
    assert project_documents.status_code == 200
    assert settings_redirect.status_code == 307
    assert settings_redirect.headers["location"] == (
        "/settings/appearance?return_to=%2F%3Fsession%3Dsession-123"
    )
    assert 'id="settings-return-application" class="settings-workspace-return" href="/?session=session-123"' in settings_with_return_target.text
    assert 'class="settings-mobile-nav-external" href="/?session=session-123"' in settings_with_return_target.text
    assert 'id="settings-return-application" class="settings-workspace-return" href="/"' in invalid_settings_return_target.text
    assert '<title>Hub</title>' in home.text
    assert 'href="/" aria-current="page"' in home.text
    assert 'href="/?section=automations"' in home.text
    assert 'href="/?section=project-docs"' in home.text
    assert "工作站环境" in home.text
    assert 'aria-label="Runtime Session 列表"' in home.text
    assert 'data-workspace-session-id="session-123"' in selected_session.text
    assert 'src="/codex/session-123/quick-interactions/conversation?embedded=workspace"' in selected_session.text
    assert 'class="workspace-preview-main is-showing-quick-session"' in selected_session.text
    assert "workspace-chub-summary" not in selected_session.text
    assert "自动化任务" in automations.text
    assert "项目说明、设计方案与维护文档" in project_documents.text
    assert legacy_workspace.status_code == 307
    assert legacy_workspace.headers["location"] == "/"
    assert legacy_automations.status_code == 307
    assert legacy_automations.headers["location"] == "/?section=automations"
    assert all(response.status_code == 404 for response in removed_assets)


@pytest.mark.anyio
async def test_home_workstation_third_party_controls_are_state_driven(
    settings: Settings,
) -> None:
    transport = httpx.ASGITransport(app=create_app(settings))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/")
        script = await client.get("/static/js/features/workspace-workstation.js")

    assert response.status_code == 200
    assert "开发环境" in response.text
    assert response.text.index("工作站环境") < response.text.index("开发环境") < response.text.index("第三方服务环境")
    assert 'id="workspace-development-refresh"' in response.text
    assert 'id="workspace-development-codex-detail"' in response.text
    assert 'id="workspace-development-weixin-detail"' in response.text
    assert 'workspace-development-codex-refresh' not in response.text
    assert 'workspace-development-weixin-refresh' not in response.text
    assert "第三方服务环境" in response.text
    assert 'id="workspace-third-party-refresh"' in response.text
    assert 'id="workspace-openclaw-start"' in response.text
    assert 'id="workspace-openclaw-restart"' in response.text
    assert '>重启</button></div>' in response.text
    assert 'id="workspace-openclaw-stop"' not in response.text
    assert 'id="workspace-openclaw-bind-weixin"' in response.text
    assert 'id="workspace-openclaw-weixin-dialog"' in response.text
    assert 'id="workspace-chub-message"' not in response.text
    assert 'id="workspace-worker-message"' not in response.text
    assert 'id="workspace-openclaw-message"' not in response.text
    assert 'id="workspace-openclaw-weixin-feedback"' not in response.text
    assert script.status_code == 200
    assert 'elements.openclawStart.hidden = !gatewayStopped;' in script.text
    assert 'elements.openclawRestart.hidden = !gatewayRestartable;' in script.text
    assert "openclawStop" not in script.text
    assert '"正在重启与恢复 OpenClaw Gateway，并确认 Gateway 与消息通道最终状态。"' in script.text
    assert 'closeOnConfirm: true,' in script.text
    assert 'onConfirm: () => controlOpenClaw("restart"),' in script.text
    assert "OpenClaw Gateway 已完成重启与恢复检查。" not in script.text
    assert "正在检查固定插件、补丁和运行状态。" not in script.text
    assert '? "Gateway 运行正常并已通过连接探测。"' in script.text
    assert 'const thirdPartySnapshotCacheKey = "chub.workspace.thirdParty.v1";' in script.text
    assert 'const developmentSnapshotCacheKey = "chub.workspace.development.v1";' in script.text
    assert 'const refreshDevelopment = async () =>' in script.text
    assert 'request("/api/runtime-modules/builtin-dev/refresh", { method: "POST" })' in script.text
    assert 'request("/api/settings/weixin-task-orchestration", {' in script.text
    assert 'body: JSON.stringify({ implementation: "weixin-orchestration-dev" }),' in script.text
    assert 'void loadDevelopment();' in script.text
    assert 'window.sessionStorage.getItem(thirdPartySnapshotCacheKey)' in script.text
    assert 'window.sessionStorage.setItem(' in script.text
    assert script.text.count('cacheThirdPartySnapshot(status, login);') == 2
    assert 'let thirdPartyLoading = false;' in script.text
    assert 'elements.thirdPartyRefresh.disabled = thirdPartyLoading;' in script.text
    assert 'elements.openclawBindWeixin.disabled = thirdPartyLoading ||' in script.text
    assert 'const showToolbarFeedback = (text, kind = "error") =>' in script.text
    assert 'window.showWorkspaceToolbarFeedback?.(text, kind);' in script.text
    assert "chubMessage" not in script.text
    assert "workerMessage" not in script.text
    assert "openclawMessage" not in script.text
    assert "openclawWeixinFeedback" not in script.text
    assert 'if (!await loadThirdParty()) return;' not in script.text
    assert 'return `${platform === "macos" ? "macOS" : platform} · Chub 可用`;' in script.text
    assert 'const workbenchStatusLoadingMinimumMs = 220;' in script.text
    assert 'workbenchStatusLoadingMinimumMs - (window.performance.now() - refreshStartedAt)' in script.text
    assert "const requestAbortController = new AbortController();" in script.text
    assert "const cancelPendingWaits = () =>" in script.text
    assert "requestAbortController.abort();" in script.text
    assert "cancelPendingWaits();" in script.text


@pytest.mark.anyio
async def test_automation_section_uses_workstation_status_rows(
    settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = create_app(settings)
    app.state.automation_manager.list = MagicMock(
        return_value=AutomationListData(
            enabled=True,
            browser_state="stopped",
            browser_message="Debug Chrome 未启动，按需启动。",
            browser_profile_name="Default",
            browser_mode="无界面",
            browser_profiles=[
                BrowserProfilePublic(
                    id="default",
                    name="Default",
                    initialized=True,
                    source_available=True,
                    active=False,
                )
            ],
            feishu_environment=FeishuEnvironmentState(
                state="login_required",
                message="飞书登录已失效，请重新登录。",
                checked_at=datetime(2026, 9, 5, 12, 30, tzinfo=timezone.utc),
                login_page_available=True,
            ),
            codex_runtime_account=RuntimeAccountEnvironmentState(
                state="available",
                auth_mode="api",
                message="API Key 模式已启用",
                checked_at=datetime(2026, 9, 5, 12, 31, tzinfo=timezone.utc),
                login_page_available=True,
            ),
            enabled_count=2,
            tasks=[
                AutomationTaskPublic(
                    id="weekly-report",
                    name="周报资料",
                    title="周报资料准备",
                    description="下载本期资料",
                    enabled=True,
                    reporting_period="2026-08-31至2026-09-06",
                    main_document_name="V 国内业务周报",
                    state=AutomationState(
                        task_id="weekly-report",
                        status="running",
                        message="正在下载主周报及关联文档",
                    ),
                ),
                AutomationTaskPublic(
                    id="monthly-report",
                    name="月报资料",
                    title="月报资料准备",
                    description="下载本月资料",
                    enabled=True,
                    state=AutomationState(
                        task_id="monthly-report",
                        status="failed",
                        message="飞书登录状态已失效",
                    ),
                ),
            ],
        )
    )
    period = "2026-08-31至2026-09-06"
    monkeypatch.setattr(
        web_routes,
        "list_latest_weekly_reports",
        lambda: [
            weekly_report_service.WeeklyReportView(
                period=period,
                report_type="focus",
                title="本期工作重点确认清单",
                summary="重点范围与取舍确认",
                status="可查看",
                updated_at=None,
                available=True,
            ),
            weekly_report_service.WeeklyReportView(
                period=period,
                report_type="report",
                title="本期业务周报",
                summary="各端进展汇总",
                status="待生成",
                updated_at=None,
                available=False,
            ),
        ],
    )
    monkeypatch.setattr(web_routes, "weekly_report_focus_confirmed", lambda _: False)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/?section=automations")
        workspace_script = await client.get("/static/workspace.js")
        stylesheet = await client.get("/static/css/components.css")

    assert response.status_code == 200
    assert 'aria-label="自动化总览"' in response.text
    assert 'aria-label="自动化详情"' in response.text
    assert "管理 Debug Chrome 与浏览器用户。" in response.text
    assert "检查自动化所需账户的当前登录状态。" in response.text
    assert "查看已配置任务的当前执行状态。" in response.text
    assert "自动化任务</span>" in response.text
    assert "进行中</span>" in response.text
    assert "需处理</span>" in response.text
    assert "2 / 2" in response.text
    assert "1 项" in response.text
    assert 'class="workspace-preview-panel workstation-card workspace-automation-details workspace-preview-work-section"' in response.text
    assert 'class="workstation-group automation-environment"' in response.text
    assert 'class="workstation-group automation-account-environment"' in response.text
    assert 'id="workspace-automation-browser-start"' in response.text
    assert 'id="workspace-automation-feishu-check"' in response.text
    assert 'id="workspace-automation-feishu-open-login" class="button-secondary" type="button" disabled title="请等待当前自动化任务完成">打开登录页面</button>' in response.text
    assert 'id="workspace-automation-feishu-detail"' in response.text
    assert 'id="workspace-automation-codex-account-check"' in response.text
    assert 'id="workspace-automation-codex-account-switch"' in response.text
    assert 'id="workspace-automation-codex-account-open-login" class="button-secondary" type="button" disabled title="请等待当前自动化任务完成">打开登录页面</button>' in response.text
    assert 'id="workspace-automation-codex-account-detail"' in response.text
    assert 'id="workspace-automation-codex-account-switch-dialog"' in response.text
    assert 'id="workspace-automation-codex-account-switch-form"' in response.text
    assert 'id="workspace-automation-codex-account-switch-feedback"' in response.text
    assert 'id="workspace-automation-codex-account-switch-current"' in response.text
    assert 'id="workspace-automation-codex-account-switch-flow-list"' in response.text
    assert "切换 Codex Runtime 账户" in response.text
    assert 'name="workspace-automation-codex-account-mode"' not in response.text
    assert 'data-browser-state="stopped"' in response.text
    assert 'data-account-state="login_required"' in response.text
    assert 'data-account-state="available"' in response.text
    assert "Debug Chrome 未启动，按需启动。 · 浏览器用户：Default · 无界面" in response.text
    assert 'id="workspace-automation-browser-message"' not in response.text
    assert "飞书登录已失效，请重新登录。 · 检查于 09-05 12:30" in response.text
    assert "API Key 模式已启用 · 检查于 09-05 12:31" in response.text
    assert 'id="workspace-automation-feishu-message"' not in response.text
    assert "周报资料准备 · 2026-08-31至2026-09-06" in response.text
    assert 'class="workstation-weekly-workflow"' in response.text
    assert "下载本期资料" in response.text
    assert "生成工作重点确认清单" in response.text
    assert "生成正式周报" in response.text
    assert "正在下载主周报及关联文档" in response.text
    assert "重点确认清单已生成，待维护者确认" in response.text
    assert "等待重点确认完成" in response.text
    assert 'href="/weekly-reports/2026-08-31%E8%87%B32026-09-06/focus"' in response.text
    assert ">查看文档</a>" in response.text
    assert 'data-weekly-report-stage="focus">重新运行</button>' in response.text
    assert 'href="/weekly-reports/2026-08-31%E8%87%B32026-09-06/report"' not in response.text
    assert 'class="button-secondary workspace-weekly-report-confirm-and-run"' in response.text
    assert ">确认并生成正式周报</button>" in response.text
    assert "正在执行 · 2026-08-31至2026-09-06" not in response.text
    assert "飞书登录状态已失效" in response.text
    assert "从飞书 Wiki 下载 Markdown 归档。" not in response.text
    assert response.text.count('class="button-secondary workspace-automation-run"') == 2
    assert 'data-automation-task-id="weekly-report"' in response.text
    assert 'data-automation-task-id="monthly-report"' in response.text
    assert 'title="该自动化任务正在执行"' in response.text
    assert 'id="workspace-automation-feishu-check" class="button-secondary" type="button" disabled title="请先启动 Debug Chrome"' in response.text
    assert response.text.count('title="请等待当前自动化任务完成"') == 2
    assert 'title="请先启动 Debug Chrome"' in response.text
    assert 'data-automation-task-message' not in response.text
    assert workspace_script.status_code == 200
    assert stylesheet.status_code == 200
    assert ".workspace-automation-details > .workstation-group > .workstation-status-list > .workstation-weekly-workflow {" in stylesheet.text
    assert "border: 0;" in stylesheet.text
    assert 'showConfirmationDialog({' in workspace_script.text
    assert '`/api/automations/${encodeURIComponent(taskId)}/run`' in workspace_script.text
    assert 'setWorkstationStatus(taskDetail, "任务已受理，正在刷新状态。", "warning");' in workspace_script.text
    assert '".workspace-weekly-report-view-session"' in workspace_script.text
    assert "window.selectWorkspaceQuickSession?.(sessionId);" in workspace_script.text
    assert "window.openWorkspaceQuickSession({ id: sessionId, title });" in workspace_script.text
    assert "window.location.assign(`/?session=${encodeURIComponent(sessionId)}`);" in workspace_script.text
    assert '".workspace-weekly-report-confirm-and-run"' in workspace_script.text
    assert '"/api/weekly-reports/current/report/confirm-and-run"' in workspace_script.text
    assert "setAutomationBrowserMessage" not in workspace_script.text
    assert "setAutomationFeishuMessage" not in workspace_script.text
    assert '"/api/automations/environment/feishu/login-page"' in workspace_script.text
    assert '"/api/automations/environment/codex/login-page"' in workspace_script.text
    assert '"/api/automations/environment/codex/switch-authentication"' in workspace_script.text
    assert 'let switchTargetMode = "";' in workspace_script.text
    assert 'switchTargetMode = currentMode === "account"' in workspace_script.text
    assert 'input[name="workspace-automation-codex-account-mode"]' not in workspace_script.text
    assert 'automationCodexAccountOpenLogin.hidden = state?.login_page_available !== true;' in workspace_script.text
    assert 'return match ? ` · 检查于 ${match[1]}-${match[2]} ${match[3]}:${match[4]}` : "";' in workspace_script.text
    assert 'const setAutomationAccountStatus = (detail, state, statusKind, fallbackMessage) => {' in workspace_script.text
    assert 'setAutomationAccountStatus(\n      automationCodexAccountDetail,' in workspace_script.text
    assert 'data-automation-refresh-active="' in response.text
    assert 'const refreshWorkspaceAutomations = async () =>' in workspace_script.text
    assert 'fetch("/?section=automations", {' in workspace_script.text
    assert 'currentSurface.replaceWith(nextSurface);' in workspace_script.text
    assert 'document.hidden ? 5_000 : 1_500' in workspace_script.text
    assert 'automationRefreshRequest?.abort();' in workspace_script.text
    assert 'document.removeEventListener("visibilitychange", automationVisibilityListener);' in workspace_script.text
    assert "badge-success" not in response.text
    assert "workspace-automation-task-list" not in response.text
    assert "查看自动化环境和已配置任务的当前执行状态。" not in response.text

    monkeypatch.setattr(
        web_routes,
        "list_latest_weekly_reports",
        lambda: [
            weekly_report_service.WeeklyReportView(
                period=period,
                report_type="focus",
                title="本期工作重点确认清单",
                summary="重点范围与取舍确认",
                status="可查看",
                updated_at=datetime(2026, 9, 5, 12, 35, tzinfo=timezone.utc),
                available=True,
            ),
            weekly_report_service.WeeklyReportView(
                period=period,
                report_type="report",
                title="本期业务周报",
                summary="各端进展汇总",
                status="可查看",
                updated_at=datetime(2026, 9, 5, 12, 20, tzinfo=timezone.utc),
                available=True,
            ),
        ],
    )
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        stale_report_response = await client.get("/?section=automations")

    assert "正式周报基于旧确认清单，需重新生成" in stale_report_response.text
    assert 'href="/weekly-reports/2026-08-31%E8%87%B32026-09-06/report">查看旧文档</a>' in stale_report_response.text
    assert ">确认并生成正式周报</button>" in stale_report_response.text

    app.state.weekly_report_generation.read_current = MagicMock(
        return_value={
            "focus": type(
                "GenerationStep",
                (),
                {
                    "session_id": "weekly-session-1",
                    "status": "failed",
                    "message": "生成会话未完成，请查看会话结果。",
                },
            )(),
            "report": type(
                "GenerationStep",
                (),
                {"session_id": None, "status": "idle", "message": "等待前序步骤完成"},
            )(),
        }
    )
    monkeypatch.setattr(
        web_routes,
        "list_latest_weekly_reports",
        lambda: [
            weekly_report_service.WeeklyReportView(
                period=period,
                report_type="focus",
                title="本期工作重点确认清单",
                summary="重点范围与取舍确认",
                status="可查看",
                updated_at=None,
                available=True,
            ),
            weekly_report_service.WeeklyReportView(
                period=period,
                report_type="report",
                title="本期业务周报",
                summary="各端进展汇总",
                status="待生成",
                updated_at=None,
                available=False,
            ),
        ],
    )
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        session_response = await client.get("/?section=automations")

    assert 'class="button-secondary workspace-weekly-report-view-session"' in session_response.text
    assert 'data-weekly-report-session-id="weekly-session-1"' in session_response.text
    assert 'href="/weekly-reports/2026-08-31%E8%87%B32026-09-06/focus">查看文档</a>' in session_response.text
    assert 'data-weekly-report-stage="focus">重新运行</button>' in session_response.text
    assert 'href="/codex/weekly-session-1/quick-interactions/conversation"' not in session_response.text

    app.state.weekly_report_generation.read_current = MagicMock(
        return_value={
            "focus": type(
                "GenerationStep",
                (),
                {
                    "session_id": None,
                    "status": "failed",
                    "message": "生成会话记录不可用",
                },
            )(),
            "report": type(
                "GenerationStep",
                (),
                {"session_id": None, "status": "idle", "message": "等待前序步骤完成"},
            )(),
        }
    )
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        unavailable_session_response = await client.get("/?section=automations")

    assert 'data-weekly-report-session-id="weekly-session-1"' not in unavailable_session_response.text
    assert 'href="/weekly-reports/2026-08-31%E8%87%B32026-09-06/focus">查看文档</a>' in unavailable_session_response.text

    app.state.weekly_report_generation.read_current = MagicMock(
        return_value={
            "focus": type(
                "GenerationStep",
                (),
                {"session_id": None, "status": "idle", "message": "等待前序步骤完成"},
            )(),
            "report": type(
                "GenerationStep",
                (),
                {"session_id": None, "status": "idle", "message": "等待前序步骤完成"},
            )(),
        }
    )
    monkeypatch.setattr(
        web_routes,
        "list_latest_weekly_reports",
        lambda: [
            weekly_report_service.WeeklyReportView(
                period=period,
                report_type="focus",
                title="本期工作重点确认清单",
                summary="重点范围与取舍确认",
                status="待生成",
                updated_at=None,
                available=False,
            ),
            weekly_report_service.WeeklyReportView(
                period=period,
                report_type="report",
                title="本期业务周报",
                summary="各端进展汇总",
                status="待生成",
                updated_at=None,
                available=False,
            ),
        ],
    )
    monkeypatch.setattr(web_routes, "weekly_report_inputs_available", lambda _: False)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        blocked_response = await client.get("/?section=automations")

    assert "等待资料完整发布" in blocked_response.text
    assert (
        'data-weekly-report-stage="focus" disabled '
        'title="请先完成资料下载并发布完整 Manifest 输入"'
    ) in blocked_response.text


@pytest.mark.anyio
async def test_home_page_uses_configured_page_title(settings: Settings) -> None:
    settings.app.page_title = "Ubuntu · Hub"
    transport = httpx.ASGITransport(app=create_app(settings))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/")

    assert "<title>Ubuntu · Hub</title>" in response.text
    assert 'class="workspace-preview-page"' in response.text


@pytest.mark.anyio
async def test_project_documents_workspace_reports_design_document_index_error(
    settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "app.services.design_documents.DOCUMENTS_INDEX",
        Path("/missing/design_documents.json"),
    )
    transport = httpx.ASGITransport(app=create_app(settings))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/?section=project-docs")
        api_response = await client.get("/api/project-docs")

    assert response.status_code == 200
    assert "项目资料暂时无法加载，请检查资料索引。" in response.text
    assert api_response.status_code == 503
    assert api_response.json()["error"]["code"] == "project_document_index_unavailable"


@pytest.mark.anyio
async def test_removed_task_api_is_not_available(settings: Settings) -> None:
    transport = httpx.ASGITransport(app=create_app(settings))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        list_response = await client.get("/api/tasks")
        run_response = await client.post("/api/tasks/run", json={})

    assert list_response.status_code == 404
    assert run_response.status_code == 404


@pytest.mark.anyio
async def test_home_page_title_uses_application_name_by_default(
    settings: Settings,
) -> None:
    transport = httpx.ASGITransport(app=create_app(settings))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/")

    assert "<title>Hub</title>" in response.text




@pytest.mark.anyio
async def test_quick_interaction_conversation_page_is_available(
    settings: Settings,
) -> None:
    app = create_app(settings)
    manager = MagicMock()
    manager.require_session_access.return_value = MagicMock()
    app.state.ai_session_manager = manager
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        removed_page = await client.get("/codex/session-1/quick-interactions")
        page = await client.get(
            "/codex/session-1/quick-interactions/conversation"
        )
        embedded_page = await client.get(
            "/codex/session-1/quick-interactions/conversation?embedded=workspace"
        )
        session_script = await client.get("/static/quick_interaction_session.js")
        timeline_script = await client.get("/static/quick_interaction_timeline.js")
        core_script = await client.get("/static/quick_interactions_core.js")
        script = await client.get("/static/quick_interaction_conversation.js")
        ui_script = await client.get("/static/js/components/ui.js")
        stylesheet = await client.get("/static/css/components.css")

    assert removed_page.status_code == 404
    assert page.status_code == 200
    assert embedded_page.status_code == 200
    assert "frame-ancestors 'self'" in page.headers["content-security-policy"]
    assert "frame-ancestors 'none'" not in page.headers["content-security-policy"]
    assert 'data-session-id="session-1"' in page.text
    assert 'class="conversation-body conversation-body-embedded"' in embedded_page.text
    assert "Session Conversation" not in page.text
    assert 'class="conversation-header"' not in page.text
    assert 'id="conversation-scroll"' in page.text
    assert 'id="conversation-feed"' in page.text
    assert 'id="conversation-load-earlier"' in page.text
    assert 'id="conversation-jump-latest"' in page.text
    assert 'id="conversation-form"' in page.text
    assert 'id="conversation-session-navigation"' in page.text
    assert 'id="conversation-session-create"' in page.text
    assert 'id="conversation-session-switcher"' in page.text
    assert 'id="conversation-session-title"' in page.text
    assert 'id="conversation-session-rename"' in page.text
    assert 'id="conversation-session-stop"' in page.text
    assert 'id="conversation-session-archive"' in page.text
    assert 'id="conversation-session-delete"' in page.text
    assert 'id="conversation-submit-message"' not in page.text
    assert 'id="conversation-rename-dialog"' in page.text
    assert 'id="conversation-rename-input"' in page.text
    assert 'id="conversation-archive-dialog"' in page.text
    assert 'id="conversation-archive-confirm"' in page.text
    assert 'id="conversation-delete-dialog"' in page.text
    assert 'id="conversation-delete-confirm"' in page.text
    assert 'class="codex-workspace-dialog confirmation-dialog conversation-archive-dialog"' in page.text
    assert 'class="confirmation-dialog-description"' in page.text
    assert 'id="conversation-create-dialog"' in page.text
    assert 'id="conversation-create-form" class="codex-workspace-dialog-surface workspace-session-create-form"' in page.text
    assert 'id="conversation-create-workspaces" type="hidden"' in page.text
    assert 'id="conversation-create-workspaces-trigger" class="settings-choice-picker-trigger"' in page.text
    assert 'id="conversation-create-confirm" class="button-secondary" type="submit">创建</button>' in page.text
    assert page.text.index('id="conversation-session-create"') < page.text.index(
        'id="conversation-session-switcher"'
    )
    assert page.text.index('id="conversation-session-switcher"') < page.text.index(
        'id="conversation-session-title"'
    ) < page.text.index('id="conversation-prompt"')
    assert page.text.index('id="conversation-session-title"') < page.text.index(
        'id="conversation-session-rename"'
    ) < page.text.index('id="conversation-session-stop"') < page.text.index(
        'id="conversation-session-archive"'
    ) < page.text.index('id="conversation-session-delete"')
    assert 'id="conversation-engine"' not in page.text
    assert 'id="conversation-more"' not in page.text
    assert 'id="conversation-submit" class="conversation-composer-control conversation-submit" type="submit"' in page.text
    assert '<svg viewBox="0 0 24 24"' in page.text
    assert "conversation-setting-label" not in page.text
    assert 'id="conversation-runtime-version"' not in page.text
    assert 'id="conversation-permission-trigger"' in page.text
    assert 'id="conversation-permission-menu" class="conversation-setting-menu"' in page.text
    assert 'id="conversation-model-trigger"' in page.text
    assert 'id="conversation-reasoning-trigger"' in page.text
    assert (
        page.text.index("/static/js/components/ui.js")
        < page.text.index("/static/quick_interactions_core.js")
        < page.text.index("/static/quick_interaction_session.js")
        < page.text.index("/static/quick_interaction_timeline.js")
        < page.text.index("/static/quick_interaction_conversation.js")
    )
    assert session_script.status_code == 200
    assert timeline_script.status_code == 200
    assert core_script.status_code == 200
    assert script.status_code == 200
    assert "loadConversationRuntimeVersions" not in script.text
    assert "implementationId:" not in script.text
    assert "implementation_id: implementationId" not in core_script.text
    assert ui_script.status_code == 200
    assert 'order: "timeline"' in script.text
    assert "CONVERSATION_PAGE_SIZE = readConversationPageSize()" in script.text
    assert "quickSessionPermissionOptions: conversationPermissionOptions" in script.text
    assert "quickSessionModelOptions" in core_script.text
    assert "before: { createdAt: oldest.created_at, id: oldest.id }" in script.text
    assert "performLoadEarlierConversation(generation, client)" in script.text
    assert "conversationPollDelay(conversationPollFailureCount)" in script.text
    assert "sessions: sessionContextResult.status === \"fulfilled\"" in script.text
    assert "resizeConversationPrompt" in script.text
    assert "updateConversationComposerActions" not in script.text
    assert "setConversationMoreExpanded" not in script.text
    assert "conversationSelectedEngine" not in script.text
    assert "isConversationNearBottom" in script.text
    assert 'event.key === "Enter"' in script.text
    assert "if (!conversationSubmit.disabled)" in script.text
    assert "canSubmitConversation" in script.text
    assert "conversationEngine" not in script.text
    assert '.workspace-preview-main.is-showing-quick-session' in stylesheet.text
    assert '.conversation-body-embedded main' in stylesheet.text
    assert '.conversation-body-embedded .conversation-page' in stylesheet.text
    assert 'padding-bottom: max(1.25rem, env(safe-area-inset-bottom));' in stylesheet.text
    assert "conversationTimelineView.restoreTopAnchor(anchor)" in script.text
    assert "client.submitTask" in script.text
    assert "client.loadSessionContext" in script.text
    assert "conversationClient.createSession" in script.text
    assert "readConversationSessionCreationPreferences" not in script.text
    assert "shouldRetryConversationCreationWithDefaults" not in script.text
    assert "clearConversationSessionModelPreferences" not in script.text
    assert "updateSessionConfiguration" in script.text
    assert "showConversationFeedback" in script.text
    assert "sessionId: conversationSessionId" in script.text
    assert "window.showChubToast" in ui_script.text
    assert "conversationSessionView.openCreate" in script.text
    assert 'label: "跟随模型默认"' in core_script.text
    assert "quickSessionReasoningOptions" in core_script.text
    assert 'const usableWorkspaces = workspaces.filter((workspace) => workspace.available);' in session_script.text
    assert 'elements.createWorkspacePicker.setOptions(' in session_script.text
    assert 'elements.createForm.onsubmit = (event) => {' in session_script.text
    assert '.workspace-session-choice-picker .settings-choice-picker-menu {' in stylesheet.text
    assert '.workspace-session-create-description {\n  margin: 0;\n  color: var(--color-text-muted);\n  line-height: 1.45;' in stylesheet.text
    assert "conversationCreationPending" in script.text
    assert "renderConversationSessionCreation(sessionContextResult.value)" in script.text
    assert "switchConversationSession(" in script.text
    assert "buildSwitcher" in session_script.text
    assert 'text: `${label} · ${status}`' in session_script.text
    assert "core.sessionSwitcherStatus" in session_script.text
    assert "core.sessionSwitcherLabels" in session_script.text
    assert "function sessionDisplayTitle(session)" in session_script.text
    assert 'return workspaceName || "未命名 Session"' in session_script.text
    assert 'documentTitle: `${displayTitle} · 快速交互`' in session_script.text
    assert "client.renameSession(title)" in script.text
    assert "conversationSessionView.openRename" in script.text
    assert "conversationRenamePending" in script.text
    assert "conversationSessionView.setRenamePending(true)" in script.text
    assert "client.archiveSession()" in script.text
    assert "client.stopSession()" in script.text
    assert "client.deleteSession()" in script.text
    assert "conversationSessionView.openArchive" in script.text
    assert "conversationSessionView.openDelete" in script.text
    assert "firstConversationSessionAfterArchive" in script.text
    assert "conversationSessions = sessions" in script.text
    assert "window.location.replace(nextSessionUrl)" in script.text
    assert '"/api/ai/usage"' not in script.text
    assert "loadConversationQuotaRain" not in script.text
    assert "conversationSessionUrl(nextSession.id)" in script.text
    assert ': "/";' in script.text
    assert "const archiveReady = Boolean(session.can_archive)" in session_script.text
    assert "elements.archive.disabled = !state.archiveReady || state.archiveBusy" in session_script.text
    assert 'session?.workspace_id !== "weixin-translation"' in session_script.text
    assert "elements.rename.disabled = !state.renameAllowed" in session_script.text
    assert "core.sessionNavigationMode" in session_script.text
    assert 'button.setAttribute("aria-current", "page")' in session_script.text
    assert "handleConversationSessionSwitch" in script.text
    assert "button.dataset.sessionId" in session_script.text
    assert "button.dataset.sessionUrl" in session_script.text
    assert 'window.history.replaceState(window.history.state, "", historyUrl)' in script.text
    assert "window.location.reload()" not in script.text
    assert "resetConversationSessionView" in script.text
    assert "renderConversationSessionPreview" in script.text
    assert 'elements.titleRow.setAttribute("aria-busy", "true")' in session_script.text
    assert 'elements.titleRow.removeAttribute("aria-busy")' in session_script.text
    assert "elements.titleRow.hidden = true" not in session_script.text
    assert "conversationGeneration += 1" in script.text
    assert "generation !== conversationGeneration" in script.text
    assert "document.body.dataset.sessionId = sessionId" in script.text
    assert 'window.open(request.url, "_blank", "noopener")' in script.text
    assert 'addEventListener("auxclick", handleConversationSessionSwitch)' in script.text
    assert 'document.createElement("a")' not in script.text
    assert 'documentRef.createElement("button")' in session_script.text
    assert 'request.mode === "new-tab"' in script.text
    assert 'request.mode === "default"' in script.text
    assert 'request.mode === "ignore"' in script.text
    assert "hub.quickInteractionSessionNumbers.v1" not in session_script.text
    assert "elements.switcher.hidden = state.items.length === 0" in session_script.text
    assert "hub.quickInteractionDraft.v1" in script.text
    assert "sessionStorage.setItem(conversationDraftKey" in script.text
    assert 'const submitLabel = state.submissionReason || "发送";' in session_script.text
    assert 'elements.submit.setAttribute("aria-label", submitLabel);' in session_script.text
    assert 'elements.submit.textContent = "发送"' not in session_script.text
    assert '"确认发送"' not in script.text
    assert 'pending: "待通知"' in timeline_script.text
    assert 'sent: "已通知"' in timeline_script.text
    assert 'failed: "通知失败"' in timeline_script.text
    assert 'skipped: "未通知"' in timeline_script.text
    assert 'succeeded: "Chub 已完成自动重启，服务已恢复。"' in timeline_script.text
    assert "Chub 已完成自动重启，服务已恢复。" in timeline_script.text
    assert 'task.deferred_restart_status === "pending"' in script.text
    assert 'failed: "重启结果通知失败"' in timeline_script.text
    assert "Chub 自动重启未完成" in timeline_script.text
    assert "task.deferred_restart_error" in timeline_script.text
    assert "旧记录没有保存具体原因，请查看 Chub 运行日志" in timeline_script.text
    assert ".conversation-assistant-info" in stylesheet.text
    assert "client.setPinned" not in script.text
    assert "conversation-pin" not in timeline_script.text
    assert "onTogglePinned" not in timeline_script.text
    assert "textContent" in session_script.text
    assert "textContent" in timeline_script.text
    assert "innerHTML" not in session_script.text
    assert "innerHTML" not in timeline_script.text
    assert "function focusConversationPromptAfterSessionAction()" in script.text
    assert "conversationPrompt.focus({ preventScroll: true });" in script.text
    assert "fetch(" not in session_script.text
    assert "fetch(" not in timeline_script.text
    assert "conversationTasks =" not in timeline_script.text
    assert "conversationSession =" not in session_script.text
    assert ".conversation-page" in stylesheet.text
    assert "width: min(100%, 1080px);" in stylesheet.text
    assert "width: min(90%, 46rem);" not in stylesheet.text
    assert "width: 90%;" in stylesheet.text
    assert "max-width: 1080px;" in stylesheet.text
    assert "width: min(100%, 720px);" not in stylesheet.text
    assert ".conversation-message-user" in stylesheet.text
    assert ".conversation-composer" in stylesheet.text
    assert ".conversation-composer {\n  position: relative;\n  z-index: 2;\n  display: grid;\n  gap: 0.35rem;" in stylesheet.text
    assert ".conversation-session-switcher" in stylesheet.text
    assert ".conversation-session-navigation" in stylesheet.text
    assert "grid-template-columns: 30px minmax(0, 1fr);" in stylesheet.text
    assert ".conversation-session-create" in stylesheet.text
    assert ".workspace-session-create-form" in stylesheet.text
    assert ":not(.conversation-session-create)" not in stylesheet.text
    assert ":not(.conversation-session-switch)" not in stylesheet.text
    assert "overflow-x: auto;" in stylesheet.text
    assert "overscroll-behavior-x: contain;" in stylesheet.text
    assert "padding: 0.05rem 0.05rem 0.1rem;" in stylesheet.text
    assert ".conversation-session-switch.is-current" in stylesheet.text
    assert ".conversation-session-title" in stylesheet.text
    assert ".conversation-session-title {\n  display: inline;" in stylesheet.text
    assert ".conversation-session-title-row {\n  display: block;\n  min-height: 2rem;" in stylesheet.text
    assert ".conversation-session-rename" in stylesheet.text
    assert ".conversation-session-archive" in stylesheet.text
    assert ".conversation-session-stop" in stylesheet.text
    assert ".conversation-session-delete" in stylesheet.text
    assert "margin-left: 0.35rem;" in stylesheet.text
    assert ".conversation-rename-form" in stylesheet.text
    assert "conversation-session-archive:not(:disabled):hover" in stylesheet.text
    assert ":not(.conversation-session-rename)" not in stylesheet.text
    assert ":not(.conversation-session-archive)" not in stylesheet.text
    assert ":not(.site-header-title):not(.session-enter)::before" not in stylesheet.text
    assert 'a.button-link::before' not in stylesheet.text
    assert ':root[data-ui-style="code-dark"] .workspace-button strong' not in stylesheet.text
    assert ".workspace-button {\n  height: auto;" in stylesheet.text
    assert "padding: 0.75rem 0.85rem;" in stylesheet.text
    assert ".session-enter {\n  grid-column: 1 / -1;\n  height: auto;" in stylesheet.text
    assert "padding: 0.75rem 0.85rem;" in stylesheet.text
    assert "font-size: 0.875rem;" in stylesheet.text
    assert ".conversation-composer-toolbar" in stylesheet.text
    assert ".conversation-setting-menu" in stylesheet.text
    assert ".conversation-setting-option:disabled" in stylesheet.text
    assert ".conversation-submit" in stylesheet.text


@pytest.mark.anyio
async def test_log_details_page_and_script_are_available(settings: Settings) -> None:
    transport = httpx.ASGITransport(app=create_app(settings))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        page = await client.get("/logs")
        script = await client.get("/static/logs.js")

    assert page.status_code == 200
    assert 'id="detail-log-source"' in page.text
    assert 'value="worker-operations"' in page.text
    assert "返回首页" not in page.text
    assert "加载更早" in page.text
    assert script.status_code == 200
    assert "/api/logs/page" in script.text
    assert "/api/logs/download" in script.text
    assert "innerHTML" not in script.text


@pytest.mark.anyio
async def test_automation_details_page_has_been_removed(
    settings: Settings,
) -> None:
    transport = httpx.ASGITransport(app=create_app(settings))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        page = await client.get("/automations")

    assert page.status_code == 404


@pytest.mark.anyio
async def test_design_document_pages_render_markdown(settings: Settings) -> None:
    transport = httpx.ASGITransport(app=create_app(settings))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        home = await client.get("/?section=project-docs")
        listing = await client.get("/project-docs")
        detail = await client.get("/project-docs/automation-download")
        project_readme = await client.get("/project-docs/project-readme")
        missing = await client.get("/project-docs/not-registered")

    assert listing.status_code == 200
    assert "项目说明、设计方案与维护文档" in listing.text
    assert "份项目资料" in listing.text
    assert "Hub 项目资料展示" in listing.text
    assert 'id="confirmation-dialog"' in listing.text
    assert listing.text.index('/static/js/components/ui.js') < listing.text.index(
        '/static/design_documents.js'
    )
    assert "Chub 项目说明" in listing.text
    assert 'href="/project-docs/project-readme"' in listing.text
    assert home.text.index('href="/project-docs/project-readme"') < home.text.index(
        'href="/project-docs/chub-architecture"'
    )
    assert listing.text.index('href="/project-docs/project-readme"') < listing.text.index(
        'href="/project-docs/chub-architecture"'
    )
    assert '<span class="badge badge-success">持续维护</span>' in listing.text
    assert project_readme.status_code == 200
    assert "面向个人设备、本地优先的轻量 AI 工作站控制面" in project_readme.text
    assert 'href="/project-docs/chub-architecture"' in project_readme.text
    assert 'href="/project-docs/chub-integration-capabilities"' in project_readme.text
    assert 'href="/project-docs/ai-session-state"' in project_readme.text
    assert 'href="docs/CHUB_INTEGRATION_CAPABILITIES.md"' not in project_readme.text
    assert "docs/archive/phase-1/README.md" not in project_readme.text
    assert "本期工作周报自动化与生成设计" in listing.text
    assert "返回首页" not in listing.text
    assert "standalone-list-card" in listing.text
    assert 'target="_blank"' not in listing.text
    assert detail.status_code == 200
    assert "返回全部文档" not in detail.text
    assert "document-navigation" not in detail.text
    assert "document-updated" not in detail.text
    assert "Hub 设计文档只读展示" not in detail.text
    assert '<p class="eyebrow">设计文档</p>' not in detail.text
    assert '<span class="badge badge-success">已实现并验收</span>' not in detail.text
    assert '<article class="markdown-body">' in detail.text
    assert "<h2" in detail.text
    assert "阶段一：资料准备与发布" in detail.text
    assert "阶段二：重点确认与正式生成" in detail.text
    assert missing.status_code == 404

    for response in [listing, detail]:
        assert "default-src 'self'" in response.headers["content-security-policy"]
        assert response.headers["x-content-type-options"] == "nosniff"


@pytest.mark.anyio
async def test_project_document_card_and_weekly_report_apis_allow_loopback(
    settings: Settings,
    weekly_reports_root: Path,
) -> None:
    transport = httpx.ASGITransport(app=create_app(settings))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/api/project-docs")
        weekly_response = await client.get("/api/weekly-reports/current")

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["count"] >= 1
    assert "weekly_reports" not in data
    assert weekly_response.status_code == 200
    weekly_reports = weekly_response.json()["data"]["reports"]
    assert [item["report_type"] for item in weekly_reports] == [
        "focus",
        "report",
    ]
    assert weekly_reports[0]["available"] is True
    assert weekly_reports[1]["available"] is False
    assert len(data["documents"]) == 10
    assert any(document["status"] == "持续维护" for document in data["documents"])
    assert "openclaw-research" not in {
        document["id"] for document in data["documents"]
    }


@pytest.mark.anyio
async def test_weekly_report_card_api_requires_trusted_network(
    settings: Settings,
) -> None:
    transport = httpx.ASGITransport(
        app=create_app(settings),
        client=("192.0.2.1", 12345),
    )
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/api/weekly-reports/current")

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "trusted_network_required"


@pytest.mark.anyio
async def test_weekly_report_detail_is_public_and_missing_report_is_404(
    settings: Settings,
    weekly_reports_root: Path,
) -> None:
    transport = httpx.ASGITransport(app=create_app(settings))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        detail = await client.get(
            "/weekly-reports/2026-08-03%E8%87%B32026-08-09/focus"
        )
        pending = await client.get(
            "/weekly-reports/2026-08-03%E8%87%B32026-08-09/report"
        )
        unsafe = await client.get("/weekly-reports/not-a-period/focus")

    assert detail.status_code == 200
    assert '<article class="markdown-body">' in detail.text
    assert "本期工作重点确认清单" in detail.text
    assert pending.status_code == 404
    assert unsafe.status_code == 404


@pytest.mark.anyio
async def test_security_headers_apply_to_page_assets_and_api(
    settings: Settings,
) -> None:
    transport = httpx.ASGITransport(app=create_app(settings))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        page = await client.get("/")
        asset = await client.get("/static/app.js")
        api = await client.get("/api/health")

    for response in [page, asset, api]:
        assert response.headers["cache-control"] == "no-store"
        assert response.headers["x-content-type-options"] == "nosniff"
        assert response.headers["referrer-policy"] == "no-referrer"
    for response in [page, asset]:
        assert "default-src 'self'" in response.headers["content-security-policy"]
        assert "img-src 'self' data: blob:" in response.headers["content-security-policy"]
        assert "frame-ancestors 'none'" in response.headers["content-security-policy"]
    assert "content-security-policy" not in api.headers


@pytest.mark.anyio
async def test_security_headers_apply_to_unhandled_errors(
    settings: Settings,
) -> None:
    app = create_app(settings)

    @app.get("/test-error")
    def test_error() -> None:
        raise RuntimeError("test error")

    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/test-error")

    assert response.status_code == 500
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["referrer-policy"] == "no-referrer"
    assert response.json()["error"]["code"] == "internal_error"




@pytest.mark.anyio
async def test_workspace_chub_restart_refreshes_after_new_instance_is_confirmed(
    settings: Settings,
) -> None:
    transport = httpx.ASGITransport(app=create_app(settings))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/static/js/features/workspace-workstation.js")

    assert response.status_code == 200
    assert 'await waitForRestart(previous.instance_id);' in response.text
    assert '"Chub 已重启并恢复。浏览器将在稍后自动刷新页面。"' in response.text
    assert "elements.chubRestart.disabled = hubRestarting || upgradeRunning;" in response.text
    assert 'await waitForWorkerRestart(operationId);' in response.text
    assert '"Quick Worker 已重启并恢复。浏览器将在稍后自动刷新页面。"' in response.text
    assert "elements.workerRestart.disabled = workerRestarting || !workerState?.can_restart || upgradeRunning;" in response.text
    assert "workerRestarting || !workerIsCurrent" not in response.text
    assert 'request("/api/maintenance/quick-worker", { timeoutMs: 12000 })' in response.text
    assert "workerRetryDelay = Math.min(workerRetryDelay * 2, 10000);" in response.text
    assert "window.setTimeout(() => window.location.reload(), 2000);" in response.text
    assert 'unavailable: ["不可用", "远程访问", "warning"]' in response.text
    assert 'unknown: ["状态未知", "远程访问", "muted"]' in response.text
    assert "tailnetDetail" not in response.text
    assert "const setSummaryStatus = (target, text, kind = \"muted\") =>" in response.text
    assert "taskSummary" not in response.text
    assert "runtimeKind(data)," in response.text
    assert "setStatus(elements.upgradeDetail, `状态：${upgradeLabel(data)}。${data.message}`" in response.text


@pytest.mark.anyio
async def test_page_uses_external_script_only(settings: Settings) -> None:
    transport = httpx.ASGITransport(app=create_app(settings))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/")

    expected_scripts = [
        "/static/js/components/ui.js",
        "/static/workspace.js",
        "/static/js/features/workspace-sessions.js",
        "/static/js/features/workspace-workstation.js",
    ]
    assert response.text.count("<script") == len(expected_scripts) + 2
    assert '<script src="/static/workspace-bootstrap.js"></script>' in response.text
    assert '<script src="/static/theme.js"></script>' in response.text
    positions = [
        response.text.index('<script src="/static/workspace-bootstrap.js"></script>'),
        response.text.index('<script src="/static/theme.js"></script>'),
    ] + [
        response.text.index(f'<script src="{source}" defer></script>')
        for source in expected_scripts
    ]
    assert positions == sorted(positions)


@pytest.mark.anyio
async def test_native_session_without_a_title_is_marked_as_unavailable(
    settings: Settings,
) -> None:
    transport = httpx.ASGITransport(app=create_app(settings))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/static/js/features/workspace-sessions.js")

    assert response.status_code == 200
    assert 'return title || "标题暂未读取到";' in response.text
    assert '"未命名 Native Session"' not in response.text


@pytest.mark.anyio
async def test_api_documentation_is_not_public(settings: Settings) -> None:
    transport = httpx.ASGITransport(app=create_app(settings))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        responses = [
            await client.get("/docs"),
            await client.get("/redoc"),
            await client.get("/openapi.json"),
        ]

    for response in responses:
        assert response.status_code == 404
        assert response.json()["error"]["code"] == "not_found"
