from datetime import UTC, datetime, timedelta
from pathlib import Path

from app.codex.models import CodexSession
from app.codex.store import CodexSessionStore


def test_session_store_persists_and_removes_sessions(tmp_path: Path) -> None:
    path = tmp_path / "sessions.json"
    store = CodexSessionStore(path)
    session = CodexSession(
        id="session-1",
        workspace_id="chub",
        workspace_name="Chub",
        cwd=tmp_path,
    )

    store.save(session)

    loaded = CodexSessionStore(path).get("session-1")
    assert loaded is not None
    assert loaded.cwd == tmp_path
    assert path.stat().st_mode & 0o777 == 0o600

    store.delete("session-1")
    assert CodexSessionStore(path).get("session-1") is None


def test_session_store_ignores_invalid_file(tmp_path: Path) -> None:
    path = tmp_path / "sessions.json"
    path.write_text("not-json", encoding="utf-8")

    assert CodexSessionStore(path).list() == []


def test_session_store_migrates_legacy_permission_modes(tmp_path: Path) -> None:
    path = tmp_path / "sessions.json"
    path.write_text(
        (
            '[{"id":"session-1","workspace_id":"chub","workspace_name":"Chub",'
            f'"cwd":"{tmp_path}","permission_mode":"workspace-write",'
            '"active_permission_mode":"read-only"}]'
        ),
        encoding="utf-8",
    )

    session = CodexSessionStore(path).get("session-1")

    assert session is not None
    assert session.permission_mode == "ask"
    assert session.active_permission_mode == "read-only"
    assert session.model is None
    assert session.reasoning_effort is None


def test_session_store_lists_newest_created_first_without_activity_reordering(
    tmp_path: Path,
) -> None:
    path = tmp_path / "sessions.json"
    store = CodexSessionStore(path)
    created_at = datetime(2026, 8, 14, tzinfo=UTC)
    older = CodexSession(
        id="older",
        workspace_id="chub",
        workspace_name="Chub",
        cwd=tmp_path,
        created_at=created_at,
        updated_at=created_at + timedelta(hours=3),
    )
    newer = older.model_copy(
        update={
            "id": "newer",
            "created_at": created_at + timedelta(hours=1),
            "updated_at": created_at + timedelta(hours=1),
        }
    )
    store.save(older)
    store.save(newer)

    assert [session.id for session in store.list()] == ["newer", "older"]
