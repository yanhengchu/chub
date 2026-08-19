from fastapi import APIRouter, Request
from pydantic import BaseModel

from app.core.response import ApiResponse
from app.core.build_info import SESSION_SCHEMA_VERSION, WEB_CODE_VERSION


router = APIRouter(prefix="/api", tags=["health"])


class HealthData(BaseModel):
    service: str
    status: str
    version: str
    instance_id: str
    quick_worker_ready: bool
    code_version: str
    session_schema_version: int


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
            code_version=WEB_CODE_VERSION,
            session_schema_version=SESSION_SCHEMA_VERSION,
        )
    )
