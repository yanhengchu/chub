from __future__ import annotations

import shutil

from pydantic import BaseModel

from app.tasks.command import (
    CommandNotFound,
    CommandPermissionDenied,
    CommandResult,
    run_command,
)
from app.tasks.models import TaskContext, TaskFailed, TaskHandlerResult


def _run(command: list[str], context: TaskContext) -> CommandResult:
    try:
        return run_command(command, timeout=context.remaining_timeout())
    except CommandNotFound as exc:
        raise TaskFailed("Docker CLI is not available") from exc
    except CommandPermissionDenied as exc:
        raise TaskFailed("Docker CLI cannot be executed") from exc


def run(_params: BaseModel, context: TaskContext) -> TaskHandlerResult:
    executable = shutil.which("docker")
    if executable is None:
        raise TaskFailed("Docker CLI is not installed or not available on PATH")

    client = _run([executable, "--version"], context)
    if client.returncode != 0:
        raise TaskFailed("Unable to read Docker client version")
    client_version = client.stdout.splitlines()[0] if client.stdout else "unknown"

    compose = _run([executable, "compose", "version", "--short"], context)
    compose_available = compose.returncode == 0
    compose_version = (
        compose.stdout.splitlines()[0]
        if compose_available and compose.stdout
        else None
    )

    info = _run(
        [
            executable,
            "info",
            "--format",
            (
                "{{.ServerVersion}}|{{.Containers}}|{{.ContainersRunning}}|"
                "{{.ContainersPaused}}|{{.ContainersStopped}}"
            ),
        ],
        context,
    )
    partial_result = {
        "cli_available": True,
        "client_version": client_version,
        "compose_available": compose_available,
        "compose_version": compose_version,
        "server_available": False,
        "server_version": None,
        "containers": None,
    }
    if info.returncode != 0:
        raise TaskFailed(
            "Docker service is not available",
            result=partial_result,
        )

    fields = info.stdout.split("|")
    if len(fields) != 5:
        raise TaskFailed(
            "Docker returned an unexpected status",
            result=partial_result,
        )
    try:
        containers = {
            "total": int(fields[1]),
            "running": int(fields[2]),
            "paused": int(fields[3]),
            "stopped": int(fields[4]),
        }
    except ValueError as exc:
        raise TaskFailed(
            "Docker returned an unexpected status",
            result=partial_result,
        ) from exc

    return TaskHandlerResult(
        message="Docker is available",
        result={
            **partial_result,
            "server_available": True,
            "server_version": fields[0],
            "containers": containers,
        },
    )
