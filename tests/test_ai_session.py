from __future__ import annotations

import json
import subprocess
from datetime import timedelta
from pathlib import Path
from unittest.mock import MagicMock
from uuid import uuid4

import pytest

from app.ai_session.manager import AiSessionManager
from app.ai_session.models import AiSession, utc_now
from app.ai_session.session_defaults import SessionDefaults, SessionDefaultsStore
from app.ai_session.store import AiSessionStore, AiSessionStoreUnavailable
from app.ai_session.supervisor import InteractiveSupervisor
from app.ai_runtime import (
    RuntimeNativeSession,
    RuntimeModelCatalog,
    RuntimeModelInfo,
    RuntimeOperationError,
    RuntimeReasoningLevel,
    RuntimeSessionDiscoveryResult,
    RuntimeStatus,
)
from app.ai_runtime.enablement import RuntimeEnablement, RuntimeEnablementStore
from app.codex.models import SessionUsage, WorkspaceInfo
from app.core.config import PROJECT_ROOT, Settings
from app.core.response import ApiError


def session(
    tmp_path: Path,
    *,
    native_session_id: str | None = None,
    runtime_id: str = "codex",
    session_mode: str = "terminal",
) -> AiSession:
    return AiSession(
        id=str(uuid4()),
        runtime_id=runtime_id,
        session_mode=session_mode,
        workspace_id="chub",
        workspace_name="Chub",
        cwd=tmp_path,
        native_session_id=native_session_id,
    )


def test_ai_session_store_only_accepts_current_versioned_format(tmp_path: Path) -> None:
    path = tmp_path / "ai-sessions.json"
    store = AiSessionStore(path)
    created = session(tmp_path)

    store.save(created)

    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["version"] == 2
    assert payload["sessions"][0]["runtime_id"] == "codex"
    assert "ttyd_pid" not in payload["sessions"][0]
    assert path.stat().st_mode & 0o777 == 0o600
    assert AiSessionStore(path).get(created.id) == created


def test_session_defaults_store_is_atomic_and_private(tmp_path: Path) -> None:
    path = tmp_path / "session-defaults.json"
    store = SessionDefaultsStore(path)

    store.save(SessionDefaults(permission_mode="read-only"))

    assert SessionDefaultsStore(path).read().permission_mode == "read-only"
    assert path.stat().st_mode & 0o777 == 0o600


def test_runtime_enablement_store_is_atomic_and_private(tmp_path: Path) -> None:
    path = tmp_path / "runtime-enablement.json"
    store = RuntimeEnablementStore(path)

    store.save(RuntimeEnablement(disabled_runtime_ids=["codex"]))

    assert RuntimeEnablementStore(path).read().disabled_runtime_ids == ["codex"]
    assert path.stat().st_mode & 0o777 == 0o600


def test_runtime_enablement_blocks_new_submission_without_hiding_management(
    settings: Settings,
) -> None:
    manager = AiSessionManager(settings)
    manager._require_available = MagicMock()
    manager.runtime_adapter.status = MagicMock(
        return_value=RuntimeStatus(runtime_id="codex", available=True)
    )

    disabled = manager.update_runtime_enabled("codex", False)

    assert disabled.basic_mode is True
    assert disabled.runtimes[0].enabled is False
    with pytest.raises(ApiError) as rejected:
        manager.require_runtime_submission("codex")
    assert rejected.value.code == "ai_runtime_disabled"
    assert manager.submission_available()[0] is False

    enabled = manager.update_runtime_enabled("codex", True)

    assert enabled.basic_mode is False
    manager.require_runtime_submission("codex")


def test_ai_session_manager_uses_node_permission_default_for_new_sessions(
    settings: Settings,
) -> None:
    manager = AiSessionManager(settings)
    manager._require_available = MagicMock()
    manager.runtime_adapter.validate_model = MagicMock()
    manager.session_defaults.save(SessionDefaults(permission_mode="read-only"))

    created = manager.create_session("chub", session_mode="quick")

    assert created.permission_mode == "read-only"
    assert created.model is None
    assert created.reasoning_effort is None
    manager.runtime_adapter.validate_model.assert_called_once_with(
        None,
        None,
    )


def test_ai_session_manager_updates_idle_quick_session_model(
    settings: Settings,
) -> None:
    manager = AiSessionManager(settings)
    manager._require_available = MagicMock()
    manager.runtime_adapter.validate_model = MagicMock()
    created = session(settings.codex_pty.workspace, session_mode="quick")
    manager.store.save(created)

    updated = manager.update_quick_session_model(
        created.id,
        "gpt-5.6-terra",
        "high",
    )

    assert updated.model == "gpt-5.6-terra"
    assert updated.reasoning_effort == "high"
    persisted = manager.store.get(created.id)
    assert persisted is not None
    assert persisted.model == "gpt-5.6-terra"
    assert persisted.active_model is None
    manager.runtime_adapter.validate_model.assert_called_once_with(
        "gpt-5.6-terra",
        "high",
    )


def test_ai_session_manager_updates_quick_session_configuration_as_public_data(
    settings: Settings,
) -> None:
    manager = AiSessionManager(settings)
    manager._require_available = MagicMock()
    manager.runtime_adapter.validate_model = MagicMock()
    created = session(settings.codex_pty.workspace, session_mode="quick")
    manager.store.save(created)

    updated = manager.update_session_configuration(
        created.id,
        "full-access",
        "gpt-5.6-terra",
        "high",
    )

    assert updated.cwd == str(settings.codex_pty.workspace)
    assert updated.permission_pending is False
    assert updated.permission_mode == "full-access"
    assert updated.model == "gpt-5.6-terra"
    assert updated.reasoning_effort == "high"


def test_native_projection_keeps_saved_next_task_model(
    settings: Settings,
) -> None:
    logical = session(settings.codex_pty.workspace, session_mode="quick")
    logical.model = "next-model"
    logical.reasoning_effort = "high"
    logical.active_model = "previous-model"
    logical.active_reasoning_effort = "medium"
    native = RuntimeNativeSession(
        runtime_id="codex",
        native_session_id="native-1",
        cwd=settings.codex_pty.workspace,
        title=None,
        active_permission_mode="full-access",
        active_model="previous-model",
        active_reasoning_effort="medium",
        created_at=logical.created_at,
        updated_at=logical.updated_at,
    )

    changed = AiSessionManager._project_native_state(logical, native)

    assert changed is False
    assert logical.model == "next-model"
    assert logical.reasoning_effort == "high"
    assert logical.active_model == "previous-model"
    assert logical.active_reasoning_effort == "medium"


def test_ai_session_manager_exposes_node_defaults_in_model_catalog(
    settings: Settings,
) -> None:
    manager = AiSessionManager(settings)
    manager.runtime_adapter.read_model_catalog = MagicMock(
        return_value=RuntimeModelCatalog(
            models=(
                RuntimeModelInfo(
                    id="gpt-5.6-luna",
                    name="GPT-5.6-Luna",
                    description="Luna",
                    default_level="high",
                    levels=(
                        RuntimeReasoningLevel(id="high", description="Deep"),
                    ),
                ),
                RuntimeModelInfo(
                    id="gpt-5.6-terra",
                    name="GPT-5.6-Terra",
                    description="Terra",
                    default_level="medium",
                    levels=(
                        RuntimeReasoningLevel(id="medium", description="Balanced"),
                    ),
                ),
            ),
            default_model="gpt-5.6-luna",
            default_reasoning_effort="high",
        )
    )
    catalog = manager.read_model_catalog()

    assert catalog.default_model == "gpt-5.6-luna"
    assert catalog.default_reasoning_effort == "high"


def test_ai_session_store_fails_closed_for_legacy_or_unsafe_state(tmp_path: Path) -> None:
    path = tmp_path / "ai-sessions.json"
    path.write_text("[]", encoding="utf-8")
    path.chmod(0o600)

    store = AiSessionStore(path)

    assert store.available is False
    with pytest.raises(AiSessionStoreUnavailable, match="格式无效"):
        store.list()

    path.write_text('{"version":1,"sessions":[]}', encoding="utf-8")
    path.chmod(0o644)
    insecure = AiSessionStore(path)
    with pytest.raises(AiSessionStoreUnavailable, match="权限"):
        insecure.list()


def test_ai_session_manager_rejects_rename_for_external_writer(
    settings: Settings,
) -> None:
    manager = AiSessionManager(settings)
    created = session(
        settings.codex_pty.workspace,
        native_session_id="native-1",
        session_mode="quick",
    )
    manager.store.save(created)
    manager._sync_bound_native_sessions = MagicMock()
    manager.runtime_adapter.has_active_writer = MagicMock(return_value=True)
    manager.supervisor.owns_terminal_writer = MagicMock(return_value=False)

    with pytest.raises(ApiError) as error:
        manager.rename_session(created.id, "新标题")

    assert error.value.code == "codex_session_writer_active"
    assert manager.store.get(created.id).title is None


