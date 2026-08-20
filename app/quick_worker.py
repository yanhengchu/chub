from __future__ import annotations

import argparse
import asyncio
import fcntl
import hashlib
import json
import os
import signal
import shutil
import socket
import stat
import struct
import sys
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.core.config import PROJECT_ROOT, Settings, get_settings
from app.core.logger import configure_worker_operation_logging
from app.ai_runtime import WorkerRuntimeRegistry, validate_runtime_wiring
from app.codex.runtime_adapter import CodexRuntimeAdapter
from app.codex.worker_runtime import CodexWorkerRuntime
from app.quick_worker_tasks import (
    RuntimeTaskSubmission,
    TestTaskSubmission,
    WorkerTaskError,
    WorkerTaskManager,
)
from app.quick_worker_test_runtime import FixedTestWorkerRuntime
from app.services.operation_log import write_operation


HEALTH_PROTOCOL_VERSION = 1
PROTOCOL_VERSION = 9
WORKER_CODE_VERSION = "quick-worker-9-error-source"
MAX_REQUEST_BYTES = 64 * 1024
MAX_RESPONSE_BYTES = 256 * 1024
CLIENT_TIMEOUT_SECONDS = 2.0
MAX_CONCURRENT_CONNECTIONS = 16


class WorkerRequestNotSent(OSError):
    """The IPC request failed before any request bytes could be sent."""


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class WorkerRequest(_StrictModel):
    protocol_version: int
    request_id: str = Field(
        min_length=1,
        max_length=128,
        pattern=r"^[A-Za-z0-9_-]+$",
    )
    action: Literal["health"]


class WorkerTaskSubmitRequest(_StrictModel):
    protocol_version: int
    request_id: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_-]+$")
    action: Literal["test_task_submit"]
    task: TestTaskSubmission


class WorkerRuntimeTaskSubmitRequest(_StrictModel):
    protocol_version: int
    request_id: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_-]+$")
    action: Literal["runtime_task_submit"]
    task: RuntimeTaskSubmission


class WorkerDrainRequest(_StrictModel):
    protocol_version: int
    request_id: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_-]+$")
    action: Literal["drain"]
    operation_id: str = Field(
        min_length=1,
        max_length=128,
        pattern=r"^[A-Za-z0-9:_-]+$",
    )


class WorkerResumeRequest(_StrictModel):
    protocol_version: int
    request_id: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_-]+$")
    action: Literal["resume"]
    operation_id: str = Field(
        min_length=1,
        max_length=128,
        pattern=r"^[A-Za-z0-9:_-]+$",
    )


class WorkerTaskGetRequest(_StrictModel):
    protocol_version: int
    request_id: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_-]+$")
    action: Literal["task_get"]
    task_id: str


class WorkerTaskListRequest(_StrictModel):
    protocol_version: int
    request_id: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_-]+$")
    action: Literal["task_list"]
    limit: int = Field(default=50, ge=1, le=100)
    active_only: bool = False
    recovery_only: bool = False


class WorkerTaskAcknowledgeRequest(_StrictModel):
    protocol_version: int
    request_id: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_-]+$")
    action: Literal["task_acknowledge"]
    task_id: str


class WorkerTaskCancelRequest(_StrictModel):
    protocol_version: int
    request_id: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_-]+$")
    action: Literal["task_cancel"]
    task_id: str


class WorkerHealth(_StrictModel):
    protocol_version: int = Field(default=PROTOCOL_VERSION, ge=1)
    status: Literal["ready", "draining"]
    generation: str
    code_version: str
    pid: int
    active_tasks: int = 0
    queued_tasks: int = 0
    uncertain_tasks: int = 0
    corrupt_tasks: int = 0
    test_tasks_enabled: bool = False
    runtime_ids: list[str] = Field(default_factory=list, max_length=16)
    available_runtime_ids: list[str] = Field(default_factory=list, max_length=16)
    runtime_workspace_ids: dict[str, list[str]] = Field(default_factory=dict)
    drain_operation_id: str | None = Field(default=None, max_length=128)
    drain_complete: bool = False
    drain_error: str | None = Field(default=None, max_length=300)


