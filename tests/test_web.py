import asyncio
from pathlib import Path
from unittest.mock import MagicMock

import httpx
import pytest

from app.application import create_app
from app.codex.models import CodexSession
from app.core.config import Settings
import app.services.weekly_reports as weekly_report_service


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
    assert response.text.count('class="workstation-status-row') == 7
    assert "<strong>AI Runtime</strong>" in response.text
    assert 'id="ai-runtime-detail" class="workstation-status-detail"' in response.text
    assert response.text.index("<strong>Chub Quick Worker</strong>") < response.text.index("<strong>AI Runtime</strong>") < response.text.index("<strong>升级与恢复</strong>")
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
    assert 'href="/automations"' in response.text
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
async def test_cyber_style_is_rendered_before_assets_load(settings: Settings) -> None:
    transport = httpx.ASGITransport(app=create_app(settings))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        client.cookies.set("hub_ui_style", "cyber")
        pages = await asyncio.gather(
            client.get("/"),
            client.get("/settings/quick-interaction"),
                client.get("/automations"),
                client.get("/logs"),
                client.get("/project-docs"),
        )

    assert all(page.status_code == 200 for page in pages)
    for page in pages:
        assert '<html lang="zh-CN" data-ui-style="cyber">' in page.text
        assert '<meta name="color-scheme" content="dark">' in page.text


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
    assert "设置 · Hub" in response.text
    assert 'class="settings-workspace-shell"' in response.text
    assert 'id="settings-sidebar"' in response.text
    assert 'id="settings-sidebar-resizer"' in response.text
    assert 'role="separator"' in response.text
    assert 'aria-valuemin="225"' in response.text
    assert 'aria-valuemax="360"' in response.text
    assert 'id="settings-return-application" class="settings-workspace-return" href="/workspace"' in response.text
    assert "返回应用" in response.text
    assert 'src="/static/settings-sidebar-bootstrap.js"' in response.text
    assert response.text.index('src="/static/settings-sidebar-bootstrap.js"') < response.text.index(
        '/static/css/tokens.css',
    )
    assert 'src="/static/settings-sidebar.js"' in response.text
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
    assert "Cyber" in response.text
    assert "当前风格" in response.text
    assert 'href="/settings/styles/standard"' in response.text
    assert 'href="/settings/styles/cyber"' in response.text
    assert 'href="/settings/workspace-preview"' not in response.text
    assert "工作台交互预览" not in response.text
    assert 'id="cyber-rain-speed"' in response.text
    assert 'id="cyber-rain-brightness"' in response.text
    assert 'id="cyber-rain-density"' in response.text
    assert "风格选择保存在当前浏览器" in response.text
    assert '<h3 id="utility-settings-title">诊断与关于</h3>' in response.text
    assert 'class="settings-utility-row" href="/logs"' in response.text
    assert 'id="settings-maintenance-terminal" class="settings-utility-row" type="button"' in response.text
    assert 'id="maintenance-terminal-dialog" class="codex-workspace-dialog confirmation-dialog"' in response.text
    assert "打开维护终端" in response.text
    assert "Chub 版本" in response.text
    assert 'data-cyber-style-details' in response.text
    cyber_details_end = response.text.index(
        "</details>",
        response.text.index("data-cyber-style-details"),
    )
    assert cyber_details_end < response.text.index(
        'id="cyber-style-settings-message"'
    )
    assert 'data-style-apply="standard"' in response.text
    assert 'data-style-apply="cyber"' in response.text
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
    assert ':root[data-ui-style="cyber"] .settings-choice-picker-option {' in stylesheet.text
    assert ".settings-integration-row > span:first-child" in stylesheet.text
    assert ".settings-workspace-shell" in stylesheet.text
    assert "--settings-sidebar-width: var(--settings-sidebar-preload-width, 225px);" in stylesheet.text
    assert stylesheet.text.count("overscroll-behavior-y: contain;") >= 2
    assert ".settings-workspace-return {\n  display: flex;\n  height: 2.25rem;" in stylesheet.text
    assert "  border-radius: 8px;\n  padding: 0 0.2rem;\n  color: var(--muted);" in stylesheet.text
    assert ".settings-workspace-return:hover,\n.settings-workspace-return:active {\n  color: var(--accent-dark);\n  background: color-mix(in srgb, var(--accent) 8%, transparent);" in stylesheet.text
    assert "grid-template-columns: var(--settings-sidebar-width) minmax(0, 1fr);" in stylesheet.text
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
    assert "/api/openclaw/status" in script.text
    assert "local_access_url" in script.text
    assert "localOpenClawAccessUrl" in script.text
    assert "/api/maintenance-terminal/access" in script.text
    assert 'window.open(data.terminal_url, "_blank", "noopener")' in script.text
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
    assert "hub.cyberRainSpeed.v1" in script.text
    assert "hub.cyberRainBrightness.v1" in script.text
    assert "hub.cyberRainDensity.v1" in script.text
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
        "quick-interaction": "/settings/quick-interaction",
        "appearance": "/settings/appearance",
        "diagnostics": "/settings/diagnostics",
        "runtime": "/settings/runtime",
        "session-defaults": "/settings/session-defaults",
        "weixin-text": "/settings/weixin-text",
        "openclaw": "/settings/openclaw",
    }
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        root = await client.get("/settings", follow_redirects=False)
        legacy_gateway = await client.get("/settings/openclaw/gateway", follow_redirects=False)
        legacy_clawbot = await client.get("/settings/openclaw/clawbot", follow_redirects=False)
        pages = {
            page: await client.get(path)
            for page, path in paths.items()
        }
        script = await client.get("/static/settings.js")
        stylesheet = await client.get("/static/css/components.css")

    assert root.status_code == 307
    assert root.headers["location"] == "/settings/quick-interaction"
    assert legacy_gateway.status_code == 307
    assert legacy_gateway.headers["location"] == "/settings/openclaw"
    assert legacy_clawbot.status_code == 307
    assert legacy_clawbot.headers["location"] == "/settings/openclaw"
    assert all(response.status_code == 200 for response in pages.values())
    for page, response in pages.items():
        assert f'data-settings-page="{page}"' in response.text
        assert 'href="#' not in response.text
        assert 'id="settings-return-application" class="settings-workspace-return" href="/workspace"' in response.text
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

    assert 'href="/settings/quick-interaction" aria-current="page"' in pages["quick-interaction"].text
    assert 'id="quick-interaction-page-size"' in pages["quick-interaction"].text
    assert 'id="runtime-management-list"' not in pages["quick-interaction"].text
    assert 'id="runtime-management-list"' in pages["runtime"].text
    assert 'id="quick-interaction-page-size"' not in pages["runtime"].text
    assert 'id="cyber-rain-speed"' in pages["appearance"].text
    assert 'id="maintenance-terminal-dialog"' in pages["diagnostics"].text
    assert 'id="weixin-processing-mode"' in pages["weixin-text"].text
    assert 'id="settings-openclaw-open"' in pages["openclaw"].text
    assert 'id="settings-openclaw-bind-weixin"' in pages["openclaw"].text
    assert pages["openclaw"].text.index('id="openclaw-gateway-settings-title"') < pages["openclaw"].text.index('id="openclaw-clawbot-settings-title"')
    assert 'href="/settings/openclaw" aria-current="page"' in pages["openclaw"].text
    assert "settings-subnavigation" not in pages["openclaw"].text
    assert 'href="/settings/openclaw/gateway"' not in pages["openclaw"].text
    assert 'href="/settings/openclaw/clawbot"' not in pages["openclaw"].text
    assert script.status_code == 200
    assert 'const settingsPage = document.body.dataset.settingsPage || "";' in script.text
    assert "scrollToSettingsSection" not in script.text
    assert "settingsWorkspaceMain.scrollTo" not in script.text
    assert '.settings-navigation-link[aria-current="page"]' in stylesheet.text
    assert "min-height: 34px;" in stylesheet.text
    assert ".settings-subnavigation" not in stylesheet.text


