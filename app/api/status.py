from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Request

from app.core.response import ApiError, ApiResponse
from app.core.security import require_token
from app.services.system_status import StatusData, collect_system_status


router = APIRouter(
    prefix="/api",
    tags=["status"],
    dependencies=[Depends(require_token)],
)


@router.get("/status", response_model=ApiResponse[StatusData])
def status(request: Request) -> ApiResponse[StatusData]:
    try:
        data = collect_system_status(
            request.app.state.settings,
            request.app.state.detected_platform,
        )
    except Exception as exc:
        logging.getLogger("hub.status").exception("Unable to collect system status")
        raise ApiError(
            503,
            "status_unavailable",
            "System status is temporarily unavailable",
        ) from exc
    return ApiResponse(data=data)
