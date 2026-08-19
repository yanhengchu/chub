from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict, Field

from app.core.config import PROJECT_ROOT
from app.core.response import ApiError, ApiResponse, error_response
from app.core.security import require_token
from app.services.operation_log import log_operation, write_operation
from app.services.restart_command import (
    describe_restart_launch_error,
    launch_restart_process,
    monitor_restart_process,
)
from app.services.quick_worker_maintenance import (
    QuickWorkerStatusData,
    inspect_quick_worker,
)
from app.quick_worker import PROTOCOL_VERSION, read_health
from app.services.system_upgrade import (
    SystemUpgradeStatusData,
    runtime_cleanup_readiness,
    runtime_recovery_plan,
)


router = APIRouter(
    prefix="/api/maintenance",
    tags=["maintenance"],
    dependencies=[Depends(require_token)],
)


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SystemUpgradeRequest(_StrictModel):
    fingerprint: str = Field(min_length=64, max_length=64, pattern=r"^[a-f0-9]{64}$")


def _system_upgrade_session_label(session: object) -> str:
    title = getattr(session, "title", None)
    workspace_name = getattr(session, "workspace_name", None)
    session_id = getattr(session, "id", "")
    name = title.strip() if isinstance(title, str) and title.strip() else "未命名 Session"
    workspace = workspace_name.strip() if isinstance(workspace_name, str) else ""
    suffix = session_id[:8] if isinstance(session_id, str) else ""
    return " · ".join(part for part in (name, workspace, suffix) if part)[:128]


async def _apply_system_upgrade_gate(
    request: Request,
    data: QuickWorkerStatusData,
) -> QuickWorkerStatusData:
    if request.app.state.system_upgrade.operation() is not None:
        return data
    try:
        loaded = request.app.state.system_upgrade.plan()
        worker = await read_health(request.app.state.settings)
    except OSError:
        return data
    worker_data = worker.get("data") if isinstance(worker, dict) else None
    if (
        loaded is not None
        and isinstance(worker_data, dict)
        and worker_data.get("protocol_version")
        in {
            loaded.plan.source_worker_protocol,
            loaded.plan.target_worker_protocol,
        }
        and PROTOCOL_VERSION == loaded.plan.target_worker_protocol
    ):
        data.can_restart = False
        data.upgrade_required = True
        if data.state == "ready":
            data.message = "服务正常，待继续系统升级。"
    return data


async def system_upgrade_status_data(application) -> SystemUpgradeStatusData:
    """Return the single authoritative upgrade readiness view for all entry points."""
    coordinator = application.state.system_upgrade
    loaded = None
    plan_error = None
    try:
        loaded = coordinator.plan()
    except OSError as exc:
        plan_error = str(exc)
    session_labels = []
    session_error = None
    try:
        current_sessions = application.state.codex_pty_manager.system_upgrade_sessions()
        session_labels = [
            _system_upgrade_session_label(session)
            for session in current_sessions
        ]
    except (OSError, ValueError) as exc:
        session_error = str(exc) or "Session 状态无法安全读取。"
    data = coordinator.status_data(
        loaded,
        session_count=len(session_labels),
        session_labels=session_labels,
        plan_error=plan_error,
    )
    effective_plan = loaded or runtime_recovery_plan()
    operation = coordinator.operation()
    if (
        operation is not None
        and operation.status == "failed"
        and operation.destructive_started
    ):
        return data
    if not data.can_start:
        return data
    restart_error = application.state.system_upgrade_restart_readiness()
    cleanup_error = runtime_cleanup_readiness(application.state.settings)
    if restart_error is not None or cleanup_error is not None:
        data.can_start = False
        data.message = restart_error or cleanup_error or "运行态恢复预检失败。"
        if data.state == "available":
            data.state = "blocked"
        return data
    if data.state != "available":
        return data
    if (
        application.state.quick_worker_maintenance.in_progress()
        or application.state.deferred_restart.pending()
        or application.state.deferred_restart.immediate_restart_in_progress()
    ):
        data.state = "blocked"
        data.can_start = False
        data.message = "已有维护或重启操作正在进行，系统升级暂不可用。"
        return data
    if session_error is None and not application.state.codex_pty_manager.available():
        data.state = "blocked"
        data.can_start = False
        data.message = "当前 Runtime 无法安全清理 Chub Session 关联。"
        return data
    try:
        worker = await read_health(application.state.settings)
    except OSError:
        worker = None
    worker_data = worker.get("data") if isinstance(worker, dict) else None
    if (
        not isinstance(worker_data, dict)
        or worker_data.get("status") != "ready"
        or worker_data.get("protocol_version")
        not in {
            effective_plan.plan.source_worker_protocol,
            effective_plan.plan.target_worker_protocol,
        }
        or "codex" not in worker_data.get("available_runtime_ids", [])
        or not application.state.quick_interactions.recovery_ready
    ):
        data.state = "blocked"
        data.can_start = False
        data.message = "Quick Worker 或 Web 恢复状态尚未满足升级前置条件。"
    elif PROTOCOL_VERSION != effective_plan.plan.target_worker_protocol:
        data.state = "blocked"
        data.can_start = False
        data.message = "当前 Chub Web 不是该升级方案的目标版本。"
    else:
        data.resume = True
    return data


