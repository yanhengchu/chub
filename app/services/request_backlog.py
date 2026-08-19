from __future__ import annotations

import hashlib
import json
import os
import stat
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator, Literal
from uuid import uuid4

import fcntl
from pydantic import BaseModel, ConfigDict, Field, ValidationError


MAX_REQUEST_SLOTS = 9
MAX_REQUEST_TITLE_CHARS = 48
MAX_REQUEST_CONTENT_CHARS = 2_000
MAX_ARCHIVED_REQUESTS = 100
MAX_REQUEST_STATE_BYTES = 512 * 1024


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class RequestBacklogError(RuntimeError):
    pass


class RequestBacklogUnavailable(RequestBacklogError):
    pass


class RequestBacklogNotFound(RequestBacklogError):
    pass


class RequestBacklogBusy(RequestBacklogError):
    pass


class RequestBacklogFull(RequestBacklogError):
    pass


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


RequestStatus = Literal["ready", "running", "succeeded", "failed"]


class RequestBacklogItem(_StrictModel):
    slot: int = Field(ge=1, le=MAX_REQUEST_SLOTS)
    generation: str = Field(min_length=32, max_length=32)
    title: str = Field(min_length=1, max_length=MAX_REQUEST_TITLE_CHARS)
    content: str = Field(min_length=1, max_length=MAX_REQUEST_CONTENT_CHARS)
    status: RequestStatus = "ready"
    active_run_id: str | None = Field(default=None, min_length=32, max_length=32)
    active_message_id: str | None = Field(default=None, min_length=1, max_length=500)
    active_task_id: str | None = Field(default=None, min_length=1, max_length=128)
    last_task_id: str | None = Field(default=None, min_length=1, max_length=128)
    last_error: str | None = Field(default=None, max_length=500)
    created_at: datetime
    updated_at: datetime
    finished_at: datetime | None = None


class ArchivedRequestBacklogItem(RequestBacklogItem):
    archived_at: datetime


class RequestBacklogState(_StrictModel):
    version: Literal[1] = 1
    active: list[RequestBacklogItem] = Field(default_factory=list)
    archived: list[ArchivedRequestBacklogItem] = Field(default_factory=list)


class RequestBacklogStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.lock_path = path.with_name(f".{path.name}.lock")
        self._thread_lock = threading.RLock()

    def list_active(self) -> tuple[RequestBacklogItem, ...]:
        with self._locked():
            state = self._read_unlocked()
        return tuple(item.model_copy(deep=True) for item in sorted(
            state.active, key=lambda item: item.slot
        ))

    def get(self, slot: int) -> RequestBacklogItem:
        self._validate_slot(slot)
        with self._locked():
            item = self._find(self._read_unlocked(), slot)
        return item.model_copy(deep=True)

    def save(self, *, title: str, content: str) -> RequestBacklogItem:
        resolved_title = self._normalize_title(title)
        resolved_content = self._normalize_content(content)
        with self._locked():
            state = self._read_unlocked()
            occupied = {item.slot for item in state.active}
            slot = next(
                (candidate for candidate in range(1, MAX_REQUEST_SLOTS + 1)
                 if candidate not in occupied),
                None,
            )
            if slot is None:
                raise RequestBacklogFull("All request slots are occupied")
            now = utc_now()
            item = RequestBacklogItem(
                slot=slot,
                generation=uuid4().hex,
                title=resolved_title,
                content=resolved_content,
                created_at=now,
                updated_at=now,
            )
            state.active.append(item)
            self._write_unlocked(state)
        return item.model_copy(deep=True)

    def update(
        self,
        slot: int,
        *,
        content: str,
        title: str | None = None,
    ) -> RequestBacklogItem:
        self._validate_slot(slot)
        resolved_content = self._normalize_content(content)
        resolved_title = self._normalize_title(title) if title is not None else None
        with self._locked():
            state = self._read_unlocked()
            item = self._find(state, slot)
            if item.status == "running":
                raise RequestBacklogBusy(f"Request R{slot} is running")
            item.generation = uuid4().hex
            item.content = resolved_content
            if resolved_title is not None:
                item.title = resolved_title
            item.status = "ready"
            item.active_run_id = None
            item.active_message_id = None
            item.active_task_id = None
            item.last_task_id = None
            item.last_error = None
            item.finished_at = None
            item.updated_at = utc_now()
            self._write_unlocked(state)
        return item.model_copy(deep=True)

    def claim_run(self, slot: int, message_id: str) -> RequestBacklogItem:
        self._validate_slot(slot)
        if not message_id or len(message_id) > 500:
            raise ValueError("Message ID is invalid")
        with self._locked():
            state = self._read_unlocked()
            item = self._find(state, slot)
            if item.status == "running":
                if item.active_message_id == message_id:
                    return item.model_copy(deep=True)
                raise RequestBacklogBusy(f"Request R{slot} is already running")
            item.status = "running"
            item.active_run_id = uuid4().hex
            item.active_message_id = message_id
            item.active_task_id = None
            item.last_error = None
            item.finished_at = None
            item.updated_at = utc_now()
            self._write_unlocked(state)
        return item.model_copy(deep=True)

    def record_submitted(
        self,
        slot: int,
        generation: str,
        run_id: str,
        task_id: str,
    ) -> None:
        with self._locked():
            state = self._read_unlocked()
            item = self._find(state, slot)
            if not self._matches_run(item, generation, run_id):
                return
            item.active_task_id = task_id
            item.updated_at = utc_now()
            self._write_unlocked(state)

    def finish_run(
        self,
        slot: int,
        generation: str,
        run_id: str,
        task_id: str | None,
        *,
        succeeded: bool,
        error: str | None = None,
    ) -> bool:
        with self._locked():
            state = self._read_unlocked()
            try:
                item = self._find(state, slot)
            except RequestBacklogNotFound:
                return False
            if not self._matches_run(item, generation, run_id):
                return False
            item.status = "succeeded" if succeeded else "failed"
            item.last_task_id = task_id or item.active_task_id
            item.last_error = None if succeeded else (error or "Task failed")[:500]
            item.active_run_id = None
            item.active_message_id = None
            item.active_task_id = None
            item.finished_at = utc_now()
            item.updated_at = item.finished_at
            self._write_unlocked(state)
            return True

    def archive(self, slot: int) -> ArchivedRequestBacklogItem:
        self._validate_slot(slot)
        with self._locked():
            state = self._read_unlocked()
            item = self._find(state, slot)
            if item.status == "running":
                raise RequestBacklogBusy(f"Request R{slot} is running")
            archived = ArchivedRequestBacklogItem(
                **item.model_dump(),
                archived_at=utc_now(),
            )
            state.active = [entry for entry in state.active if entry.slot != slot]
            state.archived = [*state.archived, archived][-MAX_ARCHIVED_REQUESTS:]
            self._write_unlocked(state)
        return archived.model_copy(deep=True)

    def slot_matches(self, slot: int, generation: str) -> bool:
        try:
            item = self.get(slot)
        except (RequestBacklogError, OSError):
            return False
        return item.generation == generation

    def reset_runs_for_system_upgrade(self, operation_id: str, *, force: bool = False) -> None:
        if len(operation_id) != 32 or any(
            char not in "0123456789abcdef" for char in operation_id
        ):
            raise ValueError("System upgrade operation ID is invalid")
        with self._locked():
            state = self._read_unlocked()
            if not force and any(item.status == "running" for item in state.active):
                raise RequestBacklogBusy("A request is still running")
            now = utc_now()
            for item in state.active:
                item.generation = hashlib.sha256(
                    f"{operation_id}:R{item.slot}".encode("ascii")
                ).hexdigest()[:32]
                item.status = "ready"
                item.active_run_id = None
                item.active_message_id = None
                item.active_task_id = None
                item.last_task_id = None
                item.last_error = None
                item.finished_at = None
                item.updated_at = now
            for item in state.archived:
                item.active_run_id = None
                item.active_message_id = None
                item.active_task_id = None
                item.last_task_id = None
                item.last_error = None
                item.finished_at = None
                item.updated_at = now
            self._write_unlocked(state)

    @contextmanager
    def _locked(self) -> Iterator[None]:
        with self._thread_lock:
            try:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                os.chmod(self.path.parent, 0o700)
                descriptor = os.open(
                    self.lock_path,
                    os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0),
                    0o600,
                )
                os.chmod(self.lock_path, 0o600)
            except OSError as exc:
                raise RequestBacklogUnavailable("Request backlog lock is unavailable") from exc
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX)
                yield
            finally:
                try:
                    fcntl.flock(descriptor, fcntl.LOCK_UN)
                finally:
                    os.close(descriptor)

    def _read_unlocked(self) -> RequestBacklogState:
        try:
            metadata = self.path.lstat()
        except FileNotFoundError:
            return RequestBacklogState()
        except OSError as exc:
            raise RequestBacklogUnavailable("Request backlog is unavailable") from exc
        if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
            raise RequestBacklogUnavailable("Request backlog must be a regular file")
        if metadata.st_size > MAX_REQUEST_STATE_BYTES:
            raise RequestBacklogUnavailable("Request backlog exceeds its size limit")
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
            state = RequestBacklogState.model_validate(payload)
        except (OSError, UnicodeError, ValueError, ValidationError) as exc:
            raise RequestBacklogUnavailable("Request backlog is invalid") from exc
        slots = [item.slot for item in state.active]
        if len(slots) != len(set(slots)) or len(slots) > MAX_REQUEST_SLOTS:
            raise RequestBacklogUnavailable("Request backlog slots are invalid")
        return state

    def _write_unlocked(self, state: RequestBacklogState) -> None:
        content = (
            json.dumps(
                state.model_dump(mode="json"),
                ensure_ascii=False,
                separators=(",", ":"),
            )
            + "\n"
        ).encode("utf-8")
        if len(content) > MAX_REQUEST_STATE_BYTES:
            raise RequestBacklogUnavailable("Request backlog exceeds its size limit")
        if self.path.is_symlink():
            raise RequestBacklogUnavailable("Request backlog must not be a symlink")
        temporary = self.path.with_name(f".{self.path.name}.{uuid4().hex}.tmp")
        try:
            descriptor = os.open(
                temporary,
                os.O_CREAT | os.O_EXCL | os.O_WRONLY | getattr(os, "O_NOFOLLOW", 0),
                0o600,
            )
            with os.fdopen(descriptor, "wb") as file:
                file.write(content)
                file.flush()
                os.fsync(file.fileno())
            temporary.replace(self.path)
            os.chmod(self.path, 0o600)
        except OSError as exc:
            raise RequestBacklogUnavailable("Request backlog could not be saved") from exc
        finally:
            try:
                temporary.unlink()
            except FileNotFoundError:
                pass

    @staticmethod
    def _find(state: RequestBacklogState, slot: int) -> RequestBacklogItem:
        item = next((entry for entry in state.active if entry.slot == slot), None)
        if item is None:
            raise RequestBacklogNotFound(f"Request R{slot} was not found")
        return item

    @staticmethod
    def _matches_run(
        item: RequestBacklogItem,
        generation: str,
        run_id: str,
    ) -> bool:
        return (
            item.status == "running"
            and item.generation == generation
            and item.active_run_id == run_id
        )

    @staticmethod
    def _validate_slot(slot: int) -> None:
        if slot < 1 or slot > MAX_REQUEST_SLOTS:
            raise ValueError("Request slot must be between 1 and 9")

    @staticmethod
    def _normalize_title(title: str) -> str:
        value = " ".join(title.strip().split())
        if not value:
            raise ValueError("Request title must not be blank")
        if len(value) > MAX_REQUEST_TITLE_CHARS:
            raise ValueError("Request title exceeds 48 characters")
        return value

    @staticmethod
    def _normalize_content(content: str) -> str:
        value = content.strip()
        if not value:
            raise ValueError("Request content must not be blank")
        if len(value) > MAX_REQUEST_CONTENT_CHARS:
            raise ValueError("Request content exceeds 2000 characters")
        return value
