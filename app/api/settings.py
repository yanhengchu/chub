from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.core.response import ApiError, ApiResponse
from app.core.security import require_trusted_network
from app.services.operation_log import log_operation
from app.services.weixin_translation import TranslationSettingsStatus
from app.services.openclaw_weixin_chub_models import (
    WeixinTaskOrchestrationSettingsStatus,
)
from app.services.weixin_orchestration_modules import (
    WeixinOrchestrationModulePreview,
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


class TaskOrchestrationSettingsUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    implementation: Literal["weixin-orchestration-dev", "module"]
    module_ref: str | None = Field(default=None, min_length=68, max_length=180)

    @model_validator(mode="after")
    def validate_module_reference(self):
        if self.implementation == "module" and self.module_ref is None:
            raise ValueError("module_ref is required for module implementation")
        if self.implementation != "module" and self.module_ref is not None:
            raise ValueError("module_ref is only valid for module implementation")
        return self


class TaskOrchestrationModuleData(BaseModel):
    model_config = ConfigDict(extra="forbid")

    implementation_ref: str = Field(min_length=68, max_length=180)
    module_id: str = Field(min_length=1, max_length=32)
    version: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=100)
    description: str = Field(min_length=1, max_length=300)
    available: bool
    active: bool
    removable: bool
    reason: str | None = Field(default=None, max_length=300)


class TaskOrchestrationModuleListData(BaseModel):
    modules: list[TaskOrchestrationModuleData]


class TaskOrchestrationModulePreviewData(BaseModel):
    module_id: str = Field(min_length=1, max_length=32)
    version: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=100)
    description: str = Field(min_length=1, max_length=300)
    implementation_ref: str = Field(min_length=68, max_length=180)


async def _read_orchestration_module_archive(request: Request) -> tuple[str, bytes]:
    source_name = request.headers.get("X-Chub-Module-Filename", "orchestration-module.zip")
    if len(source_name) > 255:
        raise ApiError(422, "weixin_orchestration_module_filename_invalid", "模块文件名无效。")
    maximum = request.app.state.settings.openclaw.weixin_chub_mode.orchestration_module_max_archive_bytes
    chunks = bytearray()
    async for chunk in request.stream():
        chunks.extend(chunk)
        if len(chunks) > maximum:
            raise ApiError(413, "weixin_orchestration_module_too_large", "编排模块压缩包超过固定大小上限。")
    return source_name, bytes(chunks)


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
            if mode != "direct":
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
    "/weixin-task-orchestration",
    response_model=ApiResponse[WeixinTaskOrchestrationSettingsStatus],
)
def get_weixin_task_orchestration_settings(
    request: Request,
) -> ApiResponse[WeixinTaskOrchestrationSettingsStatus]:
    return ApiResponse(data=request.app.state.weixin_chub_mode.orchestration_settings())


@router.put(
    "/weixin-task-orchestration",
    response_model=ApiResponse[WeixinTaskOrchestrationSettingsStatus],
)
def update_weixin_task_orchestration_settings(
    request: Request,
    payload: TaskOrchestrationSettingsUpdate,
) -> ApiResponse[WeixinTaskOrchestrationSettingsStatus]:
    operation_id = log_operation(
        request,
        action="update_weixin_task_orchestration",
        status="requested",
        target=payload.implementation,
    )
    log_operation(
        request,
        action="update_weixin_task_orchestration",
        status="started",
        target=payload.implementation,
        operation_id=operation_id,
    )
    try:
        result = request.app.state.weixin_chub_mode.set_orchestration_implementation(
            payload.implementation,
            payload.module_ref,
        )
    except ApiError:
        log_operation(
            request,
            action="update_weixin_task_orchestration",
            status="failed",
            target=payload.implementation,
            operation_id=operation_id,
        )
        raise
    except OSError:
        log_operation(
            request,
            action="update_weixin_task_orchestration",
            status="failed",
            target=payload.implementation,
            operation_id=operation_id,
        )
        raise ApiError(
            503,
            "weixin_task_orchestration_settings_unavailable",
            "微信任务编排设置暂时无法保存。",
        ) from None
    log_operation(
        request,
        action="update_weixin_task_orchestration",
        status="succeeded",
        target=payload.implementation,
        operation_id=operation_id,
    )
    return ApiResponse(data=result)


