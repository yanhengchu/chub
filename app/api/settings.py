from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.core.response import ApiError, ApiResponse
from app.core.security import require_trusted_network
from app.services.operation_log import log_operation
from app.services.weixin_translation import TranslationSettingsStatus
from app.services.deployment_package import (
    DeploymentPackageConfiguration,
    DeploymentPackageStatus,
    FORMAL_CODEX_DESCRIPTION,
    FORMAL_CODEX_IMPLEMENTATION_ID,
)


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
    show_internal_native_session: bool | None = None

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
        display_fields = {"show_internal_native_session"} & self.model_fields_set
        if not mode_fields and not model_fields and not display_fields:
            raise ValueError("a translation setting is required")
        if display_fields and self.show_internal_native_session is None:
            raise ValueError("show_internal_native_session must be a boolean")
        if self.mode is not None and self.enabled is not None:
            raise ValueError("provide mode only")
        if sum(bool(fields) for fields in (mode_fields, model_fields, display_fields)) > 1:
            raise ValueError("provide mode or model settings only")
        if model_fields and model_fields != {"model", "reasoning_effort"}:
            raise ValueError("model and reasoning_effort must be provided together")
        return self


class DeploymentPackageConfigurationUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    chub_release_version: str = Field(min_length=1, max_length=64)
    runtime_release_version: str = Field(min_length=1, max_length=64)
    weixin_release_version: str = Field(min_length=1, max_length=64)
    include_development_sources: bool = False


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
    display_update = "show_internal_native_session" in payload.model_fields_set
    target = (
        "internal_native_session_display"
        if display_update
        else "translation_model" if model_update else mode
    )
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
        if display_update:
            result = request.app.state.weixin_translation.set_show_internal_native_session(
                payload.show_internal_native_session
            )
        elif model_update:
            result = request.app.state.weixin_translation.set_model(
                payload.model,
                payload.reasoning_effort,
            )
        else:
            if mode != "direct" and request.app.state.weixin_chub_mode.orchestration_enabled():
                request.app.state.weixin_chub_mode.require_orchestration_implementation_available()
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


@router.get(
    "/deployment-package",
    response_model=ApiResponse[DeploymentPackageStatus],
)
def get_deployment_package_status(request: Request) -> ApiResponse[DeploymentPackageStatus]:
    return ApiResponse(data=request.app.state.deployment_package.status())


@router.put(
    "/deployment-package",
    response_model=ApiResponse[DeploymentPackageStatus],
)
def update_deployment_package_configuration(
    payload: DeploymentPackageConfigurationUpdate,
    request: Request,
) -> ApiResponse[DeploymentPackageStatus]:
    operation_id = log_operation(
        request,
        action="update_deployment_package_configuration",
        status="requested",
        target="deployment-package",
    )
    try:
        data = request.app.state.deployment_package.save_configuration(
            DeploymentPackageConfiguration(
                chub_release_version=payload.chub_release_version,
                runtime_implementation_id=FORMAL_CODEX_IMPLEMENTATION_ID,
                runtime_release_version=payload.runtime_release_version,
                runtime_description=FORMAL_CODEX_DESCRIPTION,
                weixin_release_version=payload.weixin_release_version,
                include_development_sources=payload.include_development_sources,
            )
        )
    except ApiError as exc:
        log_operation(request, action="update_deployment_package_configuration", status="failed", target="deployment-package", operation_id=operation_id, reason=exc.code)
        raise
    log_operation(request, action="update_deployment_package_configuration", status="succeeded", target="deployment-package", operation_id=operation_id)
    return ApiResponse(data=data)


@router.post(
    "/deployment-package/build",
    response_model=ApiResponse[DeploymentPackageStatus],
)
def build_deployment_package(request: Request) -> ApiResponse[DeploymentPackageStatus]:
    source_ip = request.client.host if request.client else "unknown"
    return ApiResponse(data=request.app.state.deployment_package.start(source_ip=source_ip))
