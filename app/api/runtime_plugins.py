from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict, Field

from app.ai_runtime.runtime_plugin_packages import RuntimePluginInstallError, is_runtime_plugin_id
from app.core.response import ApiError, ApiResponse
from app.core.security import require_trusted_network
from app.quick_worker import list_tasks, read_health, refresh_runtime_registry
from app.services.operation_log import log_operation


router = APIRouter(
    prefix="/api/runtime-modules",
    tags=["runtime-modules"],
    dependencies=[Depends(require_trusted_network)],
)


class RuntimePluginData(BaseModel):
    model_config = ConfigDict(extra="forbid")

    module_id: str = Field(min_length=1, max_length=32)
    version: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=100)
    description: str | None = Field(default=None, max_length=300)
    status: Literal["active", "unavailable"]
    reason: str | None = Field(default=None, max_length=300)
    removable: bool = True


class RuntimePluginListData(BaseModel):
    modules: list[RuntimePluginData]


class RuntimePluginInstallData(BaseModel):
    module_id: str = Field(min_length=1, max_length=32)
    worker_generation: str = Field(min_length=1, max_length=128)


class RuntimePluginPreviewData(BaseModel):
    module_id: str = Field(min_length=1, max_length=32)
    version: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=100)
    description: str = Field(min_length=1, max_length=300)


class DevelopmentRuntimePluginRefreshAvailabilityData(BaseModel):
    available: bool
    reason: str | None = Field(default=None, max_length=300)


_DEVELOPMENT_CODEX_IMPLEMENTATION_ID = "builtin-dev"


