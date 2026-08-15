from __future__ import annotations

import hashlib
import json
import logging
import os
import queue
import re
import threading
import time
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Callable, Iterable, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

from app.ai_usage.models import AiUsageData
from app.ai_usage.service import AiUsageService
from app.codex.models import (
    CodexQuotaData,
    CodexTokenUsageData,
    PermissionMode,
    QuickInteractionTask,
    QuickInteractionWeixinRoute,
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
    DeferredRestartRequest,
)
from app.services.operation_log import write_operation


WeixinChubModeCode = Literal[
    "ready",
    "disabled",
    "configuration_invalid",
    "codex_unavailable",
]
WeixinChubModeSubmissionCode = Literal[
    "submitted",
    "in_progress",
    "mode_disabled",
    "configuration_invalid",
    "codex_unavailable",
    "delivery_route_invalid",
    "message_conflict",
    "submission_failed",
    "submission_interrupted",
    "task_status_checked",
    # Kept for state-file compatibility with the original route name.
    "codex_usage_checked",
    # Kept for state-file compatibility with the retired help route.
    "codex_help_checked",
    # Kept for state-file compatibility with the retired status route.
    "codex_status_checked",
    "codex_switch_checked",
    "codex_session_archived",
    "codex_session_created",
    "codex_retry_checked",
    "chub_slots_synced",
    "chub_restart_requested",
]
WeixinChubModeDispatchCode = Literal[
    "mode_disabled",
    "submitted",
    "duplicate",
    "in_progress",
    "configuration_invalid",
    "codex_unavailable",
    "delivery_route_invalid",
    "message_conflict",
    "submission_failed",
    "submission_interrupted",
    "state_unavailable",
]
MAX_STORED_SUBMISSIONS = 5_000
MAX_STATE_BYTES = 8 * 1024 * 1024
MAX_PENDING_RETRY_PROMPT_CHARS = 8_000
MAX_STORED_RESTART_OPERATIONS = 256
PENDING_RETRY_TTL_MINUTES = 10
MAX_WEIXIN_SESSION_SLOTS = 9
WEIXIN_RESTART_TASK_PREFIX = "weixin-restart-"
LOGGER = logging.getLogger("hub.openclaw.weixin_chub_mode")


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class WeixinChubModeRuntimeConfig(_StrictModel):
    enabled: bool = False
    workspace_id: Literal["home", "workspace", "chub"] = "chub"
    permission_mode: PermissionMode = "full-access"
    model: str | None = Field(default=None, max_length=128)
    reasoning_effort: str | None = Field(default=None, max_length=32)


class WeixinChubModeSubmission(_StrictModel):
    message_id: str = Field(min_length=1, max_length=500)
    correlation_id: str | None = Field(default=None, max_length=500)
    operation_id: str = Field(min_length=1, max_length=128)
    delivery_route_fingerprint: str | None = Field(default=None, max_length=64)
    status: Literal["reserved", "submitted", "rejected", "passed", "routed"]
    code: WeixinChubModeSubmissionCode
    message: str = Field(max_length=3_000)
    http_status: Literal[200, 409, 503] | None = None
    session_id: str | None = Field(default=None, max_length=128)
    task_id: str | None = Field(default=None, max_length=128)
    new_session: bool = False
    session_slot: int | None = Field(default=None, ge=1, le=MAX_WEIXIN_SESSION_SLOTS)
    session_title: str | None = Field(default=None, max_length=48)
    dispatch_disposition: Literal["pass", "reply", "handled"] | None = None
    created_at: datetime
    updated_at: datetime


class WeixinChubModePendingRetry(_StrictModel):
    original_message_id: str = Field(min_length=1, max_length=500)
    prompt: str = Field(min_length=1, max_length=MAX_PENDING_RETRY_PROMPT_CHARS)
    delivery_route_fingerprint: str = Field(min_length=64, max_length=64)
    created_at: datetime
    expires_at: datetime
    session_id: str | None = Field(default=None, max_length=128)
    claimed_by_message_id: str | None = Field(default=None, max_length=500)
    claimed_session_id: str | None = Field(default=None, max_length=128)


class WeixinChubModeSessionSlot(_StrictModel):
    slot: int = Field(ge=1, le=MAX_WEIXIN_SESSION_SLOTS)
    session_id: str = Field(min_length=1, max_length=128)


class WeixinChubModeRestartOperation(_StrictModel):
    message_id: str = Field(min_length=1, max_length=500)
    operation_id: str = Field(min_length=1, max_length=128)
    coordinator_operation_id: str = Field(min_length=1, max_length=128)
    source_ip: str = Field(min_length=1, max_length=128)
    delivery_route_fingerprint: str = Field(min_length=64, max_length=64)
    delivery_route: QuickInteractionWeixinRoute
    status: Literal[
        "pending",
        "started",
        "succeeded",
        "start_failed",
        "sensitive_task_failed",
        "cleared",
    ] = "pending"
    error: str | None = Field(default=None, max_length=500)
    notification_status: Literal[
        "pending",
        "sending",
        "sent",
        "failed",
        "skipped",
    ] | None = None
    notification_error: str | None = Field(default=None, max_length=1_000)
    created_at: datetime
    updated_at: datetime


class WeixinChubModeState(_StrictModel):
    version: Literal[1] = 1
    configuration: WeixinChubModeRuntimeConfig
    session_id: str | None = None
    pending_retry: WeixinChubModePendingRetry | None = None
    session_slots: list[WeixinChubModeSessionSlot] = Field(default_factory=list)
    submissions: list[WeixinChubModeSubmission] = Field(default_factory=list)
    restart_operations: list[WeixinChubModeRestartOperation] = Field(
        default_factory=list
    )


class WeixinChubModeStatus(_StrictModel):
    enabled: bool
    ready: bool
    code: WeixinChubModeCode
    message: str


class WeixinChubModeSubmissionResult(_StrictModel):
    accepted: Literal[True] = True
    duplicate: bool
    new_session: bool
    code: Literal["submitted"] = "submitted"
    message: str
    task_summary: str | None = Field(default=None, max_length=20)
    session_slot: int | None = Field(default=None, ge=1, le=MAX_WEIXIN_SESSION_SLOTS)
    session_title: str | None = Field(default=None, max_length=48)


class WeixinChubModeDispatchResult(_StrictModel):
    protocol_version: Literal[3] = 3
    disposition: Literal["pass", "reply", "handled"]
    message: str | None = Field(default=None, max_length=3000)


