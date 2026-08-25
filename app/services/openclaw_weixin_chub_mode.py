from __future__ import annotations

import hashlib
import json
import logging
import os
import queue
import re
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Callable, Literal
from uuid import uuid4

from app.ai_usage.models import AiUsageData
from app.ai_usage.service import AiUsageService
from app.codex.models import (
    QuickInteractionTask,
    QuickInteractionWeixinRoute,
    SessionRenameRequest,
    sessions_newest_first,
    utc_now,
)
from app.codex.quick_interactions import build_task_summary
from app.codex.rate_limits import CodexRateLimitService
from app.core.config import OpenClawWeixinChubModeConfig, Settings
from app.core.response import ApiError
from app.services.deferred_restart import (
    DeferredRestartCoordinator,
    DeferredRestartOutcome,
    DeferredRestartReadiness,
    DeferredRestartRegistration,
    DeferredRestartRequest,
)
from app.services.operation_log import write_operation
from app.services.openclaw_weixin_chub_commands import (
    FIXED_COMMAND_KINDS,
    command_task_message_id,
    normalize_fixed_prompt,
    parse_weixin_chub_command,
    retry_submission_message_id,
)
from app.services.openclaw_weixin_chub_messages import (
    chub_help_message,
    ChubOverviewRequest,
    ChubOverviewSession,
    build_session_title,
    build_task_name,
    codex_operation_message,
    codex_usage_message,
    compact_token_count,
    detailed_usage_message,
    dispatch_failure,
    dispatch_failure_from_error,
    format_chub_overview,
    format_codex_sessions,
    format_elapsed_time,
    format_fixed_reply,
    format_session_name_line,
    format_session_blocks,
    format_task_context,
    safe_submission_error,
    session_matches_configuration,
    switch_candidate_hint,
    usage_message,
    with_task_summary,
)
from app.services.request_backlog import (
    RequestBacklogBusy,
    RequestBacklogError,
    RequestBacklogNotFound,
    RequestBacklogStore,
)
from app.services.openclaw_weixin_chub_models import (
    MAX_PENDING_RETRY_PROMPT_CHARS,
    MAX_STATE_BYTES,
    MAX_STORED_RESTART_OPERATIONS,
    MAX_STORED_STOP_OPERATIONS,
    MAX_STORED_SUBMISSIONS,
    MAX_WEIXIN_TASK_SUMMARY_CHARS,
    MAX_WEIXIN_SESSION_SLOTS,
    PENDING_RETRY_TTL_MINUTES,
    WEIXIN_RESTART_TASK_PREFIX,
    WeixinChubModeCode,
    WeixinChubModeDispatchCode,
    WeixinChubModeDispatchResult,
    WeixinChubModePendingRetry,
    WeixinChubModeRestartOperation,
    WeixinChubModeRuntimeConfig,
    WeixinChubModeSessionSlot,
    WeixinChubModeState,
    WeixinChubModeStatus,
    WeixinChubModeStopOperation,
    WeixinChubModeSubmission,
    WeixinChubModeSubmissionCode,
    WeixinChubModeSubmissionResult,
)
from app.services.weixin_translation import (
    TranslationEntry,
    TranslationExecutionOutcome,
)
from app.quick_worker import PROTOCOL_VERSION


CODEX_STATUS_TIMEOUT_SECONDS = 9
STOP_TARGET_TIMEOUT_SECONDS = 3
MAX_EPHEMERAL_STATUS_REPLIES = 256
MAX_EPHEMERAL_STATUS_INFLIGHT = 64
LOGGER = logging.getLogger("hub.openclaw.weixin_chub_mode")
FIXED_COMMAND_STATUS_CODES = frozenset(
    {
        "chub_restart_requested",
        "quick_worker_restart_requested",
        "clawbot_restart_requested",
        "chub_slots_synced",
        "codex_retry_checked",
        "codex_session_archived",
        "codex_session_deleted",
        "codex_session_created",
        "codex_session_renamed",
        "codex_session_stopped",
        "codex_switch_checked",
    }
)


@dataclass(frozen=True)
class _ChubSessionSnapshot:
    slot: int
    session_id: str
    title: str
    state: str
    current: bool


@dataclass(frozen=True)
class _ChubCollectedSnapshot:
    value: object
    checked_at: datetime
    successful: bool = True