@pytest.mark.anyio
async def test_standard_style_preview_is_static_and_available(
    settings: Settings,
) -> None:
    transport = httpx.ASGITransport(app=create_app(settings))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/settings/styles/standard")
        script = await client.get("/static/style-preview.js")

    assert response.status_code == 200
    assert "Standard 风格预览 · Hub" in response.text
    assert "以下内容均为静态示例" in response.text
    assert "节点状态" in response.text
    assert "自动化任务" in response.text
    assert "会话工作台" in response.text
    assert "控件与反馈" in response.text
    assert "状态与折叠" in response.text
    assert "暂无可展示内容" in response.text
    assert "刷新失败，已保留上一次成功内容" in response.text
    assert 'data-collapsible-card' in response.text
    assert 'data-collapsible-persist="false"' in response.text
    assert 'type="password"' in response.text
    assert 'type="checkbox"' in response.text
    assert 'href="#standard-conversation-preview"' not in response.text
    assert 'id="standard-conversation-preview"' in response.text
    assert "任务执行中，请稍候" in response.text
    assert "已通知" in response.text
    assert "待通知" in response.text
    assert '<span class="conversation-message-meta">Codex CLI</span>' in response.text
    assert "查看会话" not in response.text
    assert 'class="button-link conversation-pin"' not in response.text
    assert 'id="preview-dialog"' in response.text
    assert script.status_code == 200
    assert "showModal" in script.text
    assert "setupCollapsibleCards" in script.text


