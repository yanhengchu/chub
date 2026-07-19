from __future__ import annotations

from app.tasks.models import ALL_PLATFORMS, TaskDefinition


class TaskRegistry:
    def __init__(self) -> None:
        self._tasks: dict[str, TaskDefinition] = {}

    def register(self, task: TaskDefinition) -> None:
        if not task.name.strip():
            raise RuntimeError("Task name must not be empty")
        if task.timeout_seconds < 1:
            raise RuntimeError(f"Task timeout must be positive: {task.name}")
        if not task.platforms or not task.platforms.issubset(ALL_PLATFORMS):
            raise RuntimeError(f"Task platforms are invalid: {task.name}")
        if task.name in self._tasks:
            raise RuntimeError(f"Duplicate task name: {task.name}")
        self._tasks[task.name] = task

    def get(self, name: str) -> TaskDefinition | None:
        return self._tasks.get(name)

    def list_for_platform(self, platform: str) -> list[TaskDefinition]:
        return sorted(
            (
                task
                for task in self._tasks.values()
                if platform in task.platforms
            ),
            key=lambda task: task.name,
        )
