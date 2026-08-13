from __future__ import annotations

import json
import logging
import os
import re
import signal
import subprocess
import threading
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable, Iterator

from app.codex.models import (
    CodexSession,
    QuickInteractionDeferredRestartContext,
    QuickInteractionOrder,
    QuickInteractionTask,
    QuickInteractionWeixinRoute,
    utc_now,
)
from app.core.response import ApiError
from app.services.deferred_restart import (
    DeferredRestartCoordinator,
    DeferredRestartOutcome,
)
from app.services.log_reader import redact_log_line
from app.services.operation_log import write_operation


MAX_RESULT_BYTES = 100_000
MAX_EVENT_BYTES = 1_000_000
MAX_STORED_TASKS = 30
MAX_SESSION_TITLE_LENGTH = 48
MAX_TASK_SUMMARY_LENGTH = 13
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
SERVICE_RESTART_ERROR = "服务重启导致正在执行的任务中断，请重新提交任务。"
DEFERRED_RESTART_RESULT_SUFFIX = "本次处理已完成，即将重启 Chub 服务。"
DEFERRED_RESTART_WAITING_SUFFIX = (
    "本次处理已完成，已安排重启；正在等待其他 {count} 个快速交互结束，"
    "全部完成后将自动重启 Chub。"
)
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


