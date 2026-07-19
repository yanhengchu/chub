from __future__ import annotations

import pytest

from app.tasks.definitions import build_task_registry
from app.tasks.models import EmptyTaskParams, TaskDefinition
from app.tasks.registry import TaskRegistry


def test_registry_filters_tasks_by_platform() -> None:
    registry = build_task_registry(30)

    macos = [task.name for task in registry.list_for_platform("macos")]
    ubuntu = [task.name for task in registry.list_for_platform("ubuntu")]
    unknown = [task.name for task in registry.list_for_platform("unknown")]

    assert macos == ["check_codex", "check_system", "show_version"]
    assert ubuntu == ["check_docker", "check_system", "show_version"]
    assert unknown == ["check_system", "show_version"]


def test_registry_rejects_duplicate_task_names() -> None:
    registry = build_task_registry(30)
    task = registry.get("show_version")
    assert task is not None

    with pytest.raises(RuntimeError, match="Duplicate task name"):
        registry.register(task)


@pytest.mark.parametrize(
    ("name", "platforms", "timeout", "message"),
    [
        ("", frozenset({"macos"}), 30, "name"),
        ("task", frozenset(), 30, "platforms"),
        ("task", frozenset({"invalid"}), 30, "platforms"),
        ("task", frozenset({"macos"}), 0, "timeout"),
    ],
)
def test_registry_rejects_invalid_definitions(
    name: str,
    platforms: frozenset[str],
    timeout: int,
    message: str,
) -> None:
    registry = TaskRegistry()
    task = TaskDefinition(
        name=name,
        title="Task",
        description="Task",
        platforms=platforms,
        timeout_seconds=timeout,
        params_model=EmptyTaskParams,
        handler=lambda _params, _context: None,
    )

    with pytest.raises(RuntimeError, match=message):
        registry.register(task)