def worker_runtime_dir(settings: Settings) -> Path:
    identity = hashlib.sha256(
        str(settings.codex_pty.runtime_dir).encode("utf-8")
    ).hexdigest()[:12]
    return Path("/tmp") / f"chub-qw-{os.getuid()}-{identity}"


def worker_socket_path(settings: Settings) -> Path:
    return worker_runtime_dir(settings) / "worker.sock"


def production_codex_workspaces(settings: Settings) -> dict[str, Path]:
    return {
        "home": Path.home(),
        "workspace": settings.codex_pty.workspace,
        "chub": PROJECT_ROOT,
        "weixin-translation": (
            settings.codex_pty.runtime_dir / "translation-workspace"
        ),
    }


def _private_directory(path: Path) -> None:
    if path.is_symlink():
        raise OSError(f"Quick Worker directory must not be a symlink: {path}")
    path.mkdir(parents=True, exist_ok=True)
    if path.is_symlink() or not path.is_dir():
        raise OSError(f"Quick Worker directory is unavailable: {path}")
    if path.stat().st_uid != os.getuid():
        raise OSError(f"Quick Worker directory has an unexpected owner: {path}")
    os.chmod(path, 0o700)


def _peer_uid(connection: socket.socket) -> int:
    raw_connection = getattr(connection, "_sock", connection)
    getpeereid = getattr(raw_connection, "getpeereid", None)
    if getpeereid is not None:
        uid, _gid = getpeereid()
        return int(uid)
    if sys.platform == "darwin" and hasattr(socket, "LOCAL_PEERCRED"):
        credentials = raw_connection.getsockopt(0, socket.LOCAL_PEERCRED, 8)
        _version, uid = struct.unpack("II", credentials)
        return uid
    if hasattr(socket, "SO_PEERCRED"):
        credentials = raw_connection.getsockopt(
            socket.SOL_SOCKET,
            socket.SO_PEERCRED,
            struct.calcsize("3i"),
        )
        _pid, uid, _gid = struct.unpack("3i", credentials)
        return uid
    raise OSError("Peer credential verification is unavailable")


def _response(
    *,
    request_id: str | None,
    data: dict[str, object] | None = None,
    code: str | None = None,
    message: str | None = None,
) -> bytes:
    if code is None:
        payload: dict[str, object] = {
            "success": True,
            "request_id": request_id,
            "data": data or {},
        }
    else:
        payload = {
            "success": False,
            "request_id": request_id,
            "error": {"code": code, "message": message or "Worker request failed"},
        }
    encoded = (
        json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n"
    ).encode("utf-8")
    if len(encoded) > MAX_RESPONSE_BYTES:
        return (
            json.dumps(
                {
                    "success": False,
                    "request_id": request_id,
                    "error": {
                        "code": "worker_response_too_large",
                        "message": "Worker response exceeds its fixed limit",
                    },
                },
                separators=(",", ":"),
            )
            + "\n"
        ).encode("utf-8")
    return encoded


@dataclass
class _WorkerLock:
    descriptor: int

    def close(self) -> None:
        try:
            os.close(self.descriptor)
        except OSError:
            pass


