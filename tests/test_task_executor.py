from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

import pytest

from app.core.response import ApiError
from app.tasks.definitions import build_task_registry
from app.tasks.executor import TaskExecutor
from app.tasks.command import CommandTimedOut
from app.tasks.models import (
    ALL_PLATFORMS,
    EmptyTaskParams,
    TaskDefinition,
    TaskStatus,
)
from app.tasks.registry import TaskRegistry


def test_executor_runs_common_task(settings) -> None:
    executor = TaskExecutor(build_task_registry(30), settings, "unknown")

    result = executor.run("show_version", {})

    assert result.status == TaskStatus.SUCCESS
    assert result.result is not None
    assert result.result["node_id"] == "test-node"


def test_executor_distinguishes_unknown_and_unsupported_tasks(settings) -> None:
    executor = TaskExecutor(build_task_registry(30), settings, "macos")

    with pytest.raises(ApiError) as unknown:
        executor.run("missing", {})
    assert unknown.value.status_code == 404
    assert unknown.value.code == "task_not_found"

    with pytest.raises(ApiError) as unsupported:
        executor.run("check_docker", {})
    assert unsupported.value.status_code == 409
    assert unsupported.value.code == "task_not_supported"


@pytest.mark.parametrize(
    ("platform", "task"),
    [
        ("unknown", "show_version"),
        ("unknown", "check_system"),
        ("macos", "check_codex"),
        ("ubuntu", "check_docker"),
    ],
)
def test_executor_rejects_extra_parameters_for_every_initial_task(
    settings,
    platform: str,
    task: str,
) -> None:
    executor = TaskExecutor(build_task_registry(30), settings, platform)

    with pytest.raises(ApiError) as invalid:
        executor.run(task, {"command": "anything"})

    assert invalid.value.status_code == 422
    assert invalid.value.code == "invalid_task_parameters"


def test_check_system_reuses_status_service(settings) -> None:
    executor = TaskExecutor(build_task_registry(30), settings, "unknown")

    with patch(
        "app.tasks.builtin.check_system.collect_system_status"
    ) as collect:
        collect.return_value.system.model_dump.return_value = {"cpu_percent": 10}
        result = executor.run("check_system", {})

    collect.assert_called_once_with(settings, "unknown")
    assert result.result == {"cpu_percent": 10}


def test_concurrent_independent_runs_do_not_share_mutable_results(settings) -> None:
    executor = TaskExecutor(build_task_registry(30), settings, "unknown")

    with ThreadPoolExecutor(max_workers=2) as pool:
        first_future = pool.submit(executor.run, "show_version", {})
        second_future = pool.submit(executor.run, "show_version", {})
        first = first_future.result()
        second = second_future.result()

    assert first is not second
    assert first.result is not second.result


def test_executor_returns_timeout_result(settings) -> None:
    registry = TaskRegistry()

    def timeout_handler(_params, _context):
        raise CommandTimedOut

    registry.register(
        TaskDefinition(
            name="timeout",
            title="Timeout",
            description="Test timeout",
            platforms=ALL_PLATFORMS,
            timeout_seconds=1,
            params_model=EmptyTaskParams,
            handler=timeout_handler,
        )
    )
    executor = TaskExecutor(registry, settings, "unknown")

    result = executor.run("timeout", {})

    assert result.status == TaskStatus.TIMEOUT
    assert result.result is None
