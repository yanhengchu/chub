from fastapi import APIRouter, Depends, Query, Request

from app.ai_usage.models import AiUsageData
from app.core.response import ApiResponse
from app.core.security import require_trusted_network


router = APIRouter(
    prefix="/api/ai",
    tags=["ai-usage"],
    dependencies=[Depends(require_trusted_network)],
)


@router.get("/usage", response_model=ApiResponse[AiUsageData])
def read_ai_usage(
    request: Request,
    refresh: bool = Query(default=False),
) -> ApiResponse[AiUsageData]:
    return ApiResponse(data=request.app.state.ai_usage.read(force=refresh))
