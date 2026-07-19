from __future__ import annotations

from fastapi import APIRouter, Depends, Request

from app.core.response import ApiResponse
from app.core.security import require_token
from app.tasks.models import TaskListData, TaskRunRequest, TaskRunResult


router = APIRouter(
    prefix="/api/tasks",
    tags=["tasks"],
    dependencies=[Depends(require_token)],
)


@router.get("", response_model=ApiResponse[TaskListData])
def list_tasks(request: Request) -> ApiResponse[TaskListData]:
    registry = request.app.state.task_registry
    platform = request.app.state.detected_platform
    tasks = [
        task.public_info()
        for task in registry.list_for_platform(platform)
    ]
    return ApiResponse(data=TaskListData(tasks=tasks))


@router.post("/run", response_model=ApiResponse[TaskRunResult])
def run_task(
    payload: TaskRunRequest,
    request: Request,
) -> ApiResponse[TaskRunResult]:
    result = request.app.state.task_executor.run(
        payload.task,
        payload.params,
    )
    return ApiResponse(data=result)
