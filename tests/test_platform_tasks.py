from __future__ import annotations

import time
from unittest.mock import patch

from app.tasks.command import CommandResult
from app.tasks.models import EmptyTaskParams, TaskContext, TaskFailed
from app.tasks.platform.macos import check_codex
from app.tasks.platform.ubuntu import check_docker


def context(settings, platform: str) -> TaskContext:
    return TaskContext(
        settings=settings,
        detected_platform=platform,
        deadline=time.monotonic() + 30,
    )


def test_check_codex_uses_fixed_version_command(settings) -> None:
    with (
        patch(
            "app.tasks.platform.macos.check_codex.shutil.which",
            return_value="/usr/local/bin/codex",
        ),
        patch(
            "app.tasks.platform.macos.check_codex.run_command",
            return_value=CommandResult(0, "codex 1.2.3", ""),
        ) as run,
    ):
        result = check_codex.run(EmptyTaskParams(), context(settings, "macos"))

    assert run.call_args.args[0] == ["/usr/local/bin/codex", "--version"]
    assert result.result == {
        "available": True,
        "path": "/usr/local/bin/codex",
        "version": "codex 1.2.3",
    }


def test_check_codex_reports_missing_program_as_task_failure(settings) -> None:
    with patch(
        "app.tasks.platform.macos.check_codex.shutil.which",
        return_value=None,
    ):
        try:
            check_codex.run(EmptyTaskParams(), context(settings, "macos"))
        except TaskFailed as exc:
            assert "not installed" in exc.message
        else:
            raise AssertionError("Expected TaskFailed")


def test_check_docker_uses_only_fixed_read_commands(settings) -> None:
    results = [
        CommandResult(0, "Docker version 28.0.0", ""),
        CommandResult(0, "2.35.0", ""),
        CommandResult(0, "28.0.0|4|2|0|2", ""),
    ]
    with (
        patch(
            "app.tasks.platform.ubuntu.check_docker.shutil.which",
            return_value="/usr/bin/docker",
        ),
        patch(
            "app.tasks.platform.ubuntu.check_docker.run_command",
            side_effect=results,
        ) as run,
    ):
        result = check_docker.run(
            EmptyTaskParams(),
            context(settings, "ubuntu"),
        )

    commands = [call.args[0] for call in run.call_args_list]
    assert commands == [
        ["/usr/bin/docker", "--version"],
        ["/usr/bin/docker", "compose", "version", "--short"],
        [
            "/usr/bin/docker",
            "info",
            "--format",
            (
                "{{.ServerVersion}}|{{.Containers}}|{{.ContainersRunning}}|"
                "{{.ContainersPaused}}|{{.ContainersStopped}}"
            ),
        ],
    ]
    assert result.result is not None
    assert result.result["containers"] == {
        "total": 4,
        "running": 2,
        "paused": 0,
        "stopped": 2,
    }


def test_check_docker_returns_partial_failure_when_service_is_down(settings) -> None:
    results = [
        CommandResult(0, "Docker version 28.0.0", ""),
        CommandResult(1, "", "compose unavailable"),
        CommandResult(1, "", "daemon unavailable"),
    ]
    with (
        patch(
            "app.tasks.platform.ubuntu.check_docker.shutil.which",
            return_value="/usr/bin/docker",
        ),
        patch(
            "app.tasks.platform.ubuntu.check_docker.run_command",
            side_effect=results,
        ),
    ):
        try:
            check_docker.run(EmptyTaskParams(), context(settings, "ubuntu"))
        except TaskFailed as exc:
            assert exc.result is not None
            assert exc.result["server_available"] is False
            assert exc.result["compose_available"] is False
        else:
            raise AssertionError("Expected TaskFailed")