def test_terminal_activity_time_is_not_changed_by_session_rename(
    settings: Settings,
) -> None:
    manager = AiSessionManager(settings)
    created = session(settings.codex_pty.workspace, native_session_id="native-1")
    manager.store.save(created)
    manager._sync_bound_native_sessions = MagicMock()
    activity_at = utc_now()

    manager.set_activity(created.id, "working", "terminal", updated_at=activity_at)
    manager.rename_session(created.id, "新标题")

    stored = manager.store.get(created.id)
    assert stored is not None
    assert stored.last_activity_at == activity_at
    assert stored.updated_at >= activity_at


def test_ai_session_store_rejects_missing_runtime_owner(tmp_path: Path) -> None:
    path = tmp_path / "ai-sessions.json"
    payload = {
        "version": 1,
        "sessions": [session(tmp_path).model_dump(mode="json")],
    }
    payload["sessions"][0].pop("runtime_id")
    path.write_text(json.dumps(payload), encoding="utf-8")
    path.chmod(0o600)

    store = AiSessionStore(path)

    assert store.available is False
    with pytest.raises(AiSessionStoreUnavailable, match="格式无效"):
        store.list()


def test_ai_session_store_rejects_duplicate_native_mapping(tmp_path: Path) -> None:
    store = AiSessionStore(tmp_path / "ai-sessions.json")
    store.save(session(tmp_path, native_session_id="native-1"))

    with pytest.raises(AiSessionStoreUnavailable, match="映射"):
        store.save(session(tmp_path, native_session_id="native-1"))


def test_ai_session_store_scopes_native_mapping_by_runtime(tmp_path: Path) -> None:
    store = AiSessionStore(tmp_path / "ai-sessions.json")
    store.save(session(tmp_path, native_session_id="same-id", runtime_id="codex"))
    store.save(
        session(
            tmp_path,
            native_session_id="same-id",
            runtime_id="second-runtime",
        )
    )

    assert len(store.list()) == 2
    with pytest.raises(AiSessionStoreUnavailable, match="映射"):
        store.save(session(tmp_path, native_session_id="same-id", runtime_id="codex"))


def test_ai_session_store_detects_on_disk_tampering_after_startup(
    tmp_path: Path,
) -> None:
    path = tmp_path / "ai-sessions.json"
    store = AiSessionStore(path)
    store.save(session(tmp_path))
    path.write_text("not-json", encoding="utf-8")
    path.chmod(0o600)

    with pytest.raises(AiSessionStoreUnavailable, match="格式无效"):
        store.list()


def test_ai_session_manager_imports_discovered_unarchived_runtime_sessions(
    settings: Settings,
) -> None:
    manager = AiSessionManager(settings)
    native = RuntimeNativeSession(
        runtime_id="codex",
        native_session_id="native-1",
        cwd=settings.codex_pty.workspace,
        title="命令行 Session",
        active_permission_mode="full-access",
        active_model="gpt-test",
        active_reasoning_effort="high",
        created_at=session(settings.codex_pty.workspace).created_at,
        updated_at=session(settings.codex_pty.workspace).updated_at,
    )
    manager.runtime_adapter.discover_sessions = MagicMock(
        return_value=RuntimeSessionDiscoveryResult(
            sessions=(native,), archive_states={"native-1": False}
        )
    )
    manager._require_available = MagicMock()
    manager.workspaces = MagicMock(
        return_value=[
            WorkspaceInfo(
                id="chub",
                name="Chub",
                path=str(settings.codex_pty.workspace),
                available=True,
            )
        ]
    )
    manager.runtime_adapter.validate_model = MagicMock()

    created = manager.create_session("chub")
    sessions = manager.list_sessions()

    assert created.runtime_id == "codex"
    assert {item.title for item in sessions} == {created.title, "命令行 Session"}
    discovered = next(item for item in sessions if item.title == "命令行 Session")
    assert discovered.workspace_id == "workspace"
    assert discovered.status == "stopped"
    assert discovered.permission_mode == "full-access"
    assert "codex_session_id" not in sessions[0].model_dump()
    assert discovered.can_archive is True

    manager.list_sessions()
    assert len(manager.store.list()) == 2


def test_ai_session_manager_defers_discovery_while_quick_session_is_binding(
    settings: Settings,
) -> None:
    manager = AiSessionManager(settings)
    quick = session(settings.codex_pty.workspace, session_mode="quick")
    manager.store.save(quick)
    manager.set_quick_interaction_checker(lambda session_id: session_id == quick.id)
    native = RuntimeNativeSession(
        runtime_id="codex",
        native_session_id="native-pending",
        cwd=settings.codex_pty.workspace,
        title="Quick Worker 原生 Session",
        created_at=quick.created_at,
        updated_at=quick.updated_at,
    )
    manager.runtime_adapter.discover_sessions = MagicMock(
        return_value=RuntimeSessionDiscoveryResult(
            sessions=(native,), archive_states={"native-pending": False}
        )
    )

    manager.list_sessions()

    assert manager.store.get(quick.id).native_session_id is None
    assert len(manager.store.list()) == 1

    manager.set_quick_interaction_checker(lambda _session_id: False)
    manager.list_sessions()

    imported = [item for item in manager.store.list() if item.id != quick.id]
    assert len(imported) == 1
    assert imported[0].native_session_id == "native-pending"


def test_ai_session_manager_defers_discovery_while_terminal_is_binding(
    settings: Settings,
) -> None:
    manager = AiSessionManager(settings)
    terminal = session(settings.codex_pty.workspace)
    terminal.terminal_launch_id = "a" * 32
    terminal.status = "running"
    manager.store.save(terminal)
    manager.supervisor.owns_terminal_writer = MagicMock(
        side_effect=lambda session_id: session_id == terminal.id
    )
    native = RuntimeNativeSession(
        runtime_id="codex",
        native_session_id="terminal-pending",
        cwd=settings.codex_pty.workspace,
        title="Terminal 原生 Session",
        created_at=terminal.created_at,
        updated_at=terminal.updated_at,
    )
    manager.runtime_adapter.discover_sessions = MagicMock(
        return_value=RuntimeSessionDiscoveryResult(
            sessions=(native,), archive_states={"terminal-pending": False}
        )
    )

    manager.list_sessions()

    assert len(manager.store.list()) == 1

    manager.supervisor.owns_terminal_writer = MagicMock(return_value=False)
    manager.list_sessions()

    imported = [item for item in manager.store.list() if item.id != terminal.id]
    assert len(imported) == 1
    assert imported[0].native_session_id == "terminal-pending"


def test_ai_session_manager_publishes_unknown_native_after_claim_grace(
    settings: Settings,
) -> None:
    manager = AiSessionManager(settings)
    quick = session(settings.codex_pty.workspace, session_mode="quick")
    manager.store.save(quick)
    manager.set_quick_interaction_checker(lambda session_id: session_id == quick.id)
    native = RuntimeNativeSession(
        runtime_id="codex",
        native_session_id="native-after-grace",
        cwd=settings.codex_pty.workspace,
        title="外部原生 Session",
        created_at=quick.created_at,
        updated_at=quick.updated_at,
    )
    manager.runtime_adapter.discover_sessions = MagicMock(
        return_value=RuntimeSessionDiscoveryResult(
            sessions=(native,), archive_states={"native-after-grace": False}
        )
    )
    manager._pending_native_discoveries[("codex", "native-after-grace")] = (
        utc_now() - timedelta(seconds=6)
    )

    manager.list_sessions()

    imported = [item for item in manager.store.list() if item.id != quick.id]
    assert len(imported) == 1
    assert imported[0].native_session_id == "native-after-grace"


def test_ai_session_manager_does_not_discover_internal_translation_session(
    settings: Settings,
) -> None:
    manager = AiSessionManager(settings)
    manager._require_available = MagicMock()
    translation = manager.create_translation_session()
    native = RuntimeNativeSession(
        runtime_id="codex",
        native_session_id="translation-native",
        cwd=settings.codex_pty.runtime_dir / "translation-workspace",
        title="文本优化与翻译",
        active_permission_mode="read-only",
        created_at=translation.created_at,
        updated_at=translation.updated_at,
    )
    manager.runtime_adapter.discover_sessions = MagicMock(
        return_value=RuntimeSessionDiscoveryResult(
            sessions=(native,), archive_states={"translation-native": False}
        )
    )

    sessions = manager.list_sessions()

    assert [item.id for item in sessions] == [translation.id]
    assert manager.store.get(translation.id).native_session_id is None


