from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict, Field

from app.ai_runtime.external_modules import RuntimeModuleInstallError, is_runtime_module_id
from app.core.response import ApiError, ApiResponse
from app.core.security import require_trusted_network
from app.quick_worker import list_tasks, read_health, refresh_runtime_registry
from app.services.operation_log import log_operation


router = APIRouter(
    prefix="/api/runtime-modules",
    tags=["runtime-modules"],
    dependencies=[Depends(require_trusted_network)],
)


class RuntimeModuleData(BaseModel):
    model_config = ConfigDict(extra="forbid")

    module_id: str = Field(min_length=1, max_length=32)
    version: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=100)
    description: str | None = Field(default=None, max_length=300)
    status: Literal["active", "unavailable"]
    reason: str | None = Field(default=None, max_length=300)
    removable: bool = True


class RuntimeModuleListData(BaseModel):
    modules: list[RuntimeModuleData]


class RuntimeModuleInstallData(BaseModel):
    module_id: str = Field(min_length=1, max_length=32)
    worker_generation: str = Field(min_length=1, max_length=128)


class RuntimeModulePreviewData(BaseModel):
    module_id: str = Field(min_length=1, max_length=32)
    version: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=100)
    description: str = Field(min_length=1, max_length=300)


def _module_list(request: Request) -> RuntimeModuleListData:
    manager = request.app.state.ai_session_manager
    active_ids = set(manager.runtime_modules.implementation_ids())
    entries: list[RuntimeModuleData] = []
    loaded, discovery_failures = manager.runtime_module_service.discover()
    failures = {item.module_id: item for item in discovery_failures}
    failures.update({item.module_id: item for item in manager.runtime_module_failures})
    for item in loaded:
        module_id = item.manifest.module_id
        failure = failures.get(module_id)
        entries.append(
            RuntimeModuleData(
                module_id=module_id,
                version=item.manifest.version,
                name=item.manifest.display_name,
                description=item.manifest.description,
                status="active" if module_id in active_ids and failure is None else "unavailable",
                reason=None if failure is None else failure.reason,
                removable=module_id not in manager._builtin_runtime_modules.implementation_ids(),
            )
        )
    known = {item.module_id for item in entries}
    entries.extend(
        RuntimeModuleData(
            module_id=module_id,
            version=failure.version or "unknown",
            name=failure.name or module_id,
            description=failure.description,
            status="unavailable",
            reason=failure.reason,
            removable=False,
        )
        for module_id, failure in failures.items()
        if module_id not in known
    )
    return RuntimeModuleListData(modules=entries)


def _worker_health_data(payload: dict[str, object]) -> dict[str, object]:
    data = payload.get("data") if payload.get("success") is True else None
    if not isinstance(data, dict):
        raise ApiError(
            503,
            "runtime_module_worker_health_unavailable",
            "Quick Worker 健康状态不可确认，本次未安装 Runtime 模块。",
        )
    return data


async def _worker_generation(request: Request) -> str:
    try:
        data = _worker_health_data(await read_health(request.app.state.settings))
    except OSError as exc:
        raise ApiError(
            503,
            "runtime_module_worker_health_unavailable",
            "Quick Worker 健康状态不可确认，本次未维护 Runtime 版本。",
        ) from exc
    generation = data.get("generation")
    if (
        data.get("status") != "ready"
        or not isinstance(generation, str)
    ):
        raise ApiError(
            409,
            "runtime_module_worker_health_unavailable",
            "Quick Worker 未处于可维护状态，本次未维护 Runtime 版本。",
        )
    return generation


async def _require_implementation_idle(request: Request, implementation_id: str) -> None:
    try:
        payload = await list_tasks(request.app.state.settings, active_only=True)
    except OSError as exc:
        raise ApiError(503, "runtime_module_worker_health_unavailable", "Quick Worker 任务状态不可确认，本次未维护 Runtime 版本。") from exc
    data = payload.get("data") if payload.get("success") is True else None
    tasks = data.get("tasks") if isinstance(data, dict) else None
    if not isinstance(tasks, list):
        raise ApiError(503, "runtime_module_worker_health_unavailable", "Quick Worker 任务状态不可确认，本次未维护 Runtime 版本。")
    if any(item.get("implementation_id") == implementation_id for item in tasks if isinstance(item, dict)):
        raise ApiError(409, "runtime_implementation_busy", "该 Runtime 版本仍有已受理任务，请等待其结束后再覆盖或移除。")


