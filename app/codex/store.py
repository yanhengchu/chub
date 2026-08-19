"""Legacy Codex-only Session store retained for regression fixtures.

The production store is ``app.ai_session.store.AiSessionStore``.  Do not add
new application imports here or use this format for new runtime state.
"""

from __future__ import annotations

import json
import os
import stat
import threading
from pathlib import Path

from app.codex.models import CodexSession, sessions_newest_first


MAX_SESSION_STORE_BYTES = 4 * 1024 * 1024


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
                for session in sessions_newest_first(self._sessions.values())
            ]

    def validate_for_system_upgrade(self) -> list[CodexSession]:
        """Strictly validate the current-format store before destructive cleanup."""
        with self._lock:
            try:
                descriptor = os.open(
                    self.path,
                    os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
                )
            except FileNotFoundError:
                if self._sessions:
                    raise OSError("Session 状态文件与当前内存状态不一致。")
                return []
            except OSError as exc:
                raise OSError("Session 状态文件无法读取。") from exc
            try:
                try:
                    metadata = os.fstat(descriptor)
                except OSError as exc:
                    raise OSError("Session 状态文件无法读取。") from exc
                if (
                    not stat.S_ISREG(metadata.st_mode)
                    or metadata.st_uid != os.getuid()
                    or stat.S_IMODE(metadata.st_mode) & 0o077
                    or metadata.st_size > MAX_SESSION_STORE_BYTES
                ):
                    raise OSError(
                        "Session 状态文件的类型、所有者、权限或大小不安全。"
                    )
                try:
                    with os.fdopen(descriptor, "rb") as state_file:
                        descriptor = -1
                        content = state_file.read(MAX_SESSION_STORE_BYTES + 1)
                except OSError as exc:
                    raise OSError("Session 状态文件无法读取。") from exc
                if len(content) > MAX_SESSION_STORE_BYTES:
                    raise OSError("Session 状态文件大小超过固定上限。")
            finally:
                if descriptor >= 0:
                    os.close(descriptor)
            try:
                payload = json.loads(content)
            except (UnicodeError, json.JSONDecodeError, ValueError) as exc:
                raise OSError("Session 状态文件格式无效。") from exc
            if not isinstance(payload, list):
                raise OSError("Session 状态文件格式无效。")

            sessions: dict[str, CodexSession] = {}
            for item in payload:
                try:
                    session = CodexSession.model_validate(item)
                except ValueError as exc:
                    raise OSError("Session 状态文件包含无效记录。") from exc
                if session.id in sessions:
                    raise OSError("Session 状态文件包含重复记录。")
                sessions[session.id] = session

            current = {
                session_id: session.model_dump(mode="json")
                for session_id, session in self._sessions.items()
            }
            validated = {
                session_id: session.model_dump(mode="json")
                for session_id, session in sessions.items()
            }
            if validated != current:
                raise OSError("Session 状态文件与当前内存状态不一致。")
            return [
                session.model_copy(deep=True)
                for session in sessions_newest_first(sessions.values())
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

    def discard_after_system_upgrade(self) -> None:
        """Remove an empty legacy Store after a controlled reset."""
        with self._lock:
            if self.validate_for_system_upgrade():
                raise OSError("旧 Session 状态文件仍包含未清理记录。")
            try:
                self.path.unlink()
            except FileNotFoundError:
                pass
            except OSError as exc:
                raise OSError("旧 Session 状态文件无法安全清理。") from exc
            self._sessions = {}

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
