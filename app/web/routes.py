from pathlib import Path
from urllib.parse import urlencode, urlsplit, urlunsplit

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates

from app.services.design_documents import (
    DOCUMENT_CATEGORIES,
    DOCUMENT_CORE_STATUSES,
    DesignDocumentIndexError,
    get_design_document,
    list_design_documents,
    select_home_design_documents,
)
from app.core.response import ApiError
from app.ai_runtime import RuntimeOperationError
from app.services.weekly_reports import (
    get_weekly_report,
    get_weekly_report_template,
    list_latest_weekly_reports,
    weekly_report_inputs_available,
    weekly_report_focus_confirmed,
)
from app.web.themes import configure_theme_templates


WEB_DIR = Path(__file__).resolve().parent
STATIC_DIR = WEB_DIR / "static"
templates = Jinja2Templates(directory=WEB_DIR / "templates")
configure_theme_templates(templates)

router = APIRouter(tags=["web"])


def _settings_return_url(request: Request) -> str:
    raw_return_url = request.query_params.get("return_to", "")
    parsed = urlsplit(raw_return_url)
    if (
        parsed.scheme
        or parsed.netloc
        or parsed.path not in {"/", "/workspace"}
    ):
        return "/"
    return urlunsplit(("", "", parsed.path, parsed.query, ""))


def _imported_runtime_navigation(request: Request) -> tuple:
    try:
        imported_plugins = request.app.state.plugin_lifecycle.imported_plugin_ids()
        if "runtime" not in imported_plugins:
            return ()
        return request.app.state.ai_session_manager.runtime_plugins.navigation()
    except (ApiError, OSError, RuntimeOperationError):
        return ()


def _deliveryline_workspace_state(request: Request) -> dict[str, str] | None:
    try:
        lifecycle = request.app.state.plugin_lifecycle.list(request)
    except (ApiError, OSError):
        return None
    plugins = lifecycle.get("plugins")
    if not isinstance(plugins, list):
        return None
    plugin = next(
        (
            item
            for item in plugins
            if isinstance(item, dict) and item.get("plugin_id") == "deliveryline"
        ),
        None,
    )
    if plugin is None:
        return None
    imported_ids = plugin.get("imported_artifact_ids")
    enabled_ids = plugin.get("enabled_artifact_ids")
    artifacts = plugin.get("artifacts")
    if not isinstance(imported_ids, list) or not imported_ids:
        return None
    if not isinstance(enabled_ids, list):
        enabled_ids = []
    if not isinstance(artifacts, list):
        artifacts = []
    imported = next(
        (
            item
            for item in artifacts
            if isinstance(item, dict) and item.get("artifact_id") in imported_ids
        ),
        {},
    )
    enabled = next(
        (
            item
            for item in artifacts
            if isinstance(item, dict) and item.get("artifact_id") in enabled_ids
        ),
        None,
    )
    if enabled is None or not enabled.get("available"):
        return None
    return {
        "status": "enabled",
        "label": "已启用",
        "description": str(
            imported.get("description") or "需求交付管理业务模块；业务流程将在后续阶段接入。"
        ),
        "detail": "需求提出档案已可创建、编辑、归档并提交评审；后续阶段尚未接入。",
    }


@router.get("/", response_class=HTMLResponse, include_in_schema=False)
def index(request: Request, section: str = "workbench") -> HTMLResponse:
    workspace_session_id = request.query_params.get("session", "").strip()
    if len(workspace_session_id) > 200:
        workspace_session_id = ""
    return render_workspace(
        request,
        "workbench" if workspace_session_id else section,
        workspace_session_id=workspace_session_id or None,
    )


@router.get(
    "/weekly-reports/templates/{template_type}",
    response_class=HTMLResponse,
    include_in_schema=False,
)
def weekly_report_template_detail(
    request: Request,
    template_type: str,
) -> HTMLResponse:
    report = get_weekly_report_template(template_type)
    if report is None:
        raise HTTPException(status_code=404, detail="Weekly report template not found")
    return templates.TemplateResponse(
        request=request,
        name="weekly_report_detail.html",
        context={
            "app_name": request.app.state.settings.app.name,
            "report": report,
        },
    )


