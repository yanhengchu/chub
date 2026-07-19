from __future__ import annotations

import platform

from pydantic import BaseModel

from app.tasks.models import TaskContext, TaskHandlerResult


def run(_params: BaseModel, context: TaskContext) -> TaskHandlerResult:
    return TaskHandlerResult(
        message="Version information collected",
        result={
            "hub_version": context.settings.app.version,
            "python_version": platform.python_version(),
            "node_id": context.settings.node.id,
            "platform": context.detected_platform,
        },
    )