@pytest.mark.anyio
async def test_cyber_style_preview_is_available(settings: Settings) -> None:
    transport = httpx.ASGITransport(app=create_app(settings))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/settings/styles/cyber")
        script = await client.get("/static/style-preview.js")
        ai_usage_script = await client.get("/static/js/core/ai-usage.js")
        theme_script = await client.get("/static/theme.js")
        stylesheet = await client.get("/static/css/components.css")

    assert response.status_code == 200
    assert "Cyber 风格预览 · Hub" in response.text
    assert 'class="cyber-preview"' in response.text
    assert 'content="dark"' in response.text
    assert "科技终端版" in response.text
    assert 'class="cyber-matrix"' in response.text
    assert 'id="cyber-rain-speed"' not in response.text
    assert 'id="cyber-rain-brightness"' not in response.text
    assert "0x7F" not in response.text
    assert '<div class="cyber-matrix" aria-hidden="true"></div>' in response.text
    assert "节点状态" in response.text
    assert "快速交互" in response.text
    assert 'data-collapsible-persist="false"' in response.text
    assert script.status_code == 200
    assert ai_usage_script.status_code == 200
    assert theme_script.status_code == 200
    assert stylesheet.status_code == 200
    assert "hub.cyberRainSpeed.v1" in theme_script.text
    assert "hub.cyberRainBrightness.v1" in theme_script.text
    assert "hub.cyberRainDensity.v1" in theme_script.text
    assert "Math.random" in theme_script.text
    assert "rainSequence" in theme_script.text
    assert "RAIN_PHRASES" in theme_script.text
    assert '"good morning"' in theme_script.text
    assert '"build passing"' in theme_script.text
    assert '"thanks again"' in theme_script.text
    assert "randomPhraseRain" in theme_script.text
    assert "setCyberRainQuota" in theme_script.text
    assert "ChubAiUsage?.subscribe" in theme_script.text
    assert "ChubAiUsage?.load" in theme_script.text
    assert "root.dataset.stylePreview ? null : data" in theme_script.text
    assert "loadAiUsage" not in theme_script.text
    assert "clearAiUsage" not in theme_script.text
    assert "/api/ai/usage" not in theme_script.text
    assert 'CACHE_KEY = "hub.aiUsageCache"' in ai_usage_script.text
    assert "/api/ai/usage" in ai_usage_script.text
    assert "window.ChubAiUsage" in ai_usage_script.text
    assert "quotaRainParts" in theme_script.text
    assert 'stream.dataset.rainDynamic = "true"' in theme_script.text
    assert 'stream.dataset.rainKind = "quota"' in theme_script.text
    assert "scaledRainDuration" in theme_script.text
    assert 'character.textContent = "\\u00a0"' in theme_script.text
    assert 'data-rain-dynamic="true"' in stylesheet.text
    assert "Cyber 使用命令式说明和终端化主次按钮表达操作影响" in script.text


