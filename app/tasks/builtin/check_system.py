from __future__ import annotations

from pydantic import BaseModel

from app.services.system_status import collect_system_status
from app.tasks.models import TaskContext, TaskHandlerResult


def run(_params: BaseModel, context: TaskContext) -> TaskHandlerResult:
    status = collect_system_status(
        context.settings,
        context.detected_platform,
    )
    return TaskHandlerResult(
        message="System status collected",
        result=status.system.model_dump(mode="json"),
    )
