from __future__ import annotations

import json
import os
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

from .models import CodexSession, sessions_newest_first


MAX_DISCOVERY_LINE_BYTES = 256 * 1024
MAX_DISCOVERED_TITLE_CHARS = 500


class CodexSessionDiscovery:
    def __init__(self, codex_home: Path) -> None:
        self.codex_home = codex_home
        self.last_discovery_complete = True

    def discover(self) -> list[CodexSession]:
        titles = self._read_titles()
        sessions: list[CodexSession] = []
        root = self.codex_home / "sessions"
        self.last_discovery_complete = True
        if root.exists() and (
            not root.is_dir() or not os.access(root, os.R_OK | os.X_OK)
        ):
            raise OSError("Codex sessions source is unavailable")
        if not root.is_dir():
            self.last_discovery_complete = False
            return []
        for path in root.glob("**/*.jsonl"):
            session = self._read_session(path, titles)
            if session is not None:
                sessions.append(session)
        return sessions_newest_first(sessions)

    def session_archive_states(self) -> dict[str, bool] | None:
        database = self._state_database()
        if database is None:
            return None
        try:
            connection = sqlite3.connect(f"{database.as_uri()}?mode=ro", uri=True)
            try:
                states: dict[str, bool] = {}
                for session_id, archived in connection.execute(
                    "SELECT id, archived FROM threads"
                ):
                    if isinstance(session_id, str):
                        states[session_id] = bool(archived)
            finally:
                connection.close()
        except (OSError, ValueError, sqlite3.Error):
            return None
        return states

    def _read_session(
        self,
        path: Path,
        titles: dict[str, str],
    ) -> CodexSession | None:
        try:
            with path.open("rb") as file:
                first_line = file.readline(MAX_DISCOVERY_LINE_BYTES + 1)
            if len(first_line) > MAX_DISCOVERY_LINE_BYTES:
                self.last_discovery_complete = False
                return None
            first = json.loads(first_line.decode("utf-8"))
            payload = first["payload"]
            session_id = payload.get("id") or payload.get("session_id")
            if not isinstance(session_id, str):
                self.last_discovery_complete = False
                return None
            UUID(session_id)
            cwd = Path(payload["cwd"]).expanduser()
            timestamp = datetime.fromisoformat(payload["timestamp"].replace("Z", "+00:00"))
            if timestamp.tzinfo is None:
                timestamp = timestamp.replace(tzinfo=UTC)
            updated_at = datetime.fromtimestamp(path.stat().st_mtime, UTC)
        except (
            FileNotFoundError,
            KeyError,
            OSError,
            TypeError,
            UnicodeDecodeError,
            ValueError,
            json.JSONDecodeError,
        ):
            self.last_discovery_complete = False
            return None
        return CodexSession(
            id=session_id,
            workspace_id="codex",
            workspace_name=cwd.name or str(cwd),
            cwd=cwd,
            title=titles.get(session_id),
            codex_session_id=session_id,
            status="stopped",
            created_at=timestamp,
            updated_at=updated_at,
        )

    def _read_titles(self) -> dict[str, str]:
        titles = self._read_database_titles()
        path = self.codex_home / "session_index.jsonl"
        try:
            with path.open("rb") as file:
                while raw_line := file.readline(MAX_DISCOVERY_LINE_BYTES + 1):
                    if len(raw_line) > MAX_DISCOVERY_LINE_BYTES:
                        while raw_line and not raw_line.endswith(b"\n"):
                            raw_line = file.readline(MAX_DISCOVERY_LINE_BYTES + 1)
                        continue
                    try:
                        item = json.loads(raw_line.decode("utf-8"))
                    except (UnicodeDecodeError, json.JSONDecodeError):
                        continue
                    if not isinstance(item, dict):
                        continue
                    session_id = item.get("id")
                    title = self._title_value(item.get("thread_name"))
                    if (
                        isinstance(session_id, str)
                        and session_id not in titles
                        and title is not None
                    ):
                        titles[session_id] = title
        except OSError:
            return titles
        return titles

    def _read_database_titles(self) -> dict[str, str]:
        database = self._state_database()
        if database is None:
            return {}
        try:
            connection = sqlite3.connect(f"{database.as_uri()}?mode=ro", uri=True)
            try:
                titles: dict[str, str] = {}
                for session_id, raw_title in connection.execute(
                    "SELECT id, title FROM threads WHERE archived = 0"
                ):
                    title = self._title_value(raw_title)
                    if isinstance(session_id, str) and title is not None:
                        titles[session_id] = title
            finally:
                connection.close()
        except (OSError, ValueError, sqlite3.Error):
            return {}
        return titles

    @staticmethod
    def _title_value(value: object) -> str | None:
        if not isinstance(value, str):
            return None
        title = value[: MAX_DISCOVERED_TITLE_CHARS + 1].strip()
        return title[:MAX_DISCOVERED_TITLE_CHARS] or None

    def _state_database(self) -> Path | None:
        candidates = (
            self.codex_home / "state_5.sqlite",
            self.codex_home / "sqlite" / "state_5.sqlite",
        )
        return next((path for path in candidates if path.is_file()), None)