async def start_system_upgrade_for_source(
    application,
    *,
    source_ip: str,
    fingerprint: str | None,
) -> SystemUpgradeStatusData:
    """Start the current fixed upgrade plan after the shared readiness checks."""
    coordinator = application.state.system_upgrade
    if coordinator.in_progress():
        return await system_upgrade_status_data(application)
    try:
        loaded = coordinator.plan()
    except OSError as exc:
        raise ApiError(409, "system_upgrade_plan_invalid", str(exc)) from exc
    loaded = loaded or runtime_recovery_plan()
    if fingerprint is not None and loaded.fingerprint != fingerprint:
        raise ApiError(
            409,
            "system_upgrade_plan_changed",
            "升级方案已经变化，请刷新状态后重新确认。",
        )
    operation = coordinator.operation()
    if (
        operation is not None
        and operation.status == "failed"
        and operation.destructive_started
    ):
        if operation.fingerprint != loaded.fingerprint:
            raise ApiError(
                409,
                "system_upgrade_plan_changed",
                "升级方案已经变化，不能继续已清理运行状态的升级。",
            )
        with application.state.maintenance_lock:
            resumed = coordinator.resume_failed(application.state.run_system_upgrade)
        if resumed is None:
            raise ApiError(
                409,
                "system_upgrade_recovery_unavailable",
                "当前失败阶段不能自动继续，请检查运行日志后处理。",
            )
        return await system_upgrade_status_data(application)
    status = await system_upgrade_status_data(application)
    if not status.can_start:
        raise ApiError(
            409,
            "system_upgrade_precondition_failed",
            status.message,
        )
    worker = await read_health(application.state.settings)
    worker_data = worker.get("data") if worker.get("success") is True else None
    generation = worker_data.get("generation") if isinstance(worker_data, dict) else None
    if (
        not isinstance(generation, str)
        or worker_data.get("protocol_version")
        not in {
            loaded.plan.source_worker_protocol,
            loaded.plan.target_worker_protocol,
        }
        or PROTOCOL_VERSION != loaded.plan.target_worker_protocol
    ):
        raise ApiError(
            409,
            "system_upgrade_worker_mismatch",
            "Quick Worker 协议或实例标识无法确认。",
        )
    with application.state.maintenance_lock:
        coordinator.begin(
            loaded,
            source_ip=source_ip,
            old_worker_generation=generation,
            old_worker_protocol=worker_data["protocol_version"],
            runner=application.state.run_system_upgrade,
        )
    return await system_upgrade_status_data(application)


def _require_runtime_maintenance_available(request: Request) -> None:
    coordinator = request.app.state.system_upgrade
    if coordinator.in_progress() or coordinator.writes_blocked():
        raise ApiError(
            409,
            "system_upgrade_in_progress",
            "系统升级与恢复正在处理 Chub Web 和 Quick Worker。",
        )


@router.get(
    "/system-upgrade",
    response_model=ApiResponse[SystemUpgradeStatusData],
)
async def system_upgrade_status(
    request: Request,
) -> ApiResponse[SystemUpgradeStatusData]:
    return ApiResponse(data=await system_upgrade_status_data(request.app))


