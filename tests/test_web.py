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
async def test_home_page_is_public_and_contains_no_token(
    settings: Settings,
    weekly_reports_root: Path,
) -> None:
    transport = httpx.ASGITransport(app=create_app(settings))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/")

    assert response.status_code == 200
    assert 'type="password"' in response.text
    assert 'src="/static/app.js"' in response.text
    assert 'src="/static/theme.js"' in response.text
    assert '<html lang="zh-CN" data-ui-style="standard">' in response.text
    assert '<meta name="color-scheme" content="light">' in response.text
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
    assert 'id="refresh-status"' in response.text
    assert "节点任务" not in response.text
    assert 'id="codex-card-host"' in response.text
    assert 'id="openclaw-title"' in response.text
    assert "OpenClaw 环境" in response.text
    assert 'id="refresh-openclaw"' in response.text
    assert 'id="openclaw-start"' in response.text
    assert 'id="openclaw-restart"' in response.text
    assert 'id="openclaw-stop"' in response.text
    assert 'id="openclaw-bind-weixin"' in response.text
    assert 'id="openclaw-weixin-dialog"' in response.text
    assert 'id="openclaw-weixin-qr"' in response.text
    assert 'id="openclaw-weixin-verify-form"' in response.text
    assert 'id="openclaw-access-open"' in response.text
    assert 'id="openclaw-access-url"' not in response.text
    assert 'id="openclaw-access-unavailable"' not in response.text
    assert "远程访问未启用" not in response.text
    assert 'data-card-key="openclaw"' in response.text
    assert 'class="openclaw-status-panel"' in response.text
    assert "网关状态" in response.text
    assert "消息通道" in response.text
    assert "访问入口" in response.text
    assert 'class="openclaw-status-row openclaw-access"' in response.text
    assert "微信通道" not in response.text
    assert 'id="openclaw-channels" class="badge badge-muted"' in response.text
    assert 'id="openclaw-owner"' not in response.text
    assert 'id="openclaw-version"' not in response.text
    assert 'id="openclaw-service"' not in response.text
    assert 'id="openclaw-bind"' not in response.text
    assert 'id="openclaw-checked-at"' not in response.text
    assert 'id="automation-title"' in response.text
    assert 'id="automation-list"' in response.text
    assert 'id="automation-environment-title"' in response.text
    assert "自动化环境" in response.text
    assert 'id="refresh-automation-environment"' in response.text
    assert 'id="automation-environment-message"' in response.text
    assert 'data-card-key="automation-environment"' in response.text
    assert 'data-card-key="automation-environment" data-collapsible-card data-collapsed="true"' in response.text
    assert response.text.index('id="codex-card-host"') < response.text.index(
        'data-card-key="project-docs"'
    ) < response.text.index('data-card-key="automations"') < response.text.index(
        'data-card-key="automation-environment"'
    ) < response.text.index('data-card-key="openclaw"') < response.text.index(
        'data-card-key="logs"'
    )
    assert 'id="automation-browser-control"' in response.text
    assert 'aria-controls="automation-browser-dialog"' in response.text
    assert 'id="automation-browser-dialog"' in response.text
    assert 'id="automation-browser-form"' in response.text
    assert 'id="automation-browser-profile"' in response.text
    assert 'name="automation-browser-mode" value="headless" checked' in response.text
    assert 'name="automation-browser-mode" value="headed"' in response.text
    assert 'id="automation-browser-mode"' not in response.text
    assert 'id="automation-feishu-badge"' in response.text
    assert 'id="automation-feishu-check"' in response.text
    assert 'id="automation-feishu-login"' in response.text
    assert 'id="automation-feishu-qr"' in response.text
    assert 'id="automation-feishu-verify"' not in response.text
    assert "飞书环境" in response.text
    assert "有界面" in response.text
    assert "无界面" in response.text
    assert response.text.index('value="headless" checked') < response.text.index(
        'value="headed"'
    )
    assert 'id="refresh-automations"' in response.text
    assert "复用登录状态执行自动化任务。" in response.text
    assert 'id="refresh-project-docs"' in response.text
    assert 'id="project-docs-count"' not in response.text
    assert 'href="/automations"' in response.text
    assert 'id="design-documents-title"' in response.text
    assert "项目文档" in response.text
    assert 'id="weekly-reports-title"' in response.text
    assert "本期周报 · 2026-08-03至2026-08-09" in response.text
    assert 'id="weekly-report-list"' in response.text
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
    assert 'data-card-key="automations"' in response.text
    assert 'data-card-key="logs"' in response.text
    assert 'data-card-return-refresh="true"' in response.text
    openclaw_card = response.text.split('data-card-key="openclaw"', 1)[1].split(
        "</section>",
        1,
    )[0]
    assert 'data-card-return-refresh="true"' not in openclaw_card
    assert "OpenClaw 方案调研" not in response.text
    assert "持续维护" in response.text
    assert response.text.count("document-archive-action") == 5
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
    assert 'aria-controls="confirmation-dialog"' in response.text
    assert 'id="confirmation-dialog"' in response.text
    assert 'id="confirmation-dialog-message"' in response.text
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
    assert response.text.count('class="card-content-inner"') == 5
    assert "退出" in response.text
    assert 'id="task-list"' not in response.text
    assert "data-log-source" in response.text
    assert 'href="/logs"' in response.text
    assert 'class="card logs-card"' in response.text
    assert '<h2 id="logs-title">日志</h2>' in response.text
    assert 'class="log-toolbar" role="group" aria-label="日志显示设置"' in response.text
    assert 'class="log-toolbar-controls"' in response.text
    logs_heading = response.text.split(
        '<section class="card logs-card"', 1
    )[1].split('<div class="log-toolbar"', 1)[0]
    assert logs_heading.count("button-link") == 0
    assert 'id="log-lines"' not in logs_heading
    assert 'id="load-logs"' in logs_heading
    assert 'id="status-details"' not in response.text
    assert "展开详情" not in response.text
    assert settings.security.token.get_secret_value() not in response.text


