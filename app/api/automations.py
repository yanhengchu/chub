from __future__ import annotations

from fastapi import APIRouter, Depends, Request, status

from app.automations.models import (
    AutomationListData,
    AutomationRunAccepted,
    BrowserControlResult,
)
from app.core.response import ApiResponse
from app.core.security import require_token
from app.services.operation_log import log_operation


router = APIRouter(
    prefix="/api/automations",
    tags=["automations"],
    dependencies=[Depends(require_token)],
)


def _control_browser(
    action: str,
    request: Request,
) -> ApiResponse[BrowserControlResult]:
    operation_id = log_operation(
        request,
        action=f"{action}_debug_chrome",
        status="requested",
        target="debug-chrome",
    )
    log_operation(
        request,
        action=f"{action}_debug_chrome",
        status="started",
        target="debug-chrome",
        operation_id=operation_id,
    )
    try:
        result = request.app.state.automation_manager.control_browser(action)
    except Exception:
        log_operation(
            request,
            action=f"{action}_debug_chrome",
            status="failed",
            target="debug-chrome",
            operation_id=operation_id,
        )
        raise
    log_operation(
        request,
        action=f"{action}_debug_chrome",
        status="succeeded",
        target="debug-chrome",
        operation_id=operation_id,
    )
    return ApiResponse(data=result)


@router.post(
    "/browser/start",
    response_model=ApiResponse[BrowserControlResult],
)
def start_browser(request: Request) -> ApiResponse[BrowserControlResult]:
    return _control_browser("start", request)


@router.post(
    "/browser/stop",
    response_model=ApiResponse[BrowserControlResult],
)
def stop_browser(request: Request) -> ApiResponse[BrowserControlResult]:
    return _control_browser("stop", request)


@router.get("", response_model=ApiResponse[AutomationListData])
def list_automations(
    request: Request,
    all_tasks: bool = False,
) -> ApiResponse[AutomationListData]:
    return ApiResponse(
        data=request.app.state.automation_manager.list(home_only=not all_tasks)
    )


@router.post(
    "/{task_id}/run",
    response_model=ApiResponse[AutomationRunAccepted],
    status_code=status.HTTP_202_ACCEPTED,
)
def run_automation(
    task_id: str,
    request: Request,
) -> ApiResponse[AutomationRunAccepted]:
    operation_id = log_operation(
        request,
        action="run_automation",
        status="requested",
        target=task_id,
    )
    try:
        accepted = request.app.state.automation_manager.start(
            task_id,
            operation_id=operation_id,
            source_ip=request.client.host if request.client else "unknown",
        )
    except Exception:
        log_operation(
            request,
            action="run_automation",
            status="failed",
            target=task_id,
            operation_id=operation_id,
        )
        raise
    log_operation(
        request,
        action="run_automation",
        status="started",
        target=task_id,
        operation_id=operation_id,
    )
    return ApiResponse(data=accepted)
