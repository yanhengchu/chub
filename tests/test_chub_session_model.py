from __future__ import annotations

import json
from pathlib import Path
from uuid import uuid4

from app.ai_session.manager import AiSessionManager
from app.ai_session.models import AiSession
from app.ai_session.store import AiSessionStore


def test_prompt_optimization_session_restores_pinned_defaults_and_read_only_permission(settings):
    from unittest.mock import MagicMock
    from app.ai_interactions.prompt_optimization import PromptOptimizationUseCase

    settings.ai_runtime.shared.workspace.mkdir(parents=True, exist_ok=True)
    manager = AiSessionManager(settings)
    manager.select_new_session_runtime = MagicMock(return_value=("codex", "codex-runtime-dev"))
    manager.validate_model = MagicMock()
    use_case = PromptOptimizationUseCase(manager, MagicMock())
    snapshot = use_case.execution_snapshot()
    manager.runtime_settings_store.read_general = MagicMock(side_effect=AssertionError("must use accepted snapshot"))
    first = use_case.restore_session("stage-call-1", snapshot)
    later_snapshot = {**snapshot, "model": "a-later-default"}
    restored = use_case.restore_session("stage-call-2", later_snapshot)
    assert first == restored
    session = manager.store.get(first)
    assert session.session_kind == "internal"
    assert session.title == "任务提示词优化"
    assert session.permission_mode == "read-only"
    assert session.implementation_id == snapshot["implementation_id"]
    assert session.model == snapshot["model"]
    assert session.reasoning_effort == snapshot["reasoning_effort"]


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
    assert payload["show_internal_sessions"] is False


def test_session_kind_is_owned_by_chub_and_persisted(tmp_path: Path) -> None:
    path = tmp_path / "ai-sessions.json"
    store = AiSessionStore(path)
    ordinary = AiSession(
        id=str(uuid4()),
        runtime_id="codex",
        implementation_id="codex-runtime-dev",
        workspace_id="chub",
        workspace_name="Chub",
        cwd=tmp_path,
        permission_mode="read-only",
    )
    internal = ordinary.model_copy(update={"id": str(uuid4()), "session_kind": "internal"})

    store.save(ordinary)
    store.save(internal)
    loaded = AiSessionStore(path)

    assert {session.id: session.session_kind for session in loaded.list()} == {
        ordinary.id: "user",
        internal.id: "internal",
    }


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


def test_session_manager_discards_legacy_development_implementation_snapshot(
    settings,
    tmp_path: Path,
) -> None:
    store = AiSessionStore(settings.ai_runtime.shared.state_dir / "ai-sessions.json")
    session = AiSession(
        id=str(uuid4()),
        runtime_id="codex",
        implementation_id="builtin-dev",
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

    assert manager.store.list() == []


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


def test_accepted_quick_task_can_bind_native_session_after_plugin_disable(settings, tmp_path: Path):
    from unittest.mock import MagicMock
    manager = AiSessionManager(settings)
    manager.validate_native_session_id = MagicMock()
    session = manager.create_session(
        "chub",
        session_kind="internal",
        internal_execution_snapshot={
            "runtime_id": "codex",
            "implementation_id": "codex-runtime-dev",
            "model": None,
            "reasoning_effort": None,
        },
        internal_title="任务提示词优化",
    )
    worker_task_id = f"qw-0000000000000-{'b' * 32}"
    execution_id = "c" * 32
    manager.register_quick_native_claim(session.id, worker_task_id)
    manager.set_runtime_plugin_lifecycle_state_reader(lambda _implementation_id: (True, False))

    manager.bind_quick_interaction_native_session(
        session.id,
        "11111111-1111-4111-8111-111111111111",
        worker_task_id=worker_task_id,
        execution_id=execution_id,
        implementation_id="codex-runtime-dev",
    )

    assert manager.store.get(session.id).native_session_id == "11111111-1111-4111-8111-111111111111"


def test_prompt_optimization_submits_internal_processing_task():
    from contextlib import nullcontext
    from unittest.mock import MagicMock

    from app.ai_interactions.prompt_optimization import PromptOptimizationUseCase

    quick_interactions = MagicMock()
    quick_interactions.find_for_operation.return_value = None
    quick_interactions.session_operation_guard.return_value = nullcontext()
    expected = object()
    quick_interactions.submit.return_value = expected

    use_case = PromptOptimizationUseCase(MagicMock(), quick_interactions)
    result = use_case.submit("internal-session", "optimize this task", "stage-call")

    assert result is expected
    assert quick_interactions.submit.call_args.kwargs == {
        "operation_id": "stage-call",
        "source_ip": "127.0.0.1",
        "suppress_completion_notification": True,
        "prompt_processing": True,
        "accepted_orchestration_task": True,
    }
