from __future__ import annotations

import json
import os
import stat
import threading
from pathlib import Path

from pydantic import ValidationError

from app.ai_session.models import AiSession, AiSessionState, sessions_newest_first, utc_now


MAX_AI_SESSION_STORE_BYTES = 512 * 1024


class AiSessionStoreUnavailable(OSError):
    """The current-format Session state cannot be used safely."""


class AiSessionStore:
    """Strict, current-format-only storage for Chub logical Sessions."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = threading.RLock()
        self._sessions: dict[str, AiSession] = {}
        self._load_error: str | None = None
        self._load()

    @property
    def available(self) -> bool:
        return self._load_error is None

    @property
    def unavailable_reason(self) -> str | None:
        return self._load_error

    def _load(self) -> None:
        try:
            payload = self._read_payload()
        except FileNotFoundError:
            return
        except AiSessionStoreUnavailable as exc:
            self._load_error = str(exc)
            return
        try:
            self._sessions = self._parse_payload(payload)
        except AiSessionStoreUnavailable as exc:
            self._load_error = str(exc)

    @staticmethod
    def _parse_payload(payload: object) -> dict[str, AiSession]:
        try:
            state = AiSessionState.model_validate(payload)
        except ValidationError as exc:
            raise AiSessionStoreUnavailable("AI Session 状态文件格式无效。") from exc
        sessions: dict[str, AiSession] = {}
        native_ids: set[tuple[str, str]] = set()
        for session in state.sessions:
            if session.id in sessions:
                raise AiSessionStoreUnavailable("AI Session 状态文件包含重复 Session。")
            if session.native_session_id is not None:
                native_key = (session.runtime_id, session.native_session_id)
                if native_key in native_ids:
                    raise AiSessionStoreUnavailable(
                        "AI Session 状态文件包含冲突的 Runtime 映射。"
                    )
                native_ids.add(native_key)
            sessions[session.id] = session
        return sessions

    def _read_payload(self) -> object:
        try:
            descriptor = os.open(
                self.path,
                os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0),
            )
        except FileNotFoundError:
            raise
        except OSError as exc:
            raise AiSessionStoreUnavailable("AI Session 状态文件无法安全读取。") from exc
        try:
            metadata = os.fstat(descriptor)
            if (
                not stat.S_ISREG(metadata.st_mode)
                or metadata.st_uid != os.getuid()
                or stat.S_IMODE(metadata.st_mode) & 0o077
                or metadata.st_size > MAX_AI_SESSION_STORE_BYTES
            ):
                raise AiSessionStoreUnavailable(
                    "AI Session 状态文件的类型、所有者、权限或大小不安全。"
                )
            with os.fdopen(descriptor, "rb") as state_file:
                descriptor = -1
                content = state_file.read(MAX_AI_SESSION_STORE_BYTES + 1)
            if len(content) > MAX_AI_SESSION_STORE_BYTES:
                raise AiSessionStoreUnavailable("AI Session 状态文件大小超过固定上限。")
        except OSError as exc:
            if isinstance(exc, AiSessionStoreUnavailable):
                raise
            raise AiSessionStoreUnavailable("AI Session 状态文件无法读取。") from exc
        finally:
            if descriptor >= 0:
                os.close(descriptor)
        try:
            return json.loads(content)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise AiSessionStoreUnavailable("AI Session 状态文件格式无效。") from exc

    def _require_available(self) -> None:
        if self._load_error is not None:
            raise AiSessionStoreUnavailable(self._load_error)

    def _assert_on_disk_matches_memory(self) -> None:
        try:
            payload = self._read_payload()
        except FileNotFoundError:
            if self._sessions:
                raise AiSessionStoreUnavailable(
                    "AI Session 状态文件与当前内存状态不一致。"
                )
            return
        sessions = self._parse_payload(payload)
        current = {
            session_id: session.model_dump(mode="json")
            for session_id, session in self._sessions.items()
        }
        validated = {
            session_id: session.model_dump(mode="json")
            for session_id, session in sessions.items()
        }
        if current != validated:
            raise AiSessionStoreUnavailable(
                "AI Session 状态文件与当前内存状态不一致。"
            )

    def list(self) -> list[AiSession]:
        with self._lock:
            self._require_available()
            self._assert_on_disk_matches_memory()
            return [
                item.model_copy(deep=True)
                for item in sessions_newest_first(self._sessions.values())
            ]

    def get(self, session_id: str) -> AiSession | None:
        with self._lock:
            self._require_available()
            self._assert_on_disk_matches_memory()
            session = self._sessions.get(session_id)
            return session.model_copy(deep=True) if session is not None else None

    def validate_for_system_upgrade(self) -> list[AiSession]:
        """Re-read the on-disk state before any destructive upgrade action."""
        with self._lock:
            self._require_available()
            self._assert_on_disk_matches_memory()
            sessions = self._sessions
            return [
                item.model_copy(deep=True)
                for item in sessions_newest_first(sessions.values())
            ]

    def save(self, session: AiSession) -> None:
        with self._lock:
            self._require_available()
            self._assert_on_disk_matches_memory()
            existing = self._sessions.get(session.id)
            for candidate_id, candidate in self._sessions.items():
                if candidate_id == session.id:
                    continue
                if (
                    session.native_session_id is not None
                    and candidate.runtime_id == session.runtime_id
                    and candidate.native_session_id == session.native_session_id
                ):
                    raise AiSessionStoreUnavailable("AI Session Runtime 映射已被占用。")
            self._sessions[session.id] = session.model_copy(deep=True)
            try:
                self._write()
            except Exception:
                if existing is None:
                    self._sessions.pop(session.id, None)
                else:
                    self._sessions[session.id] = existing
                raise

    def bind_native_session(
        self,
        session_id: str,
        native_session_id: str,
        runtime_id: str,
    ) -> list[AiSession]:
        """Atomically bind a logical Session and remove discovery-only duplicates."""
        with self._lock:
            self._require_available()
            self._assert_on_disk_matches_memory()
            session = self._sessions.get(session_id)
            if session is None:
                raise AiSessionStoreUnavailable("AI Session 不存在。")
            if session.runtime_id != runtime_id:
                raise AiSessionStoreUnavailable("AI Session Runtime 映射不一致。")
            if session.native_session_id not in {None, native_session_id}:
                raise AiSessionStoreUnavailable("AI Session Runtime 映射不一致。")

            duplicates = [
                candidate
                for candidate_id, candidate in self._sessions.items()
                if candidate_id != session_id
                and candidate.runtime_id == runtime_id
                and candidate.native_session_id == native_session_id
            ]
            if any(
                not candidate.discovered
                for candidate in duplicates
            ):
                raise AiSessionStoreUnavailable("AI Session Runtime 映射已被占用。")

            before = dict(self._sessions)
            updated = session.model_copy(deep=True)
            updated.native_session_id = native_session_id
            updated.updated_at = utc_now()
            self._sessions[session_id] = updated
            for duplicate in duplicates:
                self._sessions.pop(duplicate.id, None)
            try:
                self._write()
            except Exception:
                self._sessions = before
                raise
            return [item.model_copy(deep=True) for item in duplicates]

    def delete(self, session_id: str) -> None:
        with self._lock:
            self._require_available()
            self._assert_on_disk_matches_memory()
            existing = self._sessions.pop(session_id, None)
            if existing is None:
                return
            try:
                self._write()
            except Exception:
                self._sessions[session_id] = existing
                raise

    def _write(self) -> None:
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        temporary = self.path.with_name(f".{self.path.name}.tmp")
        flags = (
            os.O_WRONLY
            | os.O_CREAT
            | os.O_TRUNC
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0)
        )
        descriptor = os.open(temporary, flags, 0o600)
        try:
            os.fchmod(descriptor, 0o600)
            payload = AiSessionState(
                sessions=[session for session in self._sessions.values()]
            ).model_dump(mode="json")
            content = (json.dumps(payload, ensure_ascii=False, indent=2) + "\n").encode(
                "utf-8"
            )
            if len(content) > MAX_AI_SESSION_STORE_BYTES:
                raise AiSessionStoreUnavailable("AI Session 状态超过固定大小上限。")
            with os.fdopen(descriptor, "wb") as state_file:
                descriptor = -1
                state_file.write(content)
                state_file.flush()
                os.fsync(state_file.fileno())
            os.replace(temporary, self.path)
            os.chmod(self.path, 0o600)
        finally:
            if descriptor >= 0:
                os.close(descriptor)
            try:
                temporary.unlink()
            except FileNotFoundError:
                pass
