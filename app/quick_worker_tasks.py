from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import signal
import stat
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

from app.ai_runtime import (
    PermissionProfile,
    RUNTIME_ID_PATTERN,
    RuntimeOperationError,
    RuntimeTurnRequest,
    RuntimeWorkerLaunchRequest,
    WorkerRuntimeRegistry,
)
from app.core.config import Settings
from app.services.log_reader import redact_log_line


MAX_PROMPT_CHARS = 50_000
MAX_PROMPT_BYTES = 56 * 1024
MAX_SPEC_BYTES = 64 * 1024
MAX_STATE_BYTES = 16 * 1024
MAX_DELIVERY_BYTES = 4 * 1024
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
    "queued",
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
WorkerTaskKind = Literal["standard", "weixin", "translation", "test"]
TaskErrorSource = Literal["chub", "runtime"]
FINAL_STATUSES = {"succeeded", "failed", "timed_out", "cancelled"}
TASK_ID_PATTERN = r"^qw-[0-9]{13}-[a-f0-9]{32}$"
SESSION_ID_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$"
WORKSPACE_ID_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$"
EXECUTION_ID_PATTERN = r"^[a-f0-9]{32}$"
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


def worker_tasks_dir(settings: Settings, protocol_version: int) -> Path:
    return worker_state_dir(settings) / f"tasks-v{protocol_version}"


def worker_tombstones_dir(settings: Settings, protocol_version: int) -> Path:
    return worker_state_dir(settings) / f"tombstones-v{protocol_version}"


def worker_leases_dir(settings: Settings, protocol_version: int) -> Path:
    return worker_state_dir(settings) / f"session-leases-v{protocol_version}"


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


class RuntimeTaskSubmission(_StrictModel):
    task_id: str = Field(pattern=TASK_ID_PATTERN)
    runtime_id: str = Field(pattern=RUNTIME_ID_PATTERN)
    session_id: str = Field(pattern=SESSION_ID_PATTERN)
    workspace_id: str = Field(pattern=WORKSPACE_ID_PATTERN)
    prompt: str = Field(min_length=1, max_length=MAX_PROMPT_CHARS)
    permission_profile: PermissionProfile
    native_session_id: str | None = Field(default=None, min_length=1, max_length=128)
    model: str | None = Field(default=None, min_length=1, max_length=128)
    reasoning_effort: str | None = Field(default=None, min_length=1, max_length=32)
    timeout_seconds: float = Field(gt=0.0, le=24 * 60 * 60)
    task_kind: Literal["standard", "weixin", "translation"] = "standard"
    restart_sensitive: bool | None = None
    queue_key: str | None = Field(default=None, pattern=SESSION_ID_PATTERN)
    queue_limit: int | None = Field(default=None, ge=1, le=50)
    queue_wait_seconds: float | None = Field(default=None, gt=0.0, le=7200.0)

    @field_validator("prompt")
    @classmethod
    def prompt_fits_private_input(cls, value: str) -> str:
        if len(value.encode("utf-8")) > MAX_PROMPT_BYTES:
            raise ValueError("prompt exceeds the fixed byte limit")
        return value

    @model_validator(mode="after")
    def validate_queue_fields(self) -> RuntimeTaskSubmission:
        queue_fields = (self.queue_key, self.queue_limit, self.queue_wait_seconds)
        if self.task_kind == "translation":
            if any(item is None for item in queue_fields):
                raise ValueError("translation queue fields are required")
        elif any(item is not None for item in queue_fields):
            raise ValueError("queue fields are only valid for translation tasks")
        expected_restart_sensitive = (
            self.workspace_id == "chub" and self.permission_profile != "read-only"
        )
        if self.restart_sensitive is None:
            self.restart_sensitive = expected_restart_sensitive
        elif self.restart_sensitive != expected_restart_sensitive:
            raise ValueError("restart_sensitive does not match the fixed workspace rule")
        return self


class StoredTaskSpec(_StrictModel):
    protocol_version: int
    task_id: str = Field(pattern=TASK_ID_PATTERN)
    runtime_id: str = Field(pattern=RUNTIME_ID_PATTERN)
    prompt: str = Field(min_length=1, max_length=MAX_PROMPT_CHARS)
    prompt_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    spec_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    test_behavior: TestBehavior | None = None
    test_run_seconds: float | None = None
    session_id: str | None = Field(default=None, pattern=SESSION_ID_PATTERN)
    workspace_id: str | None = Field(default=None, pattern=WORKSPACE_ID_PATTERN)
    permission_profile: PermissionProfile | None = None
    native_session_id: str | None = Field(default=None, min_length=1, max_length=128)
    model: str | None = Field(default=None, min_length=1, max_length=128)
    reasoning_effort: str | None = Field(default=None, min_length=1, max_length=32)
    timeout_seconds: float
    task_kind: WorkerTaskKind
    restart_sensitive: bool = False
    queue_key: str | None = Field(default=None, pattern=SESSION_ID_PATTERN)
    queue_limit: int | None = Field(default=None, ge=1, le=50)
    queue_wait_seconds: float | None = Field(default=None, gt=0.0, le=7200.0)
    created_at: datetime
    deadline_at: datetime
    queue_deadline_at: datetime | None = None

    @model_validator(mode="after")
    def validate_runtime_fields(self) -> StoredTaskSpec:
        fixed_fields = (self.test_behavior, self.test_run_seconds)
        runtime_fields = (
            self.session_id,
            self.workspace_id,
            self.permission_profile,
        )
        if self.runtime_id == "fixed-test":
            if any(item is None for item in fixed_fields) or any(
                item is not None
                for item in (
                    *runtime_fields,
                    self.native_session_id,
                    self.model,
                    self.reasoning_effort,
                )
            ) or self.task_kind != "test":
                raise ValueError("fixed test task fields are inconsistent")
        elif any(item is not None for item in fixed_fields) or any(
            item is None for item in runtime_fields
        ) or self.task_kind == "test":
            raise ValueError("Runtime task fields are inconsistent")
        if self.runtime_id == "fixed-test" and self.restart_sensitive:
            raise ValueError("fixed test tasks cannot be restart sensitive")
        if (
            self.runtime_id != "fixed-test"
            and self.restart_sensitive
            != (self.workspace_id == "chub" and self.permission_profile != "read-only")
        ):
            raise ValueError("restart_sensitive does not match the fixed workspace rule")
        queue_fields = (self.queue_key, self.queue_limit, self.queue_wait_seconds)
        if self.task_kind == "translation":
            if any(item is None for item in queue_fields) or self.queue_deadline_at is None:
                raise ValueError("translation queue fields are inconsistent")
        elif any(item is not None for item in (*queue_fields, self.queue_deadline_at)):
            raise ValueError("non-translation task has queue fields")
        return self


