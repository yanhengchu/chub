from __future__ import annotations

import time
from dataclasses import dataclass
from enum import Enum
from typing import Any, Callable

from pydantic import BaseModel, ConfigDict, Field

from app.core.config import Settings


ALL_PLATFORMS = frozenset({"macos", "ubuntu", "windows", "unknown"})


class StrictTaskModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class EmptyTaskParams(StrictTaskModel):
    pass


class TaskStatus(str, Enum):
    SUCCESS = "success"
    FAILED = "failed"
    TIMEOUT = "timeout"


class TaskPublicInfo(StrictTaskModel):
    name: str
    title: str
    description: str
    platforms: list[str]
    timeout_seconds: int
    params_schema: dict[str, Any]


class TaskListData(StrictTaskModel):
    tasks: list[TaskPublicInfo]


class TaskRunRequest(StrictTaskModel):
    task: str = Field(min_length=1)
    params: dict[str, Any] = Field(default_factory=dict)


class TaskRunResult(StrictTaskModel):
    task: str
    status: TaskStatus
    message: str
    duration_ms: int
    result: dict[str, Any] | None = None


@dataclass(frozen=True)
class TaskHandlerResult:
    message: str
    result: dict[str, Any] | None = None


@dataclass(frozen=True)
class TaskContext:
    settings: Settings
    detected_platform: str
    deadline: float

    def remaining_timeout(self) -> float:
        return max(0.0, self.deadline - time.monotonic())


TaskHandler = Callable[[BaseModel, TaskContext], TaskHandlerResult]


@dataclass(frozen=True)
class TaskDefinition:
    name: str
    title: str
    description: str
    platforms: frozenset[str]
    timeout_seconds: int
    params_model: type[BaseModel]
    handler: TaskHandler

    def public_info(self) -> TaskPublicInfo:
        return TaskPublicInfo(
            name=self.name,
            title=self.title,
            description=self.description,
            platforms=sorted(self.platforms),
            timeout_seconds=self.timeout_seconds,
            params_schema=self.params_model.model_json_schema(),
        )


class TaskFailed(Exception):
    def __init__(
        self,
        message: str,
        *,
        result: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.result = result


class TaskTimedOut(Exception):
    pass