@pytest.mark.anyio
async def test_workspace_preview_is_static_and_available(settings: Settings) -> None:
    transport = httpx.ASGITransport(app=create_app(settings))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/workspace")
        removed_preview_response = await client.get("/settings/workspace-preview")
        stylesheet = await client.get("/static/css/components.css")
        workspace_script = await client.get("/static/workspace.js")
        bootstrap_script = await client.get("/static/workspace-bootstrap.js")

    assert response.status_code == 200
    assert removed_preview_response.status_code == 404
    assert "新版首页 · Hub" in response.text
    assert "操作" in response.text
    assert "工作台分区" in response.text
    assert "最近 Session" in response.text
    assert "当前工作" in response.text
    assert "输入消息…" in response.text
    assert "Full access" in response.text
    assert "workspace-preview-shell" in response.text
    assert "workspace-preview-work-surface" in response.text
    assert 'id="workspace-sidebar-toggle"' in response.text
    assert 'id="workspace-sidebar-resizer"' in response.text
    assert 'role="separator"' in response.text
    assert 'aria-valuemin="225"' in response.text
    assert 'aria-valuemax="360"' in response.text
    assert 'id="workspace-sidebar-close"' not in response.text
    assert 'aria-label="工作台操作"' in response.text
    assert '<div class="workspace-preview-brand"><strong>Chub</strong></div>' in response.text
    assert response.text.index("节点在线 · Worker 可用") < response.text.index(
        'aria-label="工作台分区"',
    )
    assert 'aria-label="工作台辅助导航"' in response.text
    assert response.text.index('aria-label="工作台辅助导航"') > response.text.index(
        'id="workspace-preview-recent-title"',
    )
    assert "个人 AI 工作站" not in response.text
    assert '>☰</span></button>' in response.text
    assert "WORKSPACE" not in response.text
    assert "<h1>工作台</h1>" not in response.text
    assert "当前为并行建设页面" not in response.text
    assert '<a href="/settings">设置</a>' in response.text
    assert response.text.count('disabled title="功能建设中"') == 6
    assert 'aria-controls="workspace-sidebar"' in response.text
    assert 'src="/static/workspace-bootstrap.js"' in response.text
    assert response.text.index('src="/static/workspace-bootstrap.js"') < response.text.index(
        '/static/css/tokens.css',
    )
    assert 'src="/static/workspace.js"' in response.text
    assert ".workspace-preview-shell" in stylesheet.text
    assert ".workspace-preview-shell.is-sidebar-collapsed" in stylesheet.text
    assert "--workspace-sidebar-width: var(--workspace-sidebar-preload-width, 225px);" in stylesheet.text
    assert "grid-template-columns: var(--workspace-sidebar-width) minmax(0, 1fr);" in stylesheet.text
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
    assert bootstrap_script.status_code == 200
    assert "event.metaKey || event.ctrlKey" in workspace_script.text
    assert 'event.key.toLowerCase() !== "b"' in workspace_script.text
    assert "workspace-sidebar-close" not in workspace_script.text
    assert "chub.workspace.sidebarCollapsed" in workspace_script.text
    assert "chub.sidebarWidth" in workspace_script.text
    assert "chub.workspace.sidebarWidth" not in workspace_script.text
    assert "minimumSidebarWidth = 225" in workspace_script.text
    assert "maximumSidebarWidth = 360" in workspace_script.text
    assert 'resizer.addEventListener("pointerdown"' in workspace_script.text
    assert "ArrowLeft: currentSidebarWidth() - sidebarWidthStep" in workspace_script.text
    assert 'toggle.title = sidebarLabel;' in workspace_script.text
    assert '@media (min-width: 761px) and (max-width: 1080px)' in stylesheet.text
    assert ':root[data-workspace-sidebar-collapsed="true"] .workspace-preview-shell,' in stylesheet.text
    assert "const expandSidebar = () =>" in workspace_script.text
    assert "const collapseSidebar = () =>" in workspace_script.text
    assert "shell.classList.add(\"is-sidebar-opening\")" in workspace_script.text
    assert "shell.classList.add(\"is-sidebar-closing\")" in workspace_script.text
    assert 'requestAnimationFrame(() => shell.classList.add("is-layout-ready"));' in workspace_script.text
    assert "workspace-sidebar-preload-width" in bootstrap_script.text
    assert "chub.sidebarWidth" in bootstrap_script.text
    assert "data-workspace-sidebar-collapsed" in stylesheet.text
    assert ".workspace-preview-shell.is-layout-ready" in stylesheet.text


