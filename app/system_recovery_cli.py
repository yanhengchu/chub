from __future__ import annotations

import argparse
import os
import shutil
import stat
from pathlib import Path

from app.core.config import PROJECT_ROOT, load_settings
from app.quick_worker_tasks import worker_state_dir
from app.services.system_upgrade import (
    component_report_path,
    retired_ai_runtime_directories,
    retired_ai_runtime_state_files,
)


def _remove_private_file(path: Path) -> None:
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        return
    if (
        not stat.S_ISREG(metadata.st_mode)
        or stat.S_ISLNK(metadata.st_mode)
        or metadata.st_uid != os.getuid()
        or stat.S_IMODE(metadata.st_mode) & 0o077
    ):
        raise OSError(f"Unsafe Chub recovery state file: {path.name}")
    path.unlink()


def _remove_private_directory(path: Path) -> None:
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        return
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or stat.S_ISLNK(metadata.st_mode)
        or metadata.st_uid != os.getuid()
        or stat.S_IMODE(metadata.st_mode) & 0o077
    ):
        raise OSError(f"Unsafe Chub recovery state directory: {path.name}")
    shutil.rmtree(path)


def force_reset_runtime_state() -> None:
    """Discard only fixed Chub-owned AI runtime state after services stop."""
    settings = load_settings()
    state_dir = settings.ai_runtime.shared.state_dir
    upgrade_state = state_dir / "system-upgrade.json"

    for path in (
        state_dir / "sessions.json",
        state_dir / "ai-sessions.json",
        state_dir / "deferred-restart.json",
        state_dir / "quick-interactions.json",
        state_dir / "quick-worker-maintenance.json",
        upgrade_state,
        component_report_path(upgrade_state),
        *retired_ai_runtime_state_files(PROJECT_ROOT),
    ):
        _remove_private_file(path)

    for path in (
        worker_state_dir(settings),
        *retired_ai_runtime_directories(PROJECT_ROOT),
    ):
        _remove_private_directory(path)


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m app.system_recovery_cli")
    parser.add_argument("command", choices=("force-reset",))
    args = parser.parse_args()
    if args.command == "force-reset":
        force_reset_runtime_state()


if __name__ == "__main__":
    main()
