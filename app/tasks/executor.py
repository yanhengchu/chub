from __future__ import annotations

import logging
import time

from pydantic import ValidationError

from app.core.config import Settings
from app.core.response import ApiError
from app.tasks.command import CommandTimedOut
from app.tasks.models import (
    TaskContext,
    TaskFailed,
    TaskRunResult,
    TaskStatus,
    TaskTimedOut,
)
from app.tasks.registry import TaskRegistry


class TaskExecutor:
    def __init__(
        self,
        registry: TaskRegistry,
        settings: Settings,
        detected_platform: str,
    ) -> None:
        self._registry = registry
        self._settings = settings
        self._detected_platform = detected_platform
        self._logger = logging.getLogger("hub.tasks")

    def run(self, name: str, params: dict[str, object]) -> TaskRunResult:
        task = self._registry.get(name)
        if task is None:
            raise ApiError(404, "task_not_found", "Task not found")
        if self._detected_platform not in task.platforms:
            raise ApiError(
                409,
                "task_not_supported",
                "Task is not supported on this platform",
            )
        try:
            validated_params = task.params_model.model_validate(params)
        except ValidationError as exc:
            raise ApiError(
                422,
                "invalid_task_parameters",
                "Task parameters are invalid",
            ) from exc

        started = time.monotonic()
        context = TaskContext(
            settings=self._settings,
            detected_platform=self._detected_platform,
            deadline=started + task.timeout_seconds,
        )
        try:
            output = task.handler(validated_params, context)
            elapsed = time.monotonic() - started
            if elapsed > task.timeout_seconds:
                raise TaskTimedOut
            status = TaskStatus.SUCCESS
            message = output.message
            result = output.result
        except TaskFailed as exc:
            status = TaskStatus.FAILED
            message = exc.message
            result = exc.result
        except (TaskTimedOut, CommandTimedOut):
            status = TaskStatus.TIMEOUT
            message = "Task execution timed out"
            result = None
        except Exception:
            self._logger.exception("task=%s unexpected_error", task.name)
            status = TaskStatus.FAILED
            message = "Task execution failed"
            result = None

        duration_ms = max(0, round((time.monotonic() - started) * 1000))
        self._logger.info(
            "task=%s status=%s duration_ms=%d",
            task.name,
            status.value,
            duration_ms,
        )
        return TaskRunResult(
            task=task.name,
            status=status,
            message=message,
            duration_ms=duration_ms,
            result=result,
        )
