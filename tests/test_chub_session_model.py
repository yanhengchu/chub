from __future__ import annotations

import json
from pathlib import Path
from uuid import uuid4

from app.ai_session.manager import AiSessionManager
from app.ai_session.models import AiSession
from app.ai_session.store import AiSessionStore
from app.ai_runtime.codex_plugin import (
    DEVELOPMENT_CODEX_IMPLEMENTATION_ID,
    LEGACY_DEVELOPMENT_CODEX_IMPLEMENTATION_ID,
)


def test_legacy_v2_state_is_discarded_before_loading_current_schema(tmp_path: Path) -> None:
    path = tmp_path / "ai-sessions.json"
    base = {
        "runtime_id": "codex",
        "implementation_id": "codex-runtime-dev",
        "workspace_id": "chub",
        "workspace_name": "Chub",
        "cwd": str(tmp_path),
        "permission_mode": "read-only",
    }
    path.write_text(
        json.dumps(
            {
                "version": 2,
                "sessions": [{**base, "id": str(uuid4())}],
            }
        ),
        encoding="utf-8",
    )
    path.chmod(0o600)

    assert AiSessionStore.discard_legacy_session_state(path) is True
    store = AiSessionStore(path)
    assert store.list() == []


def test_current_store_persists_single_chub_session_schema(tmp_path: Path) -> None:
    store = AiSessionStore(tmp_path / "ai-sessions.json")
    created = AiSession(
        id=str(uuid4()),
        runtime_id="codex",
        implementation_id="codex-runtime-dev",
        workspace_id="chub",
        workspace_name="Chub",
        cwd=tmp_path,
        permission_mode="read-only",
    )
    store.save(created)

    payload = json.loads(store.path.read_text(encoding="utf-8"))
    assert payload["version"] == 3


def test_session_manager_registers_quick_native_claim_for_current_session_schema(
    settings,
    tmp_path: Path,
) -> None:
    manager = AiSessionManager(settings)
    session = AiSession(
        id=str(uuid4()),
        runtime_id="codex",
        implementation_id="codex-runtime-dev",
        workspace_id="chub",
        workspace_name="Chub",
        cwd=tmp_path,
        permission_mode="read-only",
    )
    manager.store.save(session)

    worker_task_id = f"qw-0000000000000-{'a' * 32}"
    manager.register_quick_native_claim(session.id, worker_task_id)

    assert manager.store.get(session.id).quick_native_claim_task_id == worker_task_id


def test_session_manager_preserves_legacy_development_implementation_snapshot(
    settings,
    tmp_path: Path,
) -> None:
    store = AiSessionStore(settings.ai_runtime.codex.data_file.with_name("ai-sessions.json"))
    session = AiSession(
        id=str(uuid4()),
        runtime_id="codex",
        implementation_id=LEGACY_DEVELOPMENT_CODEX_IMPLEMENTATION_ID,
        native_session_id="legacy-native-session",
        native_session_compatibility_id="codex-v1",
        workspace_id="chub",
        workspace_name="Chub",
        cwd=tmp_path,
        permission_mode="read-only",
        status="stopped",
        activity="idle",
    )
    store.save(session)

    manager = AiSessionManager(settings)

    preserved = manager.get_session(session.id, reconcile=False)
    assert preserved.implementation_id == LEGACY_DEVELOPMENT_CODEX_IMPLEMENTATION_ID
    assert manager.session_implementation_id(session.id) == LEGACY_DEVELOPMENT_CODEX_IMPLEMENTATION_ID
    assert preserved.native_session_id == "legacy-native-session"
    assert preserved.native_session_compatibility_id == "codex-v1"


def test_session_manager_discards_all_quick_native_claims(tmp_path: Path, settings) -> None:
    manager = AiSessionManager(settings)
    claimed = AiSession(
        id=str(uuid4()),
        runtime_id="codex",
        implementation_id="codex-runtime-dev",
        workspace_id="chub",
        workspace_name="Chub",
        cwd=tmp_path,
        permission_mode="read-only",
        quick_native_claim_task_id=f"qw-0000000000000-{'a' * 32}",
    )
    unclaimed = AiSession(
        id=str(uuid4()),
        runtime_id="codex",
        implementation_id="codex-runtime-dev",
        workspace_id="chub",
        workspace_name="Chub",
        cwd=tmp_path,
        permission_mode="read-only",
    )
    manager.store.save(claimed)
    manager.store.save(unclaimed)

    assert manager.discard_quick_native_claims() == 1
    assert manager.store.get(claimed.id).quick_native_claim_task_id is None
    assert manager.store.get(unclaimed.id).quick_native_claim_task_id is None


def test_legacy_state_is_discarded_without_parsing_obsolete_fields(
    tmp_path: Path,
) -> None:
    path = tmp_path / "ai-sessions.json"
    path.write_text(
        json.dumps(
            {
                "version": 2,
                "sessions": [
                    {
                        "id": str(uuid4()),
                        "runtime_id": "codex",
                        # Missing the required workspace fields.
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    path.chmod(0o600)

    assert AiSessionStore.discard_legacy_session_state(path) is True
    assert AiSessionStore(path).available
    assert AiSessionStore(path).list() == []