def test_ai_session_manager_does_not_discover_legacy_translation_session(
    settings: Settings,
) -> None:
    manager = AiSessionManager(settings)
    manager._require_available = MagicMock()
    legacy_workspace = PROJECT_ROOT / "data/runtime/codex/translation-workspace"
    native = RuntimeNativeSession(
        runtime_id="codex",
        native_session_id="legacy-translation-native",
        cwd=legacy_workspace,
        title="You are a text editor and translator.",
        active_permission_mode="read-only",
        created_at=session(settings.codex_pty.workspace).created_at,
        updated_at=session(settings.codex_pty.workspace).updated_at,
    )
    manager.runtime_adapter.discover_sessions = MagicMock(
        return_value=RuntimeSessionDiscoveryResult(
            sessions=(native,), archive_states={"legacy-translation-native": False}
        )
    )

    assert manager.list_sessions() == []


def test_ai_session_manager_archives_idle_legacy_translation_session(
    settings: Settings,
) -> None:
    manager = AiSessionManager(settings)
    manager._require_available = MagicMock()
    legacy = session(
        PROJECT_ROOT / "data/runtime/codex/translation-workspace",
        native_session_id="legacy-translation-native",
    )
    legacy.discovered = True
    legacy.workspace_id = "runtime-session"
    legacy.workspace_name = "translation-workspace"
    legacy.title = "You are a text editor and translator.\n\nThe JSON"
    manager.store.save(legacy)
    manager.resolve_session_usage = MagicMock(
        return_value=SessionUsage(
            native_session_present=True,
            owner="none",
            phase="idle",
        )
    )
    manager.runtime_adapter.run_native_action = MagicMock()
    manager.stop_session = MagicMock()
    quick_interactions = MagicMock()
    quick_interactions.recovery_ready = True
    quick_interactions.is_running.return_value = False

    archived = manager.archive_legacy_translation_sessions(quick_interactions)

    assert archived == 1
    manager.runtime_adapter.run_native_action.assert_called_once_with(
        "archive", "legacy-translation-native"
    )
    assert manager.store.get(legacy.id) is None


def test_ai_session_manager_keeps_legacy_translation_session_when_usage_unknown(
    settings: Settings,
) -> None:
    manager = AiSessionManager(settings)
    manager._require_available = MagicMock()
    legacy = session(
        PROJECT_ROOT / "data/runtime/codex/translation-workspace",
        native_session_id="legacy-translation-native",
    )
    legacy.discovered = True
    legacy.workspace_id = "runtime-session"
    legacy.workspace_name = "translation-workspace"
    legacy.title = "You are a text editor and translator.\n\nThe JSON"
    manager.store.save(legacy)
    manager.resolve_session_usage = MagicMock(
        return_value=SessionUsage(
            native_session_present=True,
            owner="unknown",
            phase="unknown",
        )
    )
    manager.runtime_adapter.run_native_action = MagicMock()
    quick_interactions = MagicMock()
    quick_interactions.recovery_ready = True
    quick_interactions.is_running.return_value = False

    archived = manager.archive_legacy_translation_sessions(quick_interactions)

    assert archived == 0
    manager.runtime_adapter.run_native_action.assert_not_called()
    assert manager.store.get(legacy.id) is not None


def test_ai_session_manager_skips_legacy_cleanup_when_runtime_is_unavailable(
    settings: Settings,
) -> None:
    manager = AiSessionManager(settings)
    manager._require_available = MagicMock(
        side_effect=ApiError(503, "codex_pty_unavailable", "Codex 暂不可用。")
    )
    quick_interactions = MagicMock()
    quick_interactions.recovery_ready = True

    assert manager.archive_legacy_translation_sessions(quick_interactions) == 0


def test_ai_session_manager_cleans_stale_translation_discovery_before_binding(
    settings: Settings,
) -> None:
    manager = AiSessionManager(settings)
    manager._require_available = MagicMock()
    translation = manager.create_translation_session()
    quick = manager.store.get(translation.id)
    assert quick is not None
    stale = session(quick.cwd, native_session_id="translation-native-stale")
    stale.discovered = True
    stale.workspace_id = "weixin-translation"
    stale.workspace_name = "微信文本优化与翻译"
    manager.store.save(stale)
    manager.runtime_adapter.has_active_writer = MagicMock(return_value=False)
    manager.runtime_adapter.discover_sessions = MagicMock(
        return_value=RuntimeSessionDiscoveryResult(
            sessions=(
                RuntimeNativeSession(
                    runtime_id="codex",
                    native_session_id="translation-native-stale",
                    cwd=quick.cwd,
                    title="文本优化与翻译",
                    created_at=stale.created_at,
                    updated_at=stale.updated_at,
                ),
            ),
            archive_states={"translation-native-stale": False},
        )
    )

    manager.list_sessions()
    assert manager.store.get(stale.id) is None

    manager.bind_quick_interaction_native_session(
        translation.id,
        "translation-native-stale",
    )
    assert manager.store.get(translation.id).native_session_id == (
        "translation-native-stale"
    )


def test_ai_session_manager_repairs_discovery_duplicate_for_quick_binding(
    settings: Settings,
) -> None:
    manager = AiSessionManager(settings)
    quick = session(settings.codex_pty.workspace, session_mode="quick")
    discovered = session(
        settings.codex_pty.workspace,
        native_session_id="native-race",
    )
    discovered.discovered = True
    discovered.created_at = quick.created_at
    manager.store.save(quick)
    manager.store.save(discovered)
    manager.supervisor.owns_terminal_writer = MagicMock(return_value=False)

    manager.bind_quick_interaction_native_session(quick.id, "native-race")

    assert manager.store.get(quick.id).native_session_id == "native-race"
    assert manager.store.get(discovered.id) is None


def test_ai_session_manager_does_not_adopt_discovery_duplicate_with_native_writer(
    settings: Settings,
) -> None:
    manager = AiSessionManager(settings)
    quick = session(settings.codex_pty.workspace, session_mode="quick")
    discovered = session(
        settings.codex_pty.workspace,
        native_session_id="native-active",
    )
    discovered.discovered = True
    manager.store.save(quick)
    manager.store.save(discovered)
    manager.supervisor.owns_terminal_writer = MagicMock(return_value=False)
    manager.runtime_adapter.has_active_writer = MagicMock(return_value=True)

    with pytest.raises(ApiError) as error:
        manager.bind_quick_interaction_native_session(quick.id, "native-active")

    assert error.value.code == "quick_interaction_native_session_conflict"
    assert manager.store.get(quick.id).native_session_id is None
    assert manager.store.get(discovered.id) is not None


def test_ai_session_manager_adopts_discovery_duplicate_for_current_quick_writer(
    settings: Settings,
) -> None:
    manager = AiSessionManager(settings)
    quick = session(settings.codex_pty.workspace, session_mode="quick")
    discovered = session(
        settings.codex_pty.workspace,
        native_session_id="native-active",
    )
    discovered.discovered = True
    manager.store.save(quick)
    manager.store.save(discovered)
    manager.supervisor.owns_terminal_writer = MagicMock(return_value=False)
    manager.runtime_adapter.has_active_writer = MagicMock(return_value=True)
    manager.set_quick_interaction_checker(lambda session_id: session_id == quick.id)

    manager.bind_quick_interaction_native_session(quick.id, "native-active")

    assert manager.store.get(quick.id).native_session_id == "native-active"
    assert manager.store.get(discovered.id) is None


def test_ai_session_manager_does_not_take_over_active_discovered_terminal(
    settings: Settings,
) -> None:
    manager = AiSessionManager(settings)
    quick = session(settings.codex_pty.workspace, session_mode="quick")
    discovered = session(
        settings.codex_pty.workspace,
        native_session_id="native-active",
    )
    discovered.discovered = True
    manager.store.save(quick)
    manager.store.save(discovered)
    manager.supervisor.owns_terminal_writer = MagicMock(return_value=True)

    with pytest.raises(ApiError) as error:
        manager.bind_quick_interaction_native_session(quick.id, "native-active")

    assert error.value.code == "quick_interaction_native_session_conflict"
    assert manager.store.get(quick.id).native_session_id is None
    assert manager.store.get(discovered.id) is not None


def test_ai_session_manager_rejects_discovery_from_wrong_runtime(
    settings: Settings,
) -> None:
    manager = AiSessionManager(settings)
    native = RuntimeNativeSession(
        runtime_id="second-runtime",
        native_session_id="native-1",
        cwd=settings.codex_pty.workspace,
        title="错误归属",
        created_at=session(settings.codex_pty.workspace).created_at,
        updated_at=session(settings.codex_pty.workspace).updated_at,
    )
    manager.runtime_adapter.discover_sessions = MagicMock(
        return_value=RuntimeSessionDiscoveryResult(sessions=(native,))
    )

    with pytest.raises(ApiError) as error:
        manager.list_sessions()

    assert error.value.code == "runtime_session_identity_invalid"
    assert error.value.status_code == 409


