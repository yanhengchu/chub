from __future__ import annotations

import subprocess

from fastapi import APIRouter, Depends, Request

from app.core.config import PROJECT_ROOT
from app.core.response import ApiResponse, error_response
from app.core.security import require_token
from app.services.operation_log import log_operation


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

    try:
        subprocess.Popen(
            [str(command)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    except OSError:
        log_operation(
            request,
            action="restart_hub",
            status="failed",
            target="chub",
            operation_id=operation_id,
        )
        return error_response(500, "restart_failed", "启动重启命令失败")

    log_operation(
        request,
        action="restart_hub",
        status="started",
        target="chub",
        operation_id=operation_id,
    )

    return ApiResponse(data={"status": "restarting"})
