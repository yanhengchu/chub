from __future__ import annotations

import json
import logging
import os
import subprocess
import threading
from datetime import datetime
from pathlib import Path
from typing import Callable, Literal, Protocol
from uuid import uuid4

import psutil
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.codex.models import utc_now
from app.core.config import Settings
from app.core.response import ApiError
from app.quick_worker import PROTOCOL_VERSION, read_health
from app.services.operation_log import write_operation


LOGGER = logging.getLogger("hub.quick_worker_maintenance")
MAX_RELOAD_STATE_BYTES = 16 * 1024
RELOAD_HANDOFF_GRACE_SECONDS = 15.0


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class QuickWorkerReloadState(_StrictModel):
    version: Literal[1] = 1
    operation_id: str = Field(min_length=1, max_length=128)
    status: Literal["requested", "started", "succeeded", "failed"]
    old_generation: str = Field(min_length=1, max_length=128)
    process_id: int | None = Field(default=None, ge=1)
    source_ip: str = Field(min_length=1, max_length=128)
    message: str = Field(default="", max_length=300)
    requested_at: datetime
    updated_at: datetime


class QuickWorkerOperationView(_StrictModel):
    operation_id: str = Field(min_length=1, max_length=128)
    status: Literal["restarting", "succeeded", "failed"]
    message: str = Field(default="", max_length=300)
    updated_at: datetime


class QuickWorkerStatusData(_StrictModel):
    state: Literal[
        "ready",
        "busy",
        "draining",
        "recovering",
        "restarting",
        "incompatible",
        "unavailable",
    ]
    message: str = Field(min_length=1, max_length=300)
    active_tasks: int = Field(default=0, ge=0)
    queued_tasks: int = Field(default=0, ge=0)
    can_restart: bool = False
    upgrade_required: bool = False
    operation: QuickWorkerOperationView | None = None


class QuickWorkerInspection:
    def __init__(self, data: QuickWorkerStatusData, generation: str | None) -> None:
        self.data = data
        self.generation = generation


class WorkerReloadProcess(Protocol):
    pid: int

    def wait(self) -> int: ...


