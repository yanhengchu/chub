from __future__ import annotations

import json
import logging
import os
import threading
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.codex.models import utc_now
from app.core.response import ApiError
from app.services.operation_log import write_operation
from app.services.restart_command import (
    RestartProcess,
    describe_restart_launch_error,
    monitor_restart_process,
)


LOGGER = logging.getLogger("hub.deferred_restart")
DeferredRestartOutcome = Literal[
    "succeeded",
    "start_failed",
    "sensitive_task_failed",
    "cleared",
]
DeferredRestartReadiness = Literal["waiting", "ready", "sensitive_task_failed"]
DeferredRestartImmediateDecision = Literal["launch", "claimed", "in_progress"]


@dataclass(frozen=True)
class DeferredRestartRegistration:
    operation_id: str
    created: bool


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class DeferredRestartRequest(_StrictModel):
    operation_id: str = Field(min_length=1, max_length=128)
    requested_instance_id: str = Field(min_length=1, max_length=128)
    requested_task_id: str = Field(min_length=1, max_length=128)
    source_ip: str = Field(min_length=1, max_length=128)
    status: Literal["waiting", "started"] = "waiting"
    requested_at: datetime
    updated_at: datetime


class DeferredRestartState(_StrictModel):
    version: Literal[2] = 2
    current: DeferredRestartRequest
    next: DeferredRestartRequest | None = None


class _LegacyDeferredRestartState(DeferredRestartRequest):
    version: Literal[1] = 1


def parse_deferred_restart_state(payload: object) -> DeferredRestartState:
    if isinstance(payload, dict) and payload.get("version") == 1:
        legacy = _LegacyDeferredRestartState.model_validate(payload)
        return DeferredRestartState(
            current=DeferredRestartRequest.model_validate(
                legacy.model_dump(exclude={"version"})
            )
        )
    return DeferredRestartState.model_validate(payload)


