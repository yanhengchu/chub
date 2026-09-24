from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict

from app.ai_search.models import AiSearchData
from app.core.response import ApiResponse
from app.core.security import require_trusted_network


router = APIRouter(prefix="/api/today-focus", tags=["today-focus"], dependencies=[Depends(require_trusted_network)])


@router.get("", response_model=ApiResponse[AiSearchData])
def current_today_focus(request: Request) -> ApiResponse[AiSearchData]:
    return ApiResponse(
        data=request.app.state.ai_search.current(
            request.app.state.ai_session_manager,
            request.app.state.quick_interactions,
        )
    )


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
