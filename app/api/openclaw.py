from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, Request, Response, status
from pydantic import BaseModel

from app.core.response import ApiResponse
from app.core.security import require_trusted_network
from app.services.openclaw import OpenClawStatus
from app.services.openclaw_weixin import WeixinLoginStatus
from app.services.operation_log import log_operation


router = APIRouter(
    prefix="/api/openclaw",
    tags=["openclaw"],
    dependencies=[Depends(require_trusted_network)],
)


class WeixinVerificationRequest(BaseModel):
    code: str


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


@router.post(
    "/weixin/login",
    response_model=ApiResponse[WeixinLoginStatus],
    status_code=status.HTTP_202_ACCEPTED,
)
def start_weixin_login(request: Request) -> ApiResponse[WeixinLoginStatus]:
    operation_id = log_operation(
        request,
        action="login_openclaw_weixin",
        status="requested",
        target="openclaw-weixin",
    )
    log_operation(
        request,
        action="login_openclaw_weixin",
        status="started",
        target="openclaw-weixin",
        operation_id=operation_id,
    )
    try:
        result = request.app.state.openclaw_manager.start_weixin_login(
            operation_id=operation_id,
            source_ip=request.client.host if request.client else "unknown",
        )
    except Exception:
        log_operation(
            request,
            action="login_openclaw_weixin",
            status="failed",
            target="openclaw-weixin",
            operation_id=operation_id,
        )
        raise
    return ApiResponse(data=result)


@router.get(
    "/weixin/login",
    response_model=ApiResponse[WeixinLoginStatus],
)
def get_weixin_login(request: Request) -> ApiResponse[WeixinLoginStatus]:
    return ApiResponse(data=request.app.state.openclaw_manager.weixin_login.status())


@router.get("/weixin/login/qr", response_class=Response)
def get_weixin_login_qr(request: Request) -> Response:
    content = request.app.state.openclaw_manager.weixin_login.qr_content()
    return Response(
        content=content,
        media_type="image/png",
        headers={
            "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
            "Pragma": "no-cache",
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.post(
    "/weixin/login/verify",
    response_model=ApiResponse[WeixinLoginStatus],
)
def verify_weixin_login(
    payload: WeixinVerificationRequest,
    request: Request,
) -> ApiResponse[WeixinLoginStatus]:
    return ApiResponse(
        data=request.app.state.openclaw_manager.weixin_login.submit_verification(
            payload.code
        )
    )


@router.delete(
    "/weixin/login",
    response_model=ApiResponse[WeixinLoginStatus],
)
def cancel_weixin_login(request: Request) -> ApiResponse[WeixinLoginStatus]:
    operation_id = log_operation(
        request,
        action="cancel_openclaw_weixin_login",
        status="requested",
        target="openclaw-weixin",
    )
    log_operation(
        request,
        action="cancel_openclaw_weixin_login",
        status="started",
        target="openclaw-weixin",
        operation_id=operation_id,
    )
    try:
        result = request.app.state.openclaw_manager.weixin_login.cancel()
    except Exception:
        log_operation(
            request,
            action="cancel_openclaw_weixin_login",
            status="failed",
            target="openclaw-weixin",
            operation_id=operation_id,
        )
        raise
    log_operation(
        request,
        action="cancel_openclaw_weixin_login",
        status="succeeded",
        target="openclaw-weixin",
        operation_id=operation_id,
    )
    return ApiResponse(data=result)
