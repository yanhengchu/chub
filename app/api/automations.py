from __future__ import annotations

from fastapi import APIRouter, Depends, Request, Response, status

from app.automations.models import (
    AutomationListData,
    AutomationRunAccepted,
    BrowserControlResult,
    BrowserInitializationAccepted,
    BrowserInitializationRequest,
    BrowserStartRequest,
    FeishuEnvironmentState,
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
    mode: str = "headed",
    profile_id: str | None = None,
) -> ApiResponse[BrowserControlResult]:
    target = (
        f"debug-chrome:{profile_id or 'active'}:{mode}"
        if action == "start"
        else "debug-chrome"
    )
    operation_id = log_operation(
        request,
        action=f"{action}_debug_chrome",
        status="requested",
        target=target,
    )
    log_operation(
        request,
        action=f"{action}_debug_chrome",
        status="started",
        target=target,
        operation_id=operation_id,
    )
    try:
        if profile_id is None:
            result = request.app.state.automation_manager.control_browser(action, mode)
        else:
            result = request.app.state.automation_manager.control_browser(
                action,
                mode,
                profile_id,
            )
    except Exception:
        log_operation(
            request,
            action=f"{action}_debug_chrome",
            status="failed",
            target=target,
            operation_id=operation_id,
        )
        raise
    log_operation(
        request,
        action=f"{action}_debug_chrome",
        status="succeeded",
        target=target,
        operation_id=operation_id,
    )
    return ApiResponse(data=result)


@router.post(
    "/browser/start",
    response_model=ApiResponse[BrowserControlResult],
)
def start_browser(
    request: Request,
    payload: BrowserStartRequest | None = None,
) -> ApiResponse[BrowserControlResult]:
    resolved = payload or BrowserStartRequest()
    return _control_browser(
        "start",
        request,
        resolved.mode,
        resolved.profile_id,
    )


@router.post(
    "/browser/stop",
    response_model=ApiResponse[BrowserControlResult],
)
def stop_browser(request: Request) -> ApiResponse[BrowserControlResult]:
    return _control_browser("stop", request)


@router.post(
    "/browser/initialize",
    response_model=ApiResponse[BrowserInitializationAccepted],
    status_code=status.HTTP_202_ACCEPTED,
)
def initialize_browser(
    request: Request,
    payload: BrowserInitializationRequest,
) -> ApiResponse[BrowserInitializationAccepted]:
    target = f"debug-chrome:{payload.profile_id}:{payload.mode}"
    operation_id = log_operation(
        request,
        action="initialize_debug_chrome_profile",
        status="requested",
        target=target,
    )
    try:
        accepted = request.app.state.automation_manager.initialize_browser(
            payload.profile_id,
            payload.mode,
            operation_id=operation_id,
            source_ip=request.client.host if request.client else "unknown",
        )
    except Exception as exc:
        if not getattr(exc, "operation_logged", False):
            log_operation(
                request,
                action="initialize_debug_chrome_profile",
                status="failed",
                target=target,
                operation_id=operation_id,
            )
        raise
    return ApiResponse(data=accepted)


@router.post(
    "/environment/feishu/check",
    response_model=ApiResponse[FeishuEnvironmentState],
)
def check_feishu_environment(
    request: Request,
) -> ApiResponse[FeishuEnvironmentState]:
    operation_id = log_operation(
        request,
        action="check_feishu_environment",
        status="requested",
        target="feishu",
    )
    log_operation(
        request,
        action="check_feishu_environment",
        status="started",
        target="feishu",
        operation_id=operation_id,
    )
    try:
        result = request.app.state.automation_manager.check_feishu_environment()
    except Exception:
        log_operation(
            request,
            action="check_feishu_environment",
            status="failed",
            target="feishu",
            operation_id=operation_id,
        )
        raise
    log_operation(
        request,
        action="check_feishu_environment",
        status="succeeded",
        target="feishu",
        operation_id=operation_id,
    )
    return ApiResponse(data=result)


@router.get("/environment/feishu/qr", response_class=Response)
def get_feishu_login_qr(request: Request) -> Response:
    content = request.app.state.automation_manager.feishu_qr_content()
    return Response(
        content=content,
        media_type="image/png",
        headers={
            "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
            "Pragma": "no-cache",
            "X-Content-Type-Options": "nosniff",
        },
    )


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