@pytest.mark.anyio
async def test_cyber_style_is_rendered_before_assets_load(settings: Settings) -> None:
    transport = httpx.ASGITransport(app=create_app(settings))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        client.cookies.set("hub_ui_style", "cyber")
        pages = await asyncio.gather(
            client.get("/"),
            client.get("/settings"),
            client.get("/automations"),
            client.get("/logs"),
            client.get("/project-docs"),
            client.get("/codex/session-1/quick-interactions/conversation"),
        )

    assert all(page.status_code == 200 for page in pages)
    for page in pages:
        assert '<html lang="zh-CN" data-ui-style="cyber">' in page.text
        assert '<meta name="color-scheme" content="dark">' in page.text


@pytest.mark.anyio
async def test_settings_page_supports_quick_interaction_page_size_preference(
    settings: Settings,
) -> None:
    transport = httpx.ASGITransport(app=create_app(settings))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/settings")
        script = await client.get("/static/settings.js")
        theme_script = await client.get("/static/theme.js")

    assert response.status_code == 200
    assert "设置 · Hub" in response.text
    assert "快速交互" in response.text
    assert "调整会话历史记录的加载方式。" in response.text
    assert "调整浏览器偏好和节点功能。" in response.text
    assert "界面风格" in response.text
    assert "微信处理" in response.text
    assert 'id="weixin-translation-enabled"' in response.text
    assert "会增加 Codex 用量，并将微信正文交给模型处理" in response.text
    assert "关闭只阻止新翻译，已有任务继续完成" in response.text
    assert "全部结束后会自动归档内部翻译 Session" in response.text
    assert "Standard" in response.text
    assert "Cyber" in response.text
    assert "当前风格" in response.text
    assert 'href="/settings/styles/standard"' in response.text
    assert 'href="/settings/styles/cyber"' in response.text
    assert 'id="cyber-rain-speed"' in response.text
    assert 'id="cyber-rain-brightness"' in response.text
    assert 'id="cyber-rain-density"' in response.text
    assert "风格选择保存在当前浏览器" in response.text
    assert 'data-style-apply="standard"' in response.text
    assert 'data-style-apply="cyber"' in response.text
    assert 'name="quick-interaction-view"' not in response.text
    assert "任务视图" not in response.text
    assert 'id="quick-interaction-page-size"' in response.text
    assert '<option value="5" selected>5 条</option>' in response.text
    assert '<option value="10">10 条</option>' in response.text
    assert 'id="codex-default-permission"' in response.text
    assert "设置页面展示与之后新建会话的默认配置。" in response.text
    assert 'id="codex-show-translation-session"' in response.text
    assert "显示翻译 Session" in response.text
    assert "控制当前浏览器所有 Session 列表" in response.text
    assert "不影响微信翻译、任务执行及自动归档" in response.text
    assert 'id="codex-default-model"' in response.text
    assert 'id="codex-default-reasoning-effort"' in response.text
    assert "模型列表与默认值由当前节点的 Codex 提供。" in response.text
    assert "工作区配置可能覆盖默认值；Ultra 会自动拆分并行任务。" in response.text
    assert '<option value="full-access">Full access</option>' in response.text
    assert "只影响后续新建的 Session，已有会话保持原权限。" in response.text
    assert "尚未开放" not in response.text
    assert f"v{settings.app.version}" in response.text
    assert "返回首页" not in response.text
    assert script.status_code == 200
    assert "hub.quickInteractionView.v1" not in script.text
    assert "hub.quickInteractionPageSize.v1" in script.text
    assert "hub.codexDefaultPermission.v1" in script.text
    assert "hub.codexDefaultModel.v1" in script.text
    assert "hub.codexDefaultReasoningEffort.v1" in script.text
    assert "hub.codexShowTranslationSession.v1" in script.text
    assert "当前浏览器无法保存 Session 展示偏好" in script.text
    assert "/api/codex/models" in script.text
    assert "/api/settings/weixin-translation" in script.text
    assert "项翻译仍在处理中" in script.text
    assert "已开启，将从下一条微信普通任务开始处理" not in script.text
    assert "已关闭，新任务不再翻译" not in script.text
    assert "设置结果未知，请稍后刷新页面重试" in script.text
    assert "暂时无法刷新翻译任务状态，正在重试" in script.text
    assert "window.setTimeout" in script.text
    assert 'id="weixin-translation-status"' not in response.text
    assert "之后新建的 Session 将使用该权限" in script.text
    assert "之后新建的 Session 将使用该模型与等级" not in script.text
    assert "localStorage.setItem" in script.text
    assert "hub.cyberRainSpeed.v1" in script.text
    assert "hub.cyberRainBrightness.v1" in script.text
    assert "hub.cyberRainDensity.v1" in script.text
    assert "ChubTheme.applyStyle" in script.text
    assert theme_script.status_code == 200
    assert "hub.uiStyle.v1" in theme_script.text
    assert "下次进入快速交互时生效" in script.text
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
    assert "Codex 会话" in response.text
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
    assert 'class="button-link conversation-pin"' in response.text
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
    assert "loadAiUsage" in theme_script.text
    assert "clearAiUsage" in theme_script.text
    assert 'AI_USAGE_CACHE_KEY = "hub.aiUsageCache"' in theme_script.text
    assert "/api/ai/usage" in theme_script.text
    assert "quotaRainParts" in theme_script.text
    assert 'stream.dataset.rainDynamic = "true"' in theme_script.text
    assert 'stream.dataset.rainKind = "quota"' in theme_script.text
    assert "scaledRainDuration" in theme_script.text
    assert 'character.textContent = "\\u00a0"' in theme_script.text
    assert 'data-rain-dynamic="true"' in stylesheet.text
    assert "Cyber 使用命令式说明和终端化主次按钮表达操作影响" in script.text


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
        api_response = await client.get(
            "/api/project-docs",
            headers={
                "Authorization": (
                    f"Bearer {settings.security.token.get_secret_value()}"
                )
            },
        )

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
            await client.get("/static/theme.js"),
            await client.get("/static/js/core/dashboard-core.js"),
            await client.get("/static/js/components/ui.js"),
            await client.get("/static/js/components/collapsible-card.js"),
            await client.get("/static/js/features/node-status.js"),
            await client.get("/static/js/features/codex-sessions.js"),
            await client.get("/static/js/features/openclaw.js"),
            await client.get("/static/js/features/automations.js"),
            await client.get("/static/js/features/project-documents.js"),
            await client.get("/static/js/features/logs.js"),
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

    assert all(script.status_code == 200 for script in scripts)
    assert polling_script.status_code == 200
    assert removed_stylesheet.status_code == 404

    assert all(asset.status_code == 200 for asset in stylesheets)
    assert terminal_stylesheet.status_code == 200
    assert terminal_script.status_code == 200
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
    assert "/api/openclaw/${action}" in dashboard_script
    assert "OPENCLAW_STATUS_CACHE_KEY" in dashboard_script
    assert "restoreOpenClawCache" in dashboard_script
    assert 'data.owner_state === "not_configured"' in dashboard_script
    assert 'data.owner_state === "unavailable"' in dashboard_script
    assert 'dashboardNavigationEntry?.type === "back_forward"' in dashboard_script
    assert "if (dashboardIsHistoryReturn && openclawCacheRestored)" in dashboard_script
    assert "cardLoads.push(loadOpenClawWeixinStatus())" in dashboard_script
    assert "状态刷新失败，当前展示上次检测结果" in dashboard_script
    assert 'restart: ["正在重启", "muted"]' in dashboard_script
    assert "operationVersion !== openclawOperationVersion" in dashboard_script
    assert "if (openclawBusy) {" in dashboard_script
    assert "正在重启 OpenClaw Gateway" not in dashboard_script
    assert "OpenClaw Gateway 已停止" not in dashboard_script
    assert "openclawWeixinPollFailures" in dashboard_script
    assert "setTimeout(\n          pollOpenClawWeixinLogin,\n          retryDelay" in dashboard_script
    assert "Promise.all([\n      apiFetch(\"/api/openclaw/status\")" not in dashboard_script
    assert "automationBrowserModeInputs" in dashboard_script
    assert "loadAutomationEnvironment" in dashboard_script
    assert "automationBrowserDialog.showModal()" in dashboard_script
    assert "automationBrowserDialogConfirm" in dashboard_script
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
    assert "本期下载 ·" in dashboard_script
    assert "待下载" in dashboard_script
    assert "启动并运行" in dashboard_script
    assert 'apiFetch("/api/automations/browser/start"' in dashboard_script
    assert 'JSON.stringify({ mode: "headless" })' in dashboard_script
    assert 'error.message || "Debug Chrome 启动失败。"' in dashboard_script
    assert "Promise.all([loadAutomations(), loadAutomationEnvironment()])" in dashboard_script
    assert "/api/project-docs" in dashboard_script
    assert "loadProjectDocuments" in dashboard_script
    assert 'document.createElement("time")' in dashboard_script
    assert "正在刷新文档列表" not in dashboard_script
    assert "文档列表已更新" not in dashboard_script
    assert "文档已归档" not in dashboard_script
    assert "sessionStorage" in dashboard_script
    assert "localStorage" in dashboard_script
    assert "Authorization" in dashboard_script
    assert "accessVersion" in dashboard_script
    assert "connectWithToken" in dashboard_script
    assert "退出会清除此浏览器保存的 Hub Token" in dashboard_script
    assert "createTaskCard" not in script.text
    assert "createCodexCard" in script.text
    assert 'createButton.textContent = "新建会话"' in script.text
    assert 'workspaceDialogTitle.textContent = "选择工作目录"' in script.text
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
    assert 'window.addEventListener("pageshow"' in script.text
    assert 'document.addEventListener("visibilitychange"' in script.text
    assert "stopCodexSession" in script.text
    assert "archiveCodexSession" in script.text
    assert 'quickInteraction.textContent = "快速交互"' not in script.text
    assert 'interactionHistory.textContent = "交互记录"' not in script.text
    assert "CODEX_ENTRY_MODE_KEY" in script.text
    assert "QUICK_INTERACTION_VIEW_KEY" not in script.text
    assert "quickInteractionUrl" in script.text
    assert "quick-interactions/conversation" in script.text
    assert "openCodexEntryDialog" not in script.text
    assert "toggleCodexEntryMode" in script.text
    assert "updateCodexEntryButton" in script.text
    assert "点击切换为" in script.text
    assert "session.terminal_access_allowed === false" in script.text
    assert "文本优化与翻译 Session 仅支持快速交互" in script.text
    assert "actions.append(entry, stop, archive);" in script.text
    assert "permissionPanel" not in script.text
    assert "快速交互已提交" not in script.text
    assert "quick-interaction-submit" not in script.text
    assert "confirm_stop_unknown_terminal" not in script.text
    assert "unknownConfirmationInput" not in script.text
    assert "removeCodexSession" not in script.text
    assert "renderCodexWorkspaces" in script.text
    assert "renderCodexSessions" in script.text
    assert "codexSessionsNewestFirst" in script.text
    assert "visibleCodexSessions" in script.text
    assert 'session.workspace_id !== "weixin-translation"' in script.text
    assert "visibleSessions.length" in script.text
    assert "会话 · 等待输入" in script.text
    assert "实时终端 · 执行中" in script.text
    assert "快速交互 · 执行中" in script.text
    assert "会话 · 状态未知" in script.text
    assert "尚未启动 · 可进入" in script.text
    assert "终端访问异常 · 可重试" in script.text
    assert "会话异常 · 可重试" in script.text
    assert "CODEX_POLL_FAST_MS = 2000" in script.text
    assert "CODEX_POLL_SLOW_MS = 8000" in script.text
    assert "CODEX_POLL_SLOW_AFTER_MS = 2 * 60 * 1000" in script.text
    assert 'session.activity === "working"' in polling_script.text
    assert "loadCodexSessions({ background: true })" in script.text
    assert "loadCodexSessions({ force: true })" in script.text
    assert "session.quick_interaction_running" in script.text
    assert "CODEX_DEFAULT_PERMISSION_KEY" in script.text
    assert 'permission_mode: readCodexDefaultPermission()' in script.text
    assert "const preferredModel = readCodexDefaultModel()" in script.text
    assert "const preferredEffort = readCodexDefaultReasoningEffort()" in script.text
    assert "clearCodexModelPreferences()" in script.text
    assert "await createRequest(null, null)" in script.text
    assert "archive.disabled = !session.codex_session_id" in script.text
    assert "|| quickInteractionRunning" in script.text
    assert "llmInteractionRunning" not in script.text
    assert "codexLoadPromise" in script.text
    assert "AI_USAGE_CACHE_KEY" in script.text
    assert "CODEX_MODEL_PREFERENCE_CACHE_KEY" in script.text
    assert "CODEX_QUOTA_REFRESH_MS = 5 * 60 * 1000" in script.text
    assert '"/api/codex/models"' in script.text
    assert "新建默认：正在读取…" in script.text
    assert "renderCodexModelPreference" in script.text
    assert "restoreCodexModelPreferenceCache" in script.text
    assert "storeCodexModelPreferenceCache" in script.text
    assert 'elements.codexModelPreference.dataset.hasValue = "true"' in script.text
    assert 'elements.codexModelPreference?.dataset.hasValue !== "true"' in script.text
    assert "新建默认：暂时无法确认模型与等级" in script.text
    assert "|| data?.default_reasoning_effort" in script.text
    assert "跟随 Codex 默认（${modelAndEffort}）" not in script.text
    assert "/api/ai/usage" in script.text
    assert "额度：正在读取…" in script.text
    assert "renderCodexQuota" in script.text
    assert "Weekly" in script.text
    assert "codexQuotaWindowLabel" not in script.text
    assert "refreshQuota: true" in script.text
    assert "loadCodexSessions({ refreshModelPreference: true })" in script.text
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
    assert "远程开发" in script.text
    assert "Codex PTY" in script.text
    assert "远程管理本机 Codex 会话。" in script.text
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
    assert "elements.restartHub.disabled = true" in script.text
    assert "scrollCodexPanelIntoView" not in script.text
    assert "任务状态已更新。" not in script.text
    assert "/api/maintenance/restart" in script.text
    assert "/api/health" in script.text
    assert "waitForHubRestart" in script.text
    assert "elements.globalMessage" in script.text
    assert "refreshCardsAfterRestart" in script.text
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
    assert ".confirmation-dialog-surface" in stylesheet.text
    assert ".confirmation-dialog-actions" in stylesheet.text
    assert "-webkit-tap-highlight-color: transparent" in stylesheet.text
    assert ".logs-card" in stylesheet.text
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
    transport = httpx.ASGITransport(app=create_app(settings))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        removed_page = await client.get("/codex/session-1/quick-interactions")
        page = await client.get(
            "/codex/session-1/quick-interactions/conversation"
        )
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
    assert 'id="conversation-session-archive"' in page.text
    assert 'id="conversation-rename-dialog"' in page.text
    assert 'id="conversation-rename-input"' in page.text
    assert 'id="conversation-archive-dialog"' in page.text
    assert 'id="conversation-archive-confirm"' in page.text
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
    ) < page.text.index('id="conversation-session-archive"')
    assert 'id="conversation-engine"' not in page.text
    assert 'id="conversation-more"' not in page.text
    assert 'id="conversation-submit" class="button-secondary" type="submit" disabled' in page.text
    assert page.text.index("/static/quick_interactions_core.js") < page.text.index(
        "/static/quick_interaction_conversation.js"
    )
    assert script.status_code == 200
    assert 'order: "timeline"' in script.text
    assert "CONVERSATION_PAGE_SIZE = readConversationPageSize()" in script.text
    assert "before: { createdAt: oldest.created_at, id: oldest.id }" in script.text
    assert "performLoadEarlierConversation(generation, client)" in script.text
    assert "conversationPollDelay(conversationPollFailureCount)" in script.text
    assert "resizeConversationPrompt" in script.text
    assert "updateConversationComposerActions" in script.text
    assert "setConversationMoreExpanded" not in script.text
    assert "conversationSelectedEngine" not in script.text
    assert "isConversationNearBottom" in script.text
    assert 'event.key === "Enter"' in script.text
    assert "if (!conversationSubmit.disabled)" in script.text
    assert "canSubmitConversation" in script.text
    assert "conversationEngine" not in script.text
    assert "conversationScroll.scrollTop" in script.text
    assert "client.submitTask" in script.text
    assert "client.loadSessionContext" in script.text
    assert "conversationClient.createSession" in script.text
    assert "readConversationSessionCreationPreferences" in script.text
    assert "shouldRetryConversationCreationWithDefaults" in script.text
    assert "clearConversationSessionModelPreferences" in script.text
    assert "conversationCreateDialog.showModal()" in script.text
    assert "workspace.available !== true" in script.text
    assert "conversationCreationPending" in script.text
    assert "renderConversationSessionCreation(sessionContextResult.value)" in script.text
    assert "switchConversationSession(" in script.text
    assert 'label.textContent = `${sessionLabel} · ${status}`' in script.text
    assert "conversationSessionStatus" in script.text
    assert "conversationSessionLabels" in script.text
    assert 'const displayTitle = title || "未命名 Session"' in script.text
    assert 'document.title = `${displayTitle} · 快速交互`' in script.text
    assert "client.renameSession(title)" in script.text
    assert "conversationRenameDialog.showModal()" in script.text
    assert "conversationRenamePending" in script.text
    assert 'conversationRenameForm.setAttribute("aria-busy", "true")' in script.text
    assert "conversationRenameClose.disabled = true" in script.text
    assert "conversationRenameCancel.disabled = true" in script.text
    assert "client.archiveSession()" in script.text
    assert "conversationArchiveDialog.showModal()" in script.text
    assert "firstConversationSessionAfterArchive" in script.text
    assert "conversationSessions = sessions" in script.text
    assert "window.location.replace(nextSessionUrl)" in script.text
    assert '"/api/ai/usage"' not in script.text
    assert "loadConversationQuotaRain" not in script.text
    assert "`/codex/${encodeURIComponent(nextSession.id)}/quick-interactions/conversation`" in script.text
    assert ': "/";' in script.text
    assert "const archiveReady = Boolean(session.codex_session_id)" in script.text
    assert "conversationSessionArchive.disabled = !archiveReady || archiveBusy" in script.text
    assert 'session.workspace_id !== "weixin-translation"' in script.text
    assert "conversationSessionRename.disabled = !renameAllowed" in script.text
    assert "conversationSessionNavigationMode" in script.text
    assert 'button.setAttribute("aria-current", "page")' in script.text
    assert "handleConversationSessionSwitch" in script.text
    assert "button.dataset.sessionId" in script.text
    assert "button.dataset.sessionUrl" in script.text
    assert 'window.history.replaceState(window.history.state, "", url)' in script.text
    assert "window.location.reload()" not in script.text
    assert "resetConversationSessionView" in script.text
    assert "renderConversationSessionPreview" in script.text
    assert 'conversationSessionTitleRow.setAttribute("aria-busy", "true")' in script.text
    assert 'conversationSessionTitleRow.removeAttribute("aria-busy")' in script.text
    assert "conversationSessionTitleRow.hidden = true" not in script.text
    assert "conversationGeneration += 1" in script.text
    assert "generation !== conversationGeneration" in script.text
    assert "document.body.dataset.sessionId = sessionId" in script.text
    assert 'window.open(url, "_blank", "noopener")' in script.text
    assert 'addEventListener("auxclick", handleConversationSessionSwitch)' in script.text
    assert 'document.createElement("a")' not in script.text
    assert 'document.createElement("button")' in script.text
    assert "mode === \"new-tab\"" in script.text
    assert "mode === \"default\"" in script.text
    assert "mode === \"ignore\"" in script.text
    assert "hub.quickInteractionSessionNumbers.v1" not in script.text
    assert "const sessionLabel = labels.get(session.id)" in script.text
    assert "conversationSessionSwitcher.hidden = ordered.length === 0" in script.text
    assert "hub.quickInteractionDraft.v1" in script.text
    assert "sessionStorage.setItem(conversationDraftKey" in script.text
    assert 'conversationSubmit.textContent = "发送"' in script.text
    assert '"确认发送"' not in script.text
    assert 'pending: "待通知"' in script.text
    assert 'sent: "已通知"' in script.text
    assert 'failed: "通知失败"' in script.text
    assert 'skipped: "未通知"' in script.text
    assert 'succeeded: "Chub 已完成自动重启，服务已恢复。"' in script.text
    assert "Chub 已完成自动重启，服务已恢复。" in script.text
    assert 'task.deferred_restart_status === "pending"' in script.text
    assert 'failed: "重启结果通知失败"' in script.text
    assert "Chub 自动重启未完成" in script.text
    assert "task.deferred_restart_error" in script.text
    assert "旧记录没有保存具体原因，请查看 Chub 运行日志" in script.text
    assert ".conversation-assistant-info" in stylesheet.text
    assert "client.setPinned" in script.text
    assert "textContent" in script.text
    assert "innerHTML" not in script.text
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
    assert "grid-template-columns: 36px minmax(0, 1fr);" in stylesheet.text
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
    assert "margin-left: 0.35rem;" in stylesheet.text
    assert ".conversation-rename-form" in stylesheet.text
    assert "conversation-session-archive:not(:disabled):hover" in stylesheet.text
    assert ":not(.conversation-session-rename)" in stylesheet.text
    assert ":not(.conversation-session-archive)" in stylesheet.text
    assert ":not(.site-header-title):not(.session-enter)::before" in stylesheet.text
    assert 'a.button-link::before' in stylesheet.text
    assert ':root[data-ui-style="cyber"] .openclaw-status-row .badge' in stylesheet.text
    assert ':root[data-ui-style="cyber"] .workspace-button strong' in stylesheet.text
    assert "font-size: 0.875rem;" in stylesheet.text
    assert 'grid-template-columns: auto minmax(0, 1fr) minmax(4.25rem, max-content);' in stylesheet.text
    assert "#conversation-submit" in stylesheet.text


