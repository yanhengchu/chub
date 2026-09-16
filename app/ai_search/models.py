from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


SearchStatus = Literal["submitting", "requested", "running", "succeeded", "failed"]


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SearchResultItem(_StrictModel):
    title: str = Field(min_length=1, max_length=160)
    url: str = Field(min_length=1, max_length=2048)
    description: str = Field(default="", max_length=1200)
    source: str = Field(default="网页", max_length=120)

    @field_validator("url")
    @classmethod
    def validate_url(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized.startswith(("https://", "http://")):
            raise ValueError("Result URL must use HTTP(S)")
        return normalized


class SearchResultPayload(_StrictModel):
    summary: str = Field(min_length=1, max_length=2000)
    results: list[SearchResultItem] = Field(default_factory=list, max_length=10)


class SearchRun(_StrictModel):
    id: str = Field(min_length=32, max_length=32)
    session_id: str = Field(min_length=1, max_length=64)
    operation_id: str | None = Field(default=None, min_length=1, max_length=64)
    task_id: str | None = Field(default=None, min_length=1, max_length=64)
    query: str = Field(min_length=2, max_length=2000)
    prompt: str = Field(min_length=1, max_length=6000)
    status: SearchStatus
    summary: str | None = Field(default=None, max_length=2000)
    results: list[SearchResultItem] = Field(default_factory=list, max_length=10)
    error: str | None = Field(default=None, max_length=1000)
    created_at: datetime
    updated_at: datetime


class AiSearchState(_StrictModel):
    version: Literal[2] = 2
    show_sessions: bool = False
    session_id: str | None = Field(default=None, min_length=1, max_length=64)
    pending_run: SearchRun | None = None
    runs: list[SearchRun] = Field(default_factory=list, max_length=8)


class AiSearchData(_StrictModel):
    current: SearchRun | None = None
    runs: list[SearchRun] = Field(default_factory=list, max_length=8)


class AiSearchSettings(_StrictModel):
    show_sessions: bool