class StoredTaskState(_StrictModel):
    task_id: str = Field(pattern=TASK_ID_PATTERN)
    runtime_id: str = Field(pattern=RUNTIME_ID_PATTERN)
    spec_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    status: TaskStatus
    worker_generation: str
    execution_id: str | None = Field(default=None, pattern=EXECUTION_ID_PATTERN)
    runner_pid: int | None = Field(default=None, ge=1)
    runner_created_at: float | None = Field(default=None, gt=0)
    native_session_id: str | None = Field(default=None, min_length=1, max_length=128)
    expected_native_session_id: str | None = Field(default=None, min_length=1, max_length=128)
    execution_deadline_at: datetime | None = None
    cancellation_requested: bool = False
    updated_at: datetime


class StoredRuntimeEvent(_StrictModel):
    task_id: str = Field(pattern=TASK_ID_PATTERN)
    runtime_id: str = Field(pattern=RUNTIME_ID_PATTERN)
    spec_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    execution_id: str = Field(pattern=EXECUTION_ID_PATTERN)
    native_session_id: str = Field(min_length=1, max_length=128)
    observed_at: datetime


class StoredTaskCompletion(_StrictModel):
    task_id: str = Field(pattern=TASK_ID_PATTERN)
    runtime_id: str = Field(pattern=RUNTIME_ID_PATTERN)
    spec_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    execution_id: str | None = Field(default=None, pattern=EXECUTION_ID_PATTERN)
    status: FinalTaskStatus
    result: str | None = None
    error: str | None = None
    error_source: TaskErrorSource | None = None
    error_code: str | None = Field(default=None, max_length=64)
    exit_code: int | None = None
    native_session_id: str | None = Field(default=None, min_length=1, max_length=128)
    completed_at: datetime


class TaskTombstone(_StrictModel):
    protocol_version: int
    task_id: str = Field(pattern=TASK_ID_PATTERN)
    runtime_id: str = Field(pattern=RUNTIME_ID_PATTERN)
    spec_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    completed_at: datetime
    expires_at: datetime


class SessionLease(_StrictModel):
    session_id: str = Field(pattern=SESSION_ID_PATTERN)
    task_id: str = Field(pattern=TASK_ID_PATTERN)
    runtime_id: str = Field(pattern=RUNTIME_ID_PATTERN)
    spec_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    created_at: datetime


class WorkerTaskView(_StrictModel):
    task_id: str = Field(pattern=TASK_ID_PATTERN)
    runtime_id: str = Field(pattern=RUNTIME_ID_PATTERN)
    status: TaskStatus
    prompt_sha256: str
    created_at: datetime
    deadline_at: datetime
    updated_at: datetime
    worker_generation: str
    execution_id: str | None = Field(default=None, pattern=EXECUTION_ID_PATTERN)
    runner_pid: int | None = None
    cancellation_requested: bool
    result: str | None = None
    error: str | None = None
    error_source: TaskErrorSource | None = None
    error_code: str | None = None
    exit_code: int | None = None
    native_session_id: str | None = None
    restart_sensitive: bool = False


class WorkerTaskSummary(_StrictModel):
    task_id: str = Field(pattern=TASK_ID_PATTERN)
    runtime_id: str = Field(pattern=RUNTIME_ID_PATTERN)
    status: TaskStatus
    prompt_sha256: str
    session_id: str | None = None
    task_kind: WorkerTaskKind | None = None
    execution_id: str | None = Field(default=None, pattern=EXECUTION_ID_PATTERN)
    restart_sensitive: bool = False
    native_session_id: str | None = None
    delivery_acknowledged: bool = False
    created_at: datetime
    updated_at: datetime


class WorkerTaskDelivery(_StrictModel):
    task_id: str = Field(pattern=TASK_ID_PATTERN)
    spec_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    delivered_at: datetime


class WorkerTaskError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class _RuntimeBoundaryError(OSError):
    """A Runtime contract error preserved through the Worker supervision boundary."""

    def __init__(self, error: RuntimeOperationError) -> None:
        super().__init__(error.message)
        self.code = error.code
        self.message = error.message


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


def _digest_submission(submission: TestTaskSubmission | RuntimeTaskSubmission) -> str:
    if isinstance(submission, TestTaskSubmission):
        payload: dict[str, object] = {
            "runtime_id": "fixed-test",
            "prompt": submission.prompt,
            "test_behavior": submission.behavior,
            "test_run_seconds": submission.run_seconds,
            "timeout_seconds": submission.timeout_seconds,
        }
    else:
        payload = submission.model_dump(mode="json", exclude={"task_id"})
    return hashlib.sha256(_canonical_json(payload)).hexdigest()