@router.get(
    "/weekly-reports/{period}/{report_type}",
    response_class=HTMLResponse,
    include_in_schema=False,
)
def weekly_report_detail(
    request: Request,
    period: str,
    report_type: str,
) -> HTMLResponse:
    report = get_weekly_report(period, report_type)
    if report is None:
        raise HTTPException(status_code=404, detail="Weekly report not found")
    return templates.TemplateResponse(
        request=request,
        name="weekly_report_detail.html",
        context={
            "app_name": request.app.state.settings.app.name,
            "report": report,
        },
    )


@router.get("/logs", response_class=HTMLResponse, include_in_schema=False)
def log_details(request: Request) -> HTMLResponse:
    settings = request.app.state.settings
    return templates.TemplateResponse(
        request=request,
        name="logs.html",
        context={"app_name": settings.app.name},
    )


def render_settings_page(
    request: Request,
    *,
    page: str,
    title: str,
    description: str,
    runtime_id: str | None = None,
) -> HTMLResponse:
    settings = request.app.state.settings
    runtime_navigation = ()
    orchestration_navigation = False
    deliveryline_navigation = False
    try:
        imported_plugins = request.app.state.plugin_lifecycle.imported_plugin_ids()
        orchestration_navigation = "weixin-orchestration" in imported_plugins
        deliveryline_navigation = "deliveryline" in imported_plugins
        runtime_navigation = _imported_runtime_navigation(request)
    except (ApiError, OSError, RuntimeOperationError):
        # The plugin manager remains reachable when lifecycle state cannot be read.
        pass
    return templates.TemplateResponse(
        request=request,
        name="settings.html",
        context={
            "app_name": settings.app.name,
            "app_version": settings.app.version,
            "settings_page": page,
            "settings_title": title,
            "settings_description": description,
            "settings_return_url": _settings_return_url(request),
            "settings_runtime_id": runtime_id,
            "runtime_navigation": runtime_navigation,
            "orchestration_navigation": orchestration_navigation,
            "deliveryline_navigation": deliveryline_navigation,
        },
    )


@router.get("/settings", include_in_schema=False)
def settings_page(request: Request) -> RedirectResponse:
    return_url = _settings_return_url(request)
    target = "/settings/appearance"
    if return_url != "/":
        target = f"{target}?{urlencode({'return_to': return_url})}"
    return RedirectResponse(target, status_code=307)


@router.get("/settings/appearance", response_class=HTMLResponse, include_in_schema=False)
def appearance_settings(request: Request) -> HTMLResponse:
    return render_settings_page(
        request,
        page="appearance",
        title="外观",
        description="调整工作台主题、文字层级和常用界面元素的显示效果。",
    )


@router.get("/settings/session", response_class=HTMLResponse, include_in_schema=False)
def session_settings(request: Request) -> Response:
    if not _imported_runtime_navigation(request):
        return_url = _settings_return_url(request)
        target = "/settings/runtime"
        if return_url != "/":
            target = f"{target}?{urlencode({'return_to': return_url})}"
        return RedirectResponse(target, status_code=307)
    return render_settings_page(
        request,
        page="session",
        title="会话",
        description="管理新会话默认配置，以及模块内部会话在工作台中的显示方式。",
    )


@router.get("/settings/diagnostics", response_class=HTMLResponse, include_in_schema=False)
def diagnostics_settings(request: Request) -> HTMLResponse:
    return render_settings_page(request, page="diagnostics", title="维护与版本", description="查看节点记录、维护入口与当前版本。")


@router.get("/settings/runtime", response_class=HTMLResponse, include_in_schema=False)
def runtime_settings(request: Request) -> HTMLResponse:
    return render_settings_page(
        request,
        page="runtime",
        title="插件管理",
        description="统一管理插件的导入、移除、启用、禁用与版本。",
    )


@router.get(
    "/settings/runtime/{runtime_id}",
    response_class=HTMLResponse,
    include_in_schema=False,
)
def runtime_detail_settings(request: Request, runtime_id: str) -> HTMLResponse:
    try:
        runtime = request.app.state.ai_session_manager.runtime_plugins.require_navigation(
            runtime_id
        )
    except RuntimeOperationError:
        raise HTTPException(status_code=404, detail="Runtime not found")
    return render_settings_page(
        request,
        page="runtime-detail",
        title=runtime.name,
        description=runtime.description,
        runtime_id=runtime.runtime_id,
    )


