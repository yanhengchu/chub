from __future__ import annotations

import shutil
import subprocess

from fastapi import APIRouter, Depends

from app.core.response import ApiResponse, error_response
from app.core.security import require_token


router = APIRouter(
    prefix="/api/maintenance",
    tags=["maintenance"],
    dependencies=[Depends(require_token)],
)


@router.post("/restart", response_model=ApiResponse[dict[str, str]])
def restart_hub() -> ApiResponse[dict[str, str]]:
    command = shutil.which("chub")
    if command is None:
        return error_response(503, "command_not_found", "找不到 chub 命令，无法重启")

    try:
        subprocess.Popen(
            [command, "restart"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except OSError:
        return error_response(500, "restart_failed", "启动重启命令失败")

    return ApiResponse(data={"status": "restarting"})
