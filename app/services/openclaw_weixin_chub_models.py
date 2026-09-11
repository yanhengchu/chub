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
    "ai_runtime_disabled",
    "quick_worker_unavailable",
]
WeixinChubModeSubmissionCode = Literal[
    "submitted",
    "translation_queued",
    "in_progress",
    "mode_disabled",
    "configuration_invalid",
    "codex_unavailable",
    "ai_runtime_disabled",
    "quick_worker_unavailable",
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
    "codex_auth_checked",
    "codex_auth_switch_requested",
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
    "network_restart_requested",
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
    "ai_runtime_disabled",
    "quick_worker_unavailable",
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
    orchestration_id: str | None = Field(default=None, max_length=36)
    orchestration_checkpoint: str | None = Field(default=None, max_length=64)
    delivery_route_fingerprint: str | None = Field(default=None, max_length=64)
    status: Literal["reserved", "submitted", "rejected", "passed", "routed"]
    code: WeixinChubModeSubmissionCode
    message: str = Field(max_length=3_000)
    http_status: Literal[200, 409, 503] | None = None
    session_id: str | None = Field(default=None, max_length=128)
    task_id: str | None = Field(default=None, max_length=128)
    task_ref: str | None = Field(default=None, min_length=32, max_length=64)
    new_session: bool = False
    session_slot: int | None = Field(default=None, ge=1, le=MAX_WEIXIN_SESSION_SLOTS)
    session_title: str | None = Field(default=None, max_length=48)
    session_workspace_name: str | None = Field(default=None, max_length=48)
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


class WeixinTaskOrchestrationStage(_StrictModel):
    """One immutable logical stage in a persisted Weixin request."""

    kind: Literal["internal", "development", "module"]
    stage_id: Literal["weixin_refinement"]
    development_ref: Literal["weixin-orchestration-dev"] | None = None
    source_hash: str | None = Field(default=None, min_length=64, max_length=64)
    implementation_ref: str | None = Field(default=None, min_length=68, max_length=180)


class WeixinTaskOrchestrationRequest(_StrictModel):
    """Chub-owned durable record for one validated Weixin task body."""

    id: str = Field(min_length=36, max_length=36)
    message_id: str = Field(min_length=1, max_length=500)
    operation_id: str = Field(min_length=1, max_length=128)
    implementation: str = Field(default="direct", min_length=1, max_length=180)
    task_kind: Literal["direct", "text_processing"]
    original_prompt: str | None = Field(default=None, min_length=1, max_length=8_000)
    current_prompt: str | None = Field(default=None, min_length=1, max_length=8_000)
    stage_chain: list[WeixinTaskOrchestrationStage] = Field(default_factory=list, max_length=8)
    cursor: int = Field(default=0, ge=0, le=8)
    status: Literal["accepted", "waiting", "completed", "rejected", "unavailable", "unknown"] = "accepted"
    checkpoint: str = Field(max_length=64)
    # Opaque references are the only Session authority exposed to orchestration.
    candidate_session_refs: list[str] = Field(default_factory=list, max_length=MAX_WEIXIN_SESSION_SLOTS)
    creation_context: Literal["default_slot", "fixed_session"] = "default_slot"
    session_id: str | None = Field(default=None, max_length=128)
    session_ref: str | None = Field(default=None, min_length=32, max_length=64)
    creation_ref: str | None = Field(default=None, min_length=32, max_length=64)
    task_id: str | None = Field(default=None, max_length=128)
    task_ref: str | None = Field(default=None, min_length=32, max_length=64)
    translation_entry_id: str | None = Field(default=None, max_length=128)
    capability_calls: list["WeixinTaskCapabilityCall"] = Field(default_factory=list, max_length=16)
    created_at: datetime
    updated_at: datetime


class WeixinTaskCapabilityCall(_StrictModel):
    """A bounded, non-sensitive result from the Chub capability surface."""

    name: Literal["create_session", "read_session", "submit_task", "read_task", "await_task"]
    outcome: Literal["succeeded", "waiting", "rejected", "unavailable", "unknown"]
    checkpoint: str = Field(max_length=64)
    at: datetime


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
    orchestration_implementation: Literal[
        "internal", "weixin-orchestration-dev", "module"
    ] = "weixin-orchestration-dev"
    orchestration_development_source_hash: str | None = Field(
        default=None,
        min_length=64,
        max_length=64,
    )
    orchestration_module_ref: str | None = Field(
        default=None,
        min_length=68,
        max_length=180,
    )
    session_id: str | None = None
    pending_retry: WeixinChubModePendingRetry | None = None
    session_slots: list[WeixinChubModeSessionSlot] = Field(default_factory=list)
    submissions: list[WeixinChubModeSubmission] = Field(default_factory=list)
    orchestration_requests: list[WeixinTaskOrchestrationRequest] = Field(
        default_factory=list, max_length=MAX_STORED_SUBMISSIONS
    )
    restart_operations: list[WeixinChubModeRestartOperation] = Field(
        default_factory=list
    )
    stop_operations: list[WeixinChubModeStopOperation] = Field(default_factory=list)


class WeixinChubModeStatus(_StrictModel):
    enabled: bool
    ready: bool
    code: WeixinChubModeCode
    message: str


class WeixinTaskOrchestrationSettingsStatus(_StrictModel):
    implementation: Literal["weixin-orchestration-dev", "module"]
    development_available: bool
    development_source_hash: str | None = Field(default=None, min_length=64, max_length=64)
    module_ref: str | None = Field(default=None, min_length=68, max_length=180)
    module_available: bool = False


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
    session_workspace_name: str | None = Field(default=None, max_length=48)


class WeixinChubModeDispatchResult(_StrictModel):
    protocol_version: Literal[3] = 3
    disposition: Literal["pass", "reply", "handled"]
    message: str | None = Field(default=None, max_length=3000)
