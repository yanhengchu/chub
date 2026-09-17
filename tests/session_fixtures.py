from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, model_validator

from app.ai_session.models import utc_now


class AiSessionFixture(BaseModel):
    """Minimal Session double for callers that only need a public snapshot."""

    id: str
    runtime_id: str = "codex"
    implementation_id: str = "codex-runtime-dev"
    workspace_id: str
    workspace_name: str
    cwd: Path
    title: str | None = None
    native_session_id: str | None = None
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

    @model_validator(mode="after")
    def normalize_activity_source(self) -> "AiSessionFixture":
        if self.activity != "working":
            self.activity_source = "none"
        elif self.activity_source == "none":
            self.activity = "unknown"
        return self
