from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict, Field

from app.core.config import PROJECT_ROOT
from app.core.response import ApiError, ApiResponse, error_response
from app.core.security import require_trusted_network
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
from app.services.system_upgrade import (
    SystemUpgradeStatusData,
    runtime_cleanup_readiness,
    runtime_recovery_plan,
)


router = APIRouter(
    prefix="/api/maintenance",
    tags=["maintenance"],
    dependencies=[Depends(require_trusted_network)],
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


async def system_upgrade_status_data(application) -> SystemUpgradeStatusData:
    """Return the single authoritative upgrade readiness view for all entry points."""
    coordinator = application.state.system_upgrade
    loaded = None
    recovery_fallback = False
    try:
        loaded = coordinator.plan()
    except OSError:
        # A broken or stale upgrade plan must not remove the fixed current-version
        # recovery path. The fallback never claims to perform a code upgrade.
        loaded = runtime_recovery_plan()
        recovery_fallback = True
    session_labels = []
    try:
        current_sessions = application.state.codex_pty_manager.system_upgrade_sessions()
        session_labels = [
            _system_upgrade_session_label(session)
            for session in current_sessions
        ]
    except (OSError, ValueError):
        # Session labels are only confirmation details. The fixed recovery flow
        # can clear an unreadable local mapping without using it as a start gate.
        pass
    data = coordinator.status_data(
        loaded,
        session_count=len(session_labels),
        session_labels=session_labels,
    )
    operation = coordinator.operation()
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
    if (
        data.state == "failed"
        and (
            application.state.quick_worker_maintenance.in_progress()
            or application.state.deferred_restart.pending()
            or application.state.deferred_restart.immediate_restart_in_progress()
        )
    ):
        data.can_start = False
        data.resume = False
        data.message = "已有维护或重启操作正在进行，系统升级暂不可用。"
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
    data.resume = True
    if recovery_fallback:
        data.message = "升级方案不可用，当前仅可执行运行态恢复。"
    return data


async def start_system_upgrade_for_source(
    application,
    *,
    source_ip: str,
    fingerprint: str | None,
) -> SystemUpgradeStatusData:
    """Start the current fixed upgrade plan after the shared readiness checks."""
    coordinator = application.state.system_upgrade
    operation = coordinator.operation()
    if coordinator.in_progress():
        return await system_upgrade_status_data(application)
    try:
        loaded = coordinator.plan()
    except OSError:
        # The fixed runtime reset is the recovery fallback when a prepared
        # upgrade plan cannot be read or validated.
        loaded = runtime_recovery_plan()
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
        with application.state.maintenance_lock:
            if operation.fingerprint != loaded.fingerprint:
                rebound = coordinator.rebase_failed_recovery(loaded)
                if not rebound:
                    raise ApiError(
                        409,
                        "system_upgrade_plan_changed",
                        "升级方案已经变化，不能继续已清理运行状态的升级。",
                    )
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
    with application.state.maintenance_lock:
        coordinator.begin(
            loaded,
            source_ip=source_ip,
            old_worker_generation=None,
            runner=application.state.run_system_upgrade,
        )
    return await system_upgrade_status_data(application)


def _require_runtime_maintenance_available(request: Request) -> None:
    coordinator = request.app.state.system_upgrade
    if coordinator.in_progress():
        raise ApiError(
            409,
            "system_upgrade_in_progress",
            "系统升级与恢复正在处理 Chub Web 和 Quick Worker，请等待当前操作结束。",
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
    return ApiResponse(data=inspection.data)


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
    with request.app.state.maintenance_lock:
        if not coordinator.in_progress():
            _require_runtime_maintenance_available(request)
            if not inspection.data.can_restart:
                raise ApiError(
                    409,
                    "quick_worker_not_restartable",
                    "Quick Worker 重启状态不可用，请稍后重试。",
                )
            source_ip = request.client.host if request.client else "unknown"
            coordinator.begin(
                inspection.generation,
                source_ip,
            )
    refreshed = await inspect_quick_worker(
        request.app.state.settings,
        request.app.state.quick_interactions.recovery_ready,
        coordinator,
    )
    return ApiResponse(data=refreshed.data)
