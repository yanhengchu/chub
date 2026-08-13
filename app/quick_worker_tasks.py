from __future__ import annotations

import asyncio
import fcntl
import hashlib
import json
import os
import re
import shutil
import signal
import stat
import sys
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Literal

import psutil
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
    model_validator,
)

from app.core.config import Settings


MAX_PROMPT_CHARS = 8_000
MAX_PROMPT_BYTES = 32 * 1024
MAX_SPEC_BYTES = 64 * 1024
MAX_STATE_BYTES = 16 * 1024
MAX_COMPLETION_BYTES = 128 * 1024
MAX_RESULT_BYTES = 100_000
MAX_ERROR_BYTES = 4_000
MAX_EVENT_BYTES = 2 * 1024 * 1024
MAX_TASK_DIRECTORIES = 2_000
MAX_ACTIVE_TASKS = 8
TASK_RETRY_WINDOW = timedelta(days=7)
TASK_FUTURE_SKEW = timedelta(minutes=5)
TERMINATE_GRACE_SECONDS = 0.5
TERMINATE_KILL_SECONDS = 2.0

TaskStatus = Literal[
    "accepted",
    "starting",
    "running",
    "succeeded",
    "failed",
    "timed_out",
    "cancelled",
]
FinalTaskStatus = Literal["succeeded", "failed", "timed_out", "cancelled"]
TestBehavior = Literal["succeed", "fail", "ignore_term", "orphan_child"]
RunnerKind = Literal["fixed_test", "codex"]
CodexPermissionMode = Literal["auto-review", "read-only", "full-access"]
FINAL_STATUSES = {"succeeded", "failed", "timed_out", "cancelled"}
TASK_ID_PATTERN = r"^qw-[0-9]{13}-[a-f0-9]{32}$"
SESSION_ID_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$"
WORKSPACE_ID_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$"
NATIVE_SESSION_ID_PATTERN = (
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
    r"[89abAB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}$"
)


def utc_now() -> datetime:
    return datetime.now(UTC)


def new_worker_task_id(now: datetime | None = None) -> str:
    timestamp = now or utc_now()
    milliseconds = int(timestamp.timestamp() * 1000)
    return f"qw-{milliseconds:013d}-{uuid.uuid4().hex}"


def worker_state_dir(settings: Settings) -> Path:
    return settings.codex_pty.data_file.parent / "quick-worker"


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class TestTaskSubmission(_StrictModel):
    task_id: str = Field(pattern=TASK_ID_PATTERN)
    prompt: str = Field(min_length=1, max_length=MAX_PROMPT_CHARS)
    behavior: TestBehavior = "succeed"
    run_seconds: float = Field(default=0.0, ge=0.0, le=60.0)
    timeout_seconds: float = Field(gt=0.0, le=120.0)

    @field_validator("prompt")
    @classmethod
    def prompt_fits_private_input(cls, value: str) -> str:
        if len(value.encode("utf-8")) > MAX_PROMPT_BYTES:
            raise ValueError("prompt exceeds the fixed byte limit")
        return value


class CodexTaskSubmission(_StrictModel):
    task_id: str = Field(pattern=TASK_ID_PATTERN)
    session_id: str = Field(pattern=SESSION_ID_PATTERN)
    workspace_id: str = Field(pattern=WORKSPACE_ID_PATTERN)
    prompt: str = Field(min_length=1, max_length=MAX_PROMPT_CHARS)
    permission_mode: CodexPermissionMode
    codex_session_id: str | None = Field(
        default=None,
        pattern=NATIVE_SESSION_ID_PATTERN,
    )
    model: str | None = Field(default=None, min_length=1, max_length=128)
    reasoning_effort: str | None = Field(default=None, min_length=1, max_length=32)
    timeout_seconds: float = Field(gt=0.0, le=120.0)

    @field_validator("prompt")
    @classmethod
    def prompt_fits_private_input(cls, value: str) -> str:
        if len(value.encode("utf-8")) > MAX_PROMPT_BYTES:
            raise ValueError("prompt exceeds the fixed byte limit")
        return value


class StoredTaskSpec(_StrictModel):
    protocol_version: int
    task_id: str = Field(pattern=TASK_ID_PATTERN)
    runner_kind: RunnerKind
    prompt: str = Field(min_length=1, max_length=MAX_PROMPT_CHARS)
    prompt_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    spec_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    behavior: TestBehavior | None = None
    run_seconds: float | None = None
    session_id: str | None = Field(default=None, pattern=SESSION_ID_PATTERN)
    workspace_id: str | None = Field(default=None, pattern=WORKSPACE_ID_PATTERN)
    permission_mode: CodexPermissionMode | None = None
    codex_session_id: str | None = Field(
        default=None,
        pattern=NATIVE_SESSION_ID_PATTERN,
    )
    model: str | None = Field(default=None, min_length=1, max_length=128)
    reasoning_effort: str | None = Field(default=None, min_length=1, max_length=32)
    timeout_seconds: float
    created_at: datetime
    deadline_at: datetime

    @model_validator(mode="after")
    def validate_runner_fields(self) -> StoredTaskSpec:
        fixed_fields = (self.behavior, self.run_seconds)
        codex_fields = (self.session_id, self.workspace_id, self.permission_mode)
        if self.runner_kind == "fixed_test":
            if any(item is None for item in fixed_fields) or any(
                item is not None
                for item in (
                    *codex_fields,
                    self.codex_session_id,
                    self.model,
                    self.reasoning_effort,
                )
            ):
                raise ValueError("fixed test task fields are inconsistent")
        elif any(item is not None for item in fixed_fields) or any(
            item is None for item in codex_fields
        ):
            raise ValueError("Codex task fields are inconsistent")
        return self


class StoredTaskState(_StrictModel):
    task_id: str = Field(pattern=TASK_ID_PATTERN)
    spec_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    status: TaskStatus
    worker_generation: str
    runner_pid: int | None = Field(default=None, ge=1)
    runner_created_at: float | None = Field(default=None, gt=0)
    native_session_id: str | None = Field(
        default=None,
        pattern=NATIVE_SESSION_ID_PATTERN,
    )
    cancellation_requested: bool = False
    updated_at: datetime


