from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.ai_runtime import RUNTIME_ID_PATTERN
from app.ai_session.models import PermissionMode


QuickInteractionOrder = Literal["task", "timeline"]
QuickInteractionErrorSource = Literal["chub", "runtime"]
QuickInteractionStatus = Literal["requested", "running", "succeeded", "failed", "timed_out", "cancelled"]
QuickInteractionNotificationStatus = Literal["pending", "sending", "sent", "failed", "skipped"]
QuickInteractionDeferredRestartStatus = Literal["pending", "started", "succeeded", "start_failed", "sensitive_task_failed", "cleared"]
QuickInteractionNotificationRoute = Literal["default", "weixin-task"]
QuickInteractionKind = Literal["standard"]
TASK_SUMMARY_MAX_LENGTH = 27
QUICK_INTERACTION_RESULT_MAX_LENGTH = 100_000
PROMPT_OPTIMIZATION_WARNING = "提示词优化未完成，主任务将按原始输入继续执行。"


class QuickInteractionWeixinRoute(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    account_id: str = Field(min_length=1, max_length=200)
    recipient: str = Field(min_length=1, max_length=500)

    @field_validator("account_id", "recipient")
    @classmethod
    def normalize_identifier(cls, value: str) -> str:
        resolved = value.strip()
        if not resolved:
            raise ValueError("Identifier must not be blank")
        return resolved

    @field_validator("recipient")
    @classmethod
    def validate_recipient(cls, value: str) -> str:
        if not value.endswith("@im.wechat"):
            raise ValueError("Recipient must be a Weixin identifier")
        return value


class QuickInteractionDeferredRestartContext(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    operation_id: str = Field(min_length=1, max_length=160)
    coordinator_operation_id: str = Field(min_length=1, max_length=160)
    source_ip: str = Field(min_length=1, max_length=128)


class QuickInteractionOperationContext(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    operation_id: str = Field(min_length=1, max_length=160)
    source_ip: str = Field(min_length=1, max_length=128)
    logged_statuses: tuple[Literal["requested", "started", "succeeded", "failed", "cancelled"], ...] = Field(default=(), max_length=5)


class QuickInteractionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    prompt: str = Field(min_length=1, max_length=8000)

    @field_validator("prompt")
    @classmethod
    def validate_prompt(cls, value: str) -> str:
        resolved = value.strip()
        if not resolved:
            raise ValueError("Prompt must not be blank")
        return resolved


class PromptOptimizationVersions(BaseModel):
    """Validated bilingual prompt pair for one accepted task."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    chinese: str = Field(min_length=1, max_length=8_000)
    english: str = Field(min_length=1, max_length=8_000)

    @field_validator("chinese")
    @classmethod
    def validate_chinese(cls, value: str) -> str:
        resolved = value.strip()
        if not resolved or "\x00" in resolved:
            raise ValueError("Chinese optimized prompt must be non-empty text")
        if not any("\u3400" <= char <= "\u9fff" for char in resolved):
            raise ValueError("Chinese optimized prompt must contain Chinese text")
        return resolved

    @field_validator("english")
    @classmethod
    def validate_english(cls, value: str) -> str:
        resolved = value.strip()
        if not resolved or "\x00" in resolved:
            raise ValueError("English optimized prompt must be non-empty text")
        if not any(("a" <= char.lower() <= "z") for char in resolved):
            raise ValueError("English optimized prompt must contain English text")
        return resolved


class QuickInteractionTask(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    worker_task_id: str | None = Field(default=None, pattern=r"^qw-[0-9]{13}-[a-f0-9]{32}$")
    session_id: str
    implementation_id: str | None = Field(default=None, pattern=RUNTIME_ID_PATTERN)
    prompt: str | None = Field(default=None, max_length=20_000)
    execution_prompt: str | None = Field(default=None, max_length=20_000, exclude=True)
    prompt_optimization_result: PromptOptimizationVersions | None = None
    prompt_optimization_warning: str | None = Field(default=None, max_length=200)
    orchestration_pending: bool = False
    prompt_processing: bool = False
    summary: str | None = Field(default=None, max_length=48)
    kind: QuickInteractionKind = "standard"
    permission_mode: PermissionMode | None = None
    model: str | None = Field(default=None, max_length=128)
    reasoning_effort: str | None = Field(default=None, max_length=32)
    restart_sensitive: bool = False
    submission_verifying: bool = False
    status: QuickInteractionStatus
    result: str | None = Field(default=None, max_length=QUICK_INTERACTION_RESULT_MAX_LENGTH)
    error: str | None = Field(default=None, max_length=4000)
    error_source: QuickInteractionErrorSource | None = None
    notification_status: QuickInteractionNotificationStatus | None = None
    notification_route: QuickInteractionNotificationRoute = "default"
    notification_error: str | None = Field(default=None, max_length=1000)
    notification_updated_at: datetime | None = None
    prompt_optimization_notification_status: QuickInteractionNotificationStatus | None = Field(
        default=None, exclude=True
    )
    prompt_optimization_notification_error: str | None = Field(
        default=None, max_length=1000, exclude=True
    )
    prompt_optimization_notification_updated_at: datetime | None = Field(
        default=None, exclude=True
    )
    deferred_restart_status: QuickInteractionDeferredRestartStatus | None = None
    deferred_restart_error: str | None = Field(default=None, max_length=500)
    deferred_restart_updated_at: datetime | None = None
    deferred_restart_notification_status: QuickInteractionNotificationStatus | None = None
    deferred_restart_notification_error: str | None = Field(default=None, max_length=1000)
    deferred_restart_notification_updated_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class QuickInteractionData(BaseModel):
    task: QuickInteractionTask


class QuickInteractionListData(BaseModel):
    tasks: list[QuickInteractionTask]
    total: int
    has_more: bool