def build_task_summary(prompt: str) -> str:
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
    if len(characters) <= MAX_TASK_SUMMARY_LENGTH:
        return value
    return "".join(characters[: MAX_TASK_SUMMARY_LENGTH - 1]).rstrip() + "…"


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
    ) -> None:
        self.path = data_file.with_name("quick-interactions.json")
        self.result_dir = runtime_dir / "quick-interactions"
        self.codex_manager = codex_manager
        self.completion_notifier = completion_notifier
        self.restart_notifier = restart_notifier
        self.deferred_restart = deferred_restart
        self.timeout_seconds = timeout_seconds
        self._lock = threading.RLock()
        self._tasks: dict[str, QuickInteractionTask] = {}
        self._running_sessions: set[str] = set()
        self._active_task_ids: set[str] = set()
        self._cancelled_task_ids: set[str] = set()
        self._shutdown_task_ids: set[str] = set()
        self._task_done_events: dict[str, threading.Event] = {}
        self._session_locks: dict[str, threading.RLock] = {}
        self._processes: dict[str, subprocess.Popen[bytes]] = {}
        self._operations: dict[str, tuple[str, str]] = {}
        self._notification_routes: dict[str, QuickInteractionWeixinRoute] = {}
        self._deferred_restart_contexts: dict[
            str,
            QuickInteractionDeferredRestartContext,
        ] = {}
        self.restart_request_dir = runtime_dir / "restart-requests"
        recovered_sessions, recovered_tasks = self._load()
        if recovered_tasks:
            self._write()
        if recovered_sessions:
            for session_id in recovered_sessions:
                try:
                    self.codex_manager.recover_interrupted_quick_interaction(
                        session_id
                    )
                except Exception:
                    LOGGER.warning(
                        "Unable to recover interrupted quick interaction activity",
                        exc_info=True,
                    )
        self.result_dir.mkdir(parents=True, exist_ok=True)
        os.chmod(self.result_dir, 0o700)
        self.restart_request_dir.mkdir(parents=True, exist_ok=True)
        os.chmod(self.restart_request_dir, 0o700)

    def _load(self) -> tuple[set[str], bool]:
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (FileNotFoundError, OSError, json.JSONDecodeError):
            return set(), False
        if not isinstance(payload, list):
            return set(), False
        recovered_sessions: set[str] = set()
        recovered_tasks = False
        for item in payload:
            if not isinstance(item, dict):
                continue
            task_payload = dict(item)
            route_payload = task_payload.pop("_notification_route", None)
            restart_context_payload = task_payload.pop(
                "_deferred_restart_context",
                None,
            )
            try:
                task = QuickInteractionTask.model_validate(task_payload)
            except ValueError:
                continue
            if task.notification_route == "weixin-task":
                try:
                    route = QuickInteractionWeixinRoute.model_validate(route_payload)
                except ValueError:
                    route = None
                if route is not None:
                    self._notification_routes[task.id] = route
            try:
                restart_context = QuickInteractionDeferredRestartContext.model_validate(
                    restart_context_payload
                )
            except ValueError:
                restart_context = None
            if restart_context is not None:
                self._deferred_restart_contexts[task.id] = restart_context
            if task.status in {"requested", "running"}:
                recovered_tasks = True
                task.status = "failed"
                task.error = SERVICE_RESTART_ERROR
                task.updated_at = utc_now()
                recovered_sessions.add(task.session_id)
            if task.notification_status in {"pending", "sending"}:
                recovered_tasks = True
                task.notification_status = "failed"
                task.notification_error = "服务重启时微信通知未完成。"
                task.notification_updated_at = utc_now()
            if task.deferred_restart_notification_status == "sending":
                recovered_tasks = True
                task.deferred_restart_notification_status = "failed"
                task.deferred_restart_notification_error = (
                    "服务重启时微信重启通知未完成。"
                )
                task.deferred_restart_notification_updated_at = utc_now()
            self._tasks[task.id] = task
        return recovered_sessions, recovered_tasks

    def submit(
        self,
        session_id: str,
        prompt: str,
        *,
        operation_id: str,
        source_ip: str,
        notification_route: QuickInteractionWeixinRoute | None = None,
        weixin_session_slot: int | None = None,
        weixin_session_title: str | None = None,
        kind: str = "standard",
        translation_original: str | None = None,
    ) -> QuickInteractionTask:
        with self._session_lock(session_id):
            if self.deferred_restart is not None and self.deferred_restart.pending():
                raise ApiError(
                    409,
                    "chub_restart_pending",
                    "Chub 已安排重启，暂不接受新的快速交互。",
                )
            session = self.codex_manager.get_session(session_id)
            if session.status == "error":
                raise ApiError(
                    409,
                    "quick_interaction_session_error",
                    "会话当前异常，请先通过实时终端重试。",
                )
            if session.activity == "working":
                raise ApiError(
                    409,
                    "quick_interaction_terminal_working",
                    "实时终端正在执行，请等待当前任务结束。",
                )
            if session.status == "running" and session.activity != "idle":
                raise ApiError(
                    409,
                    "quick_interaction_terminal_active",
                    "当前实时终端状态不允许快速交互。",
                )
            if session.permission_mode == "ask":
                raise ApiError(409, "quick_interaction_requires_terminal", "Ask for approval 需要进入实时终端完成审批。")
            with self._lock:
                if self._any_running(session_id):
                    raise ApiError(
                        409,
                        "quick_interaction_in_progress",
                        "该会话已有快速交互任务正在执行。",
                    )
            if self.codex_manager.has_active_writer(session.codex_session_id):
                raise ApiError(
                    409,
                    "quick_interaction_writer_active",
                    ACTIVE_WRITER_ERROR,
                )
            self.codex_manager.prepare_quick_interaction()
            if not session.codex_session_id:
                self.codex_manager.set_initial_quick_interaction_title(
                    session.id,
                    self._session_title(prompt),
                )
            with self._lock:
                if self._any_running(session_id):
                    raise ApiError(409, "quick_interaction_in_progress", "该会话已有快速交互任务正在执行。")
                task = QuickInteractionTask(
                    id=str(uuid.uuid4()),
                    session_id=session_id,
                    prompt=prompt,
                    summary=build_task_summary(prompt),
                    weixin_session_slot=weixin_session_slot,
                    weixin_session_title=weixin_session_title,
                    kind=kind,
                    translation_original=translation_original,
                    status="requested",
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
                self._operations[task.id] = (operation_id, source_ip)
                if notification_route is not None:
                    self._notification_routes[task.id] = notification_route
                try:
                    self._write()
                except OSError:
                    self._tasks.pop(task.id, None)
                    self._running_sessions.discard(session_id)
                    self._active_task_ids.discard(task.id)
                    self._task_done_events.pop(task.id, None)
                    self._operations.pop(task.id, None)
                    self._notification_routes.pop(task.id, None)
                    raise
        self._log_status(task.id, "requested", session.id)
        try:
            threading.Thread(
                target=self._run,
                args=(task.id, session, prompt),
                daemon=True,
            ).start()
        except RuntimeError:
            with self._lock:
                self._tasks.pop(task.id, None)
                self._running_sessions.discard(session_id)
                self._active_task_ids.discard(task.id)
                self._task_done_events.pop(task.id, None)
                self._notification_routes.pop(task.id, None)
                self._write()
            self._log_status(task.id, "failed", session.id)
            with self._lock:
                self._operations.pop(task.id, None)
            raise ApiError(
                503,
                "quick_interaction_start_failed",
                "快速交互未能启动。",
            ) from None
        return task

    def weixin_session_ids(self) -> set[str]:
        with self._lock:
            return {
                task.session_id
                for task in self._tasks.values()
                if task.notification_route == "weixin-task"
            }

    def _any_running(self, session_id: str) -> bool:
        return session_id in self._running_sessions

    def _session_lock(self, session_id: str) -> threading.RLock:
        with self._lock:
            return self._session_locks.setdefault(session_id, threading.RLock())

    @contextmanager
    def session_operation_guard(self, session_id: str) -> Iterator[None]:
        with self._session_lock(session_id):
            with self._lock:
                if session_id in self._running_sessions:
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
                if self._any_running(session_id):
                    raise ApiError(
                        409,
                        "quick_interaction_in_progress",
                        "该会话正在执行快速交互，请等待任务结束。",
                    )
            yield

    @contextmanager
    def terminal_access_guard(self, session_id: str) -> Iterator[None]:
        with self._session_lock(session_id):
            with self._lock:
                if session_id in self._running_sessions:
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
                allowed = session_id not in self._running_sessions
            yield allowed

    def get(self, task_id: str) -> QuickInteractionTask:
        with self._lock:
            task = self._tasks.get(task_id)
            if task is None:
                raise ApiError(404, "quick_interaction_not_found", "快速交互任务不存在。")
            return task.model_copy(deep=True)

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
        latest = max(tasks, key=lambda item: (item.created_at, item.id))
        pinned = sorted(
            (
                task
                for task in tasks
                if task.id != latest.id and task.pinned_at is not None
            ),
            key=lambda item: (item.pinned_at, item.created_at, item.id),
            reverse=True,
        )
        ordinary = sorted(
            (
                task
                for task in tasks
                if task.id != latest.id and task.pinned_at is None
            ),
            key=lambda item: (item.created_at, item.id),
            reverse=True,
        )
        return [latest, *pinned, *ordinary]

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
            )

    def set_pinned(
        self,
        session_id: str,
        task_id: str,
        pinned: bool,
    ) -> QuickInteractionTask:
        self.codex_manager.get_session(session_id)
        with self._lock:
            task = self._tasks.get(task_id)
            if task is None or task.session_id != session_id:
                raise ApiError(
                    404,
                    "quick_interaction_not_found",
                    "快速交互任务不存在。",
                )
            if pinned and task.pinned_at is None:
                task.pinned_at = utc_now()
                self._write()
            elif not pinned and task.pinned_at is not None:
                task.pinned_at = None
                self._write()
            return task.model_copy(deep=True)

    def active_sessions(self) -> dict[str, datetime]:
        with self._lock:
            active = set(self._running_sessions)
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

    def is_running(self, session_id: str) -> bool:
        with self._lock:
            return session_id in self._running_sessions

    def has_active_tasks(self) -> bool:
        with self._lock:
            return bool(self._active_task_ids)

    def has_restart_blocking_tasks(self) -> bool:
        with self._lock:
            return any(
                (task := self._tasks.get(task_id)) is None
                or task.kind != "translation"
                for task_id in self._active_task_ids
            )

    def deferred_restart_ready(self) -> bool:
        with self._lock:
            if any(
                (task := self._tasks.get(task_id)) is None
                or task.kind != "translation"
                for task_id in self._active_task_ids
            ):
                return False
            return not any(
                task.notification_status in {"pending", "sending"}
                for task in self._tasks.values()
                if task.kind != "translation"
            )

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
    ) -> None:
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
                    in {"succeeded", "start_failed", "cleared"}
                ):
                    continue
                task.deferred_restart_status = outcome
                task.deferred_restart_updated_at = completed_at
                if (
                    outcome in {"succeeded", "start_failed"}
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

    def _start_deferred_restart_notification(self, task_id: str) -> None:
        threading.Thread(
            target=self._deliver_deferred_restart_notification,
            args=(task_id,),
            daemon=True,
            name=f"chub-restart-notification-{task_id[:8]}",
        ).start()

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

    def cancel_codex_session(self, session_id: str, *, timeout: float = 5) -> bool:
        """Cancel the active Codex CLI interaction and wait for state cleanup."""
        with self._session_lock(session_id):
            with self._lock:
                task = next(
                    (
                        item
                        for item in self._tasks.values()
                        if item.session_id == session_id
                        and item.id in self._active_task_ids
                    ),
                    None,
                )
                if task is None:
                    return False
                self._cancelled_task_ids.add(task.id)
                process = self._processes.get(task.id)
                done = self._task_done_events.get(task.id)
            if process is not None:
                self._kill_process(process)
            if done is None or not done.wait(timeout):
                raise ApiError(
                    503,
                    "quick_interaction_cancel_failed",
                    "快速交互未能在限定时间内停止。",
                )
            return True

    @contextmanager
    def stop_operation_guard(self, session_id: str) -> Iterator[None]:
        """Serialize stop with submit while allowing stop to cancel Codex work."""
        with self._session_lock(session_id):
            yield

    def _is_cancelled(self, task_id: str) -> bool:
        with self._lock:
            return task_id in self._cancelled_task_ids

    def _is_shutting_down(self, task_id: str) -> bool:
        with self._lock:
            return task_id in self._shutdown_task_ids

    def _run(self, task_id: str, session: CodexSession, prompt: str) -> None:
        with self._lock:
            task = self._tasks[task_id]
            task.status = "running"
            task.updated_at = utc_now()
            self._write()
        self._log_status(task_id, "started", session.id)
        result_path = self.result_dir / f"{task_id}.txt"
        error_path = self.result_dir / f"{task_id}.err"
        event_path = self.result_dir / f"{task_id}.jsonl"
        restart_request_path = self.restart_request_dir / f"{task_id}.request"
        creating_session = not session.codex_session_id
        try:
            if self._is_shutting_down(task_id):
                return
            if self._is_cancelled(task_id):
                self._finish(task_id, "cancelled", "已由用户停止。")
                return
            self.codex_manager.set_activity(session.id, "working", "quick")
            self.result_dir.mkdir(parents=True, exist_ok=True)
            try:
                restart_request_path.unlink()
            except FileNotFoundError:
                pass
            command = self._command(session, result_path)
            env = os.environ.copy()
            env["CHUB_PTY_SESSION_ID"] = session.id
            env["CHUB_PTY_HOOK_DIR"] = str(self.codex_manager.hook_dir)
            env["CHUB_ACTIVITY_SOURCE"] = "quick"
            if self.deferred_restart is not None and task.kind != "translation":
                env["CHUB_QUICK_TASK_ID"] = task_id
                env["CHUB_QUICK_RESTART_DIR"] = str(self.restart_request_dir)
            with (
                error_path.open("w", encoding="utf-8") as error_file,
                event_path.open("wb") as event_file,
            ):
                self._set_private_permissions(error_path, event_path)
                process = subprocess.Popen(
                    command,
                    cwd=session.cwd,
                    env=env,
                    stdin=subprocess.PIPE,
                    stdout=event_file,
                    stderr=error_file,
                    start_new_session=True,
                )
                with self._lock:
                    self._processes[task_id] = process
                    cancelled = task_id in self._cancelled_task_ids
                if cancelled:
                    self._kill_process(process)
                process.communicate(
                    input=(
                        prompt
                        if task.kind == "translation"
                        else self._codex_execution_prompt(prompt)
                    ).encode("utf-8"),
                    timeout=self.timeout_seconds,
                )
            self._set_private_permissions(result_path)
            current_session = self.codex_manager.get_session(session.id)
            if self._is_shutting_down(task_id):
                return
            if self._is_cancelled(task_id):
                self._finish(task_id, "cancelled", "已由用户停止。")
            elif process.returncode != 0:
                error = self._json_error(event_path) or self._read_tail(error_path, 2000)
                if self._is_active_writer_error(error):
                    error = ACTIVE_WRITER_ERROR
                self._finish(task_id, "failed", error or "Codex 执行失败。")
                return
            elif creating_session and not current_session.codex_session_id:
                self._finish(
                    task_id,
                    "failed",
                    "Codex 已执行，但未能保存会话标识，请通过实时终端重试。",
                )
                return
            result = self._read_limited(result_path, MAX_RESULT_BYTES)
            if task.kind == "translation" and not self._valid_translation_result(result):
                self._finish(task_id, "failed", "Codex 未返回有效的润色与英文翻译。")
                return
            if (
                task.kind != "translation"
                and restart_request_path.is_file()
                and self.deferred_restart is not None
            ):
                with self._lock:
                    operation = self._operations.get(task_id) or (
                        uuid.uuid4().hex,
                        "unknown",
                    )
                    other_active_count = max(0, len(self._active_task_ids) - 1)
                try:
                    restart_registration = self.deferred_restart.request(
                        operation_id=f"{operation[0]}:restart",
                        task_id=task_id,
                        source_ip=operation[1],
                    )
                except (ApiError, OSError):
                    LOGGER.warning("Unable to register deferred restart", exc_info=True)
                    result = self._append_result_suffix(
                        result,
                        DEFERRED_RESTART_FAILED_SUFFIX,
                    )
                else:
                    restart_operation_id = f"{operation[0]}:restart"
                    with self._lock:
                        task = self._tasks[task_id]
                        task.deferred_restart_status = "pending"
                        task.deferred_restart_updated_at = utc_now()
                        self._deferred_restart_contexts[task_id] = (
                            QuickInteractionDeferredRestartContext(
                                operation_id=restart_operation_id,
                                coordinator_operation_id=(
                                    restart_registration.operation_id
                                ),
                                source_ip=operation[1],
                            )
                        )
                    if not restart_registration.created:
                        write_operation(
                            operation_id=restart_operation_id,
                            action="restart_hub",
                            status="requested",
                            target="chub",
                            source_ip=operation[1],
                        )
                    result = self._append_result_suffix(
                        result,
                        self._deferred_restart_suffix(other_active_count),
                    )
            self._finish(task_id, "succeeded", result or "Codex 未返回最终结果。")
        except subprocess.TimeoutExpired:
            self._kill_process(process)
            if not self._is_shutting_down(task_id):
                self._finish(
                    task_id,
                    "timed_out",
                    f"Codex 已达到配置的执行上限（{self.timeout_seconds} 秒）。",
                )
        except Exception:
            if self._is_shutting_down(task_id):
                return
            if self._is_cancelled(task_id):
                self._finish(task_id, "cancelled", "已由用户停止。")
            else:
                self._finish(task_id, "failed", "快速交互执行失败。")
        finally:
            if creating_session:
                try:
                    self.codex_manager.get_session(session.id)
                except Exception:
                    LOGGER.warning(
                        "Unable to synchronize newly created Codex session",
                        exc_info=True,
                    )
            for path in (
                result_path,
                error_path,
                event_path,
                restart_request_path,
            ):
                try:
                    path.unlink()
                except FileNotFoundError:
                    pass
            with self._lock:
                self._processes.pop(task_id, None)
            finished = self.get(task_id)
            try:
                self.codex_manager.set_activity(
                    session.id,
                    "idle",
                    "none",
                    updated_at=finished.updated_at,
                )
            except Exception:
                LOGGER.warning(
                    "Unable to update Codex session timestamp after quick interaction",
                    exc_info=True,
                )
            finally:
                with self._lock:
                    self._running_sessions.discard(session.id)
                    self._active_task_ids.discard(task_id)
                    self._cancelled_task_ids.discard(task_id)
                    self._shutdown_task_ids.discard(task_id)
                    done = self._task_done_events.pop(task_id, None)
                    if done is not None:
                        done.set()
            self._log_status(task_id, finished.status, session.id)
            with self._lock:
                self._operations.pop(task_id, None)
                try:
                    self._write()
                except OSError:
                    LOGGER.warning(
                        "Unable to prune persisted quick interaction history",
                        exc_info=True,
                    )
            if self.deferred_restart is not None:
                self.deferred_restart.maybe_schedule()

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
    def _command(session: CodexSession, result_path: Path) -> list[str]:
        permission_args = {
            "ask": ['-c', 'default_permissions=":workspace"', '-c', 'approval_policy="on-request"', '-c', 'approvals_reviewer="user"'],
            "auto-review": ['-c', 'default_permissions=":workspace"', '-c', 'approval_policy="on-request"', '-c', 'approvals_reviewer="auto_review"'],
            "read-only": ['-c', 'default_permissions=":read-only"', '-c', 'approval_policy="on-request"', '-c', 'approvals_reviewer="user"'],
            "full-access": ['-c', 'default_permissions=":danger-full-access"', '-c', 'approval_policy="never"'],
        }[session.permission_mode]
        command = [
            "codex", "exec", "--profile", "chub", "--json", *permission_args,
            "--output-last-message", str(result_path),
        ]
        if session.model:
            command.extend(["--model", session.model])
        if session.reasoning_effort:
            command.extend(
                [
                    "-c",
                    f"model_reasoning_effort={json.dumps(session.reasoning_effort)}",
                ]
            )
        if session.codex_session_id:
            command.extend(["resume", session.codex_session_id])
        command.append("-")
        return command

    @staticmethod
    def _read_limited(path: Path, limit: int) -> str:
        with path.open("rb") as file:
            return file.read(limit).decode("utf-8", errors="replace")

    @staticmethod
    def _append_result_suffix(result: str, suffix: str) -> str:
        if result.rstrip().endswith(suffix):
            return result.rstrip()
        suffix_text = f"\n\n{suffix}"
        suffix_bytes = suffix_text.encode("utf-8")
        available = max(0, MAX_RESULT_BYTES - len(suffix_bytes))
        base = result.encode("utf-8")[:available].decode("utf-8", errors="ignore")
        return f"{base.rstrip()}{suffix_text}"

    @staticmethod
    def _deferred_restart_suffix(other_active_count: int) -> str:
        if other_active_count:
            return DEFERRED_RESTART_WAITING_SUFFIX.format(
                count=other_active_count,
            )
        return DEFERRED_RESTART_RESULT_SUFFIX

    @staticmethod
    def _set_private_permissions(*paths: Path) -> None:
        for path in paths:
            try:
                os.chmod(path, 0o600)
            except FileNotFoundError:
                continue

    @staticmethod
    def _read_tail(path: Path, limit: int) -> str:
        with path.open("rb") as file:
            file.seek(0, os.SEEK_END)
            file.seek(max(0, file.tell() - limit))
            return file.read(limit).decode("utf-8", errors="replace")

    @classmethod
    def _json_error(cls, path: Path) -> str:
        try:
            content = cls._read_tail(path, MAX_EVENT_BYTES)
        except OSError:
            return ""
        messages: list[str] = []
        for line in content.splitlines():
            try:
                event = json.loads(line)
            except (json.JSONDecodeError, TypeError):
                continue
            if event.get("type") not in {"error", "turn.failed"}:
                continue
            message = cls._event_message(event)
            if message:
                messages.append(message)
        return (
            redact_log_line(messages[-1], (), max_line_bytes=2000)
            if messages
            else ""
        )

    @staticmethod
    def _is_active_writer_error(message: str) -> bool:
        normalized = message.casefold()
        return (
            "thread-store conflict" in normalized
            and "active writer" in normalized
        )

    @classmethod
    def _event_message(cls, value: object) -> str:
        if isinstance(value, str):
            return value
        if isinstance(value, dict):
            for key in ("message", "error", "detail", "reason"):
                message = cls._event_message(value.get(key))
                if message:
                    return message
        return ""

    def _finish(
        self,
        task_id: str,
        status: str,
        result: str,
    ) -> None:
        notification_operation: tuple[str, str] | None = None
        with self._lock:
            task = self._tasks[task_id]
            task.status = status
            if status == "succeeded":
                task.result = result
            else:
                task.error = result
            task.updated_at = utc_now()
            if (
                self.completion_notifier is not None
                and task.notification_status is None
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

    def _deliver_completion_notification(
        self,
        task_id: str,
        operation: tuple[str, str],
    ) -> None:
        operation_id, source_ip = operation
        notification_operation_id = f"{operation_id}:weixin"
        with self._lock:
            task = self._tasks.get(task_id)
            if task is None or task.notification_status != "pending":
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
        with self._lock:
            operation = self._operations.get(task_id)
            task = self._tasks.get(task_id)
        if operation is None:
            return
        operation_id, source_ip = operation
        write_operation(
            operation_id=operation_id,
            action="quick_interaction",
            status=status,
            target=target,
            source_ip=source_ip,
        )

    def close(self) -> None:
        with self._lock:
            task_ids = set(self._active_task_ids)
            self._shutdown_task_ids.update(task_ids)
            for task_id in task_ids:
                task = self._tasks.get(task_id)
                if task is not None:
                    task.status = "failed"
                    task.error = SERVICE_RESTART_ERROR
                    task.updated_at = utc_now()
            if task_ids:
                self._write()
            processes = list(self._processes.values())
        for process in processes:
            self._kill_process(process)

    async def aclose(self) -> None:
        self.close()

    @staticmethod
    def _kill_process(process: subprocess.Popen[bytes]) -> None:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except OSError:
            try:
                process.kill()
            except OSError:
                return
        try:
            process.wait(timeout=2)
        except (OSError, subprocess.TimeoutExpired):
            pass

    @staticmethod
    def _limit_text(value: str, limit: int) -> str:
        return value.encode("utf-8")[:limit].decode("utf-8", errors="ignore")

    def _write(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if len(self._tasks) > MAX_STORED_TASKS:
            sessions_with_pinned = {
                task.session_id
                for task in self._tasks.values()
                if task.pinned_at is not None
            }
            latest_by_session: dict[str, QuickInteractionTask] = {}
            for task in self._tasks.values():
                if task.session_id not in sessions_with_pinned:
                    continue
                latest = latest_by_session.get(task.session_id)
                if latest is None or (task.created_at, task.id) > (
                    latest.created_at,
                    latest.id,
                ):
                    latest_by_session[task.session_id] = task
            latest_task_ids = {task.id for task in latest_by_session.values()}
            protected = [
                task
                for task in self._tasks.values()
                if (
                    task.status in {"requested", "running"}
                    or task.id in self._active_task_ids
                    or task.deferred_restart_status in {"pending", "started"}
                    or task.deferred_restart_notification_status
                    in {"pending", "sending"}
                    or task.pinned_at is not None
                    or task.id in latest_task_ids
                )
            ]
            completed = sorted(
                (
                    task
                    for task in self._tasks.values()
                    if (
                        task.status not in {"requested", "running"}
                        and task.id not in self._active_task_ids
                        and task.deferred_restart_status not in {"pending", "started"}
                        and task.deferred_restart_notification_status
                        not in {"pending", "sending"}
                        and task.pinned_at is None
                        and task.id not in latest_task_ids
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
            payload.append(serialized)
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        os.chmod(temporary, 0o600)
        temporary.replace(self.path)
