"""Durable state for the independent Chub workstation rebuild executor."""

from __future__ import annotations

import json
import os
import stat
import tempfile
import threading
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path
from typing import Literal
from uuid import uuid4

import fcntl

from pydantic import BaseModel, ConfigDict, Field

from app.ai_session.models import utc_now
from app.core.config import PROJECT_ROOT
from app.core.response import ApiError
from app.services.operation_log import write_operation


MAX_STATE_BYTES = 32 * 1024
EXECUTOR_HANDOFF_TIMEOUT = timedelta(minutes=15)
RebuildStatus = Literal["requested", "started", "succeeded", "failed"]
RebuildStage = Literal[
    "requested", "preparing_environment", "stopping_services", "cleaning_state",
    "starting_services", "initializing_runtime", "verifying", "completed", "failed",
]


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class WorkstationRebuildOperation(_StrictModel):
    version: Literal[1] = 1
    operation_id: str = Field(pattern=r"^[a-f0-9]{32}$")
    status: RebuildStatus
    stage: RebuildStage
    source_ip: str = Field(min_length=1, max_length=128)
    message: str = Field(min_length=1, max_length=500)
    requested_at: datetime
    updated_at: datetime


class WorkstationRebuildStatusData(_StrictModel):
    state: Literal["idle", "preparing", "rebuilding", "succeeded", "failed", "unknown"]
    message: str = Field(min_length=1, max_length=500)
    can_start: bool
    operation: WorkstationRebuildOperation | None = None


