import json
from pathlib import Path
from subprocess import CompletedProcess
from unittest.mock import MagicMock

import pytest

from app.codex.manager import CodexPtyManager
from app.codex.models import CodexSession, SessionInfo, utc_now
from app.codex.store import CodexSessionStore
from app.core.config import Settings
from app.core.response import ApiError


def native_session(session_id: str) -> CodexSession:
    return CodexSession(
        id=session_id,
        workspace_id="codex",
        workspace_name="chub",
        cwd=Path("/workspace/chub"),
        codex_session_id=session_id,
        status="stopped",
    )


def test_update_session_timestamp_keeps_latest_time(settings: Settings) -> None:
    manager = CodexPtyManager(settings)
    session = native_session("99999999-9999-4999-8999-999999999999")
    original = session.updated_at
    latest = utc_now()
    manager.store.save(session)

    manager.update_session_timestamp(session.id, latest)
    manager.update_session_timestamp(session.id, original)

    assert manager.store.get(session.id).updated_at == latest


@pytest.mark.parametrize(
    "archive_states",
    [
        {},
        {"11111111-1111-4111-8111-111111111111": True},
    ],
)
def test_sync_removes_session_archived_or_deleted_outside_chub(
    settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
    archive_states: dict[str, bool],
) -> None:
    manager = CodexPtyManager(settings)
    session = native_session("11111111-1111-4111-8111-111111111111")
    manager.store.save(session)
    manager.discovery = MagicMock()
    manager.discovery.discover.return_value = []
    manager.discovery.session_archive_states.return_value = archive_states
    monkeypatch.setattr("app.codex.manager.shutil.which", lambda _name: None)

    manager._sync_native_sessions()

    assert manager.store.get(session.id) is None