@pytest.mark.anyio
async def test_log_details_page_and_script_are_available(settings: Settings) -> None:
    transport = httpx.ASGITransport(app=create_app(settings))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        page = await client.get("/logs")
        script = await client.get("/static/logs.js")

    assert page.status_code == 200
    assert 'id="detail-log-source"' in page.text
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
    assert '<span class="badge badge-success">持续维护</span>' in listing.text
    assert project_readme.status_code == 200
    assert "面向个人设备的轻量管理服务" in project_readme.text
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
async def test_project_document_card_api_is_protected(
    settings: Settings,
    weekly_reports_root: Path,
) -> None:
    transport = httpx.ASGITransport(app=create_app(settings))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        unauthorized = await client.get("/api/project-docs")
        response = await client.get(
            "/api/project-docs",
            headers={
                "Authorization": "Bearer test-token-that-is-long-enough-for-tests"
            },
        )

    assert unauthorized.status_code == 401
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["count"] >= 1
    assert [item["report_type"] for item in data["weekly_reports"]] == [
        "focus",
        "report",
    ]
    assert data["weekly_reports"][0]["available"] is True
    assert data["weekly_reports"][1]["available"] is False
    assert len(data["documents"]) == 5
    assert any(document["status"] == "持续维护" for document in data["documents"])
    assert "openclaw-research" not in {
        document["id"] for document in data["documents"]
    }


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
        "/static/js/features/project-documents.js",
        "/static/js/features/logs.js",
        "/static/app.js",
    ]
    assert response.text.count("<script") == len(expected_scripts) + 1
    assert '<script src="/static/theme.js"></script>' in response.text
    positions = [response.text.index('<script src="/static/theme.js"></script>')] + [
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
