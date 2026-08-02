from __future__ import annotations

import re
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


IDENTIFIER_PATTERN = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")
OPEN_ID_PATTERN = re.compile(r"^ou_[A-Za-z0-9]{8,128}$")


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


MentionMode = Literal["none", "recipients", "all"]


class FeishuRecipient(StrictModel):
    open_id: str

    @field_validator("open_id")
    @classmethod
    def validate_open_id(cls, value: str) -> str:
        if not OPEN_ID_PATTERN.fullmatch(value):
            raise ValueError("invalid Feishu open_id")
        return value


class FeishuTarget(StrictModel):
    provider: Literal["feishu"] = "feishu"
    webhook_file: str = Field(min_length=1, max_length=128)
    enabled: bool = True
    allow_mention_all: bool = False
    recipients: dict[str, FeishuRecipient] = Field(default_factory=dict)

    @field_validator("webhook_file")
    @classmethod
    def validate_webhook_file(cls, value: str) -> str:
        path = Path(value)
        if path.is_absolute() or path.name != value or value in {".", ".."}:
            raise ValueError("webhook_file must be a plain file name")
        return value

    @field_validator("recipients")
    @classmethod
    def validate_recipient_ids(
        cls,
        value: dict[str, FeishuRecipient],
    ) -> dict[str, FeishuRecipient]:
        if len(value) > 100:
            raise ValueError("too many recipients")
        if any(not IDENTIFIER_PATTERN.fullmatch(item) for item in value):
            raise ValueError("invalid recipient identifier")
        return value


class NotificationRegistry(StrictModel):
    version: Literal[1]
    targets: dict[str, FeishuTarget]

    @field_validator("targets")
    @classmethod
    def validate_target_ids(
        cls,
        value: dict[str, FeishuTarget],
    ) -> dict[str, FeishuTarget]:
        if len(value) > 100:
            raise ValueError("too many notification targets")
        if any(not IDENTIFIER_PATTERN.fullmatch(item) for item in value):
            raise ValueError("invalid target identifier")
        return value


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
    provider: Literal["feishu"]
    enabled: bool
    allow_mention_all: bool
    recipients: list[str]