@pytest.mark.anyio
async def test_home_page_uses_configured_page_title(settings: Settings) -> None:
    settings.app.page_title = "Ubuntu · Hub"
    transport = httpx.ASGITransport(app=create_app(settings))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/")

    assert "<title>Ubuntu · Hub</title>" in response.text
    assert "<h1>Ubuntu · Hub</h1>" in response.text


@pytest.mark.anyio
async def test_home_page_reports_design_document_index_error(
    settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "app.services.design_documents.DOCUMENTS_INDEX",
        Path("/missing/design_documents.json"),
    )
    transport = httpx.ASGITransport(app=create_app(settings))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/")
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
async def test_home_page_title_keeps_backward_compatible_default(
    settings: Settings,
) -> None:
    transport = httpx.ASGITransport(app=create_app(settings))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/")

    assert "<title>Hub 管理面板</title>" in response.text


@pytest.mark.anyio
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
    assert "实时终端 · 等待输入" in script.text
    assert "实时终端 · 执行中" in script.text
    assert "实时终端 · 正在使用" in script.text
    assert "快速交互 · 待输入" in script.text
    assert "快速交互 · 执行中" in script.text
    assert "快速交互 · 等待结果" not in script.text
    assert "活动状态未知 · 请刷新" in script.text
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
        session_script = await client.get("/static/quick_interaction_session.js")
        timeline_script = await client.get("/static/quick_interaction_timeline.js")
        script = await client.get("/static/quick_interaction_conversation.js")
        stylesheet = await client.get("/static/css/components.css")

    assert removed_page.status_code == 404
    assert page.status_code == 200
    assert 'data-session-id="session-1"' in page.text
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
    assert 'id="conversation-rename-dialog"' in page.text
    assert 'id="conversation-rename-input"' in page.text
    assert 'id="conversation-archive-dialog"' in page.text
    assert 'id="conversation-archive-confirm"' in page.text
    assert 'id="conversation-delete-dialog"' in page.text
    assert 'id="conversation-delete-confirm"' in page.text
    assert 'class="codex-workspace-dialog confirmation-dialog conversation-archive-dialog"' in page.text
    assert 'class="confirmation-dialog-description"' in page.text
    assert 'id="conversation-create-dialog"' in page.text
    assert 'id="conversation-create-workspaces"' in page.text
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
    assert 'data-value="ask" disabled aria-disabled="true"' in page.text
    assert 'id="conversation-model-trigger"' in page.text
    assert 'id="conversation-reasoning-trigger"' in page.text
    assert (
        page.text.index("/static/quick_interactions_core.js")
        < page.text.index("/static/quick_interaction_session.js")
        < page.text.index("/static/quick_interaction_timeline.js")
        < page.text.index("/static/quick_interaction_conversation.js")
    )
    assert session_script.status_code == 200
    assert timeline_script.status_code == 200
    assert script.status_code == 200
    assert 'order: "timeline"' in script.text
    assert "CONVERSATION_PAGE_SIZE = readConversationPageSize()" in script.text
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
    assert "conversationTimelineView.restoreTopAnchor(anchor)" in script.text
    assert "client.submitTask" in script.text
    assert "client.loadSessionContext" in script.text
    assert "conversationClient.createSession" in script.text
    assert "readConversationSessionCreationPreferences" not in script.text
    assert "shouldRetryConversationCreationWithDefaults" not in script.text
    assert "clearConversationSessionModelPreferences" not in script.text
    assert "updateSessionConfiguration" in script.text
    assert "conversationSessionView.openCreate" in script.text
    assert 'label: "跟随模型默认"' in script.text
    assert 'description: defaultLevel' in script.text
    assert "workspace.available !== true" in session_script.text
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
    assert 'window.history.replaceState(window.history.state, "", url)' in script.text
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
    assert 'elements.submit.setAttribute("aria-label", "发送")' in session_script.text
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
    assert ".conversation-session-switcher" in stylesheet.text
    assert ".conversation-session-navigation" in stylesheet.text
    assert "grid-template-columns: 30px minmax(0, 1fr);" in stylesheet.text
    assert ".conversation-session-create" in stylesheet.text
    assert ".conversation-create-surface" in stylesheet.text
    assert ":not(.conversation-session-create)" in stylesheet.text
    assert ":not(.conversation-session-switch)" in stylesheet.text
    assert "overflow-x: auto;" in stylesheet.text
    assert "overscroll-behavior-x: contain;" in stylesheet.text
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
    assert ":not(.conversation-session-rename)" in stylesheet.text
    assert ":not(.conversation-session-archive)" in stylesheet.text
    assert ":not(.site-header-title):not(.session-enter)::before" in stylesheet.text
    assert 'a.button-link::before' in stylesheet.text
    assert ':root[data-ui-style="cyber"] .workspace-button strong' in stylesheet.text
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
async def test_automation_details_page_and_script_are_available(
    settings: Settings,
) -> None:
    transport = httpx.ASGITransport(app=create_app(settings))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        page = await client.get("/automations")
        script = await client.get("/static/automations.js")

    assert page.status_code == 200
    assert "全部任务" in page.text
    assert "返回首页" not in page.text
    assert "standalone-list-card" in page.text
    assert 'id="detail-automation-list"' in page.text
    assert script.status_code == 200
    assert "appendWeeklyReportMaterials" in script.text
    assert "weeklyDownloadStatus" in script.text
    assert "weeklyValidationStatus" in script.text
    assert "下载成功" in script.text
    assert "校验通过" in script.text
    assert "automation-material-summary" in script.text
    assert "本期下载 ·" in script.text
    assert "启动并运行" in script.text
    assert 'request("/api/automations/browser/start"' in script.text
    assert 'JSON.stringify({ mode: "headless" })' in script.text
    assert "/api/automations?all_tasks=true" in script.text
    assert "showMessage(data.browser_message" not in script.text
    assert "innerHTML" not in script.text
    assert "default-src 'self'" in page.headers["content-security-policy"]


