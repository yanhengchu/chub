import json
import sqlite3
from pathlib import Path

from app.codex.discovery import CodexSessionDiscovery


def write_jsonl(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload) + "\n", encoding="utf-8")


def test_discovers_all_active_sessions_regardless_of_origin(tmp_path: Path) -> None:
    codex_home = tmp_path / ".codex"
    first_id = "11111111-1111-4111-8111-111111111111"
    second_id = "22222222-2222-4222-8222-222222222222"
    for session_id, cwd in (
        (first_id, "/workspace/one"),
        (second_id, "/workspace/two"),
    ):
        write_jsonl(
            codex_home / "sessions" / f"{session_id}.jsonl",
            {
                "type": "session_meta",
                "payload": {
                    "id": session_id,
                    "cwd": cwd,
                    "timestamp": "2026-07-20T08:00:00Z",
                },
            },
        )
    write_jsonl(
        codex_home / "session_index.jsonl",
        {"id": first_id, "thread_name": "已有会话"},
    )

    sessions = CodexSessionDiscovery(codex_home).discover()

    assert {session.id for session in sessions} == {first_id, second_id}
    assert next(session for session in sessions if session.id == first_id).title == "已有会话"
    assert all(session.codex_session_id == session.id for session in sessions)


def test_ignores_archived_and_malformed_sessions(tmp_path: Path) -> None:
    codex_home = tmp_path / ".codex"
    write_jsonl(
        codex_home / "archived_sessions" / "archived.jsonl",
        {
            "type": "session_meta",
            "payload": {
                "id": "33333333-3333-4333-8333-333333333333",
                "cwd": "/workspace/archived",
                "timestamp": "2026-07-20T08:00:00Z",
            },
        },
    )
    write_jsonl(codex_home / "sessions" / "broken.jsonl", {"payload": {}})

    assert CodexSessionDiscovery(codex_home).discover() == []


def test_prefers_active_title_from_codex_state_database(tmp_path: Path) -> None:
    codex_home = tmp_path / ".codex"
    session_id = "44444444-4444-4444-8444-444444444444"
    write_jsonl(
        codex_home / "sessions" / f"{session_id}.jsonl",
        {
            "type": "session_meta",
            "payload": {
                "id": session_id,
                "cwd": "/workspace/chub",
                "timestamp": "2026-07-20T08:00:00Z",
            },
        },
    )
    write_jsonl(
        codex_home / "session_index.jsonl",
        {"id": session_id, "thread_name": "旧索引标题"},
    )
    database = codex_home / "state_5.sqlite"
    with sqlite3.connect(database) as connection:
        connection.execute(
            "CREATE TABLE threads (id TEXT, title TEXT, archived INTEGER)"
        )
        connection.execute(
            "INSERT INTO threads VALUES (?, ?, 0)",
            (session_id, "Codex 真实标题"),
        )

    sessions = CodexSessionDiscovery(codex_home).discover()

    assert sessions[0].title == "Codex 真实标题"
    assert CodexSessionDiscovery(codex_home).session_archive_states() == {
        session_id: False
    }
