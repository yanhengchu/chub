from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.core.response import ApiResponse
from app.core.security import require_token
from app.services.design_documents import list_design_documents


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


class ProjectDocumentListData(BaseModel):
    count: int
    documents: list[ProjectDocumentSummary]


@router.get("", response_model=ApiResponse[ProjectDocumentListData])
def list_project_documents() -> ApiResponse[ProjectDocumentListData]:
    documents = list_design_documents()
    return ApiResponse(
        data=ProjectDocumentListData(
            count=len(documents),
            documents=[
                ProjectDocumentSummary.model_validate(item, from_attributes=True)
                for item in documents[:5]
            ],
        )
    )