class DeferredRestartCoordinator:
    """Persist task-scoped requests and trigger serialized Web restarts."""

    def __init__(
        self,
        state_file: Path,
        instance_id: str,
        restart_callback: Callable[[], RestartProcess | None],
        *,
        grace_seconds: float = 3,
    ) -> None:
        self.path = state_file
        self.instance_id = instance_id
        self.restart_callback = restart_callback
        self.grace_seconds = grace_seconds
        self._lock = threading.RLock()
        self._ready_check: Callable[
            [DeferredRestartRequest],
            DeferredRestartReadiness,
        ] = lambda _request: "waiting"
        self._started_handler: Callable[[str, str, datetime], None] | None = None
        self._completion_handler: (
            Callable[[str, str, DeferredRestartOutcome, datetime, str | None], None]
            | None
        ) = None
        self._scheduled = False
        self._immediate_restart_claimed = False
        self._closing_current = False
        self._state_error = False
        self._state = self._load()

    def _load(self) -> DeferredRestartState | None:
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
            return parse_deferred_restart_state(payload)
        except FileNotFoundError:
            return None
        except (OSError, ValueError, json.JSONDecodeError):
            self._state_error = True
            LOGGER.warning("Deferred restart state is unavailable", exc_info=True)
            return None

    def set_ready_check(
        self,
        ready_check: Callable[
            [DeferredRestartRequest],
            DeferredRestartReadiness,
        ],
    ) -> None:
        self._ready_check = ready_check

    def set_completion_handler(
        self,
        completion_handler: Callable[
            [str, str, DeferredRestartOutcome, datetime, str | None],
            None,
        ],
    ) -> None:
        self._completion_handler = completion_handler

    def set_started_handler(
        self,
        started_handler: Callable[[str, str, datetime], None],
    ) -> None:
        self._started_handler = started_handler

    def pending(self) -> bool:
        with self._lock:
            return self._state is not None

    def state(self) -> DeferredRestartRequest | None:
        with self._lock:
            return self._state.current.model_copy(deep=True) if self._state else None

    def next_state(self) -> DeferredRestartRequest | None:
        with self._lock:
            return (
                self._state.next.model_copy(deep=True)
                if self._state is not None and self._state.next is not None
                else None
            )

    def requires_service_confirmation(self) -> bool:
        with self._lock:
            return (
                self._state is not None
                and self._state.current.requested_instance_id != self.instance_id
            )

    def begin_immediate_restart(self) -> DeferredRestartImmediateDecision:
        with self._lock:
            if self._immediate_restart_claimed:
                return "in_progress"
            if (
                self._state is None
                or self._state.current.requested_instance_id != self.instance_id
            ):
                self._immediate_restart_claimed = True
                return "launch"
            if self._state.current.status == "started":
                if self._scheduled:
                    return "in_progress"
                self._immediate_restart_claimed = True
                return "launch"
            current = self._state.current.model_copy(deep=True)
            current.status = "started"
            current.updated_at = utc_now()
            queue = self._state.model_copy(deep=True)
            queue.current = current
            try:
                self._write(queue)
            except OSError:
                self._state_error = True
                LOGGER.warning(
                    "Unable to claim deferred restart for immediate start",
                    exc_info=True,
                )
                return "launch"
            self._state = queue
            self._state_error = False
            self._scheduled = True
            self._immediate_restart_claimed = True
            return "claimed"

    def confirm_immediate_restart(self) -> None:
        with self._lock:
            if (
                self._state is None
                or self._state.current.requested_instance_id != self.instance_id
                or self._state.current.status != "started"
            ):
                return
            state = self._state.current.model_copy(deep=True)
        self._notify_started(state)
        write_operation(
            operation_id=state.operation_id,
            action="restart_hub",
            status="started",
            target="chub",
            source_ip=state.source_ip,
        )

    def fail_immediate_restart(self, reason: str | None = None) -> bool:
        with self._lock:
            if not self._immediate_restart_claimed:
                return False
            if (
                self._state is None
                or self._state.current.requested_instance_id != self.instance_id
                or self._state.current.status != "started"
            ):
                self._immediate_restart_claimed = False
                self._scheduled = False
                return True
            state = self._state.current.model_copy(deep=True)
        return self._fail_started_restart(state, reason)

    def request(
        self,
        *,
        operation_id: str,
        task_id: str,
        source_ip: str,
    ) -> DeferredRestartRegistration:
        with self._lock:
            if self._state_error:
                raise ApiError(
                    503,
                    "deferred_restart_state_unavailable",
                    "延迟重启状态文件不可用，本次不会自动重启。",
                )
            if (
                self._state is not None
                and self._state.current.status == "waiting"
                and self._state.current.requested_instance_id == self.instance_id
                and not self._closing_current
            ):
                return DeferredRestartRegistration(
                    operation_id=self._state.current.operation_id,
                    created=False,
                )
            if self._state is not None and self._state.next is not None:
                return DeferredRestartRegistration(
                    operation_id=self._state.next.operation_id,
                    created=False,
                )
            now = utc_now()
            request = DeferredRestartRequest(
                operation_id=operation_id,
                requested_instance_id=self.instance_id,
                requested_task_id=task_id,
                source_ip=source_ip,
                requested_at=now,
                updated_at=now,
            )
            if self._state is None:
                state = DeferredRestartState(current=request)
            else:
                state = self._state.model_copy(deep=True)
                state.next = request
            self._write(state)
            self._state = state
        write_operation(
            operation_id=operation_id,
            action="restart_hub",
            status="requested",
            target="chub",
            source_ip=source_ip,
        )
        return DeferredRestartRegistration(operation_id=operation_id, created=True)

    def service_started(self) -> bool:
        """Consume a request satisfied by any newer healthy Chub instance."""
        with self._lock:
            queue = self._state
            if (
                queue is None
                or queue.current.requested_instance_id == self.instance_id
                or self._closing_current
            ):
                return False
            state = queue.current.model_copy(deep=True)
            self._closing_current = True
            outcome: DeferredRestartOutcome = (
                "succeeded" if state.status == "started" else "cleared"
            )
        if not self._notify_completion(state, outcome):
            with self._lock:
                if self._matches_current(state):
                    self._closing_current = False
            return False
        with self._lock:
            if not self._matches_current(state):
                return False
            try:
                self._advance_queue()
            except OSError:
                self._state_error = True
                self._scheduled = False
                self._closing_current = False
                LOGGER.warning(
                    "Unable to consume satisfied deferred restart state",
                    exc_info=True,
                )
                return False
            self._state_error = False
            self._scheduled = False
            self._closing_current = False
        write_operation(
            operation_id=state.operation_id,
            action="restart_hub",
            status="succeeded",
            target="chub",
            source_ip=state.source_ip,
        )
        self.maybe_schedule()
        return True

    def maybe_schedule(self) -> bool:
        cancelled: DeferredRestartRequest | None = None
        with self._lock:
            if (
                self._state_error
                or self._state is None
                or self._state.current.status != "waiting"
                or self._scheduled
            ):
                return False
            readiness = self._ready_check(self._state.current.model_copy(deep=True))
            if readiness == "waiting":
                return False
            if readiness == "sensitive_task_failed":
                cancelled = self._state.current.model_copy(deep=True)
                self._scheduled = True
                self._closing_current = True
            else:
                self._scheduled = True
        if cancelled is not None:
            self._complete_without_restart(cancelled, "sensitive_task_failed")
            return False
        threading.Thread(
            target=self._run_scheduled,
            daemon=True,
            name="chub-deferred-restart",
        ).start()
        return True

    def _run_scheduled(self) -> None:
        if self.grace_seconds > 0:
            time.sleep(self.grace_seconds)
        cancelled: DeferredRestartRequest | None = None
        with self._lock:
            queue = self._state
            if queue is None or queue.current.status != "waiting":
                self._scheduled = False
                self._closing_current = False
                return
            readiness = self._ready_check(queue.current.model_copy(deep=True))
            if readiness == "waiting":
                self._scheduled = False
                self._closing_current = False
                return
            state = queue.current.model_copy(deep=True)
            if readiness == "sensitive_task_failed":
                self._closing_current = True
                cancelled = state
            else:
                state.status = "started"
                state.updated_at = utc_now()
                updated_queue = queue.model_copy(deep=True)
                updated_queue.current = state
                try:
                    self._write(updated_queue)
                except OSError:
                    self._scheduled = False
                    self._closing_current = False
                    self._state_error = True
                    LOGGER.warning(
                        "Unable to persist deferred restart start",
                        exc_info=True,
                    )
                    return
                self._state = updated_queue
        if cancelled is not None:
            self._complete_without_restart(cancelled, "sensitive_task_failed")
            return
        self._notify_started(state)
        write_operation(
            operation_id=state.operation_id,
            action="restart_hub",
            status="started",
            target="chub",
            source_ip=state.source_ip,
        )
        try:
            process = self.restart_callback()
        except Exception as error:
            LOGGER.warning("Unable to start deferred Chub restart", exc_info=True)
            self._fail_started_restart(state, describe_restart_launch_error(error))
            return
        if process is not None:
            monitor_restart_process(
                process,
                lambda reason: self._fail_started_restart(state, reason),
            )

    def _fail_started_restart(
        self,
        state: DeferredRestartRequest,
        reason: str | None = None,
    ) -> bool:
        with self._lock:
            if not self._matches_current(state):
                return False
            self._closing_current = True
        write_operation(
            operation_id=state.operation_id,
            action="restart_hub",
            status="failed",
            target="chub",
            source_ip=state.source_ip,
        )
        completion_recorded = self._notify_completion(
            state,
            "start_failed",
            reason,
        )
        with self._lock:
            state_cleared = False
            if completion_recorded and self._matches_current(state):
                try:
                    self._advance_queue()
                except OSError:
                    self._state_error = True
                    LOGGER.warning(
                        "Unable to clear failed deferred restart state",
                        exc_info=True,
                    )
                else:
                    self._state_error = False
                    state_cleared = True
            self._scheduled = False
            self._immediate_restart_claimed = False
            self._closing_current = False
        if not state_cleared and completion_recorded:
            LOGGER.warning("Failed deferred restart state remains pending")
        if state_cleared:
            self.maybe_schedule()
        return state_cleared

    def _complete_without_restart(
        self,
        state: DeferredRestartRequest,
        outcome: DeferredRestartOutcome,
    ) -> None:
        if not self._notify_completion(state, outcome):
            with self._lock:
                if self._matches_current(state):
                    self._scheduled = False
                    self._closing_current = False
            return
        with self._lock:
            if not self._matches_current(state):
                return
            try:
                self._advance_queue()
            except OSError:
                self._state_error = True
                self._scheduled = False
                self._closing_current = False
                LOGGER.warning("Unable to clear deferred restart state", exc_info=True)
                return
            self._state_error = False
            self._scheduled = False
            self._closing_current = False
        write_operation(
            operation_id=state.operation_id,
            action="restart_hub",
            status="failed",
            target="chub",
            source_ip=state.source_ip,
        )
        self.maybe_schedule()

    def _matches_current(self, state: DeferredRestartRequest) -> bool:
        return bool(
            self._state is not None
            and self._state.current.operation_id == state.operation_id
            and self._state.current.requested_task_id == state.requested_task_id
            and self._state.current.status == state.status
        )

    def _advance_queue(self) -> None:
        if self._state is None or self._state.next is None:
            self._delete_state()
            self._state = None
            return
        promoted = self._state.next.model_copy(deep=True)
        promoted.requested_instance_id = self.instance_id
        promoted.status = "waiting"
        promoted.updated_at = utc_now()
        next_state = DeferredRestartState(current=promoted)
        self._write(next_state)
        self._state = next_state

    def _notify_started(self, state: DeferredRestartRequest) -> None:
        if self._started_handler is None:
            return
        try:
            self._started_handler(
                state.operation_id,
                state.requested_task_id,
                state.updated_at,
            )
        except Exception:
            LOGGER.warning(
                "Unable to record deferred restart start",
                exc_info=True,
            )

    def _notify_completion(
        self,
        state: DeferredRestartRequest,
        outcome: DeferredRestartOutcome,
        reason: str | None = None,
    ) -> bool:
        if self._completion_handler is None:
            return True
        try:
            self._completion_handler(
                state.operation_id,
                state.requested_task_id,
                outcome,
                utc_now(),
                reason,
            )
        except Exception:
            LOGGER.warning(
                "Unable to record deferred restart completion",
                exc_info=True,
            )
            return False
        return True

    def _write(self, state: DeferredRestartState) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        os.chmod(self.path.parent, 0o700)
        temporary = self.path.with_name(f".{self.path.name}.tmp")
        try:
            temporary.write_text(
                json.dumps(state.model_dump(mode="json"), ensure_ascii=False),
                encoding="utf-8",
            )
            os.chmod(temporary, 0o600)
            os.replace(temporary, self.path)
            os.chmod(self.path, 0o600)
        finally:
            try:
                temporary.unlink()
            except FileNotFoundError:
                pass

    def _delete_state(self) -> None:
        try:
            self.path.unlink()
        except FileNotFoundError:
            pass
