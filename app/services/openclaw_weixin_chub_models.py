from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.codex.models import (
    PermissionMode,
    QuickInteractionWeixinRoute,
)


WeixinChubModeCode = Literal[
    "ready",
    "disabled",
    "configuration_invalid",
    "codex_unavailable",
]
WeixinChubModeSubmissionCode = Literal[
    "submitted",
    "translation_queued",
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
    "weixin_text_mode_checked",
    # Reused by the current help route and compatible with legacy state files.
    "codex_help_checked",
    "chub_check_checked",
    "codex_model_checked",
    # Kept for state-file compatibility with the retired status route.
    "codex_status_checked",
    "codex_switch_checked",
    "codex_session_renamed",
    "codex_session_stopped",
    "codex_session_archived",
    "codex_session_deleted",
    "codex_session_created",
    "codex_retry_checked",
    "chub_slots_synced",
    "chub_restart_requested",
    "quick_worker_restart_requested",
    "clawbot_restart_requested",
    "system_upgrade_checked",
    "system_upgrade_requested",
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
MAX_STORED_STOP_OPERATIONS = 256
PENDING_RETRY_TTL_MINUTES = 10
MAX_WEIXIN_SESSION_SLOTS = 9
MAX_WEIXIN_TASK_SUMMARY_CHARS = 48
WEIXIN_RESTART_TASK_PREFIX = "weixin-restart-"


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
    continuation_kind: Literal["task", "translated_task", "confirmed_translated_task", "retry"] | None = None
    continuation_prompt: str | None = Field(
        default=None,
        max_length=MAX_PENDING_RETRY_PROMPT_CHARS,
    )
    continuation_original_message_id: str | None = Field(
        default=None,
        max_length=500,
    )
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


class WeixinChubModeStopOperation(_StrictModel):
    message_id: str = Field(min_length=1, max_length=500)
    operation_id: str = Field(min_length=1, max_length=128)
    source_ip: str = Field(min_length=1, max_length=128)
    delivery_route_fingerprint: str = Field(min_length=64, max_length=64)
    delivery_route: QuickInteractionWeixinRoute
    session_id: str = Field(min_length=1, max_length=128)
    session_slot: int = Field(ge=1, le=MAX_WEIXIN_SESSION_SLOTS)
    status: Literal["pending", "started", "succeeded", "failed"] = "pending"
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
    stop_operations: list[WeixinChubModeStopOperation] = Field(default_factory=list)


class WeixinChubModeStatus(_StrictModel):
    enabled: bool
    ready: bool
    code: WeixinChubModeCode
    message: str


class WeixinChubModeSubmissionResult(_StrictModel):
    accepted: Literal[True] = True
    duplicate: bool
    new_session: bool
    code: Literal["submitted", "translation_queued"] = "submitted"
    message: str
    task_summary: str | None = Field(
        default=None,
        max_length=MAX_WEIXIN_TASK_SUMMARY_CHARS,
    )
    session_slot: int | None = Field(default=None, ge=1, le=MAX_WEIXIN_SESSION_SLOTS)
    session_title: str | None = Field(default=None, max_length=48)


class WeixinChubModeDispatchResult(_StrictModel):
    protocol_version: Literal[3] = 3
    disposition: Literal["pass", "reply", "handled"]
    message: str | None = Field(default=None, max_length=3000)