@router.get("/settings/task-orchestration", response_class=HTMLResponse, include_in_schema=False)
def task_orchestration_settings(request: Request) -> HTMLResponse:
    return render_settings_page(
        request,
        page="task-orchestration",
        title="微信任务润色",
        description="配置微信 ClawBot 普通文本任务的处理方式和 AI Runtime 参数。",
    )

@router.get("/settings/deliveryline", response_class=HTMLResponse, include_in_schema=False)
def deliveryline_settings(request: Request) -> HTMLResponse:
    return render_settings_page(request, page="deliveryline", title="Deliveryline", description="管理 Deliveryline 业务插件的导入与启用状态；业务流程将在后续阶段接入。")


@router.get("/settings/weixin-text", include_in_schema=False)
def legacy_weixin_text_settings(request: Request) -> RedirectResponse:
    return_url = _settings_return_url(request)
    target = "/settings/task-orchestration"
    if return_url != "/":
        target = f"{target}?{urlencode({'return_to': return_url})}"
    return RedirectResponse(target, status_code=307)


@router.get("/settings/openclaw", response_class=HTMLResponse, include_in_schema=False)
def openclaw_settings(request: Request) -> HTMLResponse:
    return render_settings_page(request, page="openclaw", title="OpenClaw", description="查看 OpenClaw 集成基线与补丁状态。")


@router.get("/settings/openclaw/gateway", include_in_schema=False)
def legacy_openclaw_gateway_settings() -> RedirectResponse:
    return RedirectResponse("/settings/openclaw", status_code=307)


@router.get("/settings/openclaw/clawbot", include_in_schema=False)
def legacy_openclaw_clawbot_settings() -> RedirectResponse:
    return RedirectResponse("/settings/openclaw", status_code=307)


@router.get("/settings/styles/standard", include_in_schema=False)
def legacy_standard_style_preview() -> RedirectResponse:
    return RedirectResponse("/settings/appearance", status_code=307)


@router.get("/settings/styles/code-dark", include_in_schema=False)
def legacy_code_dark_style_preview() -> RedirectResponse:
    return RedirectResponse("/settings/appearance", status_code=307)


@router.get(
    "/workspace",
    include_in_schema=False,
)
def workspace_preview(
    request: Request,
    section: str = "workbench",
) -> RedirectResponse:
    sections = {"workbench", "project-docs", "automations", "today-focus"}
    if _deliveryline_workspace_state(request) is not None:
        sections.add("deliveryline")
    if section not in sections:
        raise HTTPException(status_code=404, detail="Workspace section not found")
    destination = "/" if section == "workbench" else f"/?section={section}"
    return RedirectResponse(destination, status_code=307)


