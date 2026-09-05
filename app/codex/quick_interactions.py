from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import threading
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable, Iterator

from app.ai_session.models import AiSession
from app.ai_session.store import AiSessionStoreUnavailable
from app.codex.models import (
    QuickInteractionDeferredRestartContext,
    QuickInteractionErrorSource,
    QuickInteractionOperationContext,
    QuickInteractionOrder,
    QuickInteractionTask,
    QuickInteractionWeixinRoute,
    TASK_SUMMARY_MAX_LENGTH,
    utc_now,
)
from app.core.response import ApiError
from app.core.text_width import truncate_display_width
from app.services.deferred_restart import (
    DeferredRestartCoordinator,
    DeferredRestartOutcome,
    DeferredRestartReadiness,
    DeferredRestartRequest,
)
from app.services.log_reader import redact_log_line
from app.services.operation_log import write_operation
from app.quick_worker import (
    PROTOCOL_VERSION,
    WorkerHealth,
    WorkerRequestNotSent,
    worker_request_sync,
)
from app.quick_worker_tasks import (
    FINAL_STATUSES,
    RuntimeTaskSubmission,
    WorkerTaskSummary,
    WorkerTaskView,
    new_worker_task_id,
)


MAX_RESULT_BYTES = 100_000
MAX_QUICK_INTERACTION_STATE_BYTES = 8 * 1024 * 1024
MAX_STORED_TASKS = 30
MAX_SESSION_TITLE_LENGTH = 48
# The Worker is authoritative; resident reconciliation is recovery work and must
# not continuously contend with interactive IPC submissions.
WORKER_RECONCILE_INTERVAL_SECONDS = 1.0
WORKER_CONNECTION_RETRY_DELAYS = (0.2, 0.5, 1.0, 2.0, 2.0)
UNCERTAIN_SUBMISSION_MAX_ATTEMPTS = 8
UNCERTAIN_SUBMISSION_RETRY_SECONDS = 0.25
LOGGER = logging.getLogger("hub.codex.quick_interactions")
CODEX_QUICK_INTERACTION_INSTRUCTIONS = (
    "[Chub 快速交互交付要求]\n"
    "完成后请面向项目维护者汇报产品结果，重点说明完成效果、页面或交互变化、"
    "验证结果、验收方法及必要风险。默认不要展开代码实现、逐文件清单、函数或"
    "样式细节，除非这些内容会影响验收、安全或兼容性。如果本次任务只是分析或"
    "评审，直接给出结论、影响和建议。\n"
    "如果本次任务需要重启 Chub，只能调用 scripts/chub-web-restart 一次；快速交互"
    "环境会把重启登记为延迟操作。不得绕过该脚本直接调用 launchctl、systemctl 或"
    "其他服务管理命令，也不得重复调用重启脚本。"
)
DEFERRED_RESTART_RESULT_SUFFIX = "本次处理已完成，即将重启 Chub 服务。"
DEFERRED_RESTART_FAILED_SUFFIX = "Chub 重启登记失败，本次不会自动重启。"
ACTIVE_WRITER_ERROR = "Codex Session 正在由其他进程使用，请等待任务结束或停止实时终端。"
VOICE_TRANSCRIPT_MARKER = "[[chub-weixin-voice-transcript]]"
SENSITIVE_SUMMARY_VALUE_PATTERN = re.compile(
    r"(?i)\b(token|secret|password|passwd|webhook)"
    r"(\s*[:=]\s*)(\S+)"
)
SENSITIVE_SUMMARY_REMAINDER_PATTERN = re.compile(
    r"(?i)\b(authorization|cookie)(\s*[:=]\s*).+$"
)
FEISHU_WEBHOOK_PATTERN = re.compile(
    r"https://open\.feishu\.cn/open-apis/bot/v2/hook/[^\s]+",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class WeixinTaskStatusSnapshot:
    running_count: int = 0
    pending_notification_count: int = 0
    failed_notification_count: int = 0
    running_tasks: tuple[tuple[str, str], ...] = ()


class _WorkerSubmissionUncertain(Exception):
    def __init__(self, submission: RuntimeTaskSubmission) -> None:
        super().__init__("Worker submission result is uncertain")
        self.submission = submission


def build_task_summary(
    prompt: str,
    max_chars: int = TASK_SUMMARY_MAX_LENGTH,
    max_width: int | None = None,
) -> str:
    """Build one stable, bounded, non-semantic display summary."""
    lines = []
    for raw_line in prompt.replace(VOICE_TRANSCRIPT_MARKER, "").splitlines():
        line = " ".join(raw_line.split())
        if not line or line in {"[用户需求]", "[Chub 快速交互交付要求]"}:
            continue
        lines.append(line)
    value = lines[0] if lines else "本次微信任务"
    sentence = re.split(r"(?<=[。！？!?])", value, maxsplit=1)[0].strip()
    value = sentence or value
    value = SENSITIVE_SUMMARY_REMAINDER_PATTERN.sub(
        lambda match: f"{match.group(1)}{match.group(2)}[REDACTED]",
        value,
    )
    value = redact_log_line(value, (), max_line_bytes=None)
    value = FEISHU_WEBHOOK_PATTERN.sub("[REDACTED]", value)
    value = SENSITIVE_SUMMARY_VALUE_PATTERN.sub(
        lambda match: f"{match.group(1)}{match.group(2)}[REDACTED]",
        value,
    )
    value = " ".join(value.split()) or "本次微信任务"
    characters = list(value)
    if len(characters) > max_chars:
        value = "".join(characters[: max_chars - 1]).rstrip() + "…"
    if max_width is not None:
        value = truncate_display_width(value, max_width)
    return value


class QuickInteractionManager:
    def __init__(
        self,
        data_file: Path,
        runtime_dir: Path,
        codex_manager,
        completion_notifier: Callable[
            [QuickInteractionTask, QuickInteractionWeixinRoute | None],
            object,
        ]
        | None = None,
        deferred_restart: DeferredRestartCoordinator | None = None,
        *,
        restart_notifier: Callable[
            [
                QuickInteractionTask,
                QuickInteractionWeixinRoute | None,
                DeferredRestartOutcome,
            ],
            object,
        ]
        | None = None,
        timeout_seconds: int = 6 * 60 * 60,
        worker_settings=None,
    ) -> None:
        self.path = data_file.with_name("quick-interactions.json")
        self.codex_manager = codex_manager
        self.completion_notifier = completion_notifier
        self.restart_notifier = restart_notifier
        self.deferred_restart = deferred_restart
        self.timeout_seconds = timeout_seconds
        self.worker_settings = worker_settings
        self._translation_queue_limit = 10
        self._translation_queue_wait_seconds = 1800
        self._lock = threading.RLock()
        self._deferred_restart_transition_lock = threading.RLock()
        self._tasks: dict[str, QuickInteractionTask] = {}
        self._running_sessions: set[str] = set()
        self._active_task_ids: set[str] = set()
        self._cancelled_task_ids: set[str] = set()
        self._task_done_events: dict[str, threading.Event] = {}
        self._session_locks: dict[str, threading.RLock] = {}
        self._operations: dict[str, tuple[str, str]] = {}
        self._notification_routes: dict[str, QuickInteractionWeixinRoute] = {}
        self._deferred_restart_contexts: dict[
            str,
            QuickInteractionDeferredRestartContext,
        ] = {}
        self._operation_contexts: dict[str, QuickInteractionOperationContext] = {}
        self._worker_delivery_confirmed: set[str] = set()
        self._submitting_task_ids: set[str] = set()
        self._native_claim_restore_errors: dict[str, str] = {}
        self._pending_native_claim_clears: set[tuple[str, str]] = set()
        self._system_upgrade_reset = False
        self._local_state_error: str | None = None
        self._recovery_ready = False
        self._recovery_error: str | None = None
        self._closed = threading.Event()
        self._resident_reconciler_started = False
        self._reconciler_stop = threading.Event()
        self._reconciler_thread: threading.Thread | None = None
        self._untracked_worker_sessions: set[str] = set()
        self._recovery_ready_handler: Callable[[], object] | None = None
        self._maintenance_window_checker: Callable[[], bool] | None = None
        self._task_finished_handler: Callable[[QuickInteractionTask], object] | None = (
            None
        )
        self.restart_request_dir = runtime_dir / "restart-requests"
        recovered_tasks = self._load()
        if recovered_tasks and self._local_state_error is None:
            self._write()
        self.restart_request_dir.mkdir(parents=True, exist_ok=True)
        os.chmod(self.restart_request_dir, 0o700)

    def _load(self) -> bool:
        try:
            if self.path.is_symlink():
                raise OSError("Web quick interaction state must not be a symlink")
            with self.path.open("rb") as state_file:
                content = state_file.read(MAX_QUICK_INTERACTION_STATE_BYTES + 1)
            if len(content) > MAX_QUICK_INTERACTION_STATE_BYTES:
                raise OSError("Web quick interaction state exceeds its fixed limit")
            payload = json.loads(content.decode("utf-8"))
        except FileNotFoundError:
            return False
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            self._local_state_error = "Web quick interaction state is unreadable"
            return False
        if not isinstance(payload, list):
            self._local_state_error = "Web quick interaction state has an invalid root"
            return False
        recovered_tasks = False
        seen_task_ids: set[str] = set()
        seen_worker_task_ids: set[str] = set()
        for item in payload:
            if not isinstance(item, dict):
                self._local_state_error = "Web quick interaction state contains an invalid entry"
                continue
            task_payload = dict(item)
            removed_session_display_snapshot = False
            for field in ("weixin_session_slot", "weixin_session_title"):
                if field in task_payload:
                    task_payload.pop(field, None)
                    removed_session_display_snapshot = True
            # Older records may contain the removed pin state.
            removed_legacy_pin_state = "pinned_at" in task_payload
            task_payload.pop("pinned_at", None)
            route_payload = task_payload.pop("_notification_route", None)
            restart_context_payload = task_payload.pop(
                "_deferred_restart_context",
                None,
            )
            operation_context_payload = task_payload.pop(
                "_operation_context",
                None,
            )
            worker_delivery_confirmed = task_payload.pop(
                "_worker_delivery_confirmed",
                False,
            )
            # Older interrupted writes may leave this optional marker as JSON
            # null. It has the same safe meaning as an absent marker: delivery
            # is not confirmed and must be reconciled with the Worker. Do not
            # turn that recoverable per-task state into a permanent global
            # Quick Worker write gate.
            if worker_delivery_confirmed is None:
                worker_delivery_confirmed = False
                recovered_tasks = True
            try:
                task = QuickInteractionTask.model_validate(task_payload)
            except ValueError:
                self._local_state_error = "Web quick interaction state contains an invalid task"
                continue
            if task.id in seen_task_ids or (
                task.worker_task_id is not None
                and task.worker_task_id in seen_worker_task_ids
            ):
                self._local_state_error = "Web quick interaction state contains duplicate task identities"
                continue
            seen_task_ids.add(task.id)
            if task.worker_task_id is not None:
                seen_worker_task_ids.add(task.worker_task_id)
            if task.notification_route == "weixin-task":
                try:
                    route = QuickInteractionWeixinRoute.model_validate(route_payload)
                except ValueError:
                    route = None
                if route is not None:
                    self._notification_routes[task.id] = route
                elif worker_delivery_confirmed is not True:
                    self._local_state_error = (
                        "Recoverable Weixin quick interaction has no valid delivery route"
                    )
            try:
                restart_context = QuickInteractionDeferredRestartContext.model_validate(
                    restart_context_payload
                )
            except ValueError:
                restart_context = None
            if restart_context is not None:
                self._deferred_restart_contexts[task.id] = restart_context
            try:
                operation_context = QuickInteractionOperationContext.model_validate(
                    operation_context_payload
                )
            except ValueError:
                operation_context = None
            if operation_context is not None:
                self._operation_contexts[task.id] = operation_context
                self._operations[task.id] = (
                    operation_context.operation_id,
                    operation_context.source_ip,
                )
            elif (
                task.worker_task_id is not None
                and worker_delivery_confirmed is not True
            ):
                self._local_state_error = (
                    "Recoverable quick interaction has no valid operation context"
                )
            if not isinstance(worker_delivery_confirmed, bool):
                self._local_state_error = (
                    "Web quick interaction state has an invalid delivery marker"
                )
            if worker_delivery_confirmed is True:
                self._worker_delivery_confirmed.add(task.id)
            if task.status in {"requested", "running"}:
                recovered_tasks = True
                if task.worker_task_id is not None:
                    self._running_sessions.add(task.session_id)
                    self._active_task_ids.add(task.id)
                    self._task_done_events[task.id] = threading.Event()
                else:
                    self._local_state_error = (
                        "Active Web quick interaction has no Worker identity"
                    )
            if removed_legacy_pin_state or removed_session_display_snapshot:
                recovered_tasks = True
            if task.notification_status == "sending":
                recovered_tasks = True
                if task.notification_route == "weixin-task":
                    task.notification_status = "failed"
                    task.notification_error = "服务重启时微信通知未完成。"
                else:
                    task.notification_status = "skipped"
                    task.notification_error = "页面任务结果仅在 Chub 快速交互页面展示。"
                task.notification_updated_at = utc_now()
            elif (
                task.notification_route != "weixin-task"
                and task.notification_status == "pending"
            ):
                recovered_tasks = True
                task.notification_status = "skipped"
                task.notification_error = "页面任务结果仅在 Chub 快速交互页面展示。"
                task.notification_updated_at = utc_now()
            if task.deferred_restart_notification_status == "sending":
                recovered_tasks = True
                task.deferred_restart_notification_status = "failed"
                task.deferred_restart_notification_error = (
                    "服务重启时微信重启通知未完成。"
                )
                task.deferred_restart_notification_updated_at = utc_now()
            self._tasks[task.id] = task
        for task in self._tasks.values():
            if task.status in FINAL_STATUSES and task.worker_task_id is not None:
                self._clear_quick_native_claim_safely(
                    task.session_id,
                    task.worker_task_id,
                )
                continue
            if task.status not in {"requested", "running"}:
                continue
            if task.worker_task_id is None:
                continue
            try:
                # The Chub Session state is reloaded independently from the
                # Web task list. Restore the durable claimant before worker
                # reconciliation so a valid result received after a Web
                # restart is not mistaken for a stale task.
                self.codex_manager.register_quick_native_claim(
                    task.session_id,
                    task.worker_task_id,
                )
            except (ApiError, AiSessionStoreUnavailable) as exc:
                # A stale claim belongs to this task and must not make unrelated
                # Sessions unavailable. Keep reconciling the Worker task, but
                # never apply its native identity or successful result.
                error_code = (
                    exc.code
                    if isinstance(exc, ApiError)
                    else "ai_session_store_unavailable"
                )
                self._native_claim_restore_errors[task.id] = error_code
                LOGGER.warning(
                    "Unable to restore Quick Worker native Session claim for task %s: %s",
                    task.id,
                    error_code,
                )
        return recovered_tasks

    def submit(
        self,
        session_id: str,
        prompt: str,
        *,
        operation_id: str,
        source_ip: str,
        notification_route: QuickInteractionWeixinRoute | None = None,
        kind: str = "standard",
        translation_original: str | None = None,
        model: str | None = None,
        reasoning_effort: str | None = None,
        suppress_completion_notification: bool = False,
        summary_max_chars: int = TASK_SUMMARY_MAX_LENGTH,
        summary_max_width: int | None = None,
    ) -> QuickInteractionTask:
        self._require_worker_recovery()
        queued_translation = kind == "translation"
        with self._session_lock(session_id):
            session = self.codex_manager.get_session(session_id)
            if session.status == "error" and not queued_translation:
                raise ApiError(
                    409,
                    "quick_interaction_session_error",
                    "会话当前异常，请先通过实时终端重试。",
                )
            if session.activity == "working" and not queued_translation:
                raise ApiError(
                    409,
                    "quick_interaction_terminal_working",
                    "实时终端正在执行，请等待当前任务结束。",
                )
            if (
                session.status == "running"
                and session.activity != "idle"
                and not queued_translation
            ):
                raise ApiError(
                    409,
                    "quick_interaction_terminal_active",
                    "当前实时终端状态不允许快速交互。",
                )
            if session.permission_mode == "ask":
                raise ApiError(
                    409,
                    "quick_interaction_ask_not_supported",
                    "快速交互不支持 Ask for approval，请选择只读、自动审核或完全访问权限。",
                )
            with self._lock:
                if self._any_running(session_id) and not queued_translation:
                    raise ApiError(
                        409,
                        "quick_interaction_in_progress",
                        "该会话已有快速交互任务正在执行。",
                    )
            if (
                not queued_translation
                and self.codex_manager.has_active_writer(session.native_session_id)
            ):
                raise ApiError(
                    409,
                    "quick_interaction_writer_active",
                    ACTIVE_WRITER_ERROR,
                )
            if isinstance(session, AiSession):
                self.codex_manager.prepare_quick_interaction(session.runtime_id)
            else:
                self.codex_manager.prepare_quick_interaction()
            if not session.native_session_id:
                self.codex_manager.set_initial_quick_interaction_title(
                    session.id,
                    self._session_title(prompt),
                )
            restart_sensitive = (
                session.workspace_id == "chub"
                and session.permission_mode != "read-only"
            )
            with self._lock:
                if self._any_running(session_id) and not queued_translation:
                    raise ApiError(409, "quick_interaction_in_progress", "该会话已有快速交互任务正在执行。")
                persisted_prompt = (
                    translation_original
                    if kind == "translation" and translation_original is not None
                    else prompt
                )
                task = QuickInteractionTask(
                    id=str(uuid.uuid4()),
                    worker_task_id=new_worker_task_id(),
                    session_id=session_id,
                    prompt=persisted_prompt,
                    summary=build_task_summary(
                        persisted_prompt,
                        max_chars=summary_max_chars,
                        max_width=summary_max_width,
                    ),
                    kind=kind,
                    translation_original=translation_original,
                    permission_mode=(
                        "read-only" if kind == "translation" else session.permission_mode
                    ),
                    model=model if kind == "translation" else session.model,
                    reasoning_effort=(
                        reasoning_effort
                        if kind == "translation"
                        else session.reasoning_effort
                    ),
                    restart_sensitive=restart_sensitive,
                    status="requested",
                    notification_status=(
                        "skipped"
                        if suppress_completion_notification
                        or notification_route is None
                        else None
                    ),
                    notification_error=(
                        "Completion is handled by the translation workflow."
                        if suppress_completion_notification
                        else (
                            "页面任务结果仅在 Chub 快速交互页面展示。"
                            if notification_route is None
                            else None
                        )
                    ),
                    notification_updated_at=(
                        utc_now()
                        if suppress_completion_notification
                        or notification_route is None
                        else None
                    ),
                    notification_route=(
                        "weixin-task" if notification_route is not None else "default"
                    ),
                    created_at=utc_now(),
                    updated_at=utc_now(),
                )
                self._tasks[task.id] = task
                self._running_sessions.add(session_id)
                self._active_task_ids.add(task.id)
                self._task_done_events[task.id] = threading.Event()
                self._submitting_task_ids.add(task.id)
                self._operations[task.id] = (operation_id, source_ip)
                self._operation_contexts[task.id] = QuickInteractionOperationContext(
                    operation_id=operation_id,
                    source_ip=source_ip,
                )
                if notification_route is not None:
                    self._notification_routes[task.id] = notification_route
                try:
                    self._write()
                except OSError:
                    self._tasks.pop(task.id, None)
                    self._running_sessions.discard(session_id)
                    self._active_task_ids.discard(task.id)
                    self._task_done_events.pop(task.id, None)
                    self._submitting_task_ids.discard(task.id)
                    self._operations.pop(task.id, None)
                    self._operation_contexts.pop(task.id, None)
                    self._notification_routes.pop(task.id, None)
                    raise
        if task.worker_task_id is None:
            raise RuntimeError("Worker task identity is unavailable")
        self._log_status(task.id, "requested", session.id)
        try:
            self.codex_manager.register_quick_native_claim(
                session.id,
                task.worker_task_id,
            )
            self._submit_worker_task(task, session, prompt)
        except _WorkerSubmissionUncertain as exc:
            with self._lock:
                task.submission_verifying = True
                task.error = (
                    "Quick Worker 正在核验本次提交；请勿重复提交。"
                )
                task.updated_at = utc_now()
                self._write()
            self._start_uncertain_submission_reconciler(
                task,
                session,
                exc.submission,
            )
            # The task is durably accepted by Chub, but Worker acceptance is
            # still being verified.  Returning it prevents every caller from
            # presenting a false "not submitted" result and retrying with a
            # second task identity.
            return task
        except Exception as exc:
            self._clear_quick_native_claim_safely(
                session.id,
                task.worker_task_id,
            )
            self._log_status(task.id, "failed", session.id)
            detail = self._worker_exception_detail(exc)
            with self._lock:
                self._tasks.pop(task.id, None)
                self._running_sessions.discard(session_id)
                self._active_task_ids.discard(task.id)
                self._task_done_events.pop(task.id, None)
                self._submitting_task_ids.discard(task.id)
                self._notification_routes.pop(task.id, None)
                self._operations.pop(task.id, None)
                self._operation_contexts.pop(task.id, None)
                self._write()
            if self.deferred_restart is not None:
                self.deferred_restart.maybe_schedule()
            raise ApiError(
                503,
                "quick_worker_unavailable",
                f"Chub Quick Worker submission error: {detail}",
            ) from exc
        else:
            with self._lock:
                self._submitting_task_ids.discard(task.id)
        try:
            self._start_worker_observer(task, session, prompt)
        except RuntimeError as observer_error:
            observer_detail = self._worker_exception_detail(observer_error)
            if task.worker_task_id:
                try:
                    cancelled = self._worker_call(
                        "task_cancel",
                        task_id=task.worker_task_id,
                        timeout_seconds=5,
                    )
                    if cancelled.get("success") is not True:
                        raise OSError(self._worker_error(cancelled))
                except (OSError, RuntimeError) as cancel_error:
                    cancel_detail = self._worker_exception_detail(cancel_error)
                    with self._lock:
                        task.status = "failed"
                        task.error = (
                            "Chub Worker observer error: "
                            f"{observer_detail}; cancellation error: {cancel_detail}"
                        )
                        task.error_source = "chub"
                        task.updated_at = utc_now()
                        self._write()
                    if self.deferred_restart is not None:
                        self.deferred_restart.maybe_schedule()
                    raise ApiError(
                        503,
                        "quick_worker_observer_unavailable",
                        f"Chub Worker observer error: {observer_detail}; "
                        f"cancellation error: {cancel_detail}",
                    ) from observer_error
            with self._lock:
                self._tasks.pop(task.id, None)
                self._running_sessions.discard(session_id)
                self._active_task_ids.discard(task.id)
                self._task_done_events.pop(task.id, None)
                self._notification_routes.pop(task.id, None)
                self._operation_contexts.pop(task.id, None)
                self._write()
            self._log_status(task.id, "failed", session.id)
            with self._lock:
                self._operations.pop(task.id, None)
            if self.deferred_restart is not None:
                self.deferred_restart.maybe_schedule()
            raise ApiError(
                503,
                "quick_interaction_start_failed",
                f"Chub Worker observer error: {observer_detail}",
            ) from observer_error
        return task

    @property
    def recovery_ready(self) -> bool:
        with self._lock:
            return self._recovery_ready

    @property
    def recovery_error(self) -> str | None:
        with self._lock:
            return self._recovery_error

    def set_recovery_ready_handler(self, handler: Callable[[], object]) -> None:
        self._recovery_ready_handler = handler

    def set_task_finished_handler(
        self,
        handler: Callable[[QuickInteractionTask], object],
    ) -> None:
        self._task_finished_handler = handler

    @contextmanager
    def session_creation_guard(self, session_mode: str = "quick") -> Iterator[None]:
        if session_mode == "quick":
            self.require_quick_session_creation()
        yield

    def quick_session_creation_availability(self) -> tuple[bool, str | None]:
        try:
            self.require_quick_session_creation()
        except ApiError as exc:
            return False, exc.message
        return True, None

    def require_quick_session_creation(self) -> None:
        self._require_worker_recovery()
        try:
            payload = self._worker_call("health", timeout_seconds=1)
            if payload.get("success") is not True:
                raise OSError("Quick Worker health request failed")
            health = WorkerHealth.model_validate(payload.get("data"))
            if health.status != "ready":
                raise OSError("Quick Worker is not ready")
        except (OSError, ValueError) as exc:
            raise ApiError(
                503,
                "quick_worker_unavailable",
                "Quick Worker 当前不可用，无法创建快速交互 Session。",
            ) from exc

    def update_session_model(
        self,
        session_id: str,
        model: str,
        reasoning_effort: str,
    ):
        """Compatibility wrapper for updating a quick Session model."""
        with self._session_lock(session_id):
            with self._lock:
                if self._any_running(session_id):
                    raise ApiError(
                        409,
                        "codex_session_model_update_busy",
                        "Session 正在执行，请等待任务结束后重试。",
                    )
            return self.codex_manager.update_quick_session_model(
                session_id,
                model,
                reasoning_effort,
            )

    def update_session_configuration(
        self,
        session_id: str,
        permission_mode: str,
        model: str | None,
        reasoning_effort: str | None,
    ):
        """Serialize a persistent future-task configuration update."""
        with self._session_lock(session_id):
            with self._lock:
                if self._any_running(session_id):
                    raise ApiError(
                        409,
                        "codex_session_configuration_update_busy",
                        "Session 正在执行，请等待任务结束后重试。",
                    )
            return self.codex_manager.update_session_configuration(
                session_id,
                permission_mode,
                model,
                reasoning_effort,
            )

    def start_worker_reconciliation(self) -> None:
        with self._lock:
            if self._closed.is_set():
                return
            if self._resident_reconciler_started:
                return
            self._resident_reconciler_started = True
            self._reconciler_stop.clear()
        try:
            self._reconcile_worker_once(initial=True)
        except Exception as exc:
            self._record_reconciliation_failure(exc)
        thread = threading.Thread(
            target=self._run_resident_reconciler,
            daemon=True,
            name="chub-quick-worker-reconciler",
        )
        with self._lock:
            self._reconciler_thread = thread
        try:
            thread.start()
        except RuntimeError as exc:
            with self._lock:
                self._resident_reconciler_started = False
                self._reconciler_thread = None
            self._record_reconciliation_failure(exc)

    def set_maintenance_window_checker(self, checker: Callable[[], bool]) -> None:
        self._maintenance_window_checker = checker

    def _run_resident_reconciler(self) -> None:
        while not self._reconciler_stop.wait(WORKER_RECONCILE_INTERVAL_SECONDS):
            try:
                self._reconcile_worker_once(initial=False)
            except Exception as exc:
                self._record_reconciliation_failure(exc)

    def _record_reconciliation_failure(self, exc: Exception) -> None:
        with self._lock:
            was_ready = self._recovery_ready
            self._recovery_ready = False
            self._recovery_error = str(exc)[:500] or type(exc).__name__
        if was_ready:
            if self._maintenance_window_active():
                LOGGER.info(
                    "Quick Worker reconciliation is temporarily unavailable during "
                    "maintenance; Session writes remain blocked"
                )
                return
            LOGGER.warning(
                "Quick Worker reconciliation became unavailable; Session writes are blocked",
                exc_info=True,
            )

    def _maintenance_window_active(self) -> bool:
        checker = self._maintenance_window_checker
        if checker is None:
            return False
        try:
            return bool(checker())
        except Exception:
            return False

    def _reconcile_worker_once(self, *, initial: bool) -> None:
        if self._closed.is_set():
            return
        if self._local_state_error is not None:
            raise OSError(self._local_state_error)
        self._retry_pending_native_claim_clears()
        listed = self._worker_call("task_list", limit=100, recovery_only=True)
        if listed.get("success") is not True:
            raise OSError(self._worker_error(listed))
        data = listed.get("data")
        if not isinstance(data, dict) or not isinstance(data.get("tasks"), list):
            raise OSError("Worker returned invalid recovery task data")
        recovery_worker_tasks: dict[str, WorkerTaskSummary] = {}
        for payload in data["tasks"]:
            summary = WorkerTaskSummary.model_validate_json(
                json.dumps(payload, ensure_ascii=False)
            )
            if summary.task_id in recovery_worker_tasks:
                raise OSError("Worker returned duplicate recovery task identities")
            recovery_worker_tasks[summary.task_id] = summary
        with self._lock:
            local_by_worker_id = {
                task.worker_task_id: task.id
                for task in self._tasks.values()
                if task.worker_task_id is not None
            }
            candidates = [
                task.id
                for task in self._tasks.values()
                if task.worker_task_id is not None
                and task.id not in self._submitting_task_ids
                and (
                    task.status in {"requested", "running"}
                    or task.id not in self._worker_delivery_confirmed
                    or task.worker_task_id in recovery_worker_tasks
                )
            ]
        unknown_recovery = [
            summary
            for worker_task_id, summary in recovery_worker_tasks.items()
            if summary.session_id is not None and worker_task_id not in local_by_worker_id
        ]
        with self._lock:
            self._untracked_worker_sessions.update(
                summary.session_id
                for summary in unknown_recovery
                if summary.session_id is not None
            )
        if unknown_recovery:
            raise OSError(
                "Worker has an active or undelivered Codex task without Web delivery metadata"
            )
        for task_id in candidates:
            if self._closed.is_set():
                return
            self._reconcile_worker_task(task_id)
        with self._lock:
            became_ready = not self._recovery_ready
        self.resume_pending_completion_notifications()
        self.resume_pending_deferred_restart_notifications()
        if became_ready and self._recovery_ready_handler is not None:
            self._recovery_ready_handler()
        with self._lock:
            self._untracked_worker_sessions.clear()
            self._recovery_ready = True
            self._recovery_error = None
        if self.deferred_restart is not None:
            self.deferred_restart.maybe_schedule()

    def _require_worker_recovery(self) -> None:
        with self._lock:
            unavailable = not self._recovery_ready
        if unavailable:
            raise ApiError(
                503,
                "quick_worker_recovery_unavailable",
                "Quick Worker 状态正在恢复或暂不可用，Session 写操作已暂停。",
            )

    def _reconcile_worker_task(self, task_id: str) -> None:
        with self._lock:
            if self._system_upgrade_reset:
                return
            task = self._tasks.get(task_id)
            if task is None or task.worker_task_id is None:
                return
            worker_task_id = task.worker_task_id
            claim_restore_error = self._native_claim_restore_errors.get(task.id)
        payload = self._worker_call("task_get", task_id=worker_task_id)
        if payload.get("success") is not True:
            error = payload.get("error")
            code = error.get("code") if isinstance(error, dict) else None
            if code == "worker_task_not_found":
                if task.submission_verifying:
                    # A bounded foreground probe did not settle the outcome.
                    # Once the Worker is reachable, retry only this immutable
                    # task identity; a successful response is the acceptance
                    # proof, while an explicit rejection is the only safe
                    # reason to release the Session.
                    try:
                        session = self.codex_manager.get_session(task.session_id)
                        if not task.prompt:
                            raise OSError("Verifying Worker task has no prompt")
                        submission = self._worker_submission(task, session, task.prompt)
                        submitted = self._worker_call(
                            "runtime_task_submit",
                            task=submission.model_dump(mode="json"),
                        )
                    except OSError:
                        raise
                    if submitted.get("success") is not True:
                        submitted_error = submitted.get("error")
                        submitted_code = (
                            submitted_error.get("code")
                            if isinstance(submitted_error, dict)
                            else None
                        )
                        if submitted_code in {
                            "worker_action_unavailable",
                            "worker_protocol_incompatible",
                            "worker_capacity_reached",
                            "worker_queue_capacity_reached",
                            "worker_workspace_unavailable",
                            "runtime_unavailable",
                        }:
                            self._finish_rejected_submission(
                                task_id,
                                session,
                                worker_task_id,
                                "Quick Worker 未接受任务，且未在 Web 内回退执行。",
                            )
                            return
                        raise OSError(self._worker_error(submitted))
                    try:
                        self._validate_worker_submission_response(submitted, submission)
                    except (OSError, ValueError) as exc:
                        raise OSError("Worker returned mismatched submission identity") from exc
                    with self._lock:
                        current = self._tasks.get(task_id)
                        if current is None:
                            return
                        current.submission_verifying = False
                        current.error = None
                        current.updated_at = utc_now()
                        self._write()
                    return
                with self._lock:
                    current = self._tasks.get(task_id)
                    if current is None:
                        return
                    was_active = current.status in {"requested", "running"}
                if was_active:
                    self._finish(
                        task_id,
                        "failed",
                        "Quick Worker 未接受该任务，任务没有执行。",
                        error_source="chub",
                    )
                    with self._lock:
                        current = self._tasks[task_id]
                        self._active_task_ids.discard(task_id)
                        self._running_sessions.discard(current.session_id)
                        done = self._task_done_events.pop(task_id, None)
                        if done is not None:
                            done.set()
                        self._write()
                with self._lock:
                    self._worker_delivery_confirmed.add(task_id)
                    self._write()
                if was_active:
                    self.codex_manager.set_activity(
                        current.session_id,
                        "idle",
                        "none",
                        updated_at=current.updated_at,
                    )
                    self._log_status(task_id, "failed", current.session_id)
                return
            raise OSError(self._worker_error(payload))
        data = payload.get("data")
        if not isinstance(data, dict):
            raise OSError("Worker returned invalid task data")
        snapshot = WorkerTaskView.model_validate_json(
            json.dumps(data.get("task"), ensure_ascii=False)
        )
        if (
            snapshot.task_id != worker_task_id
            or snapshot.restart_sensitive != task.restart_sensitive
        ):
            raise OSError("Worker returned mismatched task metadata")
        try:
            session = self.codex_manager.get_session(task.session_id)
        except Exception as exc:
            raise OSError("Worker task Session is unavailable") from exc
        if snapshot.native_session_id and claim_restore_error is None:
            try:
                if task.kind == "translation":
                    self.codex_manager.bind_quick_interaction_native_session(
                        task.session_id,
                        snapshot.native_session_id,
                    )
                else:
                    if snapshot.execution_id is None:
                        raise OSError("Worker native Session is missing execution identity")
                    self.codex_manager.bind_quick_interaction_native_session(
                        task.session_id,
                        snapshot.native_session_id,
                        worker_task_id=worker_task_id,
                        execution_id=snapshot.execution_id,
                    )
            except ApiError as exc:
                if (
                    exc.code != "quick_interaction_native_session_conflict"
                    or snapshot.status not in FINAL_STATUSES
                ):
                    raise
                snapshot = snapshot.model_copy(
                    update={
                        "status": "failed",
                        "result": None,
                        "error": exc.message,
                        "error_source": "chub",
                    }
                )
        if claim_restore_error is not None and snapshot.status in FINAL_STATUSES:
            snapshot = snapshot.model_copy(
                update={
                    "status": "failed",
                    "result": None,
                    "error": (
                        "Quick Worker native Session claim could not be restored; "
                        "the task result was not applied."
                    ),
                    "error_source": "chub",
                }
            )
        if snapshot.status not in FINAL_STATUSES:
            log_started = False
            with self._lock:
                current = self._tasks.get(task_id)
                if current is None:
                    return
                desired = (
                    "running"
                    if snapshot.status in {"starting", "running"}
                    else "requested"
                )
                changed = current.status != desired or current.error is not None
                log_started = current.status == "requested" and desired == "running"
                current.status = desired
                current.error = None
                current.error_source = None
                current.updated_at = max(current.updated_at, snapshot.updated_at)
                self._active_task_ids.add(task_id)
                self._running_sessions.add(current.session_id)
                self._task_done_events.setdefault(task_id, threading.Event())
                if changed:
                    self._write()
            if log_started or desired == "running":
                self._log_status(task_id, "started", task.session_id)
            self.codex_manager.set_activity(task.session_id, "working", "quick")
            return
        with self._lock:
            current = self._tasks.get(task_id)
            if current is None:
                return
            already_final = current.status not in {"requested", "running"}
        if not already_final:
            self._finish_from_worker_snapshot(task_id, current, snapshot)
        with self._lock:
            current = self._tasks.get(task_id)
            if current is None:
                return
            self._active_task_ids.discard(task_id)
            remaining_for_session = any(
                active_id in self._active_task_ids
                and active_task.session_id == current.session_id
                for active_id, active_task in self._tasks.items()
            )
            if not remaining_for_session:
                self._running_sessions.discard(current.session_id)
            done = self._task_done_events.pop(task_id, None)
            if done is not None:
                done.set()
            self._write()
        if not remaining_for_session:
            self.codex_manager.set_activity(
                current.session_id,
                "idle",
                "none",
                updated_at=current.updated_at,
            )
        self._log_status(task_id, current.status, current.session_id)
        acknowledged = self._worker_call(
            "task_acknowledge",
            task_id=worker_task_id,
        )
        if acknowledged.get("success") is not True:
            raise OSError(self._worker_error(acknowledged))
        with self._lock:
            self._worker_delivery_confirmed.add(task_id)
            self._native_claim_restore_errors.pop(task_id, None)
            self._write()

    def resume_pending_completion_notifications(self) -> None:
        with self._lock:
            pending = [
                (
                    task.id,
                    self._operations.get(task.id)
                    or (uuid.uuid4().hex, "unknown"),
                )
                for task in self._tasks.values()
                if (
                    task.notification_status == "pending"
                    and task.notification_route == "weixin-task"
                )
            ]
        for task_id, operation in pending:
            try:
                threading.Thread(
                    target=self._deliver_completion_notification,
                    args=(task_id, operation),
                    daemon=True,
                    name=f"chub-completion-notification-{task_id[:8]}",
                ).start()
            except RuntimeError:
                LOGGER.warning("Unable to resume completion notification thread")

    def _start_worker_observer(
        self,
        task: QuickInteractionTask,
        session: AiSession,
        prompt: str,
    ) -> None:
        with self._lock:
            if self._closed.is_set() or self._resident_reconciler_started:
                return
        threading.Thread(
            target=self._run_worker,
            args=(task.id, session, prompt),
            daemon=True,
        ).start()

    def _start_uncertain_submission_reconciler(
        self,
        task: QuickInteractionTask,
        session: AiSession,
        submission: RuntimeTaskSubmission,
    ) -> None:
        try:
            threading.Thread(
                target=self._reconcile_uncertain_submission,
                args=(task.id, session, submission),
                daemon=True,
            ).start()
        except RuntimeError:
            LOGGER.critical(
                "Unable to start uncertain Worker submission reconciler; "
                "Session remains fail-closed until Web restart"
            )

    def _reconcile_uncertain_submission(
        self,
        task_id: str,
        session: AiSession,
        submission: RuntimeTaskSubmission,
    ) -> None:
        with self._lock:
            task = self._tasks.get(task_id)
            worker_task_id = task.worker_task_id if task is not None else None
        if worker_task_id is None:
            with self._lock:
                self._submitting_task_ids.discard(task_id)
            return
        message: str | None = None
        for _attempt in range(UNCERTAIN_SUBMISSION_MAX_ATTEMPTS):
            try:
                submitted = self._worker_call(
                    "runtime_task_submit",
                    task=submission.model_dump(mode="json"),
                )
            except OSError:
                time.sleep(UNCERTAIN_SUBMISSION_RETRY_SECONDS)
                continue
            if submitted.get("success") is not True:
                error = submitted.get("error")
                code = error.get("code") if isinstance(error, dict) else None
                if code in {
                    "worker_action_unavailable",
                    "worker_protocol_incompatible",
                    "worker_capacity_reached",
                    "worker_queue_capacity_reached",
                    "worker_workspace_unavailable",
                    "runtime_unavailable",
                }:
                    message = "Quick Worker 未接受任务，且未在 Web 内回退执行。"
                    break
                time.sleep(UNCERTAIN_SUBMISSION_RETRY_SECONDS)
                continue
            try:
                self._validate_worker_submission_response(submitted, submission)
            except (OSError, ValueError):
                # The request was accepted, but its identity is not safe to adopt.
                # Keep the Session fail-closed and continue the idempotent probe.
                time.sleep(UNCERTAIN_SUBMISSION_RETRY_SECONDS)
                continue
            with self._lock:
                current = self._tasks.get(task_id)
                if current is None:
                    self._submitting_task_ids.discard(task_id)
                    return
                current.submission_verifying = False
                current.error = None
                current.updated_at = utc_now()
                self._submitting_task_ids.discard(task_id)
                self._write()
            # A matching Worker task is the acceptance proof.  Never cancel it
            # merely because its original IPC response was delayed or lost.
            self._start_worker_observer(
                current,
                session,
                current.prompt or submission.prompt,
            )
            return
        if message is None:
            with self._lock:
                current = self._tasks.get(task_id)
                if current is None:
                    self._submitting_task_ids.discard(task_id)
                    return
                # Do not leave a daemon retry loop holding the Session forever.
                # The persisted task is picked up by normal Worker reconciliation,
                # which can safely query or retry this exact Worker task identity.
                current.submission_verifying = True
                current.error = (
                    "Quick Worker 尚未确认本次提交；Chub 将继续后台核验，"
                    "请勿重复提交。"
                )
                current.updated_at = utc_now()
                self._submitting_task_ids.discard(task_id)
                self._write()
            return
        self._finish_rejected_submission(task_id, session, worker_task_id, message)

    def _finish_rejected_submission(
        self,
        task_id: str,
        session: AiSession,
        worker_task_id: str,
        message: str,
    ) -> None:
        self._clear_quick_native_claim_safely(session.id, worker_task_id)
        with self._lock:
            current = self._tasks.get(task_id)
            if current is None:
                self._submitting_task_ids.discard(task_id)
                return
            current.status = "failed"
            current.submission_verifying = False
            current.error = message
            current.updated_at = utc_now()
            self._active_task_ids.discard(task_id)
            remaining_for_session = any(
                active_id in self._active_task_ids
                and active_task.session_id == session.id
                for active_id, active_task in self._tasks.items()
            )
            if not remaining_for_session:
                self._running_sessions.discard(session.id)
            done = self._task_done_events.pop(task_id, None)
            if done is not None:
                done.set()
            self._submitting_task_ids.discard(task_id)
            self._write()
        self._log_status(task_id, "failed", session.id)
        with self._lock:
            self._operations.pop(task_id, None)
        if not remaining_for_session:
            try:
                self.codex_manager.set_activity(
                    session.id,
                    "idle",
                    "none",
                    updated_at=current.updated_at,
                )
            except Exception:
                LOGGER.warning(
                    "Unable to clear rejected Worker submission activity",
                    exc_info=True,
                )
        if self.deferred_restart is not None:
            self.deferred_restart.maybe_schedule()

    def weixin_session_ids(self) -> set[str]:
        with self._lock:
            return {
                task.session_id
                for task in self._tasks.values()
                if task.notification_route == "weixin-task"
            }

    def _any_running(self, session_id: str) -> bool:
        return (
            session_id in self._untracked_worker_sessions
            or session_id in self._running_sessions
            or any(
                task_id in self._active_task_ids and task.session_id == session_id
                for task_id, task in self._tasks.items()
            )
        )

    def _session_lock(self, session_id: str) -> threading.RLock:
        with self._lock:
            return self._session_locks.setdefault(session_id, threading.RLock())

    @contextmanager
    def session_operation_guard(self, session_id: str) -> Iterator[None]:
        with self._session_lock(session_id):
            self._require_worker_recovery()
            with self._lock:
                if self._any_running(session_id):
                    raise ApiError(
                        409,
                        "quick_interaction_in_progress",
                        "该会话正在执行快速交互，请等待任务结束。",
                    )
            yield

    @contextmanager
    def destructive_operation_guard(self, session_id: str) -> Iterator[None]:
        with self._session_lock(session_id):
            with self._lock:
                if not self._recovery_ready:
                    if self._local_state_error is not None:
                        raise ApiError(
                            503,
                            "quick_worker_recovery_unavailable",
                            "Quick Worker 状态不可用，无法确认该 Session 是否空闲。",
                        )
                    if self._any_running(session_id):
                        raise ApiError(
                            409,
                            "quick_interaction_in_progress",
                            "Quick Worker 当前不可用，且该 Session 仍有未完成的快速交互，无法删除。",
                        )
            yield

    @contextmanager
    def terminal_access_guard(self, session_id: str) -> Iterator[None]:
        with self._session_lock(session_id):
            with self._lock:
                if self._any_running(session_id):
                    raise ApiError(
                        409,
                        "quick_interaction_in_progress",
                        "该会话正在执行 Codex CLI 快速交互，请等待任务结束。",
                    )
            yield

    @contextmanager
    def terminal_input_guard(self, session_id: str) -> Iterator[bool]:
        with self._session_lock(session_id):
            with self._lock:
                allowed = not self._any_running(session_id)
            yield allowed

    def get(self, task_id: str) -> QuickInteractionTask:
        with self._lock:
            task = self._tasks.get(task_id)
            if task is None:
                raise ApiError(404, "quick_interaction_not_found", "快速交互任务不存在。")
            return task.model_copy(deep=True)

    def find_for_operation(self, operation_id: str) -> QuickInteractionTask | None:
        """Return the locally reserved task for one trusted operation, if any."""
        with self._lock:
            for task_id, context in self._operations.items():
                if context[0] == operation_id and (task := self._tasks.get(task_id)):
                    return task.model_copy(deep=True)
        return None

    def list_for_session(
        self,
        session_id: str,
        *,
        order: QuickInteractionOrder = "task",
    ) -> list[QuickInteractionTask]:
        self.codex_manager.get_session(session_id)
        with self._lock:
            tasks = [
                task.model_copy(deep=True)
                for task in self._tasks.values()
                if task.session_id == session_id
            ]
        if not tasks:
            return []
        if order == "timeline":
            return sorted(
                tasks,
                key=lambda item: (item.created_at, item.id),
                reverse=True,
            )
        return sorted(
            tasks,
            key=lambda item: (item.created_at, item.id),
            reverse=True,
        )

    def weixin_task_status_snapshot(
        self,
        route: QuickInteractionWeixinRoute,
    ) -> WeixinTaskStatusSnapshot:
        """Return one sender's task counts without changing notification state."""
        with self._lock:
            matching = [
                task
                for task in self._tasks.values()
                if task.notification_route == "weixin-task"
                and task.kind == "standard"
                and self._notification_routes.get(task.id) == route
            ]
            return WeixinTaskStatusSnapshot(
                running_count=sum(
                    task.status in {"requested", "running"} for task in matching
                ),
                pending_notification_count=sum(
                    task.status not in {"requested", "running"}
                    and task.notification_status in {"pending", "sending"}
                    for task in matching
                ),
                failed_notification_count=sum(
                    task.status not in {"requested", "running"}
                    and task.notification_status in {None, "failed", "skipped"}
                    for task in matching
                ),
                running_tasks=tuple(
                    (
                        task.session_id,
                        build_task_summary(
                            task.prompt or task.summary or "本次微信任务",
                            max_chars=48,
                        ),
                    )
                    for task in matching
                    if task.status in {"requested", "running"}
                ),
            )

    def running_standard_task_summaries(self) -> WeixinTaskStatusSnapshot:
        """Return bounded summaries for all running standard tasks without route data."""
        with self._lock:
            running_tasks = sorted(
                (
                    task
                    for task in self._tasks.values()
                    if task.kind == "standard"
                    and task.status in {"requested", "running"}
                ),
                key=lambda task: (task.created_at, task.id),
            )
            return WeixinTaskStatusSnapshot(
                running_tasks=tuple(
                    (task.session_id, task.summary) for task in running_tasks
                )
            )

    def active_sessions(self) -> dict[str, datetime]:
        with self._lock:
            active = set(self._untracked_worker_sessions) | set(self._running_sessions) | {
                task.session_id
                for task_id, task in self._tasks.items()
                if task_id in self._active_task_ids
            }
            return {
                session_id: max(
                    (
                        task.updated_at
                        for task in self._tasks.values()
                        if task.session_id == session_id
                    ),
                    default=utc_now(),
                )
                for session_id in active
            }

    def session_activity_times(self) -> dict[str, datetime]:
        """Return the latest task update for each quick-interaction Session."""
        with self._lock:
            activity_times: dict[str, datetime] = {}
            for task in self._tasks.values():
                current = activity_times.get(task.session_id)
                if current is None or task.updated_at > current:
                    activity_times[task.session_id] = task.updated_at
            return activity_times

    def is_running(self, session_id: str) -> bool:
        with self._lock:
            return self._any_running(session_id)

    def has_active_tasks(self) -> bool:
        with self._lock:
            return bool(self._active_task_ids)

    def system_upgrade_readiness(self) -> str | None:
        with self._lock:
            if self._local_state_error is not None or not self._recovery_ready:
                return "快速交互恢复状态尚未就绪。"
            if self._active_task_ids or self._submitting_task_ids:
                return "仍有快速交互任务正在执行。"
            for task in self._tasks.values():
                if task.status in {"requested", "running"}:
                    return "仍有快速交互任务尚未结束。"
                if task.notification_status in {"pending", "sending"}:
                    return "仍有任务结果通知尚未确认。"
                if task.deferred_restart_status in {"pending", "started"}:
                    return "仍有协调重启请求尚未结束。"
                if task.deferred_restart_notification_status in {"pending", "sending"}:
                    return "仍有重启结果通知尚未确认。"
            return None

    def reset_for_system_upgrade(self, *, force: bool = False) -> None:
        with self._lock:
            readiness = self.system_upgrade_readiness()
            if readiness is not None and not force:
                raise OSError(readiness)
            self._tasks.clear()
            self._running_sessions.clear()
            self._active_task_ids.clear()
            self._cancelled_task_ids.clear()
            self._task_done_events.clear()
            self._operations.clear()
            self._notification_routes.clear()
            self._deferred_restart_contexts.clear()
            self._operation_contexts.clear()
            self._worker_delivery_confirmed.clear()
            self._submitting_task_ids.clear()
            self._untracked_worker_sessions.clear()
            self._write()
            self._system_upgrade_reset = True

    def deferred_restart_ready(
        self,
        request: DeferredRestartRequest,
    ) -> DeferredRestartReadiness:
        with self._lock:
            if not self._recovery_ready:
                return "waiting"
            requesting_tasks = [
                self._tasks[task_id]
                for task_id in self._deferred_restart_task_ids(
                    request.operation_id,
                    request.requested_task_id,
                )
            ]
            if not requesting_tasks or any(
                task.status != "succeeded"
                or task.notification_status in {"pending", "sending"}
                for task in requesting_tasks
            ):
                return "waiting"
            return "ready"

    def has_pending_deferred_restart_notifications(self) -> bool:
        with self._lock:
            return any(
                task.deferred_restart_notification_status == "pending"
                for task in self._tasks.values()
            )

    def record_deferred_restart_started(
        self,
        coordinator_operation_id: str,
        requested_task_id: str,
        started_at: datetime,
    ) -> None:
        with self._deferred_restart_transition_lock:
            with self._lock:
                task_ids = self._deferred_restart_task_ids(
                    coordinator_operation_id,
                    requested_task_id,
                )
                contexts = {
                    task_id: self._deferred_restart_contexts.get(task_id)
                    for task_id in task_ids
                }
                for task_id in task_ids:
                    task = self._tasks[task_id]
                    if task.deferred_restart_status == "pending":
                        task.deferred_restart_status = "started"
                        task.deferred_restart_updated_at = started_at
                if task_ids:
                    self._write()
        for context in contexts.values():
            if context is None or context.operation_id == coordinator_operation_id:
                continue
            write_operation(
                operation_id=context.operation_id,
                action="restart_hub",
                status="started",
                target="chub",
                source_ip=context.source_ip,
            )

    def record_deferred_restart_completion(
        self,
        coordinator_operation_id: str,
        requested_task_id: str,
        outcome: DeferredRestartOutcome,
        completed_at: datetime,
        failure_reason: str | None = None,
    ) -> None:
        with self._deferred_restart_transition_lock:
            with self._lock:
                task_ids = self._deferred_restart_task_ids(
                    coordinator_operation_id,
                    requested_task_id,
                )
                if not task_ids:
                    LOGGER.warning(
                        "Unable to associate deferred restart completion with operation %s",
                        coordinator_operation_id,
                    )
                    return
                contexts = {
                    task_id: self._deferred_restart_contexts.get(task_id)
                    for task_id in task_ids
                }
                notification_task_ids = []
                for task_id in task_ids:
                    task = self._tasks[task_id]
                    if (
                        task.status != "succeeded"
                        or task.deferred_restart_status
                        in {
                            "succeeded",
                            "start_failed",
                            "sensitive_task_failed",
                            "cleared",
                        }
                    ):
                        continue
                    task.deferred_restart_status = outcome
                    task.deferred_restart_error = (
                        failure_reason[:500]
                        if outcome == "start_failed" and failure_reason
                        else None
                    )
                    task.deferred_restart_updated_at = completed_at
                    if (
                        outcome in {
                            "succeeded",
                            "start_failed",
                            "sensitive_task_failed",
                        }
                        and task.notification_route == "weixin-task"
                        and task.deferred_restart_notification_status is None
                    ):
                        task.deferred_restart_notification_status = "pending"
                        task.deferred_restart_notification_updated_at = completed_at
                        notification_task_ids.append(task_id)
                self._write()
        final_log_status = "succeeded" if outcome in {"succeeded", "cleared"} else "failed"
        for context in contexts.values():
            if context is None or context.operation_id == coordinator_operation_id:
                continue
            write_operation(
                operation_id=context.operation_id,
                action="restart_hub",
                status=final_log_status,
                target="chub",
                source_ip=context.source_ip,
            )
        for task_id in notification_task_ids:
            self._start_deferred_restart_notification(task_id)

    def resume_pending_deferred_restart_notifications(self) -> None:
        with self._lock:
            task_ids = [
                task.id
                for task in self._tasks.values()
                if task.deferred_restart_notification_status == "pending"
            ]
        for task_id in task_ids:
            self._start_deferred_restart_notification(task_id)

    def _deferred_restart_task_ids(
        self,
        coordinator_operation_id: str,
        requested_task_id: str,
    ) -> list[str]:
        task_ids = [
            task_id
            for task_id, context in self._deferred_restart_contexts.items()
            if context.coordinator_operation_id == coordinator_operation_id
            and task_id in self._tasks
        ]
        if requested_task_id in self._tasks and requested_task_id not in task_ids:
            task_ids.append(requested_task_id)
        return task_ids

    def has_deferred_restart_context(
        self,
        coordinator_operation_id: str,
        requested_task_id: str,
    ) -> bool:
        with self._lock:
            return bool(
                self._deferred_restart_task_ids(
                    coordinator_operation_id,
                    requested_task_id,
                )
            )

    def _start_deferred_restart_notification(self, task_id: str) -> None:
        try:
            threading.Thread(
                target=self._deliver_deferred_restart_notification,
                args=(task_id,),
                daemon=True,
                name=f"chub-restart-notification-{task_id[:8]}",
            ).start()
        except RuntimeError:
            with self._lock:
                task = self._tasks.get(task_id)
                if (
                    task is not None
                    and task.deferred_restart_notification_status == "pending"
                ):
                    task.deferred_restart_notification_status = "failed"
                    task.deferred_restart_notification_error = (
                        "微信重启通知线程未能启动。"
                    )
                    task.deferred_restart_notification_updated_at = utc_now()
                    self._write()
            LOGGER.warning("Unable to start deferred restart notification thread")

    def _deliver_deferred_restart_notification(self, task_id: str) -> None:
        with self._lock:
            task = self._tasks.get(task_id)
            if (
                task is None
                or task.deferred_restart_notification_status != "pending"
            ):
                return
            task.deferred_restart_notification_status = "sending"
            task.deferred_restart_notification_updated_at = utc_now()
            self._write()
            snapshot = task.model_copy(deep=True)
            context = self._deferred_restart_contexts.get(task_id)
            route = self._notification_routes.get(task_id)
        operation_id = (
            f"{context.operation_id}:weixin"
            if context is not None
            else f"{task_id}:restart:weixin"
        )
        source_ip = context.source_ip if context is not None else "unknown"
        for status in ("requested", "started"):
            write_operation(
                operation_id=operation_id,
                action="quick_interaction_restart_weixin_notification",
                status=status,
                target=snapshot.session_id,
                source_ip=source_ip,
            )
        try:
            result = (
                self.restart_notifier(
                    snapshot,
                    route,
                    snapshot.deferred_restart_status,
                )
                if self.restart_notifier is not None
                else None
            )
            notification_status = getattr(result, "status", "failed")
            notification_error = getattr(result, "error", None)
            if notification_status not in {"sent", "failed", "skipped"}:
                notification_status = "failed"
                notification_error = "微信重启通知返回了无效状态。"
        except Exception:
            LOGGER.warning("Deferred restart Weixin notification failed", exc_info=True)
            notification_status = "failed"
            notification_error = "微信重启通知未送达。"
        with self._lock:
            task = self._tasks.get(task_id)
            if task is None:
                return
            task.deferred_restart_notification_status = notification_status
            task.deferred_restart_notification_error = (
                notification_error[:1000] if notification_error else None
            )
            task.deferred_restart_notification_updated_at = utc_now()
            self._write()
        write_operation(
            operation_id=operation_id,
            action="quick_interaction_restart_weixin_notification",
            status="succeeded" if notification_status == "sent" else "failed",
            target=snapshot.session_id,
            source_ip=source_ip,
        )
        if self.deferred_restart is not None:
            self.deferred_restart.maybe_schedule()

    def cancel_codex_session(self, session_id: str, *, timeout: float = 5) -> bool:
        """Cancel the active Codex CLI interaction and wait for state cleanup."""
        with self._session_lock(session_id):
            with self._lock:
                task_ids = [
                    item.id
                    for item in self._tasks.values()
                    if item.session_id == session_id
                    and item.id in self._active_task_ids
                ]
                if not task_ids:
                    if self._any_running(session_id):
                        raise ApiError(
                            503,
                            "quick_interaction_cancel_failed",
                            "快速交互停止状态无法确认，请稍后重试。",
                        )
                    return False
            deadline = time.monotonic() + timeout
            for task_id in task_ids:
                remaining = max(0.0, deadline - time.monotonic())
                self.cancel_task(task_id, timeout=remaining)
            return True

    def remove_session_tasks(self, session_id: str) -> None:
        """Remove retained Quick Worker task records after a Session operation."""
        with self._session_lock(session_id):
            with self._lock:
                if self._any_running(session_id):
                    raise ApiError(
                        409,
                        "quick_interaction_in_progress",
                        "该会话正在执行快速交互，请等待任务结束。",
                    )
                task_ids = {
                    task.id
                    for task in self._tasks.values()
                    if task.session_id == session_id
                }
                if not task_ids:
                    return
                for task_id in task_ids:
                    self._tasks.pop(task_id, None)
                    self._notification_routes.pop(task_id, None)
                    self._deferred_restart_contexts.pop(task_id, None)
                    self._operation_contexts.pop(task_id, None)
                    self._operations.pop(task_id, None)
                    self._worker_delivery_confirmed.discard(task_id)
                    self._submitting_task_ids.discard(task_id)
                    self._cancelled_task_ids.discard(task_id)
                    self._task_done_events.pop(task_id, None)
                self._running_sessions.discard(session_id)
                self._untracked_worker_sessions.discard(session_id)
                self._write()

    def cancel_task(self, task_id: str, *, timeout: float = 5) -> bool:
        """Cancel one exact task without guessing among a shared Session queue."""
        self._require_worker_recovery()
        with self._lock:
            task = self._tasks.get(task_id)
            if task is None or task_id not in self._active_task_ids:
                return False
            self._cancelled_task_ids.add(task.id)
            done = self._task_done_events.get(task.id)
        if task.worker_task_id:
            try:
                payload = self._worker_call(
                    "task_cancel",
                    task_id=task.worker_task_id,
                    timeout_seconds=timeout,
                )
            except OSError as exc:
                raise ApiError(
                    503,
                    "quick_interaction_cancel_failed",
                    "快速交互未能在限定时间内停止。",
                ) from exc
            if payload.get("success") is not True:
                raise ApiError(
                    503,
                    "quick_interaction_cancel_failed",
                    "快速交互未能在限定时间内停止。",
                )
        if done is None or not done.wait(timeout):
            raise ApiError(
                503,
                "quick_interaction_cancel_failed",
                "快速交互未能在限定时间内停止。",
            )
        return True

    def cancel_unobserved_task(self, task_id: str, *, timeout: float = 5) -> bool:
        """Cancel a Worker task before its normal observer has been started."""
        with self._lock:
            task = self._tasks.get(task_id)
            if (
                task is None
                or task_id not in self._active_task_ids
                or task.worker_task_id is None
            ):
                return False
        try:
            payload = self._worker_call(
                "task_cancel",
                task_id=task.worker_task_id,
                timeout_seconds=timeout,
            )
        except OSError as exc:
            raise ApiError(
                503,
                "quick_interaction_cancel_failed",
                "快速交互未能在限定时间内停止。",
            ) from exc
        if payload.get("success") is not True:
            raise ApiError(
                503,
                "quick_interaction_cancel_failed",
                "快速交互未能在限定时间内停止。",
            )
        self._finish(task_id, "cancelled", "已由用户停止。")
        with self._lock:
            self._active_task_ids.discard(task_id)
            remaining_for_session = any(
                active_id in self._active_task_ids
                and active_task.session_id == task.session_id
                for active_id, active_task in self._tasks.items()
            )
            if not remaining_for_session:
                self._running_sessions.discard(task.session_id)
            self._cancelled_task_ids.discard(task_id)
            done = self._task_done_events.pop(task_id, None)
            if done is not None:
                done.set()
            self._operations.pop(task_id, None)
            self._write()
        self._log_status(task_id, "failed", task.session_id)
        if not remaining_for_session:
            self.codex_manager.set_activity(
                task.session_id,
                "idle",
                "none",
                updated_at=self.get(task_id).updated_at,
            )
        if self.deferred_restart is not None:
            self.deferred_restart.maybe_schedule()
        return True

    @contextmanager
    def stop_operation_guard(self, session_id: str) -> Iterator[None]:
        """Serialize stop with submit while allowing stop to cancel Codex work."""
        with self._session_lock(session_id):
            self._require_worker_recovery()
            yield

    def _is_cancelled(self, task_id: str) -> bool:
        with self._lock:
            return task_id in self._cancelled_task_ids

    def _run_worker(self, task_id: str, session: AiSession, prompt: str) -> None:
        if self._closed.is_set():
            return
        with self._lock:
            task = self._tasks[task_id]
        started_logged = False
        try:
            worker_task_id = task.worker_task_id
            if worker_task_id is None:
                raise OSError("Worker task identity is unavailable")
            while True:
                with self._lock:
                    if self._system_upgrade_reset or self._closed.is_set():
                        return
                snapshot_payload = self._worker_call(
                    "task_get",
                    task_id=worker_task_id,
                )
                if snapshot_payload.get("success") is not True:
                    raise OSError(self._worker_error(snapshot_payload))
                data = snapshot_payload.get("data")
                if not isinstance(data, dict):
                    raise OSError("Worker returned invalid task data")
                snapshot = WorkerTaskView.model_validate_json(
                    json.dumps(data.get("task"), ensure_ascii=False)
                )
                if self._closed.is_set():
                    return
                if snapshot.task_id != worker_task_id:
                    raise OSError("Worker returned a mismatched task identity")
                if snapshot.status in {"starting", "running"}:
                    if not started_logged:
                        with self._lock:
                            current = self._tasks[task_id]
                            if current.status == "requested":
                                current.status = "running"
                                current.updated_at = utc_now()
                                self._write()
                        self._log_status(task_id, "started", session.id)
                        self.codex_manager.set_activity(
                            session.id,
                            "working",
                            "quick",
                        )
                        started_logged = True
                if snapshot.native_session_id:
                    if task.kind == "translation":
                        self.codex_manager.bind_quick_interaction_native_session(
                            session.id,
                            snapshot.native_session_id,
                        )
                    else:
                        if snapshot.execution_id is None:
                            raise OSError("Worker native Session is missing execution identity")
                        self.codex_manager.bind_quick_interaction_native_session(
                            session.id,
                            snapshot.native_session_id,
                            worker_task_id=worker_task_id,
                            execution_id=snapshot.execution_id,
                        )
                if snapshot.status in {"queued", "accepted", "starting", "running"}:
                    threading.Event().wait(0.1)
                    continue
                self._finish_from_worker_snapshot(task_id, task, snapshot)
                break
        except Exception as exc:
            if self._is_cancelled(task_id):
                self._finish(task_id, "cancelled", "已由用户停止。")
            else:
                detail = self._worker_exception_detail(exc)
                self._finish(
                    task_id,
                    "failed",
                    f"Chub Worker observer error: {detail}",
                    error_source="chub",
                )
        finally:
            with self._lock:
                if self._system_upgrade_reset or self._closed.is_set():
                    return
            finished = self.get(task_id)
            with self._lock:
                self._active_task_ids.discard(task_id)
                remaining_for_session = any(
                    active_id in self._active_task_ids
                    and active_task.session_id == session.id
                    for active_id, active_task in self._tasks.items()
                )
                if not remaining_for_session:
                    self._running_sessions.discard(session.id)
                self._cancelled_task_ids.discard(task_id)
                done = self._task_done_events.pop(task_id, None)
                if done is not None:
                    done.set()
            if not remaining_for_session:
                self.codex_manager.set_activity(
                    session.id,
                    "idle",
                    "none",
                    updated_at=finished.updated_at,
                )
            self._log_status(task_id, finished.status, session.id)
            with self._lock:
                self._operations.pop(task_id, None)
                self._write()
            if self.deferred_restart is not None:
                self.deferred_restart.maybe_schedule()

    def _worker_submission(
        self,
        task: QuickInteractionTask,
        session: AiSession,
        prompt: str,
    ) -> RuntimeTaskSubmission:
        task_kind = "translation" if task.kind == "translation" else (
            "weixin" if task.notification_route == "weixin-task" else "standard"
        )
        worker_task_id = task.worker_task_id
        if worker_task_id is None:
            raise OSError("Worker task identity is unavailable")
        runtime_id = getattr(self.codex_manager, "runtime_id", "codex")
        if not isinstance(runtime_id, str):
            runtime_id = "codex"
        session_runtime_id = getattr(session, "runtime_id", runtime_id)
        if session_runtime_id != runtime_id:
            raise OSError("Session Runtime owner does not match the active Runtime")
        model = task.model
        reasoning_effort = task.reasoning_effort
        permission_profile = task.permission_mode or session.permission_mode
        return RuntimeTaskSubmission(
            task_id=worker_task_id,
            runtime_id=runtime_id,
            session_id=session.id,
            workspace_id=session.workspace_id,
            prompt=(
                prompt
                if task.kind == "translation"
                else self._codex_execution_prompt(prompt)
            ),
            permission_profile=permission_profile,
            native_session_id=session.native_session_id,
            model=model,
            reasoning_effort=reasoning_effort,
            timeout_seconds=self.timeout_seconds,
            task_kind=task_kind,
            restart_sensitive=task.restart_sensitive,
            queue_key=("weixin-translation" if task.kind == "translation" else None),
            queue_limit=(self._translation_queue_limit if task.kind == "translation" else None),
            queue_wait_seconds=(
                self._translation_queue_wait_seconds
                if task.kind == "translation"
                else None
            ),
        )

    def _submit_worker_task(
        self,
        task: QuickInteractionTask,
        session: AiSession,
        prompt: str,
    ) -> None:
        submission = self._worker_submission(task, session, prompt)
        try:
            for retry_delay in (*WORKER_CONNECTION_RETRY_DELAYS, None):
                try:
                    accepted = self._worker_call(
                        "runtime_task_submit",
                        task=submission.model_dump(mode="json"),
                    )
                    break
                except WorkerRequestNotSent:
                    if retry_delay is None:
                        raise
                    # The IPC request was never sent, so retrying cannot duplicate work.
                    time.sleep(retry_delay)
        except WorkerRequestNotSent:
            # No bytes reached the Worker, so this is a confirmed unavailable
            # path rather than an ambiguous accepted-or-lost submission.
            raise
        except OSError as submit_error:
            try:
                accepted = self._worker_call(
                    "runtime_task_submit",
                    task=submission.model_dump(mode="json"),
                )
            except OSError as retry_error:
                raise _WorkerSubmissionUncertain(submission) from retry_error
        if accepted.get("success") is not True:
            raise OSError(self._worker_error(accepted))
        try:
            self._validate_worker_submission_response(accepted, submission)
        except (OSError, ValueError):
            raise _WorkerSubmissionUncertain(submission) from None

    @staticmethod
    def _validate_worker_submission_response(
        payload: dict[str, object],
        submission: RuntimeTaskSubmission,
    ) -> None:
        data = payload.get("data")
        if not isinstance(data, dict):
            raise OSError("Worker returned invalid submission data")
        snapshot = WorkerTaskView.model_validate_json(
            json.dumps(data.get("task"), ensure_ascii=False)
        )
        expected_prompt_sha256 = hashlib.sha256(
            submission.prompt.encode("utf-8")
        ).hexdigest()
        if (
            snapshot.task_id != submission.task_id
            or snapshot.runtime_id != submission.runtime_id
            or snapshot.prompt_sha256 != expected_prompt_sha256
            or snapshot.restart_sensitive != bool(submission.restart_sensitive)
        ):
            raise OSError("Worker returned a mismatched submission identity")

    def _finish_from_worker_snapshot(
        self,
        task_id: str,
        task: QuickInteractionTask,
        snapshot: WorkerTaskView,
    ) -> None:
        if snapshot.status == "succeeded":
            result = snapshot.result or "Codex 未返回最终结果。"
            if task.kind == "translation" and not self._valid_translation_result(result):
                self._finish(
                    task_id,
                    "failed",
                    "Codex 未返回有效的润色与英文翻译。",
                    error_source="chub",
                )
            else:
                if task.kind != "translation":
                    with self._deferred_restart_transition_lock:
                        result = self._register_worker_deferred_restart(
                            task_id,
                            task,
                            result,
                        )
                        self._finish(task_id, "succeeded", result)
                else:
                    self._finish(task_id, "succeeded", result)
        elif snapshot.status == "cancelled":
            self._finish(task_id, "cancelled", "已由用户停止。")
        elif snapshot.status == "timed_out":
            message = (
                "翻译任务排队超时。"
                if snapshot.error_code == "queue_deadline_exceeded"
                else f"Codex 已达到配置的执行上限（{self.timeout_seconds} 秒）。"
            )
            self._finish(task_id, "timed_out", message)
        else:
            self._finish(
                task_id,
                "failed",
                snapshot.error or "Codex 执行失败。",
                error_source=getattr(snapshot, "error_source", None),
            )
        if task.worker_task_id is not None:
            try:
                (self.restart_request_dir / f"{task.worker_task_id}.request").unlink()
            except FileNotFoundError:
                pass
            except OSError:
                LOGGER.warning(
                    "Unable to remove Worker deferred restart request",
                    exc_info=True,
                )

    def _register_worker_deferred_restart(
        self,
        task_id: str,
        task: QuickInteractionTask,
        result: str,
    ) -> str:
        if task.worker_task_id is None or self.deferred_restart is None:
            return result
        request_path = self.restart_request_dir / f"{task.worker_task_id}.request"
        if request_path.is_symlink() or not request_path.is_file():
            return result
        with self._lock:
            operation = self._operations.get(task_id) or (uuid.uuid4().hex, "unknown")
        restart_operation_id = f"{operation[0]}:restart"
        try:
            with self._deferred_restart_transition_lock:
                registration = self.deferred_restart.request(
                    operation_id=restart_operation_id,
                    task_id=task_id,
                    source_ip=operation[1],
                )
                with self._lock:
                    current = self._tasks[task_id]
                    current.deferred_restart_status = "pending"
                    current.deferred_restart_error = None
                    current.deferred_restart_updated_at = utc_now()
                    self._deferred_restart_contexts[task_id] = (
                        QuickInteractionDeferredRestartContext(
                            operation_id=restart_operation_id,
                            coordinator_operation_id=registration.operation_id,
                            source_ip=operation[1],
                        )
                    )
        except (ApiError, OSError):
            LOGGER.warning("Unable to register deferred restart", exc_info=True)
            return self._append_result_suffix(result, DEFERRED_RESTART_FAILED_SUFFIX)
        if not registration.created:
            write_operation(
                operation_id=restart_operation_id,
                action="restart_hub",
                status="requested",
                target="chub",
                source_ip=operation[1],
            )
        return self._append_result_suffix(
            result,
            DEFERRED_RESTART_RESULT_SUFFIX,
        )

    def configure_translation_worker_queue(self, *, limit: int, wait_seconds: int) -> None:
        self._translation_queue_limit = limit
        self._translation_queue_wait_seconds = wait_seconds

    def _worker_call(
        self,
        action: str,
        *,
        timeout_seconds: float = 2.0,
        **payload: object,
    ) -> dict[str, object]:
        if self.worker_settings is None:
            raise OSError("Worker settings are unavailable")
        return worker_request_sync(
            self.worker_settings,
            {
                "protocol_version": PROTOCOL_VERSION,
                "request_id": uuid.uuid4().hex,
                "action": action,
                **payload,
            },
            timeout_seconds=timeout_seconds,
        )

    @staticmethod
    def _worker_error(payload: dict[str, object]) -> str:
        error = payload.get("error")
        if isinstance(error, dict) and isinstance(error.get("message"), str):
            return error["message"]
        return "Worker request failed"

    @staticmethod
    def _worker_exception_detail(error: BaseException) -> str:
        if isinstance(error, OSError) and error.strerror:
            detail = error.strerror
        else:
            detail = str(error).strip()
        return QuickInteractionManager._limit_text(
            detail or type(error).__name__,
            1_000,
        )

    @staticmethod
    def _codex_execution_prompt(prompt: str) -> str:
        return f"[用户需求]\n{prompt}\n\n{CODEX_QUICK_INTERACTION_INSTRUCTIONS}"

    @staticmethod
    def _session_title(prompt: str) -> str:
        for line in prompt.splitlines():
            title = " ".join(line.split())
            if title:
                return title[:MAX_SESSION_TITLE_LENGTH]
        return "快速交互"

    @staticmethod
    def _valid_translation_result(result: str) -> bool:
        match = re.fullmatch(
            r"润色：\n(?P<polished>\S(?:.*\S)?)\n\nEnglish：\n(?P<english>\S(?:.*\S)?)",
            result.strip(),
            flags=re.DOTALL,
        )
        return bool(
            match
            and match.group("polished").strip()
            and match.group("english").strip()
        )

    @staticmethod
    def _append_result_suffix(result: str, suffix: str) -> str:
        if result.rstrip().endswith(suffix):
            return result.rstrip()
        suffix_text = f"\n\n{suffix}"
        suffix_bytes = suffix_text.encode("utf-8")
        available = max(0, MAX_RESULT_BYTES - len(suffix_bytes))
        base = result.encode("utf-8")[:available].decode("utf-8", errors="ignore")
        return f"{base.rstrip()}{suffix_text}"

    def _finish(
        self,
        task_id: str,
        status: str,
        result: str,
        *,
        error_source: QuickInteractionErrorSource | None = None,
    ) -> None:
        notification_operation: tuple[str, str] | None = None
        finished_snapshot: QuickInteractionTask | None = None
        with self._lock:
            if self._system_upgrade_reset:
                return
            task = self._tasks[task_id]
            task.status = status
            if status == "succeeded":
                task.result = result
                task.error = None
                task.error_source = None
            else:
                task.error = result
                task.error_source = error_source
            task.updated_at = utc_now()
            if (
                task.notification_route != "weixin-task"
                and task.notification_status is None
            ):
                task.notification_status = "skipped"
                task.notification_error = "页面任务结果仅在 Chub 快速交互页面展示。"
                task.notification_updated_at = task.updated_at
            if (
                self.completion_notifier is not None
                and task.notification_status is None
                and task.notification_route == "weixin-task"
                and (
                    status == "succeeded"
                    or (
                        task.kind != "translation"
                        and status in {"failed", "timed_out"}
                    )
                )
            ):
                task.notification_status = "pending"
                task.notification_updated_at = task.updated_at
                notification_operation = self._operations.get(task_id) or (
                    uuid.uuid4().hex,
                    "unknown",
                )
            self._write()
            finished_snapshot = task.model_copy(deep=True)
        if (
            finished_snapshot is not None
            and finished_snapshot.worker_task_id is not None
        ):
            self._clear_quick_native_claim_safely(
                finished_snapshot.session_id,
                finished_snapshot.worker_task_id,
            )
        if finished_snapshot is not None and self._task_finished_handler is not None:
            try:
                self._task_finished_handler(finished_snapshot)
            except Exception:
                LOGGER.warning("Quick interaction completion handler failed", exc_info=True)
        if notification_operation is not None:
            try:
                threading.Thread(
                    target=self._deliver_completion_notification,
                    args=(task_id, notification_operation),
                    daemon=True,
                ).start()
            except RuntimeError:
                with self._lock:
                    task = self._tasks.get(task_id)
                    if task is not None and task.notification_status == "pending":
                        task.notification_status = "failed"
                        task.notification_error = "微信通知线程未能启动。"
                        task.notification_updated_at = utc_now()
                        self._write()
                LOGGER.warning("Unable to start completion notification thread")

    def _clear_quick_native_claim_safely(
        self,
        session_id: str,
        worker_task_id: str,
    ) -> None:
        claim = (session_id, worker_task_id)
        try:
            self.codex_manager.clear_quick_native_claim(*claim)
        except Exception:
            with self._lock:
                self._pending_native_claim_clears.add(claim)
            LOGGER.warning(
                "Unable to clear Quick Worker native Session claim; retrying during reconciliation",
                exc_info=True,
            )
            return
        with self._lock:
            self._pending_native_claim_clears.discard(claim)

    def _retry_pending_native_claim_clears(self) -> None:
        with self._lock:
            pending = tuple(self._pending_native_claim_clears)
        for session_id, worker_task_id in pending:
            self._clear_quick_native_claim_safely(session_id, worker_task_id)

    def _deliver_completion_notification(
        self,
        task_id: str,
        operation: tuple[str, str],
    ) -> None:
        operation_id, source_ip = operation
        notification_operation_id = f"{operation_id}:weixin"
        with self._lock:
            task = self._tasks.get(task_id)
            if (
                task is None
                or task.notification_status != "pending"
                or task.notification_route != "weixin-task"
            ):
                return
            task.notification_status = "sending"
            task.notification_updated_at = utc_now()
            self._write()
            snapshot = task.model_copy(deep=True)
        write_operation(
            operation_id=notification_operation_id,
            action="quick_interaction_weixin_notification",
            status="requested",
            target=snapshot.session_id,
            source_ip=source_ip,
        )
        write_operation(
            operation_id=notification_operation_id,
            action="quick_interaction_weixin_notification",
            status="started",
            target=snapshot.session_id,
            source_ip=source_ip,
        )
        try:
            route = self._notification_routes.get(task_id)
            result = (
                self.completion_notifier(snapshot, route)
                if self.completion_notifier
                else None
            )
            notification_status = getattr(result, "status", "failed")
            notification_error = getattr(result, "error", None)
            if notification_status not in {"sent", "failed", "skipped"}:
                notification_status = "failed"
                notification_error = "微信通知返回了无效状态。"
        except Exception:
            LOGGER.warning("Quick interaction Weixin notification failed", exc_info=True)
            notification_status = "failed"
            notification_error = "微信通知未送达。"
        with self._lock:
            task = self._tasks.get(task_id)
            if task is None:
                return
            task.notification_status = notification_status
            task.notification_error = notification_error[:1000] if notification_error else None
            task.notification_updated_at = utc_now()
            self._write()
        write_operation(
            operation_id=notification_operation_id,
            action="quick_interaction_weixin_notification",
            status="succeeded" if notification_status == "sent" else "failed",
            target=snapshot.session_id,
            source_ip=source_ip,
        )
        if self.deferred_restart is not None:
            self.deferred_restart.maybe_schedule()

    def _log_status(self, task_id: str, status: str, target: str) -> None:
        operation_status = (
            status
            if status in {"requested", "started", "succeeded", "failed"}
            else "failed"
        )
        with self._lock:
            context = self._operation_contexts.get(task_id)
            if context is not None:
                if operation_status in context.logged_statuses:
                    return
                operation = (context.operation_id, context.source_ip)
            else:
                operation = self._operations.get(task_id)
        if operation is None:
            return
        operation_id, source_ip = operation
        write_operation(
            operation_id=operation_id,
            action="quick_interaction",
            status=operation_status,
            target=target,
            source_ip=source_ip,
        )
        if context is None:
            return
        with self._lock:
            current = self._operation_contexts.get(task_id)
            if current is None or operation_status in current.logged_statuses:
                return
            self._operation_contexts[task_id] = current.model_copy(
                update={
                    "logged_statuses": (*current.logged_statuses, operation_status)
                }
            )
            try:
                self._write()
            except OSError:
                LOGGER.warning(
                    "Unable to persist quick interaction operation log projection",
                    exc_info=True,
                )

    def find_task_by_operation(
        self,
        operation_id: str,
        *,
        kind: str,
    ) -> QuickInteractionTask | None:
        with self._lock:
            matches = [
                self._tasks[task_id]
                for task_id, context in self._operation_contexts.items()
                if context.operation_id == operation_id
                and task_id in self._tasks
                and self._tasks[task_id].kind == kind
            ]
        if len(matches) > 1:
            raise OSError("Quick interaction operation identity is ambiguous")
        return matches[0].model_copy(deep=True) if matches else None

    def close(self) -> None:
        self._closed.set()
        self._reconciler_stop.set()
        reconciler = self._reconciler_thread
        if reconciler is not None and reconciler is not threading.current_thread():
            reconciler.join(timeout=2)

    async def aclose(self) -> None:
        self.close()

    @staticmethod
    def _limit_text(value: str, limit: int) -> str:
        return value.encode("utf-8")[:limit].decode("utf-8", errors="ignore")

    def _write(self) -> None:
        if self._system_upgrade_reset:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if len(self._tasks) > MAX_STORED_TASKS:
            protected = [
                task
                for task in self._tasks.values()
                if (
                    task.status in {"requested", "running"}
                    or task.id in self._active_task_ids
                    or task.notification_status in {"pending", "sending"}
                    or task.deferred_restart_status in {"pending", "started"}
                    or task.deferred_restart_notification_status
                    in {"pending", "sending"}
                )
            ]
            completed = sorted(
                (
                    task
                    for task in self._tasks.values()
                    if (
                        task.status not in {"requested", "running"}
                        and task.id not in self._active_task_ids
                        and task.notification_status not in {"pending", "sending"}
                        and task.deferred_restart_status not in {"pending", "started"}
                        and task.deferred_restart_notification_status
                        not in {"pending", "sending"}
                    )
                ),
                key=lambda item: item.updated_at,
                reverse=True,
            )
            retained = protected + completed[
                :max(0, MAX_STORED_TASKS - len(protected))
            ]
            self._tasks = {item.id: item for item in retained}
            self._notification_routes = {
                task_id: route
                for task_id, route in self._notification_routes.items()
                if task_id in self._tasks
            }
            self._deferred_restart_contexts = {
                task_id: context
                for task_id, context in self._deferred_restart_contexts.items()
                if task_id in self._tasks
            }
            self._operation_contexts = {
                task_id: context
                for task_id, context in self._operation_contexts.items()
                if task_id in self._tasks
            }
            self._worker_delivery_confirmed.intersection_update(self._tasks)
            self._submitting_task_ids.intersection_update(self._tasks)
        temporary = self.path.with_suffix(".tmp")
        payload = []
        for item in self._tasks.values():
            serialized = item.model_dump(mode="json")
            route = self._notification_routes.get(item.id)
            if route is not None:
                serialized["_notification_route"] = route.model_dump(mode="json")
            restart_context = self._deferred_restart_contexts.get(item.id)
            if restart_context is not None:
                serialized["_deferred_restart_context"] = restart_context.model_dump(
                    mode="json"
                )
            operation_context = self._operation_contexts.get(item.id)
            if operation_context is not None:
                serialized["_operation_context"] = operation_context.model_dump(
                    mode="json"
                )
            if item.id in self._worker_delivery_confirmed:
                serialized["_worker_delivery_confirmed"] = True
            payload.append(serialized)
        content = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
        if len(content.encode("utf-8")) > MAX_QUICK_INTERACTION_STATE_BYTES:
            raise OSError("Web quick interaction state exceeds its fixed limit")
        temporary.write_text(content, encoding="utf-8")
        os.chmod(temporary, 0o600)
        temporary.replace(self.path)