class StoredTaskCompletion(_StrictModel):
    task_id: str = Field(pattern=TASK_ID_PATTERN)
    spec_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    status: FinalTaskStatus
    result: str | None = None
    error: str | None = None
    error_code: str | None = Field(default=None, max_length=64)
    exit_code: int | None = None
    native_session_id: str | None = Field(
        default=None,
        pattern=NATIVE_SESSION_ID_PATTERN,
    )
    completed_at: datetime


class TaskTombstone(_StrictModel):
    task_id: str = Field(pattern=TASK_ID_PATTERN)
    spec_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    completed_at: datetime
    expires_at: datetime


class SessionLease(_StrictModel):
    session_id: str = Field(pattern=SESSION_ID_PATTERN)
    task_id: str = Field(pattern=TASK_ID_PATTERN)
    spec_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    created_at: datetime


class WorkerTaskView(_StrictModel):
    task_id: str
    status: TaskStatus
    prompt_sha256: str
    created_at: datetime
    deadline_at: datetime
    updated_at: datetime
    worker_generation: str
    runner_pid: int | None = None
    cancellation_requested: bool
    result: str | None = None
    error: str | None = None
    error_code: str | None = None
    exit_code: int | None = None
    native_session_id: str | None = None


class WorkerTaskSummary(_StrictModel):
    task_id: str
    status: TaskStatus
    prompt_sha256: str
    created_at: datetime
    updated_at: datetime


class WorkerTaskError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def _private_directory(path: Path) -> None:
    if path.is_symlink():
        raise OSError(f"Quick Worker directory must not be a symlink: {path}")
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    metadata = path.stat()
    if not path.is_dir() or metadata.st_uid != os.getuid():
        raise OSError(f"Quick Worker directory is unavailable: {path}")
    os.chmod(path, 0o700)


def _canonical_json(payload: dict[str, object]) -> bytes:
    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _digest_submission(submission: TestTaskSubmission | CodexTaskSubmission) -> str:
    payload = submission.model_dump(mode="json", exclude={"task_id"})
    return hashlib.sha256(_canonical_json(payload)).hexdigest()


def _digest_stored_spec(spec: StoredTaskSpec) -> str:
    if spec.runner_kind == "fixed_test":
        payload = {
            "prompt": spec.prompt,
            "behavior": spec.behavior,
            "run_seconds": spec.run_seconds,
            "timeout_seconds": spec.timeout_seconds,
        }
    else:
        payload = {
            "session_id": spec.session_id,
            "workspace_id": spec.workspace_id,
            "prompt": spec.prompt,
            "permission_mode": spec.permission_mode,
            "codex_session_id": spec.codex_session_id,
            "model": spec.model,
            "reasoning_effort": spec.reasoning_effort,
            "timeout_seconds": spec.timeout_seconds,
        }
    return hashlib.sha256(_canonical_json(payload)).hexdigest()


def _limited_text(value: str, byte_limit: int) -> str:
    return value.encode("utf-8")[:byte_limit].decode("utf-8", errors="ignore")


def _atomic_write(path: Path, content: bytes, *, max_bytes: int) -> None:
    if len(content) > max_bytes:
        raise OSError(f"Quick Worker record exceeds its fixed limit: {path.name}")
    _private_directory(path.parent)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(temporary, flags, 0o600)
    try:
        with os.fdopen(descriptor, "wb", closefd=True) as file:
            file.write(content)
            file.flush()
            os.fsync(file.fileno())
        os.replace(temporary, path)
        os.chmod(path, 0o600)
        directory_descriptor = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory_descriptor)
        finally:
            os.close(directory_descriptor)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def _write_model(path: Path, model: BaseModel, *, max_bytes: int) -> None:
    _atomic_write(
        path,
        _canonical_json(model.model_dump(mode="json")),
        max_bytes=max_bytes,
    )


def _read_model(path: Path, model_type: type[_StrictModel], *, max_bytes: int):
    metadata = path.lstat()
    if (
        not stat.S_ISREG(metadata.st_mode)
        or metadata.st_uid != os.getuid()
        or metadata.st_size > max_bytes
        or stat.S_IMODE(metadata.st_mode) & 0o077
    ):
        raise OSError(f"Quick Worker record is unsafe or too large: {path.name}")
    with path.open("rb") as file:
        content = file.read(max_bytes + 1)
    if len(content) > max_bytes:
        raise OSError(f"Quick Worker record is too large: {path.name}")
    return model_type.model_validate_json(content)