async def _confirm_worker_runtime(
    request: Request, implementation_id: str, *, expected_present: bool
) -> str:
    try:
        data = _worker_health_data(await read_health(request.app.state.settings))
    except OSError as exc:
        raise ApiError(
            503,
            "runtime_module_worker_refresh_unconfirmed",
            "Quick Worker 未能确认新的 Runtime 注册表，模块未激活。",
        ) from exc
    implementation_ids = data.get("implementation_ids")
    available_implementation_ids = data.get("available_implementation_ids")
    generation = data.get("generation")
    if (
        data.get("status") != "ready"
        or not isinstance(implementation_ids, list)
        or (implementation_id in implementation_ids) != expected_present
        or (
            expected_present
            and (
                not isinstance(available_implementation_ids, list)
                or implementation_id not in available_implementation_ids
            )
        )
        or not isinstance(generation, str)
    ):
        raise ApiError(
            503,
            "runtime_module_worker_refresh_unconfirmed",
            "Quick Worker 未能确认新的 Runtime 注册表。",
        )
    return generation


async def _read_archive(request: Request) -> tuple[str, bytes]:
    source_name = request.headers.get("X-Chub-Module-Filename", "runtime-module.zip")
    if len(source_name) > 255:
        raise ApiError(422, "runtime_module_filename_invalid", "模块文件名无效。")
    maximum = request.app.state.settings.ai_runtime.modules.max_archive_bytes
    chunks = bytearray()
    async for chunk in request.stream():
        chunks.extend(chunk)
        if len(chunks) > maximum:
            raise ApiError(413, "runtime_module_too_large", "模块压缩包超过固定大小上限。")
    return source_name, bytes(chunks)


@router.get("", response_model=ApiResponse[RuntimeModuleListData])
def list_runtime_modules(request: Request) -> ApiResponse[RuntimeModuleListData]:
    return ApiResponse(data=_module_list(request))


@router.post("/inspect", response_model=ApiResponse[RuntimeModulePreviewData])
async def inspect_runtime_module(request: Request) -> ApiResponse[RuntimeModulePreviewData]:
    source_name, archive = await _read_archive(request)
    try:
        preview = request.app.state.ai_session_manager.runtime_module_service.inspect_archive(
            archive,
            source_name=source_name,
        )
    except RuntimeModuleInstallError as exc:
        raise ApiError(422, exc.code, exc.message) from exc
    return ApiResponse(data=RuntimeModulePreviewData(**preview.__dict__))


