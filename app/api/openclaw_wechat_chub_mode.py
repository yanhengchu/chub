from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel

from app.core.response import ApiResponse
from app.core.security import require_tailscale


class WeixinChubModeStatusData(BaseModel):
    enabled: bool
    ready: bool
    code: Literal[
        "ready",
        "disabled",
        "configuration_invalid",
        "codex_unavailable",
    ]


router = APIRouter(
    prefix="/api/openclaw/wechat-chub-mode",
    tags=["openclaw-wechat-chub-mode"],
    dependencies=[Depends(require_tailscale)],
)


@router.get("/status", response_model=ApiResponse[WeixinChubModeStatusData])
def get_wechat_chub_mode_status(
    request: Request,
) -> ApiResponse[WeixinChubModeStatusData]:
    config = request.app.state.settings.openclaw.weixin_chub_mode
    if not config.enabled:
        return ApiResponse(
            data=WeixinChubModeStatusData(
                enabled=False,
                ready=False,
                code="disabled",
            )
        )

    manager = request.app.state.codex_pty_manager
    workspace = next(
        (item for item in manager.workspaces() if item.id == config.workspace_id),
        None,
    )
    if workspace is None or not workspace.available:
        return ApiResponse(
            data=WeixinChubModeStatusData(
                enabled=True,
                ready=False,
                code="configuration_invalid",
            )
        )
    if not manager.available():
        return ApiResponse(
            data=WeixinChubModeStatusData(
                enabled=True,
                ready=False,
                code="codex_unavailable",
            )
        )
    return ApiResponse(
        data=WeixinChubModeStatusData(
            enabled=True,
            ready=True,
            code="ready",
        )
    )
