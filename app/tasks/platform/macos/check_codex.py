from __future__ import annotations

import shutil

from pydantic import BaseModel

from app.tasks.command import (
    CommandNotFound,
    CommandPermissionDenied,
    run_command,
)
from app.tasks.models import TaskContext, TaskFailed, TaskHandlerResult


def run(_params: BaseModel, context: TaskContext) -> TaskHandlerResult:
    executable = shutil.which("codex")
    if executable is None:
        raise TaskFailed("Codex is not installed or not available on PATH")
    try:
        command = run_command(
            [executable, "--version"],
            timeout=context.remaining_timeout(),
        )
    except CommandNotFound as exc:
        raise TaskFailed("Codex is not available") from exc
    except CommandPermissionDenied as exc:
        raise TaskFailed("Codex cannot be executed") from exc
    if command.returncode != 0:
        raise TaskFailed("Unable to read Codex version")

    version = command.stdout.splitlines()[0] if command.stdout else "unknown"
    return TaskHandlerResult(
        message="Codex is available",
        result={
            "available": True,
            "path": executable,
            "version": version,
        },
    )
