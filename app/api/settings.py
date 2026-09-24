from __future__ import annotations

from fastapi import APIRouter, Depends, Header, Request
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.core.response import ApiError, ApiResponse
from app.core.security import require_trusted_network
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


class InternalSessionVisibilityUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    show_internal_sessions: bool


class InternalSessionVisibilityData(BaseModel):
    show_internal_sessions: bool


@router.get(
    "/internal-session-visibility",
    response_model=ApiResponse[InternalSessionVisibilityData],
)
def get_internal_session_visibility(request: Request) -> ApiResponse[InternalSessionVisibilityData]:
    return ApiResponse(
        data=InternalSessionVisibilityData(
            show_internal_sessions=request.app.state.ai_session_manager.show_internal_sessions()
        )
    )


@router.put(
    "/internal-session-visibility",
    response_model=ApiResponse[InternalSessionVisibilityData],
)
def update_internal_session_visibility(
    request: Request,
    payload: InternalSessionVisibilityUpdate,
) -> ApiResponse[InternalSessionVisibilityData]:
    return ApiResponse(
        data=InternalSessionVisibilityData(
            show_internal_sessions=request.app.state.ai_session_manager.set_show_internal_sessions(
                payload.show_internal_sessions
            )
        )
    )


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
