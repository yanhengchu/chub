from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict

from app.ai_search.models import AiPageOpenResult, AiSearchData, AiSearchSettings
from app.core.response import ApiResponse
from app.core.security import require_trusted_network


router = APIRouter(prefix="/api/today-focus", tags=["today-focus"], dependencies=[Depends(require_trusted_network)])


class AiSearchSettingsUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    show_sessions: bool


@router.get("", response_model=ApiResponse[AiSearchData])
def current_today_focus(request: Request) -> ApiResponse[AiSearchData]:
    return ApiResponse(
        data=request.app.state.ai_search.current(
            request.app.state.ai_session_manager,
            request.app.state.quick_interactions,
        )
    )


@router.get("/settings", response_model=ApiResponse[AiSearchSettings])
def get_today_focus_settings(request: Request) -> ApiResponse[AiSearchSettings]:
    return ApiResponse(data=AiSearchSettings(show_sessions=request.app.state.ai_search.show_sessions()))


@router.put("/settings", response_model=ApiResponse[AiSearchSettings])
def update_today_focus_settings(
    payload: AiSearchSettingsUpdate,
    request: Request,
) -> ApiResponse[AiSearchSettings]:
    return ApiResponse(
        data=AiSearchSettings(
            show_sessions=request.app.state.ai_search.set_show_sessions(payload.show_sessions)
        )
    )


@router.post("/open-pages", response_model=ApiResponse[AiPageOpenResult])
def open_today_focus_pages(request: Request) -> ApiResponse[AiPageOpenResult]:
    return ApiResponse(data=request.app.state.ai_search.open_pages())


@router.post("/refresh", response_model=ApiResponse[AiSearchData])
def refresh_today_focus(request: Request) -> ApiResponse[AiSearchData]:
    source_ip = request.client.host if request.client else "unknown"
    return ApiResponse(
        data=request.app.state.ai_search.refresh(
            request.app.state.ai_session_manager,
            request.app.state.quick_interactions,
            source_ip=source_ip,
        )
    )
