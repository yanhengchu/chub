from pathlib import Path
from urllib.parse import urlencode, urlsplit, urlunsplit

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates
from jinja2 import ChoiceLoader, FileSystemLoader

from app.services.design_documents import (
    DOCUMENT_GROUPS,
    DOCUMENT_CORE_STATUSES,
    DesignDocumentIndexError,
    get_design_document,
    list_design_documents,
    select_home_design_documents,
)
from app.core.response import ApiError
from app.core.business_modules import (
    business_module_template_dirs,
    loaded_business_module,
    loaded_business_modules,
)
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


def configure_business_module_templates(settings=None) -> None:
    templates.env.loader = ChoiceLoader(
        [FileSystemLoader(str(WEB_DIR / "templates"))]
        + [FileSystemLoader(str(path)) for path in business_module_template_dirs(settings)]
    )


configure_business_module_templates()
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


def _imported_business_modules(request: Request) -> tuple:
    try:
        imported = request.app.state.plugin_lifecycle.imported_plugin_ids()
    except (ApiError, OSError, RuntimeOperationError):
        return ()
    return tuple(
        module for module in loaded_business_modules(request)
        if module.module_id in imported
    )


_ORCHESTRATION_SETTINGS_PAGES = {
    "chub-task-prompt-optimizer": "任务提示词优化",
}


def _imported_orchestration_navigation(request: Request) -> tuple[dict[str, str], ...]:
    try:
        imported = request.app.state.plugin_lifecycle.imported_plugin_ids()
    except (ApiError, OSError, RuntimeOperationError):
        return ()
    return tuple(
        {"module_id": module_id, "name": name}
        for module_id, name in _ORCHESTRATION_SETTINGS_PAGES.items()
        if module_id in imported
    )


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
    return render_workspace(
        request,
        "automations",
        reading_detail={"kind": "weekly-template", "report": report},
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
    return render_workspace(
        request,
        "automations",
        reading_detail={"kind": "weekly-report", "report": report},
    )


@router.get("/logs", response_class=HTMLResponse, include_in_schema=False)
def log_details(request: Request) -> HTMLResponse:
    return RedirectResponse("/settings/logs", status_code=307)


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
    business_module_navigation = _imported_business_modules(request)
    orchestration_navigation = _imported_orchestration_navigation(request)
    try:
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
            "business_module_navigation": business_module_navigation,
            "orchestration_navigation": orchestration_navigation,
            "settings_module": (
                loaded_business_module(request, page)
                if page in {module.module_id for module in business_module_navigation}
                else None
            ),
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
        description="管理新会话默认配置，以及内部会话在工作台列表中的显示方式。",
    )


@router.get("/settings/diagnostics", response_class=HTMLResponse, include_in_schema=False)
def diagnostics_settings(request: Request) -> HTMLResponse:
    return render_settings_page(request, page="diagnostics", title="维护与版本", description="查看节点记录、维护入口与当前版本。")


@router.get("/settings/logs", response_class=HTMLResponse, include_in_schema=False)
def logs_settings(request: Request) -> HTMLResponse:
    return render_settings_page(
        request,
        page="logs",
        title="日志详情",
        description="查看节点操作与运行记录。",
    )


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


@router.get("/settings/openclaw", response_class=HTMLResponse, include_in_schema=False)
def openclaw_settings(request: Request) -> HTMLResponse:
    return render_settings_page(request, page="openclaw", title="OpenClaw", description="管理 Gateway、微信 ClawBot 与集成基线。")


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
    for module in loaded_business_modules(request):
        if module.workspace_state is not None and module.workspace_state(request) is not None:
            sections.add(module.module_id)
    if section not in sections:
        raise HTTPException(status_code=404, detail="Workspace section not found")
    destination = "/" if section == "workbench" else f"/?section={section}"
    return RedirectResponse(destination, status_code=307)


