from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


IDENTIFIER_PATTERN = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")
OPEN_ID_PATTERN = re.compile(r"^ou_[A-Za-z0-9]{8,128}$")
REQUEST_ID_PATTERN = re.compile(r"^[A-Za-z0-9._:-]{8,128}$")


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


MentionMode = Literal["none", "recipients", "all"]


class NotificationRequest(StrictModel):
    request_id: str = Field(min_length=8, max_length=128, pattern=r"^[A-Za-z0-9._:-]+$")
    target: str = Field(min_length=1, max_length=64, pattern=r"^[a-z][a-z0-9_-]*$")
    message: str = Field(min_length=1, max_length=8000)
    mention_mode: MentionMode = "none"
    recipients: list[str] = Field(default_factory=list, max_length=20)

    @field_validator("message")
    @classmethod
    def reject_blank_message(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("message must not be blank")
        return value

    @field_validator("recipients")
    @classmethod
    def validate_recipient_ids(cls, value: list[str]) -> list[str]:
        if any(not IDENTIFIER_PATTERN.fullmatch(item) for item in value):
            raise ValueError("invalid recipient identifier")
        if len(set(value)) != len(value):
            raise ValueError("duplicate recipient identifier")
        return value

    @model_validator(mode="after")
    def validate_mentions(self) -> "NotificationRequest":
        if self.mention_mode == "recipients" and not self.recipients:
            raise ValueError("recipients are required for recipient mentions")
        if self.mention_mode != "recipients" and self.recipients:
            raise ValueError("recipients require mention_mode=recipients")
        return self


class NotificationResult(StrictModel):
    request_id: str
    target: str
    provider: Literal["feishu"]
    status: Literal["accepted"]
    duplicate: bool = False


class NotificationTargetSummary(StrictModel):
    id: str
    display_name: str
    provider: Literal["feishu"]
    enabled: bool
    allow_mention_all: bool


class NotificationUserSummary(StrictModel):
    id: str
    display_name: str


class NotificationUserSearchResult(StrictModel):
    users: list[NotificationUserSummary]
    truncated: bool = False


class NotificationDeliveryRecord(StrictModel):
    fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")
    status: Literal["sending", "unknown", "accepted"]
    expires_at: int = Field(ge=0)


class NotificationDeliveryState(StrictModel):
    version: Literal[1]
    entries: dict[str, NotificationDeliveryRecord]

    @field_validator("entries")
    @classmethod
    def validate_entries(
        cls,
        value: dict[str, NotificationDeliveryRecord],
    ) -> dict[str, NotificationDeliveryRecord]:
        if len(value) > 1000:
            raise ValueError("too many notification delivery records")
        if any(REQUEST_ID_PATTERN.fullmatch(item) is None for item in value):
            raise ValueError("invalid notification delivery request ID")
        return value
