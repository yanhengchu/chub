from __future__ import annotations

import json
import os
import threading
from pathlib import Path

from app.codex.models import CodexSession


class CodexSessionStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = threading.RLock()
        self._sessions: dict[str, CodexSession] = {}
        self._load()

    def _load(self) -> None:
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return
        except (OSError, json.JSONDecodeError, ValueError):
            return
        if not isinstance(payload, list):
            return
        for item in payload:
            try:
                session = CodexSession.model_validate(item)
            except ValueError:
                continue
            self._sessions[session.id] = session

    def list(self) -> list[CodexSession]:
        with self._lock:
            return [
                session.model_copy(deep=True)
                for session in sorted(
                    self._sessions.values(),
                    key=lambda item: item.updated_at,
                    reverse=True,
                )
            ]

    def get(self, session_id: str) -> CodexSession | None:
        with self._lock:
            session = self._sessions.get(session_id)
            return session.model_copy(deep=True) if session else None

    def save(self, session: CodexSession) -> None:
        with self._lock:
            self._sessions[session.id] = session.model_copy(deep=True)
            self._write()

    def delete(self, session_id: str) -> None:
        with self._lock:
            self._sessions.pop(session_id, None)
            self._write()

    def _write(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(f"{self.path.suffix}.tmp")
        temporary.write_text(
            json.dumps(
                [
                    session.model_dump(mode="json")
                    for session in self._sessions.values()
                ],
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        os.chmod(temporary, 0o600)
        temporary.replace(self.path)
