from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import stat
import time

from app.core.config import load_settings
from app.services.system_upgrade import MAX_STATE_BYTES, SystemUpgradeOperation
from app.quick_worker_tasks import (
    worker_leases_dir,
    worker_tasks_dir,
    worker_tombstones_dir,
)


def _remove_private_directory(path) -> None:
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        return
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or metadata.st_uid != os.getuid()
        or stat.S_IMODE(metadata.st_mode) & 0o077
    ):
        raise OSError(f"Unsafe Quick Worker upgrade path: {path.name}")
    shutil.rmtree(path)


def _remove_private_file(path) -> None:
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
        raise OSError(f"Unsafe Chub runtime state path: {path.name}")
    path.unlink()


def _read_operation(operation_id: str) -> SystemUpgradeOperation:
    if re.fullmatch(r"[a-f0-9]{32}", operation_id) is None:
        raise OSError("Invalid system upgrade operation ID")
    settings = load_settings()
    state_path = settings.codex_pty.data_file.with_name("system-upgrade.json")
    metadata = state_path.lstat()
    if (
        not stat.S_ISREG(metadata.st_mode)
        or stat.S_ISLNK(metadata.st_mode)
        or metadata.st_uid != os.getuid()
        or stat.S_IMODE(metadata.st_mode) & 0o077
        or metadata.st_size > MAX_STATE_BYTES
    ):
        raise OSError("Unsafe system upgrade state")
    content = state_path.read_bytes()
    if len(content) > MAX_STATE_BYTES:
        raise OSError("System upgrade state exceeds its fixed limit")
    state = SystemUpgradeOperation.model_validate(json.loads(content))
    if state.operation_id != operation_id:
        raise OSError("System upgrade operation does not match")
    return state


def wait_for_restart_launch(
    operation_id: str,
    process_id: int,
    *,
    timeout: float = 10.0,
) -> None:
    if process_id <= 0:
        raise OSError("Invalid system upgrade restart process ID")
    deadline = time.monotonic() + timeout
    while True:
        state = _read_operation(operation_id)
        if state.status == "failed":
            raise OSError("System upgrade restart launch was cancelled")
        if (
            state.status == "started"
            and state.stage == "restarting_services"
            and state.restart_launch_state == "launched"
            and state.restart_process_id == process_id
            and state.destructive_started
        ):
            return
        if time.monotonic() >= deadline:
            raise TimeoutError("System upgrade restart launch was not confirmed")
        time.sleep(0.1)


def prepare_restart(operation_id: str) -> None:
    state = _read_operation(operation_id)
    if (
        state.status != "started"
        or state.stage != "restarting_services"
        or state.restart_launch_state != "launched"
        or state.restart_process_id is None
        or not state.destructive_started
    ):
        raise OSError("System upgrade is not ready to restart services")
    settings = load_settings()
    protocols = {
        state.plan.source_worker_protocol,
        state.old_worker_protocol or state.plan.source_worker_protocol,
    }
    for protocol_version in protocols:
        for path in (
            worker_tasks_dir(settings, protocol_version),
            worker_tombstones_dir(settings, protocol_version),
            worker_leases_dir(settings, protocol_version),
        ):
            _remove_private_directory(path)
    # This is the fixed, post-Worker-stop cleanup boundary. It intentionally
    # removes only Chub's local Session mappings and Hook results; native Codex
    # sessions, configuration, logs and other user data are outside this list.
    for path in (
        settings.codex_pty.data_file,
        settings.codex_pty.data_file.with_name("ai-sessions.json"),
    ):
        _remove_private_file(path)
    for path in (
        settings.codex_pty.runtime_dir / "hooks",
        settings.codex_pty.runtime_dir / "restart-requests",
    ):
        _remove_private_directory(path)


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m app.system_upgrade_cli")
    parser.add_argument("arguments", nargs="+")
    args = parser.parse_args()
    if args.arguments[0] == "wait-for-launch":
        if len(args.arguments) != 3 or not args.arguments[2].isdigit():
            parser.error("wait-for-launch requires operation_id and process_id")
        wait_for_restart_launch(
            args.arguments[1],
            int(args.arguments[2]),
        )
        return
    if len(args.arguments) != 1:
        parser.error("prepare requires one operation_id")
    prepare_restart(args.arguments[0])


if __name__ == "__main__":
    main()
