"""Public in-process use case for the fixed Chub Web restart action."""

from __future__ import annotations

from pathlib import Path

from app.core.config import PROJECT_ROOT
from app.services.restart_command import (
    RestartProcess,
    describe_restart_launch_error,
    launch_restart_process,
)


class WebRestartUnavailableError(RuntimeError):
    """The fixed independent restart adapter cannot be used."""


class WebRestartLaunchError(RuntimeError):
    """The fixed restart adapter could not be started."""


class WebRestartUseCase:
    """Own the one fixed independent adapter used to replace Chub Web."""

    def __init__(self, *, project_root: Path | None = None) -> None:
        self._project_root = project_root or PROJECT_ROOT

    @property
    def command(self) -> Path:
        return self._project_root / "scripts" / "maintenance" / "chub-web-restart"

    def ensure_available(self) -> None:
        if not self.command.is_file():
            raise WebRestartUnavailableError("找不到 Chub 重启脚本")

    def launch(
        self,
        *,
        operation_id: str | None = None,
        source_ip: str | None = None,
    ) -> RestartProcess:
        self.ensure_available()
        environment: dict[str, str] = {}
        if operation_id is not None:
            environment["CHUB_OPERATION_ID"] = operation_id
        if source_ip is not None:
            environment["CHUB_OPERATION_SOURCE_IP"] = source_ip
        try:
            return launch_restart_process(
                self.command,
                environment=environment or None,
            )
        except OSError as exc:
            raise WebRestartLaunchError(describe_restart_launch_error(exc)) from exc
