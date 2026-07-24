from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel

from app.core.response import ApiError, ApiResponse
from app.core.security import require_token
from app.services.design_documents import (
    DesignDocumentIndexError,
    list_design_documents,
    set_design_document_archived,
)
from app.services.operation_log import log_operation


router = APIRouter(
    prefix="/api/project-docs",
    tags=["project-docs"],
    dependencies=[Depends(require_token)],
)


class ProjectDocumentSummary(BaseModel):
    id: str
    title: str
    summary: str
    status: str
    updated_at: datetime
    archived: bool


class ProjectDocumentListData(BaseModel):
    count: int
    documents: list[ProjectDocumentSummary]


class ProjectDocumentArchiveUpdate(BaseModel):
    archived: bool


@router.get("", response_model=ApiResponse[ProjectDocumentListData])
def list_project_documents(request: Request) -> ApiResponse[ProjectDocumentListData]:
    try:
        documents = list_design_documents(
            request.app.state.settings.project_documents.state_file,
            include_archived=False,
        )
    except DesignDocumentIndexError:
        raise ApiError(
            503,
            "project_document_index_unavailable",
            "设计文档暂时无法加载。",
        ) from None
    return ApiResponse(
        data=ProjectDocumentListData(
            count=len(documents),
            documents=[
                ProjectDocumentSummary.model_validate(item, from_attributes=True)
                for item in documents[:5]
            ],
        )
    )


@router.put(
    "/{document_id}/archive",
    response_model=ApiResponse[ProjectDocumentSummary],
)
def update_project_document_archive(
    document_id: str,
    update: ProjectDocumentArchiveUpdate,
    request: Request,
) -> ApiResponse[ProjectDocumentSummary]:
    action = "archive_project_document" if update.archived else "restore_project_document"
    operation_id = log_operation(
        request,
        action=action,
        status="requested",
        target=document_id,
    )
    log_operation(
        request,
        action=action,
        status="started",
        target=document_id,
        operation_id=operation_id,
    )
    try:
        document = set_design_document_archived(
            document_id,
            update.archived,
            request.app.state.settings.project_documents.state_file,
        )
    except DesignDocumentIndexError:
        log_operation(
            request,
            action=action,
            status="failed",
            target=document_id,
            operation_id=operation_id,
        )
        raise ApiError(
            503,
            "project_document_index_unavailable",
            "设计文档暂时无法加载。",
        ) from None
    except OSError:
        log_operation(
            request,
            action=action,
            status="failed",
            target=document_id,
            operation_id=operation_id,
        )
        raise ApiError(
            503,
            "project_document_state_unavailable",
            "文档归档状态暂时无法保存。",
        ) from None
    if document is None:
        log_operation(
            request,
            action=action,
            status="failed",
            target=document_id,
            operation_id=operation_id,
        )
        raise ApiError(404, "project_document_not_found", "设计文档不存在。")
    log_operation(
        request,
        action=action,
        status="succeeded",
        target=document_id,
        operation_id=operation_id,
    )
    return ApiResponse(
        data=ProjectDocumentSummary.model_validate(document, from_attributes=True)
    )