class WorkstationRebuildCoordinator:
    """Small file-backed authority that survives clearing AI runtime state."""

    def __init__(self, state_path: Path | None = None) -> None:
        self.state_path = state_path or PROJECT_ROOT / "data/local/workstation-rebuild.json"
        self._lock = threading.RLock()

    def operation(self) -> WorkstationRebuildOperation | None:
        with self._lock:
            try:
                metadata = self.state_path.lstat()
            except FileNotFoundError:
                return None
            except OSError as exc:
                raise OSError("工作站重建记录无法读取。") from exc
            if (
                not stat.S_ISREG(metadata.st_mode)
                or stat.S_ISLNK(metadata.st_mode)
                or metadata.st_uid != os.getuid()
                or stat.S_IMODE(metadata.st_mode) & 0o077
                or metadata.st_size > MAX_STATE_BYTES
            ):
                raise OSError("工作站重建记录的类型、所有者、权限或大小不安全。")
            try:
                return WorkstationRebuildOperation.model_validate_json(self.state_path.read_bytes())
            except Exception as exc:
                raise OSError("工作站重建记录格式无效。") from exc

    def status_data(self) -> WorkstationRebuildStatusData:
        try:
            operation = self._reconcile_unstarted_operation()
        except OSError as exc:
            return WorkstationRebuildStatusData(state="unknown", message=str(exc), can_start=False)
        if operation is None:
            return WorkstationRebuildStatusData(state="idle", message="可按当前代码、配置与 requirements 重建 Chub 工作站。", can_start=True)
        if operation.status in {"requested", "started"}:
            state = "preparing" if operation.stage == "preparing_environment" else "rebuilding"
            return WorkstationRebuildStatusData(state=state, message=operation.message, can_start=False, operation=operation)
        if operation.status == "succeeded":
            return WorkstationRebuildStatusData(state="succeeded", message=operation.message, can_start=True, operation=operation)
        return WorkstationRebuildStatusData(state="failed", message=operation.message, can_start=True, operation=operation)

    def begin(self, source_ip: str) -> tuple[WorkstationRebuildOperation, bool]:
        self._reconcile_unstarted_operation()
        with self._lock, self._process_lock():
            current = self.operation()
            if current is not None and current.status in {"requested", "started"}:
                return current, False
            now = utc_now()
            operation = WorkstationRebuildOperation(
                operation_id=uuid4().hex,
                status="requested",
                stage="requested",
                source_ip=source_ip[:128] or "unknown",
                message="工作站重建已受理，正在启动独立维护进程。",
                requested_at=now,
                updated_at=now,
            )
            self._write(operation)
            self._log_operation(operation, status="requested")
            return operation, True

    def _reconcile_unstarted_operation(self) -> WorkstationRebuildOperation | None:
        """Fail a request that the independent executor never accepted."""
        with self._lock, self._process_lock():
            current = self.operation()
            if (
                current is None
                or current.status != "requested"
                or current.stage != "requested"
                or utc_now() - current.updated_at < EXECUTOR_HANDOFF_TIMEOUT
            ):
                return current
            failed = current.model_copy(
                update={
                    "status": "failed",
                    "stage": "failed",
                    "message": "工作站重建执行器未在 15 分钟内接手操作；可重新发起重建。",
                    "updated_at": utc_now(),
                }
            )
            self._write(failed)
            self._log_operation(failed, status="failed", reason=failed.message)
            return failed

    def mark_executor_started(self, operation_id: str) -> WorkstationRebuildOperation:
        """Record that the fixed independent executor has accepted the request."""
        with self._lock, self._process_lock():
            current = self.operation()
            if current is None or current.operation_id != operation_id:
                raise OSError("工作站重建操作不存在或已被替换。")
            if current.status == "started":
                return current
            if current.status != "requested":
                raise OSError("工作站重建操作当前不可启动。")
            started = current.model_copy(
                update={
                    "status": "started",
                    "stage": "preparing_environment",
                    "message": "正在准备项目 Python 运行环境。",
                    "updated_at": utc_now(),
                }
            )
            self._write(started)
            self._log_operation(started, status="started")
            return started

    def update(self, operation_id: str, *, status: RebuildStatus | None = None, stage: RebuildStage | None = None, message: str | None = None) -> WorkstationRebuildOperation:
        with self._lock, self._process_lock():
            current = self.operation()
            if current is None or current.operation_id != operation_id:
                raise OSError("工作站重建操作不存在或已被替换。")
            next_state = current.model_copy(update={
                "status": status or current.status,
                "stage": stage or current.stage,
                "message": (message or current.message)[:500],
                "updated_at": utc_now(),
            })
            self._write(next_state)
            return next_state

    def fail(self, operation_id: str, message: str) -> None:
        operation = self.update(
            operation_id,
            status="failed",
            stage="failed",
            message=message or "Chub 工作站重建未完成。",
        )
        self._log_operation(operation, status="failed", reason=operation.message)

    def succeed(self, operation_id: str) -> None:
        operation = self.update(
            operation_id,
            status="succeeded",
            stage="completed",
            message="工作站已重建，Web、Quick Worker 与默认 Runtime 均可用。",
        )
        self._log_operation(operation, status="succeeded")

    @staticmethod
    def _log_operation(
        operation: WorkstationRebuildOperation,
        *,
        status: str,
        reason: str | None = None,
    ) -> None:
        try:
            write_operation(
                operation_id=operation.operation_id,
                action="workstation_rebuild",
                status=status,
                target="chub",
                source_ip=operation.source_ip,
                reason=reason,
            )
        except Exception:
            # Audit logging must not change the rebuild operation outcome.
            pass

    def _write(self, operation: WorkstationRebuildOperation) -> None:
        # `model_copy()` deliberately skips validation. Revalidate here because
        # this file is the durable cross-process contract for the executor.
        operation = WorkstationRebuildOperation.model_validate(operation.model_dump())
        self.state_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        if self.state_path.parent.stat().st_mode & 0o077:
            os.chmod(self.state_path.parent, 0o700)
        descriptor, temporary = tempfile.mkstemp(prefix=".workstation-rebuild-", dir=self.state_path.parent)
        try:
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(operation.model_dump_json().encode("utf-8"))
                handle.flush()
                os.fsync(handle.fileno())
            os.chmod(temporary, 0o600)
            os.replace(temporary, self.state_path)
        finally:
            try:
                os.unlink(temporary)
            except FileNotFoundError:
                pass

    @contextmanager
    def _process_lock(self):
        self.state_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        lock_path = self.state_path.with_suffix(".lock")
        descriptor = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
        try:
            os.chmod(lock_path, 0o600)
            with os.fdopen(descriptor, "r+") as handle:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
                try:
                    yield
                finally:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        finally:
            # fdopen owns and closes the descriptor. This branch only runs if
            # opening the stream failed before ownership transferred.
            try:
                os.close(descriptor)
            except OSError:
                pass


def rebuild_status_or_error() -> WorkstationRebuildStatusData:
    return WorkstationRebuildCoordinator().status_data()