class WeixinChubModeManager:
    """Own the current Weixin-bound Codex session and inbound deduplication."""

    def __init__(
        self,
        settings: Settings,
        codex_manager,
        quick_interactions,
        route_validator: Callable[[QuickInteractionWeixinRoute], str | None]
        | None = None,
        terminal_reclaimer: Callable[[str], object] | None = None,
        codex_account_reader: CodexRateLimitService | None = None,
        translation_manager=None,
        session_archiver: Callable[[str], object] | None = None,
        session_deleter: Callable[[str], object] | None = None,
        system_status_reader: Callable[[], object] | None = None,
        restart_coordinator: DeferredRestartCoordinator | None = None,
        restart_notifier: Callable[
            [QuickInteractionWeixinRoute, DeferredRestartOutcome, str | None],
            object,
        ]
        | None = None,
        ai_usage_reader: AiUsageService | None = None,
        session_stopper: Callable[[str], object] | None = None,
        session_stop_notifier: Callable[
            [QuickInteractionWeixinRoute, Callable[[], str]], object
        ]
        | None = None,
        translation_result_notifier: Callable[..., object] | None = None,
        translation_confirmation_notifier: Callable[..., object] | None = None,
        worker_health_reader: Callable[[], object] | None = None,
        system_upgrade_starter: Callable[[str], object] | None = None,
        maintenance_command_starter: Callable[
            [str, str, QuickInteractionWeixinRoute, str], object
        ]
        | None = None,
    ) -> None:
        self.settings = settings
        self.codex_manager = codex_manager
        self.quick_interactions = quick_interactions
        self.route_validator = route_validator
        self.terminal_reclaimer = terminal_reclaimer
        self.codex_account_reader = codex_account_reader
        self.ai_usage_reader = ai_usage_reader
        self.translation_manager = translation_manager
        self.session_archiver = session_archiver
        self.session_deleter = session_deleter
        self.session_stopper = session_stopper
        self.session_stop_notifier = session_stop_notifier
        self.translation_result_notifier = translation_result_notifier
        self.translation_confirmation_notifier = translation_confirmation_notifier
        self.worker_health_reader = worker_health_reader
        self.system_status_reader = system_status_reader
        self.restart_coordinator = restart_coordinator
        self.restart_notifier = restart_notifier
        self.system_upgrade_starter = system_upgrade_starter
        self.maintenance_command_starter = maintenance_command_starter
        self.path = settings.openclaw.weixin_chub_mode.state_file
        request_state_file = settings.requests.state_file
        if not request_state_file.is_absolute() and self.path.is_absolute():
            request_state_file = self.path.with_name("requests.json")
        self.request_backlog = RequestBacklogStore(request_state_file)
        self._lock = threading.RLock()
        self._restart_lock = threading.Lock()
        self._text_mode_lock = threading.Lock()
        self._system_upgrade_lock = threading.Lock()
        self._stop_lock = threading.Lock()
        self._slot_lock = threading.RLock()
        self._status_condition = threading.Condition()
        self._status_refreshing = False
        self._status_refresh_succeeded = False
        self._status_refresh_note: str | None = None
        self._status_cache: dict[str, tuple[object, datetime]] = {}
        self._task_status_cache: dict[str, tuple[object, datetime]] = {}
        self._ephemeral_status_replies: dict[str, tuple[str, str | None, float]] = {}
        self._ephemeral_usage_replies: dict[str, tuple[str, str | None, float]] = {}
        self._status_cache_started = False
        self._state_error = False
        self._system_upgrade_reset = False
        self._state = self._load(settings.openclaw.weixin_chub_mode)
        self._mode_enabled = self._state.configuration.enabled
        self._submission_index = {
            item.message_id: item.model_copy(deep=True)
            for item in self._state.submissions
        }

    @staticmethod
    def _bootstrap_config(
        config: OpenClawWeixinChubModeConfig,
    ) -> WeixinChubModeRuntimeConfig:
        return WeixinChubModeRuntimeConfig(
            enabled=config.enabled,
            workspace_id=config.workspace_id,
            permission_mode=config.permission_mode,
            model=config.model,
            reasoning_effort=config.reasoning_effort,
        )

    def _load(self, config: OpenClawWeixinChubModeConfig) -> WeixinChubModeState:
        configured = self._bootstrap_config(config)
        fallback = WeixinChubModeState(configuration=configured)
        try:
            if self.path.is_symlink():
                raise OSError("Weixin Chub mode state must not be a symlink")
            content = self.path.read_bytes()
            if len(content) > MAX_STATE_BYTES:
                raise ValueError("Weixin Chub mode state is too large")
            payload = json.loads(content.decode("utf-8"))
            state = WeixinChubModeState.model_validate(payload)
        except FileNotFoundError:
            return fallback
        except (OSError, UnicodeDecodeError, ValueError, json.JSONDecodeError):
            self._state_error = True
            LOGGER.warning("Weixin Chub mode state is unavailable", exc_info=True)
            return fallback
        try:
            os.chmod(self.path, 0o600)
        except OSError:
            self._state_error = True
            LOGGER.warning(
                "Unable to protect Weixin Chub mode state",
                exc_info=True,
            )
        changed = False
        if state.configuration != configured:
            changed_session_configuration = any(
                getattr(state.configuration, field) != getattr(configured, field)
                for field in (
                    "workspace_id",
                    "permission_mode",
                    "model",
                    "reasoning_effort",
                )
            )
            state.configuration = configured
            if changed_session_configuration:
                state.session_id = None
                state.session_slots = []
            changed = True
        for submission in state.submissions:
            if submission.status == "reserved":
                if submission.continuation_kind == "retry":
                    submission.status = "rejected"
                    submission.code = "submission_interrupted"
                    submission.message = (
                        "Chub does not combine slot selection with retry. Send retry separately."
                    )
                    submission.http_status = 409
                    submission.continuation_kind = None
                    submission.continuation_prompt = None
                    submission.continuation_original_message_id = None
                    submission.updated_at = utc_now()
                    changed = True
                    continue
                if submission.continuation_kind is not None:
                    continue
                submission.status = "rejected"
                submission.code = "submission_interrupted"
                submission.message = "Chub 重启中断了本次提交，请发送一条新消息重试。"
                submission.http_status = 409
                submission.updated_at = utc_now()
                changed = True
            elif submission.status in {"submitted", "passed"} and (
                submission.http_status != 200
            ):
                submission.http_status = 200
                changed = True
        for operation in state.restart_operations:
            if operation.notification_status == "sending":
                operation.notification_status = "failed"
                operation.notification_error = "微信重启结果发送被服务中断，未自动重试。"
                operation.updated_at = utc_now()
                changed = True
        for operation in state.stop_operations:
            if operation.status in {"pending", "started"}:
                operation.status = "failed"
                operation.error = "Chub restart interrupted the Session stop."
                operation.notification_status = "failed"
                operation.notification_error = (
                    "Session stop result delivery was interrupted and was not retried."
                )
                operation.updated_at = utc_now()
                changed = True
            elif operation.notification_status == "sending":
                operation.notification_status = "failed"
                operation.notification_error = (
                    "Session stop result delivery was interrupted and was not retried."
                )
                operation.updated_at = utc_now()
                changed = True
        if state.pending_retry is not None and (
            state.pending_retry.expires_at <= utc_now()
            or (
                state.pending_retry.claimed_by_message_id is not None
            )
        ):
            state.pending_retry = None
            changed = True
        if changed:
            try:
                self._write_state(state)
            except OSError:
                self._state_error = True
                LOGGER.warning(
                    "Unable to recover Weixin Chub mode state",
                    exc_info=True,
                )
        return state

    def status(self) -> WeixinChubModeStatus:
        with self._lock:
            configuration = self._state.configuration.model_copy(deep=True)
            state_error = self._state_error
        if not configuration.enabled:
            return WeixinChubModeStatus(
                enabled=False,
                ready=False,
                code="disabled",
                message="微信 Chub 模式未启用。",
            )
        if state_error:
            return WeixinChubModeStatus(
                enabled=True,
                ready=False,
                code="configuration_invalid",
                message="微信 Chub 模式状态文件不可用。",
            )
        return self._validate_configuration(configuration)

    def _validate_configuration(
        self,
        configuration: WeixinChubModeRuntimeConfig,
    ) -> WeixinChubModeStatus:
        if configuration.permission_mode == "ask":
            return WeixinChubModeStatus(
                enabled=configuration.enabled,
                ready=False,
                code="configuration_invalid",
                message="Ask for approval 不支持微信 Chub 模式。",
            )
        workspace = next(
            (
                item
                for item in self.codex_manager.workspaces()
                if item.id == configuration.workspace_id
            ),
            None,
        )
        if workspace is None or not workspace.available:
            return WeixinChubModeStatus(
                enabled=configuration.enabled,
                ready=False,
                code="configuration_invalid",
                message="固定工作区当前不可用。",
            )
        if not self.codex_manager.available():
            return WeixinChubModeStatus(
                enabled=configuration.enabled,
                ready=False,
                code="codex_unavailable",
                message="Codex 运行依赖当前不可用。",
            )
        try:
            self.codex_manager.validate_model(
                configuration.model,
                configuration.reasoning_effort,
            )
        except ApiError as exc:
            return WeixinChubModeStatus(
                enabled=configuration.enabled,
                ready=False,
                code=(
                    "codex_unavailable"
                    if exc.status_code >= 500
                    else "configuration_invalid"
                ),
                message=(
                    "Codex 模型目录当前不可用。"
                    if exc.status_code >= 500
                    else "所选模型或推理等级当前不可用。"
                ),
            )
        completion = self.settings.openclaw.quick_interaction_completion
        if not completion.enabled:
            return WeixinChubModeStatus(
                enabled=configuration.enabled,
                ready=False,
                code="configuration_invalid",
                message="微信完成通知未启用。",
            )
        return WeixinChubModeStatus(
            enabled=configuration.enabled,
            ready=True,
            code="ready",
            message="微信 Chub 模式已就绪。",
        )

    def configuration(self) -> WeixinChubModeRuntimeConfig:
        with self._lock:
            return self._state.configuration.model_copy(deep=True)

    def session_id(self) -> str | None:
        with self._lock:
            return self._state.session_id

    def update_configuration(
        self,
        configuration: WeixinChubModeRuntimeConfig,
    ) -> tuple[WeixinChubModeRuntimeConfig, bool]:
        with self._slot_lock, self._lock:
            current = self._state.configuration
            changed_session_configuration = any(
                getattr(current, field) != getattr(configuration, field)
                for field in (
                    "workspace_id",
                    "permission_mode",
                    "model",
                    "reasoning_effort",
                )
            )
            if current.enabled and changed_session_configuration:
                raise ApiError(
                    409,
                    "weixin_chub_mode_configuration_locked",
                    "请先关闭微信 Chub 模式，再修改 Session 配置。",
                )
            if changed_session_configuration:
                session_ids = {
                    entry.session_id for entry in self._state.session_slots
                }
                if self._state.session_id:
                    session_ids.add(self._state.session_id)
                if any(
                    self.quick_interactions.is_running(session_id)
                    for session_id in session_ids
                ):
                    raise ApiError(
                        409,
                        "weixin_chub_mode_in_progress",
                        "微信专用任务正在执行，暂时不能修改配置。",
                    )
            if configuration.enabled:
                readiness = self._validate_configuration(configuration)
                if not readiness.ready:
                    raise ApiError(
                        409,
                        f"weixin_chub_mode_{readiness.code}",
                        readiness.message,
                    )
            next_state = self._state.model_copy(deep=True)
            next_state.configuration = configuration.model_copy(deep=True)
            session_reset = changed_session_configuration and bool(next_state.session_id)
            if changed_session_configuration:
                next_state.session_id = None
                next_state.session_slots = []
            self._write_state(next_state)
            self._state = next_state
            self._state_error = False
            return configuration.model_copy(deep=True), session_reset

    def submit(
        self,
        *,
        message_id: str,
        prompt: str,
        correlation_id: str | None,
        source_ip: str,
        delivery_route: QuickInteractionWeixinRoute,
        preprocess: bool = False,
        confirmation_required: bool = False,
        target_session_id: str | None = None,
        retain_busy_retry: bool = True,
        ignore_translation_reservation: bool = False,
    ) -> WeixinChubModeSubmissionResult:
        with self._slot_lock, self._lock:
            if self._state_error:
                raise ApiError(
                    503,
                    "weixin_chub_mode_state_unavailable",
                    "微信 Chub 模式状态文件不可用。",
                )
            route_fingerprint = self._route_fingerprint(delivery_route)
            duplicate = self._find_submission(message_id)
            if duplicate is not None:
                if duplicate.delivery_route_fingerprint != route_fingerprint:
                    raise ApiError(
                        409,
                        "weixin_chub_mode_message_conflict",
                        "同一微信消息标识关联了不同回送路由，已拒绝重复提交。",
                    )
                return self._replay(duplicate)

            operation_id = uuid4().hex
            now = utc_now()
            reservation = WeixinChubModeSubmission(
                message_id=message_id,
                correlation_id=correlation_id,
                operation_id=operation_id,
                delivery_route_fingerprint=route_fingerprint,
                status="reserved",
                code="submission_interrupted",
                message="Chub 未能确认本次提交结果，请发送一条新消息重试。",
                created_at=now,
                updated_at=now,
            )
            next_state = self._state.model_copy(deep=True)
            next_state.submissions.append(reservation)
            target = self.settings.node.id
            self._log(operation_id, "requested", target, source_ip)
            try:
                self._write_state(next_state)
            except OSError:
                self._state_error = True
                self._log(operation_id, "failed", target, source_ip)
                raise ApiError(
                    503,
                    "weixin_chub_mode_state_unavailable",
                    "微信 Chub 模式状态文件不可用。",
                ) from None
            self._state = next_state

            self._log(operation_id, "started", target, source_ip)
            session_id: str | None = None
            try:
                readiness = self.status()
                if not readiness.ready:
                    code: WeixinChubModeSubmissionCode = (
                        "mode_disabled"
                        if readiness.code == "disabled"
                        else readiness.code
                    )
                    self._reject(reservation, code, readiness.message)
                    raise ApiError(
                        409,
                        f"weixin_chub_mode_{code}",
                        readiness.message,
                    )

                if self.route_validator is None:
                    route_error = "微信原路回送校验不可用。"
                else:
                    route_error = self.route_validator(delivery_route)
                if route_error:
                    self._reject(
                        reservation,
                        "delivery_route_invalid",
                        route_error,
                    )
                    raise ApiError(
                        409,
                        "weixin_chub_mode_delivery_route_invalid",
                        route_error,
                    )

                configuration = self._state.configuration
                self._sync_session_slots(configuration, fill_candidates=False)
                if target_session_id is None:
                    session_id, new_session = self._ensure_session(configuration)
                else:
                    session_id = target_session_id
                    new_session = False
                    target_session = self.codex_manager.get_session(session_id)
                    if (
                        self._slot_for_session(session_id) is None
                        or not self._session_matches_configuration(
                            target_session,
                            configuration,
                        )
                    ):
                        raise ApiError(
                            409,
                            "weixin_chub_mode_target_unavailable",
                            "原目标 Session 已不可用，本次任务未执行。",
                        )
                if (
                    not preprocess
                    and
                    self.translation_manager is not None
                    and not ignore_translation_reservation
                    and self.translation_manager.has_active_target(session_id)
                ):
                    self._reject(
                        reservation,
                        "in_progress",
                        "目标 Session 正在处理另一条微信任务，本次任务未提交。",
                        session_id=session_id,
                    )
                    raise ApiError(
                        409,
                        "weixin_chub_mode_in_progress",
                        "目标 Session 正在处理另一条微信任务，本次任务未提交。",
                    )
                if not preprocess and self.quick_interactions.is_running(session_id):
                    if retain_busy_retry:
                        self._reject_busy_with_pending_retry(
                            reservation,
                            prompt=prompt,
                            route_fingerprint=route_fingerprint,
                            session_id=session_id,
                        )
                    else:
                        self._reject(
                            reservation,
                            "in_progress",
                            "目标 Session 正在执行其他任务，本次任务已丢弃。",
                            session_id=session_id,
                        )
                    raise ApiError(
                        409,
                        "weixin_chub_mode_in_progress",
                        (
                            "微信通道当前绑定 Session 正在执行任务，请等待完成。"
                            if retain_busy_retry
                            else "目标 Session 正在执行其他任务，本次任务已丢弃。"
                        ),
                    )
                if preprocess:
                    session = self.codex_manager.get_session(session_id)
                    session_slot = self._slot_for_session(session_id)
                    session_title = build_session_title(
                        session.title or prompt,
                        self.settings.openclaw.weixin_chub_mode.session_name_max_width,
                    )
                    if self.translation_manager is None:
                        raise ApiError(
                            503,
                            "weixin_translation_unavailable",
                            "文本优化服务当前不可用，本次任务未执行。",
                        )
                    enqueue_kwargs = {
                        "message_id": message_id,
                        "original": prompt,
                        "route": delivery_route,
                        "operation_id": operation_id,
                        "source_ip": source_ip,
                        "target_session_id": session_id,
                    }
                    if confirmation_required:
                        enqueue_kwargs["confirmation_required"] = True
                    accepted = self.translation_manager.enqueue(**enqueue_kwargs)
                    if not accepted:
                        raise ApiError(
                            503,
                            "weixin_translation_unavailable",
                            "文本优化任务未能启动，本次任务未执行。",
                        )
                    task = None
                else:
                    with self.quick_interactions.session_operation_guard(session_id):
                        session = self.codex_manager.get_session(session_id)
                        if not new_session and session.activity == "unknown":
                            self._reclaim_unknown_session(
                                session_id,
                                session.native_session_id,
                                operation_id,
                                source_ip,
                            )
                        session_slot = self._slot_for_session(session_id)
                        session_title = build_session_title(
                            session.title or prompt,
                            self.settings.openclaw.weixin_chub_mode.session_name_max_width,
                        )
                        task = self.quick_interactions.submit(
                            session_id,
                            prompt,
                            summary_max_chars=MAX_WEIXIN_TASK_SUMMARY_CHARS,
                            summary_max_width=(
                                self.settings.openclaw.weixin_chub_mode.task_name_max_width
                            ),
                            operation_id=operation_id,
                            source_ip=source_ip,
                            notification_route=delivery_route,
                        )
            except ApiError as exc:
                if reservation.status == "reserved":
                    if (
                        exc.code == "quick_interaction_in_progress"
                        and session_id
                        and retain_busy_retry
                    ):
                        try:
                            self._reject_busy_with_pending_retry(
                                reservation,
                                prompt=prompt,
                                route_fingerprint=route_fingerprint,
                                session_id=session_id,
                            )
                        except OSError:
                            self._state_error = True
                            self._log(operation_id, "failed", target, source_ip)
                            raise ApiError(
                                503,
                                "weixin_chub_mode_state_unavailable",
                                "微信 Chub 模式状态文件不可用。",
                            ) from None
                        self._log(operation_id, "failed", target, source_ip)
                        raise ApiError(
                            409,
                            "weixin_chub_mode_in_progress",
                            "微信通道当前绑定 Session 正在执行任务，请等待完成。",
                        ) from None
                    safe_message = self._safe_submission_error(exc)
                    http_status: Literal[409, 503] = (
                        503 if exc.status_code >= 500 else 409
                    )
                    try:
                        self._reject(
                            reservation,
                            "submission_failed",
                            safe_message,
                            http_status=http_status,
                        )
                    except OSError:
                        self._state_error = True
                        self._log(operation_id, "failed", target, source_ip)
                        raise ApiError(
                            503,
                            "weixin_chub_mode_state_unavailable",
                            "微信 Chub 模式状态文件不可用。",
                        ) from None
                    self._log(operation_id, "failed", target, source_ip)
                    raise ApiError(
                        http_status,
                        "weixin_chub_mode_submission_failed",
                        safe_message,
                    ) from None
                self._log(operation_id, "failed", target, source_ip)
                raise
            except OSError:
                self._state_error = True
                self._log(operation_id, "failed", target, source_ip)
                raise ApiError(
                    503,
                    "weixin_chub_mode_state_unavailable",
                    "微信 Chub 模式状态文件不可用。",
                ) from None
            except Exception:
                LOGGER.warning("Unable to submit Weixin Chub task", exc_info=True)
                if reservation.status == "reserved":
                    try:
                        self._reject(
                            reservation,
                            "submission_failed",
                            "微信任务提交失败。",
                            http_status=503,
                        )
                    except OSError:
                        self._state_error = True
                        self._log(operation_id, "failed", target, source_ip)
                        raise ApiError(
                            503,
                            "weixin_chub_mode_state_unavailable",
                            "微信 Chub 模式状态文件不可用。",
                        ) from None
                self._log(operation_id, "failed", target, source_ip)
                raise ApiError(
                    503,
                    "weixin_chub_mode_submission_failed",
                    "微信任务提交失败。",
                ) from None

            task_summary = (
                getattr(task, "summary", None)
                or build_task_name(
                    prompt,
                    self.settings.openclaw.weixin_chub_mode.task_name_max_width,
                )
            )
            if preprocess:
                reservation.status = "routed"
                reservation.code = "translation_queued"
                reservation.message = format_task_context(
                    "Optimizing · Preparing to submit.",
                    task_summary,
                    session_slot=session_slot,
                    session_title=session_title,
                    current=self._state.session_id == session_id,
                )
                reservation.http_status = 200
                reservation.session_id = session_id
                reservation.new_session = new_session
                reservation.session_slot = session_slot
                reservation.session_title = session_title
                reservation.dispatch_disposition = "handled"
                reservation.updated_at = utc_now()
                self._replace_submission(reservation)
                self._log(operation_id, "succeeded", target, source_ip)
                if new_session:
                    write_operation(
                        operation_id=f"{operation_id}:session",
                        action="weixin_chub_mode_session_created",
                        status="succeeded",
                        target=session_id,
                        source_ip=source_ip,
                    )
                return self._result(
                    reservation,
                    duplicate=False,
                    task_summary=task_summary,
                )
            reservation.status = "submitted"
            reservation.code = "submitted"
            reservation.message = self._format_submitted_task_message(
                "Submitted",
                delivery_route=delivery_route,
                submitted_session_id=session_id,
                submitted_session_slot=session_slot,
                submitted_session_title=session_title,
                submitted_task_summary=task_summary,
            )
            reservation.http_status = 200
            reservation.session_id = session_id
            reservation.task_id = task.id
            reservation.new_session = new_session
            reservation.session_slot = session_slot
            reservation.session_title = session_title
            reservation.updated_at = utc_now()
            try:
                self._replace_submission(reservation)
            except OSError:
                self._state_error = True
                self._log(operation_id, "failed", target, source_ip)
                raise ApiError(
                    503,
                    "weixin_chub_mode_state_unavailable",
                    "微信任务已启动，但 Chub 未能保存提交状态。",
                ) from None
            self._log(operation_id, "succeeded", target, source_ip)
            if new_session:
                write_operation(
                    operation_id=f"{operation_id}:session",
                    action="weixin_chub_mode_session_created",
                    status="succeeded",
                    target=session_id,
                    source_ip=source_ip,
                )
            return self._result(
                reservation,
                duplicate=False,
                task_summary=task_summary,
            )

    def _format_submitted_task_message(
        self,
        status: str,
        *,
        delivery_route: QuickInteractionWeixinRoute,
        submitted_session_id: str,
        submitted_session_slot: int | None,
        submitted_session_title: str,
        submitted_task_summary: str,
    ) -> str:
        task_summaries_by_session = {submitted_session_id: submitted_task_summary}
        try:
            task_snapshot = self.quick_interactions.weixin_task_status_snapshot(
                delivery_route
            )
            task_summaries_by_session.update(
                {
                    session_id: build_task_name(
                        summary,
                        self.settings.openclaw.weixin_chub_mode.task_name_max_width,
                    )
                    for session_id, summary in getattr(
                        task_snapshot, "running_tasks", ()
                    )
                    if session_id != submitted_session_id
                }
            )
        except Exception:
            LOGGER.warning(
                "Unable to snapshot Weixin tasks after submission",
                exc_info=True,
            )

        try:
            snapshots = list(
                self._collect_assigned_session_snapshot(
                    self._state.configuration.model_copy(deep=True),
                    self._state.session_id,
                    [item.model_copy(deep=True) for item in self._state.session_slots],
                )
            )
        except Exception:
            LOGGER.warning(
                "Unable to snapshot Sessions after task submission",
                exc_info=True,
            )
            snapshots = []

        if submitted_session_slot is not None and not any(
            item.session_id == submitted_session_id for item in snapshots
        ):
            snapshots.append(
                _ChubSessionSnapshot(
                    slot=submitted_session_slot,
                    session_id=submitted_session_id,
                    title=submitted_session_title,
                    state="Busy",
                    current=self._state.session_id == submitted_session_id,
                )
            )
        snapshots.sort(key=lambda item: item.slot)

        entries: list[tuple[int, str, str, bool]] = []
        task_summaries_by_slot: dict[int, str] = {}
        for item in snapshots:
            try:
                running = self.quick_interactions.is_running(item.session_id)
            except Exception:
                running = False
            busy = (
                item.session_id == submitted_session_id
                or item.session_id in task_summaries_by_session
                or running
            )
            entries.append(
                (
                    item.slot,
                    item.title,
                    "Busy" if busy else item.state,
                    item.current,
                )
            )
            if item.session_id in task_summaries_by_session:
                task_summaries_by_slot[item.slot] = task_summaries_by_session[
                    item.session_id
                ]

        if not entries:
            return format_task_context(status, submitted_task_summary)
        return f"{status}\n\n{format_session_blocks(entries, task_summaries_by_slot)}"

    def complete_optimized_task(
        self,
        entry: TranslationEntry,
        polished: str | None,
        english: str | None,
        error: str | None,
    ) -> TranslationExecutionOutcome:
        """Submit one persisted optimized draft to its original Session."""
        if entry.target_session_id is None:
            return TranslationExecutionOutcome(
                status="failed",
                error="文本优化任务缺少目标 Session。",
            )
        main_message_id = "optimized-" + hashlib.sha256(
            entry.message_id.encode("utf-8")
        ).hexdigest()
        route_fingerprint = self._route_fingerprint(entry.route)
        with self._lock:
            source = self._find_submission(entry.message_id)
            source_was_submitted = (
                source is not None
                and source.status == "submitted"
                and source.code == "submitted"
                and source.task_id is not None
                and source.session_id == entry.target_session_id
                and source.delivery_route_fingerprint == route_fingerprint
            )
            source_is_active = (
                source is not None
                and source.status == "routed"
                and source.code == "translation_queued"
                and source.session_id == entry.target_session_id
                and source.delivery_route_fingerprint == route_fingerprint
            )
            source_was_interrupted = (
                source is not None
                and source.status in {"reserved", "rejected"}
                and source.code == "submission_interrupted"
                and source.delivery_route_fingerprint == route_fingerprint
            )
        if source_was_submitted:
            return TranslationExecutionOutcome(
                status="submitted",
                main_task_id=source.task_id,
            )
        if not source_is_active:
            reason = "原始提交状态已失效，本次任务未执行。"
            if source_was_interrupted:
                reason = error or (
                    "服务重启中断了文本优化受理，本次任务未执行。"
                )
                self._finish_optimized_source_submission(
                    entry,
                    f"Optimization failed · {reason}",
                    failed=True,
                )
                return TranslationExecutionOutcome(
                    status="failed",
                    error=reason,
                )
            return TranslationExecutionOutcome(
                status="discarded",
                error=reason,
            )
        if error or not polished or not english:
            message = "Optimization failed · The task was not executed."
            self._finish_optimized_source_submission(
                entry,
                message,
                failed=True,
            )
            return TranslationExecutionOutcome(
                status="failed",
                error=error or "文本优化未返回有效结果。",
            )

        if entry.confirmation_required:
            return TranslationExecutionOutcome(status="ready_confirmation")

        try:
            submission = self.submit(
                message_id=main_message_id,
                prompt=polished,
                correlation_id=None,
                source_ip=entry.source_ip,
                delivery_route=entry.route,
                target_session_id=entry.target_session_id,
                retain_busy_retry=False,
                ignore_translation_reservation=True,
            )
        except ApiError as exc:
            reason = self._safe_submission_error(exc)
            if exc.code in {
                "weixin_chub_mode_in_progress",
                "quick_interaction_in_progress",
            }:
                return TranslationExecutionOutcome(
                    status="confirmed_waiting_target",
                    error=reason,
                )
            message = f"Optimized but not submitted · {reason}"
            self._finish_optimized_source_submission(
                entry,
                message,
                failed=True,
            )
            return TranslationExecutionOutcome(
                status="discarded",
                error=reason,
            )

        main_record = self._find_submission(main_message_id)
        self._finish_optimized_source_submission(
            entry,
            self._replace_submission_status(
                submission.message,
                "Optimized and submitted",
            ),
            failed=False,
            main_record=main_record,
        )
        return TranslationExecutionOutcome(
            status="submitted",
            main_task_id=main_record.task_id if main_record is not None else None,
        )

    def _finish_optimized_source_submission(
        self,
        entry: TranslationEntry,
        message: str,
        *,
        failed: bool,
        main_record: WeixinChubModeSubmission | None = None,
    ) -> None:
        with self._slot_lock, self._lock:
            source = self._find_submission(entry.message_id)
            if source is None:
                return
            if source.delivery_route_fingerprint != self._route_fingerprint(entry.route):
                return
            source.status = "rejected" if failed else "submitted"
            source.code = "submission_failed" if failed else "submitted"
            source.message = message[:3000]
            source.http_status = 409 if failed else 200
            source.dispatch_disposition = "handled"
            source.updated_at = utc_now()
            if main_record is not None:
                source.task_id = main_record.task_id
                source.session_id = main_record.session_id
                source.session_slot = main_record.session_slot
                source.session_title = main_record.session_title
            self._replace_submission(source)

    def notify_optimized_task_outcome(
        self,
        entry: TranslationEntry,
    ) -> object:
        if entry.status == "ready_confirmation":
            if self.translation_confirmation_notifier is None:
                return SimpleNamespace(
                    status="failed",
                    error="微信翻译确认通知未配置。",
                )
            if not entry.polished or not entry.english:
                return SimpleNamespace(
                    status="failed",
                    error="翻译确认内容不完整。",
                )
            return self.translation_confirmation_notifier(
                entry.route,
                target_session_id=entry.target_session_id,
                task=entry.polished,
                english=entry.english,
            )
        if self.translation_result_notifier is None:
            return SimpleNamespace(
                status="skipped",
                error="微信文本优化通知未配置。",
            )
        if entry.status == "submitted":
            outcome = "started"
        elif entry.status == "discarded":
            outcome = "not_submitted"
        else:
            outcome = "failed"
        return self.translation_result_notifier(
            entry.route,
            outcome=outcome,
            target_session_id=entry.target_session_id,
            task=entry.polished,
            english=entry.english,
            error=entry.error,
        )

    @staticmethod
    def _replace_submission_status(message: str, status: str) -> str:
        _original_status, separator, remainder = message.partition("\n\n")
        return f"{status}\n\n{remainder}" if separator else status

    def _dispatch_text_confirmation(
        self,
        *,
        message_id: str,
        delivery_route: QuickInteractionWeixinRoute,
        action: Literal["ok", "next", "cancel", "recitation"] = "recitation",
        recitation: str | None = None,
        invalid_usage: bool = False,
    ) -> WeixinChubModeDispatchResult:
        if invalid_usage:
            return WeixinChubModeDispatchResult(
                disposition="reply",
                message="Text confirmation: Usage · text-check <English>.",
            )
        if self.translation_manager is None:
            return WeixinChubModeDispatchResult(
                disposition="reply", message="Text confirmation: Unavailable."
            )
        if self.translation_manager.active_confirmation(delivery_route) is None:
            return WeixinChubModeDispatchResult(
                disposition="reply", message="Text confirmation: No pending translation."
            )
        result = self.translation_manager.confirm(
            message_id=message_id,
            route=delivery_route,
            action=action,
            recitation=recitation,
        )
        if not result.handled:
            return WeixinChubModeDispatchResult(
                disposition="reply", message="Text confirmation: No pending translation."
            )
        if result.action != "submit" or result.entry is None:
            if (
                result.action == "retry"
                and result.entry is not None
                and result.entry.status
                in {"confirmed_waiting_target", "submitted", "discarded", "failed"}
            ):
                # A repeated Weixin delivery must not recreate the removed
                # transient confirmation reply after the durable result has
                # already been accepted for asynchronous submission.
                return WeixinChubModeDispatchResult(disposition="handled")
            message = result.message or "Translation confirmation processed."
            if result.action in {"next", "cancel"}:
                message = f"{message}\n\n{self._text_processing_queue_message(delivery_route)}"
            return WeixinChubModeDispatchResult(
                disposition="reply",
                message=message,
            )
        target_busy = False
        if result.entry.target_session_id is not None:
            try:
                target_busy = self.quick_interactions.is_running(
                    result.entry.target_session_id
                )
            except Exception:
                # The persisted retry handler performs the definitive check.
                pass
        accepted = self.translation_manager.schedule_confirmed_submission_retry(
            delay_seconds=0,
        )
        if accepted:
            # Submission and outbound Started delivery both run after the
            # synchronous Weixin endpoint returns.  The confirmation itself
            # is already durable, so neither a slow Worker nor a slow message
            # send can turn an accepted confirmation into a false timeout.
            if not target_busy:
                return WeixinChubModeDispatchResult(disposition="handled")
            return WeixinChubModeDispatchResult(
                disposition="reply",
                message="Translation confirmed · Waiting for the target session.",
            )
        return WeixinChubModeDispatchResult(
            disposition="reply",
            message="Translation confirmation is temporarily unavailable.",
        )

    def retry_confirmed_optimized_task(
        self,
        entry: TranslationEntry,
    ) -> TranslationExecutionOutcome:
        if entry.target_session_id is None:
            return TranslationExecutionOutcome(
                status="failed", error="翻译确认任务缺少目标 Session。"
            )
        try:
            busy = self.quick_interactions.is_running(entry.target_session_id)
        except Exception:
            busy = True
        if busy:
            return TranslationExecutionOutcome(status="confirmed_waiting_target")
        return self.complete_optimized_task(
            entry.model_copy(update={"confirmation_required": False}),
            entry.polished,
            entry.english,
            None,
        )

    def dispatch(
        self,
        *,
        message_id: str,
        prompt: str,
        message_type: Literal["text", "voice"],
        correlation_id: str | None,
        source_ip: str,
        delivery_route: QuickInteractionWeixinRoute,
    ) -> WeixinChubModeDispatchResult:
        """Route one trusted Weixin message through the single public contract."""
        command = parse_weixin_chub_command(prompt)
        command_task_prompt = (
            prompt
            if command.kind == "normal"
            else command.task_prompt
            if command.kind == "session_slot"
            else None
        )
        mode_enabled = self._mode_enabled
        if mode_enabled and command.kind == "status":
            return self._finalize_fixed_command_result(
                command.kind,
                self._dispatch_chub_status(
                    message_id=message_id,
                    route_fingerprint=self._route_fingerprint(delivery_route),
                    delivery_route=delivery_route,
                ),
                delivery_route,
            )
        if mode_enabled and command.kind == "check":
            return self._finalize_fixed_command_result(
                command.kind,
                self._dispatch_chub_check(
                    message_id=message_id,
                    correlation_id=correlation_id,
                    route_fingerprint=self._route_fingerprint(delivery_route),
                    source_ip=source_ip,
                ),
                delivery_route,
            )
        if mode_enabled and command.kind == "usage":
            return self._dispatch_chub_usage(
                message_id=message_id,
                route_fingerprint=self._route_fingerprint(delivery_route),
            )
        if mode_enabled and command.kind == "text_control":
            return self._finalize_fixed_command_result(
                command.kind,
                self._dispatch_text_control(
                    message_id=message_id,
                    correlation_id=correlation_id,
                    route_fingerprint=self._route_fingerprint(delivery_route),
                    source_ip=source_ip,
                    delivery_route=delivery_route,
                    processing_mode=command.processing_mode,
                    text_action=command.text_action,
                    model_index=command.model_index,
                    level_index=command.level_index,
                    invalid_usage=command.invalid_usage,
                ),
                delivery_route,
            )
        if mode_enabled and command.kind == "text_check":
            return self._finalize_fixed_command_result(
                command.kind,
                self._dispatch_text_confirmation(
                    message_id=message_id,
                    recitation=command.task_prompt,
                    delivery_route=delivery_route,
                    invalid_usage=command.invalid_usage,
                ),
                delivery_route,
            )
        if mode_enabled and command.kind == "help":
            return self._finalize_fixed_command_result(
                command.kind,
                self._dispatch_chub_help(
                    message_id=message_id,
                    correlation_id=correlation_id,
                    route_fingerprint=self._route_fingerprint(delivery_route),
                    source_ip=source_ip,
                    topic=command.task_prompt,
                ),
                delivery_route,
            )
        if mode_enabled and command.kind == "model":
            return self._finalize_fixed_command_result(
                command.kind,
                self._dispatch_codex_model(
                    message_id=message_id,
                    correlation_id=correlation_id,
                    route_fingerprint=self._route_fingerprint(delivery_route),
                    source_ip=source_ip,
                ),
                delivery_route,
            )
        if mode_enabled and command.kind == "model_list":
            return self._finalize_fixed_command_result(
                command.kind,
                self._dispatch_codex_model_list(
                    message_id=message_id,
                    correlation_id=correlation_id,
                    route_fingerprint=self._route_fingerprint(delivery_route),
                    source_ip=source_ip,
                ),
                delivery_route,
            )
        if mode_enabled and command.kind == "model_levels":
            return self._finalize_fixed_command_result(
                command.kind,
                self._dispatch_codex_model_levels(
                    message_id=message_id,
                    correlation_id=correlation_id,
                    route_fingerprint=self._route_fingerprint(delivery_route),
                    source_ip=source_ip,
                    model_index=command.model_index,
                ),
                delivery_route,
            )
        if mode_enabled and command.kind == "model_use":
            return self._finalize_fixed_command_result(
                command.kind,
                self._dispatch_codex_model_use(
                    message_id=message_id,
                    correlation_id=correlation_id,
                    route_fingerprint=self._route_fingerprint(delivery_route),
                    source_ip=source_ip,
                    model_index=command.model_index,
                    level_index=command.level_index,
                ),
                delivery_route,
            )
        if mode_enabled and command.kind == "sync":
            return self._finalize_fixed_command_result(
                command.kind,
                self._dispatch_chub_sync(
                    message_id=message_id,
                    correlation_id=correlation_id,
                    route_fingerprint=self._route_fingerprint(delivery_route),
                    source_ip=source_ip,
                ),
                delivery_route,
            )
        route_fingerprint = self._route_fingerprint(delivery_route)
        ephemeral = self._wait_for_ephemeral_reply(
            message_id,
            route_fingerprint,
        )
        if ephemeral is None:
            ephemeral = self._wait_for_ephemeral_usage_reply(
                message_id,
                route_fingerprint,
            )
        if ephemeral is not None:
            return ephemeral
        if mode_enabled and command.kind == "restart_web":
            return self._finalize_fixed_command_result(
                command.kind,
                self._dispatch_chub_restart(
                    message_id=message_id,
                    correlation_id=correlation_id,
                    route_fingerprint=route_fingerprint,
                    source_ip=source_ip,
                    delivery_route=delivery_route,
                ),
                delivery_route,
            )
        if mode_enabled and command.kind in {"restart_worker", "restart_clawbot"}:
            return self._finalize_fixed_command_result(
                command.kind,
                self._dispatch_maintenance_command(
                    target=command.kind.removeprefix("restart_"),
                    message_id=message_id,
                    correlation_id=correlation_id,
                    route_fingerprint=route_fingerprint,
                    source_ip=source_ip,
                    delivery_route=delivery_route,
                ),
                delivery_route,
            )
        if mode_enabled and command.kind == "upgrade":
            return self._finalize_fixed_command_result(
                command.kind,
                self._dispatch_system_upgrade(
                    message_id=message_id,
                    correlation_id=correlation_id,
                    route_fingerprint=route_fingerprint,
                    source_ip=source_ip,
                ),
                delivery_route,
            )
        with self._slot_lock, self._lock:
            if self._state_error:
                self._log_standalone_dispatch("failed", source_ip)
                result = self._dispatch_failure("state_unavailable")
                if command_task_prompt is not None:
                    result = self._with_failure_task_summary(
                        result,
                        command_task_prompt,
                        include_current_session=False,
                    )
                return self._finalize_fixed_command_result(
                    command.kind,
                    result,
                    delivery_route,
                )

            found_ephemeral, immediate_ephemeral = self._ephemeral_reply_now(
                message_id,
                route_fingerprint,
            )
            if found_ephemeral:
                return immediate_ephemeral
            duplicate = self._find_submission(message_id)
            if duplicate is not None:
                if duplicate.delivery_route_fingerprint != route_fingerprint:
                    self._log_standalone_dispatch("failed", source_ip)
                    result = self._dispatch_failure("message_conflict")
                    if command_task_prompt is not None:
                        result = self._with_failure_task_summary(
                            result,
                            command_task_prompt,
                            include_current_session=False,
                        )
                    return result
                if (
                    duplicate.status == "reserved"
                    and duplicate.continuation_kind is not None
                ):
                    return self._finalize_fixed_command_result(
                        command.kind,
                        self._resume_switch_continuation(
                            duplicate,
                            source_ip=source_ip,
                            delivery_route=delivery_route,
                        ),
                        delivery_route,
                    )
                if duplicate.dispatch_disposition == "handled":
                    self._log_standalone_dispatch("succeeded", source_ip)
                    return WeixinChubModeDispatchResult(
                        disposition="handled",
                    )
                if duplicate.status == "passed":
                    self._log_standalone_dispatch("succeeded", source_ip)
                    return WeixinChubModeDispatchResult(
                        disposition="pass",
                    )
                if duplicate.status == "routed":
                    self._log_standalone_dispatch("succeeded", source_ip)
                    return self._finalize_fixed_command_result(
                        command.kind,
                        WeixinChubModeDispatchResult(
                            disposition=duplicate.dispatch_disposition or "reply",
                            message=self._refresh_replayed_task_context(duplicate),
                        ),
                        delivery_route,
                    )
                try:
                    submission = self.submit(
                        message_id=message_id,
                        prompt=prompt,
                        correlation_id=correlation_id,
                        source_ip=source_ip,
                        delivery_route=delivery_route,
                    )
                except ApiError as exc:
                    self._log_standalone_dispatch("failed", source_ip)
                    result = self._dispatch_failure_from_error(exc)
                    if command_task_prompt is not None:
                        result = self._with_failure_task_summary(
                            result,
                            command_task_prompt,
                            include_current_session=False,
                        )
                    return result
                self._log_standalone_dispatch("succeeded", source_ip)
                return WeixinChubModeDispatchResult(
                    disposition="reply",
                    message=submission.message,
                )

            readiness = self.status()
            if readiness.code == "disabled":
                try:
                    self._remember_passed_dispatch(
                        message_id=message_id,
                        correlation_id=correlation_id,
                        route_fingerprint=route_fingerprint,
                        source_ip=source_ip,
                    )
                except OSError:
                    self._state_error = True
                    return self._dispatch_failure("state_unavailable")
                return WeixinChubModeDispatchResult(
                    disposition="pass",
                )

            if command.kind == "retry":
                return self._finalize_fixed_command_result(
                    command.kind,
                    self._dispatch_codex_retry(
                        message_id=message_id,
                        correlation_id=correlation_id,
                        route_fingerprint=route_fingerprint,
                        source_ip=source_ip,
                        delivery_route=delivery_route,
                        create_new_session=False,
                    ),
                    delivery_route,
                )

            if command.kind == "new":
                result = self._dispatch_codex_new(
                    message_id=message_id,
                    correlation_id=correlation_id,
                    route_fingerprint=route_fingerprint,
                    source_ip=source_ip,
                    delivery_route=delivery_route,
                    title=command.task_prompt,
                )
                return self._finalize_fixed_command_result(
                    command.kind,
                    result,
                    delivery_route,
                )

            if command.kind == "rename":
                return self._finalize_fixed_command_result(
                    command.kind,
                    self._dispatch_codex_rename(
                        message_id=message_id,
                        correlation_id=correlation_id,
                        route_fingerprint=route_fingerprint,
                        source_ip=source_ip,
                        title=command.task_prompt,
                    ),
                    delivery_route,
                )

            if command.kind == "request_cat":
                return self._finalize_fixed_command_result(
                    command.kind,
                    self._dispatch_request_cat(
                        message_id=message_id,
                        correlation_id=correlation_id,
                        route_fingerprint=route_fingerprint,
                        source_ip=source_ip,
                        requested_index=command.requested_index,
                        invalid_usage=command.invalid_usage,
                    ),
                    delivery_route,
                )

            if command.kind == "request_archive":
                return self._finalize_fixed_command_result(
                    command.kind,
                    self._dispatch_request_archive(
                        message_id=message_id,
                        correlation_id=correlation_id,
                        route_fingerprint=route_fingerprint,
                        source_ip=source_ip,
                        requested_index=command.requested_index,
                        invalid_usage=command.invalid_usage,
                    ),
                    delivery_route,
                )

            if command.kind == "request_delete":
                return self._finalize_fixed_command_result(
                    command.kind,
                    self._dispatch_request_delete(
                        message_id=message_id,
                        correlation_id=correlation_id,
                        route_fingerprint=route_fingerprint,
                        source_ip=source_ip,
                        requested_index=command.requested_index,
                        invalid_usage=command.invalid_usage,
                    ),
                    delivery_route,
                )

            if command.kind == "stop":
                return self._finalize_fixed_command_result(
                    command.kind,
                    self._dispatch_codex_stop(
                        message_id=message_id,
                        correlation_id=correlation_id,
                        route_fingerprint=route_fingerprint,
                        source_ip=source_ip,
                        requested_index=command.requested_index,
                        invalid_usage=command.invalid_usage,
                        delivery_route=delivery_route,
                    ),
                    delivery_route,
                )

            if command.kind == "archive":
                return self._finalize_fixed_command_result(
                    command.kind,
                    self._dispatch_codex_archive(
                        message_id=message_id,
                        correlation_id=correlation_id,
                        route_fingerprint=route_fingerprint,
                        source_ip=source_ip,
                        delivery_route=delivery_route,
                        requested_index=command.requested_index,
                        invalid_usage=command.invalid_usage,
                    ),
                    delivery_route,
                )

            if command.kind == "delete":
                return self._finalize_fixed_command_result(
                    command.kind,
                    self._dispatch_codex_delete(
                        message_id=message_id,
                        correlation_id=correlation_id,
                        route_fingerprint=route_fingerprint,
                        source_ip=source_ip,
                        delivery_route=delivery_route,
                        requested_index=command.requested_index,
                        invalid_usage=command.invalid_usage,
                    ),
                    delivery_route,
                )

            if command.kind == "session_slot":
                preprocess_task = False
                confirmation_task = False
                if (
                    command.task_prompt is not None
                    and len(command.task_prompt.strip())
                    <= self.settings.openclaw.weixin_chub_mode.translation_preprocess_max_input_chars
                    and self.translation_manager is not None
                ):
                    try:
                        processing_mode = self.translation_manager.processing_mode()
                        preprocess_task = processing_mode != "direct"
                        confirmation_task = processing_mode == "confirm"
                    except OSError:
                        # The fixed slot selection remains independent. Its follow-up
                        # will fail closed if the optimization queue is down.
                        preprocess_task = True
                result = self._dispatch_codex_switch(
                    message_id=message_id,
                    correlation_id=correlation_id,
                    route_fingerprint=route_fingerprint,
                    source_ip=source_ip,
                    requested_index=command.requested_index,
                    invalid_usage=command.invalid_usage,
                    delivery_route=delivery_route,
                    task_prompt=command.task_prompt,
                    preprocess_task=preprocess_task,
                    confirmation_task=confirmation_task,
                )
                if (
                    command.task_prompt is not None
                    and result.message
                    and not self._has_inline_task_context(result.message)
                ):
                    result = result.model_copy(
                        update={
                            "message": with_task_summary(
                                result.message,
                                command.task_prompt,
                                self.settings.openclaw.weixin_chub_mode.task_name_max_width,
                            )
                        }
                    )
                return self._finalize_fixed_command_result(
                    command.kind,
                    result,
                    delivery_route,
                )

            task_prompt = prompt
            preprocess = False
            confirmation_required = False
            if (
                command.kind == "normal"
                and len(task_prompt.strip())
                <= self.settings.openclaw.weixin_chub_mode.translation_preprocess_max_input_chars
                and self.translation_manager is not None
            ):
                try:
                    processing_mode = self.translation_manager.processing_mode()
                    preprocess = processing_mode != "direct"
                    confirmation_required = processing_mode == "confirm"
                except OSError:
                    return self._with_failure_task_summary(
                        WeixinChubModeDispatchResult(
                            disposition="reply",
                            message=(
                                "Not submitted · Text optimization is unavailable."
                            ),
                        ),
                        task_prompt,
                    )

            try:
                submission = self.submit(
                    message_id=message_id,
                    prompt=task_prompt,
                    correlation_id=correlation_id,
                    source_ip=source_ip,
                    delivery_route=delivery_route,
                    preprocess=preprocess,
                    confirmation_required=confirmation_required,
                )
            except ApiError as exc:
                return self._with_failure_task_summary(
                    self._dispatch_failure_from_error(exc),
                    task_prompt,
                )

            return WeixinChubModeDispatchResult(
                disposition="handled" if preprocess else "reply",
                message=None if preprocess else submission.message,
            )

    def _dispatch_codex_new(
        self,
        *,
        message_id: str,
        correlation_id: str | None,
        route_fingerprint: str,
        source_ip: str,
        delivery_route: QuickInteractionWeixinRoute,
        title: str | None,
    ) -> WeixinChubModeDispatchResult:
        operation_id = uuid4().hex
        self._log_dispatch(operation_id, "requested", source_ip)
        self._log_dispatch(operation_id, "started", source_ip)
        usage = "Usage: new [title] (maximum 48 characters)."
        normalized_title = None
        if title is not None:
            try:
                normalized_title = SessionRenameRequest(title=title).title
            except ValueError:
                return self._remember_fixed_reply(
                    message_id=message_id,
                    correlation_id=correlation_id,
                    operation_id=operation_id,
                    route_fingerprint=route_fingerprint,
                    source_ip=source_ip,
                    message=usage,
                    code="codex_session_created",
                    failed=True,
                )
        now = utc_now()
        reservation = WeixinChubModeSubmission(
            message_id=message_id,
            correlation_id=correlation_id,
            operation_id=operation_id,
            delivery_route_fingerprint=route_fingerprint,
            status="reserved",
            code="submission_interrupted",
            message=(
                "Create: The result could not be confirmed. Send a new message "
                "to try again."
            ),
            created_at=now,
            updated_at=now,
        )
        reserved_state = self._state.model_copy(deep=True)
        reserved_state.submissions.append(reservation)
        try:
            self._write_state(reserved_state)
        except OSError:
            self._state_error = True
            self._log_dispatch(operation_id, "failed", source_ip)
            return self._dispatch_failure("state_unavailable")
        self._state = reserved_state
        try:
            session_id = self._create_session(self._state.configuration)
        except ApiError:
            message = "Create: Failed. Codex could not create a Session."
            message, _status_failed = self._codex_operation_message(message)
            return self._remember_fixed_reply(
                message_id=message_id,
                correlation_id=correlation_id,
                operation_id=operation_id,
                route_fingerprint=route_fingerprint,
                source_ip=source_ip,
                message=message,
                code="codex_session_created",
                failed=True,
            )
        except Exception:
            LOGGER.warning("Unable to create Weixin Codex session", exc_info=True)
            return self._remember_fixed_reply(
                message_id=message_id,
                correlation_id=correlation_id,
                operation_id=operation_id,
                route_fingerprint=route_fingerprint,
                source_ip=source_ip,
                message=(
                    "Create: Failed. The current Session was not changed. "
                    "Try again later."
                ),
                code="codex_session_created",
                failed=True,
            )
        slot = self._slot_for_session(session_id)
        slot_text = f"Session {slot}" if slot is not None else "Session"
        if normalized_title is not None:
            self._log_rename(operation_id, "requested", session_id, source_ip)
            self._log_rename(operation_id, "started", session_id, source_ip)
            try:
                renamed = self.codex_manager.rename_session(
                    session_id,
                    normalized_title,
                )
            except Exception:
                LOGGER.warning("Unable to name new Weixin Codex session", exc_info=True)
                self._log_rename(operation_id, "failed", session_id, source_ip)
                message, _status_failed = self._codex_operation_message(
                    f"Create: {slot_text} was created and selected, but its title "
                    "could not be set. Send rename <title> to try again."
                )
                return self._remember_fixed_reply(
                    message_id=message_id,
                    correlation_id=correlation_id,
                    operation_id=operation_id,
                    route_fingerprint=route_fingerprint,
                    source_ip=source_ip,
                    message=message,
                    code="codex_session_created",
                    session_id=session_id,
                    failed=True,
                )
            self._log_rename(operation_id, "succeeded", session_id, source_ip)
            renamed_title = renamed.title or normalized_title
        else:
            renamed_title = "Unnamed Session"
        message, _status_failed = self._codex_operation_message(
            f'Create: {slot_text} "{renamed_title}" was created and selected.'
        )
        return self._remember_fixed_reply(
            message_id=message_id,
            correlation_id=correlation_id,
            operation_id=operation_id,
            route_fingerprint=route_fingerprint,
            source_ip=source_ip,
            message=message,
            code="codex_session_created",
            session_id=session_id,
            failed=False,
        )

    def _dispatch_codex_retry(
        self,
        *,
        message_id: str,
        correlation_id: str | None,
        route_fingerprint: str,
        source_ip: str,
        delivery_route: QuickInteractionWeixinRoute,
        create_new_session: bool,
        requested_index: int | None = None,
    ) -> WeixinChubModeDispatchResult:
        command_operation_id = uuid4().hex
        self._log_dispatch(command_operation_id, "requested", source_ip)
        self._log_dispatch(command_operation_id, "started", source_ip)
        pending = self._available_pending_retry(route_fingerprint)
        if pending is None:
            return self._remember_fixed_reply(
                message_id=message_id,
                correlation_id=correlation_id,
                operation_id=command_operation_id,
                route_fingerprint=route_fingerprint,
                source_ip=source_ip,
                message="Retry: No task is waiting to be continued. Send the task again.",
                code="codex_retry_checked",
            )

        retry_target: tuple[str, int, str] | None = None
        if requested_index is not None:

            def target_failure(
                message: str,
                *,
                session_id: str | None = None,
                session_slot: int | None = None,
                session_title: str | None = None,
            ) -> WeixinChubModeDispatchResult:
                return self._remember_fixed_reply(
                    message_id=message_id,
                    correlation_id=correlation_id,
                    operation_id=command_operation_id,
                    route_fingerprint=route_fingerprint,
                    source_ip=source_ip,
                    message=with_task_summary(
                        message,
                        pending.prompt,
                        self.settings.openclaw.weixin_chub_mode.task_name_max_width,
                        session_slot=session_slot,
                        session_title=session_title,
                        current=(
                            session_id is not None
                            and session_id == self._state.session_id
                        ),
                    ),
                    code="codex_retry_checked",
                    failed=True,
                )

            try:
                configuration = self._state.configuration.model_copy(deep=True)
                visible, _remaining = self._read_visible_codex_sessions(
                    configuration,
                    fill_candidates=False,
                )
            except Exception:
                LOGGER.warning("Unable to find Weixin retry target", exc_info=True)
                return target_failure(
                    "Retry: Not started because the Session list is unavailable."
                )
            target_entry = next(
                (entry for entry in visible if entry[0] == requested_index),
                None,
            )
            if target_entry is None:
                return target_failure(
                    "Retry: Not started because the Session number is invalid."
                )
            target_slot, target, listed_state = target_entry
            target_title = build_session_title(
                getattr(target, "title", None) or "Unnamed Session",
                self.settings.openclaw.weixin_chub_mode.session_name_max_width,
            )
            if listed_state != "Available":
                reason = "running" if listed_state == "Busy" else "unavailable"
                return target_failure(
                    f"Retry: Not started because the target Session is {reason}.",
                    session_id=target.id,
                    session_slot=target_slot,
                    session_title=target_title,
                )
            try:
                refreshed = self.codex_manager.get_session(target.id)
                if (
                    refreshed.id != target.id
                    or not self._session_matches_configuration(
                        refreshed,
                        configuration,
                    )
                    or self._codex_session_dispatch_state(refreshed) != "Available"
                ):
                    raise ValueError("Session became unavailable")
            except Exception:
                LOGGER.info("Weixin retry target is no longer available", exc_info=True)
                return target_failure(
                    "Retry: Not started because the target Session is unavailable.",
                    session_id=target.id,
                    session_slot=target_slot,
                    session_title=target_title,
                )
            retry_target = (refreshed.id, target_slot, target_title)

        now = utc_now()
        command_reservation = WeixinChubModeSubmission(
            message_id=message_id,
            correlation_id=correlation_id,
            operation_id=command_operation_id,
            delivery_route_fingerprint=route_fingerprint,
            status="reserved",
            code="submission_interrupted",
            message=(
                "Retry: The result could not be confirmed. Send the command again."
            ),
            created_at=now,
            updated_at=now,
        )
        reserved_state = self._state.model_copy(deep=True)
        reserved_state.submissions.append(command_reservation)
        try:
            self._write_state(reserved_state)
        except OSError:
            self._state_error = True
            self._log_dispatch(command_operation_id, "failed", source_ip)
            return self._with_failure_task_summary(
                self._dispatch_failure("state_unavailable"),
                pending.prompt,
            )
        self._state = reserved_state

        claimed_state = self._state.model_copy(deep=True)
        if claimed_state.pending_retry is None:
            return self._with_failure_task_summary(
                self._dispatch_failure("state_unavailable"),
                pending.prompt,
            )
        claimed_state.pending_retry.claimed_by_message_id = message_id
        if retry_target is not None:
            claimed_state.session_id = retry_target[0]
        try:
            self._write_state(claimed_state)
        except OSError:
            self._state_error = True
            self._log_dispatch(command_operation_id, "failed", source_ip)
            return self._with_failure_task_summary(
                self._dispatch_failure("state_unavailable"),
                pending.prompt,
            )
        self._state = claimed_state

        if create_new_session:
            try:
                self._create_session(self._state.configuration)
            except Exception:
                LOGGER.warning(
                    "Unable to create a session for pending Weixin task",
                    exc_info=True,
                )
                self._release_pending_retry(message_id)
                pending_summary = build_task_name(
                    pending.prompt,
                    self.settings.openclaw.weixin_chub_mode.task_name_max_width,
                )
                return self._remember_fixed_reply(
                    message_id=message_id,
                    correlation_id=correlation_id,
                    operation_id=command_operation_id,
                    route_fingerprint=route_fingerprint,
                    source_ip=source_ip,
                    message=(
                        "Retry: A new Session could not be created. The current "
                        "Session was not changed and the pending task was kept.\n\n"
                        f"Task · {pending_summary}"
                    ),
                    code="codex_retry_checked",
                    failed=True,
                )

        try:
            submission = self.submit(
                message_id=self._retry_submission_message_id(
                    message_id,
                    pending.original_message_id,
                ),
                prompt=pending.prompt,
                correlation_id=correlation_id,
                source_ip=source_ip,
                delivery_route=delivery_route,
            )
        except ApiError as exc:
            self._release_pending_retry(message_id)
            self._log_dispatch(command_operation_id, "failed", source_ip)
            result = self._dispatch_failure_from_error(exc)
            if create_new_session:
                result.message = (
                    "Retry: A new Session was created and selected, but the task "
                    "was not submitted. The pending task was kept. Send retry."
                )
            else:
                result.message = (
                    "Retry: The task was not resubmitted.\n\n"
                    f"{result.message or 'Not submitted · Submission failed. Try again later.'}"
                )
            task_slot, task_title = self._task_session_context()
            if result.message:
                result.message = with_task_summary(
                    result.message,
                    pending.prompt,
                    self.settings.openclaw.weixin_chub_mode.task_name_max_width,
                    session_slot=task_slot,
                    session_title=task_title,
                    current=task_slot is not None,
                )
            return self._finish_retry_command(
                command_reservation,
                result.message or "Not submitted · Submission failed. Try again later.",
                source_ip=source_ip,
                failed=True,
                task_session_id=self._state.session_id,
                task_session_slot=task_slot,
                task_session_title=task_title,
            )

        cleared_state = self._state.model_copy(deep=True)
        if (
            cleared_state.pending_retry is not None
            and cleared_state.pending_retry.claimed_by_message_id == message_id
        ):
            cleared_state.pending_retry = None
            try:
                self._write_state(cleared_state)
            except OSError:
                self._state_error = True
                self._log_dispatch(command_operation_id, "failed", source_ip)
                return self._dispatch_failure("state_unavailable")
            self._state = cleared_state
        prefix = (
            "Retry: A new Session was created and selected. The task was resubmitted."
            if create_new_session
            else (
                f"Retry: Session {retry_target[1]} selected. The task was resubmitted."
                if retry_target is not None
                else "Retry: The task was resubmitted."
            )
        )
        return self._finish_retry_command(
            command_reservation,
            self._replace_submission_status(
                submission.message,
                prefix,
            ),
            source_ip=source_ip,
            task_session_id=self._state.session_id,
            task_session_slot=submission.session_slot,
            task_session_title=submission.session_title,
        )

    def _finish_retry_command(
        self,
        record: WeixinChubModeSubmission,
        message: str,
        *,
        source_ip: str,
        failed: bool = False,
        task_session_id: str | None = None,
        task_session_slot: int | None = None,
        task_session_title: str | None = None,
    ) -> WeixinChubModeDispatchResult:
        if not self._has_inline_task_context(message):
            message = self._with_command_status_suffix(message)
        record.status = "routed"
        record.code = "codex_retry_checked"
        record.message = message
        record.http_status = 200
        if task_session_id is not None:
            record.session_id = task_session_id
            record.session_slot = task_session_slot
            record.session_title = task_session_title
        record.dispatch_disposition = "reply"
        record.updated_at = utc_now()
        try:
            self._replace_submission(record)
        except OSError:
            self._log_dispatch(record.operation_id, "failed", source_ip)
            return self._dispatch_failure("state_unavailable")
        self._log_dispatch(
            record.operation_id,
            "failed" if failed else "succeeded",
            source_ip,
        )
        return WeixinChubModeDispatchResult(disposition="reply", message=message)

    _retry_submission_message_id = staticmethod(retry_submission_message_id)
    _command_task_message_id = staticmethod(command_task_message_id)
    def _available_pending_retry(
        self,
        route_fingerprint: str,
    ) -> WeixinChubModePendingRetry | None:
        pending = self._state.pending_retry
        if pending is None:
            return None
        if pending.expires_at <= utc_now():
            next_state = self._state.model_copy(deep=True)
            next_state.pending_retry = None
            try:
                self._write_state(next_state)
            except OSError:
                self._state_error = True
                return None
            self._state = next_state
            return None
        if (
            pending.delivery_route_fingerprint != route_fingerprint
            or pending.claimed_by_message_id is not None
        ):
            return None
        return pending.model_copy(deep=True)

    def _release_pending_retry(self, message_id: str) -> None:
        pending = self._state.pending_retry
        if pending is None or pending.claimed_by_message_id != message_id:
            return
        next_state = self._state.model_copy(deep=True)
        if next_state.pending_retry is not None:
            next_state.pending_retry.claimed_by_message_id = None
            next_state.pending_retry.claimed_session_id = None
        try:
            self._write_state(next_state)
        except OSError:
            self._state_error = True
            return
        self._state = next_state

    def _remember_fixed_reply(
        self,
        *,
        message_id: str,
        correlation_id: str | None,
        operation_id: str,
        route_fingerprint: str,
        source_ip: str,
        message: str,
        code: WeixinChubModeSubmissionCode,
        session_id: str | None = None,
        failed: bool = False,
        pending: bool = False,
        failed_task_prompt: str | None = None,
        task_session_slot: int | None = None,
        task_session_title: str | None = None,
        task_session_current: bool = False,
    ) -> WeixinChubModeDispatchResult:
        if failed and failed_task_prompt is not None:
            message = with_task_summary(
                message,
                failed_task_prompt,
                self.settings.openclaw.weixin_chub_mode.task_name_max_width,
                session_slot=task_session_slot,
                session_title=task_session_title,
                current=task_session_current,
            )
        if code in FIXED_COMMAND_STATUS_CODES:
            if (
                not self._has_inline_task_context(message)
                and not (
                    code
                    in {
                        "chub_restart_requested",
                        "quick_worker_restart_requested",
                        "clawbot_restart_requested",
                    }
                    and message.startswith("Restart")
                    and "Scheduled." in message
                )
            ):
                message = self._with_command_status_suffix(message)
        now = utc_now()
        record = WeixinChubModeSubmission(
            message_id=message_id,
            correlation_id=correlation_id,
            operation_id=operation_id,
            delivery_route_fingerprint=route_fingerprint,
            status="routed",
            code=code,
            message=message,
            http_status=200,
            session_id=session_id,
            session_slot=task_session_slot,
            session_title=task_session_title,
            dispatch_disposition="reply",
            created_at=now,
            updated_at=now,
        )
        next_state = self._state.model_copy(deep=True)
        next_state.submissions = [
            item
            for item in next_state.submissions
            if item.message_id != message_id
        ]
        next_state.submissions.append(record)
        try:
            self._write_state(next_state)
        except OSError:
            self._state_error = True
            self._log_dispatch(operation_id, "failed", source_ip)
            result = self._dispatch_failure("state_unavailable")
            if code in FIXED_COMMAND_STATUS_CODES and result.message:
                result.message = self._with_command_status_suffix(result.message)
            return result
        self._state = next_state
        self._log_dispatch(
            operation_id,
            "failed" if failed else ("started" if pending else "succeeded"),
            source_ip,
        )
        if code in {
            "codex_session_created",
            "codex_session_archived",
            "codex_session_deleted",
            "codex_session_renamed",
            "codex_session_stopped",
            "codex_switch_checked",
        }:
            self._schedule_session_snapshot_refresh()
        return WeixinChubModeDispatchResult(disposition="reply", message=message)

    def _dispatch_chub_restart(
        self,
        *,
        message_id: str,
        correlation_id: str | None,
        route_fingerprint: str,
        source_ip: str,
        delivery_route: QuickInteractionWeixinRoute,
    ) -> WeixinChubModeDispatchResult:
        with self._restart_lock:
            return self._dispatch_chub_restart_serialized(
                message_id=message_id,
                correlation_id=correlation_id,
                route_fingerprint=route_fingerprint,
                source_ip=source_ip,
                delivery_route=delivery_route,
            )

    def _dispatch_maintenance_command(
        self,
        *,
        target: str,
        message_id: str,
        correlation_id: str | None,
        route_fingerprint: str,
        source_ip: str,
        delivery_route: QuickInteractionWeixinRoute,
    ) -> WeixinChubModeDispatchResult:
        """Start a fixed Worker/ClawBot maintenance operation after dispatch returns."""
        operation_id = uuid4().hex
        code = (
            "quick_worker_restart_requested"
            if target == "worker"
            else "clawbot_restart_requested"
        )
        labels = {"worker": "Worker", "clawbot": "ClawBot"}
        label = labels.get(target, target)
        with self._lock:
            if self._state_error:
                self._log_standalone_dispatch("failed", source_ip)
                return self._dispatch_failure("state_unavailable")
            duplicate = self._find_submission(message_id)
            if duplicate is not None:
                if duplicate.delivery_route_fingerprint != route_fingerprint:
                    self._log_standalone_dispatch("failed", source_ip)
                    return self._dispatch_failure("message_conflict")
                self._log_standalone_dispatch("succeeded", source_ip)
                return WeixinChubModeDispatchResult(
                    disposition=duplicate.dispatch_disposition or "reply",
                    message=duplicate.message or None,
                )
        self._log_dispatch(operation_id, "requested", source_ip)
        if self.maintenance_command_starter is None:
            return self._remember_fixed_reply(
                message_id=message_id,
                correlation_id=correlation_id,
                operation_id=operation_id,
                route_fingerprint=route_fingerprint,
                source_ip=source_ip,
                message=f"Restart {label}: Unavailable. Try again later.",
                code=code,
                failed=True,
            )
        try:
            result = self.maintenance_command_starter(
                target,
                operation_id,
                delivery_route,
                source_ip,
            )
            message = getattr(result, "message", None) or str(result or "")
            if not message:
                message = (
                    f"Restart {label}: Scheduled. The result will be sent when completed."
                )
            return self._remember_fixed_reply(
                message_id=message_id,
                correlation_id=correlation_id,
                operation_id=operation_id,
                route_fingerprint=route_fingerprint,
                source_ip=source_ip,
                message=message,
                code=code,
                pending=True,
            )
        except ApiError as exc:
            return self._remember_fixed_reply(
                message_id=message_id,
                correlation_id=correlation_id,
                operation_id=operation_id,
                route_fingerprint=route_fingerprint,
                source_ip=source_ip,
                message=f"Restart {label}: Not started · {exc.message}",
                code=code,
                failed=True,
            )
        except Exception:
            LOGGER.warning("Unable to start Weixin maintenance command", exc_info=True)
            return self._remember_fixed_reply(
                message_id=message_id,
                correlation_id=correlation_id,
                operation_id=operation_id,
                route_fingerprint=route_fingerprint,
                source_ip=source_ip,
                message=f"Restart {label}: Unavailable. Try again later.",
                code=code,
                failed=True,
            )

    @staticmethod
    def _system_upgrade_message(data: object, *, started: bool = False) -> str:
        state = getattr(data, "state", "unknown")
        message = getattr(data, "message", "")
        if started:
            if state in {"preparing", "draining", "cleaning", "restarting"}:
                return "Upgrade: Started. The final result will be sent when completed."
            if state == "succeeded":
                return "Upgrade: Already completed."
        labels = {
            "idle": "No upgrade plan",
            "available": "Ready",
            "blocked": "Blocked",
            "preparing": "In progress",
            "draining": "In progress",
            "cleaning": "In progress",
            "restarting": "In progress",
            "succeeded": "Completed",
            "failed": "Failed",
        }
        label = labels.get(state, "Unavailable")
        detail = message.strip() if isinstance(message, str) else ""
        return f"Upgrade: {label}" + (f" · {detail}" if detail else ".")

    def _dispatch_system_upgrade(
        self,
        *,
        message_id: str,
        correlation_id: str | None,
        route_fingerprint: str,
        source_ip: str,
    ) -> WeixinChubModeDispatchResult:
        return self._dispatch_system_upgrade_command(
            message_id=message_id,
            correlation_id=correlation_id,
            route_fingerprint=route_fingerprint,
            source_ip=source_ip,
        )

    def _dispatch_system_upgrade_command(
        self,
        *,
        message_id: str,
        correlation_id: str | None,
        route_fingerprint: str,
        source_ip: str,
    ) -> WeixinChubModeDispatchResult:
        """Persist an idempotent fixed reply around the shared upgrade service."""
        with self._system_upgrade_lock, self._lock:
            if self._state_error:
                self._log_standalone_dispatch("failed", source_ip)
                return self._dispatch_failure("state_unavailable")
            duplicate = self._find_submission(message_id)
            if duplicate is not None:
                if duplicate.delivery_route_fingerprint != route_fingerprint:
                    self._log_standalone_dispatch("failed", source_ip)
                    return self._dispatch_failure("message_conflict")
                self._log_standalone_dispatch("succeeded", source_ip)
                return WeixinChubModeDispatchResult(
                    disposition=duplicate.dispatch_disposition or "reply",
                    message=duplicate.message or None,
                )

            operation_id = uuid4().hex
            self._log_dispatch(operation_id, "requested", source_ip)
            self._log_dispatch(operation_id, "started", source_ip)
            reader = self.system_upgrade_starter
            if reader is None:
                message = "Upgrade: Unavailable."
                failed = True
            else:
                try:
                    data = reader(source_ip)
                    message = self._system_upgrade_message(data, started=True)
                    failed = False
                except ApiError as exc:
                    message = f"Upgrade: Not started · {exc.message}"
                    failed = True
                except Exception:
                    LOGGER.warning("Unable to handle Weixin system upgrade command", exc_info=True)
                    message = "Upgrade: Unavailable. Try again later."
                    failed = True
            return self._remember_fixed_reply(
                message_id=message_id,
                correlation_id=correlation_id,
                operation_id=operation_id,
                route_fingerprint=route_fingerprint,
                source_ip=source_ip,
                message=message,
                code="system_upgrade_requested",
                failed=failed,
            )

    def _dispatch_chub_restart_serialized(
        self,
        *,
        message_id: str,
        correlation_id: str | None,
        route_fingerprint: str,
        source_ip: str,
        delivery_route: QuickInteractionWeixinRoute,
    ) -> WeixinChubModeDispatchResult:
        with self._lock:
            if self._state_error:
                self._log_standalone_dispatch("failed", source_ip)
                return self._dispatch_failure("state_unavailable")
            duplicate = self._find_submission(message_id)
            if duplicate is not None:
                if duplicate.delivery_route_fingerprint != route_fingerprint:
                    self._log_standalone_dispatch("failed", source_ip)
                    return self._dispatch_failure("message_conflict")
                self._log_standalone_dispatch("succeeded", source_ip)
                return WeixinChubModeDispatchResult(
                    disposition=duplicate.dispatch_disposition or "reply",
                    message=duplicate.message or None,
                )

        operation_id = uuid4().hex
        self._log_dispatch(operation_id, "requested", source_ip)
        self._log_dispatch(operation_id, "started", source_ip)

        def remember(
            message: str,
            *,
            failed: bool = False,
        ) -> WeixinChubModeDispatchResult:
            with self._lock:
                return self._remember_fixed_reply(
                    message_id=message_id,
                    correlation_id=correlation_id,
                    operation_id=operation_id,
                    route_fingerprint=route_fingerprint,
                    source_ip=source_ip,
                    message=message,
                    code="chub_restart_requested",
                    failed=failed,
                )

        if self.restart_coordinator is None or self.restart_notifier is None:
            return remember(
                "Restart: Not scheduled because restart coordination is unavailable.",
                failed=True,
            )
        try:
            route_error = (
                self.route_validator(delivery_route)
                if self.route_validator is not None
                else "unavailable"
            )
        except Exception:
            LOGGER.warning("Unable to validate Weixin restart route", exc_info=True)
            route_error = "unavailable"
        if route_error:
            return remember(
                "Restart: Not scheduled because the reply route is unavailable.",
                failed=True,
            )

        self._clear_orphaned_restart_operations()
        with self._lock:
            active = next(
                (
                    item
                    for item in self._state.restart_operations
                    if item.delivery_route_fingerprint == route_fingerprint
                    and item.status in {"pending", "started"}
                ),
                None,
            )
        if active is not None:
            return remember(
                "Restart: Already in progress. The result will be sent when completed."
            )

        restart_operation_id = f"{operation_id}:restart"
        now = utc_now()
        restart_operation = WeixinChubModeRestartOperation(
            message_id=message_id,
            operation_id=restart_operation_id,
            coordinator_operation_id=restart_operation_id,
            source_ip=source_ip,
            delivery_route_fingerprint=route_fingerprint,
            delivery_route=delivery_route.model_copy(deep=True),
            created_at=now,
            updated_at=now,
        )
        with self._lock:
            next_state = self._state.model_copy(deep=True)
            next_state.restart_operations.append(restart_operation)
            try:
                self._write_state(next_state)
            except OSError:
                self._state_error = True
                self._log_dispatch(operation_id, "failed", source_ip)
                return self._dispatch_failure("state_unavailable")
            self._state = next_state

        def bind_coordinator_operation(
            registration: DeferredRestartRegistration,
        ) -> None:
            if registration.operation_id == restart_operation_id:
                return
            with self._lock:
                next_state = self._state.model_copy(deep=True)
                operation = next(
                    item
                    for item in next_state.restart_operations
                    if item.operation_id == restart_operation_id
                )
                operation.coordinator_operation_id = registration.operation_id
                operation.updated_at = utc_now()
                self._write_state(next_state)
                self._state = next_state
        try:
            registration = self.restart_coordinator.request(
                operation_id=restart_operation_id,
                task_id=f"{WEIXIN_RESTART_TASK_PREFIX}{operation_id}",
                source_ip=source_ip,
                registration_handler=bind_coordinator_operation,
            )
        except (ApiError, OSError):
            LOGGER.warning("Unable to register Weixin Chub restart", exc_info=True)
            registration_error = "延迟重启请求未能登记，后续可重新发起。"
            self._finish_unregistered_restart_operation(
                restart_operation_id,
                registration_error,
            )
            write_operation(
                operation_id=restart_operation_id,
                action="restart_hub",
                status="failed",
                target="chub",
                source_ip=source_ip,
                reason=registration_error,
            )
            return remember(
                "Restart: Not scheduled. Try again later.",
                failed=True,
            )

        if not registration.created:
            write_operation(
                operation_id=restart_operation_id,
                action="restart_hub",
                status="requested",
                target="chub",
                source_ip=source_ip,
            )

        reply_message = (
            "Restart: Scheduled. The result will be sent when completed."
        )
        result = remember(reply_message)
        if result.message and result.message.startswith(reply_message):
            self.restart_coordinator.maybe_schedule()
        return result

    def _clear_orphaned_restart_operations(self) -> None:
        """Finish stale Weixin records after their coordinator has already ended."""
        if self.restart_coordinator is None:
            return
        try:
            coordinator_state = self.restart_coordinator.state()
        except Exception:
            LOGGER.warning("Unable to read deferred restart state", exc_info=True)
            return
        if coordinator_state is not None:
            return
        with self._lock:
            orphaned = [
                item
                for item in self._state.restart_operations
                if item.status in {"pending", "started"}
            ]
            if not orphaned:
                return
            next_state = self._state.model_copy(deep=True)
            for item in next_state.restart_operations:
                if item.status in {"pending", "started"}:
                    item.status = "sensitive_task_failed"
                    item.error = "重启协调器已结束，已解除过期重启状态。"
                    item.updated_at = utc_now()
            try:
                self._write_state(next_state)
            except OSError:
                self._state_error = True
                LOGGER.warning("Unable to clear stale Weixin restart state", exc_info=True)
                return
            self._state = next_state

    def _finish_unregistered_restart_operation(
        self,
        operation_id: str,
        reason: str,
    ) -> None:
        with self._lock:
            next_state = self._state.model_copy(deep=True)
            operation = next(
                (
                    item
                    for item in next_state.restart_operations
                    if item.operation_id == operation_id
                    and item.status in {"pending", "started"}
                ),
                None,
            )
            if operation is None:
                return
            operation.status = "start_failed"
            operation.error = reason
            operation.updated_at = utc_now()
            try:
                self._write_state(next_state)
            except OSError:
                self._state_error = True
                LOGGER.warning("Unable to finish unregistered Weixin restart", exc_info=True)
                return
            self._state = next_state

    def _dispatch_chub_help(
        self,
        *,
        message_id: str,
        correlation_id: str | None,
        route_fingerprint: str,
        source_ip: str,
        topic: str | None,
    ) -> WeixinChubModeDispatchResult:
        operation_id = uuid4().hex
        self._log_dispatch(operation_id, "requested", source_ip)
        self._log_dispatch(operation_id, "started", source_ip)
        return self._remember_fixed_reply(
            message_id=message_id,
            correlation_id=correlation_id,
            operation_id=operation_id,
            route_fingerprint=route_fingerprint,
            source_ip=source_ip,
            message=chub_help_message(topic),
            code="codex_help_checked",
        )

    def _dispatch_chub_check(
        self,
        *,
        message_id: str,
        correlation_id: str | None,
        route_fingerprint: str,
        source_ip: str,
    ) -> WeixinChubModeDispatchResult:
        query_started_at = time.monotonic()
        operation_id = uuid4().hex
        self._log_dispatch(operation_id, "requested", source_ip)
        self._log_dispatch(operation_id, "started", source_ip)
        failed = False

        try:
            readiness = self.status()
        except Exception:
            LOGGER.warning("Unable to check Chub readiness", exc_info=True)
            readiness = None
        if readiness is not None and readiness.ready:
            web_message = "Web API：正常"
        else:
            failed = True
            code = getattr(readiness, "code", "unavailable")
            web_message = f"Web API：异常（{code}）"

        worker_message = "Quick Worker：不可用"
        worker_tasks_message = "活动任务：无法确认，排队任务：无法确认"
        if self.worker_health_reader is not None:
            try:
                payload = self.worker_health_reader()
                data = payload.get("data") if isinstance(payload, dict) else None
                worker_ready = (
                    isinstance(payload, dict)
                    and payload.get("success") is True
                    and isinstance(data, dict)
                    and data.get("protocol_version") == PROTOCOL_VERSION
                    and data.get("status") == "ready"
                    and data.get("uncertain_tasks") == 0
                    and data.get("corrupt_tasks") == 0
                    and "codex" in data.get("available_runtime_ids", [])
                )
                if worker_ready:
                    active = max(0, int(data.get("active_tasks", 0)))
                    queued = max(0, int(data.get("queued_tasks", 0)))
                    worker_message = (
                        "Quick Worker："
                        f"`{data.get('status')}` · 协议版本 {data.get('protocol_version')}"
                    )
                    worker_tasks_message = (
                        f"活动任务：{active} 个，排队任务：{queued} 个"
                    )
                else:
                    failed = True
                    status = data.get("status", "unknown") if isinstance(data, dict) else "unknown"
                    protocol = (
                        data.get("protocol_version", "unknown")
                        if isinstance(data, dict)
                        else "unknown"
                    )
                    worker_message = (
                        f"Quick Worker：`{status}`，协议版本 {protocol}，健康检查未通过"
                    )
            except Exception:
                LOGGER.warning("Unable to check Quick Worker health", exc_info=True)
                failed = True
                worker_message = "Quick Worker：不可用，健康检查失败"
        else:
            failed = True

        try:
            system = self.system_status_reader() if self.system_status_reader else None
            system_data = getattr(system, "system", None)
            if system_data is None:
                raise ValueError("system status is incomplete")
            system_message = (
                f"系统：内存 {float(system_data.memory_percent):.1f}%，"
                f"磁盘 {float(system_data.disk_percent):.1f}%"
            )
        except Exception:
            LOGGER.warning("Unable to check system status", exc_info=True)
            failed = True
            system_message = "系统：状态无法确认"

        health_result = "通过" if not failed else "未通过"
        message = "\n\n".join(
            (
                f"Check · {format_elapsed_time(max(1, round((time.monotonic() - query_started_at) * 1000)))}",
                "\n".join(
                    (
                        "【服务】",
                        f"- {web_message}",
                        f"- {worker_message}",
                    )
                ),
                "\n".join(
                    (
                        "【资源】",
                        f"- {worker_tasks_message.replace('，', ' · ', 1)}",
                        f"- {system_message.replace('，', ' · ', 1)}",
                    )
                ),
                f"【结果】健康检查：{health_result}",
            )
        )
        return self._remember_fixed_reply(
            message_id=message_id,
            correlation_id=correlation_id,
            operation_id=operation_id,
            route_fingerprint=route_fingerprint,
            source_ip=source_ip,
            message=message,
            code="chub_check_checked",
            failed=failed,
        )

    def _dispatch_text_control(
        self,
        *,
        message_id: str,
        correlation_id: str | None,
        route_fingerprint: str,
        source_ip: str,
        delivery_route: QuickInteractionWeixinRoute,
        processing_mode: Literal["direct", "auto", "confirm"] | None,
        text_action: Literal[
            "mode",
            "list",
            "ok",
            "next",
            "cancel",
            "model",
            "model_list",
            "model_levels",
            "model_use",
        ] | None,
        model_index: int | None,
        level_index: int | None,
        invalid_usage: bool,
    ) -> WeixinChubModeDispatchResult:
        if text_action in {"ok", "next", "cancel"}:
            return self._dispatch_text_confirmation(
                message_id=message_id,
                action=text_action,
                delivery_route=delivery_route,
                invalid_usage=invalid_usage,
            )
        if text_action == "list":
            return self._dispatch_text_list(
                message_id=message_id,
                correlation_id=correlation_id,
                route_fingerprint=route_fingerprint,
                source_ip=source_ip,
                invalid_usage=invalid_usage,
                delivery_route=delivery_route,
            )
        if text_action == "model_list":
            return self._dispatch_text_model_list(
                message_id=message_id,
                correlation_id=correlation_id,
                route_fingerprint=route_fingerprint,
                source_ip=source_ip,
                invalid_usage=invalid_usage,
            )
        if text_action == "model_levels":
            return self._dispatch_text_model_levels(
                message_id=message_id,
                correlation_id=correlation_id,
                route_fingerprint=route_fingerprint,
                source_ip=source_ip,
                model_index=model_index,
                invalid_usage=invalid_usage,
            )
        if text_action == "model_use":
            return self._dispatch_text_model_use(
                message_id=message_id,
                correlation_id=correlation_id,
                route_fingerprint=route_fingerprint,
                source_ip=source_ip,
                model_index=model_index,
                level_index=level_index,
                invalid_usage=invalid_usage,
            )
        with self._text_mode_lock:
            with self._lock:
                if self._state_error:
                    self._log_standalone_dispatch("failed", source_ip)
                    return self._dispatch_failure("state_unavailable")
                duplicate = self._find_submission(message_id)
                if duplicate is not None:
                    if duplicate.delivery_route_fingerprint != route_fingerprint:
                        self._log_standalone_dispatch("failed", source_ip)
                        return self._dispatch_failure("message_conflict")
                    self._log_standalone_dispatch("succeeded", source_ip)
                    return WeixinChubModeDispatchResult(
                        disposition=duplicate.dispatch_disposition or "reply",
                        message=duplicate.message or None,
                    )
            return self._dispatch_text_mode_once(
                message_id=message_id,
                correlation_id=correlation_id,
                route_fingerprint=route_fingerprint,
                source_ip=source_ip,
                delivery_route=delivery_route,
                processing_mode=processing_mode,
                invalid_usage=invalid_usage,
            )

    def _format_text_confirmation_entry(
        self,
        entry: TranslationEntry,
        *,
        current_session_id: str | None,
    ) -> str:
        slot, title = (
            self._session_context(entry.target_session_id)
            if entry.target_session_id is not None
            else (None, None)
        )
        session_line = (
            f"{'▶ ' if entry.target_session_id == current_session_id else ''}S{slot} · {title}"
            if slot is not None and title is not None
            else "Session · Unavailable"
        )
        task_summary = build_task_name(
            entry.original,
            self.settings.openclaw.weixin_chub_mode.task_name_max_width,
        )
        return f"{session_line}\n\nTask · {task_summary}"

    def _text_current_confirmation_message(
        self,
        delivery_route: QuickInteractionWeixinRoute,
    ) -> str:
        if self.translation_manager is None:
            return "Current confirmation: Unavailable."
        try:
            entry = self.translation_manager.active_confirmation(delivery_route)
        except OSError:
            return "Current confirmation: Unavailable."
        if entry is None:
            return "Current confirmation: None."
        with self._lock:
            current_session_id = self._state.session_id
        slot, title = self._session_context(entry.target_session_id)
        session_line = (
            f"{'▶ ' if entry.target_session_id == current_session_id else ''}S{slot} · {title}"
            if slot is not None and title is not None
            else "Session · Unavailable"
        )
        return "\n\n".join(
            (
                "Current confirmation",
                session_line,
                f"Polished:\n{entry.polished or entry.original}",
                f"English:\n{entry.english or 'Unavailable'}",
            )
        )

    def _text_processing_queue_message(
        self,
        delivery_route: QuickInteractionWeixinRoute,
    ) -> str:
        if self.translation_manager is None:
            return "Text processing: Unavailable."
        try:
            groups = self.translation_manager.processing_queue(delivery_route)
        except OSError:
            return "Text processing: Unavailable."
        groups = [(heading, entries) for heading, entries in groups if entries]
        if not groups:
            return "Text processing: None."
        with self._lock:
            current_session_id = self._state.session_id
        lines = ["Text processing"]
        for heading, entries in groups:
            lines.append(heading)
            lines.extend(
                self._format_text_confirmation_entry(
                    entry,
                    current_session_id=current_session_id,
                )
                for entry in entries
            )
        return "\n\n".join(lines)

    def _dispatch_text_list(
        self,
        *,
        message_id: str,
        correlation_id: str | None,
        route_fingerprint: str,
        source_ip: str,
        delivery_route: QuickInteractionWeixinRoute,
        invalid_usage: bool,
    ) -> WeixinChubModeDispatchResult:
        operation_id = uuid4().hex
        self._log_dispatch(operation_id, "requested", source_ip)
        self._log_dispatch(operation_id, "started", source_ip)
        if invalid_usage:
            message = "Text: Usage · text list."
            failed = True
        else:
            message = self._text_processing_queue_message(delivery_route)
            failed = message == "Text processing: Unavailable."
        return self._remember_fixed_reply(
            message_id=message_id,
            correlation_id=correlation_id,
            operation_id=operation_id,
            route_fingerprint=route_fingerprint,
            source_ip=source_ip,
            message=message,
            code="weixin_text_mode_checked",
            failed=failed,
        )

    def _dispatch_text_mode_once(
        self,
        *,
        message_id: str,
        correlation_id: str | None,
        route_fingerprint: str,
        source_ip: str,
        delivery_route: QuickInteractionWeixinRoute,
        processing_mode: Literal["direct", "auto", "confirm"] | None,
        invalid_usage: bool,
    ) -> WeixinChubModeDispatchResult:
        operation_id = uuid4().hex
        target = processing_mode or "current"
        write_operation(
            operation_id=operation_id,
            action="update_weixin_translation_setting",
            status="requested",
            target=target,
            source_ip=source_ip,
        )
        write_operation(
            operation_id=operation_id,
            action="update_weixin_translation_setting",
            status="started",
            target=target,
            source_ip=source_ip,
        )
        if invalid_usage:
            write_operation(
                operation_id=operation_id,
                action="update_weixin_translation_setting",
                status="failed",
                target=target,
                source_ip=source_ip,
            )
            return self._remember_fixed_reply(
                message_id=message_id,
                correlation_id=correlation_id,
                operation_id=operation_id,
                route_fingerprint=route_fingerprint,
                source_ip=source_ip,
                message=(
                    "Text: Usage · text [mode [direct|auto|confirm]|list|ok|next|cancel]\n\n"
                    "text model list\n\n"
                    "text model level [M#]\n\n"
                    "text model use M# | L# | M# L#\n\n"
                    "text-check <English>"
                ),
                code="weixin_text_mode_checked",
                failed=True,
            )
        if self.translation_manager is None:
            write_operation(
                operation_id=operation_id,
                action="update_weixin_translation_setting",
                status="failed",
                target=target,
                source_ip=source_ip,
            )
            return self._remember_fixed_reply(
                message_id=message_id,
                correlation_id=correlation_id,
                operation_id=operation_id,
                route_fingerprint=route_fingerprint,
                source_ip=source_ip,
                message="Text mode: Unavailable.",
                code="weixin_text_mode_checked",
                failed=True,
            )
        try:
            status = (
                self.translation_manager.status()
                if processing_mode is None
                else self.translation_manager.set_processing_mode(processing_mode)
            )
        except OSError:
            write_operation(
                operation_id=operation_id,
                action="update_weixin_translation_setting",
                status="failed",
                target=target,
                source_ip=source_ip,
            )
            return self._remember_fixed_reply(
                message_id=message_id,
                correlation_id=correlation_id,
                operation_id=operation_id,
                route_fingerprint=route_fingerprint,
                source_ip=source_ip,
                message="Text mode: Unavailable.",
                code="weixin_text_mode_checked",
                failed=True,
            )
        mode_description = {
            "direct": "Direct execution",
            "auto": "Automatic polish and submit",
            "confirm": "Automatic polish and confirm",
        }[status.mode]
        write_operation(
            operation_id=operation_id,
            action="update_weixin_translation_setting",
            status="succeeded",
            target=status.mode,
            source_ip=source_ip,
        )
        if processing_mode is None:
            message = (
                "Text\n\n"
                f"Mode · {mode_description}\n\n"
                f"Model · {getattr(status, 'model', None) or 'Default'} · "
                f"{getattr(status, 'reasoning_effort', None) or 'Default'}\n\n"
                f"{self._text_current_confirmation_message(delivery_route)}"
            )
        else:
            message = f"Text mode updated · {mode_description}."
        return self._remember_fixed_reply(
            message_id=message_id,
            correlation_id=correlation_id,
            operation_id=operation_id,
            route_fingerprint=route_fingerprint,
            source_ip=source_ip,
            message=message,
            code="weixin_text_mode_checked",
        )

    def _read_text_model_catalog(self):
        if self.translation_manager is None:
            raise OSError("Weixin translation settings are unavailable")
        status = self.translation_manager.status()
        catalog = self.codex_manager.read_model_catalog()
        models = tuple(
            item
            for item in getattr(catalog, "models", ())
            if isinstance(getattr(item, "id", None), str) and item.id
        )
        if not models:
            raise ValueError("The Codex model catalog is empty")
        return status, catalog, models

    def _dispatch_text_model_list(
        self,
        *,
        message_id: str,
        correlation_id: str | None,
        route_fingerprint: str,
        source_ip: str,
        invalid_usage: bool,
    ) -> WeixinChubModeDispatchResult:
        operation_id = uuid4().hex
        self._log_dispatch(operation_id, "requested", source_ip)
        self._log_dispatch(operation_id, "started", source_ip)

        if invalid_usage:
            message = "Text model list: Usage · text model list."
            return self._remember_fixed_reply(
                message_id=message_id,
                correlation_id=correlation_id,
                operation_id=operation_id,
                route_fingerprint=route_fingerprint,
                source_ip=source_ip,
                message=message,
                code="codex_model_checked",
                failed=True,
            )
        try:
            status, catalog, models = self._read_text_model_catalog()
            model_id = getattr(status, "model", None) or getattr(
                catalog, "default_model", None
            )
            model_ids = tuple(item.id for item in models)
            if model_id not in model_ids:
                raise ValueError("The configured translation model is unavailable")
        except Exception:
            LOGGER.warning("Unable to read Weixin translation model list", exc_info=True)
            return self._remember_fixed_reply(
                message_id=message_id,
                correlation_id=correlation_id,
                operation_id=operation_id,
                route_fingerprint=route_fingerprint,
                source_ip=source_ip,
                message="Text model list: Unavailable. The translation model catalog could not be read.",
                code="codex_model_checked",
                failed=True,
            )
        return self._remember_fixed_reply(
            message_id=message_id,
            correlation_id=correlation_id,
            operation_id=operation_id,
            route_fingerprint=route_fingerprint,
            source_ip=source_ip,
            message=(
                "Text model list\n\n"
                f"Model · M{model_ids.index(model_id) + 1} · {model_id}\n\n"
                "Models\n"
                + "\n".join(
                    f"M{index} · {item.id}"
                    for index, item in enumerate(models, start=1)
                )
            ),
            code="codex_model_checked",
        )

    def _dispatch_text_model_levels(
        self,
        *,
        message_id: str,
        correlation_id: str | None,
        route_fingerprint: str,
        source_ip: str,
        model_index: int | None,
        invalid_usage: bool,
    ) -> WeixinChubModeDispatchResult:
        operation_id = uuid4().hex
        self._log_dispatch(operation_id, "requested", source_ip)
        self._log_dispatch(operation_id, "started", source_ip)

        if invalid_usage:
            message = "Text model levels: Usage · text model level [M#]."
            return self._remember_fixed_reply(
                message_id=message_id,
                correlation_id=correlation_id,
                operation_id=operation_id,
                route_fingerprint=route_fingerprint,
                source_ip=source_ip,
                message=message,
                code="codex_model_checked",
                failed=True,
            )
        try:
            status, catalog, models = self._read_text_model_catalog()
            model_ids = tuple(item.id for item in models)
            if model_index is not None:
                if not 1 <= model_index <= len(models):
                    raise ValueError("The requested model index is unavailable")
                selected_model = models[model_index - 1]
                display_model = f"M{model_index} · {selected_model.id}"
                current_level = None
            else:
                model_id = getattr(status, "model", None) or getattr(
                    catalog, "default_model", None
                )
                selected_model = next(
                    (item for item in models if item.id == model_id),
                    None,
                )
                if selected_model is None:
                    raise ValueError("The configured translation model is unavailable")
                display_model = f"M{model_ids.index(model_id) + 1} · {model_id}"
                current_level = getattr(status, "reasoning_effort", None) or getattr(
                    selected_model, "default_level", None
                ) or getattr(catalog, "default_reasoning_effort", None)
                if current_level is None:
                    raise ValueError("The current translation level is unavailable")
            level_ids = tuple(
                level.id
                for level in getattr(selected_model, "levels", ())
                if isinstance(getattr(level, "id", None), str) and level.id
            )
            if not level_ids:
                raise ValueError("The selected model has no available levels")
        except Exception:
            LOGGER.warning("Unable to read Weixin translation model levels", exc_info=True)
            return self._remember_fixed_reply(
                message_id=message_id,
                correlation_id=correlation_id,
                operation_id=operation_id,
                route_fingerprint=route_fingerprint,
                source_ip=source_ip,
                message="Text model levels: Unavailable. The translation model or levels could not be read.",
                code="codex_model_checked",
                failed=True,
            )
        current_line = f"\n\nLevel · {current_level}" if current_level else ""
        return self._remember_fixed_reply(
            message_id=message_id,
            correlation_id=correlation_id,
            operation_id=operation_id,
            route_fingerprint=route_fingerprint,
            source_ip=source_ip,
            message=(
                f"Text model levels\n\nModel · {display_model}{current_line}\n\n"
                "Levels\n"
                + "\n".join(
                    f"L{index} · {level_id}"
                    for index, level_id in enumerate(level_ids, start=1)
                )
            ),
            code="codex_model_checked",
        )

    def _dispatch_text_model_use(
        self,
        *,
        message_id: str,
        correlation_id: str | None,
        route_fingerprint: str,
        source_ip: str,
        model_index: int | None,
        level_index: int | None,
        invalid_usage: bool,
    ) -> WeixinChubModeDispatchResult:
        operation_id = uuid4().hex
        target = (
            f"M{model_index}" if model_index is not None else f"L{level_index}"
        )
        write_operation(
            operation_id=operation_id,
            action="update_weixin_translation_setting",
            status="requested",
            target=target,
            source_ip=source_ip,
        )
        write_operation(
            operation_id=operation_id,
            action="update_weixin_translation_setting",
            status="started",
            target=target,
            source_ip=source_ip,
        )

        def fail(message: str) -> WeixinChubModeDispatchResult:
            write_operation(
                operation_id=operation_id,
                action="update_weixin_translation_setting",
                status="failed",
                target=target,
                source_ip=source_ip,
            )
            return self._remember_fixed_reply(
                message_id=message_id,
                correlation_id=correlation_id,
                operation_id=operation_id,
                route_fingerprint=route_fingerprint,
                source_ip=source_ip,
                message=message,
                code="codex_model_checked",
                failed=True,
            )

        if invalid_usage or model_index is None and level_index is None:
            return fail("Text model update: Usage · text model use M# | L# | M# L#.")
        if self.translation_manager is None:
            return fail("Text model update: Unavailable.")
        try:
            status, catalog, models = self._read_text_model_catalog()
            model_ids = tuple(item.id for item in models)
            if model_index is None:
                model_id = getattr(status, "model", None)
                if model_id is None:
                    return fail(
                        "Text model update: No translation model is configured. "
                        "Select a model with M#."
                    )
                target_model = model_id
            elif not 1 <= model_index <= len(models):
                return fail("Text model update: Model index is unavailable in the current catalog.")
            else:
                target_model = model_ids[model_index - 1]
            selected_model = next(
                (item for item in models if item.id == target_model),
                None,
            )
            if selected_model is None:
                return fail("Text model update: The configured translation model is unavailable.")
            level_ids = tuple(
                level.id
                for level in getattr(selected_model, "levels", ())
                if isinstance(getattr(level, "id", None), str) and level.id
            )
            if level_index is None:
                target_level = getattr(selected_model, "default_level", None)
            elif 1 <= level_index <= len(level_ids):
                target_level = level_ids[level_index - 1]
            else:
                return fail("Text model update: Level index is unavailable for the selected model.")
            if target_level is None:
                return fail("Text model update: A default level is unavailable for the selected model.")
            self.translation_manager.set_model(target_model, target_level)
        except ApiError:
            LOGGER.warning("Unable to validate Weixin translation model update", exc_info=True)
            return fail("Text model update: The selected model or level is unavailable.")
        except Exception:
            LOGGER.warning("Unable to save Weixin translation model", exc_info=True)
            return fail("Text model update: Unable to save the translation configuration.")
        write_operation(
            operation_id=operation_id,
            action="update_weixin_translation_setting",
            status="succeeded",
            target=f"{target_model}/{target_level}",
            source_ip=source_ip,
        )
        return self._remember_fixed_reply(
            message_id=message_id,
            correlation_id=correlation_id,
            operation_id=operation_id,
            route_fingerprint=route_fingerprint,
            source_ip=source_ip,
            message=(
                "Text model updated\n\n"
                f"Next model · {target_model}\n\n"
                f"Next level · {target_level}"
            ),
            code="codex_model_checked",
        )

    def _dispatch_codex_model(
        self,
        *,
        message_id: str,
        correlation_id: str | None,
        route_fingerprint: str,
        source_ip: str,
    ) -> WeixinChubModeDispatchResult:
        operation_id = uuid4().hex
        self._log_dispatch(operation_id, "requested", source_ip)
        self._log_dispatch(operation_id, "started", source_ip)
        with self._lock:
            session_id = self._state.session_id
        if session_id is None:
            return self._remember_fixed_reply(
                message_id=message_id,
                correlation_id=correlation_id,
                operation_id=operation_id,
                route_fingerprint=route_fingerprint,
                source_ip=source_ip,
                message="Model: No Session is selected.",
                code="codex_model_checked",
                failed=True,
            )
        try:
            session = self.codex_manager.get_session(session_id)
            if getattr(session, "id", None) != session_id:
                raise ValueError("Current Session identity could not be confirmed")
        except Exception:
            LOGGER.warning(
                "Unable to read current Weixin Session model",
                exc_info=True,
            )
            return self._remember_fixed_reply(
                message_id=message_id,
                correlation_id=correlation_id,
                operation_id=operation_id,
                route_fingerprint=route_fingerprint,
                source_ip=source_ip,
                message="Model: Unavailable. The current Session could not be read.",
                code="codex_model_checked",
                failed=True,
            )
        model = getattr(session, "active_model", None) or getattr(
            session,
            "model",
            None,
        )
        reasoning_effort = (
            getattr(session, "active_reasoning_effort", None)
            or getattr(session, "reasoning_effort", None)
        )
        next_model = getattr(session, "model", None)
        next_reasoning_effort = getattr(session, "reasoning_effort", None)
        if model is None or reasoning_effort is None:
            try:
                catalog = self.codex_manager.read_model_catalog()
            except Exception:
                LOGGER.warning(
                    "Unable to read Codex model defaults for Weixin Session",
                    exc_info=True,
                )
            else:
                if model is None:
                    model = getattr(catalog, "default_model", None)
                selected_model = next(
                    (
                        item
                        for item in getattr(catalog, "models", ())
                        if getattr(item, "id", None) == model
                    ),
                    None,
                )
                if reasoning_effort is None:
                    reasoning_effort = getattr(
                        selected_model,
                        "default_level",
                        None,
                    ) or getattr(catalog, "default_reasoning_effort", None)
                if next_model is None:
                    next_model = model
                if next_reasoning_effort is None and next_model == model:
                    next_reasoning_effort = reasoning_effort
        slot = self._slot_for_session(session_id)
        title = build_session_title(
            getattr(session, "title", None) or "Unnamed Session",
            self.settings.openclaw.weixin_chub_mode.session_name_max_width,
        )
        session_message = (
            format_session_name_line(slot, title, "Available", True)
            if slot is not None
            else f"Session · {title}"
        )
        next_lines = []
        if next_model and next_model != model:
            next_lines.append(f"Next model · {next_model}")
        if next_reasoning_effort and next_reasoning_effort != reasoning_effort:
            next_lines.append(f"Next level · {next_reasoning_effort}")
        next_configuration = (
            "\n\n" + "\n\n".join(next_lines) if next_lines else ""
        )
        return self._remember_fixed_reply(
            message_id=message_id,
            correlation_id=correlation_id,
            operation_id=operation_id,
            route_fingerprint=route_fingerprint,
            source_ip=source_ip,
            message=(
                f"Session\n\n{session_message}\n\n"
                f"Model · {model or 'Default'}\n\n"
                f"Level · {reasoning_effort or 'Default'}{next_configuration}"
            ),
            code="codex_model_checked",
        )

    def _dispatch_codex_model_levels(
        self,
        *,
        message_id: str,
        correlation_id: str | None,
        route_fingerprint: str,
        source_ip: str,
        model_index: int | None = None,
    ) -> WeixinChubModeDispatchResult:
        operation_id = uuid4().hex
        self._log_dispatch(operation_id, "requested", source_ip)
        self._log_dispatch(operation_id, "started", source_ip)
        with self._lock:
            session_id = self._state.session_id
        if session_id is None:
            return self._remember_fixed_reply(
                message_id=message_id,
                correlation_id=correlation_id,
                operation_id=operation_id,
                route_fingerprint=route_fingerprint,
                source_ip=source_ip,
                message="Model levels: No Session is selected.",
                code="codex_model_checked",
                failed=True,
            )
        try:
            session = self.codex_manager.get_session(session_id)
            if getattr(session, "id", None) != session_id:
                raise ValueError("Current Session identity could not be confirmed")
            catalog = self.codex_manager.read_model_catalog()
        except Exception:
            LOGGER.warning(
                "Unable to read current Weixin Session model levels",
                exc_info=True,
            )
            return self._remember_fixed_reply(
                message_id=message_id,
                correlation_id=correlation_id,
                operation_id=operation_id,
                route_fingerprint=route_fingerprint,
                source_ip=source_ip,
                message="Model levels: Unavailable. The current Session or model catalog could not be read.",
                code="codex_model_checked",
                failed=True,
            )
        model_ids = tuple(
            item.id
            for item in getattr(catalog, "models", ())
            if isinstance(getattr(item, "id", None), str) and item.id
        )
        if model_index is not None:
            model_id = (
                model_ids[model_index - 1]
                if 1 <= model_index <= len(model_ids)
                else None
            )
            if model_id is None:
                return self._remember_fixed_reply(
                    message_id=message_id,
                    correlation_id=correlation_id,
                    operation_id=operation_id,
                    route_fingerprint=route_fingerprint,
                    source_ip=source_ip,
                    message="Model levels: Model index is unavailable in the current catalog.",
                    code="codex_model_checked",
                    failed=True,
                )
        else:
            model_id = getattr(session, "model", None) or getattr(
                session,
                "active_model",
                None,
            ) or getattr(catalog, "default_model", None)
        reasoning_effort = None if model_index is not None else (
            getattr(session, "reasoning_effort", None)
            or getattr(session, "active_reasoning_effort", None)
        )
        selected_model = next(
            (
                item
                for item in getattr(catalog, "models", ())
                if getattr(item, "id", None) == model_id
            ),
            None,
        )
        levels = tuple(getattr(selected_model, "levels", ()))
        if selected_model is None or not levels:
            return self._remember_fixed_reply(
                message_id=message_id,
                correlation_id=correlation_id,
                operation_id=operation_id,
                route_fingerprint=route_fingerprint,
                source_ip=source_ip,
                message="Model levels: Unavailable. The current model has no available reasoning levels.",
                code="codex_model_checked",
                failed=True,
            )
        if reasoning_effort is None and model_index is None:
            reasoning_effort = getattr(
                selected_model,
                "default_level",
                None,
            ) or getattr(catalog, "default_reasoning_effort", None)
        if reasoning_effort is None and model_index is None:
            return self._remember_fixed_reply(
                message_id=message_id,
                correlation_id=correlation_id,
                operation_id=operation_id,
                route_fingerprint=route_fingerprint,
                source_ip=source_ip,
                message="Model levels: Unavailable. The current reasoning level could not be confirmed.",
                code="codex_model_checked",
                failed=True,
            )
        slot = self._slot_for_session(session_id)
        title = build_session_title(
            getattr(session, "title", None) or "Unnamed Session",
            self.settings.openclaw.weixin_chub_mode.session_name_max_width,
        )
        session_message = (
            format_session_name_line(slot, title, "Available", True)
            if slot is not None
            else f"Session · {title}"
        )
        level_ids = tuple(
            level_id
            for level in levels
            if isinstance((level_id := getattr(level, "id", None)), str) and level_id
        )
        if not level_ids:
            return self._remember_fixed_reply(
                message_id=message_id,
                correlation_id=correlation_id,
                operation_id=operation_id,
                route_fingerprint=route_fingerprint,
                source_ip=source_ip,
                message="Model levels: Unavailable. The current model has no available reasoning levels.",
                code="codex_model_checked",
                failed=True,
            )
        model_label = f"M{model_index}" if model_index is not None else None
        if model_label is None and model_id in model_ids:
            model_label = f"M{model_ids.index(model_id) + 1}"
        display_model = f"{model_label} · {model_id}" if model_label else model_id
        return self._remember_fixed_reply(
            message_id=message_id,
            correlation_id=correlation_id,
            operation_id=operation_id,
            route_fingerprint=route_fingerprint,
            source_ip=source_ip,
            message=(
                f"Session\n\n{session_message}\n\n"
                f"Model · {display_model}\n\n"
                + (f"Level · {reasoning_effort}\n\n" if reasoning_effort else "")
                + "Levels\n"
                + "\n".join(
                    f"L{index} · {level_id}"
                    for index, level_id in enumerate(level_ids, start=1)
                )
            ),
            code="codex_model_checked",
        )

    def _dispatch_codex_model_use(
        self,
        *,
        message_id: str,
        correlation_id: str | None,
        route_fingerprint: str,
        source_ip: str,
        model_index: int | None,
        level_index: int | None,
    ) -> WeixinChubModeDispatchResult:
        operation_id = uuid4().hex
        self._log_dispatch(operation_id, "requested", source_ip)
        self._log_dispatch(operation_id, "started", source_ip)
        with self._lock:
            session_id = self._state.session_id
        if session_id is None:
            return self._remember_fixed_reply(
                message_id=message_id,
                correlation_id=correlation_id,
                operation_id=operation_id,
                route_fingerprint=route_fingerprint,
                source_ip=source_ip,
                message="Model update: No Session is selected.",
                code="codex_model_checked",
                failed=True,
            )
        try:
            session = self.codex_manager.get_session(session_id)
            if getattr(session, "id", None) != session_id:
                raise ValueError("Current Session identity could not be confirmed")
            catalog = self.codex_manager.read_model_catalog()
        except Exception:
            LOGGER.warning("Unable to read Weixin Session model update context", exc_info=True)
            return self._remember_fixed_reply(
                message_id=message_id,
                correlation_id=correlation_id,
                operation_id=operation_id,
                route_fingerprint=route_fingerprint,
                source_ip=source_ip,
                message="Model update: Unavailable. The current Session or model catalog could not be read.",
                code="codex_model_checked",
                failed=True,
            )
        current_model = getattr(session, "model", None) or getattr(
            session,
            "active_model",
            None,
        ) or getattr(catalog, "default_model", None)
        model_ids = tuple(
            item.id
            for item in getattr(catalog, "models", ())
            if isinstance(getattr(item, "id", None), str) and item.id
        )
        target_model = (
            model_ids[model_index - 1]
            if model_index is not None and 1 <= model_index <= len(model_ids)
            else current_model if model_index is None else None
        )
        if target_model is None:
            return self._remember_fixed_reply(
                message_id=message_id,
                correlation_id=correlation_id,
                operation_id=operation_id,
                route_fingerprint=route_fingerprint,
                source_ip=source_ip,
                message="Model update: Model index is unavailable in the current catalog.",
                code="codex_model_checked",
                failed=True,
            )
        selected_model = next(
            (
                item
                for item in getattr(catalog, "models", ())
                if getattr(item, "id", None) == target_model
            ),
            None,
        )
        level_ids = tuple(
            item.id
            for item in getattr(selected_model, "levels", ())
            if isinstance(getattr(item, "id", None), str) and item.id
        )
        if selected_model is None or not level_ids:
            return self._remember_fixed_reply(
                message_id=message_id,
                correlation_id=correlation_id,
                operation_id=operation_id,
                route_fingerprint=route_fingerprint,
                source_ip=source_ip,
                message="Model update: The current model is unavailable. Select a model with M#.",
                code="codex_model_checked",
                failed=True,
            )
        target_level = (
            level_ids[level_index - 1]
            if level_index is not None and 1 <= level_index <= len(level_ids)
            else getattr(selected_model, "default_level", None)
            if level_index is None
            else None
        )
        if target_level is None or target_level not in level_ids:
            guidance = (
                "Level index is unavailable for the selected model."
                if level_index is not None
                else "Specify a level after the model index."
            )
            return self._remember_fixed_reply(
                message_id=message_id,
                correlation_id=correlation_id,
                operation_id=operation_id,
                route_fingerprint=route_fingerprint,
                source_ip=source_ip,
                message=f"Model update: A compatible level could not be confirmed. {guidance}",
                code="codex_model_checked",
                failed=True,
            )
        try:
            self.quick_interactions.update_session_model(
                session_id,
                target_model,
                target_level,
            )
        except ApiError as exc:
            return self._remember_fixed_reply(
                message_id=message_id,
                correlation_id=correlation_id,
                operation_id=operation_id,
                route_fingerprint=route_fingerprint,
                source_ip=source_ip,
                message=f"Model update: {exc.message}",
                code="codex_model_checked",
                failed=True,
            )
        except Exception:
            LOGGER.warning("Unable to update Weixin Session model", exc_info=True)
            return self._remember_fixed_reply(
                message_id=message_id,
                correlation_id=correlation_id,
                operation_id=operation_id,
                route_fingerprint=route_fingerprint,
                source_ip=source_ip,
                message="Model update: Unable to save the Session configuration.",
                code="codex_model_checked",
                failed=True,
            )
        return self._remember_fixed_reply(
            message_id=message_id,
            correlation_id=correlation_id,
            operation_id=operation_id,
            route_fingerprint=route_fingerprint,
            source_ip=source_ip,
            message=(
                "Model updated\n\n"
                f"Next model · {target_model}\n\n"
                f"Next level · {target_level}"
            ),
            code="codex_model_checked",
        )

    def _dispatch_codex_model_list(
        self,
        *,
        message_id: str,
        correlation_id: str | None,
        route_fingerprint: str,
        source_ip: str,
    ) -> WeixinChubModeDispatchResult:
        operation_id = uuid4().hex
        self._log_dispatch(operation_id, "requested", source_ip)
        self._log_dispatch(operation_id, "started", source_ip)
        with self._lock:
            session_id = self._state.session_id
        if session_id is None:
            return self._remember_fixed_reply(
                message_id=message_id,
                correlation_id=correlation_id,
                operation_id=operation_id,
                route_fingerprint=route_fingerprint,
                source_ip=source_ip,
                message="Model list: No Session is selected.",
                code="codex_model_checked",
                failed=True,
            )
        try:
            session = self.codex_manager.get_session(session_id)
            if getattr(session, "id", None) != session_id:
                raise ValueError("Current Session identity could not be confirmed")
            catalog = self.codex_manager.read_model_catalog()
        except Exception:
            LOGGER.warning(
                "Unable to read current Weixin Session model list",
                exc_info=True,
            )
            return self._remember_fixed_reply(
                message_id=message_id,
                correlation_id=correlation_id,
                operation_id=operation_id,
                route_fingerprint=route_fingerprint,
                source_ip=source_ip,
                message="Model list: Unavailable. The current Session or model catalog could not be read.",
                code="codex_model_checked",
                failed=True,
            )
        model_id = getattr(session, "model", None) or getattr(
            session,
            "active_model",
            None,
        ) or getattr(catalog, "default_model", None)
        model_ids = tuple(
            model.id
            for model in getattr(catalog, "models", ())
            if isinstance(getattr(model, "id", None), str) and model.id
        )
        if model_id not in model_ids:
            return self._remember_fixed_reply(
                message_id=message_id,
                correlation_id=correlation_id,
                operation_id=operation_id,
                route_fingerprint=route_fingerprint,
                source_ip=source_ip,
                message="Model list: Unavailable. The current model is not in the Codex model catalog.",
                code="codex_model_checked",
                failed=True,
            )
        slot = self._slot_for_session(session_id)
        title = build_session_title(
            getattr(session, "title", None) or "Unnamed Session",
            self.settings.openclaw.weixin_chub_mode.session_name_max_width,
        )
        session_message = (
            format_session_name_line(slot, title, "Available", True)
            if slot is not None
            else f"Session · {title}"
        )
        return self._remember_fixed_reply(
            message_id=message_id,
            correlation_id=correlation_id,
            operation_id=operation_id,
            route_fingerprint=route_fingerprint,
            source_ip=source_ip,
            message=(
                f"Session\n\n{session_message}\n\n"
                f"Model · M{model_ids.index(model_id) + 1} · {model_id}\n\n"
                "Models\n"
                + "\n".join(
                    f"M{index} · {available_model}"
                    for index, available_model in enumerate(model_ids, start=1)
                )
            ),
            code="codex_model_checked",
        )

    def deferred_restart_readiness(
        self,
        request: DeferredRestartRequest,
    ) -> DeferredRestartReadiness | None:
        if not request.requested_task_id.startswith(WEIXIN_RESTART_TASK_PREFIX):
            return None
        with self._lock:
            registered = any(
                item.coordinator_operation_id == request.operation_id
                and item.status in {"pending", "started"}
                for item in self._state.restart_operations
            )
        if registered:
            return "ready"
        if (utc_now() - request.requested_at).total_seconds() < 5:
            return "waiting"
        return "sensitive_task_failed"

    def record_deferred_restart_started(
        self,
        coordinator_operation_id: str,
        _requested_task_id: str,
        started_at: datetime,
    ) -> bool:
        with self._lock:
            next_state = self._state.model_copy(deep=True)
            matched = [
                item
                for item in next_state.restart_operations
                if item.coordinator_operation_id == coordinator_operation_id
                and item.status == "pending"
            ]
            if not matched:
                return False
            for item in matched:
                item.status = "started"
                item.updated_at = started_at
            self._write_state(next_state)
            self._state = next_state
        for item in matched:
            if item.operation_id != coordinator_operation_id:
                write_operation(
                    operation_id=item.operation_id,
                    action="restart_hub",
                    status="started",
                    target="chub",
                    source_ip=item.source_ip,
                )
        return True

    def record_deferred_restart_completion(
        self,
        coordinator_operation_id: str,
        _requested_task_id: str,
        outcome: DeferredRestartOutcome,
        completed_at: datetime,
        failure_reason: str | None = None,
    ) -> bool:
        with self._lock:
            next_state = self._state.model_copy(deep=True)
            matched = [
                item
                for item in next_state.restart_operations
                if item.coordinator_operation_id == coordinator_operation_id
            ]
            if not matched:
                return False
            changed = False
            for item in matched:
                if item.status in {"pending", "started"}:
                    item.status = outcome
                    item.error = (
                        failure_reason[:500]
                        if outcome in {"start_failed", "sensitive_task_failed"}
                        and failure_reason
                        else None
                    )
                    item.notification_status = "pending"
                    item.notification_error = None
                    item.updated_at = completed_at
                    changed = True
            if changed:
                self._write_state(next_state)
                self._state = next_state
            pending_operation_ids = [
                item.operation_id
                for item in next_state.restart_operations
                if item.coordinator_operation_id == coordinator_operation_id
                and item.notification_status == "pending"
            ]
        final_status = "succeeded" if outcome in {"succeeded", "cleared"} else "failed"
        for item in matched:
            if item.operation_id != coordinator_operation_id and changed:
                write_operation(
                    operation_id=item.operation_id,
                    action="restart_hub",
                    status=final_status,
                    target="chub",
                    source_ip=item.source_ip,
                    reason=item.error if final_status == "failed" else None,
                )
        for operation_id in pending_operation_ids:
            self._deliver_restart_notification(operation_id)
        return True

    def _deliver_restart_notification(self, operation_id: str) -> None:
        with self._lock:
            current = next(
                (
                    item
                    for item in self._state.restart_operations
                    if item.operation_id == operation_id
                ),
                None,
            )
            if current is None or current.notification_status != "pending":
                return
            next_state = self._state.model_copy(deep=True)
            operation = next(
                item
                for item in next_state.restart_operations
                if item.operation_id == operation_id
            )
            operation.notification_status = "sending"
            operation.updated_at = utc_now()
            self._write_state(next_state)
            self._state = next_state
            snapshot = operation.model_copy(deep=True)

        notification_operation_id = f"{operation_id}:weixin"
        for status in ("requested", "started"):
            write_operation(
                operation_id=notification_operation_id,
                action="weixin_chub_restart_notification",
                status=status,
                target=self.settings.node.id,
                source_ip=snapshot.source_ip,
            )
        try:
            result = self.restart_notifier(
                snapshot.delivery_route,
                snapshot.status,
                snapshot.error,
            )
            notification_status = getattr(result, "status", "failed")
            notification_error = getattr(result, "error", None)
            if notification_status not in {"sent", "failed", "skipped"}:
                notification_status = "failed"
                notification_error = "微信重启结果返回了无效状态。"
        except Exception:
            LOGGER.warning("Weixin Chub restart notification failed", exc_info=True)
            notification_status = "failed"
            notification_error = "微信重启结果未送达。"

        with self._lock:
            next_state = self._state.model_copy(deep=True)
            operation = next(
                (
                    item
                    for item in next_state.restart_operations
                    if item.operation_id == operation_id
                ),
                None,
            )
            if operation is not None:
                operation.notification_status = notification_status
                operation.notification_error = (
                    notification_error[:1_000] if notification_error else None
                )
                operation.updated_at = utc_now()
                try:
                    self._write_state(next_state)
                except OSError:
                    self._state_error = True
                    LOGGER.warning(
                        "Unable to persist Weixin restart notification result",
                        exc_info=True,
                    )
                else:
                    self._state = next_state
        write_operation(
            operation_id=notification_operation_id,
            action="weixin_chub_restart_notification",
            status="succeeded" if notification_status == "sent" else "failed",
            target=self.settings.node.id,
            source_ip=snapshot.source_ip,
        )

    def start_status_cache(self) -> None:
        """Initialize the Chub overview after application startup."""
        with self._status_condition:
            if self._status_cache_started:
                return
            self._status_cache_started = True
        try:
            threading.Thread(
                target=self._refresh_chub_cache,
                kwargs={"include_ai_usage": False},
                daemon=True,
                name="chub-status-cache-init",
            ).start()
        except RuntimeError:
            with self._status_condition:
                self._status_cache_started = False
            LOGGER.warning("Unable to start Chub status cache initialization")

    def _dispatch_chub_status(
        self,
        *,
        message_id: str,
        route_fingerprint: str,
        delivery_route: QuickInteractionWeixinRoute,
    ) -> WeixinChubModeDispatchResult:
        query_started_at = time.monotonic()
        wait_for_existing = False
        with self._status_condition:
            duplicate = self._submission_index.get(message_id)
            if duplicate is not None:
                if duplicate.delivery_route_fingerprint != route_fingerprint:
                    return self._dispatch_failure("message_conflict")
                return WeixinChubModeDispatchResult(
                    disposition=duplicate.dispatch_disposition or "handled",
                    message=duplicate.message or None,
                )
            cached = self._ephemeral_status_replies.get(message_id)
            if cached is not None:
                cached_fingerprint, cached_message, _created_at = cached
                if cached_fingerprint != route_fingerprint:
                    return self._dispatch_failure("message_conflict")
                if cached_message is None:
                    wait_for_existing = True
                else:
                    return WeixinChubModeDispatchResult(
                        disposition="reply",
                        message=cached_message,
                    )
            else:
                inflight = sum(
                    value[1] is None
                    for value in self._ephemeral_status_replies.values()
                )
                if inflight >= MAX_EPHEMERAL_STATUS_INFLIGHT:
                    return WeixinChubModeDispatchResult(
                        disposition="reply",
                        message="Status: Busy. Try again later.",
                    )
                self._ephemeral_status_replies[message_id] = (
                    route_fingerprint,
                    None,
                    time.monotonic(),
                )
        if wait_for_existing:
            return self._wait_for_ephemeral_reply(
                message_id,
                route_fingerprint,
            ) or WeixinChubModeDispatchResult(
                disposition="reply",
                message="Status: In progress. Try again later.",
            )

        self._refresh_chub_cache()
        try:
            task_snapshot = self.quick_interactions.weixin_task_status_snapshot(
                delivery_route
            )
            with self._status_condition:
                self._task_status_cache[route_fingerprint] = (
                    task_snapshot,
                    utc_now(),
                )
        except Exception:
            LOGGER.warning("Unable to snapshot Weixin tasks", exc_info=True)
        try:
            message = self._format_chub_overview(
                route_fingerprint,
                elapsed_ms=max(
                    1,
                    round((time.monotonic() - query_started_at) * 1000),
                ),
            )
        except Exception:
            LOGGER.warning("Unable to format Chub status overview", exc_info=True)
            message = "Status: Failed to build the Chub overview. Try again later."
        with self._status_condition:
            self._ephemeral_status_replies[message_id] = (
                route_fingerprint,
                message,
                time.monotonic(),
            )
            self._prune_ephemeral_status_replies(message_id)
            self._status_condition.notify_all()
        return WeixinChubModeDispatchResult(disposition="reply", message=message)

    def _dispatch_chub_usage(
        self,
        *,
        message_id: str,
        route_fingerprint: str,
    ) -> WeixinChubModeDispatchResult:
        wait_for_existing = False
        with self._status_condition:
            duplicate = self._submission_index.get(message_id)
            if duplicate is not None:
                if duplicate.delivery_route_fingerprint != route_fingerprint:
                    return self._dispatch_failure("message_conflict")
                return WeixinChubModeDispatchResult(
                    disposition=duplicate.dispatch_disposition or "handled",
                    message=duplicate.message or None,
                )
            cached = self._ephemeral_usage_replies.get(message_id)
            if cached is not None:
                cached_fingerprint, cached_message, _created_at = cached
                if cached_fingerprint != route_fingerprint:
                    return self._dispatch_failure("message_conflict")
                if cached_message is None:
                    wait_for_existing = True
                else:
                    return WeixinChubModeDispatchResult(
                        disposition="reply",
                        message=cached_message,
                    )
            else:
                inflight = sum(
                    value[1] is None
                    for value in self._ephemeral_usage_replies.values()
                )
                if inflight >= MAX_EPHEMERAL_STATUS_INFLIGHT:
                    return WeixinChubModeDispatchResult(
                        disposition="reply",
                        message="Usage: Busy. Try again later.",
                    )
                self._ephemeral_usage_replies[message_id] = (
                    route_fingerprint,
                    None,
                    time.monotonic(),
                )
        if wait_for_existing:
            return self._wait_for_ephemeral_usage_reply(
                message_id,
                route_fingerprint,
            ) or WeixinChubModeDispatchResult(
                disposition="reply",
                message="Usage: In progress. Try again later.",
            )

        try:
            message = detailed_usage_message(self._read_ai_usage(force=False))
        except Exception:
            LOGGER.warning("Unable to read complete AI usage", exc_info=True)
            message = "Weekly Unavailable"
        with self._status_condition:
            self._ephemeral_usage_replies[message_id] = (
                route_fingerprint,
                message,
                time.monotonic(),
            )
            self._prune_ephemeral_usage_replies(message_id)
            self._status_condition.notify_all()
        return WeixinChubModeDispatchResult(disposition="reply", message=message)

    def _wait_for_ephemeral_reply(
        self,
        message_id: str,
        route_fingerprint: str,
    ) -> WeixinChubModeDispatchResult | None:
        """Replay an in-flight/recent status query before any persistent route."""
        with self._status_condition:
            cached = self._ephemeral_status_replies.get(message_id)
            if cached is None:
                return None
            cached_fingerprint, cached_message, _created_at = cached
            if cached_fingerprint != route_fingerprint:
                return self._dispatch_failure("message_conflict")
            if cached_message is None:
                completed = self._status_condition.wait_for(
                    lambda: (
                        self._ephemeral_status_replies.get(
                            message_id,
                            ("", None, 0),
                        )[1]
                        is not None
                    ),
                    timeout=CODEX_STATUS_TIMEOUT_SECONDS,
                )
                cached = self._ephemeral_status_replies.get(message_id)
                if not completed or cached is None or cached[1] is None:
                    return WeixinChubModeDispatchResult(
                        disposition="reply",
                        message="Status: In progress. Try again later.",
                    )
                cached_message = cached[1]
            return WeixinChubModeDispatchResult(
                disposition="reply",
                message=cached_message,
            )

    def _wait_for_ephemeral_usage_reply(
        self,
        message_id: str,
        route_fingerprint: str,
    ) -> WeixinChubModeDispatchResult | None:
        with self._status_condition:
            cached = self._ephemeral_usage_replies.get(message_id)
            if cached is None:
                return None
            cached_fingerprint, cached_message, _created_at = cached
            if cached_fingerprint != route_fingerprint:
                return self._dispatch_failure("message_conflict")
            if cached_message is None:
                completed = self._status_condition.wait_for(
                    lambda: (
                        self._ephemeral_usage_replies.get(
                            message_id,
                            ("", None, 0),
                        )[1]
                        is not None
                    ),
                    timeout=CODEX_STATUS_TIMEOUT_SECONDS,
                )
                cached = self._ephemeral_usage_replies.get(message_id)
                if not completed or cached is None or cached[1] is None:
                    return WeixinChubModeDispatchResult(
                        disposition="reply",
                        message="Usage: In progress. Try again later.",
                    )
                cached_message = cached[1]
            return WeixinChubModeDispatchResult(
                disposition="reply",
                message=cached_message,
            )

    def _prune_ephemeral_usage_replies(self, protected_id: str) -> None:
        completed = [
            (key, value)
            for key, value in self._ephemeral_usage_replies.items()
            if value[1] is not None and key != protected_id
        ]
        excess = sum(
            value[1] is not None
            for value in self._ephemeral_usage_replies.values()
        ) - MAX_EPHEMERAL_STATUS_REPLIES
        for key, _value in sorted(completed, key=lambda item: item[1][2])[
            : max(0, excess)
        ]:
            self._ephemeral_usage_replies.pop(key, None)

    def _ephemeral_reply_now(
        self,
        message_id: str,
        route_fingerprint: str,
    ) -> tuple[bool, WeixinChubModeDispatchResult]:
        with self._status_condition:
            cached = self._ephemeral_status_replies.get(message_id)
        if cached is None:
            return False, WeixinChubModeDispatchResult(disposition="pass")
        cached_fingerprint, cached_message, _created_at = cached
        if cached_fingerprint != route_fingerprint:
            return True, self._dispatch_failure("message_conflict")
        return True, WeixinChubModeDispatchResult(
            disposition="reply",
            message=cached_message or "Status: In progress. Try again later.",
        )

    def _prune_ephemeral_status_replies(self, protected_id: str) -> None:
        completed = [
            (key, value)
            for key, value in self._ephemeral_status_replies.items()
            if value[1] is not None and key != protected_id
        ]
        excess = sum(
            value[1] is not None
            for value in self._ephemeral_status_replies.values()
        ) - MAX_EPHEMERAL_STATUS_REPLIES
        for key, _value in sorted(completed, key=lambda item: item[1][2])[
            : max(0, excess)
        ]:
            self._ephemeral_status_replies.pop(key, None)

    def _refresh_chub_cache(
        self,
        *,
        include_ai_usage: bool = True,
    ) -> tuple[bool, str | None]:
        with self._status_condition:
            if self._status_refreshing:
                completed = self._status_condition.wait_for(
                    lambda: not self._status_refreshing,
                    timeout=CODEX_STATUS_TIMEOUT_SECONDS,
                )
                if not completed:
                    return False, "刷新未完成，已返回现有缓存"
                return self._status_refresh_succeeded, self._status_refresh_note
            self._status_refreshing = True

        results: queue.Queue[
            tuple[str, _ChubCollectedSnapshot | None, Exception | None]
        ] = queue.Queue()

        def collect(name: str, reader: Callable[[], object]) -> None:
            try:
                value = reader()
                if name == "account":
                    if isinstance(value, AiUsageData):
                        checked_at = value.checked_at or utc_now()
                        successful = value.status == "available" and not value.stale
                    else:
                        quota, usage = value
                        checked_at = min(quota.checked_at, usage.checked_at)
                        successful = (
                            quota.status == "available"
                            and usage.status == "available"
                            and quota.message is None
                            and usage.message is None
                        )
                    snapshot = _ChubCollectedSnapshot(
                        value=value,
                        checked_at=checked_at,
                        successful=successful,
                    )
                else:
                    snapshot = _ChubCollectedSnapshot(
                        value=value,
                        checked_at=utc_now(),
                    )
                results.put((name, snapshot, None))
            except Exception as exc:
                results.put((name, None, exc))

        with self._lock:
            configuration = self._state.configuration.model_copy(deep=True)
            current_session_id = self._state.session_id
            slots = [entry.model_copy(deep=True) for entry in self._state.session_slots]

        readers: dict[str, Callable[[], object]] = {
            "readiness": self.status,
            "sessions": lambda: self._collect_assigned_session_snapshot(
                configuration,
                current_session_id,
                slots,
            ),
        }
        if self.system_status_reader is not None:
            readers["system"] = self.system_status_reader
        if include_ai_usage and (
            self.ai_usage_reader is not None or self.codex_account_reader is not None
        ):
            readers["account"] = lambda: self._read_ai_usage(force=False)
        for name, reader in readers.items():
            try:
                threading.Thread(
                    target=collect,
                    args=(name, reader),
                    daemon=True,
                    name=f"chub-status-refresh-{name}",
                ).start()
            except RuntimeError as exc:
                results.put((name, None, exc))

        collected: dict[str, _ChubCollectedSnapshot] = {}
        deadline = time.monotonic() + CODEX_STATUS_TIMEOUT_SECONDS
        while len(collected) < len(readers):
            try:
                name, value, error = results.get(
                    timeout=max(0.0, deadline - time.monotonic())
                )
            except queue.Empty:
                break
            if error is not None:
                LOGGER.warning(
                    "Unable to refresh Chub %s snapshot: %s",
                    name,
                    error,
                )
            elif value is not None:
                collected[name] = value

        succeeded = len(collected) == len(readers) and all(
            item.successful for item in collected.values()
        )
        note = (
            "已刷新"
            if succeeded
            else "刷新失败" if not collected else "部分数据刷新失败"
        )
        with self._status_condition:
            for name, snapshot in collected.items():
                if snapshot.successful or name not in self._status_cache:
                    self._status_cache[name] = (
                        snapshot.value,
                        snapshot.checked_at,
                    )
            self._status_refresh_succeeded = succeeded
            self._status_refresh_note = note
            self._status_refreshing = False
            self._status_condition.notify_all()
        return succeeded, note

    def _collect_assigned_session_snapshot(
        self,
        configuration: WeixinChubModeRuntimeConfig,
        current_session_id: str | None,
        slots: list[WeixinChubModeSessionSlot],
    ) -> tuple[_ChubSessionSnapshot, ...]:
        sessions = {
            session.id: session
            for session in self.codex_manager.list_sessions()
            if self._session_matches_configuration(session, configuration)
        }
        slots_by_session_id = {entry.session_id: entry.slot for entry in slots}
        return tuple(
            _ChubSessionSnapshot(
                slot=slots_by_session_id[session.id],
                session_id=session.id,
                title=build_session_title(
                    getattr(session, "title", None)
                    or "Unnamed Session",
                    self.settings.openclaw.weixin_chub_mode.session_name_max_width,
                ),
                state=self._codex_session_dispatch_state(session),
                current=session.id == current_session_id,
            )
            for session in sessions_newest_first(sessions.values())
            if session.id in slots_by_session_id
        )

    def verify_system_upgrade_readiness(self) -> None:
        """Confirm the Session snapshot used by Weixin can read after an upgrade."""
        with self._lock:
            configuration = self._state.configuration.model_copy(deep=True)
            current_session_id = self._state.session_id
            slots = [entry.model_copy(deep=True) for entry in self._state.session_slots]
        self._collect_assigned_session_snapshot(
            configuration,
            current_session_id,
            slots,
        )

    def _schedule_session_snapshot_refresh(self) -> None:
        def refresh() -> None:
            with self._lock:
                configuration = self._state.configuration.model_copy(deep=True)
                current_session_id = self._state.session_id
                slots = [
                    item.model_copy(deep=True) for item in self._state.session_slots
                ]
            try:
                value = self._collect_assigned_session_snapshot(
                    configuration,
                    current_session_id,
                    slots,
                )
            except Exception:
                LOGGER.info("Unable to update Chub session snapshot", exc_info=True)
                return
            with self._status_condition:
                self._status_cache["sessions"] = (value, utc_now())

        try:
            threading.Thread(
                target=refresh,
                daemon=True,
                name="chub-session-snapshot-update",
            ).start()
        except RuntimeError:
            LOGGER.info("Unable to start Chub session snapshot update")

    def _format_chub_overview(
        self,
        route_fingerprint: str,
        *,
        elapsed_ms: int,
    ) -> str:
        with self._status_condition:
            cache = dict(self._status_cache)
            task_cached = self._task_status_cache.get(route_fingerprint)
        with self._lock:
            failed_restart_notifications = sum(
                item.delivery_route_fingerprint == route_fingerprint
                and item.notification_status in {"failed", "skipped"}
                for item in self._state.restart_operations
            )
            failed_stop_notifications = sum(
                item.delivery_route_fingerprint == route_fingerprint
                and item.notification_status in {"failed", "skipped"}
                for item in self._state.stop_operations
            )

        readiness_cached = cache.get("readiness")
        readiness = readiness_cached[0] if readiness_cached is not None else None
        system_cached = cache.get("system")
        system = system_cached[0] if system_cached is not None else None
        memory_percent = (
            float(getattr(system.system, "memory_percent", 0.0))
            if system is not None
            else None
        )
        disk_percent = (
            float(getattr(system.system, "disk_percent", 0.0))
            if system is not None
            else None
        )
        tasks = task_cached[0] if task_cached is not None else None
        running_tasks = {
            session_id: summary
            for session_id, summary in getattr(tasks, "running_tasks", ())
        }
        session_cached = cache.get("sessions")
        if session_cached is None:
            overview_sessions = None
        else:
            sessions = session_cached[0]
            overview_sessions = tuple(
                ChubOverviewSession(
                    slot=item.slot,
                    title=item.title,
                    state=(
                        "Busy"
                        if self.quick_interactions.is_running(item.session_id)
                        else item.state
                    ),
                    current=item.current,
                    task_summary=(
                        build_task_name(
                            running_tasks[item.session_id],
                            self.settings.openclaw.weixin_chub_mode.task_name_max_width,
                        )
                        if item.session_id in running_tasks
                        else None
                    ),
                )
                for item in sessions
            )
        account_cached = cache.get("account")
        account_message = (
            "Weekly Unavailable"
            if account_cached is None
            else self._usage_message(account_cached[0])
        )
        try:
            overview_requests = tuple(
                ChubOverviewRequest(
                    slot=item.slot,
                    title=build_session_title(
                        item.title,
                        self.settings.openclaw.weixin_chub_mode.session_name_max_width,
                    ),
                )
                for item in self.request_backlog.list_active()
            )
        except RequestBacklogError:
            overview_requests = None
        return format_chub_overview(
            elapsed_ms=elapsed_ms,
            readiness=readiness,
            memory_percent=memory_percent,
            disk_percent=disk_percent,
            failed_restart_notifications=failed_restart_notifications,
            failed_stop_notifications=failed_stop_notifications,
            sessions=overview_sessions,
            usage_message=account_message,
            requests=overview_requests,
        )

    _format_elapsed_time = staticmethod(format_elapsed_time)

    def _dispatch_chub_sync(
        self,
        *,
        message_id: str,
        correlation_id: str | None,
        route_fingerprint: str,
        source_ip: str,
    ) -> WeixinChubModeDispatchResult:
        ephemeral = self._wait_for_ephemeral_reply(
            message_id,
            route_fingerprint,
        )
        if ephemeral is not None:
            return ephemeral
        with self._slot_lock:
            operation_id = uuid4().hex
            with self._lock:
                if self._state_error:
                    return self._dispatch_failure("state_unavailable")
                found_ephemeral, immediate_ephemeral = self._ephemeral_reply_now(
                    message_id,
                    route_fingerprint,
                )
                if found_ephemeral:
                    return immediate_ephemeral
                duplicate = self._find_submission(message_id)
                if duplicate is not None:
                    if duplicate.delivery_route_fingerprint != route_fingerprint:
                        return self._dispatch_failure("message_conflict")
                    return WeixinChubModeDispatchResult(
                        disposition=duplicate.dispatch_disposition or "reply",
                        message=duplicate.message or None,
                    )
                configuration = self._state.configuration.model_copy(deep=True)
                original_slots = [
                    item.model_copy(deep=True) for item in self._state.session_slots
                ]
                original_current = self._state.session_id
                now = utc_now()
                reservation = WeixinChubModeSubmission(
                    message_id=message_id,
                    correlation_id=correlation_id,
                    operation_id=operation_id,
                    delivery_route_fingerprint=route_fingerprint,
                    status="reserved",
                    code="submission_interrupted",
                    message="Sync: Already in progress. Try again later.",
                    created_at=now,
                    updated_at=now,
                )
                next_state = self._state.model_copy(deep=True)
                next_state.submissions.append(reservation)
                self._log_dispatch(operation_id, "requested", source_ip)
                try:
                    self._write_state(next_state)
                except OSError:
                    self._state_error = True
                    self._log_dispatch(operation_id, "failed", source_ip)
                    return self._dispatch_failure("state_unavailable")
                self._state = next_state
                self._log_dispatch(operation_id, "started", source_ip)
            try:
                account_results: queue.Queue[str] = queue.Queue(maxsize=1)

                def read_account() -> None:
                    try:
                        account_results.put(
                            self._usage_message(self._read_ai_usage(force=False))
                        )
                    except Exception:
                        LOGGER.warning("Codex usage check failed", exc_info=True)
                        account_results.put("Weekly Unavailable")

                deadline = time.monotonic() + CODEX_STATUS_TIMEOUT_SECONDS
                try:
                    threading.Thread(target=read_account, daemon=True).start()
                except RuntimeError:
                    LOGGER.warning(
                        "Unable to start Codex usage check during slot synchronization"
                    )
                    account_results.put("Weekly Unavailable")
                sessions = self.codex_manager.list_sessions()
                synced = self._build_synced_slots(
                    configuration,
                    original_slots,
                    original_current,
                    sessions,
                )
                verified_sessions = self.codex_manager.list_sessions()
                if self._build_synced_slots(
                    configuration,
                    original_slots,
                    original_current,
                    verified_sessions,
                ) != synced:
                    raise RuntimeError(
                        "Session candidates changed during synchronization"
                    )
                sessions = verified_sessions
            except Exception:
                LOGGER.warning("Unable to synchronize Chub session slots", exc_info=True)
                with self._lock:
                    return self._remember_fixed_reply(
                        message_id=message_id,
                        correlation_id=correlation_id,
                        operation_id=operation_id,
                        route_fingerprint=route_fingerprint,
                        source_ip=source_ip,
                        message=(
                            "Sync: Failed. Existing Session slots were not changed. "
                            "Try again later."
                        ),
                        code="chub_slots_synced",
                        failed=True,
                    )
            try:
                usage_message = account_results.get(
                    timeout=max(0.0, deadline - time.monotonic())
                )
            except queue.Empty:
                LOGGER.warning("Codex usage check timed out during slot synchronization")
                usage_message = "Weekly Unavailable"
            with self._lock:
                if (
                    self._state.configuration != configuration
                    or self._state.session_slots != original_slots
                    or self._state.session_id != original_current
                ):
                    return self._remember_fixed_reply(
                        message_id=message_id,
                        correlation_id=correlation_id,
                        operation_id=operation_id,
                        route_fingerprint=route_fingerprint,
                        source_ip=source_ip,
                        message=(
                            "Sync: Not applied because Session slots changed. "
                            "Send sync again."
                        ),
                        code="chub_slots_synced",
                        failed=True,
                    )
                removed = len(
                    {item.session_id for item in original_slots}
                    - {item.session_id for item in synced}
                )
                added = len(
                    {item.session_id for item in synced}
                    - {item.session_id for item in original_slots}
                )
                next_state = self._state.model_copy(deep=True)
                next_state.session_slots = synced
                if original_current and all(
                    item.session_id != original_current for item in synced
                ):
                    next_state.session_id = None
                visible = self._session_snapshot_from_list(
                    configuration,
                    next_state.session_id,
                    synced,
                    sessions,
                )
                status = (
                    "Sync: No changes."
                    if not removed and not added
                    else (
                        f"Sync: Completed · Removed {removed} · Added {added} · "
                        f"Current {len(synced)}"
                    )
                )
                sessions_message = self._format_session_blocks(
                    (
                        (item.slot, item.title, item.state, item.current)
                        for item in visible
                    )
                )
                message = f"{status}\n\n{sessions_message}\n\n{usage_message}"
                now = utc_now()
                record = reservation.model_copy(
                    update={
                        "status": "routed",
                        "code": "chub_slots_synced",
                        "message": message,
                        "http_status": 200,
                        "dispatch_disposition": "reply",
                        "updated_at": now,
                    }
                )
                next_state.submissions = [
                    record if item.message_id == message_id else item
                    for item in next_state.submissions
                ]
                try:
                    self._write_state(next_state)
                except OSError:
                    self._state_error = True
                    self._log_dispatch(operation_id, "failed", source_ip)
                    return self._dispatch_failure("state_unavailable")
                self._state = next_state
                self._log_dispatch(operation_id, "succeeded", source_ip)
                result = WeixinChubModeDispatchResult(
                    disposition="reply",
                    message=message,
                )
            with self._status_condition:
                self._status_cache["sessions"] = (visible, utc_now())
            return result

    def _build_synced_slots(
        self,
        configuration: WeixinChubModeRuntimeConfig,
        original_slots: list[WeixinChubModeSessionSlot],
        current_session_id: str | None,
        sessions: list[object],
    ) -> list[WeixinChubModeSessionSlot]:
        eligible = {
            session.id: session
            for session in sessions
            if self._session_matches_configuration(session, configuration)
        }
        retained: list[WeixinChubModeSessionSlot] = []
        used_slots: set[int] = set()
        used_sessions: set[str] = set()
        for entry in sorted(original_slots, key=lambda item: item.slot):
            if (
                entry.session_id in eligible
                and entry.slot not in used_slots
                and entry.session_id not in used_sessions
            ):
                retained.append(entry.model_copy(deep=True))
                used_slots.add(entry.slot)
                used_sessions.add(entry.session_id)
        weixin_session_ids = self.quick_interactions.weixin_session_ids()
        candidates = sorted(
            (
                session
                for session in eligible.values()
                if session.id not in used_sessions
                and self._codex_session_dispatch_state(session) != "Unavailable"
            ),
            key=lambda session: (
                session.id != current_session_id,
                session.id not in weixin_session_ids,
                session.id,
            ),
        )
        free_slots = [
            slot
            for slot in range(1, MAX_WEIXIN_SESSION_SLOTS + 1)
            if slot not in used_slots
        ]
        retained.extend(
            WeixinChubModeSessionSlot(slot=slot, session_id=session.id)
            for slot, session in zip(free_slots, candidates, strict=False)
        )
        return sorted(retained, key=lambda item: item.slot)

    def _session_snapshot_from_list(
        self,
        configuration: WeixinChubModeRuntimeConfig,
        current_session_id: str | None,
        slots: list[WeixinChubModeSessionSlot],
        sessions: list[object],
    ) -> tuple[_ChubSessionSnapshot, ...]:
        eligible = {
            session.id: session
            for session in sessions
            if self._session_matches_configuration(session, configuration)
        }
        slots_by_session_id = {entry.session_id: entry.slot for entry in slots}
        return tuple(
            _ChubSessionSnapshot(
                slot=slots_by_session_id[session.id],
                session_id=session.id,
                title=build_session_title(
                    getattr(session, "title", None)
                    or "Unnamed Session",
                    self.settings.openclaw.weixin_chub_mode.session_name_max_width,
                ),
                state=self._codex_session_dispatch_state(session),
                current=session.id == current_session_id,
            )
            for session in sessions_newest_first(eligible.values())
            if session.id in slots_by_session_id
        )

    def _codex_operation_message(
        self,
        operation_status: str,
        *,
        fill_session_candidates: bool = False,
    ) -> tuple[str, bool]:
        codex_message, codex_failed = self._read_codex_status_message(
            self._state.configuration.model_copy(deep=True),
            self._state.session_id,
            fill_candidates=fill_session_candidates,
        )
        return codex_operation_message(operation_status, codex_message), codex_failed

    @staticmethod
    def _has_command_status_suffix(message: str) -> bool:
        status_index = max(
            message.rfind("\n\nSessions\n\n"),
            message.rfind("\n\nNo sessions\n\n"),
        )
        if status_index < 0:
            return False
        return "\n\nWeekly " in message[status_index:]

    @staticmethod
    def _has_inline_task_context(message: str) -> bool:
        first_paragraph = message.partition("\n\n")[0]
        if (
            "\n\nSessions\n\n" in message
            and "\n\nTask · " in message
            and (
                first_paragraph == "Submitted"
                or "Task submitted." in first_paragraph
                or "task was resubmitted." in first_paragraph
            )
        ):
            return True
        task_index = message.find("\nTask · ")
        if task_index < 0:
            return False
        status_index = max(
            message.rfind("\n\nSessions\n\n"),
            message.rfind("\n\nNo sessions\n\n"),
        )
        return status_index < 0 or task_index < status_index

    def _refresh_replayed_task_context(
        self,
        submission: WeixinChubModeSubmission,
    ) -> str | None:
        message = submission.message or None
        if not message or not self._has_inline_task_context(message):
            return message
        paragraphs = message.split("\n\n")
        if "Sessions" in paragraphs:
            target_slot = submission.session_slot
            target_matches = (
                target_slot is not None
                and submission.session_id is not None
                and any(
                    entry.slot == target_slot
                    and entry.session_id == submission.session_id
                    for entry in self._state.session_slots
                )
            )
            current_slot = next(
                (
                    entry.slot
                    for entry in self._state.session_slots
                    if entry.session_id == self._state.session_id
                ),
                None,
            )
            refreshed: list[str] = []
            skip_target_task = False
            for paragraph in paragraphs:
                session_line = paragraph.removeprefix("▶ ")
                slot_label, separator, _title = session_line.partition(" · ")
                slot_text = slot_label.removesuffix(" !").removeprefix("S")
                if separator and slot_text.isdigit():
                    slot = int(slot_text)
                    if slot == target_slot and not target_matches:
                        skip_target_task = True
                        continue
                    paragraph = f"{'▶ ' if slot == current_slot else ''}{session_line}"
                    skip_target_task = False
                elif skip_target_task and paragraph.startswith("Task · "):
                    skip_target_task = False
                    continue
                refreshed.append(paragraph)
            if "Sessions" in refreshed and not any(
                paragraph.removeprefix("▶ ").startswith("S")
                and " · " in paragraph
                for paragraph in refreshed
            ):
                refreshed.remove("Sessions")
            return "\n\n".join(refreshed)
        for index in range(1, min(3, len(paragraphs))):
            session_line = paragraphs[index].removeprefix("▶ ")
            slot_label, separator, _title = session_line.partition(" · ")
            if not separator or not slot_label.startswith("S"):
                continue
            slot_text = slot_label[1:]
            if not slot_text.isdigit():
                continue
            slot = int(slot_text)
            slot_matches = submission.session_id is not None and any(
                entry.slot == slot and entry.session_id == submission.session_id
                for entry in self._state.session_slots
            )
            if not slot_matches:
                paragraphs.pop(index)
            else:
                current = self._state.session_id == submission.session_id
                paragraphs[index] = f"{'▶ ' if current else ''}{session_line}"
            if "Sessions" in paragraphs and not any(
                paragraph.removeprefix("▶ ").startswith("S")
                and " · " in paragraph
                for paragraph in paragraphs
            ):
                paragraphs.remove("Sessions")
            return "\n\n".join(paragraphs)
        lines = message.splitlines()
        for index in range(1, min(3, len(lines))):
            session_line = lines[index].removeprefix("▶ ")
            slot_label, separator, _title = session_line.partition(" · ")
            if not separator or not slot_label.startswith("S"):
                continue
            slot_text = slot_label[1:]
            if not slot_text.isdigit():
                continue
            slot = int(slot_text)
            slot_matches = submission.session_id is not None and any(
                entry.slot == slot and entry.session_id == submission.session_id
                for entry in self._state.session_slots
            )
            if not slot_matches:
                lines.pop(index)
            else:
                current = self._state.session_id == submission.session_id
                lines[index] = f"{'▶ ' if current else ''}{session_line}"
            if "Sessions" in lines and not any(
                line.removeprefix("▶ ").startswith("S") and " · " in line
                for line in lines
            ):
                lines.remove("Sessions")
            task_index = next(
                (
                    task_line_index
                    for task_line_index, line in enumerate(lines[1:], start=1)
                    if line.startswith("Task · ")
                ),
                None,
            )
            if task_index is None:
                return "\n".join(lines)
            context = "\n\n".join(line for line in lines[: task_index + 1] if line)
            remainder = "\n".join(lines[task_index + 1 :]).lstrip("\n")
            return f"{context}\n\n{remainder}" if remainder else context
        return message

    def _with_command_status_suffix(
        self,
        message: str,
        *,
        delivery_route: QuickInteractionWeixinRoute | None = None,
    ) -> str:
        message = format_fixed_reply(message)
        if message.startswith(
            (
                "Usage: ",
                "Stop: Scheduled.",
                "Stop: Already in progress.",
            )
        ):
            return message
        if not self._has_command_status_suffix(message):
            if self._state_error:
                status = "Sessions\n\nUnavailable\n\nWeekly Unavailable"
            else:
                status, _failed = self._read_codex_status_message(
                    self._state.configuration.model_copy(deep=True),
                    self._state.session_id,
                    fill_candidates=False,
                )
            message = codex_operation_message(message, status)
        return self._with_running_task_summaries(message, delivery_route)

    def _with_running_task_summaries(
        self,
        message: str,
        delivery_route: QuickInteractionWeixinRoute | None,
    ) -> str:
        if delivery_route is None or "Task · Running" not in message:
            return message
        try:
            task_snapshot = self.quick_interactions.weixin_task_status_snapshot(
                delivery_route
            )
        except Exception:
            LOGGER.warning(
                "Unable to snapshot Weixin tasks for command status",
                exc_info=True,
            )
            return message
        slots = self.session_slots_snapshot()
        for session_id, summary in getattr(task_snapshot, "running_tasks", ()):
            slot = slots.get(session_id)
            if slot is None:
                continue
            task_name = build_task_name(
                summary,
                self.settings.openclaw.weixin_chub_mode.task_name_max_width,
            )
            lines = message.splitlines()
            for index, line in enumerate(lines[:-1]):
                session_line = line.removeprefix("▶ ")
                if not session_line.startswith((f"S{slot} · ", f"S{slot} ! · ")):
                    continue
                task_index = index + 1
                while task_index < len(lines) and not lines[task_index]:
                    task_index += 1
                if task_index < len(lines) and lines[task_index] == "Task · Running":
                    lines[task_index] = f"Task · {task_name}"
                    break
            message = "\n".join(lines)
        return message

    def _finalize_fixed_command_result(
        self,
        command_kind: str,
        result: WeixinChubModeDispatchResult,
        delivery_route: QuickInteractionWeixinRoute,
    ) -> WeixinChubModeDispatchResult:
        if (
            command_kind not in FIXED_COMMAND_KINDS
            or command_kind == "help"
            or command_kind == "check"
            or command_kind == "usage"
            or command_kind == "text_control"
            or command_kind == "text_check"
            or command_kind == "model"
            or command_kind == "model_list"
            or command_kind == "model_levels"
            or command_kind == "model_use"
            or command_kind in {
                "request_cat",
                "request_archive",
                "request_delete",
                "upgrade",
            }
            or result.disposition != "reply"
            or not result.message
            or self._has_inline_task_context(result.message)
            or (
                command_kind in {"restart_web", "restart_worker", "restart_clawbot"}
                and result.message.startswith("Restart")
                and "Scheduled." in result.message
            )
        ):
            return result
        message = self._with_command_status_suffix(
            result.message,
            delivery_route=delivery_route,
        )
        if message == result.message:
            return result
        return result.model_copy(update={"message": message})

    def _with_failure_task_summary(
        self,
        result: WeixinChubModeDispatchResult,
        prompt: str,
        *,
        include_current_session: bool = True,
    ) -> WeixinChubModeDispatchResult:
        if not result.message:
            return result
        session_slot, session_title = (
            self._task_session_context()
            if include_current_session
            else (None, None)
        )
        return result.model_copy(
            update={
                "message": with_task_summary(
                    result.message,
                    prompt,
                    self.settings.openclaw.weixin_chub_mode.task_name_max_width,
                    session_slot=session_slot,
                    session_title=session_title,
                    current=session_slot is not None,
                )
            }
        )

    def _task_session_context(self) -> tuple[int | None, str | None]:
        if self._state_error or self._state.session_id is None:
            return None, None
        return self._session_context(self._state.session_id)

    def _session_context(
        self,
        session_id: str,
    ) -> tuple[int | None, str | None]:
        slot = self._slot_for_session(session_id)
        if slot is None:
            return None, None
        try:
            session = self.codex_manager.get_session(session_id)
        except Exception:
            return None, None
        if getattr(session, "id", None) != session_id:
            return None, None
        title = build_session_title(
            getattr(session, "title", None) or "Unnamed Session",
            self.settings.openclaw.weixin_chub_mode.session_name_max_width,
        )
        return slot, title

    _normalize_fixed_prompt = staticmethod(normalize_fixed_prompt)
    def _read_codex_status_message(
        self,
        configuration: WeixinChubModeRuntimeConfig,
        current_session_id: str | None,
        *,
        fill_candidates: bool = False,
    ) -> tuple[str, bool]:
        session_results: queue.Queue[tuple[str | None, bool]] = queue.Queue(maxsize=1)

        def read_sessions() -> None:
            try:
                session_results.put(
                    (
                        self._codex_sessions_message(
                            configuration,
                            current_session_id,
                            fill_candidates=fill_candidates,
                        ),
                        False,
                    )
                )
            except Exception:
                LOGGER.warning("Codex session status check failed", exc_info=True)
                session_results.put((None, True))

        started_at = time.monotonic()
        threading.Thread(target=read_sessions, daemon=True).start()

        account_failed = False
        try:
            message = self._usage_message(self._read_ai_usage(force=False))
        except Exception:
            LOGGER.warning("Codex usage check failed", exc_info=True)
            message = "Weekly Unavailable"
            account_failed = True

        try:
            remaining = max(
                0.0,
                CODEX_STATUS_TIMEOUT_SECONDS - (time.monotonic() - started_at),
            )
            sessions_message, sessions_failed = session_results.get(timeout=remaining)
            if sessions_message is None:
                sessions_message = "Sessions\n\nUnavailable"
        except queue.Empty:
            LOGGER.warning("Codex session status check timed out")
            sessions_message = "Sessions\n\nUnavailable"
            sessions_failed = True
        message = f"{sessions_message}\n\n{message}"
        return message, account_failed or sessions_failed

    def _codex_sessions_message(
        self,
        configuration: WeixinChubModeRuntimeConfig,
        current_session_id: str | None,
        *,
        fill_candidates: bool = False,
    ) -> str:
        visible, remaining = self._visible_codex_sessions(
            configuration,
            fill_candidates=fill_candidates,
        )
        if not visible:
            return "No sessions"
        return self._format_codex_sessions(visible, current_session_id, remaining)

    def _dispatch_codex_rename(
        self,
        *,
        message_id: str,
        correlation_id: str | None,
        route_fingerprint: str,
        source_ip: str,
        title: str | None,
    ) -> WeixinChubModeDispatchResult:
        operation_id = uuid4().hex
        self._log_dispatch(operation_id, "requested", source_ip)
        self._log_dispatch(operation_id, "started", source_ip)
        usage = "Usage: rename <title> (maximum 48 characters)."
        try:
            normalized_title = SessionRenameRequest(title=title).title
        except ValueError:
            return self._remember_fixed_reply(
                message_id=message_id,
                correlation_id=correlation_id,
                operation_id=operation_id,
                route_fingerprint=route_fingerprint,
                source_ip=source_ip,
                message=usage,
                code="codex_session_renamed",
                failed=True,
            )

        session_id = self._state.session_id
        if session_id is None:
            return self._remember_fixed_reply(
                message_id=message_id,
                correlation_id=correlation_id,
                operation_id=operation_id,
                route_fingerprint=route_fingerprint,
                source_ip=source_ip,
                message="Rename: Not completed because no Session is selected.",
                code="codex_session_renamed",
                failed=True,
            )
        session_slot = self._slot_for_session(session_id)
        if session_slot is None:
            return self._remember_fixed_reply(
                message_id=message_id,
                correlation_id=correlation_id,
                operation_id=operation_id,
                route_fingerprint=route_fingerprint,
                source_ip=source_ip,
                message=(
                    "Rename: Not completed because the current Session is not "
                    "registered. Send sync first."
                ),
                code="codex_session_renamed",
                failed=True,
            )

        try:
            current = self.codex_manager.get_session(session_id)
            if (
                current.id != session_id
                or not self._session_matches_configuration(
                    current,
                    self._state.configuration,
                )
            ):
                raise ValueError("Current Session configuration changed")
        except Exception:
            LOGGER.info("Codex rename target is no longer available", exc_info=True)
            return self._remember_fixed_reply(
                message_id=message_id,
                correlation_id=correlation_id,
                operation_id=operation_id,
                route_fingerprint=route_fingerprint,
                source_ip=source_ip,
                message=(
                    "Rename: Not completed because the current Session is unavailable."
                ),
                code="codex_session_renamed",
                failed=True,
            )

        self._log_rename(operation_id, "requested", session_id, source_ip)
        self._log_rename(operation_id, "started", session_id, source_ip)
        try:
            renamed = self.codex_manager.rename_session(
                session_id,
                normalized_title,
            )
        except Exception as exc:
            LOGGER.warning("Codex Session rename failed", exc_info=True)
            self._log_rename(operation_id, "failed", session_id, source_ip)
            if isinstance(exc, ApiError) and exc.code == "codex_session_writer_active":
                message = (
                    "Rename: Not completed. This is open in another app, "
                    "close it there to continue here."
                )
            else:
                message = (
                    "Rename: Failed. The current title was not changed. "
                    "Try again later."
                )
            return self._remember_fixed_reply(
                message_id=message_id,
                correlation_id=correlation_id,
                operation_id=operation_id,
                route_fingerprint=route_fingerprint,
                source_ip=source_ip,
                message=message,
                code="codex_session_renamed",
                failed=True,
            )
        self._log_rename(operation_id, "succeeded", session_id, source_ip)
        renamed_title = renamed.title or normalized_title
        message, _status_failed = self._codex_operation_message(
            f'Rename: Session {session_slot} renamed to "{renamed_title}".',
            fill_session_candidates=False,
        )
        return self._remember_fixed_reply(
            message_id=message_id,
            correlation_id=correlation_id,
            operation_id=operation_id,
            route_fingerprint=route_fingerprint,
            source_ip=source_ip,
            message=message,
            code="codex_session_renamed",
            session_id=session_id,
        )

    def _dispatch_codex_stop(
        self,
        *,
        message_id: str,
        correlation_id: str | None,
        route_fingerprint: str,
        source_ip: str,
        requested_index: int | None,
        invalid_usage: bool,
        delivery_route: QuickInteractionWeixinRoute,
    ) -> WeixinChubModeDispatchResult:
        with self._stop_lock:
            return self._dispatch_codex_stop_serialized(
                message_id=message_id,
                correlation_id=correlation_id,
                route_fingerprint=route_fingerprint,
                source_ip=source_ip,
                requested_index=requested_index,
                invalid_usage=invalid_usage,
                delivery_route=delivery_route,
            )

    def _dispatch_codex_stop_serialized(
        self,
        *,
        message_id: str,
        correlation_id: str | None,
        route_fingerprint: str,
        source_ip: str,
        requested_index: int | None,
        invalid_usage: bool,
        delivery_route: QuickInteractionWeixinRoute,
    ) -> WeixinChubModeDispatchResult:
        operation_id = uuid4().hex
        now = utc_now()
        record = WeixinChubModeSubmission(
            message_id=message_id,
            correlation_id=correlation_id,
            operation_id=operation_id,
            delivery_route_fingerprint=route_fingerprint,
            status="reserved",
            code="submission_interrupted",
            message=(
                "Stop: The result could not be confirmed. Send chub to check status."
            ),
            created_at=now,
            updated_at=now,
        )
        next_state = self._state.model_copy(deep=True)
        next_state.submissions.append(record)
        self._log_dispatch(operation_id, "requested", source_ip)
        self._log_dispatch(operation_id, "started", source_ip)
        try:
            self._write_state(next_state)
        except OSError:
            self._state_error = True
            self._log_dispatch(operation_id, "failed", source_ip)
            return self._dispatch_failure("state_unavailable")
        self._state = next_state

        def finish(
            message: str,
            *,
            failed: bool,
            session_id: str | None = None,
        ) -> WeixinChubModeDispatchResult:
            return self._remember_fixed_reply(
                message_id=message_id,
                correlation_id=correlation_id,
                operation_id=operation_id,
                route_fingerprint=route_fingerprint,
                source_ip=source_ip,
                message=message,
                code="codex_session_stopped",
                session_id=session_id,
                failed=failed,
            )

        if invalid_usage:
            return finish("Usage: stop <1-9|S1-S9|一-九>", failed=True)

        try:
            configuration = self._state.configuration.model_copy(deep=True)
            visible, _remaining = self._read_visible_codex_sessions(
                configuration,
                timeout_seconds=STOP_TARGET_TIMEOUT_SECONDS,
                fill_candidates=False,
            )
        except OSError:
            self._state_error = True
            self._log_dispatch(operation_id, "failed", source_ip)
            return self._dispatch_failure("state_unavailable")
        except Exception:
            LOGGER.warning("Codex stop target lookup failed", exc_info=True)
            return finish(
                "Stop: Not completed because the Session list is unavailable.",
                failed=True,
            )

        if requested_index is None:
            current_session_id = self._state.session_id
            if current_session_id is None:
                return finish(
                    "Stop: Not completed because no Session is selected.",
                    failed=True,
                )
            target_entry = next(
                (entry for entry in visible if entry[1].id == current_session_id),
                None,
            )
        else:
            target_entry = next(
                (entry for entry in visible if entry[0] == requested_index),
                None,
            )
        if target_entry is None:
            return finish(
                (
                    "Stop: Not completed because the current Session is unavailable."
                    if requested_index is None
                    else "Stop: Not completed because the Session number is invalid."
                ),
                failed=True,
            )

        target_slot, target, _listed_state = target_entry
        try:
            refreshed = self.codex_manager.get_session(target.id)
            if (
                refreshed.id != target.id
                or self._slot_for_session(refreshed.id) != target_slot
                or not self._session_matches_configuration(refreshed, configuration)
            ):
                raise ValueError("Session configuration changed")
        except Exception:
            LOGGER.info("Codex stop target is no longer available", exc_info=True)
            return finish(
                "Stop: Not completed because the target Session is unavailable.",
                failed=True,
            )

        if self.session_stopper is None:
            return finish(
                "Stop: Not completed because Session stopping is unavailable.",
                failed=True,
                session_id=refreshed.id,
            )
        if self.session_stop_notifier is None:
            return finish(
                "Stop: Not scheduled because result delivery is unavailable.",
                failed=True,
                session_id=refreshed.id,
            )
        try:
            route_error = (
                self.route_validator(delivery_route)
                if self.route_validator is not None
                else "unavailable"
            )
        except Exception:
            LOGGER.warning("Unable to validate Weixin stop route", exc_info=True)
            route_error = "unavailable"
        if route_error:
            return finish(
                "Stop: Not scheduled because the reply route is unavailable.",
                failed=True,
                session_id=refreshed.id,
            )

        with self._lock:
            active = next(
                (
                    item
                    for item in self._state.stop_operations
                    if item.session_id == refreshed.id
                    and item.status in {"pending", "started"}
                ),
                None,
            )
        if active is not None:
            return finish(
                "Stop: Already in progress. The result will be sent when completed.",
                failed=False,
                session_id=refreshed.id,
            )

        stop_operation = WeixinChubModeStopOperation(
            message_id=message_id,
            operation_id=operation_id,
            source_ip=source_ip,
            delivery_route_fingerprint=route_fingerprint,
            delivery_route=delivery_route.model_copy(deep=True),
            session_id=refreshed.id,
            session_slot=target_slot,
            created_at=now,
            updated_at=now,
        )
        with self._lock:
            next_state = self._state.model_copy(deep=True)
            next_state.stop_operations.append(stop_operation)
            try:
                self._write_state(next_state)
            except OSError:
                self._state_error = True
                self._log_dispatch(operation_id, "failed", source_ip)
                return self._dispatch_failure("state_unavailable")
            self._state = next_state

        reply_message = "Stop: Scheduled. The result will be sent when completed."
        try:
            self._start_stop_operation(operation_id)
        except RuntimeError:
            LOGGER.warning("Unable to start Codex Session stop worker", exc_info=True)
            self._complete_stop_operation(
                operation_id,
                status="failed",
                message=(
                    "Stop: Not completed because the background worker is unavailable."
                ),
                error="Session stop background worker is unavailable.",
                notify=False,
            )
            return finish(
                "Stop: Not scheduled because the background worker is unavailable.",
                failed=True,
                session_id=refreshed.id,
            )
        return finish(reply_message, failed=False, session_id=refreshed.id)

    def _start_stop_operation(self, operation_id: str) -> None:
        threading.Thread(
            target=self._run_stop_operation,
            args=(operation_id,),
            daemon=True,
            name=f"weixin-session-stop-{operation_id[:8]}",
        ).start()

    def _run_stop_operation(self, operation_id: str) -> None:
        with self._lock:
            next_state = self._state.model_copy(deep=True)
            operation = next(
                (
                    item
                    for item in next_state.stop_operations
                    if item.operation_id == operation_id and item.status == "pending"
                ),
                None,
            )
            if operation is None:
                return
            snapshot = operation.model_copy(deep=True)
            operation.status = "started"
            operation.updated_at = utc_now()
            try:
                self._write_state(next_state)
            except OSError:
                self._state_error = True
                for log_status in ("requested", "started", "failed"):
                    self._log_stop(
                        operation_id,
                        log_status,
                        snapshot.session_id,
                        snapshot.source_ip,
                    )
                LOGGER.warning("Unable to persist Codex Session stop start")
                return
            self._state = next_state

        self._log_stop(operation_id, "requested", snapshot.session_id, snapshot.source_ip)
        self._log_stop(operation_id, "started", snapshot.session_id, snapshot.source_ip)
        try:
            stopped = self.session_stopper(snapshot.session_id)
            if (
                getattr(stopped, "status", None) != "stopped"
                or getattr(stopped, "activity", None) != "idle"
            ):
                raise OSError("Session stop result was not final")
        except Exception:
            LOGGER.warning("Codex Session stop failed", exc_info=True)
            self._log_stop(operation_id, "failed", snapshot.session_id, snapshot.source_ip)
            self._complete_stop_operation(
                operation_id,
                status="failed",
                message=(
                    "Stop: Failed. The Session may have partially stopped. "
                    "Send chub before trying again."
                ),
                error="Session stop did not reach a confirmed final state.",
            )
            return

        self._log_stop(operation_id, "succeeded", snapshot.session_id, snapshot.source_ip)
        self._complete_stop_operation(
            operation_id,
            status="succeeded",
            message=f"Stop: Session {snapshot.session_slot} stopped.",
        )

    def _complete_stop_operation(
        self,
        operation_id: str,
        *,
        status: Literal["succeeded", "failed"],
        message: str,
        error: str | None = None,
        notify: bool = True,
    ) -> None:
        fallback_snapshot: WeixinChubModeStopOperation | None = None
        with self._lock:
            next_state = self._state.model_copy(deep=True)
            operation = next(
                (
                    item
                    for item in next_state.stop_operations
                    if item.operation_id == operation_id
                ),
                None,
            )
            if operation is None:
                return
            operation.status = status
            operation.error = error
            operation.notification_status = "pending" if notify else "failed"
            operation.notification_error = (
                None if notify else "Session stop result was not sent."
            )
            operation.updated_at = utc_now()
            snapshot = operation.model_copy(deep=True)
            try:
                self._write_state(next_state)
            except OSError:
                self._state_error = True
                LOGGER.warning(
                    "Unable to persist Weixin Session stop result",
                    exc_info=True,
                )
                if notify:
                    fallback_snapshot = snapshot
            else:
                self._state = next_state
        if fallback_snapshot is not None:
            self._send_stop_notification(fallback_snapshot, message)
        elif notify:
            self._deliver_stop_notification(operation_id, message)

    def _deliver_stop_notification(self, operation_id: str, message: str) -> None:
        fallback_snapshot: WeixinChubModeStopOperation | None = None
        with self._lock:
            next_state = self._state.model_copy(deep=True)
            operation = next(
                (
                    item
                    for item in next_state.stop_operations
                    if item.operation_id == operation_id
                    and item.notification_status == "pending"
                ),
                None,
            )
            if operation is None:
                return
            operation.notification_status = "sending"
            operation.updated_at = utc_now()
            snapshot = operation.model_copy(deep=True)
            try:
                self._write_state(next_state)
            except OSError:
                self._state_error = True
                LOGGER.warning(
                    "Unable to persist Weixin Session stop notification start",
                    exc_info=True,
                )
                fallback_snapshot = snapshot
            else:
                self._state = next_state

        if fallback_snapshot is not None:
            self._send_stop_notification(fallback_snapshot, message)
            return

        notification_status, notification_error = self._send_stop_notification(
            snapshot,
            message,
        )

        with self._lock:
            next_state = self._state.model_copy(deep=True)
            operation = next(
                (
                    item
                    for item in next_state.stop_operations
                    if item.operation_id == operation_id
                ),
                None,
            )
            if operation is not None:
                operation.notification_status = notification_status
                operation.notification_error = (
                    notification_error[:1_000] if notification_error else None
                )
                operation.updated_at = utc_now()
                try:
                    self._write_state(next_state)
                except OSError:
                    self._state_error = True
                    LOGGER.warning(
                        "Unable to persist Weixin Session stop notification result",
                        exc_info=True,
                    )
                else:
                    self._state = next_state

    def _send_stop_notification(
        self,
        snapshot: WeixinChubModeStopOperation,
        message: str,
    ) -> tuple[str, str | None]:
        def message_factory() -> str:
            status_message = self._codex_operation_message(
                message,
                fill_session_candidates=False,
            )[0]
            return self._with_command_status_suffix(
                status_message,
                delivery_route=snapshot.delivery_route,
            )

        notification_operation_id = f"{snapshot.operation_id}:weixin"
        for log_status in ("requested", "started"):
            write_operation(
                operation_id=notification_operation_id,
                action="weixin_chub_stop_notification",
                status=log_status,
                target=self.settings.node.id,
                source_ip=snapshot.source_ip,
            )
        try:
            result = self.session_stop_notifier(
                snapshot.delivery_route,
                message_factory,
            )
            notification_status = getattr(result, "status", "failed")
            notification_error = getattr(result, "error", None)
            if notification_status not in {"sent", "failed", "skipped"}:
                notification_status = "failed"
                notification_error = "Session stop result returned an invalid status."
        except Exception:
            LOGGER.warning("Weixin Session stop notification failed", exc_info=True)
            notification_status = "failed"
            notification_error = "Session stop result was not delivered."

        write_operation(
            operation_id=notification_operation_id,
            action="weixin_chub_stop_notification",
            status="succeeded" if notification_status == "sent" else "failed",
            target=self.settings.node.id,
            source_ip=snapshot.source_ip,
        )
        return notification_status, notification_error

    @staticmethod
    def _request_status_label(status: str) -> str:
        return {
            "ready": "Ready",
            "running": "Running",
            "succeeded": "Done",
            "failed": "Failed",
        }.get(status, "Unavailable")

    def _remember_request_reply(
        self,
        *,
        message_id: str,
        correlation_id: str | None,
        route_fingerprint: str,
        source_ip: str,
        message: str,
        failed: bool,
    ) -> WeixinChubModeDispatchResult:
        operation_id = uuid4().hex
        self._log_dispatch(operation_id, "requested", source_ip)
        self._log_dispatch(operation_id, "started", source_ip)
        result = self._remember_fixed_reply(
            message_id=message_id,
            correlation_id=correlation_id,
            operation_id=operation_id,
            route_fingerprint=route_fingerprint,
            source_ip=source_ip,
            message=message,
            code="submitted",
            failed=failed,
        )
        return result

    def _dispatch_request_cat(
        self,
        *,
        message_id: str,
        correlation_id: str | None,
        route_fingerprint: str,
        source_ip: str,
        requested_index: int | None,
        invalid_usage: bool,
    ) -> WeixinChubModeDispatchResult:
        if invalid_usage or requested_index is None:
            return self._remember_request_reply(
                message_id=message_id,
                correlation_id=correlation_id,
                route_fingerprint=route_fingerprint,
                source_ip=source_ip,
                message="Usage: cat <R1-R9> / 查看需求 <1-9|R1-R9|一-九>",
                failed=True,
            )
        try:
            item = self.request_backlog.get(requested_index)
        except RequestBacklogNotFound:
            message = f"Request: R{requested_index} was not found."
            failed = True
        except RequestBacklogError:
            message = "Request: Unavailable. Try again later."
            failed = True
        else:
            message = (
                "Request\n\n"
                f"R{item.slot} · {item.title}\n\n"
                f"Status · {self._request_status_label(item.status)}\n\n"
                f"{item.content}"
            )
            failed = False
        result = self._remember_request_reply(
            message_id=message_id,
            correlation_id=correlation_id,
            route_fingerprint=route_fingerprint,
            source_ip=source_ip,
            message=message,
            failed=failed,
        )
        return result

    def _dispatch_request_archive(
        self,
        *,
        message_id: str,
        correlation_id: str | None,
        route_fingerprint: str,
        source_ip: str,
        requested_index: int | None,
        invalid_usage: bool,
    ) -> WeixinChubModeDispatchResult:
        if invalid_usage or requested_index is None:
            return self._remember_request_reply(
                message_id=message_id,
                correlation_id=correlation_id,
                route_fingerprint=route_fingerprint,
                source_ip=source_ip,
                message=(
                    "Usage: archive <R1-R9> / "
                    "归档需求 <1-9|R1-R9|一-九>"
                ),
                failed=True,
            )
        archive_operation_id = uuid4().hex
        for status in ("requested", "started"):
            write_operation(
                operation_id=archive_operation_id,
                action="request_backlog_archive",
                status=status,
                target=f"R{requested_index}",
                source_ip=source_ip,
            )
        try:
            item = self.request_backlog.archive(requested_index)
        except RequestBacklogNotFound:
            message = f"Archive: Request R{requested_index} was not found."
            failed = True
        except RequestBacklogBusy:
            message = f"Archive: Request R{requested_index} is running."
            failed = True
        except RequestBacklogError:
            message = "Archive: Request backlog is unavailable."
            failed = True
        else:
            message = (
                f"Archive: Request R{item.slot} archived.\n\n"
                f"Request · R{item.slot} · {item.title}"
            )
            failed = False
        result = self._remember_request_reply(
            message_id=message_id,
            correlation_id=correlation_id,
            route_fingerprint=route_fingerprint,
            source_ip=source_ip,
            message=message,
            failed=failed,
        )
        write_operation(
            operation_id=archive_operation_id,
            action="request_backlog_archive",
            status=(
                "failed"
                if failed or self._state_error or result.message != message
                else "succeeded"
            ),
            target=f"R{requested_index}",
            source_ip=source_ip,
        )
        return result

    def _dispatch_request_delete(
        self,
        *,
        message_id: str,
        correlation_id: str | None,
        route_fingerprint: str,
        source_ip: str,
        requested_index: int | None,
        invalid_usage: bool,
    ) -> WeixinChubModeDispatchResult:
        if invalid_usage or requested_index is None:
            return self._remember_request_reply(
                message_id=message_id,
                correlation_id=correlation_id,
                route_fingerprint=route_fingerprint,
                source_ip=source_ip,
                message="Usage: del <R1-R9>",
                failed=True,
            )
        delete_operation_id = uuid4().hex
        for status in ("requested", "started"):
            write_operation(
                operation_id=delete_operation_id,
                action="request_backlog_delete",
                status=status,
                target=f"R{requested_index}",
                source_ip=source_ip,
            )
        try:
            item = self.request_backlog.delete(requested_index)
        except RequestBacklogNotFound:
            message = f"Delete: Request R{requested_index} was not found."
            failed = True
        except RequestBacklogBusy:
            message = f"Delete: Request R{requested_index} is running."
            failed = True
        except RequestBacklogError:
            message = "Delete: Request backlog is unavailable."
            failed = True
        else:
            message = (
                f"Delete: Request R{item.slot} deleted.\n\n"
                f"Request · R{item.slot} · {item.title}"
            )
            failed = False
        result = self._remember_request_reply(
            message_id=message_id,
            correlation_id=correlation_id,
            route_fingerprint=route_fingerprint,
            source_ip=source_ip,
            message=message,
            failed=failed,
        )
        write_operation(
            operation_id=delete_operation_id,
            action="request_backlog_delete",
            status=(
                "failed"
                if failed or self._state_error or result.message != message
                else "succeeded"
            ),
            target=f"R{requested_index}",
            source_ip=source_ip,
        )
        return result

    def _dispatch_codex_delete(
        self,
        *,
        message_id: str,
        correlation_id: str | None,
        route_fingerprint: str,
        source_ip: str,
        delivery_route: QuickInteractionWeixinRoute,
        requested_index: int | None,
        invalid_usage: bool,
    ) -> WeixinChubModeDispatchResult:
        operation_id = uuid4().hex
        now = utc_now()
        record = WeixinChubModeSubmission(
            message_id=message_id,
            correlation_id=correlation_id,
            operation_id=operation_id,
            delivery_route_fingerprint=route_fingerprint,
            status="reserved",
            code="submission_interrupted",
            message=(
                "Delete: The result could not be confirmed. Send chub to check status."
            ),
            created_at=now,
            updated_at=now,
        )
        next_state = self._state.model_copy(deep=True)
        next_state.submissions.append(record)
        self._log_dispatch(operation_id, "requested", source_ip)
        self._log_dispatch(operation_id, "started", source_ip)
        try:
            self._write_state(next_state)
        except OSError:
            self._state_error = True
            self._log_dispatch(operation_id, "failed", source_ip)
            return self._dispatch_failure("state_unavailable")
        self._state = next_state

        usage = "Usage: del <1-9|S1-S9|一-九>"
        if invalid_usage or requested_index is None:
            return self._finish_codex_delete(
                record,
                usage,
                source_ip=source_ip,
                failed=True,
            )

        try:
            configuration = self._state.configuration.model_copy(deep=True)
            visible, _remaining = self._read_visible_codex_sessions(
                configuration,
                fill_candidates=False,
            )
        except OSError:
            self._state_error = True
            self._log_dispatch(operation_id, "failed", source_ip)
            return self._dispatch_failure("state_unavailable")
        except Exception:
            LOGGER.warning("Codex delete target lookup failed", exc_info=True)
            return self._finish_codex_delete(
                record,
                "Delete: Not completed because the Session list is unavailable.",
                source_ip=source_ip,
                failed=True,
            )

        target_entry = next(
            (entry for entry in visible if entry[0] == requested_index),
            None,
        )
        if target_entry is None:
            message, _status_failed = self._codex_operation_message(
                "Delete: Not completed because the Session number is invalid.",
                fill_session_candidates=False,
            )
            return self._finish_codex_delete(
                record,
                message,
                source_ip=source_ip,
                failed=True,
            )

        target_slot, target, _listed_state = target_entry

        try:
            refreshed = self.codex_manager.get_session(target.id)
            if (
                refreshed.id != target.id
                or self._slot_for_session(refreshed.id) != target_slot
                or not self._session_matches_configuration(refreshed, configuration)
            ):
                raise ValueError("Session is no longer safe to delete")
        except Exception:
            LOGGER.info("Codex delete target is no longer available", exc_info=True)
            message, _status_failed = self._codex_operation_message(
                "Delete: Not completed because the target Session changed state.",
                fill_session_candidates=False,
            )
            return self._finish_codex_delete(
                record,
                message,
                source_ip=source_ip,
                failed=True,
            )

        pending = self._state.pending_retry
        pending_session_id = None
        if pending is not None and pending.expires_at > utc_now():
            pending_session_id = pending.session_id or pending.claimed_session_id
            if pending_session_id is None and self._state.session_id == refreshed.id:
                pending_session_id = refreshed.id
        if pending_session_id == refreshed.id:
            message, _status_failed = self._codex_operation_message(
                "Delete: Not completed because the Session has a pending retry task.",
                fill_session_candidates=False,
            )
            return self._finish_codex_delete(
                record,
                message,
                source_ip=source_ip,
                failed=True,
            )

        if self.session_deleter is None:
            return self._finish_codex_delete(
                record,
                "Delete: Not completed because Session deletion is unavailable.",
                source_ip=source_ip,
                failed=True,
            )

        self._log_delete(operation_id, "requested", refreshed.id, source_ip)
        self._log_delete(operation_id, "started", refreshed.id, source_ip)
        try:
            self.session_deleter(refreshed.id)
        except Exception as exc:
            LOGGER.warning("Codex delete command failed", exc_info=True)
            self._log_delete(operation_id, "failed", refreshed.id, source_ip)
            if isinstance(exc, ApiError) and exc.code == "codex_session_writer_active":
                message = (
                    "Delete: Not completed. This is open in another app, "
                    "close it there to continue here."
                )
            elif isinstance(exc, ApiError) and exc.code in {
                "codex_session_in_progress",
                "quick_interaction_in_progress",
                "quick_interaction_terminal_working",
            }:
                message = (
                    "Delete: Not completed because the target Session is still "
                    "running or its state is unknown."
                )
            else:
                message = (
                    "Delete: Failed. The Session may have stopped but remains "
                    "listed. Send chub before trying again."
                )
            return self._finish_codex_delete(
                record,
                message,
                source_ip=source_ip,
                failed=True,
            )

        self._log_delete(operation_id, "succeeded", refreshed.id, source_ip)

        title = build_session_title(
            refreshed.title or "Unnamed Session",
            self.settings.openclaw.weixin_chub_mode.session_name_max_width,
        )
        was_current = self._state.session_id == refreshed.id
        record.status = "routed"
        record.code = "codex_session_deleted"
        record.message = "Delete: Completed, but status refresh was interrupted. Send chub."
        record.http_status = 200
        record.session_id = refreshed.id
        record.session_slot = target_slot
        record.session_title = title
        record.dispatch_disposition = "reply"
        record.updated_at = utc_now()
        deleted_state = self._state.model_copy(deep=True)
        deleted_state.session_slots = [
            entry
            for entry in deleted_state.session_slots
            if entry.session_id != refreshed.id
        ]
        if deleted_state.session_id == refreshed.id:
            deleted_state.session_id = None
        deleted_state.submissions = [
            record.model_copy(deep=True)
            if item.message_id == record.message_id
            else item
            for item in deleted_state.submissions
        ]
        try:
            self._write_state(deleted_state)
        except OSError:
            self._state_error = True
            self._log_dispatch(operation_id, "failed", source_ip)
            return WeixinChubModeDispatchResult(
                disposition="reply",
                message=(
                    "Delete: Completed, but Chub could not synchronize the "
                    "Session list. Send chub later."
                ),
            )
        self._state = deleted_state
        current_suffix = " The current selection was cleared." if was_current else ""
        message, _status_failed = self._codex_operation_message(
            f"Delete: Session {target_slot} deleted.{current_suffix}",
            fill_session_candidates=False,
        )
        record.message = message
        deleted_state = self._state.model_copy(deep=True)
        deleted_state.submissions = [
            record.model_copy(deep=True)
            if item.message_id == record.message_id
            else item
            for item in deleted_state.submissions
        ]
        try:
            self._write_state(deleted_state)
        except OSError:
            self._state_error = True
            self._log_dispatch(operation_id, "failed", source_ip)
            return WeixinChubModeDispatchResult(
                disposition="reply",
                message=(
                    "Delete: Completed, but Chub could not synchronize the "
                    "Session list. Send chub later."
                ),
            )
        self._state = deleted_state
        self._log_dispatch(operation_id, "succeeded", source_ip)
        return WeixinChubModeDispatchResult(
            disposition="reply",
            message=message,
        )

    def _finish_codex_delete(
        self,
        record: WeixinChubModeSubmission,
        message: str,
        *,
        source_ip: str,
        failed: bool = False,
    ) -> WeixinChubModeDispatchResult:
        message = self._with_command_status_suffix(message)
        record.status = "routed"
        record.code = "codex_session_deleted"
        record.message = message
        record.http_status = 200
        record.dispatch_disposition = "reply"
        record.updated_at = utc_now()
        try:
            self._replace_submission(record)
        except OSError:
            self._log_dispatch(record.operation_id, "failed", source_ip)
            return self._dispatch_failure("state_unavailable")
        self._log_dispatch(
            record.operation_id,
            "failed" if failed else "succeeded",
            source_ip,
        )
        self._schedule_session_snapshot_refresh()
        return WeixinChubModeDispatchResult(disposition="reply", message=message)

    def system_upgrade_readiness(self) -> str | None:
        with self._lock:
            if self._state_error:
                return "微信 Chub 模式状态不可用。"
            if any(
                item.status in {"pending", "started"}
                or item.notification_status in {"pending", "sending"}
                for item in self._state.restart_operations
            ):
                return "仍有微信重启操作或通知尚未结束。"
            if any(
                item.status in {"pending", "started"}
                or item.notification_status in {"pending", "sending"}
                for item in self._state.stop_operations
            ):
                return "仍有微信 Session 停止操作或通知尚未结束。"
        return None

    def reset_for_system_upgrade(self, operation_id: str, *, force: bool = False) -> None:
        readiness = self.system_upgrade_readiness()
        if readiness is not None and not force:
            raise OSError(readiness)
        with self._slot_lock, self._lock:
            next_state = WeixinChubModeState(
                configuration=self._state.configuration.model_copy(deep=True),
            )
            self._write_state(next_state)
            self._state = next_state
            self._system_upgrade_reset = True

    def _dispatch_codex_archive(
        self,
        *,
        message_id: str,
        correlation_id: str | None,
        route_fingerprint: str,
        source_ip: str,
        delivery_route: QuickInteractionWeixinRoute,
        requested_index: int | None,
        invalid_usage: bool,
    ) -> WeixinChubModeDispatchResult:
        operation_id = uuid4().hex
        now = utc_now()
        record = WeixinChubModeSubmission(
            message_id=message_id,
            correlation_id=correlation_id,
            operation_id=operation_id,
            delivery_route_fingerprint=route_fingerprint,
            status="reserved",
            code="submission_interrupted",
            message=(
                "Archive: The result could not be confirmed. Send chub to check status."
            ),
            created_at=now,
            updated_at=now,
        )
        next_state = self._state.model_copy(deep=True)
        next_state.submissions.append(record)
        self._log_dispatch(operation_id, "requested", source_ip)
        self._log_dispatch(operation_id, "started", source_ip)
        try:
            self._write_state(next_state)
        except OSError:
            self._state_error = True
            self._log_dispatch(operation_id, "failed", source_ip)
            return self._dispatch_failure("state_unavailable")
        self._state = next_state

        usage = "Usage: archive <1-9|S1-S9|一-九>"
        if invalid_usage or requested_index is None:
            return self._finish_codex_archive(
                record,
                usage,
                source_ip=source_ip,
                failed=True,
            )

        try:
            configuration = self._state.configuration.model_copy(deep=True)
            visible, remaining = self._read_visible_codex_sessions(
                configuration,
                fill_candidates=False,
            )
        except OSError:
            self._state_error = True
            self._log_dispatch(operation_id, "failed", source_ip)
            return self._dispatch_failure("state_unavailable")
        except Exception:
            LOGGER.warning("Codex archive target lookup failed", exc_info=True)
            return self._finish_codex_archive(
                record,
                "Archive: Not completed because the Session list is unavailable.",
                source_ip=source_ip,
                failed=True,
            )

        target_entry = next(
            (entry for entry in visible if entry[0] == requested_index),
            None,
        )
        if target_entry is None:
            message, _status_failed = self._codex_operation_message(
                "Archive: Not completed because the Session number is invalid.",
                fill_session_candidates=False,
            )
            return self._finish_codex_archive(
                record,
                message,
                source_ip=source_ip,
                failed=True,
            )

        target_slot, target, _listed_state = target_entry

        try:
            refreshed = self.codex_manager.get_session(target.id)
            if (
                refreshed.id != target.id
                or self._slot_for_session(refreshed.id) != target_slot
                or not self._session_matches_configuration(refreshed, configuration)
            ):
                raise ValueError("Session is no longer safe to archive")
        except Exception:
            LOGGER.info("Codex archive target is no longer available", exc_info=True)
            message, _status_failed = self._codex_operation_message(
                "Archive: Not completed because the target Session changed state.",
                fill_session_candidates=False,
            )
            return self._finish_codex_archive(
                record,
                message,
                source_ip=source_ip,
                failed=True,
            )

        pending = self._state.pending_retry
        pending_session_id = None
        if pending is not None and pending.expires_at > utc_now():
            pending_session_id = pending.session_id or pending.claimed_session_id
            if pending_session_id is None and self._state.session_id == refreshed.id:
                pending_session_id = refreshed.id
        if pending_session_id == refreshed.id:
            message, _status_failed = self._codex_operation_message(
                "Archive: Not completed because the Session has a pending retry task.",
                fill_session_candidates=False,
            )
            return self._finish_codex_archive(
                record,
                message,
                source_ip=source_ip,
                failed=True,
            )

        if self.session_archiver is None:
            return self._finish_codex_archive(
                record,
                "Archive: Not completed because Session archiving is unavailable.",
                source_ip=source_ip,
                failed=True,
            )

        self._log_archive(operation_id, "requested", refreshed.id, source_ip)
        self._log_archive(operation_id, "started", refreshed.id, source_ip)
        try:
            self.session_archiver(refreshed.id)
        except Exception as exc:
            LOGGER.warning("Codex archive command failed", exc_info=True)
            self._log_archive(operation_id, "failed", refreshed.id, source_ip)
            if isinstance(exc, ApiError) and exc.code in {
                "codex_session_writer_active",
                "quick_interaction_writer_active",
            }:
                message = (
                    "Archive: Not completed. This is open in another app, "
                    "close it there to continue here."
                )
            elif isinstance(exc, ApiError) and exc.code in {
                "codex_session_in_progress",
                "quick_interaction_in_progress",
                "quick_interaction_terminal_working",
            }:
                message = (
                    "Archive: Not completed because the target Session is still "
                    "running or its state is unknown."
                )
            else:
                message = (
                    "Archive: Failed. The Session may have stopped but remains "
                    "listed. Send chub before trying again."
                )
            return self._finish_codex_archive(
                record,
                message,
                source_ip=source_ip,
                failed=True,
            )

        self._log_archive(operation_id, "succeeded", refreshed.id, source_ip)

        title = build_session_title(
            refreshed.title or "Unnamed Session",
            self.settings.openclaw.weixin_chub_mode.session_name_max_width,
        )
        was_current = self._state.session_id == refreshed.id
        record.status = "routed"
        record.code = "codex_session_archived"
        record.message = (
            "Archive: Completed, but status refresh was interrupted. Send chub."
        )
        record.http_status = 200
        record.session_id = refreshed.id
        record.session_slot = target_slot
        record.session_title = title
        record.dispatch_disposition = "reply"
        record.updated_at = utc_now()
        archived_state = self._state.model_copy(deep=True)
        archived_state.session_slots = [
            entry
            for entry in archived_state.session_slots
            if entry.session_id != refreshed.id
        ]
        if archived_state.session_id == refreshed.id:
            archived_state.session_id = None
        archived_state.submissions = [
            record.model_copy(deep=True)
            if item.message_id == record.message_id
            else item
            for item in archived_state.submissions
        ]
        try:
            self._write_state(archived_state)
        except OSError:
            self._state_error = True
            self._log_dispatch(operation_id, "failed", source_ip)
            return WeixinChubModeDispatchResult(
                disposition="reply",
                message=(
                    "Archive: Completed, but Chub could not synchronize the "
                    "Session list. Send chub later."
                ),
            )
        self._state = archived_state
        current_suffix = " The current selection was cleared." if was_current else ""
        message, _status_failed = self._codex_operation_message(
            f"Archive: Session {target_slot} archived.{current_suffix}",
            fill_session_candidates=False,
        )
        record.message = message
        archived_state = self._state.model_copy(deep=True)
        archived_state.submissions = [
            record.model_copy(deep=True)
            if item.message_id == record.message_id
            else item
            for item in archived_state.submissions
        ]
        try:
            self._write_state(archived_state)
        except OSError:
            self._state_error = True
            self._log_dispatch(operation_id, "failed", source_ip)
            return WeixinChubModeDispatchResult(
                disposition="reply",
                message=(
                    "Archive: Completed, but Chub could not synchronize the "
                    "Session list. Send chub later."
                ),
            )
        self._state = archived_state
        self._log_dispatch(operation_id, "succeeded", source_ip)
        return WeixinChubModeDispatchResult(
            disposition="reply",
            message=message,
        )

    def _finish_codex_archive(
        self,
        record: WeixinChubModeSubmission,
        message: str,
        *,
        source_ip: str,
        failed: bool = False,
    ) -> WeixinChubModeDispatchResult:
        message = self._with_command_status_suffix(message)
        record.status = "routed"
        record.code = "codex_session_archived"
        record.message = message
        record.http_status = 200
        record.dispatch_disposition = "reply"
        record.updated_at = utc_now()
        try:
            self._replace_submission(record)
        except OSError:
            self._log_dispatch(record.operation_id, "failed", source_ip)
            return self._dispatch_failure("state_unavailable")
        self._log_dispatch(
            record.operation_id,
            "failed" if failed else "succeeded",
            source_ip,
        )
        self._schedule_session_snapshot_refresh()
        return WeixinChubModeDispatchResult(disposition="reply", message=message)

    def _dispatch_codex_switch(
        self,
        *,
        message_id: str,
        correlation_id: str | None,
        route_fingerprint: str,
        source_ip: str,
        requested_index: int | None,
        invalid_usage: bool,
        delivery_route: QuickInteractionWeixinRoute,
        task_prompt: str | None = None,
        preprocess_task: bool = False,
        confirmation_task: bool = False,
    ) -> WeixinChubModeDispatchResult:
        operation_id = uuid4().hex
        now = utc_now()
        record = WeixinChubModeSubmission(
            message_id=message_id,
            correlation_id=correlation_id,
            operation_id=operation_id,
            delivery_route_fingerprint=route_fingerprint,
            status="reserved",
            code="submission_interrupted",
            message=(
                "Session: The result could not be confirmed. Send a new message "
                "to try again."
            ),
            created_at=now,
            updated_at=now,
        )
        next_state = self._state.model_copy(deep=True)
        next_state.submissions.append(record)
        self._log_dispatch(operation_id, "requested", source_ip)
        self._log_dispatch(operation_id, "started", source_ip)
        try:
            self._write_state(next_state)
        except OSError:
            self._state_error = True
            self._log_dispatch(operation_id, "failed", source_ip)
            return self._dispatch_failure("state_unavailable")
        self._state = next_state

        usage = "Usage: S#"
        if invalid_usage or requested_index is None:
            return self._finish_codex_switch(
                record,
                usage,
                source_ip=source_ip,
                failed=True,
                failed_task_prompt=task_prompt,
            )

        deadline = time.monotonic() + CODEX_STATUS_TIMEOUT_SECONDS
        account_results: queue.Queue[tuple[str, bool]] = queue.Queue(maxsize=1)

        def read_account() -> None:
            try:
                account_results.put(
                    (
                        self._usage_message(self._read_ai_usage(force=False)),
                        False,
                    )
                )
            except Exception:
                LOGGER.warning("Codex usage check failed", exc_info=True)
                account_results.put(("Weekly Unavailable", True))

        threading.Thread(target=read_account, daemon=True).start()

        try:
            configuration = self._state.configuration.model_copy(deep=True)
            visible, remaining = self._read_visible_codex_sessions(
                configuration,
                timeout_seconds=max(0.0, deadline - time.monotonic()),
                fill_candidates=False,
            )
        except OSError:
            self._state_error = True
            return self._dispatch_failure("state_unavailable")
        except Exception:
            LOGGER.warning("Codex session switch lookup failed", exc_info=True)
            return self._finish_codex_switch(
                record,
                "Session: Not completed because the Session list is unavailable.",
                source_ip=source_ip,
                failed=True,
                failed_task_prompt=task_prompt,
            )

        if not visible:
            candidate_hint = self._switch_candidate_hint(remaining)
            hint = f" {candidate_hint}" if candidate_hint else ""
            status = f"Session: Not completed because no Sessions are available.{hint}"
            message = status
            if task_prompt is None:
                message, _status_failed = self._codex_operation_message(
                    status,
                    fill_session_candidates=False,
                )
            return self._finish_codex_switch(
                record,
                message,
                source_ip=source_ip,
                failed=True,
                failed_task_prompt=task_prompt,
            )

        slot_indexes = [slot for slot, _session, _state in visible]
        if requested_index not in slot_indexes:
            candidate_hint = self._switch_candidate_hint(remaining)
            hint = f" {candidate_hint}" if candidate_hint else ""
            status = f"Session: Not completed because the Session number is invalid.{hint}"
            message = status
            if task_prompt is None:
                message, _status_failed = self._codex_operation_message(
                    status,
                    fill_session_candidates=False,
                )
            return self._finish_codex_switch(
                record,
                message,
                source_ip=source_ip,
                failed=True,
                failed_task_prompt=task_prompt,
            )
        target_index = slot_indexes.index(requested_index)

        target_slot, target, listed_state = visible[target_index]
        target_title = build_session_title(
            getattr(target, "title", None) or "Unnamed Session",
            self.settings.openclaw.weixin_chub_mode.session_name_max_width,
        )
        if listed_state == "Unavailable":
            status = "Session: Not completed because the target Session is unavailable."
            message = status
            if task_prompt is None:
                message, _status_failed = self._codex_operation_message(
                    status,
                    fill_session_candidates=False,
                )
            return self._finish_codex_switch(
                record,
                message,
                source_ip=source_ip,
                failed=True,
                failed_task_prompt=task_prompt,
                task_session_id=target.id,
                task_session_slot=target_slot,
                task_session_title=target_title,
            )
        if task_prompt is not None and listed_state != "Available":
            message = (
                "Session: Not completed because the target Session is running. "
                "The task was not submitted."
            )
            return self._finish_codex_switch(
                record,
                message,
                source_ip=source_ip,
                failed=True,
                failed_task_prompt=task_prompt,
                task_session_id=target.id,
                task_session_slot=target_slot,
                task_session_title=target_title,
            )
        if (
            target.id == self._state.session_id
            and task_prompt is None
        ):
            message, _status_failed = self._codex_operation_message(
                f"Session: Not completed because Session {target_slot} is already selected.",
                fill_session_candidates=False,
            )
            return self._finish_codex_switch(
                record,
                message,
                source_ip=source_ip,
                failed=True,
                failed_task_prompt=task_prompt,
            )

        try:
            refreshed = self.codex_manager.get_session(target.id)
            if (
                refreshed.id != target.id
                or not self._session_matches_configuration(refreshed, configuration)
            ):
                raise ValueError("Session configuration changed")
            refreshed_state = self._codex_session_dispatch_state(refreshed)
            if refreshed_state == "Unavailable" or (
                task_prompt is not None and refreshed_state != "Available"
            ):
                raise ValueError("Session became unavailable")
        except Exception:
            LOGGER.info("Codex switch target is no longer available", exc_info=True)
            status = "Session: Not completed because the target Session is unavailable."
            message = status
            if task_prompt is None:
                message, _status_failed = self._codex_operation_message(
                    status,
                    fill_session_candidates=False,
                )
            return self._finish_codex_switch(
                record,
                message,
                source_ip=source_ip,
                failed=True,
                failed_task_prompt=task_prompt,
                task_session_id=target.id,
                task_session_slot=target_slot,
                task_session_title=target_title,
            )

        visible[target_index] = (target_slot, refreshed, refreshed_state)
        try:
            usage_message, _usage_failed = account_results.get(
                timeout=max(0.0, deadline - time.monotonic())
            )
        except queue.Empty:
            LOGGER.warning("Codex usage check timed out during session switch")
            usage_message = "Weekly Unavailable"
        sessions_message = self._format_codex_sessions(
            visible,
            refreshed.id,
            remaining,
        )
        message = (
            f"Session: S{target_slot} selected.\n\n"
            f"{sessions_message}\n\n{usage_message}"
        )

        record.status = "reserved" if task_prompt is not None else "routed"
        record.code = (
            "submission_interrupted"
            if record.status == "reserved"
            else "codex_switch_checked"
        )
        record.message = message
        record.http_status = 200
        record.session_id = refreshed.id
        record.session_slot = target_slot
        record.session_title = target_title
        record.dispatch_disposition = "reply"
        if task_prompt is not None:
            record.continuation_kind = (
                "confirmed_translated_task" if confirmation_task else "translated_task" if preprocess_task else "task"
            )
            record.continuation_prompt = task_prompt
        record.updated_at = utc_now()
        switched_state = self._state.model_copy(deep=True)
        switched_state.session_id = refreshed.id
        switched_state.submissions = [
            record.model_copy(deep=True)
            if item.message_id == record.message_id
            else item
            for item in switched_state.submissions
        ]
        try:
            self._write_state(switched_state)
        except OSError:
            self._state_error = True
            self._log_dispatch(operation_id, "failed", source_ip)
            return self._dispatch_failure("state_unavailable")
        self._state = switched_state
        if record.continuation_kind is not None:
            return self._resume_switch_continuation(
                record,
                source_ip=source_ip,
                delivery_route=delivery_route,
            )
        self._log_dispatch(operation_id, "succeeded", source_ip)
        return WeixinChubModeDispatchResult(disposition="reply", message=message)

    def _resume_switch_continuation(
        self,
        record: WeixinChubModeSubmission,
        *,
        source_ip: str,
        delivery_route: QuickInteractionWeixinRoute,
    ) -> WeixinChubModeDispatchResult:
        if record.session_id is None:
            return self._finish_codex_switch(
                record,
                "Session: The saved target Session is unavailable. The follow-up was not submitted.",
                source_ip=source_ip,
                failed=True,
            )
        target_slot = record.session_slot or self._slot_for_session(record.session_id)
        target_title = record.session_title or "Unnamed Session"
        prompt = record.continuation_prompt
        if prompt is None:
            return self._finish_codex_switch(
                record,
                "Session: The saved follow-up task is unavailable and was not submitted.",
                source_ip=source_ip,
                failed=True,
                task_session_id=record.session_id,
                task_session_slot=target_slot,
                task_session_title=target_title,
                task_session_current=True,
            )
        derived_message_id = self._command_task_message_id(record.message_id)

        preprocess_task = record.continuation_kind in {"translated_task", "confirmed_translated_task"}
        confirmation_task = record.continuation_kind == "confirmed_translated_task"

        try:
            submission = self.submit(
                message_id=derived_message_id,
                prompt=prompt,
                correlation_id=record.correlation_id,
                source_ip=source_ip,
                delivery_route=delivery_route,
                target_session_id=record.session_id,
                preprocess=preprocess_task,
                confirmation_required=confirmation_task,
                retain_busy_retry=True,
            )
        except ApiError as exc:
            failure = self._dispatch_failure_from_error(exc)
            return self._finish_codex_switch(
                record,
                f"Session: S{target_slot} selected, but the task was not submitted.\n\n"
                f"{failure.message or 'Not submitted · Submission failed. Try again later.'}",
                source_ip=source_ip,
                failed=True,
                failed_task_prompt=prompt,
                task_session_id=record.session_id,
                task_session_slot=target_slot,
                task_session_title=target_title,
                task_session_current=True,
            )

        status = (
            f"Session: S{target_slot} selected. "
            "Optimizing · Preparing to submit."
            if preprocess_task
            else f"Session: S{target_slot} selected. Task submitted."
        )
        return self._finish_codex_switch(
            record,
            self._replace_submission_status(submission.message, status),
            source_ip=source_ip,
            task_session_id=record.session_id,
            task_session_slot=submission.session_slot,
            task_session_title=submission.session_title,
            task_session_current=True,
        )

    _switch_candidate_hint = staticmethod(switch_candidate_hint)

    def _read_visible_codex_sessions(
        self,
        configuration: WeixinChubModeRuntimeConfig,
        *,
        timeout_seconds: float = CODEX_STATUS_TIMEOUT_SECONDS,
        fill_candidates: bool = False,
    ) -> tuple[list[tuple[int, object, str]], int]:
        results: queue.Queue[
            tuple[tuple[list[tuple[int, object, str]], int] | None, Exception | None]
        ] = queue.Queue(maxsize=1)

        def read_sessions() -> None:
            try:
                results.put((self._visible_codex_sessions(
                    configuration,
                    fill_candidates=fill_candidates,
                ), None))
            except Exception as exc:
                results.put((None, exc))

        threading.Thread(target=read_sessions, daemon=True).start()
        try:
            value, error = results.get(timeout=timeout_seconds)
        except queue.Empty as exc:
            raise RuntimeError("Codex session lookup timed out") from exc
        if error is not None:
            if isinstance(error, OSError):
                raise error
            raise RuntimeError("Codex session lookup failed") from error
        if value is None:
            raise RuntimeError("Codex session lookup returned no result")
        return value

    def _finish_codex_switch(
        self,
        record: WeixinChubModeSubmission,
        message: str,
        *,
        source_ip: str,
        failed: bool = False,
        failed_task_prompt: str | None = None,
        task_session_id: str | None = None,
        task_session_slot: int | None = None,
        task_session_title: str | None = None,
        task_session_current: bool = False,
    ) -> WeixinChubModeDispatchResult:
        if failed and failed_task_prompt is not None:
            message = with_task_summary(
                message,
                failed_task_prompt,
                self.settings.openclaw.weixin_chub_mode.task_name_max_width,
                session_slot=task_session_slot,
                session_title=task_session_title,
                current=task_session_current,
            )
        if not self._has_inline_task_context(message):
            message = self._with_command_status_suffix(message)
        record.status = "routed"
        record.code = "codex_switch_checked"
        record.message = message
        record.http_status = 200
        record.continuation_kind = None
        record.continuation_prompt = None
        if task_session_id is not None:
            record.session_id = task_session_id
            record.session_slot = task_session_slot
            record.session_title = task_session_title
        record.dispatch_disposition = "reply"
        record.updated_at = utc_now()
        try:
            self._replace_submission(record)
        except OSError:
            self._log_dispatch(record.operation_id, "failed", source_ip)
            return self._dispatch_failure("state_unavailable")
        self._log_dispatch(
            record.operation_id,
            "failed" if failed else "succeeded",
            source_ip,
        )
        self._schedule_session_snapshot_refresh()
        return WeixinChubModeDispatchResult(disposition="reply", message=message)

    def _format_codex_sessions(
        self,
        visible: list[tuple[int, object, str]],
        current_session_id: str | None,
        remaining: int,
    ) -> str:
        return format_codex_sessions(
            visible,
            current_session_id,
            remaining,
            self.settings.openclaw.weixin_chub_mode.session_name_max_width,
        )
    _format_session_blocks = staticmethod(format_session_blocks)

    def _visible_codex_sessions(
        self,
        configuration: WeixinChubModeRuntimeConfig,
        *,
        fill_candidates: bool,
    ) -> tuple[list[tuple[int, object, str]], int]:
        sessions = self.codex_manager.list_sessions()
        self._sync_session_slots(
            configuration,
            sessions=sessions,
            fill_candidates=fill_candidates,
        )
        eligible = {
            session.id: session
            for session in sessions
            if self._session_matches_configuration(session, configuration)
        }
        slots_by_session_id = {
            entry.session_id: entry.slot for entry in self._state.session_slots
        }
        visible = [
            (
                slots_by_session_id[session.id],
                session,
                self._codex_session_dispatch_state(session),
            )
            for session in sessions_newest_first(eligible.values())
            if session.id in slots_by_session_id
        ]
        assigned = {entry.session_id for entry in self._state.session_slots}
        remaining = sum(
            session_id not in assigned
            and self._codex_session_dispatch_state(session) != "Unavailable"
            for session_id, session in eligible.items()
        )
        return visible, remaining

    def _sync_session_slots(
        self,
        configuration: WeixinChubModeRuntimeConfig,
        *,
        sessions: list[object] | None = None,
        fill_candidates: bool,
    ) -> None:
        if sessions is None:
            sessions = self.codex_manager.list_sessions()
        eligible = {
            session.id: session
            for session in sessions
            if self._session_matches_configuration(session, configuration)
        }
        retained: list[WeixinChubModeSessionSlot] = []
        used_slots: set[int] = set()
        used_sessions: set[str] = set()
        for entry in sorted(self._state.session_slots, key=lambda item: item.slot):
            if (
                entry.session_id not in eligible
                or entry.slot in used_slots
                or entry.session_id in used_sessions
            ):
                continue
            retained.append(entry.model_copy(deep=True))
            used_slots.add(entry.slot)
            used_sessions.add(entry.session_id)

        candidates: list[object] = []
        current = eligible.get(self._state.session_id or "")
        if (
            current is not None
            and current.id not in used_sessions
            and self._codex_session_dispatch_state(current) != "Unavailable"
        ):
            candidates.append(current)
        if fill_candidates:
            weixin_session_ids = self.quick_interactions.weixin_session_ids()
            candidates.extend(
                sorted(
                    (
                        session
                        for session in eligible.values()
                        if session.id not in used_sessions
                        and session.id != getattr(current, "id", None)
                        and self._codex_session_dispatch_state(session) != "Unavailable"
                    ),
                    key=lambda session: (
                        session.id not in weixin_session_ids,
                        session.id,
                    ),
                )
            )
        free_slots = [
            slot
            for slot in range(1, MAX_WEIXIN_SESSION_SLOTS + 1)
            if slot not in used_slots
        ]
        for slot, session in zip(free_slots, candidates, strict=False):
            retained.append(
                WeixinChubModeSessionSlot(slot=slot, session_id=session.id)
            )
            used_sessions.add(session.id)

        retained.sort(key=lambda item: item.slot)
        if retained == self._state.session_slots:
            return
        next_state = self._state.model_copy(deep=True)
        next_state.session_slots = retained
        self._write_state(next_state)
        self._state = next_state

    def _slot_for_session(self, session_id: str) -> int | None:
        return next(
            (
                entry.slot
                for entry in self._state.session_slots
                if entry.session_id == session_id
            ),
            None,
        )

    def session_slot_matches(self, slot: int, session_id: str) -> bool:
        with self._lock:
            return any(
                entry.slot == slot and entry.session_id == session_id
                for entry in self._state.session_slots
            )

    def session_slot_is_current(self, slot: int, session_id: str) -> bool:
        with self._lock:
            return self._state.session_id == session_id and any(
                entry.slot == slot and entry.session_id == session_id
                for entry in self._state.session_slots
            )

    def session_slot(self, session_id: str) -> int | None:
        """Return the current stable Weixin slot without changing slot state."""
        with self._lock:
            return self._slot_for_session(session_id)

    def session_context(self, session_id: str) -> tuple[int | None, str | None]:
        """Resolve current display data from the authoritative Session ID."""
        with self._lock:
            return self._session_context(session_id)

    def session_slots_snapshot(self) -> dict[str, int]:
        """Return one consistent copy of the current Weixin slot mapping."""
        with self._lock:
            return {
                entry.session_id: entry.slot
                for entry in self._state.session_slots
            }

    def codex_status_message(
        self,
        route: QuickInteractionWeixinRoute | None = None,
    ) -> str:
        with self._status_condition:
            account_cached = self._status_cache.get("account")
            sessions_cached = self._status_cache.get("sessions")
        with self._lock:
            current_session_id = self._state.session_id
        running_tasks: dict[str, str] = {}
        if route is not None:
            try:
                task_snapshot = self.quick_interactions.weixin_task_status_snapshot(
                    route
                )
                running_tasks = {
                    session_id: build_task_name(
                        summary,
                        self.settings.openclaw.weixin_chub_mode.task_name_max_width,
                    )
                    for session_id, summary in task_snapshot.running_tasks
                }
            except Exception:
                LOGGER.warning(
                    "Unable to snapshot Weixin tasks for restart status",
                    exc_info=True,
                )
        if account_cached is None:
            try:
                usage_message = self._usage_message(
                    self._read_ai_usage(force=False)
                )
            except Exception:
                LOGGER.warning("AI usage snapshot is unavailable", exc_info=True)
                usage_message = "Weekly Unavailable"
        else:
            usage_message = self._usage_message(account_cached[0])
        if sessions_cached is None:
            sessions_message = "Sessions\n\nUnavailable"
        else:
            sessions = sessions_cached[0]
            task_summaries = {
                item.slot: running_tasks[item.session_id]
                for item in sessions
                if item.session_id in running_tasks
            }
            sessions_message = self._format_session_blocks(
                (
                    (
                        item.slot,
                        item.title,
                        "Busy"
                        if self.quick_interactions.is_running(item.session_id)
                        else item.state,
                        item.session_id == current_session_id,
                    )
                    for item in sessions
                ),
                task_summaries,
            )
        return f"{sessions_message}\n\n{usage_message}"

    def release_session_slot(self, session_id: str) -> bool:
        with self._slot_lock:
            with self._lock:
                if self._slot_for_session(session_id) is None:
                    return False
                next_state = self._state.model_copy(deep=True)
                next_state.session_slots = [
                    entry
                    for entry in next_state.session_slots
                    if entry.session_id != session_id
                ]
                if next_state.session_id == session_id:
                    next_state.session_id = None
                self._write_state(next_state)
                self._state = next_state
                self._schedule_session_snapshot_refresh()
                return True

    _session_matches_configuration = staticmethod(session_matches_configuration)

    def _codex_session_dispatch_state(self, session: object) -> str:
        if getattr(session, "status", None) == "error":
            return "Unavailable"
        session_id = getattr(session, "id", "")
        if self.quick_interactions.is_running(session_id):
            return "Busy"
        activity = getattr(session, "activity", "unknown")
        if activity == "working":
            return "Busy"
        if getattr(session, "status", None) == "running" and activity != "idle":
            return "Unavailable"
        native_session_id = getattr(session, "native_session_id", None)
        if native_session_id and self.codex_manager.has_active_writer(native_session_id):
            return "Busy"
        return "Available"

    _codex_usage_message = staticmethod(codex_usage_message)

    def _read_ai_usage(self, *, force: bool) -> object:
        if self.ai_usage_reader is not None:
            return self.ai_usage_reader.read(force=force)
        if self.codex_account_reader is not None:
            return self.codex_account_reader.read_account_status(force=True)
        raise RuntimeError("AI usage reader is unavailable")

    _usage_message = staticmethod(usage_message)
    _compact_token_count = staticmethod(compact_token_count)

    def _remember_passed_dispatch(
        self,
        *,
        message_id: str,
        correlation_id: str | None,
        route_fingerprint: str,
        source_ip: str,
    ) -> None:
        operation_id = uuid4().hex
        now = utc_now()
        passed = WeixinChubModeSubmission(
            message_id=message_id,
            correlation_id=correlation_id,
            operation_id=operation_id,
            delivery_route_fingerprint=route_fingerprint,
            status="passed",
            code="mode_disabled",
            message="微信 Chub 模式未启用，已放行原 OpenClaw 流程。",
            http_status=200,
            created_at=now,
            updated_at=now,
        )
        next_state = self._state.model_copy(deep=True)
        next_state.submissions.append(passed)
        self._log_dispatch(operation_id, "requested", source_ip)
        self._log_dispatch(operation_id, "started", source_ip)
        try:
            self._write_state(next_state)
        except OSError:
            self._log_dispatch(operation_id, "failed", source_ip)
            raise
        self._state = next_state
        self._log_dispatch(operation_id, "succeeded", source_ip)

    _dispatch_failure_from_error = staticmethod(dispatch_failure_from_error)
    _dispatch_failure = staticmethod(dispatch_failure)

    def stop(self) -> bool:
        with self._lock:
            session_id = self._state.session_id
        if not session_id:
            return False
        return self.quick_interactions.cancel_codex_session(session_id)

    def disable(self) -> bool:
        with self._slot_lock, self._lock:
            if not self._state.configuration.enabled:
                return False
            next_state = self._state.model_copy(deep=True)
            next_state.configuration.enabled = False
            self._write_state(next_state)
            self._state = next_state
            self._state_error = False
            return True

    def active_task(self) -> QuickInteractionTask | None:
        with self._lock:
            session_id = self._state.session_id
        if not session_id:
            return None
        tasks = self.quick_interactions.list_for_session(session_id, order="timeline")
        return tasks[0] if tasks else None

    def _ensure_session(
        self,
        configuration: WeixinChubModeRuntimeConfig,
    ) -> tuple[str, bool]:
        session_id = self._state.session_id
        if session_id:
            try:
                session = self.codex_manager.get_session(session_id)
            except ApiError as exc:
                if exc.code != "codex_session_not_found":
                    raise
            else:
                if (
                    session.workspace_id == configuration.workspace_id
                    and session.permission_mode == configuration.permission_mode
                    and (
                        configuration.model is None
                        or session.model == configuration.model
                    )
                    and (
                        configuration.reasoning_effort is None
                        or session.reasoning_effort
                        == configuration.reasoning_effort
                    )
                ):
                    return session.id, False
        return self._create_session(configuration), True

    def _create_session(
        self,
        configuration: WeixinChubModeRuntimeConfig,
    ) -> str:
        self._sync_session_slots(configuration, fill_candidates=False)
        free_slot = next(
            (
                slot
                for slot in range(1, MAX_WEIXIN_SESSION_SLOTS + 1)
                if all(entry.slot != slot for entry in self._state.session_slots)
            ),
            None,
        )
        if free_slot is None:
            raise ApiError(
                409,
                "weixin_chub_mode_session_slots_full",
                "9 个微信 Session 槽位已满，请先归档或删除一个 Session。",
            )
        with self.quick_interactions.session_creation_guard():
            created = self.codex_manager.create_session(
                configuration.workspace_id,
                configuration.permission_mode,
                configuration.model,
                configuration.reasoning_effort,
                "quick",
            )
        next_state = self._state.model_copy(deep=True)
        next_state.session_id = created.id
        next_state.session_slots.append(
            WeixinChubModeSessionSlot(slot=free_slot, session_id=created.id)
        )
        next_state.session_slots.sort(key=lambda item: item.slot)
        try:
            self._write_state(next_state)
        except OSError:
            try:
                discarded = self.codex_manager.discard_unstarted_session(created.id)
            except Exception:
                discarded = False
                LOGGER.error(
                    "Unable to discard unbound Weixin Session after state failure",
                    exc_info=True,
                )
            if not discarded:
                LOGGER.error(
                    "Unbound Weixin Session requires manual reconciliation: %s",
                    created.id,
                )
            raise
        self._state = next_state
        return created.id

    def _reclaim_unknown_session(
        self,
        session_id: str,
        native_session_id: str | None,
        operation_id: str,
        source_ip: str,
    ) -> None:
        reclaim_operation_id = f"{operation_id}:session-reclaim"
        for status in ("requested", "started"):
            write_operation(
                operation_id=reclaim_operation_id,
                action="weixin_chub_mode_session_reclaim",
                status=status,
                target=session_id,
                source_ip=source_ip,
            )
        try:
            stopped = (
                self.terminal_reclaimer(session_id)
                if self.terminal_reclaimer is not None
                else self.codex_manager.stop_session(session_id)
            )
            if (
                getattr(stopped, "status", None) != "stopped"
                or getattr(stopped, "activity", None) != "idle"
                or not self.codex_manager.wait_for_writer_release(
                    native_session_id,
                    timeout=3.0,
                )
            ):
                raise ApiError(
                    503,
                    "weixin_chub_mode_session_reclaim_failed",
                    "微信通道当前绑定 Session 状态未知且未能安全停止，请稍后重试。",
                )
        except Exception as exc:
            write_operation(
                operation_id=reclaim_operation_id,
                action="weixin_chub_mode_session_reclaim",
                status="failed",
                target=session_id,
                source_ip=source_ip,
            )
            if isinstance(exc, ApiError) and exc.code == (
                "weixin_chub_mode_session_reclaim_failed"
            ):
                raise
            raise ApiError(
                503,
                "weixin_chub_mode_session_reclaim_failed",
                "微信通道当前绑定 Session 状态未知且未能安全停止，请稍后重试。",
            ) from None
        write_operation(
            operation_id=reclaim_operation_id,
            action="weixin_chub_mode_session_reclaim",
            status="succeeded",
            target=session_id,
            source_ip=source_ip,
        )

    def _find_submission(self, message_id: str) -> WeixinChubModeSubmission | None:
        return next(
            (
                item.model_copy(deep=True)
                for item in self._state.submissions
                if item.message_id == message_id
            ),
            None,
        )

    @staticmethod
    def _route_fingerprint(route: QuickInteractionWeixinRoute) -> str:
        value = f"{route.account_id}\0{route.recipient}".encode("utf-8")
        return hashlib.sha256(value).hexdigest()

    def _replay(
        self,
        submission: WeixinChubModeSubmission,
    ) -> WeixinChubModeSubmissionResult:
        if submission.status in {"submitted", "routed"}:
            replayed = submission.model_copy(
                update={"message": self._refresh_replayed_task_context(submission)}
            )
            return self._result(replayed, duplicate=True, task_summary=None)
        raise ApiError(
            submission.http_status or 503,
            f"weixin_chub_mode_{submission.code}",
            submission.message,
        )

    @staticmethod
    def _result(
        submission: WeixinChubModeSubmission,
        *,
        duplicate: bool,
        task_summary: str | None,
    ) -> WeixinChubModeSubmissionResult:
        return WeixinChubModeSubmissionResult(
            duplicate=duplicate,
            new_session=submission.new_session,
            code=(
                "translation_queued"
                if submission.code == "translation_queued"
                else "submitted"
            ),
            message=submission.message,
            task_summary=task_summary,
            session_slot=submission.session_slot,
            session_title=submission.session_title,
        )

    def _reject(
        self,
        submission: WeixinChubModeSubmission,
        code: WeixinChubModeSubmissionCode,
        message: str,
        *,
        session_id: str | None = None,
        http_status: Literal[409, 503] = 409,
    ) -> None:
        submission.status = "rejected"
        submission.code = code
        submission.message = message[:500]
        submission.http_status = http_status
        submission.session_id = session_id
        submission.updated_at = utc_now()
        self._replace_submission(submission)

    def _reject_busy_with_pending_retry(
        self,
        submission: WeixinChubModeSubmission,
        *,
        prompt: str,
        route_fingerprint: str,
        session_id: str,
    ) -> None:
        now = utc_now()
        submission.status = "rejected"
        submission.code = "in_progress"
        submission.message = "微信通道当前绑定 Session 正在执行任务，请等待完成。"
        submission.http_status = 409
        submission.session_id = session_id
        submission.updated_at = now
        next_state = self._state.model_copy(deep=True)
        next_state.pending_retry = WeixinChubModePendingRetry(
            original_message_id=submission.message_id,
            prompt=prompt,
            delivery_route_fingerprint=route_fingerprint,
            created_at=now,
            expires_at=now + timedelta(minutes=PENDING_RETRY_TTL_MINUTES),
            session_id=session_id,
        )
        next_state.submissions = [
            submission.model_copy(deep=True)
            if item.message_id == submission.message_id
            else item
            for item in next_state.submissions
        ]
        try:
            self._write_state(next_state)
        except OSError:
            self._state_error = True
            raise
        self._state = next_state

    def _replace_submission(self, submission: WeixinChubModeSubmission) -> None:
        next_state = self._state.model_copy(deep=True)
        next_state.submissions = [
            submission.model_copy(deep=True)
            if item.message_id == submission.message_id
            else item
            for item in next_state.submissions
        ]
        try:
            self._write_state(next_state)
        except OSError:
            self._state_error = True
            raise
        self._state = next_state

    _safe_submission_error = staticmethod(safe_submission_error)

    @staticmethod
    def _log(
        operation_id: str,
        status: str,
        target: str,
        source_ip: str,
    ) -> None:
        write_operation(
            operation_id=operation_id,
            action="weixin_chub_mode_dispatch",
            status=status,
            target=target,
            source_ip=source_ip,
        )

    def _log_dispatch(
        self,
        operation_id: str,
        status: str,
        source_ip: str,
    ) -> None:
        write_operation(
            operation_id=operation_id,
            action="weixin_chub_mode_dispatch",
            status=status,
            target=self.settings.node.id,
            source_ip=source_ip,
        )

    @staticmethod
    def _log_archive(
        operation_id: str,
        status: str,
        target: str,
        source_ip: str,
    ) -> None:
        write_operation(
            operation_id=operation_id,
            action="archive_codex_session",
            status=status,
            target=target,
            source_ip=source_ip,
        )

    @staticmethod
    def _log_delete(
        operation_id: str,
        status: str,
        target: str,
        source_ip: str,
    ) -> None:
        write_operation(
            operation_id=operation_id,
            action="delete_codex_session",
            status=status,
            target=target,
            source_ip=source_ip,
        )

    @staticmethod
    def _log_rename(
        operation_id: str,
        status: str,
        target: str,
        source_ip: str,
    ) -> None:
        write_operation(
            operation_id=operation_id,
            action="rename_codex_session",
            status=status,
            target=target,
            source_ip=source_ip,
        )

    @staticmethod
    def _log_stop(
        operation_id: str,
        status: str,
        target: str,
        source_ip: str,
    ) -> None:
        write_operation(
            operation_id=operation_id,
            action="stop_codex_session",
            status=status,
            target=target,
            source_ip=source_ip,
        )

    def _log_standalone_dispatch(
        self,
        outcome: Literal["succeeded", "failed"],
        source_ip: str,
    ) -> None:
        operation_id = uuid4().hex
        self._log_dispatch(operation_id, "requested", source_ip)
        self._log_dispatch(operation_id, "started", source_ip)
        self._log_dispatch(operation_id, outcome, source_ip)

    def _write_state(self, state: WeixinChubModeState) -> None:
        if self._system_upgrade_reset:
            return
        state.submissions = sorted(
            state.submissions,
            key=lambda item: (item.created_at, item.message_id),
        )[-MAX_STORED_SUBMISSIONS:]
        active_restart_operations = [
            item
            for item in state.restart_operations
            if item.status in {"pending", "started"}
            or item.notification_status in {"pending", "sending"}
        ]
        completed_restart_operations = sorted(
            (
                item
                for item in state.restart_operations
                if item.status not in {"pending", "started"}
                and item.notification_status not in {"pending", "sending"}
            ),
            key=lambda item: (item.created_at, item.operation_id),
        )[-MAX_STORED_RESTART_OPERATIONS:]
        state.restart_operations = (
            active_restart_operations + completed_restart_operations
        )
        active_stop_operations = [
            item
            for item in state.stop_operations
            if item.status in {"pending", "started"}
            or item.notification_status in {"pending", "sending"}
        ]
        completed_stop_operations = sorted(
            (
                item
                for item in state.stop_operations
                if item.status not in {"pending", "started"}
                and item.notification_status not in {"pending", "sending"}
            ),
            key=lambda item: (item.created_at, item.operation_id),
        )[-MAX_STORED_STOP_OPERATIONS:]
        state.stop_operations = active_stop_operations + completed_stop_operations
        self.path.parent.mkdir(parents=True, exist_ok=True)
        os.chmod(self.path.parent, 0o700)
        if self.path.is_symlink():
            raise OSError("Weixin Chub mode state must not be a symlink")
        temporary = self.path.with_name(f".{self.path.name}.{uuid4().hex}.tmp")
        try:
            while True:
                content = json.dumps(
                    state.model_dump(mode="json"),
                    ensure_ascii=False,
                    separators=(",", ":"),
                ).encode("utf-8")
                if len(content) <= MAX_STATE_BYTES:
                    break
                if not state.submissions:
                    raise OSError("Weixin Chub mode state exceeds its size limit")
                drop_count = max(1, len(state.submissions) // 10)
                state.submissions = state.submissions[drop_count:]
            temporary.write_bytes(content)
            os.chmod(temporary, 0o600)
            temporary.replace(self.path)
            os.chmod(self.path, 0o600)
            with self._status_condition:
                self._mode_enabled = state.configuration.enabled
                self._submission_index = {
                    item.message_id: item.model_copy(deep=True)
                    for item in state.submissions
                }
        finally:
            try:
                temporary.unlink()
            except FileNotFoundError:
                pass
