"""Shared pre-Worker dispatch for interactive user tasks.

The dispatcher owns only Chub's orchestration request and the one physical
Quick Worker submission. Entry-specific routing and presentation stay outside
this module.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import shutil
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Callable, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.ai_interactions.models import (
    PromptOptimizationVersions,
    QuickInteractionTask,
    QuickInteractionWeixinRoute,
)
from app.ai_session.models import utc_now
from app.core.response import ApiError


MAX_STATE_BYTES = 4 * 1024 * 1024
MAX_REQUESTS = 5_000
STATE_VERSION = 1
MAX_INTERNAL_CLEANUP_ATTEMPTS = 3
LOGGER = logging.getLogger("hub.task_orchestration")
PromptOptimizationFailureReason = Literal[
    "stage_unavailable",
    "stage_selection_unavailable",
    "execution_snapshot_failed",
    "instruction_generation_failed",
    "implementation_unavailable",
    "internal_task_failed",
    "invalid_optimization_result",
    "optimization_stage_failed",
    "optimization_timed_out",
    "timeline_result_unavailable",
]


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class TaskOrchestrationStageSnapshot(_StrictModel):
    kind: Literal["module"] = "module"
    module_id: Literal["chub-task-prompt-optimizer"]
    stage_id: Literal["prompt_optimization"]
    mode: Literal["direct", "auto"]
    implementation_ref: str = Field(min_length=1, max_length=255)


class InternalSessionExecutionSnapshot(_StrictModel):
    runtime_id: str = Field(min_length=1, max_length=128)
    implementation_id: str = Field(min_length=1, max_length=255)
    model: str | None = Field(default=None, max_length=128)
    reasoning_effort: str | None = Field(default=None, max_length=32)


class TaskOrchestrationRequest(_StrictModel):
    id: str = Field(min_length=36, max_length=36)
    entry: Literal["web", "weixin"]
    idempotency_key: str = Field(min_length=64, max_length=64)
    payload_fingerprint: str = Field(min_length=64, max_length=64)
    session_id: str = Field(min_length=1, max_length=128)
    plugin_enabled: bool | None = False
    plugin_mode: Literal["direct", "auto"] | None = None
    implementation_ref: str | None = Field(default=None, max_length=255)
    # Terminal records retain only immutable fingerprints and stage snapshots.
    # Keeping the original prompt is necessary only until the physical task is linked.
    prompt: str | None = Field(default=None, min_length=1, max_length=8_000)
    stage_chain: list[TaskOrchestrationStageSnapshot] = Field(
        default_factory=list, max_length=1
    )
    cursor: int = Field(default=0, ge=0, le=1)
    operation_id: str = Field(min_length=1, max_length=160)
    task_id: str | None = Field(default=None, max_length=128)
    stage_call_id: str | None = Field(default=None, max_length=160)
    internal_execution_snapshot: InternalSessionExecutionSnapshot | None = None
    optimization_instruction: str | None = Field(default=None, max_length=20_000)
    internal_session_id: str | None = Field(default=None, max_length=128)
    internal_task_id: str | None = Field(default=None, max_length=128)
    internal_cleanup_complete: bool = False
    internal_cleanup_attempts: int = Field(default=0, ge=0, le=MAX_INTERNAL_CLEANUP_ATTEMPTS)
    # The Chinese value is the only variant submitted to the main task.
    # English is retained for the maintainer's Web timeline only.
    optimized_prompt: str | None = Field(default=None, max_length=8_000)
    optimized_prompt_en: str | None = Field(default=None, max_length=8_000)
    optimization_deadline: datetime | None = None
    optimization_fallback: bool = False
    optimization_failure_reason: PromptOptimizationFailureReason | None = None
    status: Literal["accepted", "running", "completed", "failed"] = "accepted"
    created_at: datetime
    updated_at: datetime


class _State(_StrictModel):
    version: Literal[STATE_VERSION] = STATE_VERSION
    requests: list[TaskOrchestrationRequest] = Field(default_factory=list, max_length=MAX_REQUESTS)


@dataclass(frozen=True)
class PromptOptimizerStageSelection:
    enabled: bool = False
    mode: str | None = None
    implementation_ref: str | None = None
    stage_runner: Callable[[str, str], str] | None = None
    optimization_prompt_builder: Callable[[str], str] | None = None
    optimization_result_reader: Callable[[str], PromptOptimizationVersions] | None = None


class _PromptOptimizationFailure(Exception):
    def __init__(self, reason: PromptOptimizationFailureReason):
        super().__init__(reason)
        self.reason = reason


class TaskOrchestrationDispatcher:
    """The sole writer of physical interactive user-task submissions."""

    def __init__(self, state_file: Path, quick_interactions) -> None:
        self.path = state_file.with_name("task-orchestration.json")
        self.quick_interactions = quick_interactions
        self._prompt_optimizer_stage_provider: Callable[
            [Literal["web", "weixin"]], PromptOptimizerStageSelection
        ] | None = None
        self._lock = threading.RLock()
        self._advance_locks: dict[str, threading.Lock] = {}
        self._optimization_submission_lock = threading.Lock()
        self._accepting_requests: set[str] = set()
        self._optimization_use_case = None
        self._implementation_resolver = None
        self._development_snapshot_cleanup_handler: Callable[[], None] | None = None
        self._closed = threading.Event()
        self._wake = threading.Event()
        self._runner = None
        self._state_error = False
        self._state = self._load()
        if not self._state_error and self._minimize_terminal_records():
            self._write()

    @staticmethod
    def _fingerprint(*values: str) -> str:
        return hashlib.sha256("\0".join(values).encode("utf-8")).hexdigest()

    def set_prompt_optimizer_stage_provider(
        self,
        provider: Callable[[Literal["web", "weixin"]], PromptOptimizerStageSelection],
    ) -> None:
        self._prompt_optimizer_stage_provider = provider

    def configure_optimization(self, use_case, implementation_resolver) -> None:
        self._optimization_use_case = use_case
        self._implementation_resolver = implementation_resolver

    def set_development_snapshot_cleanup_handler(self, handler: Callable[[], None]) -> None:
        self._development_snapshot_cleanup_handler = handler

    def _cleanup_development_snapshots(self) -> None:
        handler = self._development_snapshot_cleanup_handler
        if handler is None:
            return
        try:
            handler()
        except Exception:
            # Cleanup is housekeeping and must not change task submission semantics.
            LOGGER.warning("Unable to clean unreferenced orchestration snapshots")

    def start(self) -> None:
        if self._runner is None:
            self._runner = threading.Thread(target=self._run_optimization, daemon=True,
                                            name="chub-prompt-optimization")
            self._runner.start()

    def close(self) -> None:
        self._closed.set()
        self._wake.set()

    def auto_available(self) -> bool:
        return self._optimization_use_case is not None and callable(self._implementation_resolver)

    def has_active_implementation_reference(self, implementation_ref: str) -> bool:
        with self._lock:
            self._require_available()
            # A terminal parent can never consume the optimizer result again.
            # Its child cleanup is tracked separately and must not pin module code.
            if implementation_ref == "chub-task-prompt-optimizer":
                return any(
                    item.status not in {"completed", "failed"}
                    and any(stage.module_id == implementation_ref for stage in item.stage_chain)
                    for item in self._state.requests
                )
            return any(item.implementation_ref == implementation_ref
                       and item.status not in {"completed", "failed"}
                       for item in self._state.requests)

    def active_implementation_references(self) -> frozenset[str]:
        with self._lock:
            self._require_available()
            return frozenset(
                item.implementation_ref
                for item in self._state.requests
                if item.status not in {"completed", "failed"}
                and item.implementation_ref
            )

    def is_prompt_optimizer_effective(self, entry: Literal["web", "weixin"] = "web") -> bool:
        provider = self._prompt_optimizer_stage_provider
        if provider is None:
            return False
        try:
            selection = provider(entry)
        except ApiError:
            return False
        return bool(
            selection.enabled
            and selection.mode in {"direct", "auto"}
            and selection.implementation_ref
            and callable(selection.stage_runner)
            and (selection.mode == "direct" or (
                self.auto_available() and callable(selection.optimization_prompt_builder)
                and callable(selection.optimization_result_reader)))
        )

    def submit_web(
        self,
        *,
        session_id: str,
        prompt: str,
        request_id: str,
        operation_id: str,
        source_ip: str,
    ) -> QuickInteractionTask:
        try:
            return self._submit(
                entry="web",
                idempotency_key=self._fingerprint("web", session_id, request_id),
                session_id=session_id,
                prompt=prompt,
                operation_id=operation_id,
                source_ip=source_ip,
                notification_route=None,
            )
        finally:
            self._cleanup_development_snapshots()

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
        try:
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
        finally:
            self._cleanup_development_snapshots()

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
                if existing.id in self._accepting_requests:
                    raise ApiError(409, "task_orchestration_submission_in_progress",
                                   "同一任务请求正在受理，请稍后使用原请求标识重试。")
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

            selection = PromptOptimizerStageSelection()
            stage_selection_failed = False
            if self._prompt_optimizer_stage_provider is not None:
                try:
                    selection = self._prompt_optimizer_stage_provider(entry)
                except Exception:
                    # The selection may represent an enabled optional auto
                    # stage. Preserve the ordinary task path, but don't report
                    # the plugin as disabled or hide the degradation.
                    stage_selection_failed = True
                    LOGGER.warning("Prompt optimizer selection unavailable")
            stage_chain: list[TaskOrchestrationStageSnapshot] = []
            optimization_setup_failure: PromptOptimizationFailureReason | None = None
            if selection.enabled:
                if selection.mode not in {"direct", "auto"}:
                    raise ApiError(
                        409,
                        "task_orchestration_mode_unavailable",
                        "当前任务编排模式不可用；任务未提交。",
                    )
                if selection.mode == "direct" and (
                    not selection.implementation_ref or not callable(selection.stage_runner)
                ):
                    raise ApiError(
                        503,
                        "task_orchestration_stage_unavailable",
                        "已启用的任务编排阶段当前不可用；任务未提交。",
                    )
                if selection.mode == "auto" and (
                    not selection.implementation_ref or not callable(selection.stage_runner)
                ):
                    optimization_setup_failure = "stage_unavailable"
                else:
                    stage_chain.append(
                        TaskOrchestrationStageSnapshot(
                            module_id="chub-task-prompt-optimizer",
                            stage_id="prompt_optimization",
                            mode=selection.mode,
                            implementation_ref=selection.implementation_ref,
                        )
                    )

            now = utc_now()
            request = TaskOrchestrationRequest(
                id=str(uuid4()),
                entry=entry,
                idempotency_key=idempotency_key,
                payload_fingerprint=payload_fingerprint,
                session_id=session_id,
                plugin_enabled=(None if stage_selection_failed else selection.enabled),
                plugin_mode=selection.mode if selection.enabled else None,
                implementation_ref=(
                    selection.implementation_ref if selection.enabled else None
                ),
                prompt=prompt,
                stage_chain=stage_chain,
                operation_id=operation_id,
                created_at=now,
                updated_at=now,
            )
            if stage_selection_failed:
                request.optimization_fallback = True
                request.optimization_failure_reason = "stage_selection_unavailable"
                request.internal_cleanup_complete = True
            elif selection.enabled and selection.mode == "auto":
                request.stage_call_id = f"prompt-optimization:{request.id}"
                if optimization_setup_failure is None:
                    try:
                        if not (
                            self.auto_available()
                            and callable(selection.optimization_prompt_builder)
                            and callable(selection.optimization_result_reader)
                        ):
                            raise _PromptOptimizationFailure("stage_unavailable")
                    except _PromptOptimizationFailure as exc:
                        optimization_setup_failure = exc.reason
                    except Exception:
                        optimization_setup_failure = "stage_unavailable"
                if optimization_setup_failure is None:
                    try:
                        request.internal_execution_snapshot = InternalSessionExecutionSnapshot.model_validate(
                            self._optimization_use_case.execution_snapshot()
                        )
                    except Exception:
                        optimization_setup_failure = "execution_snapshot_failed"
                if optimization_setup_failure is None:
                    try:
                        instruction = selection.optimization_prompt_builder(prompt)
                        if (
                            not isinstance(instruction, str)
                            or not instruction.strip()
                            or len(instruction) > 20_000
                        ):
                            raise ValueError("invalid optimization instruction")
                    except Exception:
                        optimization_setup_failure = "instruction_generation_failed"
                    else:
                        request.optimization_instruction = instruction
                        # One bounded stage, including queueing/recovery. No replacement AI attempt.
                        request.optimization_deadline = now + timedelta(minutes=15)
                if optimization_setup_failure is not None:
                    request.optimization_fallback = True
                    request.optimization_failure_reason = optimization_setup_failure
                    request.internal_cleanup_complete = True
            self._state.requests.append(request)
            self._write()
            self._accepting_requests.add(request.id)

        submitted_prompt = prompt
        if request.stage_chain and request.plugin_mode == "direct":
            try:
                assert selection.stage_runner is not None
                submitted_prompt = selection.stage_runner("direct", prompt)
                if not isinstance(submitted_prompt, str) or submitted_prompt != prompt:
                    raise ValueError("direct mode must return the normalized prompt unchanged")
                with self._lock:
                    request = self._find(request.id)
                    request.cursor = len(request.stage_chain)
                    request.updated_at = utc_now()
                    self._write()
            except Exception:
                with self._lock:
                    request = self._find(request.id)
                    request.status = "failed"
                    request.prompt = None
                    request.updated_at = utc_now()
                    self._accepting_requests.discard(request.id)
                    self._write()
                raise ApiError(
                    503,
                    "task_orchestration_stage_failed",
                    "任务编排阶段未能完成；主任务未提交。",
                ) from None

        try:
            with self.quick_interactions.session_operation_guard(session_id):
                submit_options = {}
                if request.plugin_mode == "auto" or request.optimization_fallback:
                    submit_options["defer_orchestration"] = True
                task = self.quick_interactions.submit(
                    session_id,
                    submitted_prompt,
                    operation_id=operation_id,
                    source_ip=source_ip,
                    notification_route=notification_route,
                    summary_max_chars=summary_max_chars,
                    summary_max_width=summary_max_width,
                    **submit_options,
                )
        except Exception:
            with self._lock:
                current = self._find(request.id)
                self._accepting_requests.discard(request.id)
                self._recover_unlinked_request(current)
            raise

        with self._lock:
            request = self._find(request.id)
            request.task_id = task.id
            if request.plugin_mode == "auto" or request.optimization_fallback:
                task = self.quick_interactions.get(task.id)
            request.prompt = None
            request.status = (
                "running"
                if getattr(task, "status", "requested") in {"requested", "running"}
                else "completed" if task.status == "succeeded" else "failed"
            )
            request.updated_at = utc_now()
            self._accepting_requests.discard(request.id)
            self._write()
        if request.plugin_mode == "auto" or request.optimization_fallback:
            self._wake.set()
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
                request.optimization_instruction = None
                request.optimized_prompt = None
                request.optimized_prompt_en = None
                request.updated_at = utc_now()
                changed = True
            if changed:
                self._write()
        self._wake.set()

    def _run_optimization(self) -> None:
        while not self._closed.is_set():
            with self._lock:
                ids = [item.id for item in self._state.requests
                       if (item.plugin_mode == "auto" or item.optimization_fallback)
                       and item.task_id is not None
                       and (item.status not in {"completed", "failed"}
                            or (
                                item.optimization_fallback
                                and not item.internal_cleanup_complete
                            ))]
            for request_id in ids:
                if self._closed.is_set():
                    return
                try:
                    self._advance_optimization(request_id)
                except Exception:
                    LOGGER.warning("Optimization reconciliation unavailable: request=%s", request_id)
            self._wake.wait(1)
            self._wake.clear()

    def _advance_optimization(self, request_id: str) -> None:
        with self._lock:
            guard = self._advance_locks.setdefault(request_id, threading.Lock())
        if not guard.acquire(blocking=False):
            return
        try:
            with self._lock:
                self._require_available()
                request = self._find(request_id).model_copy(deep=True)
            try:
                parent = self.quick_interactions.get(request.task_id)
            except ApiError as exc:
                if exc.code != "quick_interaction_not_found":
                    raise
                with self._lock:
                    self._set_status(request_id, "failed")
                parent = None
            if parent is None or parent.status not in {"requested", "running"}:
                if parent is not None:
                    self.record_task_finished(parent)
                if request.optimization_fallback and not request.internal_cleanup_complete:
                    self._cleanup_orphaned_internal_task(
                        request_id, request.internal_task_id
                    )
                elif request.internal_task_id:
                    self._cleanup_orphaned_internal_task(
                        request_id, request.internal_task_id
                    )
                return
            if not parent.orchestration_pending:
                if request.optimization_fallback and not request.internal_cleanup_complete:
                    self._cleanup_orphaned_internal_task(
                        request_id, request.internal_task_id
                    )
                return
            if request.optimization_fallback:
                self._continue_after_optimization_failure(request_id, parent)
                return
            if request.optimization_deadline is None or utc_now() >= request.optimization_deadline:
                self._mark_optimization_fallback(request_id, "optimization_timed_out")
                self._continue_after_optimization_failure(
                    request_id, self.quick_interactions.get(request.task_id)
                )
                return
            if not self.quick_interactions.recovery_ready or self._closed.is_set():
                return
            try:
                optimized = self._read_optimized_result(request)
            except Exception as exc:
                if self._state_error:
                    raise
                reason = (
                    exc.reason if isinstance(exc, _PromptOptimizationFailure)
                    else "optimization_stage_failed"
                )
                self._mark_optimization_fallback(request_id, reason)
                self._continue_after_optimization_failure(
                    request_id, self.quick_interactions.get(request.task_id)
                )
                return
            if optimized is None:
                return

            with self._lock:
                current = self._find(request_id)
                if current.optimized_prompt is None:
                    current.optimized_prompt = optimized.chinese
                    current.optimized_prompt_en = optimized.english
                    current.cursor = len(current.stage_chain)
                    current.optimization_instruction = None
                    current.internal_cleanup_complete = True
                    self._write()
                request = current.model_copy(deep=True)
            if request.optimized_prompt_en is not None:
                try:
                    self.quick_interactions.set_prompt_optimization_result(
                        request.task_id,
                        PromptOptimizationVersions(
                            chinese=request.optimized_prompt,
                            english=request.optimized_prompt_en,
                        ),
                    )
                except Exception:
                    if self._state_error:
                        raise
                    self._mark_optimization_fallback(
                        request_id, "timeline_result_unavailable"
                    )
                    self._continue_after_optimization_failure(
                        request_id, self.quick_interactions.get(request.task_id)
                    )
                    return
            try:
                self.quick_interactions.notify_prompt_optimization_completed(
                    request.task_id
                )
            except Exception:
                # The result notification must never hold or fail the main task.
                LOGGER.warning(
                    "Unable to queue prompt optimization result notification: request=%s",
                    request_id,
                )
            self.quick_interactions.submit_orchestrated(
                request.task_id, request.optimized_prompt
            )
            LOGGER.info("Prompt optimization returned to dispatcher: request=%s task=%s", request_id, request.task_id)
        except Exception as exc:
            LOGGER.warning("Task orchestration advance unavailable: request=%s reason=%s", request_id, type(exc).__name__)
            # Main submission is deliberately not converted into an optimizer
            # fallback. Quick Interaction owns its persisted Worker identity and
            # reconciles any uncertain IPC result.
        finally:
            guard.release()

    def _read_optimized_result(
        self, request: TaskOrchestrationRequest
    ) -> PromptOptimizationVersions | None:
        if request.optimized_prompt is not None and request.optimized_prompt_en is not None:
            return PromptOptimizationVersions(
                chinese=request.optimized_prompt,
                english=request.optimized_prompt_en,
            )
        try:
            descriptor = self._implementation_resolver(request.implementation_ref)
            if descriptor is None or not callable(descriptor.optimization_result_reader):
                raise _PromptOptimizationFailure("implementation_unavailable")
            with self._optimization_submission_lock:
                if not request.internal_session_id:
                    if request.internal_execution_snapshot is None:
                        raise _PromptOptimizationFailure("stage_unavailable")
                    session_id = self._optimization_use_case.restore_session(
                        request.stage_call_id,
                        request.internal_execution_snapshot.model_dump(),
                    )
                    with self._lock:
                        self._find(request.id).internal_session_id = session_id
                        self._write()
                    request.internal_session_id = session_id
                if not request.internal_task_id:
                    if self._has_other_active_optimizer_task(request.id, request.internal_session_id):
                        return None
                    if not self.quick_interactions.get(request.task_id).orchestration_pending:
                        return None
                    child = self._optimization_use_case.submit(
                        request.internal_session_id,
                        request.optimization_instruction,
                        request.stage_call_id,
                    )
                    with self._lock:
                        self._find(request.id).internal_task_id = child.id
                        self._write()
                    request.internal_task_id = child.id
                    LOGGER.info(
                        "Prompt optimization child linked: request=%s task=%s",
                        request.id,
                        child.id,
                    )
            child = self._optimization_use_case.get(request.internal_task_id)
            if child.status in {"requested", "running"}:
                return None
            if child.status != "succeeded":
                raise _PromptOptimizationFailure("internal_task_failed")
            try:
                return PromptOptimizationVersions.model_validate(
                    descriptor.optimization_result_reader(child.result or "")
                )
            except Exception:
                raise _PromptOptimizationFailure("invalid_optimization_result") from None
        except _PromptOptimizationFailure:
            raise
        except Exception:
            raise _PromptOptimizationFailure("optimization_stage_failed") from None

    def _mark_optimization_fallback(
        self, request_id: str, reason: PromptOptimizationFailureReason
    ) -> None:
        with self._lock:
            request = self._find(request_id)
            if request.optimization_fallback:
                return
            request.optimization_fallback = True
            request.optimization_failure_reason = reason
            request.cursor = len(request.stage_chain)
            request.optimized_prompt = None
            request.optimized_prompt_en = None
            request.optimization_instruction = None
            request.optimization_deadline = None
            # A linked child is now orphaned with respect to the accepted
            # parent; cleanup is best-effort and never gates the main task.
            request.internal_cleanup_complete = not bool(
                request.internal_task_id or request.stage_call_id
            )
            request.updated_at = utc_now()
            self._write()

    def _continue_after_optimization_failure(
        self, request_id: str, parent: QuickInteractionTask
    ) -> None:
        request = self._find(request_id).model_copy(deep=True)
        if parent.prompt is None or not parent.prompt.strip():
            self.quick_interactions.finish_orchestration(
                parent.id,
                "提示词优化未完成，且原始任务正文不可恢复；主任务未提交。",
            )
            return
        warning_available = False
        try:
            self.quick_interactions.set_prompt_optimization_warning(parent.id)
            warning_available = True
        except Exception:
            # The durable fallback marker is authoritative; warning persistence
            # is ancillary and must not block the main task. A later notification
            # or task-state write may persist the in-memory warning.
            LOGGER.warning("Unable to persist prompt optimization warning: request=%s", request_id)
            try:
                warning_available = (
                    self.quick_interactions.get(parent.id).prompt_optimization_warning
                    is not None
                )
            except Exception:
                pass
        if warning_available:
            try:
                self.quick_interactions.notify_prompt_optimization_completed(parent.id)
            except Exception:
                LOGGER.warning("Unable to queue prompt optimization failure notice: request=%s", request_id)
        self.quick_interactions.submit_orchestrated(parent.id, parent.prompt)
        LOGGER.warning(
            "Prompt optimization failed; original task submitted: request=%s reason=%s",
            request_id,
            request.optimization_failure_reason or "optimization_stage_failed",
        )

    def _has_other_active_optimizer_task(self, request_id: str, session_id: str) -> bool:
        """Serialize writes to the shared internal optimizer Session.

        A pending/unknown child keeps the optimizer lane occupied; it must not
        turn a concurrent request into a user-visible submission failure.
        """
        with self._lock:
            candidates = [
                item.model_copy(deep=True)
                for item in self._state.requests
                if item.id != request_id
                and item.internal_session_id == session_id
                and item.plugin_mode == "auto"
                and item.stage_call_id
            ]
        find_for_operation = getattr(self._optimization_use_case, "find_for_operation", None)
        for candidate in candidates:
            task = None
            if candidate.internal_task_id:
                try:
                    task = self._optimization_use_case.get(candidate.internal_task_id)
                except ApiError as exc:
                    if exc.code != "quick_interaction_not_found":
                        return True
                except Exception:
                    return True
            elif callable(find_for_operation):
                try:
                    task = find_for_operation(candidate.stage_call_id)
                except Exception:
                    return True
            if task is not None and task.status in {"requested", "running"}:
                return True
        return False

    def _cleanup_orphaned_internal_task(
        self, request_id: str, task_id: str | None
    ) -> None:
        """Bound cancellation attempts after the parent is already terminal.

        An unresolved child no longer references plugin code: its result cannot
        advance the terminal parent, and Quick Interaction keeps reconciling its
        own Worker task. Do not let an unavailable Worker pin plugin removal or
        terminal idempotency records forever.
        """
        with self._lock:
            request = self._find(request_id)
            if request.internal_cleanup_complete:
                return
            if request.internal_cleanup_attempts >= MAX_INTERNAL_CLEANUP_ATTEMPTS:
                request.internal_cleanup_complete = True
                self._write()
                LOGGER.warning("Internal optimizer cleanup exhausted; child remains under Worker reconciliation: request=%s", request_id)
                return
            request.internal_cleanup_attempts += 1
            attempt = request.internal_cleanup_attempts
            stage_call_id = request.stage_call_id
            self._write()

        try:
            if task_id is None:
                finder = getattr(self._optimization_use_case, "find_for_operation", None)
                if not callable(finder) or stage_call_id is None:
                    raise RuntimeError("optimizer task association is unavailable")
                child = finder(stage_call_id)
                if child is None:
                    with self._lock:
                        request = self._find(request_id)
                        request.internal_cleanup_complete = True
                        self._write()
                    return
                task_id = child.id
                with self._lock:
                    request = self._find(request_id)
                    request.internal_task_id = task_id
                    self._write()
            child = self._optimization_use_case.get(task_id)
            if child.status in {"requested", "running"}:
                self._optimization_use_case.cancel(child.id)
        except ApiError as exc:
            if exc.code != "quick_interaction_not_found":
                LOGGER.warning("Internal optimizer cancellation is unconfirmed: request=%s attempt=%s", request_id, attempt)
                self._settle_internal_cleanup_attempt(request_id)
                return
        except Exception:
            LOGGER.warning("Internal optimizer cancellation is unavailable: request=%s attempt=%s", request_id, attempt)
            self._settle_internal_cleanup_attempt(request_id)
            return

        with self._lock:
            request = self._find(request_id)
            request.internal_cleanup_complete = True
            self._write()

    def _settle_internal_cleanup_attempt(self, request_id: str) -> None:
        with self._lock:
            request = self._find(request_id)
            if request.internal_cleanup_attempts >= MAX_INTERNAL_CLEANUP_ATTEMPTS:
                request.internal_cleanup_complete = True
                LOGGER.warning("Internal optimizer cleanup abandoned after bounded retries: request=%s", request_id)
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
                or (request.internal_task_id and not request.internal_cleanup_complete)
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
            request.optimization_instruction = None
            request.optimized_prompt = None
            request.optimized_prompt_en = None
        request.updated_at = utc_now()
        self._write()

    def _minimize_terminal_records(self) -> bool:
        """Drop no-longer-needed request text from both new and legacy terminal rows."""
        changed = False
        for request in self._state.requests:
            if request.status not in {"completed", "failed"}:
                continue
            if (request.prompt is not None or request.optimization_instruction is not None
                    or request.optimized_prompt is not None or request.optimized_prompt_en is not None):
                request.prompt = None
                request.optimization_instruction = None
                request.optimized_prompt = None
                request.optimized_prompt_en = None
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
