from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict, Field

from app.core.response import ApiError, ApiResponse
from app.core.security import require_trusted_network
from app.services.operation_log import log_operation

router = APIRouter(prefix="/api/plugins", tags=["plugins"], dependencies=[Depends(require_trusted_network)])


class ArtifactSelection(BaseModel):
    model_config = ConfigDict(extra="forbid")
    artifact_id: str = Field(min_length=1, max_length=255)


class Enablement(BaseModel):
    model_config = ConfigDict(extra="forbid")
    artifact_id: str = Field(min_length=1, max_length=255)
    enabled: bool


@router.get("")
def list_plugins(request: Request) -> ApiResponse[dict[str, object]]:
    return ApiResponse(data=request.app.state.plugin_lifecycle.list(request))


@router.post("/{plugin_id}/imports")
async def import_plugin(plugin_id: str, payload: ArtifactSelection, request: Request) -> ApiResponse[dict[str, object]]:
    operation_id = log_operation(request, action="import_plugin", status="requested", target=f"{plugin_id}:{payload.artifact_id}")
    log_operation(request, action="import_plugin", status="started", target=plugin_id, operation_id=operation_id)
    try:
        data = await request.app.state.plugin_lifecycle.import_artifact(request, plugin_id, payload.artifact_id)
    except ApiError as exc:
        log_operation(request, action="import_plugin", status="failed", target=plugin_id, operation_id=operation_id, reason=exc.code)
        raise
    log_operation(request, action="import_plugin", status="succeeded", target=plugin_id, operation_id=operation_id)
    return ApiResponse(data=data)


@router.delete("/{plugin_id}/imports/{artifact_id}")
async def remove_plugin(plugin_id: str, artifact_id: str, request: Request) -> ApiResponse[dict[str, object]]:
    operation_id = log_operation(request, action="remove_plugin", status="requested", target=f"{plugin_id}:{artifact_id}")
    log_operation(request, action="remove_plugin", status="started", target=plugin_id, operation_id=operation_id)
    try:
        data = await request.app.state.plugin_lifecycle.remove(request, plugin_id, artifact_id)
    except ApiError as exc:
        log_operation(request, action="remove_plugin", status="failed", target=plugin_id, operation_id=operation_id, reason=exc.code)
        raise
    log_operation(request, action="remove_plugin", status="succeeded", target=plugin_id, operation_id=operation_id)
    return ApiResponse(data=data)


@router.put("/{plugin_id}/enabled")
async def update_plugin_enablement(plugin_id: str, payload: Enablement, request: Request) -> ApiResponse[dict[str, object]]:
    action = "enable_plugin" if payload.enabled else "disable_plugin"
    operation_id = log_operation(request, action=action, status="requested", target=f"{plugin_id}:{payload.artifact_id}")
    try:
        data = await request.app.state.plugin_lifecycle.set_enabled(request, plugin_id, payload.artifact_id, payload.enabled)
    except ApiError as exc:
        log_operation(request, action=action, status="failed", target=plugin_id, operation_id=operation_id, reason=exc.code)
        raise
    log_operation(request, action=action, status="succeeded", target=plugin_id, operation_id=operation_id)
    return ApiResponse(data=data)
