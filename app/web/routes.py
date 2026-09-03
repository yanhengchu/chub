from pathlib import Path
from urllib.parse import urlencode, urlsplit, urlunsplit

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from app.services.design_documents import (
    DesignDocumentIndexError,
    get_design_document,
    list_design_documents,
    select_home_design_documents,
)
from app.core.response import ApiError
from app.services.weekly_reports import get_weekly_report


WEB_DIR = Path(__file__).resolve().parent
STATIC_DIR = WEB_DIR / "static"
templates = Jinja2Templates(directory=WEB_DIR / "templates")

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
) -> HTMLResponse:
    settings = request.app.state.settings
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
        },
    )


@router.get("/settings", include_in_schema=False)
def settings_page(request: Request) -> RedirectResponse:
    return_url = _settings_return_url(request)
    target = "/settings/quick-interaction"
    if return_url != "/":
        target = f"{target}?{urlencode({'return_to': return_url})}"
    return RedirectResponse(target, status_code=307)


@router.get("/settings/quick-interaction", response_class=HTMLResponse, include_in_schema=False)
def quick_interaction_settings(request: Request) -> HTMLResponse:
    return render_settings_page(request, page="quick-interaction", title="快速交互", description="调整会话历史记录的加载方式。")


@router.get("/settings/appearance", response_class=HTMLResponse, include_in_schema=False)
def appearance_settings(request: Request) -> HTMLResponse:
    return render_settings_page(request, page="appearance", title="界面风格", description="查看可用风格及常用界面元素的实际效果。")


@router.get("/settings/diagnostics", response_class=HTMLResponse, include_in_schema=False)
def diagnostics_settings(request: Request) -> HTMLResponse:
    return render_settings_page(request, page="diagnostics", title="诊断与关于", description="查看节点记录与当前版本。")


@router.get("/settings/runtime", response_class=HTMLResponse, include_in_schema=False)
def runtime_settings(request: Request) -> HTMLResponse:
    return render_settings_page(request, page="runtime", title="Runtime 管理", description="管理后续 AI 会话可使用的 Runtime。")


@router.get("/settings/session-defaults", response_class=HTMLResponse, include_in_schema=False)
def session_defaults_settings(request: Request) -> HTMLResponse:
    return render_settings_page(request, page="session-defaults", title="新建 Session 默认权限", description="设置之后新建 Session 使用的默认权限。")


@router.get("/settings/weixin-text", response_class=HTMLResponse, include_in_schema=False)
def weixin_text_settings(request: Request) -> HTMLResponse:
    return render_settings_page(request, page="weixin-text", title="微信任务文本优化", description="设置微信 ClawBot 普通文本任务的处理方式。")


@router.get("/settings/openclaw", response_class=HTMLResponse, include_in_schema=False)
def openclaw_settings(request: Request) -> HTMLResponse:
    return render_settings_page(request, page="openclaw", title="OpenClaw", description="管理 OpenClaw Gateway 与微信 ClawBot。")


@router.get("/settings/openclaw/gateway", include_in_schema=False)
def legacy_openclaw_gateway_settings() -> RedirectResponse:
    return RedirectResponse("/settings/openclaw", status_code=307)


@router.get("/settings/openclaw/clawbot", include_in_schema=False)
def legacy_openclaw_clawbot_settings() -> RedirectResponse:
    return RedirectResponse("/settings/openclaw", status_code=307)


@router.get(
    "/settings/styles/standard",
    response_class=HTMLResponse,
    include_in_schema=False,
)
def standard_style_preview(request: Request) -> HTMLResponse:
    settings = request.app.state.settings
    return templates.TemplateResponse(
        request=request,
        name="style_preview_standard.html",
        context={
            "app_name": settings.app.name,
            "style_name": "Standard",
            "style_badge": "当前风格",
            "style_description": "简约标准版以清晰信息层级、克制配色、自然高度卡片和轻量反馈为核心。",
            "preview_body_class": "standard-preview",
            "color_scheme": "light",
        },
    )


@router.get(
    "/settings/styles/code-dark",
    response_class=HTMLResponse,
    include_in_schema=False,
)
def code_dark_style_preview(request: Request) -> HTMLResponse:
    settings = request.app.state.settings
    return templates.TemplateResponse(
        request=request,
        name="style_preview_standard.html",
        context={
            "app_name": settings.app.name,
            "style_name": "Code Dark",
            "style_badge": "设计预览",
            "style_description": "深色开发工作台以沉稳背景、分层面板、蓝色强调和清晰状态为核心。",
            "preview_body_class": "code-dark-preview",
            "color_scheme": "dark",
        },
    )


@router.get(
    "/workspace",
    include_in_schema=False,
)
def workspace_preview(
    section: str = "workbench",
) -> RedirectResponse:
    if section not in {"workbench", "project-docs", "automations"}:
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
    if section not in {"workbench", "project-docs", "automations"}:
        raise HTTPException(status_code=404, detail="Workspace section not found")
    documents_error = None
    documents = []
    document_count = 0
    automations = None
    automations_error = None
    automation_start_available = False
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
        except ApiError as exc:
            automations_error = exc.message
    return templates.TemplateResponse(
        request=request,
        name="workspace_preview.html",
        context={
            "app_name": settings.app.name,
            "page_title": settings.app.page_title or settings.app.name,
            "workspace_section": section,
            "workspace_session_id": workspace_session_id,
            "documents": documents,
            "document_count": document_count,
            "documents_error": documents_error,
            "automations": automations,
            "automations_error": automations_error,
            "automation_start_available": automation_start_available,
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
