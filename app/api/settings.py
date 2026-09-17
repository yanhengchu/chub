from __future__ import annotations

from collections.abc import Callable

from fastapi import APIRouter, Depends, Header, Request
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.core.response import ApiError, ApiResponse
from app.core.security import require_trusted_network
from app.deliveryline.store import DeliverylineError
from app.services.internal_session_visibility import internal_session_visibility_lock
from app.services.operation_log import log_operation
from app.services.weixin_translation import TranslationSettingsStatus
from app.services.deployment_package import (
    DeploymentPackageConfiguration,
    DeploymentPackageStatus,
    FORMAL_CODEX_DESCRIPTION,
    FORMAL_CODEX_IMPLEMENTATION_ID,
    RELEASE_VERSION_PATTERN,
)


router = APIRouter(
    prefix="/api/settings",
    tags=["settings"],
    dependencies=[Depends(require_trusted_network)],
)

class TranslationSettingsUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: Literal["direct", "auto", "confirm"] | None = None
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
        mode_fields = {"mode"} & self.model_fields_set
        execution_fields = {"model", "reasoning_effort"} & self.model_fields_set
        display_fields = {"show_internal_native_session"} & self.model_fields_set
        if not mode_fields and not execution_fields and not display_fields:
            raise ValueError("a translation setting is required")
        if display_fields and self.show_internal_native_session is None:
            raise ValueError("show_internal_native_session must be a boolean")
        if sum(bool(fields) for fields in (mode_fields, execution_fields, display_fields)) > 1:
            raise ValueError("provide mode or execution settings only")
        return self


class DeploymentPackageSettingsUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    release_version: str = Field(pattern=RELEASE_VERSION_PATTERN)
    include_development_sources: bool = False


class DeploymentPackageBuildRequest(DeploymentPackageSettingsUpdate):
    release_note: str = Field(default="", max_length=2000)

    @field_validator("release_note")
    @classmethod
    def normalize_release_note(cls, value: str) -> str:
        return value.strip()


class DeploymentPackageReleaseNoteRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    release_version: str = Field(pattern=RELEASE_VERSION_PATTERN)
    include_development_sources: bool = False


class DeploymentPackageReleaseNoteSessionVisibilityUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    show_sessions: bool


class InternalSessionVisibilityUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    show_sessions: bool


class InternalSessionVisibilityData(BaseModel):
    deliveryline: bool | None = None
    translation: bool | None = None
    today_focus: bool
    deployment_package: bool


def _internal_session_visibility_data(request: Request) -> InternalSessionVisibilityData:
    try:
        imported_plugins = request.app.state.plugin_lifecycle.imported_plugin_ids()
        deliveryline = None
        if "deliveryline" in imported_plugins:
            deliveryline = request.app.state.deliveryline_collaboration.show_sessions()
        translation = None
        if "weixin-orchestration" in imported_plugins:
            translation = request.app.state.weixin_translation.status().show_internal_native_session
        return InternalSessionVisibilityData(
            deliveryline=deliveryline,
            translation=translation,
            today_focus=request.app.state.ai_search.show_sessions(),
            deployment_package=request.app.state.deployment_package.show_release_note_session(),
        )
    except (DeliverylineError, OSError):
        raise ApiError(
            503,
            "internal_session_visibility_unavailable",
            "内部会话显示设置暂时不可用。",
        ) from None