def test_ai_session_manager_repairs_legacy_discovery_workspace_mapping(
    settings: Settings,
) -> None:
    manager = AiSessionManager(settings)
    legacy = session(settings.codex_pty.workspace, native_session_id="native-1")
    legacy.workspace_id = "codex"
    legacy.workspace_name = "workspace"
    manager.store.save(legacy)
    native = RuntimeNativeSession(
        runtime_id="codex",
        native_session_id="native-1",
        cwd=settings.codex_pty.workspace,
        title="已扫描 Session",
        active_permission_mode="full-access",
        created_at=legacy.created_at,
        updated_at=legacy.updated_at,
    )
    manager.runtime_adapter.discover_sessions = MagicMock(
        return_value=RuntimeSessionDiscoveryResult(
            sessions=(native,), archive_states={"native-1": False}
        )
    )

    discovered = manager.list_sessions()[0]

    assert discovered.workspace_id == "workspace"
    assert manager.store.get(legacy.id).discovered is True


def test_ai_session_manager_repairs_legacy_external_workspace_mapping(
    settings: Settings,
    tmp_path: Path,
) -> None:
    manager = AiSessionManager(settings)
    legacy = session(tmp_path, native_session_id="native-1")
    legacy.workspace_id = "codex"
    legacy.workspace_name = "external-project"
    manager.store.save(legacy)
    native = RuntimeNativeSession(
        runtime_id="codex",
        native_session_id="native-1",
        cwd=tmp_path / "external-project",
        title="已扫描外部 Session",
        active_permission_mode="full-access",
        created_at=legacy.created_at,
        updated_at=legacy.updated_at,
    )
    manager.runtime_adapter.discover_sessions = MagicMock(
        return_value=RuntimeSessionDiscoveryResult(
            sessions=(native,), archive_states={"native-1": False}
        )
    )

    discovered = manager.list_sessions()[0]

    assert discovered.workspace_id == "runtime-session"
    assert discovered.workspace_name == "external-project"
    assert manager.store.get(legacy.id).discovered is True


def test_ai_session_manager_keeps_managed_session_workspace_unchanged(
    settings: Settings,
) -> None:
    manager = AiSessionManager(settings)
    managed = session(settings.codex_pty.workspace, native_session_id="native-1")
    manager.store.save(managed)
    native = RuntimeNativeSession(
        runtime_id="codex",
        native_session_id="native-1",
        cwd=settings.codex_pty.workspace,
        title="已管理 Session",
        active_permission_mode="full-access",
        created_at=managed.created_at,
        updated_at=managed.updated_at,
    )
    manager.runtime_adapter.discover_sessions = MagicMock(
        return_value=RuntimeSessionDiscoveryResult(
            sessions=(native,), archive_states={"native-1": False}
        )
    )

    manager.list_sessions()

    stored = manager.store.get(managed.id)
    assert stored.workspace_id == "chub"
    assert stored.discovered is False


def test_ai_session_manager_preserves_discovered_native_workspace(
    settings: Settings,
    tmp_path: Path,
) -> None:
    manager = AiSessionManager(settings)
    native = RuntimeNativeSession(
        runtime_id="codex",
        native_session_id="native-1",
        cwd=tmp_path / "outside-fixed-workspaces",
        title="外部 Session",
        active_permission_mode="full-access",
        created_at=session(settings.codex_pty.workspace).created_at,
        updated_at=session(settings.codex_pty.workspace).updated_at,
    )

    discovered = manager._session_from_native(native)

    assert discovered.workspace_id == "runtime-session"
    assert discovered.workspace_name == "outside-fixed-workspaces"
    assert discovered.status == "stopped"
    assert discovered.error is None


def test_ai_session_manager_binds_native_id_once_and_keeps_it_private(
    settings: Settings,
) -> None:
    manager = AiSessionManager(settings)
    manager.runtime_adapter.validate_native_session_id = MagicMock()
    created = session(settings.codex_pty.workspace, session_mode="quick")
    created.error = "native_session_identity_conflict"
    manager.store.save(created)

    manager.bind_quick_interaction_native_session(created.id, "native-1")

    assert manager.store.get(created.id).native_session_id == "native-1"
    assert manager.store.get(created.id).error is None
    public = manager._public(manager.store.get(created.id))
    assert "codex_session_id" not in public.model_dump()
    assert public.can_archive is True
    with pytest.raises(ApiError) as error:
        manager.bind_quick_interaction_native_session(created.id, "native-2")
    assert error.value.code == "quick_interaction_native_session_conflict"


def test_ai_session_manager_allows_translation_native_id_rotation(
    settings: Settings,
) -> None:
    manager = AiSessionManager(settings)
    manager._require_available = MagicMock()
    manager.runtime_adapter.validate_native_session_id = MagicMock()
    manager.runtime_adapter.has_active_writer = MagicMock(return_value=False)
    translation = manager.create_translation_session()
    stored = manager.store.get(translation.id)
    assert stored is not None
    stored.native_session_id = "native-old"
    manager.store.save(stored)

    manager.bind_quick_interaction_native_session(translation.id, "native-new")

    assert manager.store.get(translation.id).native_session_id == "native-new"


def test_ai_session_manager_does_not_rotate_translation_native_id_while_busy(
    settings: Settings,
) -> None:
    manager = AiSessionManager(settings)
    manager._require_available = MagicMock()
    manager.runtime_adapter.validate_native_session_id = MagicMock()
    manager.runtime_adapter.has_active_writer = MagicMock(return_value=True)
    translation = manager.create_translation_session()
    stored = manager.store.get(translation.id)
    assert stored is not None
    stored.native_session_id = "native-old"
    manager.store.save(stored)

    with pytest.raises(ApiError) as error:
        manager.bind_quick_interaction_native_session(translation.id, "native-new")

    assert error.value.code == "quick_interaction_native_session_conflict"
    assert manager.store.get(translation.id).native_session_id == "native-old"


def test_ai_session_manager_resolves_external_native_writer_usage(
    settings: Settings,
) -> None:
    manager = AiSessionManager(settings)
    created = session(settings.codex_pty.workspace, native_session_id="native-1")
    manager.store.save(created)
    manager.get_session = MagicMock(return_value=created)
    manager.supervisor.owns_terminal_writer = MagicMock(return_value=False)
    manager.runtime_adapter.has_active_writer = MagicMock(return_value=True)

    usage = manager.resolve_session_usage(created.id)

    assert usage.native_session_present is True
    assert usage.owner == "external"
    assert usage.phase == "unknown"


def test_ai_session_manager_resolves_chub_terminal_execution(
    settings: Settings,
) -> None:
    manager = AiSessionManager(settings)
    created = session(settings.codex_pty.workspace, native_session_id="native-1")
    created.activity = "working"
    created.activity_source = "terminal"
    manager.store.save(created)
    manager.get_session = MagicMock(return_value=created)
    manager.supervisor.owns_terminal_writer = MagicMock(return_value=True)
    manager.runtime_adapter.has_active_writer = MagicMock(
        side_effect=RuntimeOperationError(
            "codex_writer_status_unavailable",
            "writer status is unavailable",
        )
    )

    usage = manager.resolve_session_usage(created.id)

    assert usage.owner == "terminal"
    assert usage.phase == "running"
    manager.runtime_adapter.has_active_writer.assert_not_called()


def test_ai_session_manager_resolves_chub_worker_waiting_result(
    settings: Settings,
) -> None:
    manager = AiSessionManager(settings)
    created = session(settings.codex_pty.workspace, native_session_id="native-1")
    manager.store.save(created)
    manager.get_session = MagicMock(return_value=created)
    manager.set_quick_interaction_checker(lambda session_id: session_id == created.id)
    manager.supervisor.owns_terminal_writer = MagicMock(return_value=False)

    usage = manager.resolve_session_usage(created.id)

    assert usage.owner == "quick_worker"
    assert usage.phase == "waiting_result"


def test_ai_session_manager_resolves_local_only_session_usage(
    settings: Settings,
) -> None:
    manager = AiSessionManager(settings)
    created = session(settings.codex_pty.workspace)
    manager.store.save(created)

    usage = manager.resolve_session_usage(created.id)

    assert usage.native_session_present is False
    assert usage.owner == "none"
    assert usage.phase == "idle"


@pytest.mark.parametrize(
    ("owner", "phase"),
    [
        ("terminal", "running"),
        ("quick_worker", "waiting_result"),
    ],
)
def test_ai_session_manager_allows_stop_for_chub_execution(
    settings: Settings,
    owner: str,
    phase: str,
) -> None:
    manager = AiSessionManager(settings)
    created = session(settings.codex_pty.workspace, native_session_id="native-1")
    manager.store.save(created)
    manager.resolve_session_usage = MagicMock(
        return_value=SessionUsage(
            native_session_present=True,
            owner=owner,
            phase=phase,
        )
    )

    usage = manager.ensure_stop_allowed(created.id)

    assert usage.owner == owner
    assert usage.phase == phase


