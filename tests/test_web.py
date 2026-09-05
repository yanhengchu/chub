import asyncio
from datetime import datetime, timezone
import re
from pathlib import Path
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
from app.application import create_app
from app.codex.models import CodexSession, RuntimeManagementData, RuntimeManagementItem
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
@pytest.mark.skip(reason="旧首页已移除，由根路径工作台测试替代")
async def test_home_page_is_public_and_contains_no_credential_form(
    settings: Settings,
    weekly_reports_root: Path,
) -> None:
    transport = httpx.ASGITransport(app=create_app(settings))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/")

    assert response.status_code == 200
    assert 'type="password"' not in response.text
    assert 'src="/static/app.js"' in response.text
    assert 'src="/static/js/core/ai-usage.js"' in response.text
    assert 'src="/static/theme.js"' in response.text
    assert '<html lang="zh-CN" data-ui-style="standard">' in response.text
    assert '<meta name="color-scheme" content="light">' in response.text
    assert (
        '<meta name="viewport" content="width=device-width, initial-scale=1, '
        'viewport-fit=cover, interactive-widget=resizes-content">'
    ) in response.text
    assert 'id="codex-rename-dialog"' in response.text
    assert 'id="codex-rename-input"' in response.text
    expected_stylesheets = [
        "/static/css/tokens.css",
        "/static/css/base.css",
        "/static/css/components.css",
        "/static/css/responsive.css",
    ]
    stylesheet_positions = [
        response.text.index(f'<link rel="stylesheet" href="{href}">')
        for href in expected_stylesheets
    ]
    assert stylesheet_positions == sorted(stylesheet_positions)
    assert 'href="/static/app.css"' not in response.text
    assert 'id="connected-bar"' in response.text
    assert "更换凭证" not in response.text
    assert "清除凭证" not in response.text
    assert 'id="refresh-status"' not in response.text
    assert "节点任务" not in response.text
    assert 'id="codex-card-host"' in response.text
    assert 'id="openclaw-title"' not in response.text
    assert "OpenClaw 环境" not in response.text
    assert "<strong>OpenClaw Gateway</strong>" in response.text
    assert 'id="openclaw-badge"' not in response.text
    assert 'id="refresh-openclaw"' not in response.text
    assert 'id="openclaw-start"' in response.text
    assert 'id="openclaw-restart"' in response.text
    assert ">重启</button>" in response.text
    assert 'id="openclaw-stop"' in response.text
    assert 'id="openclaw-bind-weixin"' not in response.text
    assert 'id="openclaw-weixin-dialog"' not in response.text
    assert 'id="openclaw-access-open"' not in response.text
    assert 'id="openclaw-access-url"' not in response.text
    assert 'id="openclaw-access-unavailable"' not in response.text
    assert "远程访问未启用" not in response.text
    assert 'data-card-key="openclaw"' not in response.text
    assert 'id="clawbot-badge"' not in response.text
    assert 'id="clawbot-detail"' in response.text
    assert 'id="openclaw-gateway-badge"' not in response.text
    assert 'id="openclaw-channels"' not in response.text
    assert "访问入口" not in response.text
    assert 'class="openclaw-status-row openclaw-access"' not in response.text
    assert "微信通道" not in response.text
    assert 'id="openclaw-channels" class="badge badge-muted"' not in response.text
    assert 'id="openclaw-owner"' not in response.text
    assert 'id="openclaw-version"' not in response.text
    assert 'id="openclaw-service"' not in response.text
    assert 'id="openclaw-bind"' not in response.text
    assert 'id="openclaw-checked-at"' not in response.text
    assert 'id="automation-title"' in response.text
    assert 'id="automation-list"' in response.text
    assert 'id="automation-task-title" class="card-group-title"' in response.text
    assert 'id="automation-environment-title"' in response.text
    assert 'id="automation-environment-title" class="card-group-title"' in response.text
    assert "自动化环境" in response.text
    assert 'id="automation-environment-badge"' not in response.text
    assert 'id="refresh-automation-environment"' not in response.text
    assert 'id="automation-environment-message"' in response.text
    assert 'class="automation-feishu-panel"' in response.text
    assert 'class="automation-browser-panel"' in response.text
    assert "复用飞书登录状态执行自动化任务。" in response.text
    assert "查看 Chub 运行状态并进行必要维护。" in response.text
    assert 'data-card-key="automation-environment"' not in response.text
    assert 'data-card-key="core-capabilities" data-collapsible-card' in response.text
    assert 'data-card-key="core-capabilities" data-collapsible-card data-collapsed="true"' not in response.text
    assert 'data-card-key="third-party-services" data-collapsible-card' in response.text
    assert '<h2 id="core-capabilities-title">核心服务</h2>' in response.text
    assert '<h2 id="third-party-services-title">OpenClaw 与 ClawBot</h2>' in response.text
    assert "<strong>Chub</strong>" in response.text
    assert "<strong>Chub Quick Worker</strong>" in response.text
    assert "<strong>Chub Debug Chrome</strong>" in response.text
    assert "<strong>OpenClaw Gateway</strong>" in response.text
    assert 'id="refresh-core-capabilities"' in response.text
    assert 'id="refresh-third-party-services"' in response.text
    assert 'id="core-services-title" class="card-group-title"' in response.text
    assert response.text.count('class="workstation-status-row') == 6
    assert '<article><span>Tailnet</span><strong id="workspace-tailnet-summary"></strong><small id="workspace-tailnet-summary-detail"></small></article>' in response.text
    assert 'id="workspace-task-summary"' not in response.text
    assert 'id="workspace-chub-summary"' not in response.text
    assert 'id="workspace-worker-summary"' not in response.text
    assert 'id="workspace-runtime-detail"' not in response.text
    assert 'id="workspace-tailnet-row"' not in response.text
    assert response.text.index("<strong>Chub Quick Worker</strong>") < response.text.index("<strong>升级与恢复</strong>")
    assert "<strong>升级与恢复</strong>" in response.text
    assert "<strong>系统升级与恢复</strong>" not in response.text
    assert 'id="system-upgrade-badge"' not in response.text
    assert 'id="system-upgrade-start"' in response.text
    assert 'id="system-upgrade-detail" class="workstation-status-detail"' in response.text
    assert 'id="system-upgrade-current-statuses"' not in response.text
    assert 'id="system-upgrade-components"' not in response.text
    assert 'id="system-upgrade-operation"' not in response.text
    assert 'id="system-upgrade-runtime"' not in response.text
    assert 'id="system-upgrade-flow"' not in response.text
    assert 'id="system-upgrade-logs"' not in response.text
    assert response.text.index('id="codex-card-host"') < response.text.index(
        'data-card-key="project-docs"'
    ) < response.text.index('data-card-key="automations"') < response.text.index(
        'data-card-key="core-capabilities"'
    ) < response.text.index(
        'data-card-key="third-party-services"'
    )
    dashboard_markup = response.text.split('<div id="dashboard"', 1)[1].split('</div>\n    </main>', 1)[0]
    assert dashboard_markup.index('data-card-key="core-capabilities"') < dashboard_markup.index(
        'data-card-key="third-party-services"'
    )
    assert 'id="automation-browser-control"' in response.text
    assert 'id="automation-browser-badge"' not in response.text
    assert 'id="automation-browser-detail"' in response.text
    assert 'id="automation-browser-message"' in response.text
    assert 'aria-controls="automation-browser-dialog"' in response.text
    assert response.text.index('id="automation-browser-detail"') > response.text.index(
        'id="automation-environment-title"'
    )
    assert response.text.index('id="automation-browser-detail"') < response.text.index(
        'id="automation-feishu-detail"'
    )
    assert response.text.index('id="automation-browser-detail"') < response.text.index(
        'id="core-capabilities-title"'
    )
    assert response.text.count('id="automation-browser-control"') == 1
    assert 'id="automation-browser-dialog"' in response.text
    assert 'id="automation-browser-form"' in response.text
    assert 'id="automation-browser-profile"' in response.text
    assert 'name="automation-browser-mode" value="headless" checked' in response.text
    assert 'name="automation-browser-mode" value="headed"' in response.text
    assert 'id="automation-browser-mode"' not in response.text
    assert 'id="automation-feishu-badge"' in response.text
    assert 'id="automation-feishu-detail"' in response.text
    assert 'id="automation-feishu-check"' in response.text
    assert 'id="automation-feishu-login"' in response.text
    assert 'id="automation-feishu-qr"' in response.text
    assert 'id="automation-feishu-verify"' not in response.text
    assert "飞书环境" in response.text
    assert "正在检查浏览器控制服务" in response.text
    assert "用于飞书登录与任务执行" in response.text
    assert "有界面" in response.text
    assert "无界面" in response.text
    assert response.text.index('value="headless" checked') < response.text.index(
        'value="headed"'
    )
    assert 'id="refresh-automations"' in response.text
    assert "复用飞书登录状态执行自动化任务。" in response.text
    assert 'id="refresh-project-docs"' in response.text
    assert 'id="project-docs-count"' not in response.text
    assert 'href="/automations"' not in response.text
    assert 'id="design-documents-title"' in response.text
    assert "项目文档" in response.text
    assert "查看设计方案和调研资料。" in response.text
    assert 'id="automation-weekly-report-title" class="automation-item-title"' in response.text
    assert ">V 国内业务本期周报</h3>" in response.text
    assert 'id="automation-weekly-download-title"' in response.text
    assert "本期下载" in response.text
    assert 'id="automation-weekly-download-status"' in response.text
    assert 'id="automation-weekly-download-action"' in response.text
    assert 'id="automation-weekly-download"' in response.text
    assert 'id="automation-weekly-documents-title"' in response.text
    assert "周报文档 · 2026-08-03至2026-08-09" in response.text
    assert 'id="automation-weekly-report-list"' in response.text
    assert "本期工作重点确认清单" in response.text
    assert "本期业务周报" in response.text
    assert "重点范围与取舍确认" in response.text
    assert "各端进展汇总" in response.text
    assert 'class="weekly-report-heading"' in response.text
    assert 'class="weekly-report-summary"' in response.text
    assert "2026-08-03至2026-08-09 · 重点范围与取舍确认" not in response.text
    assert 'href="/weekly-reports/2026-08-03至2026-08-09/focus"' in response.text
    assert "待生成" in response.text
    assert 'data-card-key="project-docs"' in response.text
    assert 'data-card-key="automations" data-collapsible-card data-card-return-refresh="true"' in response.text
    assert 'data-card-key="logs"' not in response.text
    assert 'data-card-return-refresh="true"' in response.text
    core_capabilities_card = response.text.split('data-card-key="core-capabilities"', 1)[1]
    third_party_services_card = response.text.split('data-card-key="third-party-services"', 1)[1]
    assert 'data-card-return-refresh="true"' not in core_capabilities_card
    assert 'data-card-return-refresh="true"' not in third_party_services_card
    assert "OpenClaw 方案调研" not in response.text
    assert "持续维护" in response.text
    assert response.text.count("document-archive-action") == 5
    assert response.text.count(">隐藏</button>") == 5
    assert "份设计资料 · 1 份周报可查看" not in response.text
    assert 'href="/project-docs/openclaw-research"' not in response.text
    assert 'target="_blank"' not in response.text
    assert 'href="/project-docs"' in response.text
    assert '>全部文档</a>' in response.text
    assert "查看全部文档" not in response.text
    assert 'href="/project-docs" target="_blank"' not in response.text
    assert "节点维护" not in response.text
    assert "维护检查" not in response.text
    assert 'id="restart-hub"' in response.text
    assert '<button id="restart-hub"' in response.text
    assert '<button id="restart-hub" class="site-header-title"' not in response.text
    assert 'class="site-header-title" href="/workspace"' in response.text
    assert 'aria-label="进入新版首页"' in response.text
    assert 'id="chub-service-badge"' not in response.text
    assert 'id="quick-worker-badge"' not in response.text
    assert 'id="quick-worker-restart"' in response.text
    assert '>重启并清理任务</button>' not in response.text
    assert 'id="automation-browser-restart"' in response.text
    assert response.text.index('id="automation-browser-restart"') < response.text.index(
        'id="automation-browser-control"'
    )
    assert 'id="system-upgrade-badge"' not in response.text
    assert 'id="system-upgrade-start"' in response.text
    assert 'aria-controls="confirmation-dialog"' in response.text
    assert 'id="confirmation-dialog"' in response.text
    assert 'id="confirmation-dialog-message"' in response.text
    assert 'id="confirmation-dialog-details"' in response.text
    assert response.text.index('id="global-message"') < response.text.index(
        'id="connected-bar"'
    )
    assert 'id="site-settings"' in response.text
    assert 'href="/settings" hidden' in response.text
    assert f"v{settings.app.version}" not in response.text
    assert "确认操作" in response.text
    assert 'data-card-heading' in response.text
    assert 'data-card-content' in response.text
    assert 'data-collapsible-card' in response.text
    assert response.text.count('class="card-content-inner"') == 4
    assert "退出" not in response.text
    assert 'id="task-list"' not in response.text
    assert "data-log-source" not in response.text
    assert 'href="/logs"' not in response.text
    assert 'class="card logs-card"' not in response.text
    assert '<h2 id="logs-title">日志</h2>' not in response.text
    assert 'id="status-details"' not in response.text
    assert "展开详情" not in response.text