def render_workspace(
    request: Request,
    section: str,
    *,
    workspace_session_id: str | None = None,
) -> HTMLResponse:
    settings = request.app.state.settings
    third_party_environment_available = request.app.state.openclaw_manager.is_installed()
    deliveryline = _deliveryline_workspace_state(request)
    today_focus_ai_available, _today_focus_ai_unavailable_reason = (
        request.app.state.ai_session_manager.submission_available()
    )
    sections = {"workbench", "project-docs", "automations", "today-focus"}
    if deliveryline is not None:
        sections.add("deliveryline")
    if section not in sections:
        raise HTTPException(status_code=404, detail="Workspace section not found")
    documents_error = None
    documents = []
    document_count = 0
    automations = None
    automations_error = None
    automation_start_available = False
    weekly_reports = {}
    weekly_report_focus_is_confirmed = False
    weekly_report_inputs_ready = False
    weekly_report_generation = {}
    weekly_report_generation_ready = False
    weekly_report_generation_unavailable_reason = None
    deliveryline_requirements = []
    deliveryline_archived_requirements = []
    deliveryline_error = None
    if section == "project-docs":
        try:
            all_documents = list_design_documents(
                settings.project_documents.state_file,
                include_archived=False,
            )
            documents = select_home_design_documents(all_documents)
            document_count = len(all_documents)
        except DesignDocumentIndexError:
            documents_error = "项目资料暂时无法加载，请检查资料索引。"
    elif section == "automations":
        try:
            automations = request.app.state.automation_manager.list(home_only=False)
            automation_start_available = any(
                profile.initialized or profile.source_available
                for profile in automations.browser_profiles
            )
            latest_weekly_reports = list_latest_weekly_reports()
            weekly_reports = {
                report.report_type: report for report in latest_weekly_reports
            }
            if latest_weekly_reports:
                weekly_report_focus_is_confirmed = weekly_report_focus_confirmed(
                    latest_weekly_reports[0].period
                )
                weekly_report_inputs_ready = weekly_report_inputs_available(
                    latest_weekly_reports[0].period
                )
            weekly_report_generation = (
                request.app.state.weekly_report_generation.read_current()
            )
            (
                weekly_report_generation_ready,
                weekly_report_generation_unavailable_reason,
            ) = request.app.state.weekly_report_generation.configuration_ready()
        except ApiError as exc:
            automations_error = exc.message
    elif section == "deliveryline" and deliveryline is not None and deliveryline["status"] == "enabled":
        try:
            records = request.app.state.deliveryline_store.list(include_archived=True)
            deliveryline_requirements = [
                record
                for record in records
                if record.status != "已结束"
            ]
            deliveryline_archived_requirements = [
                record
                for record in records
                if record.status == "已结束"
            ]
        except (OSError, RuntimeError):
            deliveryline_error = "需求档案暂时无法加载。"
    return templates.TemplateResponse(
        request=request,
        name="workspace_preview.html",
        context={
            "app_name": settings.app.name,
            "page_title": settings.app.page_title or settings.app.name,
            "workspace_section": section,
            "workspace_session_id": workspace_session_id,
            "deliveryline": deliveryline,
            "deliveryline_navigation": deliveryline is not None,
            "today_focus_ai_available": today_focus_ai_available,
            "document_categories": DOCUMENT_CATEGORIES,
            "deliveryline_requirements": deliveryline_requirements,
            "deliveryline_archived_requirements": deliveryline_archived_requirements,
            "deliveryline_error": deliveryline_error,
            "documents": documents,
            "document_count": document_count,
            "documents_error": documents_error,
            "automations": automations,
            "automations_error": automations_error,
            "automation_start_available": automation_start_available,
            "weekly_reports": weekly_reports,
            "weekly_report_focus_is_confirmed": weekly_report_focus_is_confirmed,
            "weekly_report_inputs_ready": weekly_report_inputs_ready,
            "weekly_report_generation": weekly_report_generation,
            "weekly_report_generation_ready": weekly_report_generation_ready,
            "weekly_report_generation_unavailable_reason": weekly_report_generation_unavailable_reason,
            "third_party_environment_available": third_party_environment_available,
        },
    )


@router.get("/project-docs", response_class=HTMLResponse, include_in_schema=False)
def design_documents(request: Request) -> HTMLResponse:
    settings = request.app.state.settings
    documents_error = None
    try:
        documents = list_design_documents(
            settings.project_documents.state_file,
        )
    except DesignDocumentIndexError:
        documents = []
        documents_error = "项目资料暂时无法加载，请检查资料索引。"
    return templates.TemplateResponse(
        request=request,
        name="design_documents.html",
        context={
            "app_name": settings.app.name,
            "documents": documents,
            "document_categories": DOCUMENT_CATEGORIES,
            "document_core_statuses": DOCUMENT_CORE_STATUSES,
            "documents_error": documents_error,
        },
    )


@router.get(
    "/project-docs/{document_id}",
    response_class=HTMLResponse,
    include_in_schema=False,
)
def design_document_detail(request: Request, document_id: str) -> HTMLResponse:
    settings = request.app.state.settings
    try:
        document = get_design_document(
            document_id,
            settings.project_documents.state_file,
        )
    except DesignDocumentIndexError:
        raise HTTPException(
            status_code=503,
            detail="Design document index unavailable",
        ) from None
    if document is None:
        raise HTTPException(status_code=404, detail="Design document not found")

    return templates.TemplateResponse(
        request=request,
        name="design_document_detail.html",
        context={"app_name": settings.app.name, "document": document},
    )