@pytest.mark.parametrize(
    ("owner", "phase", "code"),
    [
        ("external", "unknown", "codex_session_writer_active"),
        ("unknown", "unknown", "codex_session_usage_unknown"),
        ("terminal", "idle", "codex_session_not_running"),
        ("none", "idle", "codex_session_not_running"),
    ],
)
def test_ai_session_manager_rejects_stop_outside_chub_execution(
    settings: Settings,
    owner: str,
    phase: str,
    code: str,
) -> None:
    manager = AiSessionManager(settings)
    created = session(settings.codex_pty.workspace, native_session_id="native-1")
    manager.store.save(created)
    manager.resolve_session_usage = MagicMock(
        return_value=SessionUsage(
            native_session_present=True,
            owner=owner,
            phase=phase,
        )
    )

    with pytest.raises(ApiError) as error:
        manager.ensure_stop_allowed(created.id)

    assert error.value.code == code


def test_ai_session_manager_rejects_delete_for_external_writer(
    settings: Settings,
) -> None:
    manager = AiSessionManager(settings)
    created = session(settings.codex_pty.workspace, native_session_id="native-1")
    manager.store.save(created)
    manager.get_session = MagicMock(return_value=created)
    manager.resolve_session_usage = MagicMock(
        return_value=SessionUsage(
            native_session_present=True,
            owner="external",
            phase="unknown",
        )
    )
    manager.runtime_adapter.run_native_action = MagicMock()

    with pytest.raises(ApiError) as error:
        manager.delete_session(created.id)

    assert error.value.code == "codex_session_writer_active"
    manager.runtime_adapter.run_native_action.assert_not_called()
    assert manager.store.get(created.id) is not None


@pytest.mark.parametrize("phase", ["running", "waiting_result"])
def test_ai_session_manager_allows_delete_for_chub_quick_execution(
    settings: Settings,
    phase: str,
) -> None:
    manager = AiSessionManager(settings)
    created = session(settings.codex_pty.workspace, native_session_id="native-1")
    manager.store.save(created)
    manager.resolve_session_usage = MagicMock(
        return_value=SessionUsage(
            native_session_present=True,
            owner="quick_worker",
            phase=phase,
        )
    )

    usage = manager.ensure_delete_allowed(created.id)

    assert usage.owner == "quick_worker"
    assert usage.phase == phase


@pytest.mark.parametrize(
    ("owner", "phase"),
    [("unknown", "unknown"), ("terminal", "unknown"), ("quick_worker", "unknown")],
)
def test_ai_session_manager_allows_delete_when_chub_usage_is_unknown(
    settings: Settings,
    owner: str,
    phase: str,
) -> None:
    manager = AiSessionManager(settings)
    created = session(settings.codex_pty.workspace, native_session_id="native-1")
    manager.store.save(created)
    manager.resolve_session_usage = MagicMock(
        return_value=SessionUsage(
            native_session_present=True,
            owner=owner,
            phase=phase,
        )
    )

    usage = manager.ensure_delete_allowed(created.id)

    assert usage.owner == owner
    assert usage.phase == phase


def test_ai_session_manager_reconciles_native_delete_before_cleanup(
    settings: Settings,
) -> None:
    manager = AiSessionManager(settings)
    created = session(settings.codex_pty.workspace, native_session_id="native-1")
    manager.store.save(created)
    manager.get_session = MagicMock(return_value=created)
    manager.ensure_delete_allowed = MagicMock()
    manager.validate_native_session_id = MagicMock()
    manager.runtime_adapter.run_native_action = MagicMock(
        side_effect=RuntimeOperationError(
            "codex_session_delete_failed",
            "native delete result was interrupted",
            kind="unavailable",
        )
    )
    manager.runtime_adapter.native_session_deleted_state = MagicMock(
        return_value=True
    )
    manager.stop_session = MagicMock()

    manager.delete_session(created.id)

    manager.runtime_adapter.native_session_deleted_state.assert_called_once_with(
        "native-1"
    )
    manager.stop_session.assert_called_once_with(created.id)
    assert manager.store.get(created.id) is None


def test_ai_session_manager_archives_native_before_clearing_chub_state(
    settings: Settings,
) -> None:
    manager = AiSessionManager(settings)
    created = session(settings.codex_pty.workspace, native_session_id="native-1")
    manager.store.save(created)
    manager.get_session = MagicMock(return_value=created)
    manager.ensure_archive_allowed = MagicMock()
    manager.validate_native_session_id = MagicMock()
    events: list[str] = []
    manager.runtime_adapter.run_native_action = MagicMock(
        side_effect=lambda *_args: events.append("native-archive")
    )
    manager.stop_session = MagicMock(
        side_effect=lambda _session_id: events.append("stop")
    )

    manager.archive_session(created.id)

    assert events == ["native-archive", "stop"]
    manager.runtime_adapter.run_native_action.assert_called_once_with(
        "archive", "native-1"
    )
    assert manager.store.get(created.id) is None


def test_ai_session_manager_archive_does_not_reconcile_mapping_before_action(
    settings: Settings,
) -> None:
    manager = AiSessionManager(settings)
    created = session(settings.codex_pty.workspace, native_session_id="native-1")
    manager.store.save(created)
    manager._sync_bound_native_sessions = MagicMock()
    manager.runtime_adapter.has_active_writer = MagicMock(return_value=False)
    manager.runtime_adapter.run_native_action = MagicMock()

    manager.archive_native_session(created.id)

    manager._sync_bound_native_sessions.assert_not_called()
    manager.runtime_adapter.run_native_action.assert_called_once_with(
        "archive",
        "native-1",
    )


def test_ai_session_manager_keeps_chub_state_when_native_archive_fails(
    settings: Settings,
) -> None:
    manager = AiSessionManager(settings)
    created = session(settings.codex_pty.workspace, native_session_id="native-1")
    manager.store.save(created)
    manager.get_session = MagicMock(return_value=created)
    manager.ensure_archive_allowed = MagicMock()
    manager.validate_native_session_id = MagicMock()
    manager.stop_session = MagicMock()
    manager.runtime_adapter.run_native_action = MagicMock(
        side_effect=RuntimeOperationError(
            "codex_session_archive_failed",
            "native archive failed",
            kind="conflict",
        )
    )

    with pytest.raises(ApiError) as error:
        manager.archive_session(created.id)

    assert error.value.code == "codex_session_archive_failed"
    manager.stop_session.assert_not_called()
    assert manager.store.get(created.id) is not None


def test_ai_session_manager_reconciles_native_archive_before_retrying_cleanup(
    settings: Settings,
) -> None:
    manager = AiSessionManager(settings)
    created = session(settings.codex_pty.workspace, native_session_id="native-1")
    manager.store.save(created)
    manager.get_session = MagicMock(return_value=created)
    manager.ensure_archive_allowed = MagicMock()
    manager.validate_native_session_id = MagicMock()
    manager.runtime_adapter.run_native_action = MagicMock(
        side_effect=RuntimeOperationError(
            "codex_session_archive_failed",
            "native archive result was interrupted",
            kind="unavailable",
        )
    )
    manager.runtime_adapter.native_session_archive_state = MagicMock(
        return_value=True
    )
    manager.stop_session = MagicMock()

    manager.archive_session(created.id)

    manager.runtime_adapter.native_session_archive_state.assert_called_once_with(
        "native-1"
    )
    manager.stop_session.assert_called_once_with(created.id)
    assert manager.store.get(created.id) is None


@pytest.mark.parametrize(
    ("owner", "phase"),
    [("terminal", "running"), ("quick_worker", "waiting_result")],
)
def test_ai_session_manager_rejects_archive_during_chub_execution(
    settings: Settings,
    owner: str,
    phase: str,
) -> None:
    manager = AiSessionManager(settings)
    created = session(settings.codex_pty.workspace, native_session_id="native-1")
    manager.store.save(created)
    manager.resolve_session_usage = MagicMock(
        return_value=SessionUsage(
            native_session_present=True,
            owner=owner,
            phase=phase,
        )
    )

    with pytest.raises(ApiError) as error:
        manager.ensure_archive_allowed(created.id)

    assert error.value.code == "codex_session_in_progress"


@pytest.mark.parametrize(
    ("owner", "phase"),
    [("unknown", "unknown"), ("terminal", "unknown"), ("quick_worker", "unknown")],
)
def test_ai_session_manager_allows_archive_when_execution_is_unknown(
    settings: Settings,
    owner: str,
    phase: str,
) -> None:
    manager = AiSessionManager(settings)
    created = session(settings.codex_pty.workspace, native_session_id="native-1")
    manager.store.save(created)
    manager.resolve_session_usage = MagicMock(
        return_value=SessionUsage(
            native_session_present=True,
            owner=owner,
            phase=phase,
        )
    )

    usage = manager.ensure_archive_allowed(created.id)

    assert usage.owner == owner
    assert usage.phase == phase