@pytest.mark.anyio
async def test_design_document_pages_render_markdown(settings: Settings) -> None:
    transport = httpx.ASGITransport(app=create_app(settings))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        home = await client.get("/")
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
async def test_codex_sessions_use_placeholder_for_empty_title(
    settings: Settings,
) -> None:
    transport = httpx.ASGITransport(app=create_app(settings))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/static/js/features/codex-sessions.js")

    assert response.status_code == 200
    assert 'session.title || "未命名 Session"' in response.text


@pytest.mark.anyio
async def test_page_uses_external_script_only(settings: Settings) -> None:
    transport = httpx.ASGITransport(app=create_app(settings))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/")

    expected_scripts = [
        "/static/codex_polling.js",
        "/static/js/core/dashboard-core.js",
        "/static/js/components/ui.js",
        "/static/js/components/collapsible-card.js",
        "/static/js/features/node-status.js",
        "/static/js/features/codex-sessions.js",
        "/static/js/features/openclaw.js",
        "/static/js/features/automations.js",
        "/static/js/features/workstation.js",
        "/static/js/features/project-documents.js",
        "/static/app.js",
    ]
    assert response.text.count("<script") == len(expected_scripts) + 2
    assert '<script src="/static/js/core/ai-usage.js"></script>' in response.text
    assert '<script src="/static/theme.js"></script>' in response.text
    positions = [
        response.text.index('<script src="/static/js/core/ai-usage.js"></script>'),
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
