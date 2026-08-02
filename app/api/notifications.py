from __future__ import annotations

from fastapi import APIRouter, Depends, Request

from app.core.response import ApiResponse
from app.core.security import require_token
from app.notifications import (
    NotificationError,
    NotificationRequest,
    NotificationResult,
    NotificationTargetSummary,
)
from app.services.operation_log import log_operation


router = APIRouter(
    prefix="/api/notifications",
    tags=["notifications"],
    dependencies=[Depends(require_token)],
)


def _raise_api_error(exc: NotificationError) -> None:
    from app.core.response import ApiError

    raise ApiError(exc.status_code, exc.code, exc.message) from exc


@router.get("/targets", response_model=ApiResponse[list[NotificationTargetSummary]])
def list_notification_targets(
    request: Request,
) -> ApiResponse[list[NotificationTargetSummary]]:
    try:
        targets = request.app.state.notification_service.targets()
    except NotificationError as exc:
        _raise_api_error(exc)
    return ApiResponse(data=targets)


@router.post("/send", response_model=ApiResponse[NotificationResult])
async def send_notification(
    payload: NotificationRequest,
    request: Request,
) -> ApiResponse[NotificationResult]:
    operation_id = log_operation(
        request,
        action="send_notification",
        status="requested",
        target=payload.target,
    )
    log_operation(
        request,
        action="send_notification",
        status="started",
        target=payload.target,
        operation_id=operation_id,
    )
    try:
        result = await request.app.state.notification_service.send(payload)
    except NotificationError as exc:
        log_operation(
            request,
            action="send_notification",
            status="failed",
            target=payload.target,
            operation_id=operation_id,
        )
        _raise_api_error(exc)
    log_operation(
        request,
        action="send_notification",
        status="succeeded",
        target=payload.target,
        operation_id=operation_id,
    )
    return ApiResponse(data=result)