@router.get(
    "/weixin-task-orchestration/modules",
    response_model=ApiResponse[TaskOrchestrationModuleListData],
)
def list_weixin_task_orchestration_modules(
    request: Request,
) -> ApiResponse[TaskOrchestrationModuleListData]:
    modules = [
        TaskOrchestrationModuleData(
            implementation_ref=item.implementation_ref,
            module_id=item.module_id,
            version=item.version,
            name=item.name,
            description=item.description,
            available=item.available,
            active=active,
            removable=removable,
            reason=item.reason,
        )
        for item, active, removable in request.app.state.weixin_chub_mode.list_orchestration_modules()
    ]
    return ApiResponse(data=TaskOrchestrationModuleListData(modules=modules))


@router.post(
    "/weixin-task-orchestration/modules/inspect",
    response_model=ApiResponse[TaskOrchestrationModulePreviewData],
)
async def inspect_weixin_task_orchestration_module(
    request: Request,
) -> ApiResponse[TaskOrchestrationModulePreviewData]:
    source_name, archive = await _read_orchestration_module_archive(request)
    preview = request.app.state.weixin_chub_mode.orchestration_module_service.inspect_archive(
        archive,
        source_name=source_name,
    )
    return ApiResponse(data=TaskOrchestrationModulePreviewData(**preview.__dict__))


@router.post(
    "/weixin-task-orchestration/modules/install",
    response_model=ApiResponse[TaskOrchestrationModulePreviewData],
)
async def install_weixin_task_orchestration_module(
    request: Request,
) -> ApiResponse[TaskOrchestrationModulePreviewData]:
    source_name, archive = await _read_orchestration_module_archive(request)
    operation_id = log_operation(
        request,
        action="install_weixin_orchestration_module",
        status="requested",
        target=source_name,
    )
    log_operation(request, action="install_weixin_orchestration_module", status="started", target=source_name, operation_id=operation_id)
    try:
        preview = request.app.state.weixin_chub_mode.orchestration_module_service.install(
            archive,
            source_name=source_name,
        )
    except ApiError as exc:
        log_operation(request, action="install_weixin_orchestration_module", status="failed", target=source_name, operation_id=operation_id, reason=exc.code)
        raise
    log_operation(request, action="install_weixin_orchestration_module", status="succeeded", target=preview.implementation_ref, operation_id=operation_id)
    return ApiResponse(data=TaskOrchestrationModulePreviewData(**preview.__dict__))


@router.delete(
    "/weixin-task-orchestration/modules/{implementation_ref}",
    response_model=ApiResponse[TaskOrchestrationModulePreviewData],
)
def remove_weixin_task_orchestration_module(
    implementation_ref: str,
    request: Request,
) -> ApiResponse[TaskOrchestrationModulePreviewData]:
    operation_id = log_operation(request, action="remove_weixin_orchestration_module", status="requested", target=implementation_ref)
    log_operation(request, action="remove_weixin_orchestration_module", status="started", target=implementation_ref, operation_id=operation_id)
    try:
        artifacts = request.app.state.weixin_chub_mode.orchestration_module_service.list_artifacts()
        artifact = next((item for item in artifacts if item.implementation_ref == implementation_ref), None)
        if artifact is None:
            raise ApiError(404, "weixin_orchestration_module_not_found", "编排模块不存在。")
        request.app.state.weixin_chub_mode.remove_orchestration_module(implementation_ref)
    except ApiError as exc:
        log_operation(request, action="remove_weixin_orchestration_module", status="failed", target=implementation_ref, operation_id=operation_id, reason=exc.code)
        raise
    log_operation(request, action="remove_weixin_orchestration_module", status="succeeded", target=implementation_ref, operation_id=operation_id)
    return ApiResponse(
        data=TaskOrchestrationModulePreviewData(
            module_id=artifact.module_id,
            version=artifact.version,
            name=artifact.name,
            description=artifact.description,
            implementation_ref=artifact.implementation_ref,
        )
    )