class WorkerTaskManager:
    def __init__(
        self,
        settings: Settings,
        generation: str,
        *,
        protocol_version: int,
        allow_test_tasks: bool = False,
        isolated_workspaces: dict[str, Path] | None = None,
        codex_executable: str | Path | None = None,
        codex_home: Path | None = None,
    ) -> None:
        self.root = worker_state_dir(settings)
        self.tasks_dir = self.root / "tasks"
        self.tombstones_dir = self.root / "tombstones"
        self.leases_dir = self.root / "session-leases"
        self.generation = generation
        self.protocol_version = protocol_version
        self.allow_test_tasks = allow_test_tasks
        self.isolated_workspaces = {
            workspace_id: workspace.resolve()
            for workspace_id, workspace in (isolated_workspaces or {}).items()
        }
        resolved_executable = (
            str(codex_executable)
            if codex_executable is not None
            else shutil.which("codex")
        )
        self.codex_executable = resolved_executable
        self.codex_home = (
            codex_home
            if codex_home is not None
            else Path(os.environ.get("CODEX_HOME", Path.home() / ".codex"))
        )
        self._lock = asyncio.Lock()
        self._supervisors: dict[str, asyncio.Task[None]] = {}
        self._processes: dict[str, asyncio.subprocess.Process] = {}
        self._corrupt_task_ids: set[str] = set()
        self._abandoning = False

    @property
    def active_count(self) -> int:
        return len(self._supervisors)

    @property
    def corrupt_count(self) -> int:
        return len(self._corrupt_task_ids)

    async def start(self) -> None:
        _private_directory(self.root)
        _private_directory(self.tasks_dir)
        _private_directory(self.tombstones_dir)
        _private_directory(self.leases_dir)
        self._validate_isolated_workspaces()
        await self._recover_tasks()

    async def close(self, *, interrupt_tasks: bool = True) -> None:
        self._abandoning = not interrupt_tasks
        supervisors = list(self._supervisors.values())
        if interrupt_tasks:
            for task_id in list(self._supervisors):
                try:
                    await self._interrupt_task(
                        task_id,
                        error_code="worker_stopped",
                        error="Quick Worker stopped before the task completed.",
                    )
                except (OSError, WorkerTaskError):
                    pass
        for supervisor in supervisors:
            if not supervisor.done():
                supervisor.cancel()
        if supervisors:
            await asyncio.gather(*supervisors, return_exceptions=True)
        self._supervisors.clear()
        self._processes.clear()

    async def submit_test(self, submission: TestTaskSubmission) -> WorkerTaskView:
        if not self.allow_test_tasks:
            raise WorkerTaskError(
                "worker_action_unavailable",
                "Fixed test tasks are disabled for this Worker instance",
            )
        return await self._submit(submission, runner_kind="fixed_test")

    async def submit_codex(self, submission: CodexTaskSubmission) -> WorkerTaskView:
        if not self.allow_test_tasks or not self.isolated_workspaces:
            raise WorkerTaskError(
                "worker_action_unavailable",
                "Isolated Codex tasks are disabled for this Worker instance",
            )
        if self.codex_executable is None:
            raise WorkerTaskError(
                "codex_unavailable",
                "Codex executable is unavailable to the Worker",
            )
        workspace = self.isolated_workspaces.get(submission.workspace_id)
        if workspace is None:
            raise WorkerTaskError(
                "worker_workspace_unavailable",
                "The fixed isolated workspace is unavailable",
            )
        return await self._submit(submission, runner_kind="codex")

    async def _submit(
        self,
        submission: TestTaskSubmission | CodexTaskSubmission,
        *,
        runner_kind: RunnerKind,
    ) -> WorkerTaskView:
        now = utc_now()
        self._validate_task_id_time(submission.task_id, now)
        spec_digest = _digest_submission(submission)
        async with self._lock:
            existing = self.tasks_dir / submission.task_id
            tombstone_path = self.tombstones_dir / f"{submission.task_id}.json"
            if existing.exists() or existing.is_symlink():
                try:
                    spec = self._read_spec(submission.task_id)
                except (OSError, ValidationError, ValueError):
                    self._corrupt_task_ids.add(submission.task_id)
                    raise WorkerTaskError(
                        "worker_task_corrupt",
                        "Existing task record is invalid; the task was not replayed",
                    ) from None
                if spec.spec_sha256 != spec_digest or spec.runner_kind != runner_kind:
                    raise WorkerTaskError(
                        "worker_task_conflict",
                        "Task ID is already reserved for a different specification",
                    )
                return self._view(submission.task_id)
            if tombstone_path.exists() or tombstone_path.is_symlink():
                try:
                    tombstone = _read_model(
                        tombstone_path,
                        TaskTombstone,
                        max_bytes=MAX_STATE_BYTES,
                    )
                except (OSError, ValidationError, ValueError):
                    raise WorkerTaskError(
                        "worker_task_corrupt",
                        "Task tombstone is invalid; the task was not replayed",
                    ) from None
                if tombstone.task_id != submission.task_id:
                    raise WorkerTaskError(
                        "worker_task_corrupt",
                        "Task tombstone identity does not match its file",
                    )
                if tombstone.spec_sha256 != spec_digest:
                    raise WorkerTaskError(
                        "worker_task_conflict",
                        "Task ID is already reserved for a different specification",
                    )
                raise WorkerTaskError(
                    "worker_task_expired",
                    "Task result has been retired and cannot be submitted again",
                )

            if len(self._supervisors) >= MAX_ACTIVE_TASKS:
                raise WorkerTaskError(
                    "worker_capacity_reached",
                    "Worker has reached its fixed active task limit",
                )
            if self._task_directory_count() >= MAX_TASK_DIRECTORIES:
                raise WorkerTaskError(
                    "worker_store_oversized",
                    "Worker task store has reached its fixed limit",
                )

            if isinstance(submission, CodexTaskSubmission):
                self._ensure_session_available(submission.session_id)

            task_dir = self.tasks_dir / submission.task_id
            task_dir.mkdir(mode=0o700)
            os.chmod(task_dir, 0o700)
            prompt_sha256 = hashlib.sha256(submission.prompt.encode("utf-8")).hexdigest()
            spec = StoredTaskSpec(
                protocol_version=self.protocol_version,
                task_id=submission.task_id,
                runner_kind=runner_kind,
                prompt=submission.prompt,
                prompt_sha256=prompt_sha256,
                spec_sha256=spec_digest,
                behavior=(
                    submission.behavior
                    if isinstance(submission, TestTaskSubmission)
                    else None
                ),
                run_seconds=(
                    submission.run_seconds
                    if isinstance(submission, TestTaskSubmission)
                    else None
                ),
                session_id=(
                    submission.session_id
                    if isinstance(submission, CodexTaskSubmission)
                    else None
                ),
                workspace_id=(
                    submission.workspace_id
                    if isinstance(submission, CodexTaskSubmission)
                    else None
                ),
                permission_mode=(
                    submission.permission_mode
                    if isinstance(submission, CodexTaskSubmission)
                    else None
                ),
                codex_session_id=(
                    submission.codex_session_id
                    if isinstance(submission, CodexTaskSubmission)
                    else None
                ),
                model=(submission.model if isinstance(submission, CodexTaskSubmission) else None),
                reasoning_effort=(
                    submission.reasoning_effort
                    if isinstance(submission, CodexTaskSubmission)
                    else None
                ),
                timeout_seconds=submission.timeout_seconds,
                created_at=now,
                deadline_at=now + timedelta(seconds=submission.timeout_seconds),
            )
            state = StoredTaskState(
                task_id=submission.task_id,
                spec_sha256=spec_digest,
                status="accepted",
                worker_generation=self.generation,
                updated_at=now,
            )
            try:
                _write_model(task_dir / "spec.json", spec, max_bytes=MAX_SPEC_BYTES)
                if spec.session_id is not None:
                    self._write_lease(
                        SessionLease(
                            session_id=spec.session_id,
                            task_id=spec.task_id,
                            spec_sha256=spec.spec_sha256,
                            created_at=now,
                        )
                    )
                self._write_state(state)
            except Exception:
                cleanup_failed = False
                if spec.session_id is not None:
                    try:
                        self._release_lease(spec)
                    except FileNotFoundError:
                        pass
                    except Exception:
                        cleanup_failed = True
                for name in ("state.json", "spec.json"):
                    try:
                        (task_dir / name).unlink()
                    except FileNotFoundError:
                        pass
                    except OSError:
                        cleanup_failed = True
                try:
                    task_dir.rmdir()
                except FileNotFoundError:
                    pass
                except OSError:
                    cleanup_failed = True
                if cleanup_failed:
                    self._corrupt_task_ids.add(submission.task_id)
                raise
            supervisor = asyncio.create_task(
                self._launch_and_monitor(submission.task_id),
                name=f"quick-worker-{submission.task_id}",
            )
            self._supervisors[submission.task_id] = supervisor
            supervisor.add_done_callback(
                lambda _task, task_id=submission.task_id: self._supervisors.pop(
                    task_id, None
                )
            )
            return self._view(submission.task_id)

    async def cancel(self, task_id: str) -> WorkerTaskView:
        self._validate_task_id(task_id)
        try:
            async with self._lock:
                spec, state, completion = self._records(task_id)
                if completion is not None:
                    return self._view_from(spec, state, completion)
                state.cancellation_requested = True
                state.updated_at = utc_now()
                self._write_state(state)
                process = self._processes.get(task_id)
                supervisor = self._supervisors.get(task_id)
        except FileNotFoundError:
            raise WorkerTaskError("worker_task_not_found", "Worker task was not found") from None
        except (OSError, ValidationError, ValueError):
            self._corrupt_task_ids.add(task_id)
            raise WorkerTaskError(
                "worker_task_corrupt",
                "Worker task record is invalid and cannot be cancelled safely",
            ) from None
        if process is not None:
            await self._terminate_process(process)
        if supervisor is not None:
            try:
                await asyncio.wait_for(asyncio.shield(supervisor), timeout=3.0)
            except asyncio.TimeoutError as exc:
                raise WorkerTaskError(
                    "worker_cancel_incomplete",
                    "Task process did not reach a final state after cancellation",
                ) from exc
        return self.get(task_id)

    def get(self, task_id: str) -> WorkerTaskView:
        self._validate_task_id(task_id)
        try:
            spec, state, completion = self._records(task_id)
            return self._view_from(spec, state, completion)
        except FileNotFoundError:
            raise WorkerTaskError("worker_task_not_found", "Worker task was not found") from None
        except (OSError, ValidationError, ValueError):
            self._corrupt_task_ids.add(task_id)
            raise WorkerTaskError(
                "worker_task_corrupt",
                "Worker task record is invalid and cannot be treated as completed",
            ) from None

    def list(self, *, limit: int) -> list[WorkerTaskSummary]:
        summaries: list[WorkerTaskSummary] = []
        try:
            entries = list(self.tasks_dir.iterdir())
        except OSError as exc:
            raise WorkerTaskError(
                "worker_store_unavailable", "Worker task store could not be read"
            ) from exc
        if len(entries) > MAX_TASK_DIRECTORIES:
            raise WorkerTaskError(
                "worker_store_oversized", "Worker task store exceeds its fixed limit"
            )
        for entry in entries:
            if not entry.is_dir() or entry.is_symlink():
                continue
            try:
                view = self.get(entry.name)
            except WorkerTaskError:
                continue
            summaries.append(
                WorkerTaskSummary(
                    task_id=view.task_id,
                    status=view.status,
                    prompt_sha256=view.prompt_sha256,
                    created_at=view.created_at,
                    updated_at=view.updated_at,
                )
            )
        summaries.sort(key=lambda item: (item.created_at, item.task_id), reverse=True)
        return summaries[:limit]

    async def _recover_tasks(self) -> None:
        entries = list(self.tasks_dir.iterdir())
        if len(entries) > MAX_TASK_DIRECTORIES:
            raise OSError("Quick Worker task store exceeds its fixed limit")
        for entry in entries:
            if entry.is_symlink() or not entry.is_dir():
                continue
            task_id = entry.name
            try:
                self._validate_task_id(task_id)
                spec, state, completion = self._records(task_id, allow_state_repair=True)
            except (FileNotFoundError, OSError, ValidationError, ValueError, WorkerTaskError):
                self._corrupt_task_ids.add(task_id)
                continue
            if completion is not None:
                if spec.session_id is not None:
                    try:
                        self._release_lease(spec)
                    except FileNotFoundError:
                        pass
                continue
            if state.runner_pid is not None and state.runner_created_at is not None:
                await self._terminate_identity(
                    state.runner_pid,
                    state.runner_created_at,
                )
            if spec.runner_kind == "codex":
                native_session_id = self._extract_native_session_id(
                    self.tasks_dir / task_id / "stdout.txt",
                    missing_ok=True,
                )
                if native_session_id is not None and (
                    spec.codex_session_id is None
                    or native_session_id == spec.codex_session_id
                ):
                    state.native_session_id = native_session_id
                    state.updated_at = utc_now()
                    self._write_state(state)
            self._finalize(
                spec,
                state,
                status="failed",
                error_code="worker_restarted",
                error="Quick Worker restarted before completion; the task was not replayed.",
            )

    async def _launch_and_monitor(self, task_id: str) -> None:
        process: asyncio.subprocess.Process | None = None
        release_read: int | None = None
        release_write: int | None = None
        stdout_file = None
        stderr_file = None
        native_observer: asyncio.Task[None] | None = None
        try:
            async with self._lock:
                spec, state, completion = self._records(task_id)
                if completion is not None:
                    return
                if state.cancellation_requested:
                    self._finalize(
                        spec,
                        state,
                        status="cancelled",
                        error_code="cancelled",
                        error="Task was cancelled before execution started.",
                    )
                    return
                if spec.runner_kind == "codex" and spec.codex_session_id is not None:
                    if self._has_active_codex_writer(spec.codex_session_id):
                        self._finalize(
                            spec,
                            state,
                            status="failed",
                            error_code="codex_session_busy",
                            error=(
                                "Codex session already has an active writer; "
                                "the task was not started."
                            ),
                        )
                        return
                task_dir = self.tasks_dir / task_id
                stdout_path = task_dir / "stdout.txt"
                stderr_path = task_dir / "stderr.txt"
                stdout_file = self._open_private_output(stdout_path)
                stderr_file = self._open_private_output(stderr_path)
                release_read, release_write = os.pipe()
                runner_args = [
                    sys.executable,
                    "-m",
                    "app.quick_worker_runner",
                    "--task-dir",
                    str(task_dir),
                    "--release-fd",
                    str(release_read),
                ]
                if spec.runner_kind == "codex":
                    runner_args.extend(
                        [
                            "--codex-executable",
                            str(self.codex_executable),
                            "--working-directory",
                            str(self._runner_cwd(spec)),
                        ]
                    )
                process = await asyncio.create_subprocess_exec(
                    *runner_args,
                    cwd=Path(__file__).resolve().parents[1],
                    stdin=(
                        asyncio.subprocess.PIPE
                        if spec.runner_kind == "codex"
                        else asyncio.subprocess.DEVNULL
                    ),
                    stdout=stdout_file,
                    stderr=stderr_file,
                    start_new_session=True,
                    pass_fds=(release_read,),
                )
                os.close(release_read)
                release_read = None
                runner_created_at = psutil.Process(process.pid).create_time()
                state.status = "starting"
                state.worker_generation = self.generation
                state.runner_pid = process.pid
                state.runner_created_at = runner_created_at
                state.updated_at = utc_now()
                self._write_state(state)
                self._processes[task_id] = process
                os.write(release_write, b"1")
                os.close(release_write)
                release_write = None
                if spec.runner_kind == "codex":
                    if process.stdin is None:
                        raise OSError("Codex runner input pipe is unavailable")
                    process.stdin.write(spec.prompt.encode("utf-8"))
                    await process.stdin.drain()
                    process.stdin.close()
                state.status = "running"
                state.updated_at = utc_now()
                self._write_state(state)

            if spec.runner_kind == "codex":
                native_observer = asyncio.create_task(
                    self._observe_native_session(task_id),
                    name=f"quick-worker-native-session-{task_id}",
                )
            remaining = max(0.0, (spec.deadline_at - utc_now()).total_seconds())
            try:
                exit_code = await asyncio.wait_for(process.wait(), timeout=remaining)
            except asyncio.TimeoutError:
                await self._terminate_process(process)
                async with self._lock:
                    spec, state, completion = self._records(task_id)
                    if completion is None:
                        self._finalize(
                            spec,
                            state,
                            status="timed_out",
                            error_code="deadline_exceeded",
                            error="Task reached its absolute execution deadline.",
                        )
                return

            async with self._lock:
                spec, state, completion = self._records(task_id)
                if completion is not None:
                    return
                native_session_id = self._extract_native_session_id(
                    self.tasks_dir / task_id / "stdout.txt"
                ) if spec.runner_kind == "codex" else None
                native_session_confirmed = native_session_id is not None and (
                    spec.codex_session_id is None
                    or native_session_id == spec.codex_session_id
                )
                if native_session_confirmed:
                    state.native_session_id = native_session_id
                    state.updated_at = utc_now()
                    self._write_state(state)
                if state.cancellation_requested:
                    self._finalize(
                        spec,
                        state,
                        status="cancelled",
                        error_code="cancelled",
                        error="Task was cancelled.",
                        exit_code=exit_code,
                    )
                elif exit_code == 0:
                    result_path = (
                        self.tasks_dir / task_id / "result.txt"
                        if spec.runner_kind == "codex"
                        else self.tasks_dir / task_id / "stdout.txt"
                    )
                    result = self._read_limited_file(result_path, MAX_RESULT_BYTES)
                    if spec.runner_kind == "codex" and not native_session_confirmed:
                        self._finalize(
                            spec,
                            state,
                            status="failed",
                            result=result,
                            error_code="native_session_unconfirmed",
                            error=(
                                "Codex completed, but its native Session ID could "
                                "not be confirmed safely."
                            ),
                            exit_code=exit_code,
                        )
                        return
                    self._finalize(
                        spec,
                        state,
                        status="succeeded",
                        result=result,
                        exit_code=exit_code,
                    )
                else:
                    error = self._read_limited_file(
                        self.tasks_dir / task_id / "stderr.txt",
                        MAX_ERROR_BYTES,
                    )
                    self._finalize(
                        spec,
                        state,
                        status="failed",
                        error_code="runner_failed",
                        error=error or "Task runner exited unsuccessfully.",
                        exit_code=exit_code,
                    )
        except asyncio.CancelledError:
            if not self._abandoning and process is not None:
                await self._terminate_process(process)
            raise
        except Exception:
            if process is not None:
                await self._terminate_process(process)
            if not self._abandoning:
                try:
                    async with self._lock:
                        spec, state, completion = self._records(task_id)
                        if completion is None:
                            self._finalize(
                                spec,
                                state,
                                status="failed",
                                error_code="runner_start_failed",
                                error="Worker could not start or supervise the task runner.",
                            )
                except Exception:
                    self._corrupt_task_ids.add(task_id)
        finally:
            if native_observer is not None:
                native_observer.cancel()
                await asyncio.gather(native_observer, return_exceptions=True)
            for descriptor in (release_read, release_write):
                if descriptor is not None:
                    try:
                        os.close(descriptor)
                    except OSError:
                        pass
            for file in (stdout_file, stderr_file):
                if file is not None:
                    file.close()
            self._processes.pop(task_id, None)

    async def _observe_native_session(self, task_id: str) -> None:
        path = self.tasks_dir / task_id / "stdout.txt"
        while True:
            try:
                native_session_id = self._extract_native_session_id(
                    path,
                    missing_ok=True,
                )
                if native_session_id is not None:
                    async with self._lock:
                        spec, state, completion = self._records(task_id)
                        if completion is not None:
                            return
                        if (
                            spec.codex_session_id is not None
                            and native_session_id != spec.codex_session_id
                        ):
                            return
                        state.native_session_id = native_session_id
                        state.updated_at = utc_now()
                        self._write_state(state)
                    return
            except (FileNotFoundError, OSError, ValidationError, ValueError):
                return
            await asyncio.sleep(0.05)

    async def _interrupt_task(
        self,
        task_id: str,
        *,
        error_code: str,
        error: str,
    ) -> None:
        async with self._lock:
            spec, state, completion = self._records(task_id)
            if completion is not None:
                return
            process = self._processes.get(task_id)
        if process is not None:
            await self._terminate_process(process)
        async with self._lock:
            spec, state, completion = self._records(task_id)
            if completion is None:
                self._finalize(
                    spec,
                    state,
                    status="failed",
                    error_code=error_code,
                    error=error,
                )

    async def _terminate_process(self, process: asyncio.subprocess.Process) -> None:
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        await self._wait_process_and_group(
            process,
            timeout=TERMINATE_GRACE_SECONDS,
        )
        if not self._process_group_members(process.pid):
            return
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        await self._wait_process_and_group(
            process,
            timeout=TERMINATE_KILL_SECONDS,
        )
        if self._process_group_members(process.pid):
            raise WorkerTaskError(
                "worker_process_group_alive",
                "Task process group could not be confirmed stopped",
            )

    async def _terminate_identity(self, pid: int, created_at: float) -> None:
        if not self._group_identity_matches(pid, created_at):
            return
        try:
            os.killpg(pid, signal.SIGTERM)
        except ProcessLookupError:
            return
        deadline = asyncio.get_running_loop().time() + TERMINATE_GRACE_SECONDS
        while asyncio.get_running_loop().time() < deadline:
            if not self._process_group_members(pid):
                return
            await asyncio.sleep(0.05)
        if self._process_group_members(pid):
            try:
                os.killpg(pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        deadline = asyncio.get_running_loop().time() + TERMINATE_KILL_SECONDS
        while asyncio.get_running_loop().time() < deadline:
            if not self._process_group_members(pid):
                return
            await asyncio.sleep(0.05)
        if self._process_group_members(pid):
            raise WorkerTaskError(
                "worker_process_group_alive",
                "Recovered task process group could not be confirmed stopped",
            )

    async def _wait_process_and_group(
        self,
        process: asyncio.subprocess.Process,
        *,
        timeout: float,
    ) -> None:
        deadline = asyncio.get_running_loop().time() + timeout
        while asyncio.get_running_loop().time() < deadline:
            if process.returncode is None:
                try:
                    await asyncio.wait_for(
                        asyncio.shield(process.wait()),
                        timeout=min(0.05, max(0.0, deadline - asyncio.get_running_loop().time())),
                    )
                except asyncio.TimeoutError:
                    pass
            if not self._process_group_members(process.pid):
                return
            await asyncio.sleep(0.02)

    @staticmethod
    def _process_group_members(process_group_id: int) -> list[int]:
        members: list[int] = []
        for process in psutil.process_iter(["pid", "status"]):
            try:
                if process.info["status"] in {psutil.STATUS_DEAD, psutil.STATUS_ZOMBIE}:
                    continue
                if os.getpgid(process.pid) == process_group_id:
                    members.append(process.pid)
            except (OSError, psutil.Error):
                continue
        return members

    @classmethod
    def _group_identity_matches(cls, pid: int, created_at: float) -> bool:
        if cls._identity_matches(pid, created_at):
            return True
        members = cls._process_group_members(pid)
        if not members:
            return False
        for member_pid in members:
            try:
                if psutil.Process(member_pid).create_time() < created_at - 1.0:
                    return False
            except (psutil.NoSuchProcess, psutil.AccessDenied, ValueError):
                return False
        return True

    @staticmethod
    def _identity_matches(pid: int, created_at: float) -> bool:
        try:
            process = psutil.Process(pid)
            if process.status() in {psutil.STATUS_DEAD, psutil.STATUS_ZOMBIE}:
                return False
            return abs(process.create_time() - created_at) < 0.01
        except (psutil.NoSuchProcess, psutil.AccessDenied, ValueError):
            return False

    def _records(
        self,
        task_id: str,
        *,
        allow_state_repair: bool = False,
    ) -> tuple[StoredTaskSpec, StoredTaskState, StoredTaskCompletion | None]:
        spec = self._read_spec(task_id)
        state = _read_model(
            self.tasks_dir / task_id / "state.json",
            StoredTaskState,
            max_bytes=MAX_STATE_BYTES,
        )
        completion_path = self.tasks_dir / task_id / "completion.json"
        try:
            completion = _read_model(
                completion_path,
                StoredTaskCompletion,
                max_bytes=MAX_COMPLETION_BYTES,
            )
        except FileNotFoundError:
            completion = None
        if spec.task_id != task_id or state.task_id != task_id:
            raise ValueError("task record identity does not match its directory")
        if spec.spec_sha256 != state.spec_sha256:
            raise ValueError("task specification and state do not match")
        if (state.runner_pid is None) != (state.runner_created_at is None):
            raise ValueError("task runner identity is incomplete")
        if spec.created_at.tzinfo is None or spec.deadline_at.tzinfo is None:
            raise ValueError("task timestamps must include a timezone")
        if spec.deadline_at <= spec.created_at:
            raise ValueError("task deadline is inconsistent")
        if state.updated_at.tzinfo is None or state.updated_at < spec.created_at:
            raise ValueError("task state timestamp is inconsistent")
        if state.status in FINAL_STATUSES and state.runner_pid is not None:
            raise ValueError("final task state still has a runner identity")
        if completion is not None:
            if completion.task_id != task_id:
                raise ValueError("task completion identity does not match its directory")
            if completion.spec_sha256 != spec.spec_sha256:
                raise ValueError("task completion does not match its specification")
            if (
                completion.completed_at.tzinfo is None
                or completion.completed_at < spec.created_at
            ):
                raise ValueError("task completion timestamp is inconsistent")
            if completion.native_session_id != state.native_session_id:
                raise ValueError("task native Session identity does not match")
            if state.status != completion.status:
                if not allow_state_repair or state.status in FINAL_STATUSES:
                    raise ValueError("task state and completion do not match")
                state.status = completion.status
                state.runner_pid = None
                state.runner_created_at = None
                state.updated_at = completion.completed_at
                self._write_state(state)
        elif state.status in FINAL_STATUSES:
            raise ValueError("final task state is missing its completion record")
        return spec, state, completion

    def _read_spec(self, task_id: str) -> StoredTaskSpec:
        task_directory = self.tasks_dir / task_id
        directory_metadata = task_directory.lstat()
        if (
            not stat.S_ISDIR(directory_metadata.st_mode)
            or directory_metadata.st_uid != os.getuid()
            or stat.S_IMODE(directory_metadata.st_mode) & 0o077
        ):
            raise OSError("task directory is unsafe")
        spec = _read_model(
            task_directory / "spec.json",
            StoredTaskSpec,
            max_bytes=MAX_SPEC_BYTES,
        )
        if spec.task_id != task_id or spec.protocol_version != self.protocol_version:
            raise ValueError("task specification is incompatible")
        if hashlib.sha256(spec.prompt.encode("utf-8")).hexdigest() != spec.prompt_sha256:
            raise ValueError("task prompt digest does not match")
        if _digest_stored_spec(spec) != spec.spec_sha256:
            raise ValueError("task specification digest does not match")
        return spec

    def _view(self, task_id: str) -> WorkerTaskView:
        spec, state, completion = self._records(task_id)
        return self._view_from(spec, state, completion)

    @staticmethod
    def _view_from(
        spec: StoredTaskSpec,
        state: StoredTaskState,
        completion: StoredTaskCompletion | None,
    ) -> WorkerTaskView:
        return WorkerTaskView(
            task_id=spec.task_id,
            status=state.status,
            prompt_sha256=spec.prompt_sha256,
            created_at=spec.created_at,
            deadline_at=spec.deadline_at,
            updated_at=state.updated_at,
            worker_generation=state.worker_generation,
            runner_pid=state.runner_pid,
            cancellation_requested=state.cancellation_requested,
            result=completion.result if completion else None,
            error=completion.error if completion else None,
            error_code=completion.error_code if completion else None,
            exit_code=completion.exit_code if completion else None,
            native_session_id=state.native_session_id,
        )

    def _write_state(self, state: StoredTaskState) -> None:
        _write_model(
            self.tasks_dir / state.task_id / "state.json",
            state,
            max_bytes=MAX_STATE_BYTES,
        )

    def _finalize(
        self,
        spec: StoredTaskSpec,
        state: StoredTaskState,
        *,
        status: FinalTaskStatus,
        result: str | None = None,
        error: str | None = None,
        error_code: str | None = None,
        exit_code: int | None = None,
    ) -> None:
        if state.status in FINAL_STATUSES:
            return
        completed_at = utc_now()
        completion = StoredTaskCompletion(
            task_id=spec.task_id,
            spec_sha256=spec.spec_sha256,
            status=status,
            result=_limited_text(result, MAX_RESULT_BYTES) if result is not None else None,
            error=_limited_text(error, MAX_ERROR_BYTES) if error is not None else None,
            error_code=error_code,
            exit_code=exit_code,
            native_session_id=state.native_session_id,
            completed_at=completed_at,
        )
        _write_model(
            self.tasks_dir / spec.task_id / "completion.json",
            completion,
            max_bytes=MAX_COMPLETION_BYTES,
        )
        state.status = status
        state.runner_pid = None
        state.runner_created_at = None
        state.updated_at = completed_at
        self._write_state(state)
        tombstone = TaskTombstone(
            task_id=spec.task_id,
            spec_sha256=spec.spec_sha256,
            completed_at=completed_at,
            expires_at=spec.created_at + TASK_RETRY_WINDOW,
        )
        _write_model(
            self.tombstones_dir / f"{spec.task_id}.json",
            tombstone,
            max_bytes=MAX_STATE_BYTES,
        )
        if spec.session_id is not None:
            self._release_lease(spec)

    def _task_directory_count(self) -> int:
        try:
            entries = list(self.tasks_dir.iterdir())
        except OSError as exc:
            raise WorkerTaskError(
                "worker_store_unavailable",
                "Worker task store could not be read",
            ) from exc
        if len(entries) > MAX_TASK_DIRECTORIES:
            raise WorkerTaskError(
                "worker_store_oversized",
                "Worker task store exceeds its fixed limit",
            )
        return len(entries)

    def _validate_isolated_workspaces(self) -> None:
        if self.isolated_workspaces and not self.allow_test_tasks:
            raise OSError("Isolated Worker workspaces require test task mode")
        for workspace_id, path in self.isolated_workspaces.items():
            if re.fullmatch(WORKSPACE_ID_PATTERN, workspace_id) is None:
                raise OSError("Isolated Worker workspace ID is invalid")
            if not path.is_dir() or path.is_symlink():
                raise OSError("Isolated Worker workspace is unavailable")

    def _runner_cwd(self, spec: StoredTaskSpec) -> Path:
        if spec.runner_kind == "fixed_test":
            return Path(__file__).resolve().parents[1]
        if spec.workspace_id is None:
            raise OSError("Codex task workspace ID is missing")
        workspace = self.isolated_workspaces.get(spec.workspace_id)
        if workspace is None or not workspace.is_dir() or workspace.is_symlink():
            raise OSError("Codex task workspace is unavailable")
        return workspace

    def _lease_path(self, session_id: str) -> Path:
        if re.fullmatch(SESSION_ID_PATTERN, session_id) is None:
            raise ValueError("Session ID is invalid")
        return self.leases_dir / f"{session_id}.json"

    def _ensure_session_available(self, session_id: str) -> None:
        path = self._lease_path(session_id)
        try:
            lease = _read_model(path, SessionLease, max_bytes=MAX_STATE_BYTES)
        except FileNotFoundError:
            return
        except (OSError, ValidationError, ValueError) as exc:
            raise WorkerTaskError(
                "worker_session_lease_corrupt",
                "Session lease is invalid and cannot be replaced safely",
            ) from exc
        if lease.session_id != session_id:
            raise WorkerTaskError(
                "worker_session_lease_corrupt",
                "Session lease identity does not match its file",
            )
        raise WorkerTaskError(
            "worker_session_busy",
            "Session already has an active Worker task",
        )

    def _write_lease(self, lease: SessionLease) -> None:
        path = self._lease_path(lease.session_id)
        content = _canonical_json(lease.model_dump(mode="json"))
        if len(content) > MAX_STATE_BYTES:
            raise OSError("Session lease exceeds its fixed limit")
        flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        try:
            descriptor = os.open(path, flags, 0o600)
        except FileExistsError as exc:
            raise WorkerTaskError(
                "worker_session_busy",
                "Session already has an active Worker task",
            ) from exc
        try:
            with os.fdopen(descriptor, "wb", closefd=True) as file:
                file.write(content)
                file.flush()
                os.fsync(file.fileno())
            os.chmod(path, 0o600)
            directory_descriptor = os.open(self.leases_dir, os.O_RDONLY)
            try:
                os.fsync(directory_descriptor)
            finally:
                os.close(directory_descriptor)
        except Exception:
            try:
                path.unlink()
            except FileNotFoundError:
                pass
            raise

    def _release_lease(self, spec: StoredTaskSpec) -> None:
        if spec.session_id is None:
            return
        path = self._lease_path(spec.session_id)
        lease = _read_model(path, SessionLease, max_bytes=MAX_STATE_BYTES)
        if (
            lease.session_id != spec.session_id
            or lease.task_id != spec.task_id
            or lease.spec_sha256 != spec.spec_sha256
        ):
            raise ValueError("Session lease does not match its task")
        path.unlink()
        directory_descriptor = os.open(self.leases_dir, os.O_RDONLY)
        try:
            os.fsync(directory_descriptor)
        finally:
            os.close(directory_descriptor)

    def _has_active_codex_writer(self, native_session_id: str) -> bool:
        if re.fullmatch(NATIVE_SESSION_ID_PATTERN, native_session_id) is None:
            raise OSError("Codex Session ID is invalid")
        path = self.codex_home / "thread-writer-locks" / f"{native_session_id}.lock"
        flags = os.O_RDWR | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
        try:
            descriptor = os.open(path, flags)
        except FileNotFoundError:
            return False
        try:
            if not stat.S_ISREG(os.fstat(descriptor).st_mode):
                raise OSError("Codex writer lock is not a regular file")
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                return True
            fcntl.flock(descriptor, fcntl.LOCK_UN)
            return False
        finally:
            os.close(descriptor)

    @staticmethod
    def _extract_native_session_id(
        path: Path,
        *,
        missing_ok: bool = False,
    ) -> str | None:
        try:
            metadata = path.lstat()
        except FileNotFoundError:
            if missing_ok:
                return None
            raise
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != os.getuid()
            or metadata.st_size > MAX_EVENT_BYTES
            or stat.S_IMODE(metadata.st_mode) & 0o077
        ):
            raise OSError("Codex event stream is unsafe or too large")
        found: set[str] = set()
        with path.open("rb") as file:
            for raw_line in file:
                if len(raw_line) > MAX_EVENT_BYTES:
                    raise OSError("Codex event line exceeds its fixed limit")
                try:
                    event = json.loads(raw_line)
                except (UnicodeDecodeError, json.JSONDecodeError):
                    continue
                native_id = (
                    event.get("thread_id")
                    if isinstance(event, dict)
                    and event.get("type") == "thread.started"
                    else None
                )
                if isinstance(native_id, str) and re.fullmatch(
                    NATIVE_SESSION_ID_PATTERN,
                    native_id,
                ):
                    found.add(native_id)
        if len(found) > 1:
            raise ValueError("Codex event stream contains conflicting Session IDs")
        return next(iter(found), None)

    def _open_private_output(self, path: Path):
        flags = os.O_CREAT | os.O_TRUNC | os.O_WRONLY
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        descriptor = os.open(path, flags, 0o600)
        os.fchmod(descriptor, 0o600)
        return os.fdopen(descriptor, "wb", closefd=True)

    @staticmethod
    def _read_limited_file(path: Path, byte_limit: int) -> str:
        metadata = path.lstat()
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_uid != os.getuid():
            raise OSError("Quick Worker output is unsafe")
        with path.open("rb") as file:
            content = file.read(byte_limit + 1)
        if len(content) > byte_limit:
            content = content[:byte_limit]
        return content.decode("utf-8", errors="replace")

    @staticmethod
    def _validate_task_id(task_id: str) -> None:
        if not isinstance(task_id, str) or re.fullmatch(TASK_ID_PATTERN, task_id) is None:
            raise WorkerTaskError("worker_task_id_invalid", "Task ID is invalid")

    @classmethod
    def _validate_task_id_time(cls, task_id: str, now: datetime) -> None:
        cls._validate_task_id(task_id)
        milliseconds = int(task_id.split("-", 2)[1])
        created = datetime.fromtimestamp(milliseconds / 1000, tz=UTC)
        if created > now + TASK_FUTURE_SKEW or created < now - TASK_RETRY_WINDOW:
            raise WorkerTaskError(
                "worker_task_id_expired",
                "Task ID is outside the fixed submission retry window",
            )
