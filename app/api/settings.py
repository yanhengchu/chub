from __future__ import annotations

from collections.abc import Callable

from fastapi import APIRouter, Depends, Header, Request
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.core.response import ApiError, ApiResponse
from app.core.security import require_trusted_network
from app.core.business_modules import loaded_business_module, loaded_business_modules
from app.services.internal_session_visibility import internal_session_visibility_lock
from app.services.operation_log import log_operation
from app.services.deployment_package import (
    DeploymentPackageConfiguration,
    DeploymentPackageReleasePreview,
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


class DeploymentPackageReleasePreviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    release_version: str = Field(pattern=RELEASE_VERSION_PATTERN)


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
    business_modules: dict[str, bool] = Field(default_factory=dict)
    # Kept as a response compatibility alias for existing clients.
    deliveryline: bool | None = None
    today_focus: bool
    deployment_package: bool


@router.get(
    "/business-modules/{module_id}/session-visibility",
    response_model=ApiResponse[InternalSessionVisibilityUpdate],
)
def get_business_module_session_visibility(
    request: Request,
    module_id: str,
) -> ApiResponse[InternalSessionVisibilityUpdate]:
    module = loaded_business_module(request, module_id)
    if (
        module is None
        or module.session_visibility_get is None
        or module_id not in request.app.state.plugin_lifecycle.imported_plugin_ids()
    ):
        raise ApiError(404, "business_module_not_found", "业务模块不可用。")
    try:
        return ApiResponse(
            data=InternalSessionVisibilityUpdate(
                show_sessions=module.session_visibility_get(request)
            )
        )
    except (OSError, RuntimeError):
        raise ApiError(
            503,
            "internal_session_visibility_unavailable",
            "内部会话显示设置暂时不可用。",
        ) from None


@router.put(
    "/business-modules/{module_id}/session-visibility",
    response_model=ApiResponse[InternalSessionVisibilityUpdate],
)
def update_business_module_session_visibility(
    request: Request,
    module_id: str,
    payload: InternalSessionVisibilityUpdate,
) -> ApiResponse[InternalSessionVisibilityUpdate]:
    module = loaded_business_module(request, module_id)
    if (
        module is None
        or module.session_visibility_get is None
        or module.session_visibility_set is None
        or module_id not in request.app.state.plugin_lifecycle.imported_plugin_ids()
    ):
        raise ApiError(404, "business_module_not_found", "业务模块不可用。")
    try:
        module.session_visibility_set(request, payload.show_sessions)
        return ApiResponse(
            data=InternalSessionVisibilityUpdate(
                show_sessions=module.session_visibility_get(request)
            )
        )
    except (OSError, RuntimeError):
        raise ApiError(
            503,
            "internal_session_visibility_update_failed",
            "内部会话显示设置未能完成，请稍后重试。",
        ) from None


def _internal_session_visibility_data(request: Request) -> InternalSessionVisibilityData:
    try:
        imported_plugins = request.app.state.plugin_lifecycle.imported_plugin_ids()
        module_visibility: dict[str, bool] = {}
        for module in loaded_business_modules(request):
            if (
                module.module_id in imported_plugins
                and module.session_visibility_get is not None
            ):
                module_visibility[module.module_id] = module.session_visibility_get(request)
        return InternalSessionVisibilityData(
            business_modules=module_visibility,
            deliveryline=module_visibility.get("deliveryline"),
            today_focus=request.app.state.ai_search.show_sessions(),
            deployment_package=request.app.state.deployment_package.show_release_note_session(),
        )
    except (OSError, RuntimeError):
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
    for module in loaded_business_modules(request):
        value = current.business_modules.get(module.module_id)
        if value is None and module.module_id == "deliveryline":
            value = current.deliveryline
        if value is not None and module.session_visibility_set is not None:
            setters.append(
                (
                    lambda value, module=module: module.session_visibility_set(request, value),
                    value,
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
        except (ApiError, OSError, RuntimeError) as exc:
            if not applied and isinstance(exc, ApiError):
                raise
            rollback_failed = False
            for setter, previous in reversed(applied):
                try:
                    setter(previous)
                except (ApiError, OSError, RuntimeError):
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
    "/deployment-package/release-preview",
    response_model=ApiResponse[DeploymentPackageReleasePreview],
)
def preview_deployment_package_release(
    payload: DeploymentPackageReleasePreviewRequest,
    request: Request,
) -> ApiResponse[DeploymentPackageReleasePreview]:
    return ApiResponse(
        data=request.app.state.deployment_package.release_preview(payload.release_version)
    )


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
        include_development_sources=payload.include_development_sources,
        release_note=payload.release_note,
    )
    return ApiResponse(
        data=request.app.state.deployment_package.start(
            source_ip=source_ip,
            configuration=configuration,
        )
    )