def test_sync_keeps_unindexed_active_native_session(
    settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = CodexPtyManager(settings)
    session = native_session("22222222-2222-4222-8222-222222222222")
    manager.store.save(session)
    manager.discovery = MagicMock()
    manager.discovery.discover.return_value = []
    manager.discovery.session_archive_states.return_value = {
        session.codex_session_id: False
    }
    monkeypatch.setattr("app.codex.manager.shutil.which", lambda _name: None)

    manager._sync_native_sessions()

    assert manager.store.get(session.id) is not None


def test_sync_merges_discovered_session_bound_to_new_chub_session(
    settings: Settings,
) -> None:
    manager = CodexPtyManager(settings)
    native_id = "23232323-2323-4232-8232-232323232323"
    chub_session = CodexSession(
        id="chub-session",
        workspace_id="chub",
        workspace_name="Chub",
        cwd=Path("/workspace/chub"),
        permission_mode="auto-review",
        status="stopped",
    )
    discovered = native_session(native_id)
    discovered.title = "首次快速交互"
    discovered.active_permission_mode = "auto-review"
    manager.store.save(chub_session)
    manager.store.save(discovered)
    manager.discovery = MagicMock()
    manager.discovery.discover.return_value = [discovered]
    manager.discovery.session_archive_states.return_value = None

    manager._sync_native_sessions()
    assert len(manager.store.list()) == 2

    manager.hook_dir.mkdir(parents=True, exist_ok=True)
    (manager.hook_dir / f"{chub_session.id}.json").write_text(
        json.dumps({"codex_session_id": native_id}),
        encoding="utf-8",
    )
    manager._consume_hook_result(chub_session.id)
    manager._sync_native_sessions()

    sessions = manager.store.list()
    assert len(sessions) == 1
    assert sessions[0].id == chub_session.id
    assert sessions[0].codex_session_id == native_id
    assert sessions[0].title == "首次快速交互"
    assert sessions[0].permission_mode == "auto-review"
    assert sessions[0].active_permission_mode == "auto-review"


def test_sync_defers_native_session_while_new_quick_session_is_binding(
    settings: Settings,
) -> None:
    manager = CodexPtyManager(settings)
    chub_session = CodexSession(
        id="chub-session",
        workspace_id="chub",
        workspace_name="Chub",
        cwd=Path("/workspace/chub"),
        permission_mode="auto-review",
        status="stopped",
    )
    discovered = native_session("24242424-2424-4242-8242-242424242424")
    manager.store.save(chub_session)
    manager.set_quick_interaction_checker(lambda session_id: session_id == chub_session.id)
    manager.discovery = MagicMock()
    manager.discovery.discover.return_value = [discovered]
    manager.discovery.session_archive_states.return_value = None

    manager._sync_native_sessions()

    assert [session.id for session in manager.store.list()] == [chub_session.id]


def test_restart_resets_unverified_running_activity(
    settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = native_session("77777777-7777-4777-8777-777777777777")
    session.status = "running"
    session.activity = "idle"
    CodexSessionStore(settings.codex_pty.data_file).save(session)
    monkeypatch.setattr("app.codex.manager.shutil.which", lambda _name: "/tmux")
    monkeypatch.setattr(
        "app.codex.manager.subprocess.run",
        MagicMock(return_value=CompletedProcess([], 0)),
    )

    manager = CodexPtyManager(settings)

    assert manager.store.get(session.id).activity == "unknown"


def test_list_clears_stale_quick_activity_without_active_task(
    settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = CodexPtyManager(settings)
    session = native_session("67676767-6767-4767-8767-676767676767")
    session.status = "stopped"
    session.activity = "working"
    session.activity_source = "quick"
    manager.store.save(session)
    manager.set_quick_interaction_checker(lambda _session_id: False)
    manager.discovery = MagicMock()
    manager.discovery.discover.return_value = [session]
    manager.discovery.session_archive_states.return_value = None
    monkeypatch.setattr("app.codex.manager.shutil.which", lambda _name: None)

    listed = manager.list_sessions()

    restored = next(item for item in listed if item.id == session.id)
    assert restored.status == "stopped"
    assert restored.activity == "idle"
    assert restored.activity_source == "none"


def test_late_quick_hook_cannot_restore_finished_activity(
    settings: Settings,
) -> None:
    manager = CodexPtyManager(settings)
    session = native_session("68686868-6868-4868-8868-686868686868")
    session.status = "stopped"
    session.activity = "idle"
    manager.store.save(session)
    manager.set_quick_interaction_checker(lambda _session_id: False)
    manager.hook_dir.mkdir(parents=True, exist_ok=True)
    (manager.hook_dir / f"{session.id}.json").write_text(
        json.dumps({
            "codex_session_id": session.codex_session_id,
            "activity": "working",
            "activity_source": "quick",
        }),
        encoding="utf-8",
    )

    manager._consume_hook_result(session.id)

    restored = manager.store.get(session.id)
    assert restored.activity == "idle"
    assert restored.activity_source == "none"


def test_restart_terminal_backend_recycles_ttyd_without_stopping_tmux(
    settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = CodexPtyManager(settings)
    session = native_session("88888888-8888-4888-8888-888888888888")
    session.status = "running"
    manager.get_session = MagicMock(return_value=session)
    manager._require_available = MagicMock()
    manager._stop_backend = MagicMock()
    manager.ensure_terminal = MagicMock(return_value=session)

    restarted = manager.restart_terminal_backend(session.id)

    manager._stop_backend.assert_called_once_with(session)
    manager.ensure_terminal.assert_called_once_with(session.id)
    assert restarted is session


@pytest.mark.parametrize(
    ("status", "initial_activity", "expected_activity"),
    [
        ("running", "idle", "idle"),
        ("running", "working", "working"),
        ("stopped", "idle", "unknown"),
    ],
)
def test_terminal_backend_reconnect_preserves_running_tmux_activity(
    settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
    status: str,
    initial_activity: str,
    expected_activity: str,
) -> None:
    manager = CodexPtyManager(settings)
    session = native_session("99999999-9999-4999-8999-999999999999")
    session.status = status
    session.activity = initial_activity
    manager.get_session = MagicMock(return_value=session)
    manager._require_available = MagicMock()
    manager._ensure_profile = MagicMock()
    manager._available_port = MagicMock(return_value=12345)
    manager._ttyd_command = MagicMock(return_value=["ttyd"])
    manager._wait_for_port = MagicMock()
    manager._running_tmux_count = MagicMock(return_value=0)
    process = MagicMock(pid=1234)
    monkeypatch.setattr(
        "app.codex.manager.subprocess.Popen",
        MagicMock(return_value=process),
    )

    result = manager.ensure_terminal(session.id)

    assert result.activity == expected_activity


@pytest.mark.parametrize(
    ("status", "expected_status"),
    [
        ("stopped", "error"),
        ("running", "running"),
    ],
)
def test_terminal_backend_failure_records_retryable_error(
    settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
    status: str,
    expected_status: str,
) -> None:
    manager = CodexPtyManager(settings)
    session = native_session("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
    session.status = status
    manager.get_session = MagicMock(return_value=session)
    manager._require_available = MagicMock()
    manager._ensure_profile = MagicMock()
    manager._available_port = MagicMock(return_value=12345)
    manager._ttyd_command = MagicMock(return_value=["ttyd"])
    manager._running_tmux_count = MagicMock(return_value=0)
    monkeypatch.setattr(
        "app.codex.manager.subprocess.Popen",
        MagicMock(side_effect=OSError("failed")),
    )

    with pytest.raises(OSError):
        manager.ensure_terminal(session.id)

    stored = manager.store.get(session.id)
    assert stored.status == expected_status
    assert stored.error == "terminal_backend_failed"


def test_refresh_preserves_terminal_error_until_retry(
    settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = CodexPtyManager(settings)
    session = native_session("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb")
    session.status = "error"
    session.error = "terminal_backend_failed"
    manager.store.save(session)
    monkeypatch.setattr("app.codex.manager.shutil.which", lambda _name: "/tmux")
    monkeypatch.setattr(
        "app.codex.manager.subprocess.run",
        MagicMock(return_value=CompletedProcess([], 1)),
    )

    manager._refresh_status(session)

    stored = manager.store.get(session.id)
    assert stored.status == "error"
    assert stored.error == "terminal_backend_failed"


def test_archive_uses_codex_cli_and_removes_mapping(
    settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = CodexPtyManager(settings)
    session = native_session("33333333-3333-4333-8333-333333333333")
    manager.store.save(session)
    monkeypatch.setattr(manager, "get_session", lambda _session_id: session)
    monkeypatch.setattr(manager, "stop_session", MagicMock())
    run = MagicMock(return_value=CompletedProcess([], 0))
    monkeypatch.setattr("app.codex.manager.subprocess.run", run)

    manager.archive_session(session.id)

    run.assert_called_once_with(
        ["codex", "archive", session.codex_session_id],
        check=False,
        stdout=-3,
        stderr=-1,
        text=True,
    )
    assert manager.store.get(session.id) is None


def test_archive_failure_preserves_mapping(
    settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = CodexPtyManager(settings)
    session = native_session("55555555-5555-4555-8555-555555555555")
    manager.store.save(session)
    monkeypatch.setattr(manager, "get_session", lambda _session_id: session)
    monkeypatch.setattr(manager, "stop_session", MagicMock())
    monkeypatch.setattr(
        "app.codex.manager.subprocess.run",
        MagicMock(return_value=CompletedProcess([], 1, stderr="failed")),
    )

    with pytest.raises(ApiError) as error:
        manager.archive_session(session.id)

    assert error.value.code == "codex_session_archive_failed"
    assert manager.store.get(session.id) is not None


def test_empty_session_cannot_be_archived(settings: Settings) -> None:
    manager = CodexPtyManager(settings)
    session = CodexSession(
        id="empty-session",
        workspace_id="chub",
        workspace_name="Chub",
        cwd=Path("/workspace/chub"),
    )
    manager.store.save(session)
    manager.get_session = MagicMock(return_value=session)

    with pytest.raises(ApiError) as error:
        manager.archive_session(session.id)

    assert error.value.code == "codex_session_not_started"
    assert manager.store.get(session.id) is not None


def test_hook_result_updates_turn_activity(settings: Settings) -> None:
    manager = CodexPtyManager(settings)
    session = native_session("66666666-6666-4666-8666-666666666666")
    session.activity = "idle"
    manager.store.save(session)
    manager.hook_dir.mkdir(parents=True)
    (manager.hook_dir / f"{session.id}.json").write_text(
        (
            '{"codex_session_id":"66666666-6666-4666-8666-666666666666",'
            '"activity":"working"}'
        ),
        encoding="utf-8",
    )

    manager._consume_hook_result(session.id)

    assert manager.store.get(session.id).activity == "working"
    assert manager.store.get(session.id).activity_source == "terminal"


@pytest.mark.parametrize(
    ("status", "expected_activity"),
    [("running", "unknown"), ("stopped", "idle")],
)
def test_recover_interrupted_quick_interaction_clears_activity_source(
    settings: Settings,
    status: str,
    expected_activity: str,
) -> None:
    manager = CodexPtyManager(settings)
    session = native_session("77777777-7777-4777-8777-777777777777")
    session.status = status
    session.activity = "working"
    session.activity_source = "quick"
    manager.store.save(session)

    manager.recover_interrupted_quick_interaction(session.id)

    recovered = manager.store.get(session.id)
    assert recovered.activity == expected_activity
    assert recovered.activity_source == "none"


def test_delete_failure_preserves_mapping(
    settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = CodexPtyManager(settings)
    session = native_session("44444444-4444-4444-8444-444444444444")
    manager.store.save(session)
    monkeypatch.setattr(manager, "get_session", lambda _session_id: session)
    monkeypatch.setattr(manager, "stop_session", MagicMock())
    monkeypatch.setattr(
        "app.codex.manager.subprocess.run",
        MagicMock(return_value=CompletedProcess([], 1, stderr="failed")),
    )

    with pytest.raises(ApiError) as error:
        manager.delete_session(session.id)

    assert error.value.code == "codex_session_delete_failed"
    assert manager.store.get(session.id) is not None


def test_running_session_permission_change_is_pending(settings: Settings) -> None:
    manager = CodexPtyManager(settings)
    session = native_session("12121212-1212-4212-8212-121212121212")
    session.status = "running"
    session.permission_mode = "ask"
    session.active_permission_mode = "ask"
    manager.store.save(session)
    manager.get_session = MagicMock(return_value=session)

    result = manager.update_permission(session.id, "auto-review")

    assert result.permission_mode == "auto-review"
    assert result.active_permission_mode == "ask"
    assert result.permission_pending is True


def test_ttyd_command_passes_session_permission(settings: Settings) -> None:
    manager = CodexPtyManager(settings)
    session = native_session("13131313-1313-4313-8313-131313131313")
    session.permission_mode = "full-access"

    command = manager._ttyd_command(session, 12345)

    permission_index = command.index("--permission-mode")
    assert command[permission_index + 1] == "full-access"


def test_permission_applies_after_stop_and_reenter(
    settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = CodexPtyManager(settings)
    session = native_session("14141414-1414-4414-8414-141414141414")
    session.status = "running"
    session.permission_mode = "ask"
    session.active_permission_mode = "ask"
    manager.store.save(session)
    manager.get_session = lambda session_id: manager.store.get(session_id)
    manager._require_available = MagicMock()
    manager._ensure_profile = MagicMock()
    manager._available_port = MagicMock(return_value=12345)
    manager._wait_for_port = MagicMock()
    manager._running_tmux_count = MagicMock(return_value=0)
    monkeypatch.setattr("app.codex.manager.shutil.which", lambda _name: None)
    process = MagicMock(pid=1234)
    monkeypatch.setattr(
        "app.codex.manager.subprocess.Popen",
        MagicMock(return_value=process),
    )

    pending = manager.update_permission(session.id, "auto-review")
    stopped = manager.stop_session(session.id)
    active = manager.ensure_terminal(session.id)

    assert pending.permission_pending is True
    assert stopped.active_permission_mode is None
    assert active.permission_mode == "auto-review"
    assert active.active_permission_mode == "auto-review"
    assert manager._ttyd_command(active, 12345)[-2:] == [
        "--codex-session",
        session.codex_session_id,
    ]


@pytest.mark.parametrize("activity", ["idle", "working", "unknown"])
def test_permission_change_stops_running_session(
    settings: Settings,
    activity: str,
) -> None:
    manager = CodexPtyManager(settings)
    session = native_session("17171717-1717-4717-8717-171717171717")
    session.status = "running"
    session.activity = activity
    session.permission_mode = "ask"
    session.active_permission_mode = "ask"
    manager.store.save(session)
    manager.get_session = MagicMock(return_value=session)
    stopped = session.model_copy(
        update={
            "status": "stopped",
            "permission_mode": "full-access",
            "active_permission_mode": None,
        }
    )
    def stop_and_save(_session_id: str) -> SessionInfo:
        manager.store.save(stopped)
        return manager._public(stopped)

    manager.stop_session = MagicMock(side_effect=stop_and_save)

    result, auto_stopped = manager.update_permission_and_stop(
        session.id,
        "full-access",
    )

    assert auto_stopped is True
    assert result.status == "stopped"
    manager.stop_session.assert_called_once_with(session.id)


def test_permission_change_does_not_stop_when_mode_is_unchanged(
    settings: Settings,
) -> None:
    manager = CodexPtyManager(settings)
    session = native_session("18181818-1818-4818-8818-181818181818")
    session.status = "running"
    session.activity = "working"
    session.permission_mode = "ask"
    session.active_permission_mode = "ask"
    manager.get_session = MagicMock(return_value=session)
    manager.stop_session = MagicMock()

    result, auto_stopped = manager.update_permission_and_stop(
        session.id,
        "ask",
    )

    assert auto_stopped is False
    assert result.permission_pending is False
    manager.stop_session.assert_not_called()


def test_permission_change_keeps_old_permission_when_stop_fails(
    settings: Settings,
) -> None:
    manager = CodexPtyManager(settings)
    session = native_session("19191919-1919-4919-8919-191919191919")
    session.status = "running"
    session.permission_mode = "ask"
    session.active_permission_mode = "ask"
    manager.store.save(session)
    manager.get_session = MagicMock(return_value=session)
    manager.stop_session = MagicMock(side_effect=ApiError(
        503,
        "codex_session_stop_failed",
        "Codex session is still running",
    ))

    with pytest.raises(ApiError):
        manager.update_permission_and_stop(session.id, "full-access")

    saved = manager.store.get(session.id)
    assert saved.permission_mode == "ask"
    assert saved.status == "running"


def test_sync_adopts_permission_changed_inside_codex(settings: Settings) -> None:
    manager = CodexPtyManager(settings)
    session = native_session("15151515-1515-4515-8515-151515151515")
    session.status = "running"
    session.permission_mode = "ask"
    session.active_permission_mode = "ask"
    manager.store.save(session)
    discovered = session.model_copy(
        update={
            "status": "stopped",
            "permission_mode": "ask",
            "active_permission_mode": "auto-review",
        }
    )
    manager.discovery = MagicMock()
    manager.discovery.discover.return_value = [discovered]
    manager.discovery.session_archive_states.return_value = None

    manager._sync_native_sessions()

    synced = manager.store.get(session.id)
    assert synced.permission_mode == "auto-review"
    assert synced.active_permission_mode == "auto-review"


def test_sync_does_not_overwrite_stopped_session_permission(
    settings: Settings,
) -> None:
    manager = CodexPtyManager(settings)
    session = native_session("16161616-1616-4616-8616-161616161616")
    session.permission_mode = "ask"
    session.active_permission_mode = None
    manager.store.save(session)
    discovered = session.model_copy(
        update={
            "permission_mode": "ask",
            "active_permission_mode": "full-access",
        }
    )
    manager.discovery = MagicMock()
    manager.discovery.discover.return_value = [discovered]
    manager.discovery.session_archive_states.return_value = None

    manager._sync_native_sessions()

    synced = manager.store.get(session.id)
    assert synced.permission_mode == "ask"
    assert synced.active_permission_mode is None
