from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path
from typing import Iterable, Literal, Protocol, TypeVar

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

SessionStatus = Literal["new", "running", "stopped", "error"]
TurnActivity = Literal["unknown", "working", "idle"]
ActivitySource = Literal["none", "terminal", "quick"]
SessionMode = Literal["terminal", "quick"]
PermissionMode = Literal["ask", "auto-review", "read-only", "full-access"]


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
    session_mode: SessionMode = "terminal"
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
    last_activity_at: datetime | None = None

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
