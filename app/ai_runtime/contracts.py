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
    "activity_events",
    "writer_probe",
    "model_catalog",
    "permission_profiles",
    "usage_snapshot",
    "runtime_settings",
]
RuntimeCapabilityState = Literal["supported", "unsupported", "unavailable"]
RUNTIME_ID_PATTERN = r"^[a-z][a-z0-9-]{0,31}$"
RUNTIME_CAPABILITIES: frozenset[RuntimeCapability] = frozenset(
    {
        "runtime_status",
        "background_turn",
        "task_cancel",
        "native_session_mapping",
        "interactive_terminal",
        "session_resume",
        "session_archive",
        "structured_events",
        "activity_events",
        "writer_probe",
        "model_catalog",
        "permission_profiles",
        "usage_snapshot",
        "runtime_settings",
    }
)
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
    runtime_id: str = Field(pattern=RUNTIME_ID_PATTERN)
    capabilities: frozenset[RuntimeCapability]


class RuntimeCapabilityMatrix(_StrictModel):
    """The fixed, read-only capability view used during Runtime admission."""

    runtime_id: str = Field(pattern=RUNTIME_ID_PATTERN)
    available: bool
    reason: str | None = Field(default=None, max_length=300)
    capabilities: dict[RuntimeCapability, RuntimeCapabilityState]

    @classmethod
    def from_descriptor(
        cls,
        descriptor: RuntimeDescriptor,
        *,
        available: bool,
        reason: str | None = None,
    ) -> "RuntimeCapabilityMatrix":
        state: RuntimeCapabilityState = "supported" if available else "unavailable"
        return cls(
            runtime_id=descriptor.runtime_id,
            available=available,
            reason=reason,
            capabilities={
                capability: state
                if capability in descriptor.capabilities
                else "unsupported"
                for capability in sorted(RUNTIME_CAPABILITIES)
            },
        )


class RuntimeStatus(_StrictModel):
    runtime_id: str = Field(pattern=RUNTIME_ID_PATTERN)
    available: bool
    reason: str | None = Field(default=None, max_length=300)
    dependencies: dict[str, bool] = Field(default_factory=dict)


class RuntimeTurnRequest(_StrictModel):
    permission_profile: PermissionProfile
    native_session_id: str | None = Field(default=None, min_length=1, max_length=128)
    model: str | None = Field(default=None, min_length=1, max_length=128)
    reasoning_effort: str | None = Field(default=None, min_length=1, max_length=32)


class RuntimeNativeSession(_StrictModel):
    runtime_id: str = Field(pattern=RUNTIME_ID_PATTERN)
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
    launch_id: str | None = Field(default=None, pattern=r"^[a-f0-9]{32}$")
    cwd: Path
    permission_mode: RuntimePermissionMode
    native_session_id: str | None = Field(default=None, min_length=1, max_length=128)
    model: str | None = Field(default=None, min_length=1, max_length=128)
    reasoning_effort: str | None = Field(default=None, min_length=1, max_length=32)


class RuntimeProcessSpec(_StrictModel):
    argv: tuple[str, ...] = Field(min_length=1, max_length=64)


class RuntimeEventSummary(_StrictModel):
    native_session_id: str | None = Field(default=None, min_length=1, max_length=128)


class RuntimeActivityEvent(_StrictModel):
    """A bounded activity update emitted by a Runtime-owned hook/event source."""

    native_session_id: str | None = Field(default=None, min_length=1, max_length=128)
    launch_id: str | None = Field(default=None, pattern=r"^[a-f0-9]{32}$")
    activity: Literal["working", "idle"] | None = None
    activity_source: Literal["none", "terminal", "quick"] = "none"


class RuntimeTurnResult(_StrictModel):
    text: str = Field(max_length=1_000_000)
    truncated: bool = False


RuntimeSettingInputType = Literal["text", "number"]
RuntimeSettingValue = str | int | None


class RuntimeSettingsField(_StrictModel):
    id: str = Field(pattern=r"^[a-z][a-z0-9-]{0,63}$")
    label: str = Field(min_length=1, max_length=100)
    description: str = Field(min_length=1, max_length=300)
    input_type: RuntimeSettingInputType
    value: RuntimeSettingValue = None
    placeholder: str | None = Field(default=None, max_length=200)


class RuntimeSettingsSection(_StrictModel):
    id: str = Field(pattern=r"^[a-z][a-z0-9-]{0,63}$")
    title: str = Field(min_length=1, max_length=100)
    description: str | None = Field(default=None, max_length=300)
    fields: tuple[RuntimeSettingsField, ...]


class RuntimeSettingsData(_StrictModel):
    runtime_id: str = Field(pattern=RUNTIME_ID_PATTERN)
    sections: tuple[RuntimeSettingsSection, ...]


class AiRuntimeGeneralSettingsData(_StrictModel):
    sections: tuple[RuntimeSettingsSection, ...]


class RuntimeSettingsUpdate(_StrictModel):
    values: dict[str, RuntimeSettingValue] = Field(min_length=1, max_length=20)


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

    def runtime_process_matches(self, command: tuple[str, ...]) -> bool: ...


@runtime_checkable
class RuntimeActivityEventAdapter(Protocol):
    def read_activity_event(self, session_id: str) -> RuntimeActivityEvent | None: ...

    def clear_activity_event(self, session_id: str) -> None: ...

    def rebind_activity_session(self, old_session_id: str, new_session_id: str) -> None: ...


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

    def terminal_backend_matches(
        self,
        command: tuple[str, ...],
        session_id: str,
    ) -> bool: ...


@runtime_checkable
class RuntimeSessionArchiveAdapter(Protocol):
    def native_session_deleted_state(
        self,
        native_session_id: str,
    ) -> bool | None: ...

    def native_session_archive_state(
        self,
        native_session_id: str,
    ) -> bool | None: ...

    def run_native_action(
        self,
        action: RuntimeNativeAction,
        native_session_id: str,
    ) -> None: ...


@runtime_checkable
class RuntimeUsageSnapshotAdapter(Protocol):
    def read_usage_snapshot(self, *, force: bool = False): ...


@runtime_checkable
class RuntimeSettingsAdapter(Protocol):
    def read_runtime_settings(self) -> RuntimeSettingsData: ...

    def update_runtime_settings(
        self,
        update: RuntimeSettingsUpdate,
    ) -> RuntimeSettingsData: ...