def _digest_stored_spec(spec: StoredTaskSpec) -> str:
    if spec.runtime_id == "fixed-test":
        payload = {
            "runtime_id": spec.runtime_id,
            "prompt": spec.prompt,
            "test_behavior": spec.test_behavior,
            "test_run_seconds": spec.test_run_seconds,
            "timeout_seconds": spec.timeout_seconds,
        }
    else:
        payload = {
            "runtime_id": spec.runtime_id,
            "session_id": spec.session_id,
            "workspace_id": spec.workspace_id,
            "prompt": spec.prompt,
            "permission_profile": spec.permission_profile,
            "native_session_id": spec.native_session_id,
            "model": spec.model,
            "reasoning_effort": spec.reasoning_effort,
            "timeout_seconds": spec.timeout_seconds,
            "task_kind": spec.task_kind,
            "queue_key": spec.queue_key,
            "queue_limit": spec.queue_limit,
            "queue_wait_seconds": spec.queue_wait_seconds,
            "restart_sensitive": spec.restart_sensitive,
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
        runtime_registry: WorkerRuntimeRegistry,
        allow_test_tasks: bool = False,
    ) -> None:
        self.root = worker_state_dir(settings)
        self.tasks_dir = worker_tasks_dir(settings, protocol_version)
        self.tombstones_dir = worker_tombstones_dir(settings, protocol_version)
        self.leases_dir = worker_leases_dir(settings, protocol_version)
        self.hook_dir = settings.codex_pty.runtime_dir / "hooks"
        self.restart_request_dir = settings.codex_pty.runtime_dir / "restart-requests"
        self.generation = generation
        self.protocol_version = protocol_version
        self.allow_test_tasks = allow_test_tasks
        self.runtime_registry = runtime_registry
        configured_token = settings.security.token
        self._sensitive_values = (
            (configured_token.get_secret_value(),)
            if configured_token is not None
            else ()
        )
        self._lock = asyncio.Lock()
        self._supervisors: dict[str, asyncio.Task[None]] = {}
        self._processes: dict[str, asyncio.subprocess.Process] = {}
        self._corrupt_task_ids: set[str] = set()
        self._recovery_task_ids: set[str] = set()
        self._abandoning = False

    @property
    def active_count(self) -> int:
        return len(self._supervisors)

    @property
    def corrupt_count(self) -> int:
        return len(self._corrupt_task_ids)

    @property
    def queued_count(self) -> int:
        count = 0
        for task_id in self._supervisors:
            try:
                _spec, state, completion = self._records(task_id)
            except (FileNotFoundError, OSError, ValidationError, ValueError):
                continue
            if completion is None and state.status == "queued":
                count += 1
        return count

    @property
    def running_count(self) -> int:
        return max(0, self.active_count - self.queued_count)

    async def wait_until_idle(self) -> None:
        while self._supervisors:
            await asyncio.sleep(0.02)

    async def start(self) -> None:
        _private_directory(self.root)
        _private_directory(self.tasks_dir)
        _private_directory(self.tombstones_dir)
        _private_directory(self.leases_dir)
        _private_directory(self.hook_dir)
        _private_directory(self.restart_request_dir)
        self._validate_runtime_registry()
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
        self.runtime_registry.require("fixed-test")
        return await self._submit(submission, runtime_id="fixed-test")

    async def submit_runtime(self, submission: RuntimeTaskSubmission) -> WorkerTaskView:
        try:
            runner = self.runtime_registry.require(submission.runtime_id)
            runner.validate_turn(
                submission.workspace_id,
                RuntimeTurnRequest(
                    permission_profile=submission.permission_profile,
                    native_session_id=submission.native_session_id,
                    model=submission.model,
                    reasoning_effort=submission.reasoning_effort,
                ),
            )
        except RuntimeOperationError as exc:
            raise WorkerTaskError(exc.code, exc.message) from exc
        return await self._submit(submission, runtime_id=submission.runtime_id)

    async def _submit(
        self,
        submission: TestTaskSubmission | RuntimeTaskSubmission,
        *,
        runtime_id: str,
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
                if spec.spec_sha256 != spec_digest or spec.runtime_id != runtime_id:
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
                if (
                    tombstone.task_id != submission.task_id
                    or tombstone.protocol_version != self.protocol_version
                    or tombstone.runtime_id != runtime_id
                ):
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

            queued_translation = (
                isinstance(submission, RuntimeTaskSubmission)
                and submission.task_kind == "translation"
            )
            if not queued_translation and len(self._running_task_ids()) >= MAX_ACTIVE_TASKS:
                raise WorkerTaskError(
                    "worker_capacity_reached",
                    "Worker has reached its fixed active task limit",
                )
            if self._task_directory_count() >= MAX_TASK_DIRECTORIES:
                raise WorkerTaskError(
                    "worker_store_oversized",
                    "Worker task store has reached its fixed limit",
                )

            if queued_translation:
                self._ensure_queue_capacity(submission)
            elif isinstance(submission, RuntimeTaskSubmission):
                self._ensure_session_available(submission.session_id)

            task_dir = self.tasks_dir / submission.task_id
            task_dir.mkdir(mode=0o700)
            os.chmod(task_dir, 0o700)
            prompt_sha256 = hashlib.sha256(submission.prompt.encode("utf-8")).hexdigest()
            spec = StoredTaskSpec(
                protocol_version=self.protocol_version,
                task_id=submission.task_id,
                runtime_id=runtime_id,
                prompt=submission.prompt,
                prompt_sha256=prompt_sha256,
                spec_sha256=spec_digest,
                test_behavior=(
                    submission.behavior
                    if isinstance(submission, TestTaskSubmission)
                    else None
                ),
                test_run_seconds=(
                    submission.run_seconds
                    if isinstance(submission, TestTaskSubmission)
                    else None
                ),
                session_id=(
                    submission.session_id
                    if isinstance(submission, RuntimeTaskSubmission)
                    else None
                ),
                workspace_id=(
                    submission.workspace_id
                    if isinstance(submission, RuntimeTaskSubmission)
                    else None
                ),
                permission_profile=(
                    submission.permission_profile
                    if isinstance(submission, RuntimeTaskSubmission)
                    else None
                ),
                native_session_id=(
                    submission.native_session_id
                    if isinstance(submission, RuntimeTaskSubmission)
                    else None
                ),
                model=(submission.model if isinstance(submission, RuntimeTaskSubmission) else None),
                reasoning_effort=(
                    submission.reasoning_effort
                    if isinstance(submission, RuntimeTaskSubmission)
                    else None
                ),
                timeout_seconds=submission.timeout_seconds,
                task_kind=(
                    submission.task_kind
                    if isinstance(submission, RuntimeTaskSubmission)
                    else "test"
                ),
                restart_sensitive=(
                    bool(submission.restart_sensitive)
                    if isinstance(submission, RuntimeTaskSubmission)
                    else False
                ),
                queue_key=(
                    submission.queue_key
                    if isinstance(submission, RuntimeTaskSubmission)
                    else None
                ),
                queue_limit=(
                    submission.queue_limit
                    if isinstance(submission, RuntimeTaskSubmission)
                    else None
                ),
                queue_wait_seconds=(
                    submission.queue_wait_seconds
                    if isinstance(submission, RuntimeTaskSubmission)
                    else None
                ),
                created_at=now,
                deadline_at=now + timedelta(
                    seconds=(
                        submission.timeout_seconds
                        + (
                            (submission.queue_wait_seconds or 0)
                            if isinstance(submission, RuntimeTaskSubmission)
                            else 0
                        )
                    )
                ),
                queue_deadline_at=(
                    now + timedelta(seconds=submission.queue_wait_seconds)
                    if queued_translation and submission.queue_wait_seconds is not None
                    else None
                ),
            )
            state = StoredTaskState(
                task_id=submission.task_id,
                runtime_id=runtime_id,
                spec_sha256=spec_digest,
                status="queued" if queued_translation else "accepted",
                worker_generation=self.generation,
                updated_at=now,
            )
            try:
                _write_model(task_dir / "spec.json", spec, max_bytes=MAX_SPEC_BYTES)
                if spec.session_id is not None and spec.task_kind != "translation":
                    self._write_lease(
                        SessionLease(
                            session_id=spec.session_id,
                            task_id=spec.task_id,
                            runtime_id=spec.runtime_id,
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
            self._recovery_task_ids.add(submission.task_id)
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

    def list(
        self,
        *,
        limit: int,
        active_only: bool = False,
        recovery_only: bool = False,
    ) -> list[WorkerTaskSummary]:
        summaries: list[WorkerTaskSummary] = []
        if recovery_only:
            if self._corrupt_task_ids:
                raise WorkerTaskError(
                    "worker_task_store_corrupt",
                    "Worker task store contains an invalid record",
                )
            if len(self._recovery_task_ids) > limit:
                raise WorkerTaskError(
                    "worker_recovery_set_oversized",
                    "Worker recovery task set exceeds the fixed response limit",
                )
            entries = [self.tasks_dir / task_id for task_id in self._recovery_task_ids]
        else:
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
        corrupt_found = False
        for entry in entries:
            if not entry.is_dir() or entry.is_symlink():
                continue
            try:
                spec, state, completion = self._records(entry.name)
                view = self._view_from(spec, state, completion)
                delivery_acknowledged = self._delivery_acknowledged(spec, completion)
            except WorkerTaskError:
                corrupt_found = True
                continue
            except (FileNotFoundError, OSError, ValidationError, ValueError):
                self._corrupt_task_ids.add(entry.name)
                corrupt_found = True
                continue
            if active_only and view.status in FINAL_STATUSES:
                continue
            if recovery_only and view.status in FINAL_STATUSES and delivery_acknowledged:
                continue
            summaries.append(
                WorkerTaskSummary(
                    task_id=view.task_id,
                    runtime_id=view.runtime_id,
                    status=view.status,
                    prompt_sha256=view.prompt_sha256,
                    session_id=spec.session_id,
                    task_kind=spec.task_kind,
                    execution_id=view.execution_id,
                    restart_sensitive=spec.restart_sensitive,
                    native_session_id=view.native_session_id,
                    delivery_acknowledged=delivery_acknowledged,
                    created_at=view.created_at,
                    updated_at=view.updated_at,
                )
            )
        if corrupt_found:
            raise WorkerTaskError(
                "worker_task_store_corrupt",
                "Worker task store contains an invalid record",
            )
        summaries.sort(key=lambda item: (item.created_at, item.task_id), reverse=True)
        return summaries[:limit]

    def _delivery_acknowledged(
        self,
        spec: StoredTaskSpec,
        completion: StoredTaskCompletion | None,
    ) -> bool:
        if completion is None:
            return False
        try:
            delivery = _read_model(
                self.tasks_dir / spec.task_id / "delivery.json",
                WorkerTaskDelivery,
                max_bytes=MAX_DELIVERY_BYTES,
            )
        except FileNotFoundError:
            return False
        except (OSError, ValidationError, ValueError):
            self._corrupt_task_ids.add(spec.task_id)
            raise WorkerTaskError(
                "worker_delivery_corrupt",
                "Worker delivery acknowledgement is invalid",
            ) from None
        if delivery.task_id != spec.task_id or delivery.spec_sha256 != spec.spec_sha256:
            self._corrupt_task_ids.add(spec.task_id)
            raise WorkerTaskError(
                "worker_delivery_corrupt",
                "Worker delivery acknowledgement does not match the task",
            )
        return True

    def acknowledge_delivery(self, task_id: str) -> WorkerTaskDelivery:
        self._validate_task_id(task_id)
        try:
            spec, state, completion = self._records(task_id)
        except FileNotFoundError:
            raise WorkerTaskError(
                "worker_task_not_found",
                "Worker task was not found",
            ) from None
        except (OSError, ValidationError, ValueError):
            self._corrupt_task_ids.add(task_id)
            raise WorkerTaskError(
                "worker_task_corrupt",
                "Worker task record is invalid and cannot be acknowledged",
            ) from None
        if completion is None or state.status not in FINAL_STATUSES:
            raise WorkerTaskError(
                "worker_task_not_final",
                "Worker task is not ready for delivery acknowledgement",
            )
        path = self.tasks_dir / task_id / "delivery.json"
        try:
            delivery = _read_model(
                path,
                WorkerTaskDelivery,
                max_bytes=MAX_DELIVERY_BYTES,
            )
        except FileNotFoundError:
            delivery = WorkerTaskDelivery(
                task_id=task_id,
                spec_sha256=spec.spec_sha256,
                delivered_at=utc_now(),
            )
            _write_model(path, delivery, max_bytes=MAX_DELIVERY_BYTES)
        except (OSError, ValidationError, ValueError):
            raise WorkerTaskError(
                "worker_delivery_corrupt",
                "Worker delivery acknowledgement is invalid",
            ) from None
        if delivery.task_id != task_id or delivery.spec_sha256 != spec.spec_sha256:
            raise WorkerTaskError(
                "worker_delivery_corrupt",
                "Worker delivery acknowledgement does not match the task",
            )
        self._recovery_task_ids.discard(task_id)
        return delivery

    def _running_task_ids(self) -> set[str]:
        running: set[str] = set()
        for task_id in self._supervisors:
            try:
                _spec, state, completion = self._records(task_id)
            except (FileNotFoundError, OSError, ValidationError, ValueError):
                continue
            if completion is None and state.status in {"accepted", "starting", "running"}:
                running.add(task_id)
        return running

    def _ensure_queue_capacity(self, submission: RuntimeTaskSubmission) -> None:
        if submission.queue_key is None or submission.queue_limit is None:
            raise WorkerTaskError(
                "worker_queue_invalid", "Translation queue configuration is incomplete"
            )
        active = 0
        for entry in self.tasks_dir.iterdir():
            if entry.is_symlink() or not entry.is_dir():
                continue
            try:
                spec, state, completion = self._records(entry.name)
            except (FileNotFoundError, OSError, ValidationError, ValueError):
                continue
            if (
                completion is None
                and spec.task_kind == "translation"
                and spec.queue_key == submission.queue_key
                and state.status in {"queued", "accepted", "starting", "running"}
            ):
                active += 1
        if active >= submission.queue_limit:
            raise WorkerTaskError(
                "worker_queue_capacity_reached",
                "Translation queue has reached its fixed capacity",
            )

    async def _wait_for_queue_turn(self, task_id: str) -> bool:
        while True:
            should_wait = False
            async with self._lock:
                spec, state, completion = self._records(task_id)
                if completion is not None:
                    return False
                if spec.task_kind != "translation":
                    return True
                if state.cancellation_requested:
                    self._finalize(
                        spec,
                        state,
                        status="cancelled",
                        error_code="cancelled",
                        error="Task was cancelled before execution started.",
                    )
                    return False
                if spec.queue_deadline_at is None or utc_now() >= spec.queue_deadline_at:
                    self._finalize(
                        spec,
                        state,
                        status="timed_out",
                        error_code="queue_deadline_exceeded",
                        error="Translation task reached its absolute queue deadline.",
                    )
                    return False
                earlier_active = False
                latest_native: tuple[datetime, str] | None = None
                for entry in self.tasks_dir.iterdir():
                    if entry.name == task_id or entry.is_symlink() or not entry.is_dir():
                        continue
                    try:
                        other_spec, other_state, other_completion = self._records(entry.name)
                    except (FileNotFoundError, OSError, ValidationError, ValueError):
                        continue
                    if (
                        other_spec.task_kind != "translation"
                        or other_spec.queue_key != spec.queue_key
                    ):
                        continue
                    if (other_spec.created_at, other_spec.task_id) < (
                        spec.created_at,
                        spec.task_id,
                    ):
                        if other_completion is None:
                            earlier_active = True
                        if other_spec.session_id != spec.session_id:
                            continue
                        native_id = (
                            other_completion.native_session_id
                            if other_completion is not None
                            else other_state.native_session_id
                        )
                        if native_id is not None and (
                            latest_native is None
                            or other_spec.created_at > latest_native[0]
                        ):
                            latest_native = (other_spec.created_at, native_id)
                if earlier_active or len(self._running_task_ids()) >= MAX_ACTIVE_TASKS:
                    should_wait = True
                else:
                    candidate_native_session_id = (
                        latest_native[1] if latest_native is not None else spec.native_session_id
                    )
                    state.expected_native_session_id = (
                        candidate_native_session_id
                        if candidate_native_session_id is not None
                        and self._native_session_available(
                            spec.runtime_id,
                            candidate_native_session_id,
                        )
                        else None
                    )
                    try:
                        self._write_lease(
                            SessionLease(
                                session_id=spec.session_id or "",
                                task_id=spec.task_id,
                                runtime_id=spec.runtime_id,
                                spec_sha256=spec.spec_sha256,
                                created_at=utc_now(),
                            )
                        )
                    except WorkerTaskError as exc:
                        if exc.code != "worker_session_busy":
                            raise
                        should_wait = True
                    else:
                        state.status = "accepted"
                        state.updated_at = utc_now()
                        self._write_state(state)
                        return True
            if should_wait:
                await asyncio.sleep(0.05)

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
                try:
                    delivered = self._delivery_acknowledged(spec, completion)
                except WorkerTaskError:
                    self._corrupt_task_ids.add(task_id)
                    continue
                if not delivered:
                    self._recovery_task_ids.add(task_id)
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
            if spec.task_kind != "test":
                runner = self.runtime_registry.require(spec.runtime_id)
                native_session_id = self._extract_native_session_id(
                    runner,
                    self.tasks_dir / task_id / "stdout.txt",
                    missing_ok=True,
                )
                expected_native_id = self._expected_native_session_id(spec, state)
                if native_session_id is not None and (
                    expected_native_id is None
                    or native_session_id == expected_native_id
                ):
                    state.native_session_id = native_session_id
                    state.updated_at = utc_now()
                    self._write_state(state)
                    self._write_runtime_event(spec, state, native_session_id)
            self._finalize(
                spec,
                state,
                status="failed",
                error_code="worker_restarted",
                error="Quick Worker restarted before completion; the task was not replayed.",
                error_source="chub",
            )

    async def _launch_and_monitor(self, task_id: str) -> None:
        process: asyncio.subprocess.Process | None = None
        release_read: int | None = None
        release_write: int | None = None
        stdout_file = None
        stderr_file = None
        native_observer: asyncio.Task[None] | None = None
        runner = None
        try:
            if not await self._wait_for_queue_turn(task_id):
                return
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
                try:
                    runner = self.runtime_registry.require(spec.runtime_id)
                except RuntimeOperationError as exc:
                    raise _RuntimeBoundaryError(exc) from exc
                expected_native_id = self._expected_native_session_id(spec, state)
                if expected_native_id is not None:
                    try:
                        active_writer = runner.has_active_writer(expected_native_id)
                    except RuntimeOperationError as exc:
                        raise _RuntimeBoundaryError(exc) from exc
                    if active_writer:
                        self._finalize(
                            spec,
                            state,
                            status="failed",
                            error_code="runtime_session_busy",
                            error=(
                                "Runtime Session already has an active writer; "
                                "the task was not started."
                            ),
                            error_source="chub",
                        )
                        return
                task_dir = self.tasks_dir / task_id
                stdout_path = task_dir / "stdout.txt"
                stderr_path = task_dir / "stderr.txt"
                stdout_file = self._open_private_output(stdout_path)
                stderr_file = self._open_private_output(stderr_path)
                release_read, release_write = os.pipe()
                turn = (
                    RuntimeTurnRequest(
                        permission_profile=spec.permission_profile,
                        native_session_id=expected_native_id,
                        model=spec.model,
                        reasoning_effort=spec.reasoning_effort,
                    )
                    if spec.permission_profile is not None
                    else None
                )
                state.execution_id = uuid.uuid4().hex
                state.updated_at = utc_now()
                self._write_state(state)
                try:
                    launch = runner.build_launch(
                        RuntimeWorkerLaunchRequest(
                            task_id=spec.task_id,
                            task_dir=task_dir,
                            release_fd=release_read,
                            session_id=spec.session_id,
                            task_kind=spec.task_kind,
                            workspace_id=spec.workspace_id,
                            turn=turn,
                            start_new_session=(
                                spec.task_kind == "translation"
                                and expected_native_id is None
                            ),
                            hook_dir=self.hook_dir,
                            restart_request_dir=self.restart_request_dir,
                            test_behavior=spec.test_behavior,
                            test_run_seconds=spec.test_run_seconds,
                        )
                    )
                except RuntimeOperationError as exc:
                    raise _RuntimeBoundaryError(exc) from exc
                runner_env = os.environ.copy()
                for name in (
                    "CHUB_PTY_SESSION_ID",
                    "CHUB_PTY_HOOK_DIR",
                    "CHUB_ACTIVITY_SOURCE",
                    "CHUB_QUICK_TASK_ID",
                    "CHUB_QUICK_RESTART_DIR",
                ):
                    runner_env.pop(name, None)
                runner_env.update(launch.environment)
                process = await asyncio.create_subprocess_exec(
                    *launch.argv,
                    cwd=Path(__file__).resolve().parents[1],
                    env=runner_env,
                    stdin=(
                        asyncio.subprocess.PIPE
                        if launch.stdin_prompt
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
                state.execution_deadline_at = (
                    utc_now() + timedelta(seconds=spec.timeout_seconds)
                    if spec.task_kind == "translation"
                    else spec.deadline_at
                )
                state.worker_generation = self.generation
                state.runner_pid = process.pid
                state.runner_created_at = runner_created_at
                state.updated_at = utc_now()
                self._write_state(state)
                self._processes[task_id] = process
                os.write(release_write, b"1")
                os.close(release_write)
                release_write = None
                if launch.stdin_prompt:
                    if process.stdin is None:
                        raise OSError("Runtime Runner input pipe is unavailable")
                    process.stdin.write(spec.prompt.encode("utf-8"))
                    await process.stdin.drain()
                    process.stdin.close()
                state.status = "running"
                state.updated_at = utc_now()
                self._write_state(state)

            if spec.task_kind != "test":
                native_observer = asyncio.create_task(
                    self._observe_native_session(task_id),
                    name=f"quick-worker-native-session-{task_id}",
                )
            execution_deadline = state.execution_deadline_at or spec.deadline_at
            remaining = max(0.0, (execution_deadline - utc_now()).total_seconds())
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
                if runner is None:
                    raise OSError("Runtime Runner is unavailable")
                runtime_error = (
                    self._redact_error(
                        self._read_runner_error(
                            runner,
                            self.tasks_dir / task_id,
                        )
                    )
                    if exit_code != 0
                    else None
                )
                native_session_id = (
                    self._extract_native_session_id(
                        runner,
                        self.tasks_dir / task_id / "stdout.txt",
                    )
                    if exit_code == 0 and spec.task_kind != "test"
                    else None
                )
                expected_native_id = self._expected_native_session_id(spec, state)
                native_session_confirmed = native_session_id is not None and (
                    expected_native_id is None
                    or native_session_id == expected_native_id
                )
                if native_session_confirmed:
                    state.native_session_id = native_session_id
                    state.updated_at = utc_now()
                    self._write_state(state)
                    self._write_runtime_event(spec, state, native_session_id)
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
                    try:
                        result = runner.read_result(
                            self.tasks_dir / task_id,
                            max_bytes=MAX_RESULT_BYTES,
                        ).text
                    except RuntimeOperationError as exc:
                        raise _RuntimeBoundaryError(exc) from exc
                    if spec.task_kind != "test" and not native_session_confirmed:
                        self._finalize(
                            spec,
                            state,
                            status="failed",
                            result=result,
                            error_code="native_session_unconfirmed",
                            error=(
                                "Runtime completed, but its native Session ID could "
                                "not be confirmed safely."
                            ),
                            error_source="chub",
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
                    error = runtime_error
                    if not error:
                        error = self._redact_error(
                            self._read_limited_file(
                                self.tasks_dir / task_id / "stderr.txt",
                                MAX_ERROR_BYTES,
                            )
                        )
                    error_source: TaskErrorSource = "runtime" if error else "chub"
                    self._finalize(
                        spec,
                        state,
                        status="failed",
                        error_code="runner_failed",
                        error=error or "Task runner exited unsuccessfully.",
                        error_source=error_source,
                        exit_code=exit_code,
                    )
        except asyncio.CancelledError:
            if not self._abandoning and process is not None:
                await self._terminate_process(process)
            raise
        except Exception as exc:
            if process is not None:
                await self._terminate_process(process)
            if not self._abandoning:
                try:
                    async with self._lock:
                        spec, state, completion = self._records(task_id)
                        if completion is None:
                            error_code, error, error_source = (
                                self._runner_failure_details(exc)
                            )
                            self._finalize(
                                spec,
                                state,
                                status="failed",
                                error_code=error_code,
                                error=error,
                                error_source=error_source,
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
                spec = self._read_spec(task_id)
                runner = self.runtime_registry.require(spec.runtime_id)
                native_session_id = self._extract_native_session_id(
                    runner,
                    path,
                    missing_ok=True,
                )
                if native_session_id is not None:
                    async with self._lock:
                        spec, state, completion = self._records(task_id)
                        if completion is not None:
                            return
                        expected_native_id = self._expected_native_session_id(spec, state)
                        if (
                            expected_native_id is not None
                            and native_session_id != expected_native_id
                        ):
                            return
                        state.native_session_id = native_session_id
                        state.updated_at = utc_now()
                        self._write_state(state)
                        self._write_runtime_event(spec, state, native_session_id)
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
                    error_source="chub",
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
        try:
            runtime_event = _read_model(
                self.tasks_dir / task_id / "runtime-event.json",
                StoredRuntimeEvent,
                max_bytes=MAX_STATE_BYTES,
            )
        except FileNotFoundError:
            runtime_event = None
        if spec.task_id != task_id or state.task_id != task_id:
            raise ValueError("task record identity does not match its directory")
        if spec.runtime_id != state.runtime_id:
            raise ValueError("task Runtime identity does not match")
        if spec.spec_sha256 != state.spec_sha256:
            raise ValueError("task specification and state do not match")
        if (state.runner_pid is None) != (state.runner_created_at is None):
            raise ValueError("task runner identity is incomplete")
        if spec.created_at.tzinfo is None or spec.deadline_at.tzinfo is None:
            raise ValueError("task timestamps must include a timezone")
        if spec.deadline_at <= spec.created_at:
            raise ValueError("task deadline is inconsistent")
        expected_deadline = spec.created_at + timedelta(
            seconds=spec.timeout_seconds + (spec.queue_wait_seconds or 0)
        )
        if spec.deadline_at != expected_deadline:
            raise ValueError("task deadline does not match its specification")
        if spec.queue_deadline_at is not None:
            if spec.queue_deadline_at.tzinfo is None:
                raise ValueError("task queue deadline must include a timezone")
            expected_queue_deadline = spec.created_at + timedelta(
                seconds=spec.queue_wait_seconds or 0
            )
            if spec.queue_deadline_at != expected_queue_deadline:
                raise ValueError("task queue deadline does not match its specification")
        if state.updated_at.tzinfo is None or state.updated_at < spec.created_at:
            raise ValueError("task state timestamp is inconsistent")
        if state.execution_deadline_at is not None:
            if (
                state.execution_deadline_at.tzinfo is None
                or state.execution_deadline_at < spec.created_at
                or state.execution_deadline_at > spec.deadline_at
            ):
                raise ValueError("task execution deadline is inconsistent")
        if state.status in {"starting", "running"} and (
            state.runner_pid is None
            or state.execution_deadline_at is None
            or state.execution_id is None
        ):
            raise ValueError("running task state is incomplete")
        if state.status == "queued" and any(
            item is not None
            for item in (
                state.runner_pid,
                state.execution_id,
                state.expected_native_session_id,
                state.execution_deadline_at,
                state.native_session_id,
            )
        ):
            raise ValueError("queued task state contains execution identity")
        expected_native_id = self._expected_native_session_id(spec, state)
        if (
            state.native_session_id is not None
            and expected_native_id is not None
            and state.native_session_id != expected_native_id
        ):
            raise ValueError("task native Session identity is inconsistent")
        if state.status in FINAL_STATUSES and state.runner_pid is not None:
            raise ValueError("final task state still has a runner identity")
        if runtime_event is not None and (
            runtime_event.task_id != task_id
            or runtime_event.runtime_id != spec.runtime_id
            or runtime_event.spec_sha256 != spec.spec_sha256
            or runtime_event.execution_id != state.execution_id
            or runtime_event.native_session_id != state.native_session_id
            or runtime_event.observed_at.tzinfo is None
            or runtime_event.observed_at < spec.created_at
            or runtime_event.observed_at > state.updated_at
        ):
            raise ValueError("task Runtime event identity is inconsistent")
        if completion is not None:
            if completion.task_id != task_id:
                raise ValueError("task completion identity does not match its directory")
            if completion.runtime_id != spec.runtime_id:
                raise ValueError("task completion Runtime identity does not match")
            if completion.spec_sha256 != spec.spec_sha256:
                raise ValueError("task completion does not match its specification")
            if completion.execution_id != state.execution_id:
                raise ValueError("task execution identity does not match")
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
            runtime_id=spec.runtime_id,
            status=state.status,
            prompt_sha256=spec.prompt_sha256,
            created_at=spec.created_at,
            deadline_at=spec.deadline_at,
            updated_at=state.updated_at,
            worker_generation=state.worker_generation,
            execution_id=state.execution_id,
            runner_pid=state.runner_pid,
            cancellation_requested=state.cancellation_requested,
            result=completion.result if completion else None,
            error=completion.error if completion else None,
            error_source=completion.error_source if completion else None,
            error_code=completion.error_code if completion else None,
            exit_code=completion.exit_code if completion else None,
            native_session_id=state.native_session_id,
            restart_sensitive=spec.restart_sensitive,
        )

    def _write_state(self, state: StoredTaskState) -> None:
        _write_model(
            self.tasks_dir / state.task_id / "state.json",
            state,
            max_bytes=MAX_STATE_BYTES,
        )

    def _write_runtime_event(
        self,
        spec: StoredTaskSpec,
        state: StoredTaskState,
        native_session_id: str,
    ) -> None:
        if state.execution_id is None:
            raise OSError("Runtime event is missing its execution identity")
        _write_model(
            self.tasks_dir / spec.task_id / "runtime-event.json",
            StoredRuntimeEvent(
                task_id=spec.task_id,
                runtime_id=spec.runtime_id,
                spec_sha256=spec.spec_sha256,
                execution_id=state.execution_id,
                native_session_id=native_session_id,
                observed_at=state.updated_at,
            ),
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
        error_source: TaskErrorSource | None = None,
        error_code: str | None = None,
        exit_code: int | None = None,
    ) -> None:
        if state.status in FINAL_STATUSES:
            return
        completed_at = utc_now()
        lease_expected = state.status != "queued"
        completion = StoredTaskCompletion(
            task_id=spec.task_id,
            runtime_id=spec.runtime_id,
            spec_sha256=spec.spec_sha256,
            execution_id=state.execution_id,
            status=status,
            result=_limited_text(result, MAX_RESULT_BYTES) if result is not None else None,
            error=self._redact_error(error) if error is not None else None,
            error_source=error_source,
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
        self._recovery_task_ids.add(spec.task_id)
        tombstone = TaskTombstone(
            protocol_version=self.protocol_version,
            task_id=spec.task_id,
            runtime_id=spec.runtime_id,
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
            try:
                self._release_lease(spec)
            except FileNotFoundError:
                if lease_expected:
                    raise

    def _task_directory_count(self) -> int:
        try:
            entries = list(self.tasks_dir.iterdir())
        except OSError as exc:
            raise WorkerTaskError(
                "worker_store_unavailable",
                "Worker task store could not be read",
            ) from exc
        total = len(entries)
        if total > MAX_TASK_DIRECTORIES:
            raise WorkerTaskError(
                "worker_store_oversized",
                "Worker task store exceeds its fixed limit",
            )
        return total

    def _validate_runtime_registry(self) -> None:
        for runtime_id, workspace_ids in self.runtime_registry.workspace_ids().items():
            if re.fullmatch(RUNTIME_ID_PATTERN, runtime_id) is None:
                raise OSError("Worker Runtime ID is invalid")
            for workspace_id in workspace_ids:
                if re.fullmatch(WORKSPACE_ID_PATTERN, workspace_id) is None:
                    raise OSError("Worker workspace ID is invalid")

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
        try:
            leased_spec, _leased_state, leased_completion = self._records(lease.task_id)
        except (FileNotFoundError, OSError, ValidationError, ValueError) as exc:
            raise WorkerTaskError(
                "worker_session_lease_corrupt",
                "Session lease task is invalid and cannot be replaced safely",
            ) from exc
        if leased_spec.runtime_id != lease.runtime_id:
            raise WorkerTaskError(
                "worker_session_lease_corrupt",
                "Session lease Runtime does not match its task",
            )
        if leased_completion is not None:
            if (
                leased_spec.session_id != lease.session_id
                or leased_spec.spec_sha256 != lease.spec_sha256
            ):
                raise WorkerTaskError(
                    "worker_session_lease_corrupt",
                    "Session lease does not match its completed task",
                )
            self._release_lease(leased_spec)
            return
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
            or lease.runtime_id != spec.runtime_id
            or lease.spec_sha256 != spec.spec_sha256
        ):
            raise ValueError("Session lease does not match its task")
        path.unlink()
        directory_descriptor = os.open(self.leases_dir, os.O_RDONLY)
        try:
            os.fsync(directory_descriptor)
        finally:
            os.close(directory_descriptor)

    def _native_session_available(
        self,
        runtime_id: str,
        native_session_id: str,
    ) -> bool:
        try:
            return self.runtime_registry.require(runtime_id).native_session_available(
                native_session_id
            )
        except RuntimeOperationError as exc:
            raise _RuntimeBoundaryError(exc) from exc

    @staticmethod
    def _expected_native_session_id(
        spec: StoredTaskSpec,
        state: StoredTaskState,
    ) -> str | None:
        if spec.task_kind == "translation":
            return state.expected_native_session_id
        return state.expected_native_session_id or spec.native_session_id

    @staticmethod
    def _extract_native_session_id(
        runner,
        path: Path,
        *,
        missing_ok: bool = False,
    ) -> str | None:
        try:
            return runner.parse_event_stream(
                path,
                max_event_bytes=MAX_EVENT_BYTES,
                missing_ok=missing_ok,
            ).native_session_id
        except RuntimeOperationError as exc:
            raise _RuntimeBoundaryError(exc) from exc

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

    def _redact_error(self, error: str | None) -> str | None:
        if error is None:
            return None
        return redact_log_line(
            error,
            self._sensitive_values,
            max_line_bytes=MAX_ERROR_BYTES,
        )

    def _runner_failure_details(
        self,
        error: BaseException,
    ) -> tuple[str, str, TaskErrorSource]:
        if isinstance(error, _RuntimeBoundaryError):
            return (
                error.code,
                f"Chub Runtime error ({error.code}): {error.message}",
                "chub",
            )
        if isinstance(error, RuntimeOperationError):
            return (
                error.code,
                f"Chub Runtime error ({error.code}): {error.message}",
                "chub",
            )
        if isinstance(error, OSError) and error.strerror:
            detail = error.strerror
        else:
            detail = str(error).strip()
        detail = self._redact_error(detail) or type(error).__name__
        return (
            "runner_supervision_failed",
            f"Chub Worker error ({type(error).__name__}): {detail}",
            "chub",
        )

    @staticmethod
    def _read_runner_error(runner, task_dir: Path) -> str | None:
        if runner is None:
            return None
        try:
            return runner.read_error(task_dir, max_bytes=MAX_ERROR_BYTES)
        except RuntimeOperationError as exc:
            raise _RuntimeBoundaryError(exc) from exc

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
