from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Iterable, Literal, Protocol, TypeVar
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.ai_runtime import RUNTIME_ID_PATTERN
from app.codex.models import SessionMode


def utc_now() -> datetime:
    return datetime.now(UTC)


SessionStatus = Literal["new", "running", "stopped", "error"]
TurnActivity = Literal["unknown", "working", "idle"]
ActivitySource = Literal["none", "terminal", "quick"]
PermissionMode = Literal["ask", "auto-review", "read-only", "full-access"]


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


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class AiSession(_StrictModel):
    """Current logical Session record owned exclusively by Chub."""

    id: str = Field(min_length=36, max_length=36)
    # Runtime IDs are opaque to Chub. The current backend writes ``codex``;
    # persisted records must still carry an explicit owner.
    runtime_id: str = Field(pattern=RUNTIME_ID_PATTERN)
    session_mode: SessionMode
    native_session_id: str | None = Field(
        default=None,
        min_length=1,
        max_length=128,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$",
    )
    workspace_id: str = Field(min_length=1, max_length=64)
    workspace_name: str = Field(min_length=1, max_length=128)
    cwd: Path
    discovered: bool = False
    terminal_launch_id: str | None = Field(
        default=None, pattern=r"^[a-f0-9]{32}$"
    )
    quick_native_claim_task_id: str | None = Field(
        default=None, pattern=r"^qw-[0-9]{13}-[a-f0-9]{32}$"
    )
    quick_native_claim_execution_id: str | None = Field(
        default=None, pattern=r"^[a-f0-9]{32}$"
    )
    title: str | None = Field(default=None, max_length=48)
    status: SessionStatus = "new"
    activity: TurnActivity = "unknown"
    activity_source: ActivitySource = "none"
    permission_mode: PermissionMode = "ask"
    active_permission_mode: PermissionMode | None = None
    model: str | None = Field(default=None, max_length=128)
    reasoning_effort: str | None = Field(default=None, max_length=32)
    active_model: str | None = Field(default=None, max_length=128)
    active_reasoning_effort: str | None = Field(default=None, max_length=32)
    error: str | None = Field(default=None, max_length=300)
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)
    last_activity_at: datetime | None = None

    @field_validator("id")
    @classmethod
    def validate_id(cls, value: str) -> str:
        try:
            parsed = UUID(value)
        except (ValueError, AttributeError) as exc:
            raise ValueError("Session ID must be a UUID") from exc
        if str(parsed) != value:
            raise ValueError("Session ID must use canonical UUID form")
        return value

    @model_validator(mode="after")
    def normalize_activity_source(self) -> "AiSession":
        if self.activity != "working":
            self.activity_source = "none"
        elif self.activity_source == "none":
            self.activity = "unknown"
        return self


class AiSessionState(_StrictModel):
    version: Literal[2] = 2
    sessions: list[AiSession] = Field(default_factory=list)