TASK_STATUS_CHECK_PROMPTS = frozenset(
    {"查询状态", "状态查询", "检查状态", "状态检查"}
)
CHUB_STATUS_PROMPT = "chub"
CHUB_SYNC_PROMPTS = frozenset({"sync"})
CHUB_SYNC_ALIASES = frozenset({"同步状态", "状态同步"})
CHUB_RESTART_PROMPTS = frozenset({"restart"})
CHUB_RESTART_ALIASES = frozenset({"重启"})
SESSION_NEW_PROMPT = "session new"
SESSION_RETRY_PROMPT = "session retry"
SESSION_NEW_RETRY_PROMPT = "session new retry"
SESSION_SWITCH_PROMPT = "session switch"
SESSION_SWITCH_PATTERN = re.compile(r"session switch ([1-9])")
SESSION_SWITCH_TASK_PATTERN = re.compile(
    r"session\s+switch\s+([1-9])", re.IGNORECASE
)
CHINESE_SWITCH_PATTERN = re.compile(
    r"(?:切换(?:会话)?|会话)\s*([1-9一二三四五六七八九])"
)
CHINESE_SWITCH_COMMAND_PATTERN = re.compile(
    r"(?:切换(?:会话)?|会话)"
    r"(?:\s*[+\-＋－]?[0-9０-９零〇一二三四五六七八九十百千万两]+)?"
)
SESSION_ARCHIVE_PROMPT = "session archive"
SESSION_ARCHIVE_PATTERN = re.compile(r"session archive ([1-9])")
SESSION_ARCHIVE_TASK_PATTERN = re.compile(
    r"session\s+archive\s+([1-9])", re.IGNORECASE
)
CHINESE_ARCHIVE_PATTERN = re.compile(
    r"归档(?:会话)?\s*([1-9一二三四五六七八九])"
)
CHINESE_ARCHIVE_COMMAND_PATTERN = re.compile(
    r"归档(?:会话)?(?:\s*[+\-＋－]?[0-9０-９零〇一二三四五六七八九十百千万两]+)?"
)
CHINESE_SLOT_NUMBERS = {
    "一": 1,
    "二": 2,
    "三": 3,
    "四": 4,
    "五": 5,
    "六": 6,
    "七": 7,
    "八": 8,
    "九": 9,
}
CONTINUE_RETRY_PROMPTS = frozenset({"新建会话执行"})
WEEKLY_WINDOW_MINUTES = 7 * 24 * 60
CODEX_STATUS_TIMEOUT_SECONDS = 9
MAX_EPHEMERAL_STATUS_REPLIES = 256
MAX_EPHEMERAL_STATUS_INFLIGHT = 64
COMMAND_TASK_SEPARATORS = frozenset(":：,，.。;；!?！？")


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
        system_status_reader: Callable[[], object] | None = None,
        restart_coordinator: DeferredRestartCoordinator | None = None,
        restart_notifier: Callable[
            [QuickInteractionWeixinRoute, DeferredRestartOutcome, str | None],
            object,
        ]
        | None = None,
        ai_usage_reader: AiUsageService | None = None,
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
        self.system_status_reader = system_status_reader
        self.restart_coordinator = restart_coordinator
        self.restart_notifier = restart_notifier
        self.path = settings.openclaw.weixin_chub_mode.state_file
        self._lock = threading.RLock()
        self._restart_lock = threading.Lock()
        self._slot_lock = threading.RLock()
        self._status_condition = threading.Condition()
        self._status_refreshing = False
        self._status_refresh_succeeded = False
        self._status_refresh_note: str | None = None
        self._status_cache: dict[str, tuple[object, datetime]] = {}
        self._task_status_cache: dict[str, tuple[object, datetime]] = {}
        self._ephemeral_status_replies: dict[str, tuple[str, str | None, float]] = {}
        self._status_cache_started = False
        self._state_error = False
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
        if state.pending_retry is not None and (
            state.pending_retry.expires_at <= utc_now()
            or state.pending_retry.claimed_by_message_id is not None
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
            self.codex_manager.model_catalog.validate(
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
                session_id, new_session = self._ensure_session(configuration)
                if self.quick_interactions.is_running(session_id):
                    self._reject_busy_with_pending_retry(
                        reservation,
                        prompt=prompt,
                        route_fingerprint=route_fingerprint,
                        session_id=session_id,
                    )
                    raise ApiError(
                        409,
                        "weixin_chub_mode_in_progress",
                        "微信通道当前绑定 Session 正在执行任务，请等待完成。",
                    )
                with self.quick_interactions.session_operation_guard(session_id):
                    session = self.codex_manager.get_session(session_id)
                    if not new_session and session.activity == "unknown":
                        self._reclaim_unknown_session(
                            session_id,
                            session.codex_session_id,
                            operation_id,
                            source_ip,
                        )
                    session_slot = self._slot_for_session(session_id)
                    session_title = build_task_summary(session.title or prompt)
                    submit_kwargs = {
                        "operation_id": operation_id,
                        "source_ip": source_ip,
                        "notification_route": delivery_route,
                        "weixin_session_slot": session_slot,
                        "weixin_session_title": session_title,
                    }
                    task = self.quick_interactions.submit(
                        session_id,
                        prompt,
                        **submit_kwargs,
                    )
            except ApiError as exc:
                if reservation.status == "reserved":
                    if exc.code == "quick_interaction_in_progress" and session_id:
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

            reservation.status = "submitted"
            reservation.code = "submitted"
            reservation.message = "任务已提交，完成后将通过微信发送结果。"
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
            if self.translation_manager is not None:
                try:
                    self.translation_manager.enqueue(
                        message_id=message_id,
                        original=prompt,
                        route=delivery_route,
                        operation_id=operation_id,
                        source_ip=source_ip,
                    )
                except Exception:
                    LOGGER.warning(
                        "Unable to enqueue Weixin translation after submission",
                        exc_info=True,
                    )
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
                task_summary=(
                    getattr(task, "summary", None) or build_task_summary(prompt)
                ),
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
        normalized_prompt = self._normalize_fixed_prompt(prompt)
        normalized_codex_prompt = normalized_prompt.casefold()
        mode_enabled = self._mode_enabled
        if mode_enabled and (
            normalized_prompt in TASK_STATUS_CHECK_PROMPTS
            or normalized_codex_prompt == CHUB_STATUS_PROMPT
        ):
            return self._dispatch_chub_status(
                message_id=message_id,
                route_fingerprint=self._route_fingerprint(delivery_route),
                delivery_route=delivery_route,
            )
        if mode_enabled and (
            normalized_codex_prompt in CHUB_SYNC_PROMPTS
            or normalized_prompt in CHUB_SYNC_ALIASES
        ):
            return self._dispatch_chub_sync(
                message_id=message_id,
                correlation_id=correlation_id,
                route_fingerprint=self._route_fingerprint(delivery_route),
                source_ip=source_ip,
            )
        route_fingerprint = self._route_fingerprint(delivery_route)
        ephemeral = self._wait_for_ephemeral_reply(
            message_id,
            route_fingerprint,
        )
        if ephemeral is not None:
            return ephemeral
        if mode_enabled and (
            normalized_codex_prompt in CHUB_RESTART_PROMPTS
            or normalized_prompt in CHUB_RESTART_ALIASES
        ):
            return self._dispatch_chub_restart(
                message_id=message_id,
                correlation_id=correlation_id,
                route_fingerprint=route_fingerprint,
                source_ip=source_ip,
                delivery_route=delivery_route,
            )
        with self._slot_lock, self._lock:
            if self._state_error:
                self._log_standalone_dispatch("failed", source_ip)
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
                    self._log_standalone_dispatch("failed", source_ip)
                    return self._dispatch_failure("message_conflict")
                if duplicate.status == "passed":
                    self._log_standalone_dispatch("succeeded", source_ip)
                    return WeixinChubModeDispatchResult(
                        disposition="pass",
                    )
                if duplicate.status == "routed":
                    self._log_standalone_dispatch("succeeded", source_ip)
                    return WeixinChubModeDispatchResult(
                        disposition=duplicate.dispatch_disposition or "reply",
                        message=duplicate.message or None,
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
                    return self._dispatch_failure_from_error(exc)
                self._log_standalone_dispatch("succeeded", source_ip)
                return WeixinChubModeDispatchResult(
                    disposition="handled",
                )

            readiness = self.status()
            if readiness.code == "disabled":
                if duplicate is None:
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

            if normalized_codex_prompt == SESSION_RETRY_PROMPT:
                return self._dispatch_codex_retry(
                    message_id=message_id,
                    correlation_id=correlation_id,
                    route_fingerprint=route_fingerprint,
                    source_ip=source_ip,
                    delivery_route=delivery_route,
                    create_new_session=False,
                )

            retry_command, retry_task = self._split_command_task(
                prompt,
                (SESSION_NEW_RETRY_PROMPT, *CONTINUE_RETRY_PROMPTS),
            )
            if retry_command and retry_task is not None:
                operation_id = uuid4().hex
                self._log_dispatch(operation_id, "requested", source_ip)
                self._log_dispatch(operation_id, "started", source_ip)
                return self._remember_fixed_reply(
                    message_id=message_id,
                    correlation_id=correlation_id,
                    operation_id=operation_id,
                    route_fingerprint=route_fingerprint,
                    source_ip=source_ip,
                    message=(
                        "“新建会话执行”只用于继续最近一条未提交任务，"
                        "请不要附带新正文。"
                    ),
                    code="codex_retry_checked",
                    failed=True,
                )
            if retry_command:
                return self._dispatch_codex_retry(
                    message_id=message_id,
                    correlation_id=correlation_id,
                    route_fingerprint=route_fingerprint,
                    source_ip=source_ip,
                    delivery_route=delivery_route,
                    create_new_session=True,
                )

            new_command, new_task = self._split_command_task(
                prompt,
                (SESSION_NEW_PROMPT, "新建会话"),
            )
            if (
                new_command
                and not normalized_codex_prompt.startswith(SESSION_NEW_RETRY_PROMPT)
            ):
                return self._dispatch_codex_new(
                    message_id=message_id,
                    correlation_id=correlation_id,
                    route_fingerprint=route_fingerprint,
                    source_ip=source_ip,
                    delivery_route=delivery_route,
                    task_prompt=new_task,
                )

            archive_command = self._split_numbered_command_task(
                prompt,
                SESSION_ARCHIVE_TASK_PATTERN,
            ) or self._split_numbered_command_task(
                prompt,
                CHINESE_ARCHIVE_PATTERN,
            )
            if archive_command is not None:
                requested_index, archive_task = archive_command
                return self._dispatch_codex_archive(
                    message_id=message_id,
                    correlation_id=correlation_id,
                    route_fingerprint=route_fingerprint,
                    source_ip=source_ip,
                    delivery_route=delivery_route,
                    requested_index=requested_index,
                    invalid_usage=archive_task is not None,
                )

            if CHINESE_ARCHIVE_COMMAND_PATTERN.fullmatch(normalized_prompt):
                return self._dispatch_codex_archive(
                    message_id=message_id,
                    correlation_id=correlation_id,
                    route_fingerprint=route_fingerprint,
                    source_ip=source_ip,
                    delivery_route=delivery_route,
                    requested_index=None,
                    invalid_usage=True,
                )

            if normalized_codex_prompt.startswith(SESSION_ARCHIVE_PROMPT):
                match = SESSION_ARCHIVE_PATTERN.fullmatch(normalized_codex_prompt)
                requested_index = None
                invalid_usage = match is None
                if match is not None:
                    try:
                        requested_index = int(match.group(1))
                    except ValueError:
                        invalid_usage = True
                return self._dispatch_codex_archive(
                    message_id=message_id,
                    correlation_id=correlation_id,
                    route_fingerprint=route_fingerprint,
                    source_ip=source_ip,
                    delivery_route=delivery_route,
                    requested_index=requested_index,
                    invalid_usage=invalid_usage,
                )

            switch_command = self._split_numbered_command_task(
                prompt,
                SESSION_SWITCH_TASK_PATTERN,
            ) or self._split_numbered_command_task(
                prompt,
                CHINESE_SWITCH_PATTERN,
            )
            if switch_command is not None:
                requested_index, switch_task = switch_command
                return self._dispatch_codex_switch(
                    message_id=message_id,
                    correlation_id=correlation_id,
                    route_fingerprint=route_fingerprint,
                    source_ip=source_ip,
                    requested_index=requested_index,
                    invalid_usage=False,
                    delivery_route=delivery_route,
                    task_prompt=switch_task,
                )

            if CHINESE_SWITCH_COMMAND_PATTERN.fullmatch(normalized_prompt):
                return self._dispatch_codex_switch(
                    message_id=message_id,
                    correlation_id=correlation_id,
                    route_fingerprint=route_fingerprint,
                    source_ip=source_ip,
                    requested_index=None,
                    invalid_usage=True,
                    delivery_route=delivery_route,
                )

            if normalized_codex_prompt.startswith(f"{SESSION_SWITCH_PROMPT} "):
                match = SESSION_SWITCH_PATTERN.fullmatch(normalized_codex_prompt)
                requested_index = None
                invalid_usage = match is None
                if match is not None:
                    try:
                        requested_index = int(match.group(1))
                    except ValueError:
                        invalid_usage = True
                return self._dispatch_codex_switch(
                    message_id=message_id,
                    correlation_id=correlation_id,
                    route_fingerprint=route_fingerprint,
                    source_ip=source_ip,
                    requested_index=requested_index,
                    invalid_usage=invalid_usage,
                    delivery_route=delivery_route,
                )

            try:
                self.submit(
                    message_id=message_id,
                    prompt=prompt,
                    correlation_id=correlation_id,
                    source_ip=source_ip,
                    delivery_route=delivery_route,
                )
            except ApiError as exc:
                return self._dispatch_failure_from_error(exc)

            return WeixinChubModeDispatchResult(disposition="handled")

    def _dispatch_codex_new(
        self,
        *,
        message_id: str,
        correlation_id: str | None,
        route_fingerprint: str,
        source_ip: str,
        delivery_route: QuickInteractionWeixinRoute,
        task_prompt: str | None = None,
    ) -> WeixinChubModeDispatchResult:
        operation_id = uuid4().hex
        self._log_dispatch(operation_id, "requested", source_ip)
        self._log_dispatch(operation_id, "started", source_ip)
        now = utc_now()
        reservation = WeixinChubModeSubmission(
            message_id=message_id,
            correlation_id=correlation_id,
            operation_id=operation_id,
            delivery_route_fingerprint=route_fingerprint,
            status="reserved",
            code="submission_interrupted",
            message="Chub 未能确认本次 Session 创建结果，请发送一条新消息重试。",
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
        except ApiError as exc:
            message, _status_failed = self._codex_operation_message(
                f"创建状态：创建失败，{exc.message}"
            )
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
                message="新 Session 创建失败，当前绑定未改变，请稍后重试。",
                code="codex_session_created",
                failed=True,
            )
        slot = self._slot_for_session(session_id)
        slot_text = f"Session {slot}" if slot is not None else "Session"
        task_status = ""
        if task_prompt is not None:
            try:
                submission = self.submit(
                    message_id=self._command_task_message_id(message_id),
                    prompt=task_prompt,
                    correlation_id=correlation_id,
                    source_ip=source_ip,
                    delivery_route=delivery_route,
                )
            except ApiError as exc:
                failure = self._dispatch_failure_from_error(exc)
                message, _status_failed = self._codex_operation_message(
                    f"创建状态：{slot_text} 已创建并切换，但任务未提交。\n\n"
                    f"{failure.message or '任务提交失败，请稍后重试。'}"
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
            task_status = (
                f"\n\n任务状态：已提交。\n\n"
                f"任务摘要：{submission.task_summary or build_task_summary(task_prompt)}"
            )
        message, _status_failed = self._codex_operation_message(
            f"创建状态：{slot_text} 已创建并切换。{task_status}"
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
                message="没有可继续执行的任务，请重新发送任务内容。",
                code="codex_retry_checked",
            )

        now = utc_now()
        command_reservation = WeixinChubModeSubmission(
            message_id=message_id,
            correlation_id=correlation_id,
            operation_id=command_operation_id,
            delivery_route_fingerprint=route_fingerprint,
            status="reserved",
            code="submission_interrupted",
            message="Chub 未能确认本次待继续任务结果，请发送一条新指令重试。",
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
            return self._dispatch_failure("state_unavailable")
        self._state = reserved_state

        claimed_state = self._state.model_copy(deep=True)
        if claimed_state.pending_retry is None:
            return self._dispatch_failure("state_unavailable")
        claimed_state.pending_retry.claimed_by_message_id = message_id
        try:
            self._write_state(claimed_state)
        except OSError:
            self._state_error = True
            self._log_dispatch(command_operation_id, "failed", source_ip)
            return self._dispatch_failure("state_unavailable")
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
                return self._remember_fixed_reply(
                    message_id=message_id,
                    correlation_id=correlation_id,
                    operation_id=command_operation_id,
                    route_fingerprint=route_fingerprint,
                    source_ip=source_ip,
                    message=(
                        "新 Session 创建失败，当前绑定未改变；"
                        "待继续任务已保留，请稍后重试。"
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
                    "已创建并切换到新的 Session，但刚才的任务提交失败；"
                    "待继续任务已保留，请回复：session retry。"
                )
            return self._finish_retry_command(
                command_reservation,
                result.message or "任务提交失败，请稍后重试。",
                source_ip=source_ip,
                failed=True,
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
        summary = submission.task_summary or build_task_summary(pending.prompt)
        prefix = (
            "已创建并切换到新的 Session，刚才的任务已重新提交。"
            if create_new_session
            else "刚才的任务已重新提交。"
        )
        return self._finish_retry_command(
            command_reservation,
            "\n\n".join(
                item
                for item in (
                    prefix,
                    (
                        f"Session：{submission.session_slot} · {submission.session_title}"
                        if submission.session_slot is not None and submission.session_title
                        else None
                    ),
                    f"任务摘要：{summary}",
                    "完成后将原路发送结果。",
                )
                if item
            ),
            source_ip=source_ip,
        )

    def _finish_retry_command(
        self,
        record: WeixinChubModeSubmission,
        message: str,
        *,
        source_ip: str,
        failed: bool = False,
    ) -> WeixinChubModeDispatchResult:
        record.status = "routed"
        record.code = "codex_retry_checked"
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
        return WeixinChubModeDispatchResult(disposition="reply", message=message)

    @staticmethod
    def _retry_submission_message_id(
        command_message_id: str,
        original_message_id: str,
    ) -> str:
        digest = hashlib.sha256(
            f"{command_message_id}\0{original_message_id}".encode("utf-8")
        ).hexdigest()
        return f"retry-{digest}"

    @staticmethod
    def _command_task_message_id(command_message_id: str) -> str:
        digest = hashlib.sha256(
            f"{command_message_id}\0command-task".encode("utf-8")
        ).hexdigest()
        return f"command-task-{digest}"

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
    ) -> WeixinChubModeDispatchResult:
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
            return self._dispatch_failure("state_unavailable")
        self._state = next_state
        self._log_dispatch(operation_id, "failed" if failed else "succeeded", source_ip)
        if code in {
            "codex_session_created",
            "codex_session_archived",
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
                "Chub 重启未登记：重启协调能力不可用。",
                failed=True,
            )
        try:
            route_error = (
                self.route_validator(delivery_route)
                if self.route_validator is not None
                else "无法确认本次消息的微信回送通道。"
            )
        except Exception:
            LOGGER.warning("Unable to validate Weixin restart route", exc_info=True)
            route_error = "无法确认本次消息的微信回送通道。"
        if route_error:
            return remember(
                f"Chub 重启未登记：{route_error}",
                failed=True,
            )

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
                "Chub 重启已在处理中，完成后将原路发送结果。"
            )

        restart_operation_id = f"{operation_id}:restart"
        try:
            registration = self.restart_coordinator.request(
                operation_id=restart_operation_id,
                task_id=f"{WEIXIN_RESTART_TASK_PREFIX}{operation_id}",
                source_ip=source_ip,
            )
        except (ApiError, OSError):
            LOGGER.warning("Unable to register Weixin Chub restart", exc_info=True)
            return remember(
                "Chub 重启未登记，请稍后重试。",
                failed=True,
            )

        now = utc_now()
        restart_operation = WeixinChubModeRestartOperation(
            message_id=message_id,
            operation_id=restart_operation_id,
            coordinator_operation_id=registration.operation_id,
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
        if not registration.created:
            write_operation(
                operation_id=restart_operation_id,
                action="restart_hub",
                status="requested",
                target="chub",
                source_ip=source_ip,
            )

        reply_message = "Chub 重启已登记，完成后将原路发送结果。"
        result = remember(reply_message)
        if result.message == reply_message:
            self.restart_coordinator.maybe_schedule()
        return result

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
        return "ready" if registered else "sensitive_task_failed"

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
                        if outcome == "start_failed" and failure_reason
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
                        message="状态查询繁忙，请稍后重试。",
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
                message="状态查询正在处理中，请稍后重试。",
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
            message = "Chub 状态总览生成失败，请稍后重试。"
        with self._status_condition:
            self._ephemeral_status_replies[message_id] = (
                route_fingerprint,
                message,
                time.monotonic(),
            )
            self._prune_ephemeral_status_replies(message_id)
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
                        message="状态查询正在处理中，请稍后重试。",
                    )
                cached_message = cached[1]
            return WeixinChubModeDispatchResult(
                disposition="reply",
                message=cached_message,
            )

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
            message=cached_message or "状态查询正在处理中，请稍后重试。",
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
                title=build_task_summary(
                    getattr(session, "title", None)
                    or "未命名 Session"
                ),
                state=self._codex_session_dispatch_state(session),
                current=session.id == current_session_id,
            )
            for session in sessions_newest_first(sessions.values())
            if session.id in slots_by_session_id
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
        anomalies: list[str] = []
        readiness_cached = cache.get("readiness")
        readiness = readiness_cached[0] if readiness_cached is not None else None
        lines = [f"Chub · {self._format_elapsed_time(elapsed_ms)}"]
        if readiness is None:
            anomalies.append("Chub 状态尚未初始化")
        elif not readiness.ready:
            anomalies.append(readiness.message)
        system_cached = cache.get("system")
        if system_cached is not None:
            system = system_cached[0]
            memory = float(getattr(system.system, "memory_percent", 0.0))
            disk = float(getattr(system.system, "disk_percent", 0.0))
            if memory >= 85:
                anomalies.append(f"内存使用率较高：{memory:.0f}%")
            if disk >= 85:
                anomalies.append(f"磁盘使用率较高：{disk:.0f}%")
        if task_cached is None:
            tasks = None
        else:
            tasks = task_cached[0]
            failed_notifications = int(
                getattr(tasks, "failed_notification_count", 0)
            )
            if failed_notifications:
                anomalies.append(f"{failed_notifications} 个任务结果通知失败")
        if failed_restart_notifications:
            anomalies.append(
                f"{failed_restart_notifications} 个重启结果通知失败"
            )
        session_cached = cache.get("sessions")
        if session_cached is None:
            sessions = ()
        else:
            sessions, checked_at = session_cached
            display_states = {
                item.session_id: (
                    "Busy"
                    if self.quick_interactions.is_running(item.session_id)
                    else item.state
                )
                for item in sessions
            }
        running_tasks = {
            session_id: summary
            for session_id, summary in getattr(tasks, "running_tasks", ())
        }
        account_cached = cache.get("account")
        if account_cached is None:
            usage_message = "Weekly 暂不可用"
        else:
            account = account_cached[0]
            usage_message = self._usage_message(account)

        if anomalies:
            lines.extend(["", "异常"])
            lines.extend(
                f"{index}. {message}"
                for index, message in enumerate(dict.fromkeys(anomalies), start=1)
            )

        session_lines = ["Sessions"]
        if sessions:
            for item in sessions:
                state = display_states[item.session_id]
                session_lines.append(f"S{item.slot} · {item.title}")
                task_summary = running_tasks.get(item.session_id)
                if state == "Busy" and task_summary:
                    session_lines.append(f"T{item.slot} · {task_summary}")
                status = f"{state} · Current" if item.current else state
                session_lines.append(status)
        else:
            session_lines.append(
                "暂无已分配 Session" if session_cached else "暂不可用"
            )
        lines.extend(["", "\n\n".join(session_lines)])

        lines.extend(["", "Codex", "", usage_message])
        return "\n".join(lines)

    @staticmethod
    def _format_elapsed_time(elapsed_ms: int) -> str:
        milliseconds = max(1, elapsed_ms)
        if milliseconds < 1000:
            return f"{milliseconds}ms"
        seconds = f"{milliseconds / 1000:.1f}".removesuffix(".0")
        return f"{seconds}s"

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
                    message="槽位同步正在处理中，请稍后重试。",
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
                        account_results.put("Codex 用量查询失败，请稍后重试。")

                deadline = time.monotonic() + CODEX_STATUS_TIMEOUT_SECONDS
                try:
                    threading.Thread(target=read_account, daemon=True).start()
                except RuntimeError:
                    LOGGER.warning(
                        "Unable to start Codex usage check during slot synchronization"
                    )
                    account_results.put("Codex 用量查询失败，请稍后重试。")
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
                        message="槽位同步失败，原有槽位未改变，请稍后重试。",
                        code="chub_slots_synced",
                        failed=True,
                    )
            try:
                usage_message = account_results.get(
                    timeout=max(0.0, deadline - time.monotonic())
                )
            except queue.Empty:
                LOGGER.warning("Codex usage check timed out during slot synchronization")
                usage_message = "Codex 用量查询失败，请稍后重试。"
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
                        message="槽位状态已变化，本次未写入，请重新发送 sync。",
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
                    "槽位无需调整。"
                    if not removed and not added
                    else f"槽位同步完成：清理 {removed} · 补充 {added} · 当前 {len(synced)}。"
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
                title=build_task_summary(
                    getattr(session, "title", None)
                    or "未命名 Session"
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
        return f"{operation_status}\n\n{codex_message}", codex_failed

    @staticmethod
    def _normalize_fixed_prompt(prompt: str) -> str:
        normalized = " ".join(prompt.strip().split())
        while normalized and unicodedata.category(normalized[0]).startswith("P"):
            normalized = normalized[1:].lstrip()
        while normalized and unicodedata.category(normalized[-1]).startswith("P"):
            normalized = normalized[:-1].rstrip()
        return normalized

    @staticmethod
    def _strip_command_leading_punctuation(prompt: str) -> str:
        value = prompt.strip()
        while value and unicodedata.category(value[0]).startswith("P"):
            value = value[1:].lstrip()
        return value

    @staticmethod
    def _split_command_task(
        prompt: str,
        commands: tuple[str, ...],
    ) -> tuple[bool, str | None]:
        value = WeixinChubModeManager._strip_command_leading_punctuation(prompt)
        folded = value.casefold()
        for command in sorted(commands, key=len, reverse=True):
            if not folded.startswith(command.casefold()):
                continue
            suffix = value[len(command) :]
            if not suffix:
                return True, None
            if not (
                suffix[0].isspace()
                or suffix[0] in COMMAND_TASK_SEPARATORS
            ):
                continue
            return True, WeixinChubModeManager._command_task_suffix(suffix)
        return False, None

    @staticmethod
    def _command_task_suffix(suffix: str) -> str | None:
        if not suffix or all(
            char.isspace() or char in COMMAND_TASK_SEPARATORS for char in suffix
        ):
            return None
        if suffix[0].isspace():
            return suffix.lstrip().rstrip() or None
        return suffix[1:].lstrip().rstrip() or None

    @staticmethod
    def _split_numbered_command_task(
        prompt: str,
        pattern: re.Pattern[str],
    ) -> tuple[int, str | None] | None:
        value = WeixinChubModeManager._strip_command_leading_punctuation(prompt)
        match = pattern.match(value)
        if match is None:
            return None
        slot = match.group(1)
        suffix = value[match.end() :]
        if suffix and not (
            suffix[0].isspace()
            or suffix[0] in COMMAND_TASK_SEPARATORS
        ):
            return None
        requested_index = int(slot) if slot.isdigit() else CHINESE_SLOT_NUMBERS[slot]
        return requested_index, WeixinChubModeManager._command_task_suffix(suffix)

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
            message = "Codex 用量查询失败，请稍后重试。"
            account_failed = True

        try:
            remaining = max(
                0.0,
                CODEX_STATUS_TIMEOUT_SECONDS - (time.monotonic() - started_at),
            )
            sessions_message, sessions_failed = session_results.get(timeout=remaining)
            if sessions_message is None:
                sessions_message = "Sessions\n\n暂不可用"
        except queue.Empty:
            LOGGER.warning("Codex session status check timed out")
            sessions_message = "Sessions\n\n暂不可用"
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
            return "Sessions\n\n暂无已分配 Session"
        return self._format_codex_sessions(visible, current_session_id, remaining)

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
            message="Chub 未能确认本次 Session 归档结果，请发送 chub 查看状态。",
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

        usage = (
            "用法：发送 session archive n、归档N 或归档会话N"
            "（1–9/一至九）。"
        )
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
                "Session 列表查询失败，本次未归档，请稍后重试。",
                source_ip=source_ip,
                failed=True,
            )

        target_entry = next(
            (entry for entry in visible if entry[0] == requested_index),
            None,
        )
        if target_entry is None:
            message, _status_failed = self._codex_operation_message(
                "归档状态：未归档，编号无效。",
                fill_session_candidates=False,
            )
            return self._finish_codex_archive(
                record,
                message,
                source_ip=source_ip,
                failed=True,
            )

        target_slot, target, listed_state = target_entry
        if listed_state != "Available":
            state_text = "正在使用" if listed_state == "Busy" else "状态不可确认"
            message, _status_failed = self._codex_operation_message(
                f"归档状态：未归档，目标 Session {state_text}。",
                fill_session_candidates=False,
            )
            return self._finish_codex_archive(
                record,
                message,
                source_ip=source_ip,
                failed=True,
            )

        try:
            refreshed = self.codex_manager.get_session(target.id)
            if (
                refreshed.id != target.id
                or self._slot_for_session(refreshed.id) != target_slot
                or not self._session_matches_configuration(refreshed, configuration)
                or getattr(refreshed, "activity", "unknown") == "unknown"
                or self._codex_session_dispatch_state(refreshed) != "Available"
            ):
                raise ValueError("Session is no longer safe to archive")
        except Exception:
            LOGGER.info("Codex archive target is no longer available", exc_info=True)
            message, _status_failed = self._codex_operation_message(
                "归档状态：未归档，目标 Session 状态已变化。",
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
                "归档状态：未归档，该 Session 关联一条待继续执行的任务。",
                fill_session_candidates=False,
            )
            return self._finish_codex_archive(
                record,
                message,
                source_ip=source_ip,
                failed=True,
            )

        if not getattr(refreshed, "codex_session_id", None):
            message, _status_failed = self._codex_operation_message(
                "归档状态：未归档，目标 Session 尚未启动。",
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
                "Session 归档能力当前不可用，本次未归档。",
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
                "quick_interaction_in_progress",
                "quick_interaction_terminal_working",
                "quick_interaction_writer_active",
            }:
                message = "目标 Session 状态已变化或正在使用，本次未归档。"
            else:
                message = (
                    "归档失败；Session 可能已停止，但未从列表移除。"
                    "请发送 chub 查看状态后再重试。"
                )
            return self._finish_codex_archive(
                record,
                message,
                source_ip=source_ip,
                failed=True,
            )

        self._log_archive(operation_id, "succeeded", refreshed.id, source_ip)

        title = build_task_summary(refreshed.title or "未命名 Session")
        was_current = self._state.session_id == refreshed.id
        record.status = "routed"
        record.code = "codex_session_archived"
        record.message = "Session 已归档；状态刷新被中断，请发送 chub 查看状态。"
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
                    "Session 已归档，但 Chub 未能同步列表状态。"
                    "请稍后发送 chub 查看状态。"
                ),
            )
        self._state = archived_state
        current_suffix = "，当前绑定已清除" if was_current else ""
        message, _status_failed = self._codex_operation_message(
            f"归档状态：Session {target_slot} 已归档{current_suffix}。",
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
                    "Session 已归档，但 Chub 未能同步列表状态。"
                    "请稍后发送 chub 查看状态。"
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
            message="Chub 未能确认本次 Session 切换，请发送一条新消息重试。",
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

        usage = (
            "用法：发送 session switch n 切换到指定编号，"
            "或发送切换N、切换会话N、会话N（1–9/一至九）。"
        )
        if invalid_usage or requested_index is None:
            return self._finish_codex_switch(
                record,
                usage,
                source_ip=source_ip,
                failed=True,
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
                account_results.put(("Codex 用量查询失败，请稍后重试。", True))

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
                "Session 列表查询失败，请稍后重试。",
                source_ip=source_ip,
                failed=True,
            )

        if not visible:
            candidate_hint = self._switch_candidate_hint(remaining)
            message, _status_failed = self._codex_operation_message(
                f"切换状态：未切换，当前没有可切换的 Session。{candidate_hint}",
                fill_session_candidates=False,
            )
            return self._finish_codex_switch(
                record,
                message,
                source_ip=source_ip,
                failed=True,
            )

        slot_indexes = [slot for slot, _session, _state in visible]
        if requested_index not in slot_indexes:
            candidate_hint = self._switch_candidate_hint(remaining)
            message, _status_failed = self._codex_operation_message(
                f"切换状态：未切换，编号无效。{candidate_hint}",
                fill_session_candidates=False,
            )
            return self._finish_codex_switch(
                record,
                message,
                source_ip=source_ip,
                failed=True,
            )
        target_index = slot_indexes.index(requested_index)

        target_slot, target, listed_state = visible[target_index]
        if listed_state == "Unavailable":
            message, _status_failed = self._codex_operation_message(
                "切换状态：未切换，目标 Session 当前不可用。",
                fill_session_candidates=False,
            )
            return self._finish_codex_switch(
                record,
                message,
                source_ip=source_ip,
                failed=True,
            )
        if task_prompt is not None and listed_state != "Available":
            message, _status_failed = self._codex_operation_message(
                "切换状态：未切换，目标 Session 正在执行，任务未提交。",
                fill_session_candidates=False,
            )
            return self._finish_codex_switch(
                record,
                message,
                source_ip=source_ip,
                failed=True,
            )
        if target.id == self._state.session_id and task_prompt is None:
            message, _status_failed = self._codex_operation_message(
                f"切换状态：未切换，Session {target_slot} 已是当前绑定。",
                fill_session_candidates=False,
            )
            return self._finish_codex_switch(
                record,
                message,
                source_ip=source_ip,
                failed=True,
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
            message, _status_failed = self._codex_operation_message(
                "切换状态：未切换，目标 Session 当前不可用。",
                fill_session_candidates=False,
            )
            return self._finish_codex_switch(
                record,
                message,
                source_ip=source_ip,
                failed=True,
            )

        visible[target_index] = (target_slot, refreshed, refreshed_state)
        try:
            usage_message, _usage_failed = account_results.get(
                timeout=max(0.0, deadline - time.monotonic())
            )
        except queue.Empty:
            LOGGER.warning("Codex usage check timed out during session switch")
            usage_message = "Codex 用量查询失败，请稍后重试。"
        sessions_message = self._format_codex_sessions(
            visible,
            refreshed.id,
            remaining,
        )
        message = (
            f"切换状态：已切换到 Session {target_slot}。\n\n"
            f"{sessions_message}\n\n{usage_message}"
        )

        record.status = "routed"
        record.code = "codex_switch_checked"
        record.message = message
        record.http_status = 200
        record.session_id = refreshed.id
        record.dispatch_disposition = "reply"
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
        if task_prompt is not None:
            try:
                submission = self.submit(
                    message_id=self._command_task_message_id(message_id),
                    prompt=task_prompt,
                    correlation_id=correlation_id,
                    source_ip=source_ip,
                    delivery_route=delivery_route,
                )
            except ApiError as exc:
                failure = self._dispatch_failure_from_error(exc)
                return self._finish_codex_switch(
                    record,
                    f"切换状态：已切换到 Session {target_slot}，但任务未提交。\n\n"
                    f"{failure.message or '任务提交失败，请稍后重试。'}",
                    source_ip=source_ip,
                    failed=True,
                )
            return self._finish_codex_switch(
                record,
                f"切换状态：已切换到 Session {target_slot}。\n\n"
                "任务状态：已提交。\n\n"
                f"任务摘要：{submission.task_summary or build_task_summary(task_prompt)}",
                source_ip=source_ip,
            )
        self._log_dispatch(operation_id, "succeeded", source_ip)
        return WeixinChubModeDispatchResult(disposition="reply", message=message)

    @staticmethod
    def _switch_candidate_hint(remaining: int) -> str:
        if remaining <= 0:
            return ""
        return "另有未登记的可用 Session，请先发送 sync、同步状态或状态同步后再切换。"

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
    ) -> WeixinChubModeDispatchResult:
        record.status = "routed"
        record.code = "codex_switch_checked"
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

    @staticmethod
    def _format_codex_sessions(
        visible: list[tuple[int, object, str]],
        current_session_id: str | None,
        remaining: int,
    ) -> str:
        message = WeixinChubModeManager._format_session_blocks(
            (
                (
                    slot,
                    build_task_summary(session.title or "未命名 Session"),
                    state,
                    session.id == current_session_id,
                )
                for slot, session, state in visible
            )
        )
        if remaining:
            message = f"{message}\n\n另有 {remaining} 个"
        return message

    @staticmethod
    def _format_session_blocks(
        entries: Iterable[tuple[int, str, str, bool]],
    ) -> str:
        paragraphs = ["Sessions"]
        for slot, title, state, current in entries:
            paragraphs.append(f"S{slot} · {title}")
            paragraphs.append(f"{state} · Current" if current else state)
        if len(paragraphs) == 1:
            paragraphs.append("暂无已分配 Session")
        return "\n\n".join(paragraphs)

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

    def session_slot(self, session_id: str) -> int | None:
        """Return the current stable Weixin slot without changing slot state."""
        with self._lock:
            return self._slot_for_session(session_id)

    def session_slots_snapshot(self) -> dict[str, int]:
        """Return one consistent copy of the current Weixin slot mapping."""
        with self._lock:
            return {
                entry.session_id: entry.slot
                for entry in self._state.session_slots
            }

    def codex_status_message(self) -> str:
        with self._status_condition:
            account_cached = self._status_cache.get("account")
            sessions_cached = self._status_cache.get("sessions")
        if account_cached is None:
            try:
                usage_message = self._usage_message(
                    self._read_ai_usage(force=False)
                )
            except Exception:
                LOGGER.warning("AI usage snapshot is unavailable", exc_info=True)
                usage_message = "Weekly 暂不可用"
        else:
            usage_message = self._usage_message(account_cached[0])
        if sessions_cached is None:
            sessions_message = "Sessions\n\n暂不可用"
        else:
            sessions = sessions_cached[0]
            sessions_message = self._format_session_blocks(
                (
                    (
                        item.slot,
                        item.title,
                        "Busy"
                        if self.quick_interactions.is_running(item.session_id)
                        else item.state,
                        item.current,
                    )
                    for item in sessions
                )
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

    @staticmethod
    def _session_matches_configuration(
        session: object,
        configuration: WeixinChubModeRuntimeConfig,
    ) -> bool:
        return bool(
            getattr(session, "workspace_id", None) == configuration.workspace_id
            and getattr(session, "permission_mode", None)
            == configuration.permission_mode
            and configuration.permission_mode != "ask"
            and (
                configuration.model is None
                or getattr(session, "model", None) == configuration.model
            )
            and (
                configuration.reasoning_effort is None
                or getattr(session, "reasoning_effort", None)
                == configuration.reasoning_effort
            )
        )

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
        codex_session_id = getattr(session, "codex_session_id", None)
        if codex_session_id and self.codex_manager.has_active_writer(codex_session_id):
            return "Busy"
        return "Available"

    @staticmethod
    def _codex_usage_message(
        quota: CodexQuotaData,
        usage: CodexTokenUsageData,
    ) -> str:
        weekly = next(
            (
                window
                for window in quota.windows
                if window.window_duration_minutes == WEEKLY_WINDOW_MINUTES
            ),
            None,
        )
        weekly_text = (
            f"Weekly {weekly.remaining_percent}%"
            if weekly is not None
            else "Weekly 暂不可用"
        )
        today = datetime.now().astimezone().date()
        today_bucket = next(
            (bucket for bucket in usage.daily_usage if bucket.start_date == today),
            None,
        )
        if usage.status == "available" and today_bucket is not None:
            return (
                f"{weekly_text} · Today "
                f"{AiUsageService.compact_tokens(today_bucket.tokens)}"
            )
        return weekly_text

    def _read_ai_usage(self, *, force: bool) -> object:
        if self.ai_usage_reader is not None:
            return self.ai_usage_reader.read(force=force)
        if self.codex_account_reader is not None:
            return self.codex_account_reader.read_account_status(force=True)
        raise RuntimeError("AI usage reader is unavailable")

    def _usage_message(self, value: object) -> str:
        if isinstance(value, AiUsageData):
            if value.status == "available" and value.display.short:
                return value.display.short
            return "Weekly 暂不可用"
        quota, usage = value
        return self._codex_usage_message(quota, usage)

    @staticmethod
    def _compact_token_count(tokens: int) -> str:
        for divisor, suffix in (
            (1_000_000_000, "B"),
            (1_000_000, "M"),
            (1_000, "K"),
        ):
            if tokens >= divisor:
                return f"{tokens / divisor:.1f}{suffix}"
        return str(tokens)

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

    @staticmethod
    def _dispatch_failure_from_error(
        exc: ApiError,
    ) -> WeixinChubModeDispatchResult:
        code_map: dict[str, WeixinChubModeDispatchCode] = {
            "weixin_chub_mode_in_progress": "in_progress",
            "weixin_chub_mode_configuration_invalid": "configuration_invalid",
            "weixin_chub_mode_codex_unavailable": "codex_unavailable",
            "weixin_chub_mode_delivery_route_invalid": "delivery_route_invalid",
            "weixin_chub_mode_message_conflict": "message_conflict",
            "weixin_chub_mode_submission_interrupted": "submission_interrupted",
            "weixin_chub_mode_state_unavailable": "state_unavailable",
        }
        return WeixinChubModeManager._dispatch_failure(
            code_map.get(exc.code, "submission_failed")
        )

    @staticmethod
    def _dispatch_failure(
        code: WeixinChubModeDispatchCode,
    ) -> WeixinChubModeDispatchResult:
        messages = {
            "in_progress": (
                "任务提交失败：当前 Session 正在执行，本任务未提交。\n\n"
                "如需新建 Session 并继续执行本任务，请回复："
                "session new retry 或“新建会话执行”。"
            ),
            "configuration_invalid": (
                "任务提交失败：微信 Chub 模式配置无效，请检查工作区、权限、模型和微信通知配置。"
            ),
            "codex_unavailable": "任务提交失败：Codex 当前不可用，请稍后重试。",
            "delivery_route_invalid": "任务提交失败：无法确认本次消息的微信回送通道，请稍后重试。",
            "message_conflict": "任务提交失败：该消息的回送通道与首次提交不一致。",
            "submission_interrupted": "上次提交被 Chub 重启中断，请重新发送任务。",
            "state_unavailable": "任务提交失败：Chub 当前状态不可用，请稍后重试。",
            "submission_failed": "任务提交失败，请稍后重试。",
        }
        return WeixinChubModeDispatchResult(
            disposition="reply",
            message=messages.get(code, "任务提交失败，请稍后重试。"),
        )

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
        codex_session_id: str | None,
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
                    codex_session_id,
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
        if submission.status == "submitted":
            return self._result(submission, duplicate=True, task_summary=None)
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

    @staticmethod
    def _safe_submission_error(exc: ApiError) -> str:
        allowed = {
            "quick_interaction_in_progress": "微信通道当前绑定 Session 正在执行任务，请等待完成。",
            "quick_interaction_terminal_working": "微信通道当前绑定 Session 正在由终端使用。",
            "quick_interaction_terminal_active": "微信通道当前绑定 Session 不能执行快速交互。",
            "quick_interaction_writer_active": (
                "微信通道当前绑定 Session 仍由实时终端占用，请先停止终端。"
            ),
            "codex_writer_status_unavailable": (
                "暂时无法确认微信通道当前绑定 Session 是否可写，请稍后重试。"
            ),
            "weixin_chub_mode_session_reclaim_failed": (
                "微信通道当前绑定 Session 状态未知且未能安全停止，请稍后重试。"
            ),
            "quick_interaction_requires_terminal": "当前权限不支持微信快速交互。",
            "codex_model_unavailable": "所选 Codex 模型当前不可用。",
            "codex_reasoning_effort_unsupported": "所选推理等级当前不可用。",
            "weixin_chub_mode_session_slots_full": (
                "9 个微信 Session 槽位已满，请先归档或删除一个 Session。"
            ),
        }
        return allowed.get(exc.code, "微信任务提交失败。")

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