@router.post("/install", response_model=ApiResponse[RuntimeModuleInstallData])
async def install_runtime_module(request: Request) -> ApiResponse[RuntimeModuleInstallData]:
    source_name, archive = await _read_archive(request)
    manager = request.app.state.ai_session_manager
    operation_id = log_operation(request, action="install_runtime_module", status="requested", target=source_name)
    log_operation(request, action="install_runtime_module", status="started", target=source_name, operation_id=operation_id)
    activation = None
    implementation_id: str | None = None
    try:
        preview = manager.runtime_module_service.inspect_archive(archive, source_name=source_name)
        implementation_id = preview.implementation_id
        await _require_implementation_idle(request, implementation_id)
        await _worker_generation(request)
        activation = manager.install_runtime_module(archive, source_name=source_name, operation_id=operation_id)
        refreshed = await refresh_runtime_registry(
            request.app.state.settings,
            implementation_id=implementation_id,
            expected_present=True,
        )
        if refreshed.get("success") is not True:
            raise ApiError(503, "runtime_module_worker_refresh_unconfirmed", "Quick Worker 未能确认新的 Runtime 注册表，模块未激活。")
        generation = await _confirm_worker_runtime(request, implementation_id, expected_present=True)
        manager.runtime_module_service.finalize(activation)
        log_operation(request, action="install_runtime_module", status="succeeded", target=implementation_id, operation_id=operation_id)
        return ApiResponse(data=RuntimeModuleInstallData(module_id=implementation_id, worker_generation=generation))
    except RuntimeModuleInstallError as exc:
        error = ApiError(422, exc.code, exc.message)
    except ApiError as exc:
        error = exc
    except OSError:
        error = ApiError(503, "runtime_module_worker_refresh_unconfirmed", "Quick Worker 未能确认新的 Runtime 注册表，模块未激活。")
    except Exception:
        error = ApiError(500, "runtime_module_install_failed", "Runtime 模块安装失败，当前模块已保持不变。")
    rollback_confirmed = True
    if activation is not None and implementation_id is not None:
        try:
            manager.runtime_module_service.rollback(activation)
            manager.refresh_external_runtime_modules()
            rollback = await refresh_runtime_registry(
                request.app.state.settings,
                implementation_id=implementation_id,
                expected_present=False,
            )
            rollback_confirmed = rollback.get("success") is True
        except Exception:
            rollback_confirmed = False
    if not rollback_confirmed:
        error = ApiError(503, "runtime_module_rollback_unconfirmed", "模块激活失败，Web 与 Quick Worker 的恢复状态无法确认。")
    log_operation(request, action="install_runtime_module", status="failed", target=implementation_id or source_name, operation_id=operation_id, reason=error.code)
    raise error


@router.delete("/{module_id}", response_model=ApiResponse[RuntimeModuleInstallData])
async def remove_runtime_module(module_id: str, request: Request) -> ApiResponse[RuntimeModuleInstallData]:
    if not is_runtime_module_id(module_id):
        raise ApiError(422, "runtime_module_id_invalid", "Runtime 模块标识无效。")
    manager = request.app.state.ai_session_manager
    operation_id = log_operation(request, action="remove_runtime_module", status="requested", target=module_id)
    log_operation(request, action="remove_runtime_module", status="started", target=module_id, operation_id=operation_id)
    removal = None
    try:
        await _require_implementation_idle(request, module_id)
        await _worker_generation(request)
        removal = manager.remove_runtime_module(module_id, operation_id=operation_id)
        refreshed = await refresh_runtime_registry(
            request.app.state.settings,
            implementation_id=module_id,
            expected_present=False,
        )
        if refreshed.get("success") is not True:
            raise ApiError(503, "runtime_module_worker_refresh_unconfirmed", "Quick Worker 未能确认 Runtime 已移除。")
        generation = await _confirm_worker_runtime(request, module_id, expected_present=False)
        manager.runtime_module_service.finalize_removal(removal)
        log_operation(request, action="remove_runtime_module", status="succeeded", target=module_id, operation_id=operation_id)
        return ApiResponse(data=RuntimeModuleInstallData(module_id=module_id, worker_generation=generation))
    except ApiError as exc:
        error = exc
    except OSError:
        error = ApiError(503, "runtime_module_worker_refresh_unconfirmed", "Quick Worker 未能确认 Runtime 已移除。")
    except Exception:
        error = ApiError(500, "runtime_module_remove_failed", "Runtime 模块移除失败，当前模块已保持不变。")
    rollback_confirmed = True
    if removal is not None:
        try:
            manager.runtime_module_service.rollback_removal(removal)
            manager.refresh_external_runtime_modules()
            rollback = await refresh_runtime_registry(
                request.app.state.settings,
                implementation_id=module_id,
                expected_present=True,
            )
            rollback_confirmed = rollback.get("success") is True
        except Exception:
            rollback_confirmed = False
    if not rollback_confirmed:
        error = ApiError(503, "runtime_module_rollback_unconfirmed", "模块移除失败，Web 与 Quick Worker 的恢复状态无法确认。")
    log_operation(request, action="remove_runtime_module", status="failed", target=module_id, operation_id=operation_id, reason=error.code)
    raise error
