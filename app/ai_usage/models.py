from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


AiUsageStatus = Literal["available", "unavailable"]
AiUsageSource = Literal["account_login", "sub2api"]
AiTokenScope = Literal["account", "local_device"]


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class AiWeeklyUsage(_StrictModel):
    remaining_percent: int = Field(ge=0, le=100)
    used_usd: Decimal | None = Field(default=None, ge=0)
    remaining_usd: Decimal | None = Field(default=None, ge=0)
    limit_usd: Decimal | None = Field(default=None, gt=0)
    window_duration_minutes: Literal[10080] = 10080
    resets_at: datetime


class AiFiveHourUsage(_StrictModel):
    remaining_percent: int = Field(ge=0, le=100)
    window_duration_minutes: Literal[300] = 300
    resets_at: datetime


class AiTodayUsage(_StrictModel):
    date: date
    used_usd: Decimal | None = Field(default=None, ge=0)
    tokens: int | None = Field(default=None, ge=0)
    tokens_scope: AiTokenScope | None = None

    @model_validator(mode="after")
    def validate_token_scope(self) -> "AiTodayUsage":
        if (self.tokens is None) != (self.tokens_scope is None):
            raise ValueError("tokens and tokens_scope must be provided together")
        return self


class AiUsageDisplayPart(_StrictModel):
    kind: Literal["weekly", "limit", "reset", "five_hour", "today"]
    text: str


class AiUsageDisplay(_StrictModel):
    long: str | None = None
    short: str | None = None
    home: list[AiUsageDisplayPart] = Field(default_factory=list)


class AiUsageData(_StrictModel):
    runtime_id: str = Field(default="codex", pattern=r"^[a-z][a-z0-9-]{0,31}$")
    status: AiUsageStatus
    provider: str
    source: AiUsageSource | None = None
    timezone: str
    checked_at: datetime | None = None
    stale: bool = False
    message: str | None = None
    weekly: AiWeeklyUsage | None = None
    five_hour: AiFiveHourUsage | None = None
    today: AiTodayUsage | None = None
    display: AiUsageDisplay = Field(default_factory=AiUsageDisplay)
