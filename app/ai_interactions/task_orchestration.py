"""Shared pre-Worker dispatch for interactive user tasks.

The dispatcher owns only Chub's orchestration request and the one physical
Quick Worker submission. Entry-specific routing and presentation stay outside
this module.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import threading
from datetime import datetime
from pathlib import Path
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.ai_interactions.models import QuickInteractionTask, QuickInteractionWeixinRoute
from app.ai_session.models import utc_now
from app.core.response import ApiError


MAX_STATE_BYTES = 4 * 1024 * 1024
MAX_REQUESTS = 5_000
STATE_VERSION = 1


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class TaskOrchestrationRequest(_StrictModel):
    id: str = Field(min_length=36, max_length=36)
    entry: Literal["web", "weixin"]
    idempotency_key: str = Field(min_length=64, max_length=64)
    payload_fingerprint: str = Field(min_length=64, max_length=64)
    session_id: str = Field(min_length=1, max_length=128)
    # Terminal records retain only the immutable fingerprints.  Keeping the
    # original prompt is necessary only until the physical task is linked.
    prompt: str | None = Field(default=None, min_length=1, max_length=8_000)
    stage_chain: list[str] = Field(default_factory=list, max_length=0)
    cursor: Literal[0] = 0
    operation_id: str = Field(min_length=1, max_length=160)
    task_id: str | None = Field(default=None, max_length=128)
    status: Literal["accepted", "running", "completed", "failed"] = "accepted"
    created_at: datetime
    updated_at: datetime


class _State(_StrictModel):
    version: Literal[STATE_VERSION] = STATE_VERSION
    requests: list[TaskOrchestrationRequest] = Field(default_factory=list, max_length=MAX_REQUESTS)


class TaskOrchestrationDispatcher:
    """The sole writer of physical interactive user-task submissions."""

    def __init__(self, state_file: Path, quick_interactions) -> None:
        self.path = state_file.with_name("task-orchestration.json")
        self.quick_interactions = quick_interactions
        self._lock = threading.RLock()
        self._state_error = False
        self._state = self._load()
        if not self._state_error and self._minimize_terminal_records():
            self._write()

    @staticmethod
    def _fingerprint(*values: str) -> str:
        return hashlib.sha256("\0".join(values).encode("utf-8")).hexdigest()

    def submit_web(
        self,
        *,
        session_id: str,
        prompt: str,
        request_id: str,
        operation_id: str,
        source_ip: str,
    ) -> QuickInteractionTask:
        return self._submit(
            entry="web",
            idempotency_key=self._fingerprint("web", session_id, request_id),
            session_id=session_id,
            prompt=prompt,
            operation_id=operation_id,
            source_ip=source_ip,
            notification_route=None,
        )

    def submit_weixin(
        self,
        *,
        message_id: str,
        route_fingerprint: str,
        session_id: str,
        prompt: str,
        operation_id: str,
        source_ip: str,
        notification_route: QuickInteractionWeixinRoute,
        summary_max_chars: int,
        summary_max_width: int,
    ) -> QuickInteractionTask:
        return self._submit(
            entry="weixin",
            idempotency_key=self._fingerprint("weixin", message_id, route_fingerprint),
            session_id=session_id,
            prompt=prompt,
            operation_id=operation_id,
            source_ip=source_ip,
            notification_route=notification_route,
            summary_max_chars=summary_max_chars,
            summary_max_width=summary_max_width,
        )

    def _submit(
        self,
        *,
        entry: Literal["web", "weixin"],
        idempotency_key: str,
        session_id: str,
        prompt: str,
        operation_id: str,
        source_ip: str,
        notification_route: QuickInteractionWeixinRoute | None,
        summary_max_chars: int = 27,
        summary_max_width: int | None = None,
    ) -> QuickInteractionTask:
        payload_fingerprint = self._fingerprint(session_id, prompt)
        with self._lock:
            self._require_available()
            existing = next(
                (item for item in self._state.requests if item.idempotency_key == idempotency_key),
                None,
            )
            if existing is not None:
                if existing.payload_fingerprint != payload_fingerprint:
                    raise ApiError(409, "task_orchestration_request_conflict", "请求标识已用于不同任务，未重复提交。")
                self._recover_unlinked_request(existing)
                if existing.task_id is None:
                    raise ApiError(
                        409,
                        "task_orchestration_submission_failed",
                        "此前任务未被 Quick Worker 接受，请重新提交。",
                    )
                try:
                    return self.quick_interactions.get(existing.task_id)
                except ApiError as exc:
                    if exc.code != "quick_interaction_not_found":
                        raise
                    if existing.status in {"completed", "failed"}:
                        raise ApiError(
                            409,
                            "task_orchestration_retained",
                            "此前任务已结束，当前幂等记录只保留终态，未重复提交。",
                        ) from None
                    self._set_status(existing.id, "failed")
                    raise ApiError(
                        409,
                        "task_orchestration_submission_failed",
                        "此前任务状态无法确认，已结束本次请求。请重新提交。",
                    ) from None

            if len(self._state.requests) >= MAX_REQUESTS:
                raise ApiError(
                    503,
                    "task_orchestration_retention_full",
                    "任务幂等记录已达固定留存上限，请先执行本机编排终态清理后再提交。",
                )

            now = utc_now()
            request = TaskOrchestrationRequest(
                id=str(uuid4()),
                entry=entry,
                idempotency_key=idempotency_key,
                payload_fingerprint=payload_fingerprint,
                session_id=session_id,
                prompt=prompt,
                operation_id=operation_id,
                created_at=now,
                updated_at=now,
            )
            self._state.requests.append(request)
            self._write()

        try:
            with self.quick_interactions.session_operation_guard(session_id):
                task = self.quick_interactions.submit(
                    session_id,
                    prompt,
                    operation_id=operation_id,
                    source_ip=source_ip,
                    notification_route=notification_route,
                    summary_max_chars=summary_max_chars,
                    summary_max_width=summary_max_width,
                )
        except Exception:
            with self._lock:
                current = self._find(request.id)
                self._recover_unlinked_request(current)
            raise

        with self._lock:
            request = self._find(request.id)
            request.task_id = task.id
            request.status = (
                "running"
                if getattr(task, "status", "requested") in {"requested", "running"}
                else "completed"
            )
            request.updated_at = utc_now()
            self._write()
        return task

    def record_task_finished(self, task: QuickInteractionTask) -> None:
        if task.status not in {"succeeded", "failed", "timed_out", "cancelled"}:
            return
        with self._lock:
            changed = False
            for request in self._state.requests:
                if request.task_id != task.id:
                    continue
                request.status = "completed" if task.status == "succeeded" else "failed"
                request.prompt = None
                request.updated_at = utc_now()
                changed = True
            if changed:
                self._write()

    def reconcile(self) -> None:
        """Refresh known task outcomes; never create a replacement submission."""
        with self._lock:
            if self._state_error:
                return
            for request in self._state.requests:
                if request.task_id is None:
                    self._recover_unlinked_request(request)
                if request.task_id is None or request.status in {"completed", "failed"}:
                    continue
                try:
                    task = self.quick_interactions.get(request.task_id)
                except ApiError:
                    continue
                if task.status in {"succeeded", "failed", "timed_out", "cancelled"}:
                    request.status = "completed" if task.status == "succeeded" else "failed"
                    request.prompt = None
                    request.updated_at = utc_now()
            self._write()

    def compact_terminal_records(self) -> tuple[int, int]:
        """Remove only terminal idempotency tombstones through a trusted maintenance action.

        This deliberately never runs as part of ordinary submission: callers
        must make the recovery boundary explicit, then issue new request IDs.
        """
        with self._lock:
            self._require_available()
            retained = [
                request
                for request in self._state.requests
                if request.status not in {"completed", "failed"}
            ]
            removed = len(self._state.requests) - len(retained)
            if removed:
                self._state.requests = retained
                self._write()
            return removed, len(retained)

    def _find(self, request_id: str) -> TaskOrchestrationRequest:
        return next(item for item in self._state.requests if item.id == request_id)

    def _set_status(self, request_id: str, status: Literal["failed"]) -> None:
        request = self._find(request_id)
        request.status = status
        if status == "failed":
            request.prompt = None
        request.updated_at = utc_now()
        self._write()

    def _minimize_terminal_records(self) -> bool:
        """Drop no-longer-needed request text from both new and legacy terminal rows."""
        changed = False
        for request in self._state.requests:
            if request.status not in {"completed", "failed"} or request.prompt is None:
                continue
            request.prompt = None
            changed = True
        return changed

    def _recover_unlinked_request(self, request: TaskOrchestrationRequest) -> None:
        """Resolve a persisted submission before declaring it failed.

        Quick Interaction persists the trusted operation association before it
        talks to the Worker, so a lost response must be reconciled from that
        association instead of being treated as an unknown duplicate.
        """
        if request.task_id is not None:
            return
        finder = getattr(self.quick_interactions, "find_for_operation", None)
        task = finder(request.operation_id) if callable(finder) else None
        if task is None:
            self._set_status(request.id, "failed")
            return
        if (
            getattr(task, "session_id", None) != request.session_id
            or self._fingerprint(request.session_id, getattr(task, "prompt", ""))
            != request.payload_fingerprint
        ):
            self._set_status(request.id, "failed")
            return
        request.task_id = task.id
        request.status = (
            "running"
            if getattr(task, "status", "requested") in {"requested", "running"}
            else "completed"
            if getattr(task, "status", None) == "succeeded"
            else "failed"
        )
        if request.status in {"completed", "failed"}:
            request.prompt = None
        request.updated_at = utc_now()
        self._write()

    def _require_available(self) -> None:
        if self._state_error:
            raise ApiError(
                503,
                "task_orchestration_state_unavailable",
                "任务编排状态不可用，当前拒绝新的任务提交；请先完成恢复。",
            )

    def _load(self) -> _State:
        fallback = _State()
        try:
            if self.path.is_symlink():
                raise OSError("Task orchestration state must not be a symlink")
            if self.path.stat().st_size > MAX_STATE_BYTES:
                raise OSError("Task orchestration state exceeds its fixed limit")
            payload = json.loads(self.path.read_bytes().decode("utf-8"))
            state = _State.model_validate(payload)
        except FileNotFoundError:
            return fallback
        except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValidationError):
            self._state_error = True
            return fallback
        return state

    def _write(self) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            encoded = self._state.model_dump_json().encode("utf-8")
            if len(encoded) > MAX_STATE_BYTES:
                raise OSError("Task orchestration state exceeds its fixed limit")
            temporary = self.path.with_name(f".{self.path.name}.{uuid4().hex}.tmp")
            temporary.write_bytes(encoded)
            os.chmod(temporary, 0o600)
            temporary.replace(self.path)
            os.chmod(self.path, 0o600)
        except OSError:
            self._state_error = True
            raise
        finally:
            if "temporary" in locals():
                temporary.unlink(missing_ok=True)


def retire_weixin_refinement_state(
    *,
    weixin_state_file: Path,
    translation_state_file: Path,
    orchestration_modules_dir: Path,
    plugin_lifecycle_file: Path,
    retirement_marker_file: Path,
) -> None:
    """Discard the retired Weixin-only refinement implementation.

    The retired state is inspected before it is discarded. This release never
    migrates, replays, or preserves Weixin-only refinement work.
    """
    if retirement_marker_file.exists():
        marker = _read_json_object(retirement_marker_file)
        if marker.get("version") == 1:
            return
        raise OSError("Retired Weixin orchestration marker is invalid")
    _read_legacy_request_state(weixin_state_file, translation_state_file)
    _clear_legacy_weixin_fields(weixin_state_file)
    _remove_file(translation_state_file)
    _remove_tree(orchestration_modules_dir)
    _clear_legacy_plugin_lifecycle(plugin_lifecycle_file)
    _write_json_object(
        retirement_marker_file,
        {"version": 1, "completed_at": utc_now().isoformat()},
    )


def _read_legacy_request_state(
    weixin_state_file: Path,
    translation_state_file: Path,
) -> None:
    weixin = _read_json_object(weixin_state_file)
    translation = _read_json_object(translation_state_file)
    requests = weixin.get("orchestration_requests", [])
    entries = translation.get("entries", [])
    if not isinstance(requests, list) or not isinstance(entries, list):
        raise OSError("Retired Weixin orchestration state is invalid")
    # Validate the retired structures before removal. Their contents are
    # deliberately discarded, including non-terminal work, by this version.


def _clear_legacy_weixin_fields(path: Path) -> None:
    payload = _read_json_object(path)
    if not payload:
        return
    changed = False
    for key in (
        "orchestration_implementation",
        "orchestration_enabled",
        "orchestration_development_source_hash",
        "orchestration_module_ref",
        "orchestration_requests",
    ):
        if key in payload:
            payload.pop(key)
            changed = True
    if changed:
        _write_json_object(path, payload)


def _clear_legacy_plugin_lifecycle(path: Path) -> None:
    payload = _read_json_object(path)
    if not payload:
        return
    changed = False
    for container in ("imports", "enabled", "metadata"):
        value = payload.get(container)
        if isinstance(value, dict):
            if "weixin-orchestration" in value:
                value.pop("weixin-orchestration")
                changed = True
    if changed:
        _write_json_object(path, payload)


def _read_json_object(path: Path) -> dict[str, object]:
    if not path.exists():
        return {}
    if path.is_symlink() or not path.is_file():
        raise OSError("Retired state path is unsafe")
    try:
        if path.stat().st_size > MAX_STATE_BYTES:
            raise OSError("Retired state exceeds its fixed limit")
        payload = json.loads(path.read_bytes().decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise OSError("Retired state cannot be read") from exc
    if not isinstance(payload, dict):
        raise OSError("Retired state has an invalid root")
    return payload


def _write_json_object(path: Path, payload: dict[str, object]) -> None:
    encoded = json.dumps(payload, ensure_ascii=True, separators=(",", ":")).encode("utf-8")
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    try:
        temporary.write_bytes(encoded)
        os.chmod(temporary, 0o600)
        temporary.replace(path)
        os.chmod(path, 0o600)
    finally:
        temporary.unlink(missing_ok=True)


def _remove_file(path: Path) -> None:
    if not path.exists():
        return
    if path.is_symlink() or not path.is_file():
        raise OSError("Retired state path is unsafe")
    path.unlink()


def _remove_tree(path: Path) -> None:
    if not path.exists():
        return
    if path.is_symlink() or not path.is_dir():
        raise OSError("Retired module directory is unsafe")
    shutil.rmtree(path)
