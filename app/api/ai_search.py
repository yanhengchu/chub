from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict, Field

from app.ai_search.models import AiSearchData, AiSearchSettings, SearchRun
from app.core.response import ApiResponse
from app.core.security import require_trusted_network


router = APIRouter(prefix="/api/search", tags=["search"], dependencies=[Depends(require_trusted_network)])


class AiSearchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: str = Field(min_length=2, max_length=2000)


class AiSearchSettingsUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    show_sessions: bool


@router.get("/session", response_model=ApiResponse[AiSearchData])
def current_search(request: Request) -> ApiResponse[AiSearchData]:
    return ApiResponse(
        data=request.app.state.ai_search.current(
            request.app.state.ai_session_manager,
            request.app.state.quick_interactions,
        )
    )


@router.get("/settings", response_model=ApiResponse[AiSearchSettings])
def get_search_settings(request: Request) -> ApiResponse[AiSearchSettings]:
    return ApiResponse(data=AiSearchSettings(show_sessions=request.app.state.ai_search.show_sessions()))


@router.put("/settings", response_model=ApiResponse[AiSearchSettings])
def update_search_settings(
    payload: AiSearchSettingsUpdate,
    request: Request,
) -> ApiResponse[AiSearchSettings]:
    return ApiResponse(
        data=AiSearchSettings(
            show_sessions=request.app.state.ai_search.set_show_sessions(payload.show_sessions)
        )
    )


@router.get("/runs/{search_id}", response_model=ApiResponse[SearchRun])
def search_run(search_id: str, request: Request) -> ApiResponse[SearchRun]:
    return ApiResponse(
        data=request.app.state.ai_search.get(
            search_id,
            request.app.state.ai_session_manager,
            request.app.state.quick_interactions,
        )
    )


@router.post("/session", response_model=ApiResponse[AiSearchData])
def submit_search(payload: AiSearchRequest, request: Request) -> ApiResponse[AiSearchData]:
    source_ip = request.client.host if request.client else "unknown"
    return ApiResponse(
        data=request.app.state.ai_search.submit(
            payload.query,
            request.app.state.ai_session_manager,
            request.app.state.quick_interactions,
            source_ip=source_ip,
        )
    )
