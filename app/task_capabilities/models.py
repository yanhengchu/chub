from __future__ import annotations

from typing import Literal, TypeAlias

from pydantic import BaseModel, ConfigDict, Field, model_validator


TaskCapabilityId: TypeAlias = Literal[
    "chub.debug_chrome.page.interact",
    "chub.debug_chrome.page.read",
]
TASK_CAPABILITY_IDS = frozenset(
    {
        "chub.debug_chrome.page.interact",
        "chub.debug_chrome.page.read",
    }
)


class TaskCapabilityContext(BaseModel):
    """Private, short-lived grant context inherited by one Worker task."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    task_id: str = Field(min_length=1, max_length=128)
    token: str = Field(min_length=32, max_length=256)
    capability_ids: list[TaskCapabilityId] = Field(default_factory=list, max_length=8)

    @model_validator(mode="after")
    def validate_capability_ids(self) -> "TaskCapabilityContext":
        if self.capability_ids != sorted(set(self.capability_ids)):
            raise ValueError("capability IDs must be unique and sorted")
        return self
