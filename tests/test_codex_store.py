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