@router.post(
    "/system-upgrade",
    response_model=ApiResponse[SystemUpgradeStatusData],
)
async def start_system_upgrade(
    payload: SystemUpgradeRequest,
    request: Request,
) -> ApiResponse[SystemUpgradeStatusData]:
    return ApiResponse(
        data=await start_system_upgrade_for_source(
            request.app,
            source_ip=request.client.host if request.client else "unknown",
            fingerprint=payload.fingerprint,
        )
    )


@router.post("/restart", response_model=ApiResponse[dict[str, str]])
def restart_hub(request: Request) -> ApiResponse[dict[str, str]]:
    with request.app.state.maintenance_lock:
        _require_runtime_maintenance_available(request)
        operation_id = log_operation(
            request,
            action="restart_hub",
            status="requested",
            target="chub",
        )
        command = PROJECT_ROOT / "scripts" / "chub-web-restart"
        if not command.is_file():
            log_operation(
                request,
                action="restart_hub",
                status="failed",
                target="chub",
                operation_id=operation_id,
            )
            return error_response(503, "command_not_found", "找不到 Chub 重启脚本")

        coordinator = request.app.state.deferred_restart
        restart_decision = coordinator.begin_immediate_restart()
        if restart_decision == "in_progress":
            log_operation(
                request,
                action="restart_hub",
                status="started",
                target="chub",
                operation_id=operation_id,
            )
            return ApiResponse(data={"status": "restarting"})

        try:
            process = launch_restart_process(command)
        except OSError as error:
            failure_reason = describe_restart_launch_error(error)
            coordinator.fail_immediate_restart(failure_reason)
            log_operation(
                request,
                action="restart_hub",
                status="failed",
                target="chub",
                operation_id=operation_id,
            )
            return error_response(500, "restart_failed", failure_reason)

        if restart_decision == "claimed":
            coordinator.confirm_immediate_restart()

    log_operation(
        request,
        action="restart_hub",
        status="started",
        target="chub",
        operation_id=operation_id,
    )

    source_ip = request.client.host if request.client else "unknown"

    def record_restart_failure(reason: str) -> None:
        coordinator.fail_immediate_restart(reason)
        write_operation(
            operation_id=operation_id,
            action="restart_hub",
            status="failed",
            target="chub",
            source_ip=source_ip,
        )

    monitor_restart_process(process, record_restart_failure)

    return ApiResponse(data={"status": "restarting"})


@router.get(
    "/quick-worker",
    response_model=ApiResponse[QuickWorkerStatusData],
)
async def quick_worker_status(
    request: Request,
) -> ApiResponse[QuickWorkerStatusData]:
    inspection = await inspect_quick_worker(
        request.app.state.settings,
        request.app.state.quick_interactions.recovery_ready,
        request.app.state.quick_worker_maintenance,
    )
    return ApiResponse(data=await _apply_system_upgrade_gate(request, inspection.data))


@router.post(
    "/quick-worker/restart",
    response_model=ApiResponse[QuickWorkerStatusData],
)
async def restart_quick_worker(
    request: Request,
) -> ApiResponse[QuickWorkerStatusData]:
    coordinator = request.app.state.quick_worker_maintenance
    if coordinator.in_progress():
        inspection = await inspect_quick_worker(
            request.app.state.settings,
            request.app.state.quick_interactions.recovery_ready,
            coordinator,
        )
        return ApiResponse(data=inspection.data)
    inspection = await inspect_quick_worker(
        request.app.state.settings,
        request.app.state.quick_interactions.recovery_ready,
        coordinator,
    )
    inspection.data = await _apply_system_upgrade_gate(request, inspection.data)
    with request.app.state.maintenance_lock:
        if not coordinator.in_progress():
            _require_runtime_maintenance_available(request)
            if inspection.data.upgrade_required:
                raise ApiError(
                    409,
                    "system_upgrade_required",
                    "Quick Worker 正在等待系统升级，请使用“继续升级”。",
                )
            if not inspection.data.can_restart or inspection.generation is None:
                raise ApiError(
                    409,
                    "quick_worker_not_restartable",
                    "Quick Worker 当前不可重启，请等待任务结束并确认服务恢复正常。",
                )
            source_ip = request.client.host if request.client else "unknown"
            coordinator.begin(inspection.generation, source_ip)
    refreshed = await inspect_quick_worker(
        request.app.state.settings,
        request.app.state.quick_interactions.recovery_ready,
        coordinator,
    )
    return ApiResponse(data=refreshed.data)
