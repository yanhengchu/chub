from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, Request

from app.core.response import ApiResponse
from app.core.security import require_token
from app.services.openclaw import OpenClawStatus
from app.services.operation_log import log_operation


router = APIRouter(
    prefix="/api/openclaw",
    tags=["openclaw"],
    dependencies=[Depends(require_token)],
)


@router.get("/status", response_model=ApiResponse[OpenClawStatus])
def get_openclaw_status(request: Request) -> ApiResponse[OpenClawStatus]:
    return ApiResponse(data=request.app.state.openclaw_manager.status())


def _control(
    action: Literal["start", "stop", "restart"],
    request: Request,
) -> ApiResponse[OpenClawStatus]:
    operation_id = log_operation(
        request,
        action=f"{action}_openclaw_gateway",
        status="requested",
        target="openclaw-gateway",
    )
    log_operation(
        request,
        action=f"{action}_openclaw_gateway",
        status="started",
        target="openclaw-gateway",
        operation_id=operation_id,
    )
    try:
        result = request.app.state.openclaw_manager.control(action)
    except Exception:
        log_operation(
            request,
            action=f"{action}_openclaw_gateway",
            status="failed",
            target="openclaw-gateway",
            operation_id=operation_id,
        )
        raise
    log_operation(
        request,
        action=f"{action}_openclaw_gateway",
        status="succeeded",
        target="openclaw-gateway",
        operation_id=operation_id,
    )
    return ApiResponse(data=result)


@router.post("/start", response_model=ApiResponse[OpenClawStatus])
def start_openclaw(request: Request) -> ApiResponse[OpenClawStatus]:
    return _control("start", request)


@router.post("/stop", response_model=ApiResponse[OpenClawStatus])
def stop_openclaw(request: Request) -> ApiResponse[OpenClawStatus]:
    return _control("stop", request)


@router.post("/restart", response_model=ApiResponse[OpenClawStatus])
def restart_openclaw(request: Request) -> ApiResponse[OpenClawStatus]:
    return _control("restart", request)