@pytest.mark.anyio
async def test_removed_cyber_style_falls_back_to_standard_before_assets_load(settings: Settings) -> None:
    transport = httpx.ASGITransport(app=create_app(settings))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        client.cookies.set("hub_ui_style", "cyber")
        pages = await asyncio.gather(
            client.get("/"),
            client.get("/settings/session-defaults"),
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
async def legacy_settings_page_supports_quick_interaction_page_size_preference(
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
    assert 'class="settings-mobile-nav-link" type="button" data-settings-url="/settings/quick-interaction"' in response.text
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
    assert 'id="quick-interaction-page-size"' in response.text
    assert 'name="quick-interaction-page-size" data-settings-picker' in response.text
    assert '<option value="5" selected>5 条</option>' in response.text
    assert '<option value="10">10 条</option>' in response.text
    assert 'id="codex-default-full-access"' in response.text
    assert 'name="codex-default-full-access"' in response.text
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
    assert "hub.quickInteractionPageSize.v1" in script.text
    assert "hub.codexDefaultPermission.v1" not in script.text
    assert "hub.codexDefaultModel.v1" not in script.text
    assert "hub.codexDefaultReasoningEffort.v1" not in script.text
    assert "hub.weixinTranslationSettingsCache" in script.text
    assert "hub.openclawWeixinSettingsCache.v1" in script.text
    assert "hub.codexShowTranslationSession.v1" not in script.text
    assert "/api/codex/models" in script.text
    assert "/api/codex/session-defaults" in script.text
    assert "/api/settings/weixin-translation" in script.text
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
        "session-defaults": "/settings/session-defaults",
        "openclaw": "/settings/openclaw",
    }
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        root = await client.get("/settings", follow_redirects=False)
        legacy_quick_interaction = await client.get(
            "/settings/quick-interaction",
            follow_redirects=False,
        )
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
    assert legacy_quick_interaction.status_code == 307
    assert legacy_quick_interaction.headers["location"] == "/settings/session-defaults"
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

    assert 'href="/settings/session-defaults" aria-current="page"' in pages["session-defaults"].text
    assert '<p class="settings-workspace-sidebar-label">设置</p>' not in pages["session-defaults"].text
    assert 'id="quick-interaction-page-size"' in pages["session-defaults"].text
    assert 'id="codex-default-full-access"' in pages["session-defaults"].text
    assert 'id="runtime-management-list"' not in pages["session-defaults"].text
    assert pages["appearance"].text.index('href="/settings/appearance"') < pages["appearance"].text.index('href="/settings/session-defaults"')
    assert 'id="runtime-management-list"' not in pages["runtime"].text
    assert 'id="runtime-general-settings-title">任务接入规则</h3>' in pages["runtime"].text
    assert 'id="ai-runtime-general-settings"' in pages["runtime"].text
    assert "按 Runtime 分别控制" in pages["runtime"].text
    assert 'href="/settings/runtime" aria-current="page"' in pages["runtime"].text
    assert 'href="/settings/runtime/codex"' in pages["runtime"].text
    assert 'id="quick-interaction-page-size"' not in pages["runtime"].text
    assert 'data-settings-page="runtime-detail"' in pages["runtime-detail"].text
    assert 'id="runtime-management-list"' in pages["runtime-detail"].text
    assert 'id="runtime-settings-panel"' not in pages["runtime-detail"].text
    assert "控制是否接收新任务" in pages["runtime-detail"].text
    assert "ai_runtime.{{ settings_runtime_id }}" not in pages["runtime-detail"].text
    assert "ai_runtime.codex" in pages["runtime-detail"].text
    assert "usage-timezone" not in pages["runtime-detail"].text
    assert '.settings-field input[type="text"]' in stylesheet.text
    assert 'background: var(--color-surface-field);' in stylesheet.text
    assert 'data-runtime-id="codex"' in pages["runtime-detail"].text
    assert pages["runtime-detail"].text.count('id="runtime-management-description"') == 1
    assert 'href="/settings/runtime/codex" aria-current="page"' in pages["runtime-detail"].text
    assert 'href="/settings/task-orchestration"' in pages["runtime"].text
    assert 'href="/settings/task-orchestration" aria-current="page"' in pages["task-orchestration"].text
    assert 'id="workspace-task-processing-trigger"' in pages["task-orchestration"].text
    assert 'class="theme-option-grid" role="radiogroup" aria-label="主题选择"' in pages["appearance"].text
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
    assert '<p>比较文字、状态与控件效果。</p>' in pages["appearance"].text
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
    assert ".theme-option-toolbar p" in stylesheet.text
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
    app.state.codex_pty_manager.read_runtime_management = MagicMock(
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
@pytest.mark.skip(reason="并行新版入口已移除，由根路径工作台测试替代")
async def test_workspace_preview_is_static_and_available(settings: Settings) -> None:
    transport = httpx.ASGITransport(app=create_app(settings))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/workspace")
        project_documents_response = await client.get("/workspace?section=project-docs")
        automations_response = await client.get("/workspace?section=automations")
        removed_preview_response = await client.get("/settings/workspace-preview")
        stylesheet = await client.get("/static/css/components.css")
        workspace_script = await client.get("/static/workspace.js")
        workspace_sessions_script = await client.get("/static/js/features/workspace-sessions.js")
        workspace_workstation_script = await client.get("/static/js/features/workspace-workstation.js")
        bootstrap_script = await client.get("/static/workspace-bootstrap.js")

    assert response.status_code == 200
    assert project_documents_response.status_code == 200
    assert automations_response.status_code == 200
    assert removed_preview_response.status_code == 404
    assert "新版首页 · Hub" in response.text
    assert "操作" in response.text
    assert "工作台分区" not in response.text
    assert "最近 Session" not in response.text
    assert "AI Session" in response.text
    assert "工作站环境" in response.text
    assert 'class="workstation-group workspace-core-environment"' in response.text
    assert 'class="workstation-group workspace-third-party-environment"' in response.text
    assert "设备状态" not in response.text
    assert "常用入口" not in response.text
    assert 'id="workspace-chub-restart"' in response.text
    assert 'id="workspace-worker-restart"' in response.text
    assert 'id="workspace-upgrade-start"' in response.text
    assert "workspace-preview-shell" in response.text
    assert "workspace-preview-work-surface" in response.text
    assert 'id="workspace-sidebar-toggle"' in response.text
    assert 'id="workspace-sidebar-resizer"' in response.text
    assert 'id="workspace-sidebar-scrim"' in response.text
    assert 'role="separator"' in response.text
    assert 'aria-valuemin="225"' in response.text
    assert 'aria-valuemax="360"' in response.text
    assert 'id="workspace-sidebar-close"' in response.text
    assert 'aria-label="工作台操作"' in response.text
    assert 'id="workspace-toolbar-error" class="workspace-preview-toolbar-error" aria-live="polite" hidden' in response.text
    assert '<div class="workspace-preview-brand"><strong>Chub</strong><button id="workspace-sidebar-close"' in response.text
    assert 'aria-label="工作台辅助导航"' in response.text
    assert response.text.index('aria-label="工作台辅助导航"') > response.text.index(
        'id="workspace-preview-sessions-title"',
    )
    assert "个人 AI 工作站" not in response.text
    assert '>☰</span></button>' in response.text
    assert "WORKSPACE" not in response.text
    assert "<h1>工作台</h1>" not in response.text
    assert "当前为并行建设页面" not in response.text
    assert '<a href="/settings"><span class="workspace-preview-nav-icon"' in response.text
    assert 'disabled title="功能建设中"' not in response.text
    assert 'aria-controls="workspace-sidebar"' in response.text
    assert 'src="/static/workspace-bootstrap.js"' in response.text
    assert response.text.index('src="/static/workspace-bootstrap.js"') < response.text.index(
        '/static/css/tokens.css',
    )
    assert 'src="/static/workspace.js"' in response.text
    assert 'src="/static/js/features/workspace-sessions.js"' in response.text
    assert 'src="/static/js/features/workspace-workstation.js"' in response.text
    assert 'href="/workspace?section=project-docs"' in response.text
    assert 'href="/workspace?section=automations"' in response.text
    assert 'href="/workspace" aria-current="page" class="is-current"><span class="workspace-preview-nav-icon"' in response.text
    assert response.text.count('class="workspace-preview-nav-icon" aria-hidden="true"') == 4
    assert 'class="workspace-preview-compact-nav" data-workspace-section-navigation aria-label="折叠侧栏导航"' in response.text
    assert 'aria-label="工作台" title="工作台"' in response.text
    assert 'class="workspace-preview-compact-nav-external" href="/settings"' in response.text
    assert 'id="workspace-session-create" class="workspace-preview-create" type="button" disabled>+ New Session</button>' in response.text
    assert 'id="workspace-session-list"' in response.text
    assert 'id="workspace-quick-session-toolbar" class="workspace-quick-session-toolbar" aria-label="快速会话切换" hidden' in response.text
    assert 'id="workspace-session-create-dialog"' in response.text
    assert 'id="workspace-session-more-dialog"' not in response.text
    assert 'id="workspace-session-rename-dialog"' in response.text
    assert response.text.index('id="confirmation-dialog"') > response.text.index(
        'id="workspace-sidebar-resizer"',
    )
    assert '<span>会话</span>' not in response.text
    assert 'href="/workspace"' in project_documents_response.text
    assert 'href="/workspace?section=project-docs" aria-current="page" class="is-current"><span class="workspace-preview-nav-icon"' in project_documents_response.text
    assert "项目说明、设计方案与维护文档" in project_documents_response.text
    assert "workspace-project-document-list" in project_documents_response.text
    assert 'class="button-secondary workspace-project-documents-all" href="/project-docs"' in project_documents_response.text
    assert "Chub 项目说明" in project_documents_response.text
    assert 'href="/workspace?section=automations" aria-current="page" class="is-current"><span class="workspace-preview-nav-icon"' in automations_response.text
    assert "自动化任务" in automations_response.text
    assert "自动化环境" in automations_response.text
    assert 'class="workspace-preview-panel workstation-card workspace-automation-details workspace-preview-work-section"' in automations_response.text
    assert 'class="workstation-group automation-environment"' in automations_response.text
    assert 'class="workstation-status-list"' in automations_response.text
    assert 'id="workspace-automation-browser-start"' not in automations_response.text
    assert 'href="/automations"' not in automations_response.text
    assert ".workspace-preview-shell" in stylesheet.text
    assert "workspace-preview-local-nav" not in response.text
    assert ".workspace-project-documents-all {\n  display: inline-flex;" in stylesheet.text
    assert ".workstation-group {\n  display: grid;" in stylesheet.text
    assert ".workstation-group-heading h2,\n.workstation-group-heading h3 {\n  margin: 0;\n  font-size: 1rem;" in stylesheet.text
    assert ".workstation-status-row {\n  display: flex;" in stylesheet.text
    assert ".workspace-workstation {\n  gap: 0;" in stylesheet.text
    assert ".workspace-workstation > .workstation-group + .workstation-group::before" in stylesheet.text
    assert ".workspace-automation-details > .workstation-group + .workstation-group::before" in stylesheet.text
    assert "right: -1rem;" in stylesheet.text
    assert "left: -1rem;" in stylesheet.text
    assert ".automation-environment .workstation-status-row,\n.automation-account-environment .workstation-status-row {\n  min-height: 0;" in stylesheet.text
    assert ".workspace-preview-shell.is-sidebar-collapsed" in stylesheet.text
    assert ".workspace-quick-session-toolbar-button" in stylesheet.text
    assert "--workspace-sidebar-width: var(--workspace-sidebar-preload-width, 225px);" in stylesheet.text
    assert "grid-template-columns: var(--workspace-sidebar-width) minmax(0, 1fr);" in stylesheet.text
    assert '.workspace-preview-nav a[aria-current="page"] {' in stylesheet.text
    assert ".workspace-preview-nav-icon {" in stylesheet.text
    assert ".workspace-preview-compact-nav {" in stylesheet.text
    assert ".workspace-preview-shell.is-sidebar-collapsed .workspace-preview-compact-nav" in stylesheet.text
    assert ".workspace-preview-compact-nav a:not(.workspace-preview-compact-nav-external):hover" in stylesheet.text
    assert ".workspace-preview-compact-nav-external:active" in stylesheet.text
    assert "  min-width: 0;\n  overflow: hidden;" in stylesheet.text
    assert ".workspace-preview-nav > span," in stylesheet.text
    assert "border-color: var(--color-accent);" in stylesheet.text
    assert ".workspace-preview-session {\n  display: grid;" in stylesheet.text
    assert ".workspace-preview-session.is-current {\n  border-color: var(--color-accent);" in stylesheet.text
    assert ".workspace-preview-session.is-current strong {\n  color: var(--color-accent-text);" in stylesheet.text
    assert "  height: auto;\n  min-height: 3.75rem;" in stylesheet.text
    assert ".workspace-preview-session strong {\n  color: var(--ink);" in stylesheet.text
    assert "  padding: 0.6rem 0.65rem;" in stylesheet.text
    assert "border-right: 1px solid var(--line);" in stylesheet.text
    assert "width: 1.75rem;" in stylesheet.text
    assert "--workspace-toolbar-height: 2.25rem;" in stylesheet.text
    assert "--workspace-toolbar-top-gap: 0.45rem;" in stylesheet.text
    assert "height: var(--workspace-toolbar-height);" in stylesheet.text
    assert "padding: var(--workspace-toolbar-top-gap) 1rem 1rem;" in stylesheet.text
    assert "padding: var(--workspace-toolbar-top-gap) clamp(0.75rem, 1.5vw, 1.25rem) 1rem;" in stylesheet.text
    assert ".workspace-preview-brand {\n  display: flex;\n  height: var(--workspace-toolbar-height);" in stylesheet.text
    assert "color: color-mix(in srgb, var(--accent-dark) 78%, var(--muted));" in stylesheet.text
    assert ".workspace-preview-sidebar-footer {\n  display: grid;\n  gap: 0.75rem;\n  margin-top: auto;" in stylesheet.text
    assert "gap: 0.75rem;" in stylesheet.text
    assert ".workspace-preview-toolbar" in stylesheet.text
    assert ".workspace-preview-main {\n  align-content: start;" in stylesheet.text
    assert workspace_script.status_code == 200
    assert workspace_sessions_script.status_code == 200
    assert workspace_workstation_script.status_code == 200
    assert bootstrap_script.status_code == 200
    assert "event.metaKey || event.ctrlKey" in workspace_script.text
    assert "window.location.replace(link.href);" in workspace_script.text
    assert 'document.getElementById("workspace-section-content")' in workspace_script.text
    assert "window.disposeWorkspaceWorkstation?.();" in workspace_script.text
    assert "currentContent.replaceWith(nextContent);" in workspace_script.text
    assert "const preserveWorkspaceReturnTarget = (event) =>" in workspace_script.text
    assert 'targetUrl.searchParams.set(\n      "return_to",' in workspace_script.text
    assert "const clearWorkspaceSectionSelection = () =>" in workspace_script.text
    assert "clearWorkspaceSectionSelection();" in workspace_script.text
    assert 'targetUrl.pathname !== "/workspace"' in workspace_script.text
    assert "link.blur();" in workspace_script.text
    assert "window.initializeWorkspaceAutomationControls?.();" in workspace_script.text
    assert "window.initializeWorkspaceWorkstation?.();" in workspace_script.text
    assert '"/api/automations/browser/start"' in workspace_script.text
    assert '"/api/automations/browser/stop"' in workspace_script.text
    assert '"/api/automations/environment/feishu/check"' in workspace_script.text
    assert '"/api/automations/environment/codex/check"' in workspace_script.text
    assert 'automationFeishuCheck.textContent = "检查中…";' in workspace_script.text
    assert 'automationCodexAccountCheck.textContent = "检查中…";' in workspace_script.text
    assert 'automationBrowserDetail.dataset.browserState === "running"' in workspace_script.text
    assert 'detail.dataset.accountState === "unchecked"' in workspace_script.text
    assert 'automationFeishuCheck?.click();' in workspace_script.text
    assert 'automationCodexAccountCheck?.click();' in workspace_script.text
    assert '"workspace-automation-browser-start-dialog"' in workspace_script.text
    assert 'automationStartConfirm?.focus();' in workspace_script.text
    assert 'automationStopConfirm?.focus();' in workspace_script.text
    assert 'event.key.toLowerCase() !== "b"' in workspace_script.text
    assert 'document.getElementById("workspace-sidebar-close")' in workspace_script.text
    assert "chub.workspace.sidebarCollapsed" in workspace_script.text
    assert '"/api/codex/sessions"' in workspace_sessions_script.text
    assert "const renderQuickSessionToolbar = (orderedSessions) =>" in workspace_sessions_script.text
    assert "const quickSessionLabel = (session) =>" in workspace_sessions_script.text
    assert 'workspace-quick-session-toolbar-button' in workspace_sessions_script.text
    assert "/quick-interactions/conversation" in workspace_sessions_script.text
    assert "/access" in workspace_sessions_script.text
    assert '"/api/status"' in workspace_workstation_script.text
    assert '"/api/maintenance/restart"' in workspace_workstation_script.text
    assert '"/api/maintenance/quick-worker/restart"' in workspace_workstation_script.text
    assert '"/api/maintenance/system-upgrade"' in workspace_workstation_script.text
    assert "chub.sidebarWidth" in workspace_script.text
    assert "chub.workspace.sidebarWidth" not in workspace_script.text
    assert "minimumSidebarWidth = 225" in workspace_script.text
    assert "maximumSidebarWidth = 360" in workspace_script.text
    assert 'resizer.addEventListener("pointerdown"' in workspace_script.text
    assert "ArrowLeft: currentSidebarWidth() - sidebarWidthStep" in workspace_script.text
    assert 'toggle.title = sidebarLabel;' in workspace_script.text
    assert '@media (min-width: 761px) and (max-width: 1080px)' in stylesheet.text
    assert ".workspace-preview-shell.is-mobile-sidebar-open .workspace-preview-sidebar" in stylesheet.text
    assert ".workspace-preview-sidebar-scrim" in stylesheet.text
    assert ".workspace-preview-sidebar-close" in stylesheet.text
    assert ':root[data-workspace-sidebar-collapsed="true"] .workspace-preview-shell,' in stylesheet.text
    assert "const expandSidebar = () =>" in workspace_script.text
    assert "const collapseSidebar = () =>" in workspace_script.text
    assert "shell.classList.add(\"is-sidebar-opening\")" in workspace_script.text
    assert "shell.classList.add(\"is-sidebar-closing\")" in workspace_script.text
    assert "const setMobileSidebarOpen = (open) =>" in workspace_script.text
    assert "sidebar.inert = !open;" in workspace_script.text
    assert "const openMobileSidebar = () =>" in workspace_script.text
    assert "history.pushState(" in workspace_script.text
    assert 'window.addEventListener("popstate"' in workspace_script.text
    assert 'sidebarClose.addEventListener("click"' in workspace_script.text
    assert 'document.addEventListener("pointerdown"' in workspace_script.text
    assert "sidebar.contains(event.target)" in workspace_script.text
    assert 'requestAnimationFrame(() => shell.classList.add("is-layout-ready"));' in workspace_script.text
    assert "workspace-sidebar-preload-width" in bootstrap_script.text
    assert "chub.sidebarWidth" in bootstrap_script.text
    assert "data-workspace-sidebar-collapsed" in stylesheet.text
    assert ".workspace-preview-shell.is-layout-ready" in stylesheet.text
    assert 'content.className = "workspace-preview-session-content";' in workspace_sessions_script.text
    assert ".workspace-preview-session-content {" in stylesheet.text


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
        legacy_settings_with_return_target = await client.get(
            "/settings/quick-interaction?return_to=%2F%3Fsession%3Dsession-123",
            follow_redirects=False,
        )
        legacy_invalid_settings_return_target = await client.get(
            "/settings/quick-interaction?return_to=https%3A%2F%2Fexample.invalid",
            follow_redirects=False,
        )
        settings_with_return_target = await client.get(
            "/settings/session-defaults?return_to=%2F%3Fsession%3Dsession-123"
        )
        invalid_settings_return_target = await client.get(
            "/settings/session-defaults?return_to=https%3A%2F%2Fexample.invalid"
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
    assert legacy_settings_with_return_target.status_code == 307
    assert legacy_settings_with_return_target.headers["location"] == (
        "/settings/session-defaults?return_to=%2F%3Fsession%3Dsession-123"
    )
    assert legacy_invalid_settings_return_target.status_code == 307
    assert legacy_invalid_settings_return_target.headers["location"] == "/settings/session-defaults"
    assert 'id="settings-return-application" class="settings-workspace-return" href="/?session=session-123"' in settings_with_return_target.text
    assert 'class="settings-mobile-nav-external" href="/?session=session-123"' in settings_with_return_target.text
    assert 'id="settings-return-application" class="settings-workspace-return" href="/"' in invalid_settings_return_target.text
    assert '<title>Hub</title>' in home.text
    assert 'href="/" aria-current="page"' in home.text
    assert 'href="/?section=automations"' in home.text
    assert 'href="/?section=project-docs"' in home.text
    assert "工作站环境" in home.text
    assert "AI Session" in home.text
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
            ),
            codex_runtime_account=RuntimeAccountEnvironmentState(
                state="available",
                message="ChatGPT 登录与 AI 额度可用",
                checked_at=datetime(2026, 9, 5, 12, 31, tzinfo=timezone.utc),
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
    assert 'id="workspace-automation-feishu-detail"' in response.text
    assert 'id="workspace-automation-codex-account-check"' in response.text
    assert 'id="workspace-automation-codex-account-detail"' in response.text
    assert 'data-browser-state="stopped"' in response.text
    assert 'data-account-state="login_required"' in response.text
    assert 'data-account-state="available"' in response.text
    assert "Debug Chrome 未启动，按需启动。 · 浏览器用户：Default · 无界面" in response.text
    assert 'id="workspace-automation-browser-message"' not in response.text
    assert "飞书登录已失效，请重新登录。 · 检查于 09-05 12:30" in response.text
    assert "ChatGPT 登录与 AI 额度可用 · 检查于 09-05 12:31" in response.text
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
    assert 'title="请等待当前自动化任务完成"' not in response.text
    assert 'title="请先启动 Debug Chrome"' in response.text
    assert 'data-automation-task-message' not in response.text
    assert workspace_script.status_code == 200
    assert 'showConfirmationDialog({' in workspace_script.text
    assert '`/api/automations/${encodeURIComponent(taskId)}/run`' in workspace_script.text
    assert 'setWorkstationStatus(taskDetail, "任务已受理，正在刷新状态。", "warning");' in workspace_script.text
    assert '".workspace-weekly-report-view-session"' in workspace_script.text
    assert "window.selectWorkspaceQuickSession?.(sessionId);" in workspace_script.text
    assert "window.openWorkspaceQuickSession({ id: sessionId, session_mode: \"quick\", title });" in workspace_script.text
    assert "window.location.assign(`/?session=${encodeURIComponent(sessionId)}`);" in workspace_script.text
    assert '".workspace-weekly-report-confirm-and-run"' in workspace_script.text
    assert '"/api/weekly-reports/current/report/confirm-and-run"' in workspace_script.text
    assert "setAutomationBrowserMessage" not in workspace_script.text
    assert "setAutomationFeishuMessage" not in workspace_script.text
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
@pytest.mark.skip(reason="旧首页资源已移除，由新版工作台资源测试替代")
async def test_web_assets_are_available(settings: Settings) -> None:
    transport = httpx.ASGITransport(app=create_app(settings))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        scripts = [
            await client.get("/static/js/core/ai-usage.js"),
            await client.get("/static/theme.js"),
            await client.get("/static/js/core/dashboard-core.js"),
            await client.get("/static/js/components/ui.js"),
            await client.get("/static/js/components/collapsible-card.js"),
            await client.get("/static/js/features/node-status.js"),
            await client.get("/static/js/features/codex-sessions.js"),
            await client.get("/static/js/features/openclaw.js"),
            await client.get("/static/js/features/automations.js"),
            await client.get("/static/js/features/workstation.js"),
            await client.get("/static/js/features/project-documents.js"),
            await client.get("/static/app.js"),
        ]
        polling_script = await client.get("/static/codex_polling.js")
        removed_stylesheet = await client.get("/static/app.css")
        stylesheets = [
            await client.get("/static/css/tokens.css"),
            await client.get("/static/css/base.css"),
            await client.get("/static/css/components.css"),
            await client.get("/static/css/responsive.css"),
        ]
        terminal_stylesheet = await client.get("/static/terminal.css")
        terminal_script = await client.get("/static/terminal.js")
        maintenance_terminal_script = await client.get("/static/maintenance_terminal.js")

    assert all(script.status_code == 200 for script in scripts)
    assert polling_script.status_code == 200
    assert removed_stylesheet.status_code == 404

    assert all(asset.status_code == 200 for asset in stylesheets)
    assert terminal_stylesheet.status_code == 200
    assert terminal_script.status_code == 200
    assert maintenance_terminal_script.status_code == 200
    assert "/maintenance-terminal/connection/" in maintenance_terminal_script.text
    dashboard_script = "\n".join(script.text for script in scripts)
    script = MagicMock(text=dashboard_script)
    stylesheet = MagicMock(
        text="\n".join(asset.text for asset in stylesheets),
    )
    assert "innerHTML" not in dashboard_script
    assert "window.confirm" not in dashboard_script
    assert "showConfirmationDialog" in dashboard_script
    assert "confirmationDialogBusy" in dashboard_script
    assert "closeOnConfirm = false" in dashboard_script
    assert "if (current.closeOnConfirm)" in dashboard_script
    assert 'siteSettings: document.querySelector("#site-settings")' in dashboard_script
    assert "elements.siteSettings.hidden = true" in dashboard_script
    assert "elements.siteSettings.hidden = false" in dashboard_script
    assert "/api/automations/browser/" in dashboard_script
    assert "/api/openclaw/status" in dashboard_script
    assert "/api/openclaw/weixin/login" in dashboard_script
    assert "/api/maintenance/quick-worker" in dashboard_script
    assert "/api/maintenance/system-upgrade" in dashboard_script
    assert "browser_supervisor" not in dashboard_script
    assert "debug_chrome_instance" not in dashboard_script
    assert "未纳入升级" not in dashboard_script
    assert "reloadDashboardAfterMaintenance" in dashboard_script
    assert "systemUpgradeCurrentStatuses" not in dashboard_script
    assert "systemUpgradeOperation" not in dashboard_script
    assert "systemUpgradeRuntime" not in dashboard_script
    assert "systemUpgradeFlow" not in dashboard_script
    assert "maintenance-timeline-step" not in dashboard_script
    assert "failed_stage" not in dashboard_script
    assert "runtime_message ? ` · ${data.runtime_message}`" not in dashboard_script
    assert "最近操作" not in dashboard_script
    assert "SYSTEM_UPGRADE_TIMELINE" not in dashboard_script
    assert "runtime_message" in dashboard_script
    assert "data.runtimes" in dashboard_script
    assert "maintenanceReloadTimer" in dashboard_script
    assert "}, 2000);" in dashboard_script
    assert "浏览器将在稍后自动刷新页面" in dashboard_script
    assert "Chub Quick Worker、Ubuntu Chub Debug Chrome 和 OpenClaw Gateway 是独立服务，不会被重启" in dashboard_script
    assert "tmux 和原生 Codex 会话保留，重新进入时恢复" in dashboard_script
    assert 'setWorkstationStatus(elements.quickWorkerDetail, data.operation.message, "success")' in dashboard_script
    assert "systemUpgradeReloadOperationId" in dashboard_script
    assert 'data.operation?.status === "succeeded"' in dashboard_script
    assert 'headers: { "Content-Type": "application/json" }' in dashboard_script
    assert "syncCoreMaintenanceControls" in dashboard_script
    assert 'sessionsTitle.className = "card-group-title codex-sessions-title"' in dashboard_script
    assert "codex-sessions-divider" not in dashboard_script
    assert "Promise.allSettled" in dashboard_script
    assert "/api/openclaw/${action}" in dashboard_script
    assert "OPENCLAW_STATUS_CACHE_KEY" in dashboard_script
    assert "restoreOpenClawCache" in dashboard_script
    assert 'data.owner_state === "not_configured"' in dashboard_script
    assert 'data.owner_state === "unavailable"' in dashboard_script
    assert 'dashboardNavigationEntry?.type === "back_forward"' in dashboard_script
    assert "if (dashboardIsHistoryReturn && openclawCacheRestored)" in dashboard_script
    assert "cardLoads.push(loadOpenClawWeixinStatus())" in dashboard_script
    assert "无法读取最新任务执行服务状态，保留上次结果。" in dashboard_script
    assert '"正在执行重启与恢复"' in dashboard_script
    assert "operationVersion !== openclawOperationVersion" in dashboard_script
    assert "if (openclawBusy) {" in dashboard_script
    assert "正在重启 OpenClaw Gateway" not in dashboard_script
    assert "OpenClaw Gateway 已停止" not in dashboard_script
    assert "OPENCLAW_WEIXIN_ACTIVE_STATES" in dashboard_script
    assert "pollOpenClawWeixinLogin" not in dashboard_script
    assert "Promise.all([\n      apiFetch(\"/api/openclaw/status\")" not in dashboard_script
    assert "automationBrowserModeInputs" in dashboard_script
    assert "loadAutomationEnvironment" in dashboard_script
    assert "automationBrowserDialog.showModal()" in dashboard_script
    assert "automationBrowserDialogConfirm" in dashboard_script
    assert 'return ["浏览器控制服务可用；实例按需启动", "muted"]' in dashboard_script
    assert '"浏览器控制服务可用", "实例正在运行", mode' in dashboard_script
    assert 'return "用于飞书登录与任务执行"' in dashboard_script
    assert 'return `登录验证于 ${automationMonthDay(data.checked_at)}`' in dashboard_script
    assert "appendWeeklyReportMaterials" in dashboard_script
    assert "weeklyDownloadStatus" in dashboard_script
    assert "weeklyValidationStatus" in dashboard_script
    assert "下载成功" in dashboard_script
    assert "校验通过" in dashboard_script
    assert 'mainLabel.textContent = mainPassed ? "主文档 · 1/1 通过" : "主文档"' in dashboard_script
    assert '`${waiting ? "等待原因" : "校验原因"} · ${task.state.validation_message}`' in dashboard_script
    assert "上周参考 · ${linkedDocument.name}" in dashboard_script
    assert "各端周报 · ${linkedSuccesses}/${currentDocuments.length} 通过" in dashboard_script
    assert "automation-material-summary" in dashboard_script
    assert "includeHeading = true, includeButton = true" in dashboard_script
    assert "{ includeHeading: false, includeButton: false }" in dashboard_script
    assert "automationWeeklyDownloadAction.append(rendered.button)" in dashboard_script
    assert "automation-weekly-download-heading" in stylesheet.text
    assert ".automation-weekly-report-group {\n  border: 1px solid var(--line);\n  border-radius: 8px;" in stylesheet.text
    assert "border: 0;\n  padding: 0;\n  background: transparent;" in stylesheet.text
    assert ".automation-item {\n  display: grid;\n  grid-template-columns: minmax(0, 1fr) auto;\n  align-items: start;" in stylesheet.text
    assert "本期下载 ·" in dashboard_script
    assert "待下载" in dashboard_script
    assert "启动并运行" in dashboard_script
    assert 'apiFetch("/api/automations/browser/start"' in dashboard_script
    assert 'JSON.stringify({ mode: "headless" })' in dashboard_script
    assert 'error.message || "Debug Chrome 启动失败。"' in dashboard_script
    assert "Promise.all([loadAutomations(), loadAutomationEnvironment()])" in dashboard_script
    assert 'apiFetch("/api/weekly-reports/current")' in dashboard_script
    assert "loadWeeklyReports" in dashboard_script
    assert "refreshAutomationCard" in script.text
    assert "/api/project-docs" in dashboard_script
    assert "loadProjectDocuments" in dashboard_script
    assert 'document.createElement("time")' in dashboard_script
    assert 'archive.textContent = "隐藏"' in dashboard_script
    assert "此操作不会移动或冻结仓库文件" in dashboard_script
    assert "正在刷新文档列表" not in dashboard_script
    assert "文档列表已更新" not in dashboard_script
    assert "文档已归档" not in dashboard_script
    assert "sessionStorage" in dashboard_script
    assert "localStorage" in dashboard_script
    assert "accessVersion" in dashboard_script
    assert "connectToHub" in dashboard_script
    assert "connectWithToken" not in dashboard_script
    assert "暂时无法读取节点状态，请稍后重试。" in dashboard_script
    assert 'error.code === "trusted_network_required"' in dashboard_script
    assert "createTaskCard" not in script.text
    assert "createCodexCard" in script.text
    assert 'createButton.textContent = "新建会话"' in script.text
    assert 'workspaceDialogTitle.textContent = "选择工作目录"' in script.text
    assert "首页列出三个常用目录；其他目录启动的 Codex 会话会自动出现在列表中。" in script.text
    assert "workspaceDialog.showModal()" in script.text
    assert "workspaceDialog.close()" in script.text
    assert "ensureCodexCard" in script.text
    assert "elements.codexCardHost.replaceChildren();" in script.text
    assert "createCodexSession" in script.text
    assert "enterCodexSession" in script.text
    assert "CARD_RETURN_REFRESHERS" in script.text
    assert "CARD_COLLAPSED_STATE_KEY" in script.text
    assert "loadCardCollapsedState" in script.text
    assert "saveCardCollapsedState" in script.text
    assert "hub.cardCollapsedState.v1" in script.text
    assert "hub.codexRefreshOnReturn" not in script.text
    assert "hub.projectDocsRefreshOnReturn" not in script.text
    assert 'data-card-return-refresh="true"' in script.text
    assert "cardsRefreshAt" in script.text
    assert 'now - cardsRefreshAt < 500' in script.text
    assert "cardsRefreshAt = now;\n  loadStatus();" in script.text
    assert 'window.addEventListener("pageshow"' in script.text
    assert 'document.addEventListener("visibilitychange"' in script.text
    assert "stopCodexSession" in script.text
    assert "archiveCodexSession" in script.text
    assert 'quickInteraction.textContent = "快速交互"' not in script.text
    assert 'interactionHistory.textContent = "交互记录"' not in script.text
    assert "codex-session-realtime" in script.text
    assert 'sessionModeTitle.textContent = "实时会话"' in script.text
    assert "QUICK_INTERACTION_VIEW_KEY" not in script.text
    assert "quickInteractionUrl" in script.text
    assert "quick-interactions/conversation" in script.text
    assert "openCodexEntryDialog" not in script.text
    assert "toggleCodexEntryMode" not in script.text
    assert "session.session_mode === \"quick\"" in script.text
    assert "actions.append(rename, stop, archive, remove);" in script.text
    assert "renameCodexSession" in script.text
    assert "closeCodexRenameDialog(true)" in script.text
    assert "permissionPanel" not in script.text
    assert "快速交互已提交" not in script.text
    assert "quick-interaction-submit" not in script.text
    assert "confirm_stop_unknown_terminal" not in script.text
    assert "unknownConfirmationInput" not in script.text
    assert "deleteCodexSession" in script.text
    assert "renderCodexWorkspaces" in script.text
    assert "renderCodexSessions" in script.text
    assert "codexSessionsNewestFirst" in script.text
    assert "visibleCodexSessions" in script.text
    assert 'session.workspace_id !== "weixin-translation"' in script.text
    assert "visibleSessions.length" in script.text
    assert 'return "等待输入";' in script.text
    assert 'return "执行中";' in script.text
    assert 'return "正在使用";' in script.text
    assert "实时终端 · 等待输入" not in script.text
    assert "快速交互 · 待输入" not in script.text
    assert "快速交互 · 执行中" not in script.text
    assert "快速交互 · 等待结果" not in script.text
    assert "活动状态未知 · 请刷新" in script.text
    assert script.text.index('if (session.status === "error" || session.error)') < script.text.index(
        'if (session.session_mode === "terminal" && owner === "terminal")'
    )
    assert 'session.error === "terminal_backend_failed"' in script.text
    assert 'session.status === "new"' in script.text
    assert "尚未启动 · 可进入" in script.text
    assert "终端连接异常 · 可重试" in script.text
    assert "会话异常 · 可重试" in script.text
    assert "CODEX_POLL_FAST_MS = 2000" in script.text
    assert "CODEX_POLL_SLOW_MS = 8000" in script.text
    assert "CODEX_POLL_SLOW_AFTER_MS = 2 * 60 * 1000" in script.text
    assert 'session.activity === "working"' in polling_script.text
    assert "loadCodexSessions({ background: true })" in script.text
    assert "loadCodexSessions({ force: true })" in script.text
    assert "session.quick_interaction_running" in script.text
    assert "CODEX_DEFAULT_PERMISSION_KEY" not in script.text
    assert "readCodexDefaultPermission" not in script.text
    assert "readCodexDefaultModel" not in script.text
    assert "readCodexDefaultReasoningEffort" not in script.text
    assert "clearCodexModelPreferences" not in script.text
    assert "archive.disabled = !session.can_archive" in script.text
    assert "|| quickInteractionRunning" not in script.text
    assert "llmInteractionRunning" not in script.text
    assert "codexLoadPromise" in script.text
    assert 'CACHE_KEY = "hub.aiUsageCache"' in script.text
    assert "REFRESH_MS = 5 * 60 * 1000" in script.text
    assert "新建默认：" not in script.text
    assert "codex-model-preference" not in script.text
    assert "restoreCodexModelPreferenceCache" not in dashboard_script
    assert "refreshModelPreference" not in dashboard_script
    assert "/api/ai/usage" in script.text
    assert "额度：正在读取…" in script.text
    assert "renderCodexQuota" in script.text
    assert '"codex-quota-compact"' in script.text
    assert '"codex-quota-today-complete"' in script.text
    assert "codex-quota-${kind}-break" in script.text
    assert "codex-quota-${kind}-separator" in script.text
    assert "Weekly" in script.text
    assert "codexQuotaWindowLabel" not in script.text
    assert "refreshQuota: true" in script.text
    assert "codexMutationCount" in script.text
    assert "codexSessionsSignature" in script.text
    assert "codexLoadPromise = null" in script.text
    assert "codexMutationCount = 0" in script.text
    assert "if (handleAccessError(error))" in script.text
    assert "loadProjectDocuments({ clearMessage: false })" not in script.text
    assert "renderCodexData" in script.text
    assert "restoreCodexCardCache" in script.text
    assert "storeCodexCardCache" in script.text
    assert "hub.codexCardCache" in script.text
    assert "formatSessionTime" in script.text
    assert "dependencyMessage" in script.text
    assert "AI" in script.text
    assert "会话工作台" in script.text
    assert "统一管理实时终端和快速交互会话。" in script.text
    assert "会话工作台不可用。" in script.text
    assert "showCodexPanel" not in script.text
    assert "setupCollapsibleCard" in script.text
    assert "cardContentInner.append(panel, workspaceDialog)" in script.text
    assert "card.append(header, cardContent)" in script.text
    assert 'aria-expanded' in script.text
    assert "is-collapsed" in script.text
    assert "setContentCollapsed" in script.text
    assert "playContentAnimation" in script.text
    assert "cancelContentAnimations" in script.text
    assert "fadeTargets = Array.from(content.children)" in script.text
    assert "CARD_FADE_DURATION_MS = 140" in script.text
    assert "CARD_HEIGHT_DURATION_MS = 180" in script.text
    assert 'card.dataset.collapsiblePersist !== "false"' in script.text
    assert "if (shouldPersist)" in script.text
    assert "transition: gap 180ms ease 140ms" in stylesheet.text
    assert 'matchMedia("(prefers-reduced-motion: reduce)")' in script.text
    assert "requestHubRestart" in script.text
    assert "monitorHubRestart" in script.text
    assert "hubRestartInProgress" in script.text
    assert "syncCoreMaintenanceControls()" in script.text
    assert "scrollCodexPanelIntoView" not in script.text
    assert "任务状态已更新。" not in script.text
    assert "/api/maintenance/restart" in script.text
    assert "/api/health" in script.text
    assert "waitForHubRestart" in script.text
    assert "elements.globalMessage" in script.text
    assert "reloadDashboardAfterMaintenance" in script.text
    assert "previousInstanceId" in script.text
    assert "正在检查 Tailnet 可信访问" not in script.text
    assert "正在验证已保存凭证" not in script.text
    assert "重启命令已下发，正在等待 Hub 恢复" not in script.text
    assert "Chub 已恢复，正在同步卡片状态" not in script.text
    assert "Chub 已重启并恢复连接" not in script.text
    assert 'setMessage(elements.globalMessage, "")' in script.text
    assert 'errorMessage: "重启失败。"' in script.text
    assert "/api/codex/restart" not in script.text
    assert "/api/codex/sessions" in script.text
    assert "connection" in terminal_script.text
    assert "window.history.back()" not in terminal_script.text
    assert "hub.codexReturnToDashboard" not in terminal_script.text
    assert "view=codex" not in terminal_script.text
    assert "response.status === 404" in terminal_script.text
    assert ".section-heading > .button-link" in stylesheet.text
    assert "white-space: nowrap" in stylesheet.text
    assert ".dashboard > .card" in stylesheet.text
    assert "#codex-card-host" in stylesheet.text
    assert "align-self: start" in stylesheet.text
    assert "container: codex-card / inline-size" in stylesheet.text
    assert "@container codex-card (min-width: 40rem)" in stylesheet.text
    assert ".codex-quota-compact .codex-quota-reset-break" in stylesheet.text
    assert ".codex-quota-today-complete .codex-quota-reset-break" in stylesheet.text
    assert ".confirmation-dialog-surface" in stylesheet.text
    assert ".confirmation-dialog-actions" in stylesheet.text
    assert "-webkit-tap-highlight-color: transparent" in stylesheet.text
    assert ".workstation-card" in stylesheet.text
    assert ".workstation-status-row" in stylesheet.text
    assert ".session-path" in stylesheet.text
    assert ".session-actions" in stylesheet.text
    assert "grid-column: 1 / -1;" in stylesheet.text
    assert ".session-permission-panel" not in stylesheet.text
    assert ".quick-interaction-history" not in stylesheet.text
    assert "grid-template-columns: minmax(0, 1fr) auto;" in stylesheet.text
    assert "grid-template-columns: 1fr;" in stylesheet.text
    assert "align-content: start" in stylesheet.text
    assert ".markdown-body > :first-child" in stylesheet.text
    assert ".message:empty" in stylesheet.text
    assert "--page-top-space: 1.25rem" in stylesheet.text
    assert "--page-top-space: 1.5rem" in stylesheet.text
    assert "--content-card-top-space: 0.75rem" in stylesheet.text
    assert "padding: var(--page-top-space) 0 1.25rem" in stylesheet.text
    assert "margin-top: var(--content-card-top-space)" in stylesheet.text


@pytest.mark.anyio
async def test_quick_interaction_conversation_page_is_available(
    settings: Settings,
) -> None:
    app = create_app(settings)
    manager = MagicMock()
    manager.require_quick_access.return_value = MagicMock()
    app.state.codex_pty_manager = manager
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
    assert 'const displayTitle = title || "未命名 Session"' in session_script.text
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
    assert len(data["documents"]) == 5
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
async def test_terminal_page_uses_session_title(settings: Settings) -> None:
    app = create_app(settings)
    manager = MagicMock()
    manager.require_terminal_access.return_value = CodexSession(
        id="session-1",
        workspace_id="codex",
        workspace_name="chub",
        cwd=Path("/workspace/chub"),
        title="真实会话标题",
        codex_session_id="11111111-1111-4111-8111-111111111111",
    )
    tickets = MagicMock()
    tickets.valid.return_value = True
    connections = MagicMock()
    connections.open_page.return_value.id = "page-1"
    app.state.codex_pty_manager = manager
    app.state.terminal_tickets = tickets
    app.state.terminal_connections = connections
    transport = httpx.ASGITransport(app=app)

    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://test",
        cookies={"chub_terminal": "ticket"},
    ) as client:
        response = await client.get("/codex/session-1")

    assert response.status_code == 200
    assert "真实会话标题 · Codex PTY" in response.text
    assert 'src="/static/terminal.js"' in response.text
    assert 'id="return-codex"' not in response.text
    assert "<header>" not in response.text
    assert 'data-page-id="page-1"' in response.text
    assert "page_id=page-1" in response.text
    assert "disableLeaveAlert=true" in response.text


@pytest.mark.anyio
async def test_system_upgrade_blocks_new_terminal_page_and_backend(
    settings: Settings,
) -> None:
    app = create_app(settings)
    manager = MagicMock()
    tickets = MagicMock()
    tickets.valid.return_value = True
    app.state.codex_pty_manager = manager
    app.state.terminal_tickets = tickets
    app.state.system_upgrade._writes_blocked = True
    transport = httpx.ASGITransport(app=app)

    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://test",
        cookies={"chub_terminal": "ticket"},
    ) as client:
        page = await client.get("/codex/session-1")
        backend = await client.get("/codex/session-1/terminal/index.html")

    assert page.status_code == 409
    assert page.json()["error"]["code"] == "system_upgrade_in_progress"
    assert backend.status_code == 409
    assert backend.json()["error"]["code"] == "system_upgrade_in_progress"
    manager.require_terminal_access.assert_not_called()
    manager.backend_url.assert_not_called()


@pytest.mark.anyio
async def test_terminal_page_detects_when_another_device_takes_over(
    settings: Settings,
) -> None:
    app = create_app(settings)
    connections = MagicMock()
    connections.page_state.return_value = "displaced"
    app.state.terminal_connections = connections
    transport = httpx.ASGITransport(app=app)

    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://test",
        cookies={"chub_terminal": "old-ticket"},
    ) as client:
        response = await client.get("/codex/session-1/connection/page-1")

    assert response.status_code == 200
    assert response.json() == {"state": "displaced"}
    connections.page_state.assert_called_once_with("session-1", "page-1")


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
async def test_workspace_sessions_use_placeholder_for_empty_title(
    settings: Settings,
) -> None:
    transport = httpx.ASGITransport(app=create_app(settings))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/static/js/features/workspace-sessions.js")
        workspace_script = await client.get("/static/workspace.js")
        conversation_script = await client.get("/static/quick_interaction_conversation.js")
        stylesheet = await client.get("/static/css/components.css")

    assert response.status_code == 200
    assert workspace_script.status_code == 200
    assert conversation_script.status_code == 200
    assert stylesheet.status_code == 200
    assert '"未命名 Session"' in response.text
    assert "return relativeTime(session.last_activity_at || session.created_at);" in response.text
    assert 'new URL(window.location.href).searchParams.get("session")' in response.text
    assert "setSelectedQuickSessionLocation(session.id);" in response.text
    assert "restoreSelectedQuickSession(data.sessions);" in response.text
    assert 'if (!selectedSessionUnavailable) setSidebarMessage("");' in response.text
    assert 'if (window.workspaceQuickSessionOpen) {' in response.text
    assert "window.selectWorkspaceQuickSession = (sessionId) =>" in response.text
    assert "window.clearWorkspaceQuickSessionSelection = () =>" in response.text
    assert "window.setWorkspaceToolbarError = (text = \"\") =>" in workspace_script.text
    assert "window.showWorkspaceToolbarFeedback = (text, kind = \"error\") =>" in workspace_script.text
    assert 'fetch("/api/ai/usage", { signal: controller.signal })' in workspace_script.text
    assert "result.data.display.long.trim()" in workspace_script.text
    assert 'setToolbarStatus(usage || "AI 额度暂不可用。")' in workspace_script.text
    assert "void loadQuickSessionToolbarUsage();" in workspace_script.text
    assert "window.setWorkspaceToolbarError?.(text);" in response.text
    assert "const sidebarMessageMinimumVisibleMs = 6000;" in response.text
    assert "sidebarMessageClearTimer = window.setTimeout" in response.text
    assert "workspace-session-message" not in response.text
    assert ".workspace-preview-toolbar-error {" in stylesheet.text
    assert ".workspace-preview-toolbar-error.workspace-preview-toolbar-error-warning" in stylesheet.text
    assert ".chub-toast {" in stylesheet.text
    assert 'className = "workspace-preview-session-more"' in response.text
    assert 'more.setAttribute("aria-haspopup", "menu");' in response.text
    assert 'menu.className = "workspace-session-action-menu";' in response.text
    assert "document.body.append(menu);" in response.text
    assert 'actionButton.setAttribute("role", "menuitem");' in response.text
    assert "toggleSessionActionMenu(more, currentSession, clickPoint);" in response.text
    assert "const eventIsInsideSessionActionMenu = (event) =>" in response.text
    assert 'document.addEventListener("pointerdown", (event) => {' in response.text
    assert 'Boolean(event.target.closest(".workspace-session-action-menu"))' in response.text
    assert 'window.addEventListener("chub.workspace.session-action-menu-dismiss", closeSessionActionMenu);' in response.text
    assert "const triggerRect = trigger.getBoundingClientRect();" in response.text
    assert "const anchorX = Number.isFinite(clickPoint?.x) ? clickPoint.x : triggerRect.right;" in response.text
    assert "const anchorY = Number.isFinite(clickPoint?.y) ? clickPoint.y : triggerRect.bottom;" in response.text
    assert "openSessionActionSessionId = session.id;" in response.text
    assert 'owner === "external"' in response.text
    assert 'return "其他应用 · 正在使用";' in response.text
    assert 'return "正在使用";' in response.text
    assert 'return "等待输入";' in response.text
    assert 'return "执行中";' in response.text
    assert "const sessionHasActiveExecution = (session) =>" in response.text
    assert "const sessionNeedsRefresh = (session) =>" in response.text
    assert "let sessionRequestGeneration = 0;" in response.text
    assert "const requestGeneration = ++sessionRequestGeneration;" in response.text
    assert "if (requestGeneration !== sessionRequestGeneration) return;" in response.text
    assert "|| sessionNeedsRefresh(session)" in response.text
    assert "session.status === \"running\" && session.activity === \"unknown\"" not in response.text
    assert "const sessionIsExternallyOccupied = (session) => session.usage?.owner === \"external\";" in response.text
    assert "more.hidden = sessionIsExternallyOccupied(session);" in response.text
    assert "if (sessionIsExternallyOccupied(session)) return;" in response.text
    assert "const externalQuickReadOnly = externallyOccupied && session.session_mode === \"quick\";" in response.text
    assert "button.disabled = externallyOccupied && !externalQuickReadOnly;" in response.text
    assert "|| sessionIsExternallyOccupied(session)" in response.text
    assert 'showConfirmationDialog({' in response.text
    assert "/title`, {" in response.text
    assert "const opensSessionInNewTab = (event) =>" in response.text
    assert 'window.open(quickSessionUrl(session.id), "_blank", "noopener");' in response.text
    assert 'const terminalTab = newTab ? window.open("", "_blank") : null;' in response.text
    assert "terminalTab.location.replace(data.terminal_url);" in response.text
    assert 'button.addEventListener("auxclick"' in response.text
    assert '"chub.workspace.quick-session-selection"' in workspace_script.text
    assert '"chub.workspace.quick-session-activity"' in workspace_script.text
    assert '"chub.workspace.quick-session-interaction"' in workspace_script.text
    assert '"chub.workspace.quick-session-changed"' in workspace_script.text
    assert "window.refreshWorkspaceSessions?.();" in workspace_script.text
    assert "window.refreshWorkspaceSessions = () =>" in response.text
    assert "function notifyWorkspaceSessionChanged(sessionId, { returnToWorkspace = false } = {})" in conversation_script.text
    assert "notifyWorkspaceSessionChanged(session.id);" in conversation_script.text
    assert "notifyWorkspaceSessionChanged(archivedSessionId);" in conversation_script.text
    assert "notifyWorkspaceSessionChanged(deletedSessionId);" in conversation_script.text
    assert "notifyWorkspaceSessionChanged(conversationSessionId);" in conversation_script.text
    assert "returnToWorkspace: true" in conversation_script.text
    assert 'window.location.replace("/");' in workspace_script.text
    assert "event.source !== frame.contentWindow" in workspace_script.text
    assert "window.selectWorkspaceQuickSession?.(selection.sessionId);" in workspace_script.text
    assert "window.updateWorkspaceQuickSessionActivity?.(" in workspace_script.text
    assert "window.parent.postMessage(" in conversation_script.text
    assert "window.parent !== window" in conversation_script.text
    assert "function notifyWorkspaceSessionActivity()" in conversation_script.text
    assert "function notifyWorkspaceSessionInteraction()" in conversation_script.text
    assert 'document.addEventListener("pointerdown", notifyWorkspaceSessionInteraction, { capture: true });' in conversation_script.text
    assert "notifyWorkspaceSessionActivity();" in conversation_script.text
    assert "window.updateWorkspaceQuickSessionActivity = (sessionId, running, updatedAt) =>" in response.text
    assert 'targetUrl.searchParams.set("embedded", "workspace");' in conversation_script.text
    assert ".workspace-preview-session.is-current {\n  border-color: var(--color-accent);" in stylesheet.text
    assert ".workspace-preview-session.is-current strong {\n  color: var(--color-accent-text);" in stylesheet.text
    assert ".workspace-preview-session-row {" in stylesheet.text
    assert ".workspace-preview-session-more {" in stylesheet.text
    assert ".workspace-preview-session-row.is-current {" in stylesheet.text
    assert ".workspace-preview-session-row.is-externally-occupied:hover," in stylesheet.text
    assert ".workspace-preview-session-row.is-externally-occupied {" in stylesheet.text
    assert ".workspace-preview-session-row.is-externally-occupied .workspace-preview-session:disabled {" in stylesheet.text
    assert ".workspace-preview-session-more:hover," in stylesheet.text
    assert ".workspace-session-action-menu {" in stylesheet.text
    assert "position: fixed;" in stylesheet.text
    assert "width: 10.5rem;" in stylesheet.text
    assert "z-index: 100;" in stylesheet.text
    assert ".workspace-session-action {" in stylesheet.text
    assert ".workspace-session-action.is-danger {" in stylesheet.text
    assert "grid-template-columns: minmax(0, 1fr) 24px;" in stylesheet.text
    assert "grid-template-columns: minmax(0, 1fr) 30px;" in stylesheet.text
    assert ".workspace-preview-session small {\n  color: var(--color-text-muted);" in stylesheet.text
    assert "workspace-session-marquee" in stylesheet.text
    assert "updateSessionMarquee" in response.text
    assert "--workspace-session-marquee-duration" in response.text


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
