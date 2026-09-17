from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Literal
from unicodedata import category

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.ai_runtime import RUNTIME_ID_PATTERN
from app.ai_session.models import PermissionMode


WORKSPACE_ID_PATTERN = r"^[a-z][a-z0-9-]{0,63}$"
SessionUsageOwner = Literal["none", "quick_worker", "external", "unknown"]
SessionUsagePhase = Literal["idle", "running", "waiting_result", "unknown"]


class WorkspaceInfo(BaseModel):
    id: str
    name: str
    path: str
    available: bool


class SessionUsage(BaseModel):
    native_session_present: bool = False
    owner: SessionUsageOwner = "none"
    phase: SessionUsagePhase = "idle"


class SessionInfo(BaseModel):
    id: str
    runtime_id: str = Field(pattern=RUNTIME_ID_PATTERN)
    workspace_id: str
    workspace_name: str
    cwd: str
    title: str | None
    can_archive: bool = False
    status: Literal["new", "running", "stopped", "error"]
    activity: Literal["unknown", "working", "idle"]
    activity_source: Literal["none", "quick"] = "none"
    permission_mode: PermissionMode
    model: str | None = None
    reasoning_effort: str | None = None
    error: str | None
    created_at: datetime
    updated_at: datetime
    last_activity_at: datetime | None = None
    quick_interaction_running: bool = False
    quick_interaction_updated_at: datetime | None = None
    runtime_submission_available: bool = True
    runtime_submission_reason: str | None = Field(default=None, max_length=300)
    weixin_session_slot: int | None = Field(default=None, ge=1, le=9)
    usage: SessionUsage = Field(default_factory=SessionUsage)


class SessionCreationAvailability(BaseModel):
    available: bool
    reason: str | None = Field(default=None, max_length=300)


class SessionRuntimeGroup(BaseModel):
    runtime_id: str = Field(pattern=RUNTIME_ID_PATTERN)
    name: str = Field(min_length=1, max_length=128)


class NativeSessionInfo(BaseModel):
    runtime_id: str = Field(pattern=RUNTIME_ID_PATTERN)
    cwd: str
    title: str | None = Field(default=None, max_length=500)
    created_at: datetime
    updated_at: datetime
    writer_lock_state: Literal["held", "free", "unknown"] = "unknown"
    chub_writer_lock_state: Literal["held", "free", "unknown"] = "unknown"
    chub_session_id: str | None = None
    native_action_ref: str | None = Field(default=None, min_length=16, max_length=128)


class SessionListData(BaseModel):
    available: bool
    unavailable_reason: str | None = None
    runtime_registered: bool
    default_runtime_id: str | None = Field(default=None, pattern=RUNTIME_ID_PATTERN)
    quick_creation: SessionCreationAvailability
    dependencies: dict[str, bool]
    workspaces: list[WorkspaceInfo]
    sessions: list[SessionInfo]
    native_sessions: list[NativeSessionInfo] = Field(default_factory=list)
    runtime_groups: list[SessionRuntimeGroup] = Field(default_factory=list)


class RuntimeManagementItem(BaseModel):
    runtime_id: str = Field(pattern=RUNTIME_ID_PATTERN)
    name: str = Field(min_length=1, max_length=128)
    enabled: bool
    healthy: bool
    reason: str | None = Field(default=None, max_length=300)


class RuntimeManagementData(BaseModel):
    runtimes: list[RuntimeManagementItem]
    basic_mode: bool


class RuntimeImplementationItem(BaseModel):
    implementation_id: str = Field(pattern=RUNTIME_ID_PATTERN)
    name: str = Field(min_length=1, max_length=128)
    version: str = Field(min_length=1, max_length=64)
    description: str = Field(min_length=1, max_length=300)
    imported: bool
    enabled: bool
    healthy: bool
    is_default: bool
    compatibility_id: str | None = Field(default=None, max_length=64)
    removable: bool
    reason: str | None = Field(default=None, max_length=300)


class RuntimeImplementationData(BaseModel):
    runtime_id: str = Field(pattern=RUNTIME_ID_PATTERN)
    default_implementation_id: str | None = Field(default=None, pattern=RUNTIME_ID_PATTERN)
    implementations: list[RuntimeImplementationItem]


class RuntimeImplementationEnabledUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool


class RuntimeDefaultImplementationUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    implementation_id: str = Field(pattern=RUNTIME_ID_PATTERN)


class RuntimeEnablementUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool


class SessionCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    workspace_id: str = Field(pattern=WORKSPACE_ID_PATTERN)
    permission_mode: PermissionMode | None = None
    model: str | None = Field(default=None, max_length=128)
    reasoning_effort: str | None = Field(default=None, max_length=32)


class SessionConfigurationUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    permission_mode: PermissionMode
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
        if any(category(character) == "Cc" and not character.isspace() for character in value):
            raise ValueError("Title must not contain control characters")
        return " ".join(value.split())
