from __future__ import annotations

import asyncio
import time
from typing import Literal

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict, Field

from app.ai_runtime.external_modules import RuntimeModuleInstallError, is_runtime_module_id
from app.core.response import ApiError, ApiResponse
from app.core.security import require_trusted_network
from app.quick_worker import clear_runtime_state, read_health, request_drain, resume_after_drain
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
    active_ids = set(manager.runtime_modules.runtime_ids())
    entries: list[RuntimeModuleData] = []
    loaded, discovery_failures = manager.runtime_module_service.discover()
    failures = {item.module_id: item.reason for item in discovery_failures}
    failures.update(
        {item.module_id: item.reason for item in manager.runtime_module_failures}
    )
    for item in loaded:
        module_id = item.manifest.module_id
        reason = failures.get(module_id)
        entries.append(
            RuntimeModuleData(
                module_id=module_id,
                version=item.manifest.version,
                name=item.manifest.display_name,
                description=item.manifest.description,
                status="active" if module_id in active_ids and reason is None else "unavailable",
                reason=reason,
            )
        )
    known = {item.module_id for item in entries}
    entries.extend(
        RuntimeModuleData(
            module_id=module_id,
            version="unknown",
            name=module_id,
            description=None,
            status="unavailable",
            reason=reason,
        )
        for module_id, reason in failures.items()
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


async def _read_idle_worker_generation(request: Request) -> str:
    try:
        data = _worker_health_data(await read_health(request.app.state.settings))
    except OSError as exc:
        raise ApiError(
            503,
            "runtime_module_worker_health_unavailable",
            "Quick Worker 健康状态不可确认，本次未安装 Runtime 模块。",
        ) from exc
    generation = data.get("generation")
    if (
        data.get("status") != "ready"
        or data.get("active_tasks") != 0
        or data.get("queued_tasks") != 0
        or data.get("uncertain_tasks") != 0
        or data.get("corrupt_tasks") != 0
        or not isinstance(generation, str)
    ):
        raise ApiError(
            409,
            "runtime_module_worker_busy",
            "Quick Worker 未处于可安全维护的空闲状态，请等待任务完成或恢复后再安装 Runtime 模块。",
        )
    return generation


async def _wait_for_reload(coordinator) -> bool:
    deadline = time.monotonic() + 45
    while time.monotonic() < deadline:
        operation = coordinator.operation()
        if operation is not None and operation.status in {"succeeded", "failed"}:
            return operation.status == "succeeded"
        await asyncio.sleep(0.2)
    return False


async def _confirm_worker_runtime(request: Request, module_id: str) -> str:
    try:
        data = _worker_health_data(await read_health(request.app.state.settings))
    except OSError as exc:
        raise ApiError(
            503,
            "runtime_module_worker_refresh_unconfirmed",
            "Quick Worker 未能确认新的 Runtime 注册表，模块未激活。",
        ) from exc
    runtime_ids = data.get("runtime_ids")
    available_runtime_ids = data.get("available_runtime_ids")
    generation = data.get("generation")
    if (
        data.get("status") != "ready"
        or data.get("active_tasks") != 0
        or data.get("queued_tasks") != 0
        or data.get("uncertain_tasks") != 0
        or data.get("corrupt_tasks") != 0
        or not isinstance(runtime_ids, list)
        or module_id not in runtime_ids
        or not isinstance(available_runtime_ids, list)
        or module_id not in available_runtime_ids
        or not isinstance(generation, str)
    ):
        raise ApiError(
            503,
            "runtime_module_worker_refresh_unconfirmed",
            "Quick Worker 未能确认新的 Runtime 注册表，模块未激活。",
        )
    return generation


async def _confirm_worker_runtime_removed(request: Request, module_id: str) -> str:
    try:
        data = _worker_health_data(await read_health(request.app.state.settings))
    except OSError as exc:
        raise ApiError(503, "runtime_module_worker_refresh_unconfirmed", "Quick Worker 未能确认 Runtime 已移除。") from exc
    generation = data.get("generation")
    runtime_ids = data.get("runtime_ids")
    if (
        data.get("status") != "ready"
        or data.get("active_tasks") != 0
        or data.get("queued_tasks") != 0
        or data.get("uncertain_tasks") != 0
        or data.get("corrupt_tasks") != 0
        or not isinstance(runtime_ids, list)
        or module_id in runtime_ids
        or not isinstance(generation, str)
    ):
        raise ApiError(503, "runtime_module_worker_refresh_unconfirmed", "Quick Worker 未能确认 Runtime 已移除。")
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
    operation_id = log_operation(
        request,
        action="install_runtime_module",
        status="requested",
        target=source_name,
    )
    log_operation(
        request,
        action="install_runtime_module",
        status="started",
        target=source_name,
        operation_id=operation_id,
    )
    activation = None
    drained = False
    reload_started = False
    registry_confirmed = False
    state_cleanup_started = False
    state_cleanup_completed = False
    drain_operation_id = f"runtime-module:{operation_id}"
    old_generation: str | None = None
    coordinator = request.app.state.quick_worker_maintenance
    try:
        preview = manager.runtime_module_service.inspect_archive(
            archive,
            source_name=source_name,
        )
        old_generation = await _read_idle_worker_generation(request)
        drain = await request_drain(
            request.app.state.settings,
            operation_id=drain_operation_id,
            wait_seconds=15,
        )
        if drain.get("success") is not True:
            raise ApiError(
                503,
                "runtime_module_worker_drain_unconfirmed",
                "Quick Worker 未能确认维护排空，本次未安装 Runtime 模块。",
        )
        drained = True
        activation = manager.install_runtime_module(
            archive,
            source_name=source_name,
            operation_id=operation_id,
        )
        manager.runtime_module_service.mark_worker_reload_requested(activation)
        if not coordinator.begin(
            old_generation,
            request.client.host if request.client else "unknown",
        ):
            raise ApiError(
                409,
                "quick_worker_reload_in_progress",
                "Quick Worker 正在重启，模块尚未激活。",
            )
        reload_started = True
        if not await _wait_for_reload(coordinator):
            raise ApiError(
                503,
                "runtime_module_worker_refresh_unconfirmed",
                "Quick Worker 未能确认新的 Runtime 注册表，模块未激活。",
            )
        module_id = activation.installed.manifest.module_id
        generation = await _confirm_worker_runtime(request, module_id)
        registry_confirmed = True
        # Registry confirmation is the last rollback-prone step. Clear the
        # upgrade boundary only after both Web and Worker use the new module.
        state_cleanup_started = True
        cleared = await clear_runtime_state(
            request.app.state.settings,
            runtime_id=preview.module_id,
        )
        if cleared.get("success") is not True:
            raise ApiError(
                503,
                "runtime_module_state_cleanup_unconfirmed",
                "Runtime 模块已激活，但关联运行态清理未确认。",
            )
        manager.clear_runtime_module_state(preview.module_id)
        state_cleanup_completed = True
        manager.runtime_module_service.finalize(activation)
        log_operation(
            request,
            action="install_runtime_module",
            status="succeeded",
            target=module_id,
            operation_id=operation_id,
        )
        return ApiResponse(
            data=RuntimeModuleInstallData(
                module_id=module_id,
                worker_generation=generation,
            )
        )
    except RuntimeModuleInstallError as exc:
        error = ApiError(422, exc.code, exc.message)
    except ApiError as exc:
        error = exc
    except (OSError, TimeoutError):
        error = ApiError(
            503,
            "runtime_module_worker_refresh_unconfirmed",
            "Quick Worker 未能确认新的 Runtime 注册表，模块未激活。",
        )
    except Exception:
        error = ApiError(
            500,
            "runtime_module_install_failed",
            "Runtime 模块安装失败，当前模块已保持不变。",
        )

    if activation is not None and registry_confirmed and state_cleanup_started:
        finalized = True
        try:
            manager.runtime_module_service.finalize(activation)
        except Exception:
            finalized = False
            error = ApiError(
                503,
                "runtime_module_finalization_unconfirmed",
                "Runtime 模块已激活，但最终状态无法确认。",
            )
        if finalized and not state_cleanup_completed:
            error = ApiError(
                503,
                "runtime_module_state_cleanup_unconfirmed",
                "Runtime 模块已激活，但关联运行态清理未确认。",
            )
        log_operation(
            request,
            action="install_runtime_module",
            status="failed",
            target=activation.installed.manifest.module_id,
            operation_id=operation_id,
            reason=error.code,
        )
        raise error

    rollback_confirmed = True
    if activation is not None:
        try:
            manager.runtime_module_service.rollback(activation)
            manager.refresh_external_runtime_modules()
            if reload_started:
                current_generation = await _read_idle_worker_generation(request)
                if not coordinator.begin(
                    current_generation,
                    request.client.host if request.client else "unknown",
                ) or not await _wait_for_reload(coordinator):
                    rollback_confirmed = False
            elif drained:
                resumed = await resume_after_drain(
                    request.app.state.settings,
                    operation_id=drain_operation_id,
                )
                rollback_confirmed = resumed.get("success") is True
        except Exception:
            rollback_confirmed = False
    elif drained and not reload_started:
        try:
            resumed = await resume_after_drain(
                request.app.state.settings,
                operation_id=drain_operation_id,
            )
            rollback_confirmed = resumed.get("success") is True
        except Exception:
            rollback_confirmed = False
    if not rollback_confirmed:
        error = ApiError(
            503,
            "runtime_module_rollback_unconfirmed",
            "模块激活失败，Web 与 Quick Worker 的恢复状态无法确认。",
        )
    log_operation(
        request,
        action="install_runtime_module",
        status="failed",
        target=source_name,
        operation_id=operation_id,
        reason=error.code,
    )
    raise error


@router.delete("/{module_id}", response_model=ApiResponse[RuntimeModuleInstallData])
async def remove_runtime_module(module_id: str, request: Request) -> ApiResponse[RuntimeModuleInstallData]:
    if not is_runtime_module_id(module_id):
        raise ApiError(422, "runtime_module_id_invalid", "Runtime 模块标识无效。")
    manager = request.app.state.ai_session_manager
    operation_id = log_operation(
        request,
        action="remove_runtime_module",
        status="requested",
        target=module_id,
    )
    log_operation(
        request,
        action="remove_runtime_module",
        status="started",
        target=module_id,
        operation_id=operation_id,
    )
    removal = None
    drained = False
    reload_started = False
    registry_confirmed = False
    state_cleanup_started = False
    state_cleanup_completed = False
    coordinator = request.app.state.quick_worker_maintenance
    drain_operation_id = f"runtime-module:{operation_id}"
    try:
        old_generation = await _read_idle_worker_generation(request)
        drained_result = await request_drain(
            request.app.state.settings,
            operation_id=drain_operation_id,
            wait_seconds=15,
        )
        if drained_result.get("success") is not True:
            raise ApiError(503, "runtime_module_worker_drain_unconfirmed", "Quick Worker 未能确认维护排空，本次未移除 Runtime 模块。")
        drained = True
        removal = manager.remove_runtime_module(module_id, operation_id=operation_id)
        if not coordinator.begin(old_generation, request.client.host if request.client else "unknown"):
            raise ApiError(409, "quick_worker_reload_in_progress", "Quick Worker 正在重启，模块尚未移除。")
        reload_started = True
        if not await _wait_for_reload(coordinator):
            raise ApiError(503, "runtime_module_worker_refresh_unconfirmed", "Quick Worker 未能确认 Runtime 已移除。")
        generation = await _confirm_worker_runtime_removed(request, module_id)
        registry_confirmed = True
        # The removed Runtime is no longer registered in either process, so
        # a later Worker reload failure cannot force a stateful rollback.
        state_cleanup_started = True
        cleared = await clear_runtime_state(
            request.app.state.settings,
            runtime_id=module_id,
        )
        if cleared.get("success") is not True:
            raise ApiError(503, "runtime_module_state_cleanup_unconfirmed", "Runtime 模块已移除，但关联运行态清理未确认。")
        manager.clear_runtime_module_state(module_id)
        state_cleanup_completed = True
        manager.runtime_module_service.finalize_removal(removal)
        log_operation(request, action="remove_runtime_module", status="succeeded", target=module_id, operation_id=operation_id)
        return ApiResponse(data=RuntimeModuleInstallData(module_id=module_id, worker_generation=generation))
    except ApiError as exc:
        error = exc
    except (OSError, TimeoutError):
        error = ApiError(503, "runtime_module_worker_refresh_unconfirmed", "Quick Worker 未能确认 Runtime 已移除。")
    except Exception:
        error = ApiError(500, "runtime_module_remove_failed", "Runtime 模块移除失败，当前模块已保持不变。")
    if removal is not None and registry_confirmed and state_cleanup_started:
        finalized = True
        try:
            manager.runtime_module_service.finalize_removal(removal)
        except Exception:
            finalized = False
            error = ApiError(
                503,
                "runtime_module_finalization_unconfirmed",
                "Runtime 模块已移除，但最终状态无法确认。",
            )
        if finalized and not state_cleanup_completed:
            error = ApiError(
                503,
                "runtime_module_state_cleanup_unconfirmed",
                "Runtime 模块已移除，但关联运行态清理未确认。",
            )
        log_operation(
            request,
            action="remove_runtime_module",
            status="failed",
            target=module_id,
            operation_id=operation_id,
            reason=error.code,
        )
        raise error

    recovered = True
    try:
        if removal is not None:
            manager.runtime_module_service.rollback_removal(removal)
            manager.refresh_external_runtime_modules()
        if reload_started:
            generation = await _read_idle_worker_generation(request)
            recovered = coordinator.begin(generation, request.client.host if request.client else "unknown") and await _wait_for_reload(coordinator)
        elif drained:
            resumed = await resume_after_drain(request.app.state.settings, operation_id=drain_operation_id)
            recovered = resumed.get("success") is True
    except Exception:
        recovered = False
    if not recovered:
        error = ApiError(503, "runtime_module_rollback_unconfirmed", "模块移除失败，Web 与 Quick Worker 的恢复状态无法确认。")
    log_operation(request, action="remove_runtime_module", status="failed", target=module_id, operation_id=operation_id, reason=error.code)
    raise error
