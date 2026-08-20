from __future__ import annotations

from typing import Literal, Self

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.codex.models import QuickInteractionWeixinRoute
from app.core.response import ApiError, ApiResponse
from app.core.security import _allows_loopback_request


WEIXIN_CHUB_MODE_PROTOCOL_VERSION = 3


class WeixinChubModeDispatchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    protocol_version: int = Field(ge=1, le=100)
    message_id: str = Field(min_length=1, max_length=500)
    content: str = Field(min_length=1, max_length=8000)
    message_type: Literal["text", "voice"]
    correlation_id: str | None = Field(default=None, max_length=500)
    reply_account_id: str = Field(min_length=1, max_length=200)
    reply_recipient: str = Field(min_length=1, max_length=500)

    @field_validator("message_id", "reply_account_id", "reply_recipient")
    @classmethod
    def reject_blank(cls, value: str) -> str:
        resolved = value.strip()
        if not resolved:
            raise ValueError("Value must not be blank")
        return resolved

    @field_validator("content")
    @classmethod
    def reject_blank_content(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Value must not be blank")
        return value

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


class WeixinChubModeDispatchData(BaseModel):
    protocol_version: Literal[3]
    disposition: Literal["pass", "reply", "handled"]
    message: str | None = Field(default=None, max_length=3000)

    @model_validator(mode="after")
    def validate_disposition_message(self) -> Self:
        if self.disposition in {"pass", "handled"} and self.message is not None:
            raise ValueError("Pass and handled responses must not include a message")
        if self.disposition == "reply" and not (self.message or "").strip():
            raise ValueError("Reply responses must include a message")
        return self


def require_local_openclaw(request: Request) -> None:
    if not _allows_loopback_request(request):
        raise ApiError(
            403,
            "weixin_chub_mode_source_required",
            "微信 Chub 模式只接受本机 OpenClaw 调度。",
        )


router = APIRouter(
    prefix="/api/openclaw/wechat-chub-mode",
    tags=["openclaw-wechat-chub-mode"],
)


@router.post(
    "/dispatch",
    response_model=ApiResponse[WeixinChubModeDispatchData],
    dependencies=[Depends(require_local_openclaw)],
)
def dispatch_wechat_chub_mode_message(
    payload: WeixinChubModeDispatchRequest,
    request: Request,
) -> ApiResponse[WeixinChubModeDispatchData]:
    if payload.protocol_version != WEIXIN_CHUB_MODE_PROTOCOL_VERSION:
        raise ApiError(
            409,
            "weixin_chub_mode_protocol_mismatch",
            "OpenClaw 与 Chub 微信调度协议版本不匹配。",
        )
    result = request.app.state.weixin_chub_mode.dispatch(
        message_id=payload.message_id,
        prompt=payload.content,
        message_type=payload.message_type,
        correlation_id=payload.correlation_id,
        source_ip=request.client.host if request.client else "unknown",
        delivery_route=QuickInteractionWeixinRoute(
            account_id=payload.reply_account_id,
            recipient=payload.reply_recipient,
        ),
    )
    return ApiResponse(
        data=WeixinChubModeDispatchData.model_validate(result.model_dump())
    )