def _internal_session_visibility_setters(
    request: Request,
    current: InternalSessionVisibilityData,
) -> list[tuple[Callable[[bool], object], bool]]:
    setters: list[tuple[Callable[[bool], object], bool]] = []
    if current.deliveryline is not None:
        setters.append((request.app.state.deliveryline_collaboration.set_show_sessions, current.deliveryline))
    if current.translation is not None:
        setters.append(
            (
                request.app.state.weixin_translation.set_show_internal_native_session,
                current.translation,
            )
        )
    setters.extend(
        (
            (request.app.state.ai_search.set_show_sessions, current.today_focus),
            (
                request.app.state.deployment_package.set_show_release_note_session,
                current.deployment_package,
            ),
        )
    )
    return setters


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
    model_update = "model" in payload.model_fields_set
    reasoning_update = "reasoning_effort" in payload.model_fields_set
    execution_update = model_update or reasoning_update
    display_update = "show_internal_native_session" in payload.model_fields_set
    target = (
        "internal_native_session_display"
        if display_update
        else "translation_execution_settings" if execution_update else mode
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
            with internal_session_visibility_lock:
                result = request.app.state.weixin_translation.set_show_internal_native_session(
                    payload.show_internal_native_session
                )
        elif execution_update:
            current = request.app.state.weixin_translation.status()
            result = request.app.state.weixin_translation.set_execution_settings(
                current.runtime_id,
                payload.model if model_update else current.model,
                payload.reasoning_effort if reasoning_update else current.reasoning_effort,
            )
        else:
            assert mode is not None
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


@router.put(
    "/internal-session-visibility",
    response_model=ApiResponse[InternalSessionVisibilityData],
)
def update_internal_session_visibility(
    request: Request,
    payload: InternalSessionVisibilityUpdate,
) -> ApiResponse[InternalSessionVisibilityData]:
    with internal_session_visibility_lock:
        applied: list[tuple[Callable[[bool], object], bool]] = []
        try:
            current = _internal_session_visibility_data(request)
            for setter, previous in _internal_session_visibility_setters(request, current):
                if previous != payload.show_sessions:
                    setter(payload.show_sessions)
                    applied.append((setter, previous))
            result = _internal_session_visibility_data(request)
        except (ApiError, DeliverylineError, OSError) as exc:
            if not applied and isinstance(exc, ApiError):
                raise
            rollback_failed = False
            for setter, previous in reversed(applied):
                try:
                    setter(previous)
                except (ApiError, DeliverylineError, OSError):
                    rollback_failed = True
            if rollback_failed:
                raise ApiError(
                    503,
                    "internal_session_visibility_state_unknown",
                    "部分内部会话显示设置状态暂时无法确认，请刷新后重试。",
                ) from None
            raise ApiError(
                503,
                "internal_session_visibility_update_failed",
                "内部会话显示设置未能完成，已恢复原状态。",
            ) from None
    return ApiResponse(data=result)


@router.get(
    "/deployment-package",
    response_model=ApiResponse[DeploymentPackageStatus],
)
def get_deployment_package_status(
    request: Request,
    release_note_draft_token: str | None = Header(
        default=None,
        max_length=128,
        pattern=r"^[A-Za-z0-9_-]{16,128}$",
        alias="X-Chub-Release-Note-Draft-Token",
    ),
) -> ApiResponse[DeploymentPackageStatus]:
    return ApiResponse(
        data=request.app.state.deployment_package.status(
            release_note_draft_token=release_note_draft_token
        )
    )


@router.put(
    "/deployment-package",
    response_model=ApiResponse[DeploymentPackageStatus],
)
def update_deployment_package_configuration(
    payload: DeploymentPackageSettingsUpdate,
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
                chub_release_version=payload.release_version,
                runtime_implementation_id=FORMAL_CODEX_IMPLEMENTATION_ID,
                runtime_release_version=payload.release_version,
                runtime_description=FORMAL_CODEX_DESCRIPTION,
                weixin_release_version=payload.release_version,
                include_development_sources=payload.include_development_sources,
                release_note="",
            )
        )
    except ApiError as exc:
        log_operation(request, action="update_deployment_package_configuration", status="failed", target="deployment-package", operation_id=operation_id, reason=exc.code)
        raise
    log_operation(request, action="update_deployment_package_configuration", status="succeeded", target="deployment-package", operation_id=operation_id)
    return ApiResponse(data=data)


