from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path
from typing import Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field

from app.ai_runtime.contracts import (
    RuntimeDescriptor,
    RuntimeEventSummary,
    RuntimeOperationError,
    RuntimeTurnRequest,
    RuntimeTurnResult,
)


BACKGROUND_RUNTIME_CAPABILITIES = frozenset(
    {
        "runtime_status",
        "background_turn",
        "task_cancel",
        "native_session_mapping",
        "structured_events",
        "permission_profiles",
    }
)


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class RuntimeWorkerLaunchRequest(_StrictModel):
    task_id: str = Field(min_length=1, max_length=128)
    task_dir: Path
    release_fd: int = Field(ge=0)
    session_id: str | None = Field(default=None, min_length=1, max_length=128)
    task_kind: str = Field(min_length=1, max_length=32)
    workspace_id: str | None = Field(default=None, min_length=1, max_length=64)
    turn: RuntimeTurnRequest | None = None
    start_new_session: bool = False
    hook_dir: Path
    restart_request_dir: Path
    test_behavior: str | None = Field(default=None, max_length=32)
    test_run_seconds: float | None = Field(default=None, ge=0.0, le=60.0)


class RuntimeWorkerLaunchSpec(_StrictModel):
    argv: tuple[str, ...] = Field(min_length=1, max_length=64)
    stdin_prompt: bool
    environment: dict[str, str] = Field(default_factory=dict)


@runtime_checkable
class RuntimeWorkerRunner(Protocol):
    @property
    def descriptor(self) -> RuntimeDescriptor: ...

    @property
    def available(self) -> bool: ...

    @property
    def workspace_ids(self) -> tuple[str, ...]: ...

    def validate_turn(self, workspace_id: str, request: RuntimeTurnRequest) -> None: ...

    def build_launch(self, request: RuntimeWorkerLaunchRequest) -> RuntimeWorkerLaunchSpec: ...

    def has_active_writer(self, native_session_id: str) -> bool: ...

    def native_session_available(self, native_session_id: str) -> bool: ...

    def parse_event_stream(
        self,
        path: Path,
        *,
        max_event_bytes: int,
        missing_ok: bool = False,
    ) -> RuntimeEventSummary: ...

    def read_result(self, task_dir: Path, *, max_bytes: int) -> RuntimeTurnResult: ...


class WorkerRuntimeRegistry:
    def __init__(self, runners: Iterable[RuntimeWorkerRunner] = ()) -> None:
        self._runners: dict[str, RuntimeWorkerRunner] = {}
        for runner in runners:
            self.register(runner)

    def register(self, runner: RuntimeWorkerRunner) -> None:
        required_methods = (
            "validate_turn",
            "build_launch",
            "has_active_writer",
            "native_session_available",
            "parse_event_stream",
            "read_result",
        )
        if not isinstance(runner, RuntimeWorkerRunner) or any(
            not callable(getattr(runner, name, None)) for name in required_methods
        ):
            raise RuntimeOperationError(
                "runtime_runner_invalid",
                "Runtime Runner does not implement the fixed Worker contract",
                kind="conflict",
            )
        descriptor = runner.descriptor
        if not isinstance(descriptor, RuntimeDescriptor):
            raise RuntimeOperationError(
                "runtime_runner_invalid",
                "Runtime Runner descriptor is invalid",
                kind="conflict",
            )
        runtime_id = descriptor.runtime_id
        if runtime_id in self._runners:
            raise RuntimeOperationError(
                "runtime_runner_duplicate",
                f"Runtime Runner is already registered: {runtime_id}",
                kind="conflict",
            )
        missing = BACKGROUND_RUNTIME_CAPABILITIES - descriptor.capabilities
        if missing:
            raise RuntimeOperationError(
                "runtime_runner_capability_invalid",
                f"Runtime Runner {runtime_id} is missing capabilities: "
                f"{', '.join(sorted(missing))}",
                kind="conflict",
            )
        self._runners[runtime_id] = runner

    def require(self, runtime_id: str) -> RuntimeWorkerRunner:
        runner = self._runners.get(runtime_id)
        if runner is None:
            raise RuntimeOperationError(
                "runtime_unavailable",
                f"Runtime is not registered for background execution: {runtime_id}",
            )
        if not runner.available:
            raise RuntimeOperationError(
                "runtime_unavailable",
                f"Runtime is unavailable for background execution: {runtime_id}",
            )
        return runner

    def runtime_ids(self) -> tuple[str, ...]:
        return tuple(self._runners)

    def available_runtime_ids(self) -> tuple[str, ...]:
        return tuple(
            runtime_id
            for runtime_id, runner in self._runners.items()
            if runner.available
        )

    def workspace_ids(self) -> dict[str, tuple[str, ...]]:
        return {
            runtime_id: runner.workspace_ids
            for runtime_id, runner in self._runners.items()
        }
