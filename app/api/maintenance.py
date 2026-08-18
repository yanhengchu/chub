from __future__ import annotations

from fastapi import APIRouter, Depends, Request

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


router = APIRouter(
    prefix="/api/maintenance",
    tags=["maintenance"],
    dependencies=[Depends(require_token)],
)


@router.post("/restart", response_model=ApiResponse[dict[str, str]])
def restart_hub(request: Request) -> ApiResponse[dict[str, str]]:
    with request.app.state.maintenance_lock:
        if request.app.state.quick_worker_maintenance.in_progress():
            raise ApiError(
                409,
                "quick_worker_reload_in_progress",
                "Quick Worker 正在重启，请等待操作完成后再重启 Chub Web。",
            )
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
            deferred_restart = request.app.state.deferred_restart
            if (
                deferred_restart.pending()
                or deferred_restart.immediate_restart_in_progress()
            ):
                raise ApiError(
                    409,
                    "chub_restart_in_progress",
                    "Chub Web 已有重启请求，请等待操作完成后再重启 Quick Worker。",
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
