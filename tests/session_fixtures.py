from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, model_validator

from app.codex.models import utc_now


class CodexSession(BaseModel):
    """Minimal Session double for callers that only need a public snapshot."""

    id: str
    workspace_id: str
    workspace_name: str
    cwd: Path
    title: str | None = None
    codex_session_id: str | None = None
    status: Literal["new", "running", "stopped", "error"] = "new"
    activity: Literal["unknown", "working", "idle"] = "unknown"
    activity_source: Literal["none", "quick"] = "none"
    permission_mode: Literal["ask", "auto-review", "read-only", "full-access"] = "ask"
    model: str | None = Field(default=None, max_length=128)
    reasoning_effort: str | None = Field(default=None, max_length=32)
    error: str | None = None
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)
    last_activity_at: datetime | None = None

    @property
    def native_session_id(self) -> str | None:
        return self.codex_session_id

    @native_session_id.setter
    def native_session_id(self, value: str | None) -> None:
        self.codex_session_id = value

    @model_validator(mode="after")
    def normalize_activity_source(self) -> "CodexSession":
        if self.activity != "working":
            self.activity_source = "none"
        elif self.activity_source == "none":
            self.activity = "unknown"
        return self
