from __future__ import annotations

import hashlib
import json
import logging
import os
import threading
from datetime import datetime
from pathlib import Path
from typing import Callable, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

from app.codex.models import (
    PermissionMode,
    QuickInteractionTask,
    QuickInteractionWeixinRoute,
    utc_now,
)
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
]
MAX_STORED_SUBMISSIONS = 5_000
MAX_STATE_BYTES = 8 * 1024 * 1024
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
    status: Literal["reserved", "submitted", "rejected"]
    code: WeixinChubModeSubmissionCode
    message: str = Field(max_length=500)
    http_status: Literal[409, 503] = 409
    session_id: str | None = Field(default=None, max_length=128)
    task_id: str | None = Field(default=None, max_length=128)
    new_session: bool = False
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


class WeixinChubModeManager:
    """Own the single persistent Weixin Codex session and inbound deduplication."""

    def __init__(
        self,
        settings: Settings,
        codex_manager,
        quick_interactions,
        route_validator: Callable[[QuickInteractionWeixinRoute], str | None]
        | None = None,
        terminal_reclaimer: Callable[[str], object] | None = None,
    ) -> None:
        self.settings = settings
        self.codex_manager = codex_manager
        self.quick_interactions = quick_interactions
        self.route_validator = route_validator
        self.terminal_reclaimer = terminal_reclaimer
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
                submission.updated_at = utc_now()
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
                        "微信专用 Session 正在执行任务，请等待完成。",
                        session_id=session_id,
                    )
                    raise ApiError(
                        409,
                        "weixin_chub_mode_in_progress",
                        "微信专用 Session 正在执行任务，请等待完成。",
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
            return self._result(reservation, duplicate=False)

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
                    "微信专用 Session 状态未知且未能安全停止，请稍后重试。",
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
                "微信专用 Session 状态未知且未能安全停止，请稍后重试。",
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
            return self._result(submission, duplicate=True)
        raise ApiError(
            submission.http_status,
            f"weixin_chub_mode_{submission.code}",
            submission.message,
        )

    @staticmethod
    def _result(
        submission: WeixinChubModeSubmission,
        *,
        duplicate: bool,
    ) -> WeixinChubModeSubmissionResult:
        return WeixinChubModeSubmissionResult(
            duplicate=duplicate,
            new_session=submission.new_session,
            message=submission.message,
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
            "quick_interaction_in_progress": "微信专用 Session 正在执行任务，请等待完成。",
            "quick_interaction_terminal_working": "微信专用 Session 当前正在由终端使用。",
            "quick_interaction_terminal_active": "微信专用 Session 当前不能执行快速交互。",
            "quick_interaction_writer_active": (
                "微信专用 Session 当前仍由实时终端占用，请先停止终端。"
            ),
            "codex_writer_status_unavailable": (
                "暂时无法确认微信专用 Session 是否可写，请稍后重试。"
            ),
            "weixin_chub_mode_session_reclaim_failed": (
                "微信专用 Session 状态未知且未能安全停止，请稍后重试。"
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
            action="weixin_chub_mode_submit",
            status=status,
            target=target,
            source_ip=source_ip,
        )

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