class QuickWorkerServer:
    def __init__(
        self,
        settings: Settings,
        *,
        allow_test_tasks: bool = False,
        request_timeout_seconds: float = CLIENT_TIMEOUT_SECONDS,
        codex_workspaces: dict[str, Path] | None = None,
        codex_executable: str | Path | None = None,
        codex_home: Path | None = None,
        runtime_registry: WorkerRuntimeRegistry | None = None,
    ) -> None:
        self.runtime_dir = worker_runtime_dir(settings)
        self.socket_path = worker_socket_path(settings)
        self.generation = uuid.uuid4().hex
        self.status: Literal["ready", "draining"] = "ready"
        if runtime_registry is None:
            runners = []
            if codex_workspaces:
                resolved_executable = (
                    str(codex_executable)
                    if codex_executable is not None
                    else shutil.which("codex")
                )
                resolved_codex_home = (
                    codex_home
                    if codex_home is not None
                    else Path(os.environ.get("CODEX_HOME", Path.home() / ".codex"))
                )
                adapter = CodexRuntimeAdapter(
                    settings,
                    codex_home=resolved_codex_home,
                    executable=resolved_executable,
                )
                runner = CodexWorkerRuntime(
                    adapter,
                    executable=resolved_executable,
                    workspaces=codex_workspaces,
                )
                validate_runtime_wiring(adapter, runner)
                runners.append(runner)
            if allow_test_tasks:
                runners.append(FixedTestWorkerRuntime())
            runtime_registry = WorkerRuntimeRegistry(runners)
        self.runtime_registry = runtime_registry
        self.task_manager = WorkerTaskManager(
            settings,
            self.generation,
            protocol_version=PROTOCOL_VERSION,
            runtime_registry=self.runtime_registry,
            allow_test_tasks=allow_test_tasks,
        )
        self.request_timeout_seconds = request_timeout_seconds
        self._server: asyncio.AbstractServer | None = None
        self._lock: _WorkerLock | None = None
        self._socket_identity: tuple[int, int] | None = None
        self._stopped = asyncio.Event()
        self._active_connections = 0
        self._submission_gate = asyncio.Lock()
        self._drain_operation_id: str | None = None
        self._drain_task: asyncio.Task[None] | None = None
        self._drain_complete = False
        self._drain_error: str | None = None

    @property
    def runtime_ids(self) -> tuple[str, ...]:
        return self.runtime_registry.runtime_ids()

    def _acquire_lock(self) -> None:
        _private_directory(self.runtime_dir)
        lock_path = self.runtime_dir / "worker.lock"
        flags = os.O_CREAT | os.O_RDWR
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        descriptor = os.open(lock_path, flags, 0o600)
        try:
            os.fchmod(descriptor, 0o600)
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except Exception:
            os.close(descriptor)
            raise OSError("Quick Worker is already running or its lock is unavailable") from None
        self._lock = _WorkerLock(descriptor)

    def _remove_stale_socket(self) -> None:
        try:
            metadata = self.socket_path.lstat()
        except FileNotFoundError:
            return
        if not stat.S_ISSOCK(metadata.st_mode):
            raise OSError(f"Quick Worker socket path is not a socket: {self.socket_path}")
        self.socket_path.unlink()

    async def start(self) -> None:
        self._acquire_lock()
        try:
            await self.task_manager.start()
            self._remove_stale_socket()
            self._server = await asyncio.start_unix_server(
                self._handle_connection,
                path=self.socket_path,
                limit=MAX_REQUEST_BYTES + 1,
            )
            os.chmod(self.socket_path, 0o600)
            metadata = self.socket_path.lstat()
            self._socket_identity = (metadata.st_dev, metadata.st_ino)
        except Exception:
            self._release_resources()
            raise

    async def serve(self) -> None:
        await self.start()
        loop = asyncio.get_running_loop()
        for signum in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(signum, self.request_stop)
            except (NotImplementedError, RuntimeError):
                pass
        await self._stopped.wait()
        await self.close()

    def request_stop(self) -> None:
        self.status = "draining"
        self._stopped.set()

    def _record_drain_operation(self, status: str) -> None:
        if self._drain_operation_id is None:
            return
        try:
            write_operation(
                operation_id=self._drain_operation_id,
                action="quick_worker_drain",
                status=status,
                target=f"quick-worker:{self.generation}",
                source_ip="127.0.0.1",
            )
        except Exception:
            pass

    async def begin_drain(self, operation_id: str) -> None:
        async with self._submission_gate:
            if self._drain_operation_id is not None:
                if self._drain_operation_id != operation_id:
                    raise WorkerTaskError(
                        "worker_drain_conflict",
                        "Worker is already draining for another operation",
                    )
                return
            self._drain_operation_id = operation_id
            self.status = "draining"
            for operation_status in ("requested", "started"):
                self._record_drain_operation(operation_status)
            self._drain_task = asyncio.create_task(
                self._finish_drain(),
                name=f"quick-worker-drain-{operation_id[:16]}",
            )

    async def resume_after_drain(self, operation_id: str) -> None:
        async with self._submission_gate:
            if self._drain_operation_id != operation_id:
                raise WorkerTaskError(
                    "worker_drain_identity_changed",
                    "Worker drain operation identity changed",
                )
            if not self._drain_complete or self._drain_error is not None:
                raise WorkerTaskError(
                    "worker_drain_not_complete",
                    "Worker drain has not completed cleanly",
                )
            if self.task_manager.active_count or self.task_manager.queued_count:
                raise WorkerTaskError(
                    "worker_not_idle", "Worker still has active or queued tasks"
                )
            self._record_drain_operation("failed")
            self._drain_operation_id = None
            self._drain_task = None
            self._drain_complete = False
            self._drain_error = None
            self.status = "ready"

    async def _finish_drain(self) -> None:
        await self.task_manager.wait_until_idle()
        if self.task_manager.corrupt_count:
            self._drain_error = "Worker task store contains invalid records"
            self._record_drain_operation("failed")
            return
        self._drain_complete = True
        self._record_drain_operation("succeeded")

    async def close(self, *, interrupt_tasks: bool = True) -> None:
        self.status = "draining"
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
            self._server = None
        if self._drain_task is not None and not self._drain_task.done():
            self._drain_error = "Worker stopped before drain completed"
            self._record_drain_operation("failed")
            self._drain_task.cancel()
            await asyncio.gather(self._drain_task, return_exceptions=True)
        await self.task_manager.close(interrupt_tasks=interrupt_tasks)
        self._release_resources()

    def _release_resources(self) -> None:
        try:
            metadata = self.socket_path.lstat()
        except FileNotFoundError:
            metadata = None
        if (
            metadata is not None
            and stat.S_ISSOCK(metadata.st_mode)
            and self._socket_identity == (metadata.st_dev, metadata.st_ino)
        ):
            try:
                self.socket_path.unlink()
            except FileNotFoundError:
                pass
        self._socket_identity = None
        if self._lock is not None:
            self._lock.close()
            self._lock = None

    async def _handle_connection(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        request_id: str | None = None
        if self._active_connections >= MAX_CONCURRENT_CONNECTIONS:
            writer.write(
                _response(
                    request_id=None,
                    code="worker_busy",
                    message="Worker has reached its fixed connection limit",
                )
            )
            await asyncio.wait_for(
                writer.drain(), timeout=self.request_timeout_seconds
            )
            writer.close()
            await writer.wait_closed()
            return
        self._active_connections += 1
        try:
            connection = writer.get_extra_info("socket")
            if connection is None or _peer_uid(connection) != os.getuid():
                writer.write(
                    _response(
                        request_id=None,
                        code="worker_peer_forbidden",
                        message="Worker IPC peer is not authorized",
                    )
                )
                await asyncio.wait_for(
                    writer.drain(), timeout=self.request_timeout_seconds
                )
                return
            try:
                raw = await asyncio.wait_for(
                    reader.readline(),
                    timeout=self.request_timeout_seconds,
                )
            except (asyncio.TimeoutError, ValueError):
                raw = b""
            if not raw or len(raw) > MAX_REQUEST_BYTES or not raw.endswith(b"\n"):
                writer.write(
                    _response(
                        request_id=None,
                        code="worker_request_invalid",
                        message="Worker request is empty, incomplete, or too large",
                    )
                )
                await asyncio.wait_for(
                    writer.drain(), timeout=self.request_timeout_seconds
                )
                return
            try:
                payload = json.loads(raw)
                if isinstance(payload, dict) and isinstance(payload.get("request_id"), str):
                    request_id = payload["request_id"][:128]
                request = self._parse_request(payload)
            except (json.JSONDecodeError, ValidationError, ValueError):
                writer.write(
                    _response(
                        request_id=request_id,
                        code="worker_request_invalid",
                        message="Worker request does not match the fixed protocol",
                    )
                )
                await asyncio.wait_for(
                    writer.drain(), timeout=self.request_timeout_seconds
                )
                return
            compatible_versions = (
                {HEALTH_PROTOCOL_VERSION, PROTOCOL_VERSION}
                if isinstance(request, WorkerRequest)
                else {PROTOCOL_VERSION}
            )
            if request.protocol_version not in compatible_versions:
                writer.write(
                    _response(
                        request_id=request.request_id,
                        code="worker_protocol_incompatible",
                        message="Worker protocol version is incompatible",
                    )
                )
                await asyncio.wait_for(
                    writer.drain(), timeout=self.request_timeout_seconds
                )
                return
            data = await self._dispatch(request)
            writer.write(
                _response(
                    request_id=request.request_id,
                    data=data,
                )
            )
            await asyncio.wait_for(
                writer.drain(), timeout=self.request_timeout_seconds
            )
        except WorkerTaskError as exc:
            writer.write(
                _response(
                    request_id=request_id,
                    code=exc.code,
                    message=exc.message,
                )
            )
            await asyncio.wait_for(
                writer.drain(), timeout=self.request_timeout_seconds
            )
        except Exception:
            try:
                writer.write(
                    _response(
                        request_id=request_id,
                        code="worker_internal_error",
                        message="Worker could not process the request",
                    )
                )
                await asyncio.wait_for(
                    writer.drain(), timeout=self.request_timeout_seconds
                )
            except Exception:
                pass
        finally:
            self._active_connections -= 1
            writer.close()
            try:
                await writer.wait_closed()
            except (BrokenPipeError, ConnectionError):
                pass

    @staticmethod
    def _parse_request(payload: object):
        if not isinstance(payload, dict):
            raise ValueError("Worker request must be an object")
        action = payload.get("action")
        model = {
            "health": WorkerRequest,
            "test_task_submit": WorkerTaskSubmitRequest,
            "runtime_task_submit": WorkerRuntimeTaskSubmitRequest,
            "drain": WorkerDrainRequest,
            "resume": WorkerResumeRequest,
            "task_get": WorkerTaskGetRequest,
            "task_list": WorkerTaskListRequest,
            "task_cancel": WorkerTaskCancelRequest,
            "task_acknowledge": WorkerTaskAcknowledgeRequest,
        }.get(action)
        if model is None:
            raise ValueError("Worker action is unsupported")
        return model.model_validate(payload)

    async def _dispatch(self, request) -> dict[str, object]:
        if isinstance(request, WorkerRequest):
            health = WorkerHealth(
                status=self.status,
                generation=self.generation,
                code_version=WORKER_CODE_VERSION,
                pid=os.getpid(),
                active_tasks=self.task_manager.running_count,
                queued_tasks=self.task_manager.queued_count,
                uncertain_tasks=0,
                corrupt_tasks=self.task_manager.corrupt_count,
                test_tasks_enabled=self.task_manager.allow_test_tasks,
                runtime_ids=list(self.runtime_registry.runtime_ids()),
                available_runtime_ids=list(
                    self.runtime_registry.available_runtime_ids()
                ),
                runtime_workspace_ids={
                    runtime_id: list(workspace_ids)
                    for runtime_id, workspace_ids in (
                        self.runtime_registry.workspace_ids().items()
                    )
                },
                drain_operation_id=self._drain_operation_id,
                drain_complete=self._drain_complete,
                drain_error=self._drain_error,
            )
            return health.model_dump(mode="json")
        if isinstance(request, WorkerDrainRequest):
            await self.begin_drain(request.operation_id)
            return {
                "operation_id": request.operation_id,
                "status": self.status,
                "complete": self._drain_complete,
                "error": self._drain_error,
            }
        if isinstance(request, WorkerResumeRequest):
            await self.resume_after_drain(request.operation_id)
            return {"operation_id": request.operation_id, "status": self.status}
        if isinstance(request, WorkerTaskSubmitRequest):
            async with self._submission_gate:
                if self.status != "ready":
                    raise WorkerTaskError(
                        "worker_draining", "Worker is not accepting new tasks"
                    )
                task = await self.task_manager.submit_test(request.task)
            return {"task": task.model_dump(mode="json")}
        if isinstance(request, WorkerRuntimeTaskSubmitRequest):
            async with self._submission_gate:
                if self.status != "ready":
                    raise WorkerTaskError(
                        "worker_draining", "Worker is not accepting new tasks"
                    )
                task = await self.task_manager.submit_runtime(request.task)
            return {"task": task.model_dump(mode="json")}
        if isinstance(request, WorkerTaskGetRequest):
            async with self.task_manager._lock:
                task = self.task_manager.get(request.task_id)
            return {"task": task.model_dump(mode="json")}
        if isinstance(request, WorkerTaskListRequest):
            async with self.task_manager._lock:
                tasks = self.task_manager.list(
                    limit=request.limit,
                    active_only=request.active_only,
                    recovery_only=request.recovery_only,
                )
            return {"tasks": [task.model_dump(mode="json") for task in tasks]}
        if isinstance(request, WorkerTaskAcknowledgeRequest):
            async with self.task_manager._lock:
                delivery = self.task_manager.acknowledge_delivery(request.task_id)
            return {"delivery": delivery.model_dump(mode="json")}
        if isinstance(request, WorkerTaskCancelRequest):
            task = await self.task_manager.cancel(request.task_id)
            return {"task": task.model_dump(mode="json")}
        raise WorkerTaskError("worker_request_invalid", "Worker action is unsupported")


async def worker_request(
    settings: Settings,
    request: dict[str, object],
    *,
    timeout_seconds: float = CLIENT_TIMEOUT_SECONDS,
) -> dict[str, object]:
    effective_timeout = (
        max(timeout_seconds, 5.0)
        if request.get("action") == "task_cancel"
        else timeout_seconds
    )
    try:
        reader, writer = await asyncio.wait_for(
            asyncio.open_unix_connection(
                worker_socket_path(settings),
                limit=MAX_RESPONSE_BYTES + 1,
            ),
            timeout=effective_timeout,
        )
    except (OSError, asyncio.TimeoutError) as exc:
        raise WorkerRequestNotSent("Quick Worker connection is unavailable") from exc
    try:
        request_id = request.get("request_id")
        encoded = (
            json.dumps(request, ensure_ascii=False, separators=(",", ":")) + "\n"
        ).encode("utf-8")
        if len(encoded) > MAX_REQUEST_BYTES:
            raise WorkerRequestNotSent(
                "Quick Worker request exceeds its fixed limit"
            )
        writer.write(encoded)
        await asyncio.wait_for(writer.drain(), timeout=effective_timeout)
        try:
            raw = await asyncio.wait_for(
                reader.readline(),
                timeout=effective_timeout,
            )
        except ValueError as exc:
            raise OSError("Quick Worker returned an oversized response") from exc
        if not raw or len(raw) > MAX_RESPONSE_BYTES:
            raise OSError("Quick Worker returned an invalid response")
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise OSError("Quick Worker returned invalid JSON") from exc
        if not isinstance(payload, dict) or payload.get("request_id") != request_id:
            raise OSError("Quick Worker response did not match the request")
        if payload.get("success") not in {True, False}:
            raise OSError("Quick Worker returned an invalid response status")
        return payload
    finally:
        writer.close()
        try:
            await writer.wait_closed()
        except OSError:
            pass


def worker_request_sync(
    settings: Settings,
    request: dict[str, object],
    *,
    timeout_seconds: float = CLIENT_TIMEOUT_SECONDS,
) -> dict[str, object]:
    """Call the private Worker IPC from the existing synchronous service layer."""
    return asyncio.run(
        worker_request(settings, request, timeout_seconds=timeout_seconds)
    )


async def read_health(settings: Settings) -> dict[str, object]:
    request_id = uuid.uuid4().hex
    payload = await worker_request(
        settings,
        {
            "protocol_version": HEALTH_PROTOCOL_VERSION,
            "request_id": request_id,
            "action": "health",
        },
    )
    if payload.get("success") is True:
        try:
            data = payload.get("data")
            health = WorkerHealth.model_validate(data)
        except ValidationError as exc:
            raise OSError("Quick Worker returned invalid health data") from exc
        payload["data"] = health.model_dump(mode="json")
    return payload


async def request_drain(
    settings: Settings,
    *,
    operation_id: str | None = None,
    wait_seconds: float = 7200.0,
    protocol_version: int | None = None,
) -> dict[str, object]:
    resolved_operation_id = operation_id or f"worker-drain:{uuid.uuid4().hex}"
    resolved_protocol_version = protocol_version or PROTOCOL_VERSION
    if resolved_protocol_version < 1:
        raise ValueError("Quick Worker protocol version must be positive")
    payload = await worker_request(
        settings,
        {
            "protocol_version": resolved_protocol_version,
            "request_id": uuid.uuid4().hex,
            "action": "drain",
            "operation_id": resolved_operation_id,
        },
    )
    if payload.get("success") is not True:
        return payload
    deadline = asyncio.get_running_loop().time() + wait_seconds
    while True:
        health = await read_health(settings)
        data = health.get("data") if health.get("success") is True else None
        if not isinstance(data, dict):
            raise OSError("Quick Worker returned invalid drain health")
        if data.get("drain_operation_id") != resolved_operation_id:
            raise OSError("Quick Worker drain operation identity changed")
        if data.get("drain_error"):
            return {
                "success": False,
                "request_id": payload.get("request_id"),
                "error": {
                    "code": "worker_drain_failed",
                    "message": str(data["drain_error"]),
                },
            }
        if data.get("drain_complete") is True:
            return {
                "success": True,
                "request_id": payload.get("request_id"),
                "data": {
                    "operation_id": resolved_operation_id,
                    "status": "draining",
                    "complete": True,
                    "generation": data.get("generation"),
                },
            }
        if asyncio.get_running_loop().time() >= deadline:
            raise TimeoutError("Quick Worker drain did not complete before its deadline")
        await asyncio.sleep(0.1)


async def resume_after_drain(
    settings: Settings,
    *,
    operation_id: str,
    protocol_version: int | None = None,
) -> dict[str, object]:
    resolved_protocol_version = protocol_version or PROTOCOL_VERSION
    if resolved_protocol_version < 1:
        raise ValueError("Quick Worker protocol version must be positive")
    payload = await worker_request(
        settings,
        {
            "protocol_version": resolved_protocol_version,
            "request_id": uuid.uuid4().hex,
            "action": "resume",
            "operation_id": operation_id,
        },
    )
    if payload.get("success") is not True:
        return payload
    health = await read_health(settings)
    data = health.get("data") if health.get("success") is True else None
    if not isinstance(data, dict) or data.get("status") != "ready":
        raise OSError("Quick Worker did not resume ready state")
    return payload


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m app.quick_worker")
    parser.add_argument(
        "command",
        choices=("serve", "health", "drain"),
    )
    parser.add_argument("--wait-seconds", type=float, default=7200.0)
    parser.add_argument("--operation-id")
    return parser


def main() -> int:
    args = _parser().parse_args()
    try:
        settings = get_settings()
    except RuntimeError:
        print("quick-worker: configuration is unavailable", file=sys.stderr)
        return 1
    try:
        configure_worker_operation_logging(settings.logs)
    except OSError:
        print("quick-worker: operation log is unavailable", file=sys.stderr)
        return 1
    if args.command == "serve":
        try:
            workspaces = production_codex_workspaces(settings)
            _private_directory(workspaces["weixin-translation"])
            asyncio.run(
                QuickWorkerServer(
                    settings,
                    codex_workspaces=workspaces,
                ).serve()
            )
        except (OSError, RuntimeError) as exc:
            print(f"quick-worker: {exc}", file=sys.stderr)
            return 1
        return 0
    try:
        payload = asyncio.run(
            request_drain(
                settings,
                operation_id=args.operation_id,
                wait_seconds=args.wait_seconds,
            )
            if args.command == "drain"
            else read_health(settings)
        )
    except (OSError, asyncio.TimeoutError, json.JSONDecodeError) as exc:
        print(f"quick-worker: health unavailable: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(payload, ensure_ascii=False))
    return 0 if payload.get("success") is True else 1


if __name__ == "__main__":
    raise SystemExit(main())
