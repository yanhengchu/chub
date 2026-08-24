from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.core.response import ApiResponse
from app.core.security import require_trusted_network
from app.services.weekly_reports import list_latest_weekly_reports


router = APIRouter(
    prefix="/api/weekly-reports",
    tags=["weekly-reports"],
    dependencies=[Depends(require_trusted_network)],
)


class WeeklyReportSummary(BaseModel):
    period: str
    report_type: str
    title: str
    summary: str
    status: str
    updated_at: datetime | None
    available: bool


class WeeklyReportListData(BaseModel):
    reports: list[WeeklyReportSummary]


@router.get("/current", response_model=ApiResponse[WeeklyReportListData])
def list_current_weekly_reports() -> ApiResponse[WeeklyReportListData]:
    return ApiResponse(
        data=WeeklyReportListData(
            reports=[
                WeeklyReportSummary.model_validate(item, from_attributes=True)
                for item in list_latest_weekly_reports()
            ]
        )
    )
