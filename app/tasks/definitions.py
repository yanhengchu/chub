from __future__ import annotations

from app.tasks.builtin import check_system, show_version
from app.tasks.models import ALL_PLATFORMS, EmptyTaskParams, TaskDefinition
from app.tasks.platform.macos import check_codex
from app.tasks.platform.ubuntu import check_docker
from app.tasks.registry import TaskRegistry


def build_task_registry(timeout_seconds: int) -> TaskRegistry:
    registry = TaskRegistry()
    for task in (
        TaskDefinition(
            name="show_version",
            title="Show versions",
            description="Show Hub, Python, node, and platform versions.",
            platforms=ALL_PLATFORMS,
            timeout_seconds=timeout_seconds,
            params_model=EmptyTaskParams,
            handler=show_version.run,
        ),
        TaskDefinition(
            name="check_system",
            title="Check system",
            description="Check CPU, memory, disk, and system uptime.",
            platforms=ALL_PLATFORMS,
            timeout_seconds=timeout_seconds,
            params_model=EmptyTaskParams,
            handler=check_system.run,
        ),
        TaskDefinition(
            name="check_codex",
            title="Check Codex",
            description="Check whether Codex is available and report its version.",
            platforms=frozenset({"macos", "ubuntu"}),
            timeout_seconds=timeout_seconds,
            params_model=EmptyTaskParams,
            handler=check_codex.run,
        ),
        TaskDefinition(
            name="check_docker",
            title="Check Docker",
            description="Check Docker, Compose, and container status.",
            platforms=frozenset({"ubuntu"}),
            timeout_seconds=timeout_seconds,
            params_model=EmptyTaskParams,
            handler=check_docker.run,
        ),
    ):
        registry.register(task)
    return registry
