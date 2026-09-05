from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, Request
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


class WeeklyReportGenerationStartData(BaseModel):
    stage: str
    session_id: str
    task_id: str


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


@router.post(
    "/current/report/confirm-and-run",
    response_model=ApiResponse[WeeklyReportGenerationStartData],
)
def confirm_and_start_current_weekly_report(
    request: Request,
) -> ApiResponse[WeeklyReportGenerationStartData]:
    source_ip = request.client.host if request.client else "unknown"
    step = request.app.state.weekly_report_generation.confirm_and_start_report(
        source_ip=source_ip
    )
    if step.session_id is None or step.task_id is None:
        raise RuntimeError("Weekly report generation session was not created")
    return ApiResponse(
        data=WeeklyReportGenerationStartData(
            stage=step.stage,
            session_id=step.session_id,
            task_id=step.task_id,
        )
    )


@router.post(
    "/current/{stage}/run",
    response_model=ApiResponse[WeeklyReportGenerationStartData],
)
def start_weekly_report_generation(
    stage: str,
    request: Request,
) -> ApiResponse[WeeklyReportGenerationStartData]:
    source_ip = request.client.host if request.client else "unknown"
    step = request.app.state.weekly_report_generation.start(stage, source_ip=source_ip)
    if step.session_id is None or step.task_id is None:
        raise RuntimeError("Weekly report generation session was not created")
    return ApiResponse(
        data=WeeklyReportGenerationStartData(
            stage=step.stage,
            session_id=step.session_id,
            task_id=step.task_id,
        )
    )