def test_ai_session_manager_archives_chub_only_session_without_native_action(
    settings: Settings,
) -> None:
    manager = AiSessionManager(settings)
    created = session(settings.codex_pty.workspace)
    manager.store.save(created)
    manager.ensure_archive_allowed = MagicMock()
    manager.stop_session = MagicMock()
    manager.runtime_adapter.run_native_action = MagicMock()

    manager.archive_session(created.id)

    manager.runtime_adapter.run_native_action.assert_not_called()
    manager.stop_session.assert_called_once_with(created.id)
    assert manager.store.get(created.id) is None


def test_ai_session_manager_marks_chub_only_session_archivable(
    settings: Settings,
) -> None:
    manager = AiSessionManager(settings)
    created = session(settings.codex_pty.workspace)

    public = manager._public(created)

    assert public.can_archive is True


def test_quick_hook_cannot_replace_worker_native_identity(
    settings: Settings,
) -> None:
    manager = AiSessionManager(settings)
    first = "11111111-1111-4111-8111-111111111111"
    second = "22222222-2222-4222-8222-222222222222"
    created = session(
        settings.codex_pty.workspace,
        native_session_id=first,
        session_mode="quick",
    )
    manager.store.save(created)
    manager.set_quick_interaction_checker(lambda _session_id: True)
    manager.runtime_adapter.hook_dir.mkdir(parents=True)
    hook_path = manager.runtime_adapter.hook_dir / f"{created.id}.json"
    hook_path.write_text(
        json.dumps(
            {
                "codex_session_id": second,
                "activity": "idle",
                "activity_source": "quick",
            }
        ),
        encoding="utf-8",
    )
    hook_path.chmod(0o600)

    manager._consume_hook_result(created.id)

    assert manager.store.get(created.id).native_session_id == first


def test_hook_identity_conflict_identifies_chub_as_the_owner(
    settings: Settings,
) -> None:
    manager = AiSessionManager(settings)
    first = "33333333-3333-4333-8333-333333333333"
    second = "44444444-4444-4444-8444-444444444444"
    created = session(settings.codex_pty.workspace, native_session_id=first)
    manager.store.save(created)
    manager.runtime_adapter.hook_dir.mkdir(parents=True)
    hook_path = manager.runtime_adapter.hook_dir / f"{created.id}.json"
    hook_path.write_text(
        json.dumps({"codex_session_id": second, "activity": "idle"}),
        encoding="utf-8",
    )
    hook_path.chmod(0o600)

    with pytest.raises(ApiError) as error:
        manager._consume_hook_result(created.id)

    assert error.value.code == "codex_session_native_conflict"
    assert error.value.message.startswith("Chub Session identity conflict:")


def test_terminal_hook_adopts_discovery_created_during_terminal_start(
    settings: Settings,
) -> None:
    manager = AiSessionManager(settings)
    native_id = "44444444-4444-4444-8444-444444444444"
    terminal = session(settings.codex_pty.workspace)
    terminal.terminal_launch_id = "a" * 32
    discovered = session(
        settings.codex_pty.workspace,
        native_session_id=native_id,
    )
    discovered.discovered = True
    discovered.created_at = terminal.created_at
    manager.store.save(terminal)
    manager.store.save(discovered)
    manager.supervisor.owns_terminal_writer = MagicMock(
        side_effect=lambda session_id: session_id == terminal.id
    )
    manager.runtime_adapter.hook_dir.mkdir(parents=True)
    hook_path = manager.runtime_adapter.hook_dir / f"{terminal.id}.json"
    hook_path.write_text(
        json.dumps(
            {
                "codex_session_id": native_id,
                "activity": "idle",
                "launch_id": "a" * 32,
            }
        ),
        encoding="utf-8",
    )
    hook_path.chmod(0o600)

    manager._consume_hook_result(terminal.id)

    assert manager.store.get(terminal.id).native_session_id == native_id
    assert manager.store.get(discovered.id) is None
    assert not hook_path.exists()


def test_terminal_hook_requires_current_launch_generation(
    settings: Settings,
) -> None:
    manager = AiSessionManager(settings)
    terminal = session(settings.codex_pty.workspace)
    terminal.terminal_launch_id = "a" * 32
    manager.store.save(terminal)
    manager.runtime_adapter.hook_dir.mkdir(parents=True)
    hook_path = manager.runtime_adapter.hook_dir / f"{terminal.id}.json"
    hook_path.write_text(
        json.dumps(
            {
                "codex_session_id": "44444444-4444-4444-8444-444444444444",
                "launch_id": "b" * 32,
                "activity": "idle",
            }
        ),
        encoding="utf-8",
    )
    hook_path.chmod(0o600)

    manager._consume_hook_result(terminal.id)

    assert manager.store.get(terminal.id).native_session_id is None
    assert not hook_path.exists()


def test_quick_native_binding_rejects_stale_worker_task(
    settings: Settings,
) -> None:
    manager = AiSessionManager(settings)
    quick = session(settings.codex_pty.workspace, session_mode="quick")
    manager.store.save(quick)
    current_task = "qw-1750000000000-11111111111111111111111111111111"
    stale_task = "qw-1750000000000-22222222222222222222222222222222"
    manager.register_quick_native_claim(quick.id, current_task)

    with pytest.raises(ApiError) as error:
        manager.bind_quick_interaction_native_session(
            quick.id,
            "44444444-4444-4444-8444-444444444444",
            worker_task_id=stale_task,
            execution_id="c" * 32,
        )

    assert error.value.code == "quick_interaction_native_session_stale"
    assert manager.store.get(quick.id).native_session_id is None


def test_quick_native_binding_accepts_current_task_for_bound_native_session(
    settings: Settings,
) -> None:
    manager = AiSessionManager(settings)
    native_session_id = "44444444-4444-4444-8444-444444444444"
    quick = session(
        settings.codex_pty.workspace,
        native_session_id=native_session_id,
        session_mode="quick",
    )
    task_id = "qw-1750000000000-11111111111111111111111111111111"
    manager.store.save(quick)

    manager.register_quick_native_claim(quick.id, task_id)
    manager.bind_quick_interaction_native_session(
        quick.id,
        native_session_id,
        worker_task_id=task_id,
        execution_id="a" * 32,
    )

    stored = manager.store.get(quick.id)
    assert stored is not None
    assert stored.quick_native_claim_task_id == task_id
    assert stored.quick_native_claim_execution_id == "a" * 32


def test_list_sessions_discards_conflicting_hook_without_failing_control_plane(
    settings: Settings,
) -> None:
    manager = AiSessionManager(settings)
    created = session(
        settings.codex_pty.workspace,
        native_session_id="33333333-3333-4333-8333-333333333333",
    )
    manager.store.save(created)
    manager.runtime_adapter.hook_dir.mkdir(parents=True)
    hook_path = manager.runtime_adapter.hook_dir / f"{created.id}.json"
    hook_path.write_text(
        json.dumps(
            {
                "codex_session_id": "44444444-4444-4444-8444-444444444444",
                "activity": "idle",
            }
        ),
        encoding="utf-8",
    )
    hook_path.chmod(0o600)
    manager.runtime_adapter.discover_sessions = MagicMock(
        return_value=RuntimeSessionDiscoveryResult(
            sessions=(),
            archive_states={created.native_session_id: False},
        )
    )

    sessions = manager.list_sessions()

    assert [item.id for item in sessions] == [created.id]
    assert not hook_path.exists()


def test_quick_hook_does_not_bind_native_identity_when_discovery_created_duplicate(
    settings: Settings,
) -> None:
    manager = AiSessionManager(settings)
    quick = session(settings.codex_pty.workspace, session_mode="quick")
    discovered = session(
        settings.codex_pty.workspace,
        native_session_id="native-1",
    )
    discovered.discovered = True
    manager.store.save(quick)
    manager.store.save(discovered)
    manager.runtime_adapter.hook_dir.mkdir(parents=True)
    hook_path = manager.runtime_adapter.hook_dir / f"{quick.id}.json"
    hook_path.write_text(
        '{"codex_session_id":"native-1","activity":"idle"}',
        encoding="utf-8",
    )
    hook_path.chmod(0o600)

    manager._consume_hook_result(quick.id)

    assert manager.store.get(quick.id).native_session_id is None
    assert manager.store.get(discovered.id) is not None


