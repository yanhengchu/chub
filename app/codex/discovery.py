from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

from app.codex.models import CodexSession, PermissionMode


PERMISSION_TAIL_BYTES = 512 * 1024
PERMISSION_MAX_LINE_BYTES = 256 * 1024


class CodexSessionDiscovery:
    def __init__(self, codex_home: Path) -> None:
        self.codex_home = codex_home

    def discover(self) -> list[CodexSession]:
        titles = self._read_titles()
        sessions: list[CodexSession] = []
        root = self.codex_home / "sessions"
        for path in root.glob("**/*.jsonl"):
            session = self._read_session(path, titles)
            if session is not None:
                sessions.append(session)
        return sorted(sessions, key=lambda item: item.updated_at, reverse=True)

    def session_archive_states(self) -> dict[str, bool] | None:
        database = self._state_database()
        if database is None:
            return None
        try:
            connection = sqlite3.connect(f"{database.as_uri()}?mode=ro", uri=True)
            try:
                rows = connection.execute(
                    "SELECT id, archived FROM threads"
                ).fetchall()
            finally:
                connection.close()
        except (OSError, sqlite3.Error):
            return None
        return {
            session_id: bool(archived)
            for session_id, archived in rows
            if isinstance(session_id, str)
        }

    def _read_session(
        self,
        path: Path,
        titles: dict[str, str],
    ) -> CodexSession | None:
        try:
            with path.open(encoding="utf-8") as file:
                first = json.loads(file.readline())
            payload = first["payload"]
            session_id = payload.get("id") or payload.get("session_id")
            if not isinstance(session_id, str):
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
            ValueError,
            json.JSONDecodeError,
        ):
            return None
        thread_settings = self._read_thread_settings(path)
        active_model, active_reasoning_effort = self._model_settings(
            thread_settings
        )
        return CodexSession(
            id=session_id,
            workspace_id="codex",
            workspace_name=cwd.name or str(cwd),
            cwd=cwd,
            title=titles.get(session_id),
            codex_session_id=session_id,
            status="stopped",
            active_permission_mode=self._permission_mode_from_settings(
                thread_settings
            ),
            model=active_model,
            reasoning_effort=active_reasoning_effort,
            active_model=active_model,
            active_reasoning_effort=active_reasoning_effort,
            created_at=timestamp,
            updated_at=updated_at,
        )

    def _read_thread_settings(self, path: Path) -> dict[str, object]:
        try:
            with path.open("rb") as file:
                size = file.seek(0, 2)
                start = max(0, size - PERMISSION_TAIL_BYTES)
                file.seek(start)
                data = file.read(PERMISSION_TAIL_BYTES)
        except OSError:
            return {}
        lines = data.splitlines()
        if start and lines:
            lines = lines[1:]
        resolved: dict[str, object] = {}
        for raw_line in reversed(lines):
            if len(raw_line) > PERMISSION_MAX_LINE_BYTES:
                continue
            try:
                item = json.loads(raw_line)
            except (UnicodeDecodeError, json.JSONDecodeError):
                continue
            settings = self._permission_settings(item)
            if settings is None:
                continue
            for key, value in settings.items():
                canonical_key = (
                    "reasoning_effort"
                    if key in {"effort", "reasoning_effort"}
                    else key
                )
                resolved.setdefault(canonical_key, value)
        return resolved

    @staticmethod
    def _model_settings(
        settings: dict[str, object],
    ) -> tuple[str | None, str | None]:
        model = settings.get("model")
        effort = settings.get("reasoning_effort")
        return (
            model if isinstance(model, str) and 0 < len(model) <= 128 else None,
            effort if isinstance(effort, str) and 0 < len(effort) <= 32 else None,
        )

    @staticmethod
    def _permission_settings(item: object) -> dict[str, object] | None:
        if not isinstance(item, dict):
            return None
        payload = item.get("payload")
        if not isinstance(payload, dict):
            return None
        if (
            item.get("type") == "event_msg"
            and payload.get("type") == "thread_settings_applied"
            and isinstance(payload.get("thread_settings"), dict)
        ):
            return payload["thread_settings"]
        if item.get("type") == "turn_context":
            return payload
        return None

    @staticmethod
    def _permission_mode_from_settings(
        settings: dict[str, object],
    ) -> PermissionMode | None:
        active = settings.get("active_permission_profile")
        profile_id = active.get("id") if isinstance(active, dict) else None
        sandbox = settings.get("sandbox_policy")
        sandbox_type = sandbox.get("type") if isinstance(sandbox, dict) else None
        if profile_id == ":danger-full-access" or sandbox_type == "danger-full-access":
            return "full-access"
        if profile_id == ":read-only" or sandbox_type == "read-only":
            return "read-only"
        reviewer = settings.get("approvals_reviewer")
        if profile_id == ":workspace" or sandbox_type == "workspace-write":
            return "auto-review" if reviewer == "auto_review" else "ask"
        if settings.get("approval_policy") == "never":
            return "full-access"
        return None

    def _read_titles(self) -> dict[str, str]:
        titles = self._read_database_titles()
        path = self.codex_home / "session_index.jsonl"
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except OSError:
            return titles
        for line in lines:
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                continue
            session_id = item.get("id")
            title = item.get("thread_name")
            if (
                isinstance(session_id, str)
                and session_id not in titles
                and isinstance(title, str)
                and title.strip()
            ):
                titles[session_id] = title.strip()
        return titles

    def _read_database_titles(self) -> dict[str, str]:
        database = self._state_database()
        if database is None:
            return {}
        try:
            connection = sqlite3.connect(f"{database.as_uri()}?mode=ro", uri=True)
            try:
                rows = connection.execute(
                    "SELECT id, title FROM threads WHERE archived = 0"
                ).fetchall()
            finally:
                connection.close()
        except (OSError, sqlite3.Error):
            return {}
        return {
            session_id: title.strip()
            for session_id, title in rows
            if isinstance(session_id, str)
            and isinstance(title, str)
            and title.strip()
        }

    def _state_database(self) -> Path | None:
        candidates = (
            self.codex_home / "state_5.sqlite",
            self.codex_home / "sqlite" / "state_5.sqlite",
        )
        return next((path for path in candidates if path.is_file()), None)
