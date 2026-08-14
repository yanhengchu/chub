from fastapi import APIRouter, Request
from pydantic import BaseModel

from app.core.response import ApiResponse


router = APIRouter(prefix="/api", tags=["health"])


class HealthData(BaseModel):
    service: str
    status: str
    version: str
    instance_id: str
    quick_worker_ready: bool


@router.get("/health", response_model=ApiResponse[HealthData])
def health(request: Request) -> ApiResponse[HealthData]:
    settings = request.app.state.settings
    return ApiResponse(
        data=HealthData(
            service=settings.app.name.lower(),
            status="ok",
            version=settings.app.version,
            instance_id=request.app.state.instance_id,
            quick_worker_ready=request.app.state.quick_interactions.recovery_ready,
        )
    )
