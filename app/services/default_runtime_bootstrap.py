"""Build the only Runtime baseline required after a workstation rebuild."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

from app.ai_runtime.development_plugins import development_runtime_artifact_id
from app.ai_session.manager import AiSessionManager
from app.core.config import Settings, load_settings


def initialize_default_runtime(settings: Settings | None = None) -> str:
    """Import and enable the checked-out default Runtime in an empty Chub state.

    Rebuild never restores an earlier Runtime selection or optional plugin.  It
    only establishes the current development Runtime that Chub can submit to.
    """
    active_settings = settings or load_settings()
    manager = AiSessionManager(active_settings)
    implementation_id = manager.default_submission_implementation_id()
    if implementation_id not in manager.development_runtime_implementation_ids():
        raise OSError("默认 Runtime 不是当前代码提供的开发实现。")
    manager.update_runtime_implementation_enabled(implementation_id, True)
    artifact_id = development_runtime_artifact_id(implementation_id)
    _write_lifecycle_state(
        active_settings.business_modules.state_file,
        artifact_id,
    )
    return implementation_id


def _write_lifecycle_state(path: Path, artifact_id: str) -> None:
    state = {
        "imports": {"runtime": [artifact_id]},
        "enabled": {"runtime": [artifact_id]},
        "metadata": {},
    }
    try:
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        if path.parent.stat().st_mode & 0o077:
            os.chmod(path.parent, 0o700)
        descriptor, temporary = tempfile.mkstemp(prefix=".plugin-lifecycle-", dir=path.parent)
        try:
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(json.dumps(state, ensure_ascii=True, separators=(",", ":")).encode("utf-8"))
                handle.flush()
                os.fsync(handle.fileno())
            os.chmod(temporary, 0o600)
            os.replace(temporary, path)
        finally:
            try:
                os.unlink(temporary)
            except FileNotFoundError:
                pass
    except OSError as exc:
        raise OSError("默认 Runtime 生命周期状态不可写。") from exc
