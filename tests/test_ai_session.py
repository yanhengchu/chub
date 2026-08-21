from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock
from uuid import uuid4

import pytest

from app.ai_session.manager import AiSessionManager
from app.ai_session.models import AiSession
from app.ai_session.store import AiSessionStore, AiSessionStoreUnavailable
from app.ai_session.supervisor import InteractiveSupervisor
from app.ai_runtime import (
    RuntimeNativeSession,
    RuntimeOperationError,
    RuntimeSessionDiscoveryResult,
)
from app.codex.models import WorkspaceInfo
from app.core.config import Settings
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
    manager.store.save(created)

    manager.bind_quick_interaction_native_session(created.id, "native-1")

    assert manager.store.get(created.id).native_session_id == "native-1"
    public = manager._public(manager.store.get(created.id))
    assert "codex_session_id" not in public.model_dump()
    assert public.can_archive is True
    with pytest.raises(ApiError) as error:
        manager.bind_quick_interaction_native_session(created.id, "native-2")
    assert error.value.code == "quick_interaction_native_session_conflict"


def test_quick_hook_cannot_replace_worker_native_identity(
    settings: Settings,
) -> None:
    manager = AiSessionManager(settings)
    first = "11111111-1111-4111-8111-111111111111"
    second = "22222222-2222-4222-8222-222222222222"
    created = session(settings.codex_pty.workspace, native_session_id=first)
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


def test_hook_reuses_quick_session_when_discovery_created_duplicate(
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

    with pytest.raises(ApiError) as error:
        manager._consume_hook_result(quick.id)

    assert error.value.code == "codex_session_native_conflict"
    assert manager.store.get(quick.id).native_session_id is None
    assert manager.store.get(discovered.id) is not None


def test_upgrade_readiness_merges_hook_and_duplicate_runtime_discovery(
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

    with pytest.raises(ApiError) as error:
        manager.verify_system_upgrade_readiness()

    assert error.value.code == "codex_session_native_conflict"
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
    supervisor._tmux_running = MagicMock(return_value=False)

    assert supervisor.ensure_terminal(created, max_running=3) == 12345
    assert supervisor.ensure_terminal(created, max_running=3) == 12345
    assert adapter.terminal_command.call_count == 1
    assert supervisor.backend_port(created.id) == 12345

    supervisor.stop_backend(created.id)

    process.terminate.assert_called_once()
    with pytest.raises(ApiError, match="backend is unavailable"):
        supervisor.backend_port(created.id)


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