def test_upgrade_readiness_ignores_quick_hook_identity_and_keeps_duplicate_discovery(
    settings: Settings,
) -> None:
    manager = AiSessionManager(settings)
    quick = session(settings.codex_pty.workspace, session_mode="quick")
    discovered = session(
        settings.codex_pty.workspace,
        native_session_id="native-1",
    )
    discovered.discovered = True
    manager.store.save(quick)
    manager.store.save(discovered)
    manager.runtime_adapter.discover_sessions = MagicMock(
        return_value=RuntimeSessionDiscoveryResult(
            sessions=(
                RuntimeNativeSession(
                    runtime_id="codex",
                    native_session_id="native-1",
                    cwd=settings.codex_pty.workspace,
                    title="Runtime Session",
                    active_permission_mode="full-access",
                    active_model=None,
                    active_reasoning_effort=None,
                    created_at=quick.created_at,
                    updated_at=quick.updated_at,
                ),
                RuntimeNativeSession(
                    runtime_id="codex",
                    native_session_id="native-1",
                    cwd=settings.codex_pty.workspace,
                    title="Runtime Session",
                    active_permission_mode="full-access",
                    active_model=None,
                    active_reasoning_effort=None,
                    created_at=quick.created_at,
                    updated_at=quick.updated_at,
                ),
            ),
            archive_states={"native-1": False},
        )
    )
    manager.runtime_adapter.hook_dir.mkdir(parents=True)
    hook_path = manager.runtime_adapter.hook_dir / f"{quick.id}.json"
    hook_path.write_text(
        '{"codex_session_id":"native-1","activity":"idle"}',
        encoding="utf-8",
    )
    hook_path.chmod(0o600)

    manager.verify_system_upgrade_readiness()

    assert manager.store.get(quick.id).native_session_id is None
    assert manager.store.get(discovered.id) is not None


def test_ai_session_manager_does_not_read_legacy_store_records(
    settings: Settings,
) -> None:
    legacy_path = settings.codex_pty.data_file
    legacy_path.write_text('[{"id":"legacy-session"}]', encoding="utf-8")
    legacy_path.chmod(0o600)

    manager = AiSessionManager(settings)
    manager.runtime_adapter.discover_sessions = MagicMock(
        return_value=RuntimeSessionDiscoveryResult(sessions=(), archive_states={})
    )

    assert manager.list_sessions() == []
    assert manager.store.path.name == "ai-sessions.json"


def test_ai_session_manager_removes_externally_archived_native_session(
    settings: Settings,
) -> None:
    manager = AiSessionManager(settings)
    created = session(settings.codex_pty.workspace, native_session_id="native-1")
    manager.store.save(created)
    manager.runtime_adapter.discover_sessions = MagicMock(
        return_value=MagicMock(sessions=(), archive_states={"native-1": True})
    )
    manager.supervisor.stop_backend = MagicMock()

    manager._sync_bound_native_sessions()

    assert manager.store.get(created.id) is None
    manager.supervisor.stop_backend.assert_called_once_with(created.id)


def test_ai_session_manager_upgrade_discard_preserves_native_session(
    settings: Settings,
) -> None:
    manager = AiSessionManager(settings)
    created = session(settings.codex_pty.workspace, native_session_id="native-1")
    manager.store.save(created)
    manager.supervisor.stop_backend = MagicMock()
    manager.runtime_adapter.run_native_action = MagicMock()

    manager.discard_session_for_system_upgrade(created.id)

    assert manager.store.get(created.id) is None
    manager.supervisor.stop_backend.assert_called_once_with(created.id)
    manager.runtime_adapter.run_native_action.assert_not_called()


def test_ai_session_manager_rebinds_upgrade_terminal_carrier(
    settings: Settings,
) -> None:
    manager = AiSessionManager(settings)
    native = RuntimeNativeSession(
        runtime_id="codex",
        native_session_id="native-1",
        cwd=settings.codex_pty.workspace,
        created_at=session(settings.codex_pty.workspace).created_at,
        updated_at=session(settings.codex_pty.workspace).updated_at,
    )
    manager.runtime_adapter.discover_sessions = MagicMock(
        return_value=RuntimeSessionDiscoveryResult(
            sessions=(native,),
            archive_states={"native-1": False},
        )
    )
    manager.supervisor.rebind_terminal_carrier = MagicMock(return_value=False)
    manager.supervisor.rebind_terminal_carrier_by_native_session = MagicMock(
        return_value=True
    )
    manager.runtime_adapter.rebind_activity_session = MagicMock()

    old_session_id = str(uuid4())
    manager.rebind_upgrade_terminal_carriers([(old_session_id, "native-1")])

    discovered = manager.store.list()
    assert len(discovered) == 1
    assert discovered[0].native_session_id == "native-1"
    manager.supervisor.rebind_terminal_carrier.assert_called_once_with(
        old_session_id,
        discovered[0].id,
    )
    manager.supervisor.rebind_terminal_carrier_by_native_session.assert_called_once_with(
        "native-1",
        discovered[0].id,
    )
    manager.runtime_adapter.rebind_activity_session.assert_called_once_with(
        old_session_id,
        discovered[0].id,
    )


def test_ai_session_manager_reenters_owned_terminal_without_runtime_writer_probe(
    settings: Settings,
) -> None:
    manager = AiSessionManager(settings)
    created = session(settings.codex_pty.workspace, native_session_id="native-1")
    manager.store.save(created)
    manager._require_available = MagicMock()
    manager.get_session = MagicMock(return_value=created)
    manager._ensure_profile = MagicMock()
    manager.supervisor.ensure_terminal = MagicMock()
    manager.supervisor.owns_terminal_writer = MagicMock(return_value=True)
    manager.runtime_adapter.has_active_writer = MagicMock(
        side_effect=RuntimeOperationError(
            "codex_writer_status_unavailable",
            "writer status is unavailable",
        )
    )

    manager.ensure_terminal(created.id)

    manager.runtime_adapter.has_active_writer.assert_not_called()
    manager.supervisor.owns_terminal_writer.assert_called_once_with(created.id)
    manager.supervisor.ensure_terminal.assert_called_once_with(
        created,
        max_running=settings.codex_pty.max_running,
    )


def test_ai_session_manager_reenters_terminal_without_probing_native_writer(
    settings: Settings,
) -> None:
    manager = AiSessionManager(settings)
    created = session(settings.codex_pty.workspace, native_session_id="native-1")
    created.status = "running"
    manager.store.save(created)
    manager._require_available = MagicMock()
    manager.get_session = MagicMock(return_value=created)
    manager._ensure_profile = MagicMock()
    manager.supervisor.ensure_terminal = MagicMock()
    manager.runtime_adapter.has_active_writer = MagicMock(return_value=True)
    manager.supervisor.owns_terminal_writer = MagicMock(return_value=True)

    manager.ensure_terminal(created.id)

    manager.supervisor.owns_terminal_writer.assert_called_once_with(created.id)
    manager.supervisor.ensure_terminal.assert_called_once_with(
        created,
        max_running=settings.codex_pty.max_running,
    )


def test_ai_session_manager_restarts_terminal_with_new_launch_generation(
    settings: Settings,
) -> None:
    manager = AiSessionManager(settings)
    created = session(settings.codex_pty.workspace, native_session_id="native-1")
    created.terminal_launch_id = "a" * 32
    manager.store.save(created)
    manager._require_available = MagicMock()
    manager._ensure_profile = MagicMock()
    manager._consume_hook_result_safely = MagicMock()
    manager.supervisor.restart_terminal_backend = MagicMock()

    manager.restart_terminal_backend(created.id)

    refreshed = manager.store.get(created.id)
    assert refreshed is not None
    assert refreshed.terminal_launch_id is not None
    assert refreshed.terminal_launch_id != "a" * 32
    manager._consume_hook_result_safely.assert_called_once_with(created.id)
    manager.supervisor.restart_terminal_backend.assert_called_once_with(
        refreshed,
        max_running=settings.codex_pty.max_running,
    )


def test_ai_session_manager_rejects_unowned_active_writer_for_terminal(
    settings: Settings,
) -> None:
    manager = AiSessionManager(settings)
    created = session(settings.codex_pty.workspace, native_session_id="native-1")
    manager.store.save(created)
    manager._require_available = MagicMock()
    manager.get_session = MagicMock(return_value=created)
    manager.supervisor.ensure_terminal = MagicMock()
    manager.runtime_adapter.has_active_writer = MagicMock(return_value=True)
    manager.supervisor.owns_terminal_writer = MagicMock(return_value=False)

    with pytest.raises(ApiError) as error:
        manager.ensure_terminal(created.id)

    assert error.value.code == "codex_session_writer_active"
    manager.supervisor.ensure_terminal.assert_not_called()