@router.post(
    "/deployment-package/release-note",
    response_model=ApiResponse[DeploymentPackageStatus],
)
def generate_deployment_package_release_note(
    payload: DeploymentPackageReleaseNoteRequest,
    request: Request,
    release_note_draft_token: str = Header(
        min_length=16,
        max_length=128,
        pattern=r"^[A-Za-z0-9_-]{16,128}$",
        alias="X-Chub-Release-Note-Draft-Token",
    ),
) -> ApiResponse[DeploymentPackageStatus]:
    source_ip = request.client.host if request.client else "unknown"
    operation_id = log_operation(
        request,
        action="generate_deployment_package_release_note",
        status="requested",
        target=payload.release_version,
    )
    log_operation(
        request,
        action="generate_deployment_package_release_note",
        status="started",
        target=payload.release_version,
        operation_id=operation_id,
    )
    try:
        data = request.app.state.deployment_package.generate_release_note(
            release_version=payload.release_version,
            include_development_sources=payload.include_development_sources,
            source_ip=source_ip,
            operation_id=operation_id,
            release_note_draft_token=release_note_draft_token,
        )
    except ApiError as exc:
        log_operation(
            request,
            action="generate_deployment_package_release_note",
            status="failed",
            target=payload.release_version,
            operation_id=operation_id,
            reason=exc.code,
        )
        raise
    except Exception:
        log_operation(
            request,
            action="generate_deployment_package_release_note",
            status="failed",
            target=payload.release_version,
            operation_id=operation_id,
            reason="submission_failed",
        )
        raise
    return ApiResponse(data=data)


@router.get(
    "/deployment-package/release-note-session",
    response_model=ApiResponse[dict[str, bool]],
)
def get_deployment_package_release_note_session_visibility(
    request: Request,
) -> ApiResponse[dict[str, bool]]:
    return ApiResponse(
        data={"show_sessions": request.app.state.deployment_package.show_release_note_session()}
    )


@router.put(
    "/deployment-package/release-note-session",
    response_model=ApiResponse[dict[str, bool]],
)
def update_deployment_package_release_note_session_visibility(
    payload: DeploymentPackageReleaseNoteSessionVisibilityUpdate,
    request: Request,
) -> ApiResponse[dict[str, bool]]:
    with internal_session_visibility_lock:
        show_sessions = request.app.state.deployment_package.set_show_release_note_session(
            payload.show_sessions
        )
    return ApiResponse(data={"show_sessions": show_sessions})


@router.post(
    "/deployment-package/open-output",
    response_model=ApiResponse[dict[str, str]],
)
def open_deployment_package_output(request: Request) -> ApiResponse[dict[str, str]]:
    operation_id = log_operation(
        request,
        action="open_deployment_package_output",
        status="requested",
        target="deployment-package-output",
    )
    log_operation(
        request,
        action="open_deployment_package_output",
        status="started",
        target="deployment-package-output",
        operation_id=operation_id,
    )
    try:
        request.app.state.deployment_package.open_output_directory()
    except ApiError as exc:
        log_operation(
            request,
            action="open_deployment_package_output",
            status="failed",
            target="deployment-package-output",
            operation_id=operation_id,
            reason=exc.code,
        )
        raise
    log_operation(
        request,
        action="open_deployment_package_output",
        status="succeeded",
        target="deployment-package-output",
        operation_id=operation_id,
    )
    return ApiResponse(data={"status": "succeeded"})


@router.post(
    "/deployment-package/build",
    response_model=ApiResponse[DeploymentPackageStatus],
)
def build_deployment_package(
    payload: DeploymentPackageBuildRequest,
    request: Request,
) -> ApiResponse[DeploymentPackageStatus]:
    source_ip = request.client.host if request.client else "unknown"
    configuration = DeploymentPackageConfiguration(
        chub_release_version=payload.release_version,
        runtime_implementation_id=FORMAL_CODEX_IMPLEMENTATION_ID,
        runtime_release_version=payload.release_version,
        runtime_description=FORMAL_CODEX_DESCRIPTION,
        weixin_release_version=payload.release_version,
        include_development_sources=payload.include_development_sources,
        release_note=payload.release_note,
    )
    return ApiResponse(
        data=request.app.state.deployment_package.start(
            source_ip=source_ip,
            configuration=configuration,
        )
    )
