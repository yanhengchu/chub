from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path
from typing import Iterable, Literal, Protocol, TypeVar
from unicodedata import category

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.ai_runtime import RUNTIME_ID_PATTERN

SessionStatus = Literal["new", "running", "stopped", "error"]
TurnActivity = Literal["unknown", "working", "idle"]
ActivitySource = Literal["none", "terminal", "quick"]
PermissionMode = Literal["ask", "auto-review", "read-only", "full-access"]
QuickInteractionOrder = Literal["task", "timeline"]
TASK_SUMMARY_MAX_LENGTH = 27


def utc_now() -> datetime:
    return datetime.now(UTC)


class SessionCreationRecord(Protocol):
    id: str
    created_at: datetime


SessionCreationRecordT = TypeVar(
    "SessionCreationRecordT",
    bound=SessionCreationRecord,
)


def sessions_newest_first(
    sessions: Iterable[SessionCreationRecordT],
) -> list[SessionCreationRecordT]:
    return sorted(
        sessions,
        key=lambda session: (session.created_at, session.id),
        reverse=True,
    )


class CodexSession(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    workspace_id: str
    workspace_name: str
    cwd: Path
    title: str | None = None
    codex_session_id: str | None = None
    status: SessionStatus = "new"
    activity: TurnActivity = "unknown"
    activity_source: ActivitySource = "none"
    permission_mode: PermissionMode = "ask"
    active_permission_mode: PermissionMode | None = None
    model: str | None = Field(default=None, max_length=128)
    reasoning_effort: str | None = Field(default=None, max_length=32)
    active_model: str | None = Field(default=None, max_length=128)
    active_reasoning_effort: str | None = Field(default=None, max_length=32)
    error: str | None = None
    ttyd_pid: int | None = None
    ttyd_port: int | None = None
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)

    @property
    def native_session_id(self) -> str | None:
        """Compatibility view for test fixtures and one-time upgrade records."""
        return self.codex_session_id

    @native_session_id.setter
    def native_session_id(self, value: str | None) -> None:
        self.codex_session_id = value

    @field_validator("permission_mode", "active_permission_mode", mode="before")
    @classmethod
    def migrate_legacy_permission_mode(cls, value: object) -> object:
        return {
            "inherit": "ask",
            "workspace-write": "ask",
        }.get(value, value)

    @model_validator(mode="after")
    def normalize_activity_source(self) -> CodexSession:
        if self.activity != "working":
            self.activity_source = "none"
        elif self.activity_source == "none":
            if self.status == "running":
                self.activity_source = "terminal"
            else:
                self.activity = "unknown"
        return self


class WorkspaceInfo(BaseModel):
    id: str
    name: str
    path: str
    available: bool


class SessionInfo(BaseModel):
    id: str
    runtime_id: str = Field(pattern=RUNTIME_ID_PATTERN)
    workspace_id: str
    workspace_name: str
    cwd: str
    title: str | None
    can_archive: bool = False
    status: SessionStatus
    activity: TurnActivity
    activity_source: ActivitySource = "none"
    permission_mode: PermissionMode
    active_permission_mode: PermissionMode | None
    permission_pending: bool
    model: str | None = None
    reasoning_effort: str | None = None
    active_model: str | None = None
    active_reasoning_effort: str | None = None
    error: str | None
    created_at: datetime
    updated_at: datetime
    quick_interaction_running: bool = False
    quick_interaction_updated_at: datetime | None = None
    terminal_access_allowed: bool = True
    weixin_session_slot: int | None = Field(default=None, ge=1, le=9)


class SessionListData(BaseModel):
    available: bool
    unavailable_reason: str | None = None
    dependencies: dict[str, bool]
    workspaces: list[WorkspaceInfo]
    sessions: list[SessionInfo]


QuotaStatus = Literal["available", "unavailable"]


class CodexQuotaWindow(BaseModel):
    remaining_percent: int = Field(ge=0, le=100)
    window_duration_minutes: int = Field(ge=1)
    resets_at: datetime


class CodexQuotaData(BaseModel):
    status: QuotaStatus
    message: str | None = None
    checked_at: datetime = Field(default_factory=utc_now)
    windows: list[CodexQuotaWindow] = Field(default_factory=list)


class CodexDailyTokenUsage(BaseModel):
    start_date: date
    tokens: int = Field(ge=0)


class CodexTokenUsageData(BaseModel):
    status: QuotaStatus
    message: str | None = None
    checked_at: datetime = Field(default_factory=utc_now)
    daily_usage: list[CodexDailyTokenUsage] = Field(default_factory=list)


class SessionCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    workspace_id: Literal["home", "workspace", "chub"]
    permission_mode: PermissionMode = "full-access"
    model: str | None = Field(default=None, max_length=128)
    reasoning_effort: str | None = Field(default=None, max_length=32)


class SessionRenameRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    title: str = Field(min_length=1, max_length=48)

    @field_validator("title", mode="before")
    @classmethod
    def normalize_title(cls, value: object) -> object:
        if not isinstance(value, str):
            return value
        if any(
            category(character) == "Cc" and not character.isspace()
            for character in value
        ):
            raise ValueError("Title must not contain control characters")
        return " ".join(value.split())


class CodexReasoningLevel(BaseModel):
    id: str
    description: str


class CodexModelInfo(BaseModel):
    id: str
    name: str
    description: str
    default_level: str | None
    levels: list[CodexReasoningLevel]


class CodexModelCatalogData(BaseModel):
    models: list[CodexModelInfo]
    default_model: str | None = None
    default_reasoning_effort: str | None = None


class SessionAccessData(BaseModel):
    terminal_url: str
    expires_in: int


QuickInteractionStatus = Literal[
    "requested",
    "running",
    "succeeded",
    "failed",
    "timed_out",
    "cancelled",
    "needs_terminal",
]
QuickInteractionNotificationStatus = Literal[
    "pending",
    "sending",
    "sent",
    "failed",
    "skipped",
]
QuickInteractionDeferredRestartStatus = Literal[
    "pending",
    "started",
    "succeeded",
    "start_failed",
    "sensitive_task_failed",
    "cleared",
]
QuickInteractionNotificationRoute = Literal["default", "weixin-task"]
QuickInteractionKind = Literal["standard", "translation"]


class QuickInteractionWeixinRoute(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    account_id: str = Field(min_length=1, max_length=200)
    recipient: str = Field(min_length=1, max_length=500)

    @field_validator("account_id", "recipient")
    @classmethod
    def normalize_identifier(cls, value: str) -> str:
        resolved = value.strip()
        if not resolved:
            raise ValueError("Identifier must not be blank")
        return resolved

    @field_validator("recipient")
    @classmethod
    def validate_recipient(cls, value: str) -> str:
        if not value.endswith("@im.wechat"):
            raise ValueError("Recipient must be a Weixin identifier")
        return value


class QuickInteractionDeferredRestartContext(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    operation_id: str = Field(min_length=1, max_length=160)
    coordinator_operation_id: str = Field(min_length=1, max_length=160)
    source_ip: str = Field(min_length=1, max_length=128)


class QuickInteractionOperationContext(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    operation_id: str = Field(min_length=1, max_length=160)
    source_ip: str = Field(min_length=1, max_length=128)
    logged_statuses: tuple[
        Literal["requested", "started", "succeeded", "failed"], ...
    ] = Field(default=(), max_length=4)


class QuickInteractionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    prompt: str = Field(min_length=1, max_length=8000)
    confirm_stop_unknown_terminal: bool = False

    @field_validator("prompt")
    @classmethod
    def validate_prompt(cls, value: str) -> str:
        resolved = value.strip()
        if not resolved:
            raise ValueError("Prompt must not be blank")
        return resolved

class QuickInteractionTask(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    worker_task_id: str | None = Field(
        default=None,
        pattern=r"^qw-[0-9]{13}-[a-f0-9]{32}$",
    )
    session_id: str
    # Internal translation tasks add a fixed bounded instruction around an
    # otherwise API-limited 8000-character source.
    prompt: str | None = Field(default=None, max_length=20_000)
    # Keep the legacy bound so persisted tasks created before the 13-character
    # summary limit remain readable after an upgrade.
    summary: str | None = Field(default=None, max_length=48)
    weixin_session_slot: int | None = Field(default=None, ge=1, le=9)
    weixin_session_title: str | None = Field(default=None, max_length=48)
    weixin_request_slot: int | None = Field(default=None, ge=1, le=9)
    weixin_request_generation: str | None = Field(
        default=None,
        min_length=32,
        max_length=32,
    )
    weixin_request_run_id: str | None = Field(
        default=None,
        min_length=32,
        max_length=32,
    )
    weixin_request_title: str | None = Field(default=None, max_length=48)
    kind: QuickInteractionKind = "standard"
    translation_original: str | None = Field(default=None, max_length=8000)
    restart_sensitive: bool = False
    status: QuickInteractionStatus
    result: str | None = Field(default=None, max_length=100_000)
    error: str | None = Field(default=None, max_length=2000)
    notification_status: QuickInteractionNotificationStatus | None = None
    notification_route: QuickInteractionNotificationRoute = "default"
    notification_error: str | None = Field(default=None, max_length=1000)
    notification_updated_at: datetime | None = None
    deferred_restart_status: QuickInteractionDeferredRestartStatus | None = None
    deferred_restart_error: str | None = Field(default=None, max_length=500)
    deferred_restart_updated_at: datetime | None = None
    deferred_restart_notification_status: QuickInteractionNotificationStatus | None = None
    deferred_restart_notification_error: str | None = Field(
        default=None,
        max_length=1000,
    )
    deferred_restart_notification_updated_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class QuickInteractionData(BaseModel):
    task: QuickInteractionTask


class QuickInteractionListData(BaseModel):
    tasks: list[QuickInteractionTask]
    total: int
    has_more: bool
