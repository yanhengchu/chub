from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


SessionStatus = Literal["new", "running", "stopped", "error"]
TurnActivity = Literal["unknown", "working", "idle"]
ActivitySource = Literal["none", "terminal", "quick"]
PermissionMode = Literal["ask", "auto-review", "read-only", "full-access"]
QuickInteractionOrder = Literal["task", "timeline"]


def utc_now() -> datetime:
    return datetime.now(UTC)


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
    error: str | None = None
    ttyd_pid: int | None = None
    ttyd_port: int | None = None
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)

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
    workspace_id: str
    workspace_name: str
    cwd: str
    title: str | None
    codex_session_id: str | None
    status: SessionStatus
    activity: TurnActivity
    activity_source: ActivitySource = "none"
    permission_mode: PermissionMode
    active_permission_mode: PermissionMode | None
    permission_pending: bool
    error: str | None
    created_at: datetime
    updated_at: datetime
    quick_interaction_running: bool = False
    quick_interaction_updated_at: datetime | None = None
    llm_interaction_running: bool = False
    llm_interaction_updated_at: datetime | None = None


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


class SessionCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    workspace_id: Literal["home", "workspace", "chub"]
    permission_mode: PermissionMode = "full-access"


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
QuickInteractionEngine = Literal["codex_cli", "bedrock_api"]
QuickInteractionNotificationStatus = Literal[
    "pending",
    "sending",
    "sent",
    "failed",
    "skipped",
]


class QuickInteractionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    prompt: str = Field(min_length=1, max_length=8000)
    engine: QuickInteractionEngine = "codex_cli"
    confirm_stop_unknown_terminal: bool = False

    @field_validator("prompt")
    @classmethod
    def validate_prompt(cls, value: str) -> str:
        resolved = value.strip()
        if not resolved:
            raise ValueError("Prompt must not be blank")
        return resolved

    @model_validator(mode="after")
    def validate_engine_prompt_length(self) -> "QuickInteractionRequest":
        if self.engine == "bedrock_api" and len(self.prompt) > 4000:
            raise ValueError("Amazon Bedrock API prompt exceeds 4000 characters")
        return self


class QuickInteractionPinRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    pinned: bool


class QuickInteractionTask(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    session_id: str
    engine: QuickInteractionEngine = "codex_cli"
    provider: str | None = Field(default=None, max_length=200)
    model: str | None = Field(default=None, max_length=500)
    prompt: str | None = Field(default=None, max_length=8000)
    status: QuickInteractionStatus
    result: str | None = Field(default=None, max_length=100_000)
    error: str | None = Field(default=None, max_length=2000)
    notification_status: QuickInteractionNotificationStatus | None = None
    notification_error: str | None = Field(default=None, max_length=1000)
    notification_updated_at: datetime | None = None
    pinned_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class QuickInteractionData(BaseModel):
    task: QuickInteractionTask


class QuickInteractionListData(BaseModel):
    tasks: list[QuickInteractionTask]
    total: int
    has_more: bool
