from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from app.services.design_documents import (
    DesignDocumentIndexError,
    get_design_document,
    list_design_documents,
)
from app.services.weekly_reports import get_weekly_report, list_latest_weekly_reports


WEB_DIR = Path(__file__).resolve().parent
STATIC_DIR = WEB_DIR / "static"
templates = Jinja2Templates(directory=WEB_DIR / "templates")

router = APIRouter(tags=["web"])


@router.get("/", response_class=HTMLResponse, include_in_schema=False)
def index(request: Request) -> HTMLResponse:
    settings = request.app.state.settings
    design_documents_error = None
    try:
        design_documents = list_design_documents(
            settings.project_documents.state_file,
            include_archived=False,
        )
    except DesignDocumentIndexError:
        design_documents = []
        design_documents_error = "设计文档暂时无法加载，请检查文档索引。"
    weekly_reports = list_latest_weekly_reports()
    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context={
            "app_name": settings.app.name,
            "page_title": settings.app.page_title
            or f"{settings.app.name} 管理面板",
            "site_title": settings.app.page_title or settings.app.name,
            "app_version": settings.app.version,
            "design_documents": design_documents[:5],
            "design_document_count": len(design_documents),
            "design_documents_error": design_documents_error,
            "weekly_reports": weekly_reports,
            "available_weekly_report_count": sum(
                report.available for report in weekly_reports
            ),
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


@router.get("/settings", response_class=HTMLResponse, include_in_schema=False)
def settings_page(request: Request) -> HTMLResponse:
    settings = request.app.state.settings
    return templates.TemplateResponse(
        request=request,
        name="settings.html",
        context={
            "app_name": settings.app.name,
            "app_version": settings.app.version,
        },
    )


@router.get("/automations", response_class=HTMLResponse, include_in_schema=False)
def automation_details(request: Request) -> HTMLResponse:
    settings = request.app.state.settings
    return templates.TemplateResponse(
        request=request,
        name="automations.html",
        context={"app_name": settings.app.name},
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
        documents_error = "设计文档暂时无法加载，请检查文档索引。"
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
