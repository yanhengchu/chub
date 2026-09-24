from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import Field, field_validator, model_validator

from app.notifications.models import IDENTIFIER_PATTERN, OPEN_ID_PATTERN, StrictModel


class FeishuRecipient(StrictModel):
    display_name: str = Field(min_length=1, max_length=128)
    open_id: str

    @field_validator("display_name")
    @classmethod
    def reject_blank_display_name(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("display_name must not be blank")
        return value

    @field_validator("open_id")
    @classmethod
    def validate_open_id(cls, value: str) -> str:
        if not OPEN_ID_PATTERN.fullmatch(value):
            raise ValueError("invalid Feishu open_id")
        return value


class FeishuTarget(StrictModel):
    display_names: list[str] = Field(min_length=1, max_length=8)
    provider: Literal["feishu"] = "feishu"
    webhook_file: str = Field(min_length=1, max_length=128)
    enabled: bool = True
    allow_mention_all: bool = False

    @model_validator(mode="before")
    @classmethod
    def migrate_display_name(cls, value: object) -> object:
        if not isinstance(value, dict) or "display_name" not in value:
            return value
        if "display_names" in value:
            raise ValueError("display_name and display_names cannot both be set")
        migrated = dict(value)
        migrated["display_names"] = [migrated.pop("display_name")]
        return migrated

    @field_validator("display_names")
    @classmethod
    def validate_display_names(cls, value: list[str]) -> list[str]:
        normalized: list[str] = []
        seen: set[str] = set()
        for item in value:
            name = item.strip()
            if not name:
                raise ValueError("display_names must not contain blank names")
            if len(name) > 128:
                raise ValueError("display name is too long")
            key = name.casefold()
            if key in seen:
                raise ValueError("display_names must not contain duplicates")
            seen.add(key)
            normalized.append(name)
        return normalized

    @property
    def display_name(self) -> str:
        """Compatibility view for callers that only need the primary label."""
        return self.display_names[0]

    @field_validator("webhook_file")
    @classmethod
    def validate_webhook_file(cls, value: str) -> str:
        path = Path(value)
        if path.is_absolute() or path.name != value or value in {".", ".."}:
            raise ValueError("webhook_file must be a plain file name")
        return value


class NotificationRegistry(StrictModel):
    version: Literal[2]
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

    @model_validator(mode="after")
    def validate_unique_display_names(self) -> "NotificationRegistry":
        owners: dict[str, str] = {}
        for target_id, target in self.targets.items():
            for display_name in target.display_names:
                key = display_name.casefold()
                owner = owners.get(key)
                if owner is not None and owner != target_id:
                    raise ValueError("display_names must be unique across targets")
                owners[key] = target_id
        for target_id in self.targets:
            key = target_id.casefold()
            owner = owners.get(key)
            if owner is not None and owner != target_id:
                raise ValueError("display_names must not collide with target IDs")
        return self


class NotificationUsers(StrictModel):
    version: Literal[1]
    users: dict[str, FeishuRecipient]

    @field_validator("users")
    @classmethod
    def validate_user_ids(
        cls,
        value: dict[str, FeishuRecipient],
    ) -> dict[str, FeishuRecipient]:
        if len(value) > 1000:
            raise ValueError("too many users")
        if any(not IDENTIFIER_PATTERN.fullmatch(item) for item in value):
            raise ValueError("invalid user identifier")
        return value

    @model_validator(mode="after")
    def validate_unique_open_ids(self) -> "NotificationUsers":
        open_ids: set[str] = set()
        for user in self.users.values():
            if user.open_id in open_ids:
                raise ValueError("duplicate Feishu open_id")
            open_ids.add(user.open_id)
        return self