@router.get("/settings/{module_id}", response_class=HTMLResponse, include_in_schema=False)
def business_module_settings(request: Request, module_id: str) -> HTMLResponse:
    if module_id == "chub-task-prompt-optimizer":
        try:
            imported = request.app.state.plugin_lifecycle.imported_plugin_ids()
        except (ApiError, OSError, RuntimeOperationError):
            raise HTTPException(status_code=503, detail="Plugin settings are temporarily unavailable") from None
        if module_id not in imported:
            raise HTTPException(status_code=404, detail="Settings page not found")
        return render_settings_page(
            request,
            page=module_id,
            title="任务提示词优化",
            description="配置提示词优化插件的运行模式。",
        )
    module = loaded_business_module(request, module_id)
    if module is None or module.settings_template is None:
        raise HTTPException(status_code=404, detail="Settings page not found")
    if module_id not in {module.module_id for module in _imported_business_modules(request)}:
        raise HTTPException(status_code=404, detail="Settings page not found")
    return render_settings_page(
        request,
        page=module_id,
        title=module.name,
        description=module.description,
    )


def render_workspace(
    request: Request,
    section: str,
    *,
    workspace_session_id: str | None = None,
    reading_detail: dict[str, object] | None = None,
) -> HTMLResponse:
    settings = request.app.state.settings
    workspace_module = loaded_business_module(request, section)
    workspace_module_state = (
        workspace_module.workspace_state(request)
        if workspace_module is not None and workspace_module.workspace_state is not None
        else None
    )
    today_focus_ai_available, _today_focus_ai_unavailable_reason = (
        request.app.state.ai_session_manager.submission_available()
    )
    sections = {"workbench", "project-docs", "automations", "today-focus"}
    for module in loaded_business_modules(request):
        if module.workspace_state is not None and module.workspace_state(request) is not None:
            sections.add(module.module_id)
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
    workspace_module_records = []
    workspace_module_archived_records = []
    workspace_module_error = None
    if reading_detail is None and section == "project-docs":
        try:
            all_documents = list_design_documents(
                settings.project_documents.state_file,
                include_archived=False,
            )
            documents = select_home_design_documents(all_documents)
            document_count = len(all_documents)
        except DesignDocumentIndexError:
            documents_error = "项目资料暂时无法加载，请检查资料索引。"
    elif (
        section == "project-docs"
        and reading_detail is not None
        and reading_detail.get("kind") == "project-document-library"
    ):
        try:
            documents = list_design_documents(settings.project_documents.state_file)
            document_count = len(documents)
        except DesignDocumentIndexError:
            documents_error = "项目资料暂时无法加载，请检查资料索引。"
    elif reading_detail is None and section == "automations":
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
    elif (
        workspace_module is not None
        and workspace_module_state is not None
        and workspace_module_state.get("status") == "enabled"
        and workspace_module.workspace_records is not None
    ):
        try:
            workspace_module_records, workspace_module_archived_records = (
                workspace_module.workspace_records(request)
            )
        except (OSError, RuntimeError):
            workspace_module_error = "模块业务数据暂时无法加载。"
    return templates.TemplateResponse(
        request=request,
        name="workspace_preview.html",
        context={
            "app_name": settings.app.name,
            "page_title": settings.app.page_title or settings.app.name,
            "workspace_section": section,
            "workspace_session_id": workspace_session_id,
            "workspace_reading_detail": reading_detail,
            "workspace_module": workspace_module,
            "workspace_module_state": workspace_module_state,
            "workspace_module_navigation": tuple(
                module
                for module in loaded_business_modules(request)
                if module.workspace_state is not None
                and module.workspace_state(request) is not None
            ),
            "today_focus_ai_available": today_focus_ai_available,
            "document_groups": DOCUMENT_GROUPS,
            "document_core_statuses": DOCUMENT_CORE_STATUSES,
            "workspace_module_records": workspace_module_records,
            "workspace_module_archived_records": workspace_module_archived_records,
            "workspace_module_error": workspace_module_error,
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
        },
    )


@router.get("/project-docs", response_class=HTMLResponse, include_in_schema=False)
def design_documents(request: Request) -> HTMLResponse:
    return render_workspace(
        request,
        "project-docs",
        reading_detail={"kind": "project-document-library"},
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

    return render_workspace(
        request,
        "project-docs",
        reading_detail={"kind": "project-document", "document": document},
    )
