from __future__ import annotations

import hashlib
import json
import logging
import os
import queue
import re
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Callable, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

from app.codex.models import (
    CodexQuotaData,
    CodexTokenUsageData,
    PermissionMode,
    QuickInteractionTask,
    QuickInteractionWeixinRoute,
    utc_now,
)
from app.codex.quick_interactions import build_task_summary
from app.codex.rate_limits import CodexRateLimitService
from app.core.config import OpenClawWeixinChubModeConfig, Settings
from app.core.response import ApiError
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
    "codex_status_checked",
    "codex_switch_checked",
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
MAX_DISPATCH_MESSAGE_CHARS = 3_000
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
    dispatch_disposition: Literal["pass", "reply", "handled"] | None = None
    created_at: datetime
    updated_at: datetime


class WeixinChubModeState(_StrictModel):
    version: Literal[1] = 1
    configuration: WeixinChubModeRuntimeConfig
    session_id: str | None = None
    submissions: list[WeixinChubModeSubmission] = Field(default_factory=list)


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
    task_summary: str | None = Field(default=None, max_length=13)


class WeixinChubModeDispatchResult(_StrictModel):
    protocol_version: Literal[3] = 3
    disposition: Literal["pass", "reply", "handled"]
    message: str | None = Field(default=None, max_length=3000)


