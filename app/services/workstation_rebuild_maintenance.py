"""Fixed launcher for the executor that outlives Chub Web and Quick Worker."""

from __future__ import annotations

import os
import re
import stat
import subprocess
from pathlib import Path

from app.core.config import PROJECT_ROOT


class WorkstationRebuildUnavailableError(RuntimeError):
    pass


class WorkstationRebuildLaunchError(RuntimeError):
    pass


class WorkstationRebuildMaintenanceUseCase:
    @property
    def command(self) -> Path:
        return PROJECT_ROOT / "scripts" / "maintenance" / "chub-workstation-rebuild"

    def ensure_available(self) -> None:
        try:
            metadata = self.command.lstat()
        except OSError as exc:
            raise WorkstationRebuildUnavailableError("工作站重建执行器不可用。") from exc
        if (
            not stat.S_ISREG(metadata.st_mode)
            or stat.S_ISLNK(metadata.st_mode)
            or metadata.st_uid != os.getuid()
            or stat.S_IMODE(metadata.st_mode) & 0o022
            or not os.access(self.command, os.X_OK)
        ):
            raise WorkstationRebuildUnavailableError("工作站重建执行器的类型、所有者或权限不安全。")

    def start(self, operation_id: str) -> None:
        if re.fullmatch(r"[a-f0-9]{32}", operation_id) is None:
            raise ValueError("工作站重建操作 ID 无效。")
        self.ensure_available()
        try:
            subprocess.Popen(
                [str(self.command), operation_id],
                cwd=PROJECT_ROOT,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
        except OSError as exc:
            raise WorkstationRebuildLaunchError("工作站重建独立执行器未能启动。") from exc