def _module_list(request: Request) -> RuntimePluginListData:
    manager = request.app.state.ai_session_manager
    active_ids = set(manager.runtime_plugins.implementation_ids())
    entries: list[RuntimePluginData] = []
    loaded, discovery_failures = manager.runtime_plugin_service.discover()
    failures = {item.module_id: item for item in discovery_failures}
    failures.update({item.module_id: item for item in manager.runtime_plugin_failures})
    for item in loaded:
        module_id = item.manifest.module_id
        failure = failures.get(module_id)
        entries.append(
            RuntimePluginData(
                module_id=module_id,
                version=item.manifest.version,
                name=item.manifest.display_name,
                description=item.manifest.description,
                status="active" if module_id in active_ids and failure is None else "unavailable",
                reason=None if failure is None else failure.reason,
                removable=module_id not in manager._development_runtime_plugins.implementation_ids(),
            )
        )
    known = {item.module_id for item in entries}
    entries.extend(
        RuntimePluginData(
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
    return RuntimePluginListData(modules=entries)


def _worker_health_data(payload: dict[str, object]) -> dict[str, object]:
    data = payload.get("data") if payload.get("success") is True else None
    if not isinstance(data, dict):
        raise ApiError(
            503,
            "runtime_plugin_worker_health_unavailable",
            "Quick Worker 健康状态不可确认，本次未安装 Runtime 插件。",
        )
    return data


async def _worker_generation(request: Request) -> str:
    try:
        data = _worker_health_data(await read_health(request.app.state.settings))
    except OSError as exc:
        raise ApiError(
            503,
            "runtime_plugin_worker_health_unavailable",
            "Quick Worker 健康状态不可确认，本次未维护 Runtime 版本。",
        ) from exc
    generation = data.get("generation")
    if (
        data.get("status") != "ready"
        or not isinstance(generation, str)
    ):
        raise ApiError(
            409,
            "runtime_plugin_worker_health_unavailable",
            "Quick Worker 未处于可维护状态，本次未维护 Runtime 版本。",
        )
    return generation


async def _require_implementation_idle(request: Request, implementation_id: str) -> None:
    try:
        payload = await list_tasks(request.app.state.settings, active_only=True)
    except OSError as exc:
        raise ApiError(503, "runtime_plugin_worker_health_unavailable", "Quick Worker 任务状态不可确认，本次未维护 Runtime 版本。") from exc
    data = payload.get("data") if payload.get("success") is True else None
    tasks = data.get("tasks") if isinstance(data, dict) else None
    if not isinstance(tasks, list):
        raise ApiError(503, "runtime_plugin_worker_health_unavailable", "Quick Worker 任务状态不可确认，本次未维护 Runtime 版本。")
    if any(item.get("implementation_id") == implementation_id for item in tasks if isinstance(item, dict)):
        raise ApiError(409, "runtime_implementation_busy", "该 Runtime 版本仍有已受理任务，请等待其结束后再维护源码。")


@router.get("/builtin-dev/refresh-availability", response_model=ApiResponse[DevelopmentRuntimePluginRefreshAvailabilityData])
async def read_development_runtime_plugin_refresh_availability(
    request: Request,
) -> ApiResponse[DevelopmentRuntimePluginRefreshAvailabilityData]:
    manager = request.app.state.ai_session_manager
    try:
        manager.require_runtime_enabled_for_maintenance("codex")
        await _worker_generation(request)
        await _require_implementation_idle(
            request,
            _DEVELOPMENT_CODEX_IMPLEMENTATION_ID,
        )
    except ApiError as exc:
        return ApiResponse(
            data=DevelopmentRuntimePluginRefreshAvailabilityData(
                available=False,
                reason=exc.message,
            )
        )
    return ApiResponse(data=DevelopmentRuntimePluginRefreshAvailabilityData(available=True))


async def _confirm_worker_runtime(
    request: Request, implementation_id: str, *, expected_present: bool
) -> str:
    try:
        data = _worker_health_data(await read_health(request.app.state.settings))
    except OSError as exc:
        raise ApiError(
            503,
            "runtime_plugin_worker_refresh_unconfirmed",
            "Quick Worker 未能确认新的 Runtime 注册表，插件未激活。",
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
            "runtime_plugin_worker_refresh_unconfirmed",
            "Quick Worker 未能确认新的 Runtime 注册表。",
        )
    return generation


def _require_builtin_refresh_worker_confirmation(payload: dict[str, object]) -> None:
    if payload.get("success") is True:
        return
    error = payload.get("error")
    code = error.get("code") if isinstance(error, dict) else None
    if code == "runtime_implementation_busy":
        raise ApiError(
            409,
            code,
            "开发版仍有运行中任务，请等待任务结束后再刷新。",
        )
    if code == "worker_draining":
        raise ApiError(409, code, "Quick Worker 正在维护中，请稍后再刷新开发版。")
    if code in {"worker_request_invalid", "worker_protocol_incompatible"}:
        raise ApiError(
            409,
            "quick_worker_refresh_upgrade_required",
            "Quick Worker 尚未加载开发版刷新能力；请等待当前任务结束后重载 Quick Worker，再重试开发版刷新。",
        )
    raise ApiError(
        503,
        "development_runtime_plugin_worker_refresh_unconfirmed",
        "Quick Worker 未能确认开发版 Runtime 刷新。",
    )


async def _read_archive(request: Request) -> tuple[str, bytes]:
    source_name = request.headers.get("X-Chub-Module-Filename", "runtime-module.zip")
    if len(source_name) > 255:
        raise ApiError(422, "runtime_plugin_filename_invalid", "插件文件名无效。")
    maximum = request.app.state.settings.ai_runtime.modules.max_archive_bytes
    chunks = bytearray()
    async for chunk in request.stream():
        chunks.extend(chunk)
        if len(chunks) > maximum:
            raise ApiError(413, "runtime_plugin_too_large", "插件包超过固定大小上限。")
    return source_name, bytes(chunks)


@router.get("", response_model=ApiResponse[RuntimePluginListData])
def list_runtime_plugins(request: Request) -> ApiResponse[RuntimePluginListData]:
    return ApiResponse(data=_module_list(request))


@router.post("/inspect", response_model=ApiResponse[RuntimePluginPreviewData])
async def inspect_runtime_plugin(request: Request) -> ApiResponse[RuntimePluginPreviewData]:
    source_name, archive = await _read_archive(request)
    try:
        preview = request.app.state.ai_session_manager.runtime_plugin_service.inspect_archive(
            archive,
            source_name=source_name,
        )
    except RuntimePluginInstallError as exc:
        raise ApiError(422, exc.code, exc.message) from exc
    return ApiResponse(data=RuntimePluginPreviewData(**preview.__dict__))


@router.post("/install", response_model=ApiResponse[RuntimePluginInstallData])
async def install_runtime_plugin(request: Request) -> ApiResponse[RuntimePluginInstallData]:
    source_name, archive = await _read_archive(request)
    manager = request.app.state.ai_session_manager
    operation_id = log_operation(request, action="install_runtime_plugin", status="requested", target=source_name)
    log_operation(request, action="install_runtime_plugin", status="started", target=source_name, operation_id=operation_id)
    activation = None
    implementation_id: str | None = None
    try:
        preview = manager.runtime_plugin_service.inspect_archive(archive, source_name=source_name)
        implementation_id = preview.implementation_id
        await _require_implementation_idle(request, implementation_id)
        await _worker_generation(request)
        activation = manager.install_runtime_plugin(archive, source_name=source_name, operation_id=operation_id)
        refreshed = await refresh_runtime_registry(
            request.app.state.settings,
            implementation_id=implementation_id,
            expected_present=True,
        )
        if refreshed.get("success") is not True:
            raise ApiError(503, "runtime_plugin_worker_refresh_unconfirmed", "Quick Worker 未能确认新的 Runtime 注册表，插件未激活。")
        generation = await _confirm_worker_runtime(request, implementation_id, expected_present=True)
        manager.runtime_plugin_service.finalize(activation)
        log_operation(request, action="install_runtime_plugin", status="succeeded", target=implementation_id, operation_id=operation_id)
        return ApiResponse(data=RuntimePluginInstallData(module_id=implementation_id, worker_generation=generation))
    except RuntimePluginInstallError as exc:
        error = ApiError(422, exc.code, exc.message)
    except ApiError as exc:
        error = exc
    except OSError:
        error = ApiError(503, "runtime_plugin_worker_refresh_unconfirmed", "Quick Worker 未能确认新的 Runtime 注册表，插件未激活。")
    except Exception:
        error = ApiError(500, "runtime_plugin_install_failed", "Runtime 插件安装失败，当前插件已保持不变。")
    rollback_confirmed = True
    if activation is not None and implementation_id is not None:
        try:
            manager.runtime_plugin_service.rollback(activation)
            manager.refresh_runtime_plugins()
            rollback = await refresh_runtime_registry(
                request.app.state.settings,
                implementation_id=implementation_id,
                expected_present=False,
            )
            rollback_confirmed = rollback.get("success") is True
        except Exception:
            rollback_confirmed = False
    if not rollback_confirmed:
        error = ApiError(503, "runtime_plugin_rollback_unconfirmed", "插件激活失败，Web 与 Quick Worker 的恢复状态无法确认。")
    log_operation(request, action="install_runtime_plugin", status="failed", target=implementation_id or source_name, operation_id=operation_id, reason=error.code)
    raise error


@router.post("/builtin-dev/refresh", response_model=ApiResponse[RuntimePluginInstallData])
async def refresh_development_codex_plugin(request: Request) -> ApiResponse[RuntimePluginInstallData]:
    """Explicitly reload the checked-out Codex source in Web and Quick Worker."""
    manager = request.app.state.ai_session_manager
    manager.require_runtime_enabled_for_maintenance("codex")
    operation_id = log_operation(
        request,
        action="refresh_development_runtime_plugin",
        status="requested",
        target=_DEVELOPMENT_CODEX_IMPLEMENTATION_ID,
    )
    log_operation(
        request,
        action="refresh_development_runtime_plugin",
        status="started",
        target=_DEVELOPMENT_CODEX_IMPLEMENTATION_ID,
        operation_id=operation_id,
    )
    previous_builtin = None
    worker_refresh_outcome_known = False
    worker_refresh_confirmed = False
    try:
        await _require_implementation_idle(request, _DEVELOPMENT_CODEX_IMPLEMENTATION_ID)
        await _worker_generation(request)
        previous_builtin = manager.refresh_development_codex_plugin()
        refreshed = await refresh_runtime_registry(
            request.app.state.settings,
            implementation_id=_DEVELOPMENT_CODEX_IMPLEMENTATION_ID,
            expected_present=True,
            reload_builtin_source=True,
        )
        worker_refresh_outcome_known = True
        worker_refresh_confirmed = refreshed.get("success") is True
        _require_builtin_refresh_worker_confirmation(refreshed)
        generation = await _confirm_worker_runtime(
            request,
            _DEVELOPMENT_CODEX_IMPLEMENTATION_ID,
            expected_present=True,
        )
        log_operation(
            request,
            action="refresh_development_runtime_plugin",
            status="succeeded",
            target=_DEVELOPMENT_CODEX_IMPLEMENTATION_ID,
            operation_id=operation_id,
        )
        return ApiResponse(
            data=RuntimePluginInstallData(
                module_id=_DEVELOPMENT_CODEX_IMPLEMENTATION_ID,
                worker_generation=generation,
            )
        )
    except ApiError as exc:
        error = exc
    except OSError:
        error = ApiError(503, "development_runtime_plugin_worker_refresh_unconfirmed", "Quick Worker 未能确认开发版 Runtime 刷新。")
    except Exception:
        error = ApiError(500, "development_runtime_plugin_refresh_failed", "开发版 Runtime 刷新失败，当前状态请以设置页和操作日志为准。")
    if (
        previous_builtin is not None
        and worker_refresh_outcome_known
        and not worker_refresh_confirmed
    ):
        try:
            manager.restore_development_codex_plugin(previous_builtin)
        except Exception:
            error = ApiError(
                503,
                "development_runtime_plugin_rollback_unconfirmed",
                "开发版刷新被 Quick Worker 拒绝，但 Web 注册表恢复状态无法确认。",
            )
    log_operation(
        request,
        action="refresh_development_runtime_plugin",
        status="failed",
        target=_DEVELOPMENT_CODEX_IMPLEMENTATION_ID,
        operation_id=operation_id,
        reason=error.code,
    )
    raise error


@router.delete("/{module_id}", response_model=ApiResponse[RuntimePluginInstallData])
async def remove_runtime_plugin(module_id: str, request: Request) -> ApiResponse[RuntimePluginInstallData]:
    if not is_runtime_plugin_id(module_id):
        raise ApiError(422, "runtime_plugin_id_invalid", "Runtime 插件标识无效。")
    manager = request.app.state.ai_session_manager
    operation_id = log_operation(request, action="remove_runtime_plugin", status="requested", target=module_id)
    log_operation(request, action="remove_runtime_plugin", status="started", target=module_id, operation_id=operation_id)
    removal = None
    try:
        manager.require_implementation_maintenance_available(module_id)
        await _require_implementation_idle(request, module_id)
        await _worker_generation(request)
        removal = manager.remove_runtime_plugin(module_id, operation_id=operation_id)
        refreshed = await refresh_runtime_registry(
            request.app.state.settings,
            implementation_id=module_id,
            expected_present=False,
        )
        if refreshed.get("success") is not True:
            raise ApiError(503, "runtime_plugin_worker_refresh_unconfirmed", "Quick Worker 未能确认 Runtime 插件已移除。")
        generation = await _confirm_worker_runtime(request, module_id, expected_present=False)
        manager.runtime_plugin_service.finalize_removal(removal)
        log_operation(request, action="remove_runtime_plugin", status="succeeded", target=module_id, operation_id=operation_id)
        return ApiResponse(data=RuntimePluginInstallData(module_id=module_id, worker_generation=generation))
    except ApiError as exc:
        error = exc
    except OSError:
        error = ApiError(503, "runtime_plugin_worker_refresh_unconfirmed", "Quick Worker 未能确认 Runtime 插件已移除。")
    except Exception:
        error = ApiError(500, "runtime_plugin_remove_failed", "Runtime 插件移除失败，当前插件已保持不变。")
    rollback_confirmed = True
    if removal is not None:
        try:
            manager.runtime_plugin_service.rollback_removal(removal)
            manager.refresh_runtime_plugins()
            rollback = await refresh_runtime_registry(
                request.app.state.settings,
                implementation_id=module_id,
                expected_present=True,
            )
            rollback_confirmed = rollback.get("success") is True
        except Exception:
            rollback_confirmed = False
    if not rollback_confirmed:
        error = ApiError(503, "runtime_plugin_rollback_unconfirmed", "插件移除失败，Web 与 Quick Worker 的恢复状态无法确认。")
    log_operation(request, action="remove_runtime_plugin", status="failed", target=module_id, operation_id=operation_id, reason=error.code)
    raise error
