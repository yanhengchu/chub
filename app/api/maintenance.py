from __future__ import annotations

from fastapi import APIRouter, Depends, Request

from app.core.config import PROJECT_ROOT
from app.core.response import ApiResponse, error_response
from app.core.security import require_token
from app.services.operation_log import log_operation, write_operation
from app.services.restart_command import (
    describe_restart_launch_error,
    launch_restart_process,
    monitor_restart_process,
)


router = APIRouter(
    prefix="/api/maintenance",
    tags=["maintenance"],
    dependencies=[Depends(require_token)],
)


@router.post("/restart", response_model=ApiResponse[dict[str, str]])
def restart_hub(request: Request) -> ApiResponse[dict[str, str]]:
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
