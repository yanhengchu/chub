from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict

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

    enabled: bool


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
    target = "enabled" if payload.enabled else "disabled"
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
        result = request.app.state.weixin_translation.set_enabled(payload.enabled)
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
