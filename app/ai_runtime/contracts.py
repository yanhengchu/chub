from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Literal, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field


RuntimeCapability = Literal[
    "runtime_status",
    "background_turn",
    "task_cancel",
    "native_session_mapping",
    "interactive_terminal",
    "session_resume",
    "session_archive",
    "structured_events",
    "writer_probe",
    "model_catalog",
    "permission_profiles",
]
PermissionProfile = Literal["auto-review", "read-only", "full-access"]
RuntimePermissionMode = Literal[
    "ask",
    "auto-review",
    "read-only",
    "full-access",
]
RuntimeNativeAction = Literal["archive", "delete"]
RuntimeErrorKind = Literal[
    "invalid_request",
    "conflict",
    "unavailable",
]


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class RuntimeDescriptor(_StrictModel):
    runtime_id: str = Field(pattern=r"^[a-z][a-z0-9-]{0,31}$")
    capabilities: frozenset[RuntimeCapability]


class RuntimeStatus(_StrictModel):
    runtime_id: str = Field(pattern=r"^[a-z][a-z0-9-]{0,31}$")
    available: bool
    reason: str | None = Field(default=None, max_length=300)
    dependencies: dict[str, bool] = Field(default_factory=dict)


class RuntimeTurnRequest(_StrictModel):
    permission_profile: PermissionProfile
    native_session_id: str | None = Field(default=None, min_length=1, max_length=128)
    model: str | None = Field(default=None, min_length=1, max_length=128)
    reasoning_effort: str | None = Field(default=None, min_length=1, max_length=32)


class RuntimeNativeSession(_StrictModel):
    native_session_id: str = Field(min_length=1, max_length=128)
    cwd: Path
    title: str | None = Field(default=None, max_length=500)
    active_permission_mode: RuntimePermissionMode | None = None
    active_model: str | None = Field(default=None, min_length=1, max_length=128)
    active_reasoning_effort: str | None = Field(
        default=None,
        min_length=1,
        max_length=32,
    )
    created_at: datetime
    updated_at: datetime


class RuntimeSessionDiscoveryResult(_StrictModel):
    sessions: tuple[RuntimeNativeSession, ...]
    archive_states: dict[str, bool] | None = None


class RuntimeReasoningLevel(_StrictModel):
    id: str = Field(min_length=1, max_length=32)
    description: str = Field(max_length=300)


class RuntimeModelInfo(_StrictModel):
    id: str = Field(min_length=1, max_length=128)
    name: str = Field(min_length=1, max_length=128)
    description: str = Field(max_length=500)
    default_level: str | None = Field(default=None, min_length=1, max_length=32)
    levels: tuple[RuntimeReasoningLevel, ...]


class RuntimeModelCatalog(_StrictModel):
    models: tuple[RuntimeModelInfo, ...]
    default_model: str | None = Field(default=None, min_length=1, max_length=128)
    default_reasoning_effort: str | None = Field(
        default=None,
        min_length=1,
        max_length=32,
    )


class RuntimeTerminalRequest(_StrictModel):
    session_id: str = Field(min_length=1, max_length=128)
    cwd: Path
    permission_mode: RuntimePermissionMode
    native_session_id: str | None = Field(default=None, min_length=1, max_length=128)
    model: str | None = Field(default=None, min_length=1, max_length=128)
    reasoning_effort: str | None = Field(default=None, min_length=1, max_length=32)


class RuntimeProcessSpec(_StrictModel):
    argv: tuple[str, ...] = Field(min_length=1, max_length=64)


class RuntimeEventSummary(_StrictModel):
    native_session_id: str | None = Field(default=None, min_length=1, max_length=128)


class RuntimeTurnResult(_StrictModel):
    text: str = Field(max_length=1_000_000)
    truncated: bool = False


class RuntimeOperationError(RuntimeError):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        kind: RuntimeErrorKind = "unavailable",
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.kind = kind


@runtime_checkable
class AgentRuntimeAdapter(Protocol):
    @property
    def descriptor(self) -> RuntimeDescriptor: ...

    def status(self) -> RuntimeStatus: ...


@runtime_checkable
class RuntimeNativeSessionAdapter(Protocol):
    def validate_native_session_id(self, native_session_id: str) -> None: ...

    def discover_sessions(self) -> RuntimeSessionDiscoveryResult: ...

    def native_session_available(self, native_session_id: str) -> bool: ...


@runtime_checkable
class RuntimeWriterProbeAdapter(Protocol):
    def has_active_writer(self, native_session_id: str | None) -> bool: ...

    def wait_for_writer_release(
        self,
        native_session_id: str | None,
        *,
        timeout: float = 3.0,
    ) -> bool: ...


@runtime_checkable
class RuntimeModelCatalogAdapter(Protocol):
    def validate_model(
        self,
        model: str | None,
        reasoning_effort: str | None,
    ) -> None: ...

    def read_model_catalog(self) -> RuntimeModelCatalog: ...


@runtime_checkable
class RuntimeInteractiveTerminalAdapter(Protocol):
    def terminal_command(
        self,
        request: RuntimeTerminalRequest,
        port: int,
    ) -> RuntimeProcessSpec: ...


@runtime_checkable
class RuntimeSessionArchiveAdapter(Protocol):
    def run_native_action(
        self,
        action: RuntimeNativeAction,
        native_session_id: str,
    ) -> None: ...
