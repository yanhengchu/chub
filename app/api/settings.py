from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.core.response import ApiError, ApiResponse
from app.core.security import require_trusted_network
from app.services.operation_log import log_operation
from app.services.weixin_translation import TranslationSettingsStatus


router = APIRouter(
    prefix="/api/settings",
    tags=["settings"],
    dependencies=[Depends(require_trusted_network)],
)


class TranslationSettingsUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: Literal["direct", "auto", "confirm"] | None = None
    # Compatibility for the previous settings switch. A boolean request maps
    # false to direct and true to automatic execution.
    enabled: bool | None = None
    model: str | None = Field(default=None, max_length=128)
    reasoning_effort: str | None = Field(default=None, max_length=32)

    @field_validator("model", "reasoning_effort", mode="before")
    @classmethod
    def normalize_selection(cls, value: object) -> object:
        if isinstance(value, str):
            value = value.strip()
            return value or None
        return value

    @model_validator(mode="after")
    def validate_mode(self):
        mode_fields = {"mode", "enabled"} & self.model_fields_set
        model_fields = {"model", "reasoning_effort"} & self.model_fields_set
        if not mode_fields and not model_fields:
            raise ValueError("a translation setting is required")
        if self.mode is not None and self.enabled is not None:
            raise ValueError("provide mode only")
        if mode_fields and model_fields:
            raise ValueError("provide mode or model settings only")
        if model_fields and model_fields != {"model", "reasoning_effort"}:
            raise ValueError("model and reasoning_effort must be provided together")
        return self


@router.get(
    "/weixin-translation",
    response_model=ApiResponse[TranslationSettingsStatus],
)
def get_weixin_translation_settings(
    request: Request,
) -> ApiResponse[TranslationSettingsStatus]:
    try:
        result = request.app.state.weixin_translation.status()
    except OSError:
        raise ApiError(
            503,
            "weixin_translation_settings_unavailable",
            "微信翻译设置暂时无法读取。",
        ) from None
    return ApiResponse(data=result)


@router.put(
    "/weixin-translation",
    response_model=ApiResponse[TranslationSettingsStatus],
)
def update_weixin_translation_settings(
    request: Request,
    payload: TranslationSettingsUpdate,
) -> ApiResponse[TranslationSettingsStatus]:
    mode = payload.mode
    if mode is None:
        mode = "auto" if payload.enabled else "direct"
    model_update = "model" in payload.model_fields_set
    target = "translation_model" if model_update else mode
    operation_id = log_operation(
        request,
        action="update_weixin_translation_setting",
        status="requested",
        target=target,
    )
    log_operation(
        request,
        action="update_weixin_translation_setting",
        status="started",
        target=target,
        operation_id=operation_id,
    )
    try:
        if model_update:
            result = request.app.state.weixin_translation.set_model(
                payload.model,
                payload.reasoning_effort,
            )
        else:
            result = request.app.state.weixin_translation.set_processing_mode(mode)
    except ApiError:
        log_operation(
            request,
            action="update_weixin_translation_setting",
            status="failed",
            target=target,
            operation_id=operation_id,
        )
        raise
    except OSError:
        log_operation(
            request,
            action="update_weixin_translation_setting",
            status="failed",
            target=target,
            operation_id=operation_id,
        )
        raise ApiError(
            503,
            "weixin_translation_settings_unavailable",
            "微信翻译设置暂时无法保存。",
        ) from None
    log_operation(
        request,
        action="update_weixin_translation_setting",
        status="succeeded",
        target=target,
        operation_id=operation_id,
    )
    return ApiResponse(data=result)
