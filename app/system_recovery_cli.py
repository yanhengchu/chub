from __future__ import annotations

import argparse
import os
import shutil
import stat
import tempfile
from pathlib import Path

from app.core.business_modules import load_business_modules
from app.core.config import PROJECT_ROOT, Settings, load_settings
from app.quick_worker_tasks import worker_state_dir
from app.services.openclaw_weixin_chub_models import (
    WeixinChubModeRuntimeConfig,
    WeixinChubModeState,
)


def retired_ai_runtime_state_files(project_root: Path = PROJECT_ROOT) -> tuple[Path, ...]:
    return (
        project_root / "data/codex-sessions.json",
        project_root / "data/codex-quick-interactions.json",
    )


def retired_ai_runtime_directories(project_root: Path = PROJECT_ROOT) -> tuple[Path, ...]:
    return (
        project_root / "data/state/codex",
        project_root / "data/codex-quick-interactions",
        project_root / "data/runtime/codex/quick-interactions",
        project_root / "data/local/state/codex",
        project_root / "data/local/runtime/codex",
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


def _reset_weixin_chub_task_state(settings: Settings) -> None:
    """Keep Weixin mode configuration but discard task projections.

    A workstation rebuild intentionally removes the AI Session and Worker task
    authorities.  Keeping submissions, slots, retries, or orchestration records
    would leave Weixin referring to those removed authorities.
    """
    mode_config = settings.openclaw.weixin_chub_mode
    state_path = mode_config.state_file
    try:
        metadata = state_path.lstat()
    except FileNotFoundError:
        metadata = None
    if metadata is not None and (
        not stat.S_ISREG(metadata.st_mode)
        or stat.S_ISLNK(metadata.st_mode)
        or metadata.st_uid != os.getuid()
        or stat.S_IMODE(metadata.st_mode) & 0o077
    ):
        raise OSError(f"Unsafe Chub recovery state file: {state_path.name}")
    configuration = WeixinChubModeRuntimeConfig(
        enabled=mode_config.enabled,
        workspace_id=mode_config.workspace_id,
    )
    if metadata is not None:
        try:
            configuration = WeixinChubModeState.model_validate_json(
                state_path.read_bytes()
            ).configuration
        except Exception:
            # The configured mode is the safe fallback when only an obsolete
            # task projection remains unreadable.
            pass
    state = WeixinChubModeState(
        configuration=configuration
    )
    state_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    if state_path.parent.stat().st_mode & 0o077:
        os.chmod(state_path.parent, 0o700)
    descriptor, temporary = tempfile.mkstemp(
        prefix=f".{state_path.name}.",
        dir=state_path.parent,
    )
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(state.model_dump_json().encode("utf-8"))
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, 0o600)
        os.replace(temporary, state_path)
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass


def force_reset_runtime_state() -> None:
    """Discard Chub-owned rebuildable state after services stop.

    User configuration, shared documents, logs, OpenClaw state, native
    Runtime data, and browser Profiles are intentionally outside this reset.
    """
    settings = load_settings()
    business_modules = load_business_modules(settings)
    state_dir = settings.ai_runtime.shared.state_dir
    plugin_lifecycle_state = settings.business_modules.state_file

    # These paths contain only Chub-owned state or generated outputs. Keep
    # configuration, shared business data, logs, and external Runtime data.
    for path in (
        state_dir,
        settings.ai_runtime.shared.runtime_dir,
        settings.automations.state_dir,
        settings.automations.artifacts_dir,
        settings.ai_runtime.modules.install_dir,
        settings.business_modules.install_dir,
        *(
            path
            for module in business_modules
            if module.recovery_state_paths
            for path in module.recovery_state_paths(settings)
        ),
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
    _reset_weixin_chub_task_state(settings)


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m app.system_recovery_cli")
    parser.add_argument("command", choices=("force-reset",))
    args = parser.parse_args()
    if args.command == "force-reset":
        force_reset_runtime_state()


if __name__ == "__main__":
    main()
