from __future__ import annotations

from ipaddress import ip_address
from typing import Literal

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.codex.models import QuickInteractionWeixinRoute
from app.core.response import ApiError, ApiResponse
from app.core.security import require_tailscale


class WeixinChubModeStatusData(BaseModel):
    enabled: bool
    ready: bool
    code: Literal[
        "ready",
        "disabled",
        "configuration_invalid",
        "codex_unavailable",
    ]


class WeixinChubModeSubmitRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    message_id: str = Field(min_length=1, max_length=500)
    prompt: str = Field(min_length=1, max_length=8000)
    correlation_id: str | None = Field(default=None, max_length=500)
    reply_account_id: str = Field(min_length=1, max_length=200)
    reply_recipient: str = Field(min_length=1, max_length=500)

    @field_validator("message_id", "prompt", "reply_account_id", "reply_recipient")
    @classmethod
    def reject_blank(cls, value: str) -> str:
        resolved = value.strip()
        if not resolved:
            raise ValueError("Value must not be blank")
        return resolved

    @field_validator("correlation_id")
    @classmethod
    def normalize_correlation_id(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return value.strip() or None

    @field_validator("reply_recipient")
    @classmethod
    def validate_reply_recipient(cls, value: str) -> str:
        if not value.endswith("@im.wechat"):
            raise ValueError("Value must be a Weixin identifier")
        return value


class WeixinChubModeSubmissionData(BaseModel):
    accepted: Literal[True]
    duplicate: bool
    new_session: bool
    code: Literal["submitted"]
    message: str = Field(max_length=500)


def require_same_node_tailscale(request: Request) -> None:
    client_host = request.client.host if request.client else ""
    server_host = request.app.state.settings.server.host
    try:
        same_node = ip_address(client_host) == ip_address(server_host)
    except ValueError:
        same_node = False
    if not same_node:
        raise ApiError(
            403,
            "weixin_chub_mode_source_required",
            "微信 Chub 模式任务只接受本节点 OpenClaw 提交。",
        )


router = APIRouter(
    prefix="/api/openclaw/wechat-chub-mode",
    tags=["openclaw-wechat-chub-mode"],
    dependencies=[Depends(require_tailscale)],
)


@router.get("/status", response_model=ApiResponse[WeixinChubModeStatusData])
def get_wechat_chub_mode_status(
    request: Request,
) -> ApiResponse[WeixinChubModeStatusData]:
    status = request.app.state.weixin_chub_mode.status()
    return ApiResponse(
        data=WeixinChubModeStatusData(
            enabled=status.enabled,
            ready=status.ready,
            code=status.code,
        )
    )


@router.post(
    "/submit",
    response_model=ApiResponse[WeixinChubModeSubmissionData],
    dependencies=[Depends(require_same_node_tailscale)],
)
def submit_wechat_chub_mode_task(
    payload: WeixinChubModeSubmitRequest,
    request: Request,
) -> ApiResponse[WeixinChubModeSubmissionData]:
    result = request.app.state.weixin_chub_mode.submit(
        message_id=payload.message_id,
        prompt=payload.prompt,
        correlation_id=payload.correlation_id,
        source_ip=request.client.host if request.client else "unknown",
        delivery_route=QuickInteractionWeixinRoute(
            account_id=payload.reply_account_id,
            recipient=payload.reply_recipient,
        ),
    )
    return ApiResponse(
        data=WeixinChubModeSubmissionData.model_validate(
            result.model_dump()
        )
    )