def launch_quick_worker_reload_process(command: Path) -> WorkerReloadProcess:
    environment = os.environ.copy()
    environment["CHUB_WORKER_RELOAD_EXTERNAL_LOGGING"] = "1"
    return subprocess.Popen(
        [str(command), "worker-reload"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
        env=environment,
    )


class QuickWorkerReloadCoordinator:
    """Serialize Web-requested Worker reloads and preserve their visible state."""

    def __init__(
        self,
        state_file: Path,
        command: Path,
        *,
        handoff_grace_seconds: float = RELOAD_HANDOFF_GRACE_SECONDS,
    ) -> None:
        self.path = state_file
        self.command = command
        self.handoff_grace_seconds = max(0.0, handoff_grace_seconds)
        self._lock = threading.RLock()
        self._state_error = False
        self._state = self._load()
        self._process: WorkerReloadProcess | None = None
        self._completion_handler: Callable[[], object] | None = None

    def set_completion_handler(self, handler: Callable[[], object]) -> None:
        self._completion_handler = handler

    def in_progress(self) -> bool:
        with self._lock:
            return self._state is not None and self._state.status in {
                "requested",
                "started",
            }

    def maintenance_available(self) -> bool:
        with self._lock:
            return not self._state_error

    def operation(self) -> QuickWorkerOperationView | None:
        with self._lock:
            if self._state is None:
                return None
            status = (
                "restarting"
                if self._state.status in {"requested", "started"}
                else self._state.status
            )
            return QuickWorkerOperationView(
                operation_id=self._state.operation_id,
                status=status,
                message=self._state.message,
                updated_at=self._state.updated_at,
            )

    def begin(self, old_generation: str, source_ip: str) -> bool:
        with self._lock:
            if self._state is not None and self._state.status in {
                "requested",
                "started",
            }:
                return False
            if self._state_error:
                raise ApiError(
                    503,
                    "quick_worker_reload_state_unavailable",
                    "Quick Worker 重启状态不可用，本次未执行重启。",
                )
            now = utc_now()
            state = QuickWorkerReloadState(
                operation_id=f"worker-reload:{uuid4().hex}",
                status="requested",
                old_generation=old_generation,
                source_ip=source_ip,
                requested_at=now,
                updated_at=now,
            )
            try:
                self._write(state)
            except OSError as error:
                self._state_error = True
                LOGGER.warning(
                    "Unable to persist Quick Worker reload request",
                    exc_info=True,
                )
                raise ApiError(
                    503,
                    "quick_worker_reload_state_unavailable",
                    "Quick Worker 重启状态不可用，本次未执行重启。",
                ) from error
            self._state = state
        self._record(state, "requested")

        if not self.command.is_file():
            self._finish(
                state.operation_id,
                "failed",
                "找不到 Quick Worker 重启命令，本次未执行重启。",
            )
            raise ApiError(
                503,
                "quick_worker_reload_command_not_found",
                "找不到 Quick Worker 重启命令",
            )
        try:
            process = launch_quick_worker_reload_process(self.command)
        except OSError as error:
            LOGGER.warning("Unable to launch Quick Worker reload", exc_info=True)
            self._finish(
                state.operation_id,
                "failed",
                "系统未能启动 Quick Worker 重启命令。",
            )
            raise ApiError(
                500,
                "quick_worker_reload_failed",
                "系统未能启动 Quick Worker 重启命令",
            ) from error

        with self._lock:
            current = self._matching_state(state.operation_id)
            if current is None:
                return False
            current.status = "started"
            current.process_id = process.pid
            current.updated_at = utc_now()
            try:
                self._write(current)
            except OSError:
                self._state_error = True
                LOGGER.warning(
                    "Unable to persist started Quick Worker reload",
                    exc_info=True,
                )
            self._state = current
            self._process = process
        self._record(current, "started")
        try:
            threading.Thread(
                target=self._monitor,
                args=(process, state.operation_id),
                daemon=True,
                name="chub-quick-worker-reload",
            ).start()
        except RuntimeError:
            LOGGER.warning("Unable to start Quick Worker reload monitor", exc_info=True)
            with self._lock:
                if self._process is process:
                    self._process = None
        return True

    def reconcile(self, generation: str | None, ready: bool) -> None:
        with self._lock:
            state = self._state.model_copy(deep=True) if self._state else None
            has_local_process = self._process is not None
        if state is None or state.status not in {"requested", "started"}:
            return
        if generation and generation != state.old_generation and ready:
            self._finish(state.operation_id, "succeeded", "Quick Worker 已重启并恢复。")
            return
        if has_local_process:
            return
        if (
            state.status == "requested"
            and state.process_id is None
            and (utc_now() - state.updated_at).total_seconds()
            < self.handoff_grace_seconds
        ):
            return
        if state.process_id and self._reload_process_is_running(state.process_id):
            return
        self._finish(
            state.operation_id,
            "failed",
            "Quick Worker 重启结果无法确认，请检查当前服务状态。",
        )

    def _monitor(self, process: WorkerReloadProcess, operation_id: str) -> None:
        try:
            return_code = process.wait()
        except Exception:
            LOGGER.warning("Unable to observe Quick Worker reload", exc_info=True)
            self._finish(
                operation_id,
                "failed",
                "Quick Worker 重启结果无法确认，请检查当前服务状态。",
            )
            return
        if return_code == 0:
            self._finish(operation_id, "succeeded", "Quick Worker 已重启并恢复。")
        else:
            LOGGER.warning(
                "Quick Worker reload exited unsuccessfully: return_code=%s",
                return_code,
            )
            self._finish(
                operation_id,
                "failed",
                "Quick Worker 重启失败，请查看日志详情。",
            )

    def _finish(
        self,
        operation_id: str,
        status: Literal["succeeded", "failed"],
        message: str,
    ) -> None:
        with self._lock:
            state = self._matching_state(operation_id)
            if state is None or state.status in {"succeeded", "failed"}:
                return
            state.status = status
            state.message = message
            state.updated_at = utc_now()
            try:
                self._write(state)
            except OSError:
                self._state_error = True
                LOGGER.warning(
                    "Unable to persist Quick Worker reload completion",
                    exc_info=True,
                )
            else:
                self._state_error = False
            self._state = state
            self._process = None
        self._record(state, status)
        if self._completion_handler is not None:
            try:
                self._completion_handler()
            except Exception:
                LOGGER.warning(
                    "Unable to resume deferred restart after Worker reload",
                    exc_info=True,
                )

    def _matching_state(self, operation_id: str) -> QuickWorkerReloadState | None:
        if self._state is None or self._state.operation_id != operation_id:
            return None
        return self._state.model_copy(deep=True)

    @staticmethod
    def _record(
        state: QuickWorkerReloadState,
        status: Literal["requested", "started", "succeeded", "failed"],
    ) -> None:
        try:
            write_operation(
                operation_id=state.operation_id,
                action="quick_worker_reload",
                status=status,
                target="quick-worker",
                source_ip=state.source_ip,
            )
        except Exception:
            LOGGER.warning("Unable to record Quick Worker reload operation", exc_info=True)

    def _load(self) -> QuickWorkerReloadState | None:
        try:
            with self.path.open("rb") as state_file:
                content = state_file.read(MAX_RELOAD_STATE_BYTES + 1)
            if len(content) > MAX_RELOAD_STATE_BYTES:
                raise ValueError("Quick Worker reload state exceeds its fixed limit")
            return QuickWorkerReloadState.model_validate_json(content)
        except FileNotFoundError:
            return None
        except (OSError, ValueError, ValidationError):
            self._state_error = True
            LOGGER.warning("Quick Worker reload state is unavailable", exc_info=True)
            return None

    def _write(self, state: QuickWorkerReloadState) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_name(f".{self.path.name}.{uuid4().hex}.tmp")
        descriptor = os.open(
            temporary,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL,
            0o600,
        )
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as state_file:
                json.dump(
                    state.model_dump(mode="json"),
                    state_file,
                    ensure_ascii=False,
                    separators=(",", ":"),
                )
                state_file.flush()
                os.fsync(state_file.fileno())
            os.chmod(temporary, 0o600)
            os.replace(temporary, self.path)
            os.chmod(self.path, 0o600)
        finally:
            temporary.unlink(missing_ok=True)

    def _reload_process_is_running(self, process_id: int) -> bool:
        try:
            process = psutil.Process(process_id)
            command = process.cmdline()
        except (psutil.Error, OSError):
            return False
        return str(self.command) in command and "worker-reload" in command


async def inspect_quick_worker(
    settings: Settings,
    recovery_ready: bool,
    reload_coordinator: QuickWorkerReloadCoordinator,
) -> QuickWorkerInspection:
    operation = reload_coordinator.operation()
    try:
        payload = await read_health(settings)
    except OSError:
        reload_coordinator.reconcile(None, False)
        operation = reload_coordinator.operation()
        if operation and operation.status == "restarting":
            return QuickWorkerInspection(
                QuickWorkerStatusData(
                    state="restarting",
                    message="正在重启并等待健康恢复。",
                    operation=operation,
                ),
                None,
            )
        return QuickWorkerInspection(
            QuickWorkerStatusData(
                state="unavailable",
                message="无法连接 Quick Worker。",
                operation=operation,
            ),
            None,
        )

    data = payload.get("data") if payload.get("success") is True else None
    if not isinstance(data, dict):
        reload_coordinator.reconcile(None, False)
        return QuickWorkerInspection(
            QuickWorkerStatusData(
                state="unavailable",
                message="Quick Worker 健康状态不可用。",
                operation=reload_coordinator.operation(),
            ),
            None,
        )

    generation = data.get("generation") if isinstance(data.get("generation"), str) else None
    active_tasks = max(0, int(data.get("active_tasks", 0)))
    queued_tasks = max(0, int(data.get("queued_tasks", 0)))
    worker_healthy = (
        data.get("status") == "ready"
        and data.get("uncertain_tasks") == 0
        and data.get("corrupt_tasks") == 0
        and "codex" in data.get("available_runtime_ids", [])
    )
    worker_ready = (
        data.get("protocol_version") == PROTOCOL_VERSION and worker_healthy
    )
    reload_coordinator.reconcile(generation, worker_ready and recovery_ready)
    operation = reload_coordinator.operation()

    if operation and operation.status == "restarting":
        state = "restarting"
        message = "正在重启并等待健康恢复。"
    elif data.get("protocol_version") != PROTOCOL_VERSION:
        state = "incompatible"
        message = "Worker 协议与当前 Chub 不兼容；空闲后可重启到当前版本。"
    elif data.get("uncertain_tasks") != 0:
        state = "unavailable"
        message = "Worker 存在未确认任务，暂不可维护。"
    elif data.get("corrupt_tasks") != 0 or "codex" not in data.get(
        "available_runtime_ids", []
    ):
        state = "unavailable"
        message = "Worker 健康检查未通过。"
    elif data.get("status") == "draining":
        state = "draining"
        message = "正在排空已受理任务。"
    elif active_tasks or queued_tasks:
        state = "busy"
        parts = []
        if active_tasks:
            parts.append(f"{active_tasks} 个执行中")
        if queued_tasks:
            parts.append(f"{queued_tasks} 个排队中")
        message = " · ".join(parts)
    elif not recovery_ready:
        state = "recovering"
        message = "Worker 已连接，Chub 正在恢复任务状态。"
    else:
        state = "ready"
        message = (
            "可以接收快速任务。"
            if reload_coordinator.maintenance_available()
            else "可以接收快速任务；重启状态暂不可用。"
        )

    return QuickWorkerInspection(
        QuickWorkerStatusData(
            state=state,
            message=message,
            active_tasks=active_tasks,
            queued_tasks=queued_tasks,
            can_restart=(
                state in {"ready", "incompatible"}
                and active_tasks == 0
                and queued_tasks == 0
                and worker_healthy
                and reload_coordinator.maintenance_available()
                and (
                    data.get("protocol_version") != PROTOCOL_VERSION
                    or recovery_ready
                )
            ),
            operation=operation,
        ),
        generation,
    )
