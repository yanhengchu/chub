from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


SessionStatus = Literal["new", "running", "stopped", "error"]
TurnActivity = Literal["unknown", "working", "idle"]


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
    error: str | None = None
    ttyd_pid: int | None = None
    ttyd_port: int | None = None
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


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
    error: str | None
    created_at: datetime
    updated_at: datetime


class SessionListData(BaseModel):
    available: bool
    unavailable_reason: str | None = None
    dependencies: dict[str, bool]
    workspaces: list[WorkspaceInfo]
    sessions: list[SessionInfo]


class SessionCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    workspace_id: Literal["home", "workspace", "chub"]


class SessionAccessData(BaseModel):
    terminal_url: str
    expires_in: int