TASK_STATUS_CHECK_PROMPTS = frozenset(
    {"检查任务状态", "任务状态", "查询任务结果", "任务结果"}
)
CODEX_STATUS_PROMPT = "codex"
CODEX_SWITCH_PROMPT = "codex switch"
CODEX_SWITCH_PATTERN = re.compile(r"codex switch ([1-9]\d*)")
WEEKLY_WINDOW_MINUTES = 7 * 24 * 60
MAX_CODEX_STATUS_SESSIONS = 10
CODEX_STATUS_TIMEOUT_SECONDS = 9


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
    ) -> None:
        self.settings = settings
        self.codex_manager = codex_manager
        self.quick_interactions = quick_interactions
        self.route_validator = route_validator
        self.terminal_reclaimer = terminal_reclaimer
        self.codex_account_reader = codex_account_reader
        self.path = settings.openclaw.weixin_chub_mode.state_file
        self._lock = threading.RLock()
        self._state_error = False
        self._state = self._load(settings.openclaw.weixin_chub_mode)

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
        with self._lock:
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
            if changed_session_configuration and self._state.session_id:
                if self.quick_interactions.is_running(self._state.session_id):
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
        with self._lock:
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
                session_id, new_session = self._ensure_session(configuration)
                if self.quick_interactions.is_running(session_id):
                    self._reject(
                        reservation,
                        "in_progress",
                        "微信通道当前绑定 Session 正在执行任务，请等待完成。",
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
                    task = self.quick_interactions.submit(
                        session_id,
                        prompt,
                        operation_id=operation_id,
                        source_ip=source_ip,
                        notification_route=delivery_route,
                    )
            except ApiError as exc:
                if reservation.status == "reserved":
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
        with self._lock:
            if self._state_error:
                self._log_standalone_dispatch("failed", source_ip)
                return self._dispatch_failure("state_unavailable")

            route_fingerprint = self._route_fingerprint(delivery_route)
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
                    disposition="reply",
                    message="该消息已处理，任务不会重复执行。",
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

            normalized_prompt = " ".join(prompt.strip().split())

            if normalized_prompt in TASK_STATUS_CHECK_PROMPTS:
                return self._dispatch_task_status(
                    message_id=message_id,
                    correlation_id=correlation_id,
                    route_fingerprint=route_fingerprint,
                    source_ip=source_ip,
                    delivery_route=delivery_route,
                )

            if normalized_prompt == CODEX_STATUS_PROMPT:
                return self._dispatch_codex_status(
                    message_id=message_id,
                    correlation_id=correlation_id,
                    route_fingerprint=route_fingerprint,
                    source_ip=source_ip,
                )

            if normalized_prompt == CODEX_SWITCH_PROMPT:
                return self._dispatch_codex_switch(
                    message_id=message_id,
                    correlation_id=correlation_id,
                    route_fingerprint=route_fingerprint,
                    source_ip=source_ip,
                    requested_index=None,
                    invalid_usage=False,
                )

            if normalized_prompt.startswith(CODEX_SWITCH_PROMPT):
                match = CODEX_SWITCH_PATTERN.fullmatch(normalized_prompt)
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
                return self._dispatch_failure_from_error(exc)

            if submission.duplicate:
                return WeixinChubModeDispatchResult(
                    disposition="reply",
                    message="该消息已处理，任务不会重复执行。",
                )
            message = self._submission_dispatch_message(
                submission,
                prompt=prompt,
                message_type=message_type,
            )
            return WeixinChubModeDispatchResult(
                disposition="reply",
                message=message,
            )

    def _dispatch_task_status(
        self,
        *,
        message_id: str,
        correlation_id: str | None,
        route_fingerprint: str,
        source_ip: str,
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
            message="Chub 未能确认本次状态检查结果，请发送一条新消息重试。",
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

        try:
            checked = self.quick_interactions.check_weixin_task_status(
                delivery_route,
                operation_id=operation_id,
                source_ip=source_ip,
            )
        except Exception:
            LOGGER.warning("Weixin task status check failed", exc_info=True)
            result = WeixinChubModeDispatchResult(
                disposition="reply",
                message="任务状态检查失败，请稍后重试。",
            )
            log_status = "failed"
        else:
            if checked.outcome == "running":
                tasks = tuple(getattr(checked, "tasks", ()) or ())
                if not tasks and checked.task is not None:
                    tasks = (checked.task,)
                summaries = [
                    getattr(task, "summary", None) or "本次微信任务"
                    for task in tasks
                ]
                running_count = getattr(checked, "running_count", 0) or len(summaries)
                if running_count == 1:
                    message = f"任务正在执行\n\n任务摘要：{summaries[0]}"
                else:
                    lines = [f"当前有 {running_count} 个任务正在执行：", ""]
                    lines.extend(
                        f"{index}. {summary}"
                        for index, summary in enumerate(summaries, start=1)
                    )
                    if running_count > len(summaries):
                        lines.append(f"另有 {running_count - len(summaries)} 个")
                    message = "\n".join(lines)
                notification_outcome = getattr(
                    checked,
                    "notification_outcome",
                    None,
                )
                if notification_outcome == "notification_queued":
                    message += "\n\n另有已结束任务，结果将原路补发。"
                elif notification_outcome == "notification_sending":
                    message += "\n\n另有已结束任务，结果正在原路发送。"
                elif notification_outcome == "notification_failed":
                    message += "\n\n另有已结束任务通知失败，请稍后再次检查。"
                result = WeixinChubModeDispatchResult(
                    disposition="reply",
                    message=message,
                )
            elif checked.outcome == "notification_queued":
                result = WeixinChubModeDispatchResult(disposition="handled")
            elif checked.outcome == "notification_sending":
                result = WeixinChubModeDispatchResult(
                    disposition="reply",
                    message="任务已结束，结果正在原路发送。",
                )
            elif checked.outcome == "notification_failed":
                result = WeixinChubModeDispatchResult(
                    disposition="reply",
                    message="任务已结束，但结果通知失败，请稍后再次检查。",
                )
            else:
                result = WeixinChubModeDispatchResult(
                    disposition="reply",
                    message="当前没有执行中或待通知的任务。",
                )
            log_status = "succeeded"

        record.status = "routed"
        record.code = "task_status_checked"
        record.message = result.message or ""
        record.http_status = 200
        record.dispatch_disposition = result.disposition
        record.updated_at = utc_now()
        try:
            self._replace_submission(record)
        except OSError:
            self._log_dispatch(operation_id, "failed", source_ip)
            if result.disposition == "handled":
                return result
            return self._dispatch_failure("state_unavailable")
        self._log_dispatch(operation_id, log_status, source_ip)
        return result

    def _dispatch_codex_status(
        self,
        *,
        message_id: str,
        correlation_id: str | None,
        route_fingerprint: str,
        source_ip: str,
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
            message="Chub 未能确认本次 Codex 状态，请发送一条新消息重试。",
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

        message, status_failed = self._read_codex_status_message(
            self._state.configuration.model_copy(deep=True),
            self._state.session_id,
        )
        log_status = "failed" if status_failed else "succeeded"

        result = WeixinChubModeDispatchResult(
            disposition="reply",
            message=message,
        )
        record.status = "routed"
        record.code = "codex_status_checked"
        record.message = message
        record.http_status = 200
        record.dispatch_disposition = "reply"
        record.updated_at = utc_now()
        try:
            self._replace_submission(record)
        except OSError:
            self._log_dispatch(operation_id, "failed", source_ip)
            return self._dispatch_failure("state_unavailable")
        self._log_dispatch(operation_id, log_status, source_ip)
        return result

    def _read_codex_status_message(
        self,
        configuration: WeixinChubModeRuntimeConfig,
        current_session_id: str | None,
    ) -> tuple[str, bool]:
        session_results: queue.Queue[tuple[str | None, bool]] = queue.Queue(maxsize=1)

        def read_sessions() -> None:
            try:
                session_results.put(
                    (
                        self._codex_sessions_message(
                            configuration,
                            current_session_id,
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
            if self.codex_account_reader is None:
                raise RuntimeError("Codex account reader is unavailable")
            quota, usage = self.codex_account_reader.read_account_status(force=True)
            message = self._codex_usage_message(quota, usage)
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
                sessions_message = "Active sessions: 暂不可用"
        except queue.Empty:
            LOGGER.warning("Codex session status check timed out")
            sessions_message = "Active sessions: 暂不可用"
            sessions_failed = True
        message = f"{message}\n{sessions_message}"
        return message, account_failed or sessions_failed

    def _codex_sessions_message(
        self,
        configuration: WeixinChubModeRuntimeConfig,
        current_session_id: str | None,
    ) -> str:
        visible, remaining = self._visible_codex_sessions(configuration)
        if not visible:
            return "Active sessions: None"
        return self._format_codex_sessions(visible, current_session_id, remaining)

    def _dispatch_codex_switch(
        self,
        *,
        message_id: str,
        correlation_id: str | None,
        route_fingerprint: str,
        source_ip: str,
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
            "用法：发送 codex switch 切换到下一个 Session，"
            "或发送 codex switch n 切换到指定编号。"
        )
        if invalid_usage:
            return self._finish_codex_switch(
                record,
                usage,
                source_ip=source_ip,
            )

        deadline = time.monotonic() + CODEX_STATUS_TIMEOUT_SECONDS
        account_results: queue.Queue[tuple[str, bool]] = queue.Queue(maxsize=1)

        def read_account() -> None:
            try:
                if self.codex_account_reader is None:
                    raise RuntimeError("Codex account reader is unavailable")
                quota, account_usage = self.codex_account_reader.read_account_status(
                    force=True
                )
                account_results.put(
                    (self._codex_usage_message(quota, account_usage), False)
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
            )
        except Exception:
            LOGGER.warning("Codex session switch lookup failed", exc_info=True)
            return self._finish_codex_switch(
                record,
                "Session 列表查询失败，请稍后重试。",
                source_ip=source_ip,
                failed=True,
            )

        if not visible:
            return self._finish_codex_switch(
                record,
                "当前没有可切换的 Session。",
                source_ip=source_ip,
            )

        if requested_index is not None:
            if requested_index > len(visible):
                message = "\n".join(
                    (
                        "编号无效。",
                        self._format_codex_sessions(
                            visible,
                            self._state.session_id,
                            remaining,
                        ),
                        "",
                        usage,
                    )
                )
                return self._finish_codex_switch(
                    record,
                    message,
                    source_ip=source_ip,
                )
            target_index = requested_index - 1
        else:
            current_index = next(
                (
                    index
                    for index, (session, _state) in enumerate(visible)
                    if session.id == self._state.session_id
                ),
                None,
            )
            if current_index is None:
                target_index = 0
            elif len(visible) == 1:
                return self._finish_codex_switch(
                    record,
                    "当前没有其他可切换的 Session。",
                    source_ip=source_ip,
                )
            else:
                target_index = (current_index + 1) % len(visible)

        target, _listed_state = visible[target_index]
        if target.id == self._state.session_id:
            title = build_task_summary(target.title or target.workspace_name)
            return self._finish_codex_switch(
                record,
                f"该 Session 已经是微信通道当前绑定项：\n{title} [Current]",
                source_ip=source_ip,
            )

        try:
            refreshed = self.codex_manager.get_session(target.id)
            if (
                refreshed.id != target.id
                or not self._session_matches_configuration(refreshed, configuration)
            ):
                raise ValueError("Session configuration changed")
            refreshed_state = self._codex_session_dispatch_state(refreshed)
            if refreshed_state == "Unavailable":
                raise ValueError("Session became unavailable")
        except Exception:
            LOGGER.info("Codex switch target is no longer available", exc_info=True)
            return self._finish_codex_switch(
                record,
                "目标 Session 当前不可用，绑定未改变；请重新发送 codex 查看列表。",
                source_ip=source_ip,
            )

        visible[target_index] = (refreshed, refreshed_state)
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
        message = f"{usage_message}\n{sessions_message}"

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
        self._log_dispatch(operation_id, "succeeded", source_ip)
        return WeixinChubModeDispatchResult(disposition="reply", message=message)

    def _read_visible_codex_sessions(
        self,
        configuration: WeixinChubModeRuntimeConfig,
        *,
        timeout_seconds: float = CODEX_STATUS_TIMEOUT_SECONDS,
    ) -> tuple[list[tuple[object, str]], int]:
        results: queue.Queue[
            tuple[tuple[list[tuple[object, str]], int] | None, Exception | None]
        ] = queue.Queue(maxsize=1)

        def read_sessions() -> None:
            try:
                results.put((self._visible_codex_sessions(configuration), None))
            except Exception as exc:
                results.put((None, exc))

        threading.Thread(target=read_sessions, daemon=True).start()
        try:
            value, error = results.get(timeout=timeout_seconds)
        except queue.Empty as exc:
            raise RuntimeError("Codex session lookup timed out") from exc
        if error is not None:
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
        return WeixinChubModeDispatchResult(disposition="reply", message=message)

    @staticmethod
    def _format_codex_sessions(
        visible: list[tuple[object, str]],
        current_session_id: str | None,
        remaining: int,
    ) -> str:
        lines = ["Active sessions:"]
        for index, (session, state) in enumerate(visible, start=1):
            title = build_task_summary(session.title or session.workspace_name)
            current = " [Current]" if session.id == current_session_id else ""
            lines.append(f"{index}. {title}{current} · {state}")
        if remaining:
            lines.append(f"另有 {remaining} 个")
        return "\n".join(lines)

    def _visible_codex_sessions(
        self,
        configuration: WeixinChubModeRuntimeConfig,
    ) -> tuple[list[tuple[object, str]], int]:
        eligible: list[tuple[object, str]] = []
        for session in self.codex_manager.list_sessions():
            if not self._session_matches_configuration(session, configuration):
                continue
            state = self._codex_session_dispatch_state(session)
            if state == "Unavailable":
                continue
            eligible.append((session, state))
        eligible.sort(key=lambda item: item[0].id)
        visible = eligible[:MAX_CODEX_STATUS_SESSIONS]
        return visible, len(eligible) - len(visible)

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
        deferred_restart = getattr(self.quick_interactions, "deferred_restart", None)
        if deferred_restart is not None and deferred_restart.pending():
            return "Unavailable"
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
            f"{weekly.remaining_percent}% left"
            if weekly is not None
            else "暂不可用"
        )
        today = datetime.now().astimezone().date()
        today_bucket = next(
            (bucket for bucket in usage.daily_usage if bucket.start_date == today),
            None,
        )
        if today_bucket is None and usage.daily_usage:
            latest_bucket = max(usage.daily_usage, key=lambda item: item.start_date)
            if 0 <= (today - latest_bucket.start_date).days <= 1:
                today_bucket = latest_bucket
        if usage.status == "available" and today_bucket is not None:
            token_text = WeixinChubModeManager._compact_token_count(
                today_bucket.tokens
            )
            token_text += f" ({today_bucket.start_date:%m-%d})"
        else:
            token_text = "暂不可用"
        return (
            f"Codex Usage: Weekly {weekly_text} · Daily tokens {token_text}"
        )

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

    @staticmethod
    def _submission_dispatch_message(
        submission: WeixinChubModeSubmissionResult,
        *,
        prompt: str,
        message_type: Literal["text", "voice"],
    ) -> str:
        message = submission.message
        if submission.task_summary:
            message = "\n\n".join(
                (
                    "任务已提交",
                    f"任务摘要：{submission.task_summary}",
                    "完成后将原路发送结果。",
                )
            )
        if message_type != "voice":
            return message

        prefix = f"{message}\n\n语音识别内容：\n"
        truncated_suffix = "\n（语音识别内容过长，已截断）"
        available = max(0, MAX_DISPATCH_MESSAGE_CHARS - len(prefix))
        if len(prompt) <= available:
            transcript = prompt
        else:
            body_length = max(0, available - len(truncated_suffix))
            transcript = f"{prompt[:body_length]}{truncated_suffix}"
        return f"{prefix}{transcript}"

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
            "in_progress": "任务提交失败：已有微信任务正在执行，请等待完成后重试。",
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
        with self._lock:
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
        created = self.codex_manager.create_session(
            configuration.workspace_id,
            configuration.permission_mode,
            configuration.model,
            configuration.reasoning_effort,
        )
        self.codex_manager.set_initial_quick_interaction_title(
            created.id,
            "微信 Chub",
        )
        next_state = self._state.model_copy(deep=True)
        next_state.session_id = created.id
        self._write_state(next_state)
        self._state = next_state
        return created.id, True

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
        finally:
            try:
                temporary.unlink()
            except FileNotFoundError:
                pass
