from __future__ import annotations

import json
import logging
import os
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Callable, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.codex.models import utc_now
from app.core.response import ApiError
from app.services.operation_log import write_operation


LOGGER = logging.getLogger("hub.deferred_restart")


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class DeferredRestartState(_StrictModel):
    version: Literal[1] = 1
    operation_id: str = Field(min_length=1, max_length=128)
    requested_instance_id: str = Field(min_length=1, max_length=128)
    requested_task_id: str = Field(min_length=1, max_length=128)
    source_ip: str = Field(min_length=1, max_length=128)
    status: Literal["waiting", "started"] = "waiting"
    requested_at: datetime
    updated_at: datetime


class DeferredRestartCoordinator:
    """Persist and trigger one node-wide restart after quick work is delivered."""

    def __init__(
        self,
        state_file: Path,
        instance_id: str,
        restart_callback: Callable[[], None],
        *,
        grace_seconds: float = 3,
    ) -> None:
        self.path = state_file
        self.instance_id = instance_id
        self.restart_callback = restart_callback
        self.grace_seconds = grace_seconds
        self._lock = threading.RLock()
        self._ready_check: Callable[[], bool] = lambda: False
        self._completion_handler: Callable[[str, bool, datetime], None] | None = None
        self._scheduled = False
        self._state_error = False
        self._state = self._load()

    def _load(self) -> DeferredRestartState | None:
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
            return DeferredRestartState.model_validate(payload)
        except FileNotFoundError:
            return None
        except (OSError, ValueError, json.JSONDecodeError):
            self._state_error = True
            LOGGER.warning("Deferred restart state is unavailable", exc_info=True)
            return None

    def set_ready_check(self, ready_check: Callable[[], bool]) -> None:
        self._ready_check = ready_check

    def set_completion_handler(
        self,
        completion_handler: Callable[[str, bool, datetime], None],
    ) -> None:
        self._completion_handler = completion_handler

    def pending(self) -> bool:
        with self._lock:
            return self._state is not None

    def state(self) -> DeferredRestartState | None:
        with self._lock:
            return self._state.model_copy(deep=True) if self._state else None

    def request(
        self,
        *,
        operation_id: str,
        task_id: str,
        source_ip: str,
    ) -> bool:
        with self._lock:
            if self._state_error:
                raise ApiError(
                    503,
                    "deferred_restart_state_unavailable",
                    "延迟重启状态文件不可用，本次不会自动重启。",
                )
            if self._state is not None:
                return False
            now = utc_now()
            state = DeferredRestartState(
                operation_id=operation_id,
                requested_instance_id=self.instance_id,
                requested_task_id=task_id,
                source_ip=source_ip,
                requested_at=now,
                updated_at=now,
            )
            self._write(state)
            self._state = state
        write_operation(
            operation_id=operation_id,
            action="restart_hub",
            status="requested",
            target="chub",
            source_ip=source_ip,
        )
        return True

    def service_started(self) -> bool:
        """Consume a request satisfied by any newer healthy Chub instance."""
        with self._lock:
            state = self._state
            if state is None or state.requested_instance_id == self.instance_id:
                return False
            try:
                self._delete_state()
            except OSError:
                self._state_error = True
                self._scheduled = False
                LOGGER.warning(
                    "Unable to consume satisfied deferred restart state",
                    exc_info=True,
                )
                return False
            self._state = None
            self._state_error = False
            self._scheduled = False
        write_operation(
            operation_id=state.operation_id,
            action="restart_hub",
            status="succeeded",
            target="chub",
            source_ip=state.source_ip,
        )
        self._notify_completion(state, state.status == "started")
        return True

    def maybe_schedule(self) -> bool:
        with self._lock:
            if (
                self._state is None
                or self._state.status != "waiting"
                or self._scheduled
                or not self._ready_check()
            ):
                return False
            self._scheduled = True
        threading.Thread(
            target=self._run_scheduled,
            daemon=True,
            name="chub-deferred-restart",
        ).start()
        return True

    def _run_scheduled(self) -> None:
        if self.grace_seconds > 0:
            time.sleep(self.grace_seconds)
        with self._lock:
            state = self._state
            if state is None or state.status != "waiting" or not self._ready_check():
                self._scheduled = False
                return
            state = state.model_copy(deep=True)
            state.status = "started"
            state.updated_at = utc_now()
            try:
                self._write(state)
            except OSError:
                self._scheduled = False
                self._state_error = True
                LOGGER.warning("Unable to persist deferred restart start", exc_info=True)
                return
            self._state = state
        write_operation(
            operation_id=state.operation_id,
            action="restart_hub",
            status="started",
            target="chub",
            source_ip=state.source_ip,
        )
        try:
            self.restart_callback()
        except Exception:
            write_operation(
                operation_id=state.operation_id,
                action="restart_hub",
                status="failed",
                target="chub",
                source_ip=state.source_ip,
            )
            with self._lock:
                state_cleared = False
                try:
                    self._delete_state()
                except OSError:
                    self._state_error = True
                    LOGGER.warning(
                        "Unable to clear failed deferred restart state",
                        exc_info=True,
                    )
                else:
                    self._state = None
                    self._state_error = False
                    state_cleared = True
                self._scheduled = False
            if state_cleared:
                self._notify_completion(state, False)
            LOGGER.warning("Unable to start deferred Chub restart", exc_info=True)

    def _notify_completion(
        self,
        state: DeferredRestartState,
        show_automatic_success: bool,
    ) -> None:
        if self._completion_handler is None:
            return
        try:
            self._completion_handler(
                state.requested_task_id,
                show_automatic_success,
                utc_now(),
            )
        except Exception:
            LOGGER.warning(
                "Unable to record deferred restart completion",
                exc_info=True,
            )

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