def test_interactive_supervisor_identifies_only_terminal_writer_process(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    supervisor = InteractiveSupervisor(MagicMock())
    supervisor._tmux_running = MagicMock(return_value=False)
    quick = MagicMock()
    quick.info = {"cmdline": ["/usr/bin/codex"]}
    quick.environ.return_value = {
        "CHUB_PTY_SESSION_ID": "session-1",
        "CHUB_ACTIVITY_SOURCE": "quick",
    }
    terminal = MagicMock()
    terminal.info = {"cmdline": ["/usr/bin/codex"]}
    terminal.environ.return_value = {"CHUB_PTY_SESSION_ID": "session-1"}
    monkeypatch.setattr(
        "app.ai_session.supervisor.psutil.process_iter",
        lambda _attrs: [quick, terminal],
    )

    assert supervisor.owns_terminal_writer("session-1") is True


def test_interactive_supervisor_rejects_quick_writer_as_terminal_owner(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    supervisor = InteractiveSupervisor(MagicMock())
    supervisor._tmux_running = MagicMock(return_value=False)
    quick = MagicMock()
    quick.info = {"cmdline": ["/usr/bin/codex"]}
    quick.environ.return_value = {
        "CHUB_PTY_SESSION_ID": "session-1",
        "CHUB_ACTIVITY_SOURCE": "quick",
    }
    monkeypatch.setattr(
        "app.ai_session.supervisor.psutil.process_iter",
        lambda _attrs: [quick],
    )

    assert supervisor.owns_terminal_writer("session-1") is False


def test_interactive_supervisor_uses_runtime_neutral_terminal_state() -> None:
    supervisor = InteractiveSupervisor(MagicMock())

    assert supervisor.connections.__class__.__module__ == "app.ai_session.terminal"
    assert supervisor.tickets.__class__.__module__ == "app.ai_session.terminal"


def test_runtime_api_error_marks_upstream_source(settings: Settings) -> None:
    manager = AiSessionManager(settings)

    error = manager._runtime_api_error(
        RuntimeOperationError("codex_not_trusted", "Not inside a trusted directory")
    )

    assert error.source == "runtime"
    assert error.code == "codex_not_trusted"


def test_interactive_supervisor_reuses_backend_and_stops_it(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = MagicMock()
    adapter.terminal_command.return_value = MagicMock(argv=("ttyd", "-p", "12345"))
    supervisor = InteractiveSupervisor(adapter)
    created = session(tmp_path, native_session_id="native-1")
    process = MagicMock()
    process.poll.return_value = None

    monkeypatch.setattr(
        "app.ai_session.supervisor.subprocess.Popen",
        MagicMock(return_value=process),
    )
    supervisor._available_port = MagicMock(return_value=12345)
    supervisor._wait_for_port = MagicMock()
    supervisor._tmux_running = MagicMock(return_value=True)

    assert supervisor.ensure_terminal(created, max_running=3) == 12345
    assert supervisor.ensure_terminal(created, max_running=3) == 12345
    assert adapter.terminal_command.call_count == 1
    assert supervisor.backend_port(created.id) == 12345

    supervisor.stop_backend(created.id)

    process.terminate.assert_called_once()
    with pytest.raises(ApiError, match="backend is unavailable"):
        supervisor.backend_port(created.id)


def test_interactive_supervisor_rebuilds_bridge_when_tmux_disappears(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = MagicMock()
    adapter.terminal_command.side_effect = [
        MagicMock(argv=("ttyd", "-p", "12345")),
        MagicMock(argv=("ttyd", "-p", "12346")),
    ]
    supervisor = InteractiveSupervisor(adapter)
    created = session(tmp_path, native_session_id="native-1")
    old_process = MagicMock()
    old_process.poll.return_value = None
    new_process = MagicMock()
    new_process.poll.return_value = None

    monkeypatch.setattr(
        "app.ai_session.supervisor.subprocess.Popen",
        MagicMock(side_effect=[old_process, new_process]),
    )
    supervisor._available_port = MagicMock(side_effect=[12345, 12346])
    supervisor._wait_for_port = MagicMock()
    supervisor._tmux_running = MagicMock(return_value=False)

    assert supervisor.ensure_terminal(created, max_running=3) == 12345
    assert supervisor.ensure_terminal(created, max_running=3) == 12346

    old_process.terminate.assert_called_once()
    assert adapter.terminal_command.call_count == 2
    assert supervisor.backend_port(created.id) == 12346


def test_interactive_supervisor_rebinds_upgrade_tmux_carrier(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    supervisor = InteractiveSupervisor(MagicMock())
    old_session_id = str(uuid4())
    new_session_id = str(uuid4())
    supervisor._tmux_running = MagicMock(side_effect=[True, False, True])
    rename = MagicMock(returncode=0)
    subprocess_run = MagicMock(return_value=rename)
    monkeypatch.setattr(
        "app.ai_session.supervisor.subprocess.run",
        subprocess_run,
    )

    assert supervisor.rebind_terminal_carrier(old_session_id, new_session_id) is True

    subprocess_run.assert_called_once_with(
        [
            "tmux",
            "rename-session",
            "-t",
            f"chub-{old_session_id}",
            f"chub-{new_session_id}",
        ],
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    assert new_session_id in supervisor._known_session_ids
    assert old_session_id not in supervisor._known_session_ids


def test_interactive_supervisor_rebinds_tmux_carrier_by_native_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    supervisor = InteractiveSupervisor(MagicMock())
    supervisor._tmux_running = MagicMock(side_effect=[False, True])
    supervisor.runtime_adapter.runtime_process_matches.return_value = True
    native_session_id = "01a0234c-3529-7ad2-8ae1-3d51717012db"
    old_session_id = str(uuid4())
    new_session_id = str(uuid4())
    rename = MagicMock(returncode=0)
    subprocess_run = MagicMock(
        side_effect=[
            MagicMock(
                    returncode=0,
                    stdout=(
                    f'chub-{old_session_id}\t"'
                    f'CHUB_PTY_SESSION_ID={old_session_id} '
                    f'CHUB_PTY_HOOK_DIR=/tmp/hooks codex resume {native_session_id}"\n'
                    ),
            ),
            rename,
        ]
    )
    monkeypatch.setattr(
        "app.ai_session.supervisor.subprocess.run",
        subprocess_run,
    )

    assert (
        supervisor.rebind_terminal_carrier_by_native_session(
            native_session_id,
            new_session_id,
        )
        is True
    )
    assert new_session_id in supervisor._known_session_ids
    assert old_session_id not in supervisor._known_session_ids


def test_interactive_supervisor_close_keeps_tmux_carrier(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = MagicMock()
    adapter.terminal_command.return_value = MagicMock(argv=("ttyd", "-p", "12345"))
    supervisor = InteractiveSupervisor(adapter)
    created = session(tmp_path, native_session_id="native-1")
    process = MagicMock()
    process.poll.return_value = None

    monkeypatch.setattr(
        "app.ai_session.supervisor.subprocess.Popen",
        MagicMock(return_value=process),
    )
    supervisor._available_port = MagicMock(return_value=12345)
    supervisor._wait_for_port = MagicMock()
    supervisor._tmux_running = MagicMock(return_value=True)

    supervisor.ensure_terminal(created, max_running=3)
    supervisor.close()

    process.terminate.assert_called_once()
    assert supervisor._tmux_running(created.id) is True


def test_interactive_supervisor_enforces_terminal_limit(tmp_path: Path) -> None:
    adapter = MagicMock()
    supervisor = InteractiveSupervisor(adapter)
    created = session(tmp_path)
    supervisor._tmux_running = MagicMock(return_value=False)
    supervisor.running_terminal_count = MagicMock(return_value=1)

    with pytest.raises(ApiError) as error:
        supervisor.ensure_terminal(created, max_running=1)

    assert error.value.code == "codex_session_limit"
    adapter.terminal_command.assert_not_called()
def test_system_upgrade_reconciles_an_already_archived_native_session(
    settings: Settings,
) -> None:
    manager = AiSessionManager(settings)
    created = session(settings.codex_pty.workspace, native_session_id="native-1")
    manager.store.save(created)
    manager.stop_session = MagicMock()
    manager.runtime_adapter.discovery.session_archive_states = MagicMock(
        return_value={"native-1": True}
    )
    manager.runtime_adapter.run_native_action = MagicMock()

    outcome = manager.archive_session_for_system_upgrade(created.id, "native-1")

    assert outcome == "archived"
    assert manager.store.get(created.id) is None
    manager.runtime_adapter.run_native_action.assert_not_called()


def test_system_upgrade_requires_a_confirmed_native_archive_state(
    settings: Settings,
) -> None:
    manager = AiSessionManager(settings)
    created = session(settings.codex_pty.workspace, native_session_id="native-1")
    manager.store.save(created)
    manager.stop_session = MagicMock()
    manager.runtime_adapter.discovery.session_archive_states = MagicMock(return_value={})

    with pytest.raises(OSError, match="archive state cannot be confirmed"):
        manager.archive_session_for_system_upgrade(created.id, "native-1")

    assert manager.store.get(created.id) is not None
