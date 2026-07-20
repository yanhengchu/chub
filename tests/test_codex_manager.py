from pathlib import Path
from subprocess import CompletedProcess
from unittest.mock import MagicMock

import pytest

from app.codex.manager import CodexPtyManager
from app.codex.models import CodexSession
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
