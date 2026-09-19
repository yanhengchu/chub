from __future__ import annotations

import argparse
import os
import shutil
import stat
from pathlib import Path

from app.core.config import PROJECT_ROOT, load_settings
from app.quick_worker_tasks import worker_state_dir
from app.services.system_upgrade import (
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
    """Discard Chub-owned rebuildable state after services stop.

    User configuration, shared documents, logs, OpenClaw state, native
    Runtime data, and browser Profiles are intentionally outside this reset.
    """
    settings = load_settings()
    state_dir = settings.ai_runtime.shared.state_dir
    plugin_lifecycle_state = settings.business_modules.state_file.with_name(
        "plugin-lifecycle.json"
    )

    # These paths contain only Chub-owned state or generated outputs. Keep
    # configuration, shared business data, logs, and external Runtime data.
    for path in (
        state_dir,
        settings.ai_runtime.shared.runtime_dir,
        settings.automations.state_dir,
        settings.automations.artifacts_dir,
        settings.ai_runtime.modules.install_dir,
        settings.business_modules.install_dir,
        settings.business_modules.deliveryline_state_dir,
        settings.deployment_package.artifacts_dir,
        worker_state_dir(settings),
        *retired_ai_runtime_directories(PROJECT_ROOT),
    ):
        _remove_private_directory(path)

    for path in (
        settings.deployment_package.state_file,
        settings.business_modules.state_file,
        plugin_lifecycle_state,
        *retired_ai_runtime_state_files(PROJECT_ROOT),
    ):
        _remove_private_file(path)

    # Automation logs live beside locks and task state but remain useful for
    # post-recovery diagnosis, so only the rebuildable lock directory goes.
    _remove_private_directory(settings.automations.runtime_dir / "locks")


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m app.system_recovery_cli")
    parser.add_argument("command", choices=("force-reset",))
    args = parser.parse_args()
    if args.command == "force-reset":
        force_reset_runtime_state()


if __name__ == "__main__":
    main()
