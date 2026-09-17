"""Public fixed use cases for the independent system-upgrade oneshot."""

from __future__ import annotations

import os
import re
import stat
import subprocess
from pathlib import Path

from app.core.config import PROJECT_ROOT
from app.services.system_upgrade import system_upgrade_restart_readiness


class SystemUpgradeMaintenanceUnavailableError(RuntimeError):
    """The fixed system-upgrade maintenance adapter is unavailable."""


class SystemUpgradeMaintenanceLaunchError(RuntimeError):
    """The fixed system-upgrade maintenance adapter could not be started."""


class SystemUpgradeMaintenanceUseCase:
    """Own the fixed independent processes used by system upgrade recovery."""

    def __init__(self, detected_platform: str) -> None:
        self._detected_platform = detected_platform

    @property
    def start_command(self) -> Path:
        return PROJECT_ROOT / "scripts" / "maintenance" / "chub-system-upgrade-start"

    @property
    def restart_command(self) -> Path:
        return (
            PROJECT_ROOT / "scripts" / "maintenance" / "chub-system-upgrade-restart"
        )

    def ensure_available(self) -> None:
        readiness = system_upgrade_restart_readiness(
            PROJECT_ROOT,
            self._detected_platform,
        )
        if readiness is not None:
            raise SystemUpgradeMaintenanceUnavailableError(readiness)

    def ensure_worker_recovery_available(self) -> None:
        try:
            metadata = self.restart_command.lstat()
        except OSError as exc:
            raise SystemUpgradeMaintenanceUnavailableError(
                "系统升级服务切换脚本不可用。"
            ) from exc
        if (
            not stat.S_ISREG(metadata.st_mode)
            or stat.S_ISLNK(metadata.st_mode)
            or metadata.st_uid != os.getuid()
            or stat.S_IMODE(metadata.st_mode) & 0o022
            or not os.access(self.restart_command, os.X_OK)
        ):
            raise SystemUpgradeMaintenanceUnavailableError(
                "系统升级服务切换脚本的类型、所有者或权限不安全。"
            )

    def start(self, operation_id: str) -> None:
        self._validate_operation_id(operation_id)
        self.ensure_available()
        try:
            result = subprocess.run(
                [str(self.start_command), operation_id],
                cwd=PROJECT_ROOT,
                capture_output=True,
                text=True,
                timeout=15,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise SystemUpgradeMaintenanceLaunchError(
                "系统升级独立服务未能启动。"
            ) from exc
        if result.returncode != 0:
            detail = (result.stderr or result.stdout).strip()
            raise SystemUpgradeMaintenanceLaunchError(
                detail[-500:] or "系统升级独立服务未能启动。"
            )

    def launch_worker_recovery(self, operation_id: str) -> subprocess.Popen:
        self._validate_operation_id(operation_id)
        self.ensure_worker_recovery_available()
        try:
            return subprocess.Popen(
                [str(self.restart_command), operation_id, "--recover-worker"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
        except OSError as exc:
            raise SystemUpgradeMaintenanceLaunchError(
                "Quick Worker 恢复程序未能启动。"
            ) from exc

    @staticmethod
    def _validate_operation_id(operation_id: str) -> None:
        if re.fullmatch(r"[a-f0-9]{32}", operation_id) is None:
            raise ValueError("系统升级操作 ID 无效。")
