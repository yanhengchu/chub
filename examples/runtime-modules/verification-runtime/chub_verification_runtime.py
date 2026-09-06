from __future__ import annotations

import os
import stat
import sys
from pathlib import Path

from app.ai_runtime import (
    BACKGROUND_RUNTIME_CAPABILITIES,
    RuntimeDescriptor,
    RuntimeEventSummary,
    RuntimeOperationError,
    RuntimeSessionDiscoveryResult,
    RuntimeStatus,
    RuntimeTurnRequest,
    RuntimeTurnResult,
    RuntimeWorkerLaunchRequest,
    RuntimeWorkerLaunchSpec,
)


DESCRIPTOR = RuntimeDescriptor(
    runtime_id="verification-runtime",
    capabilities=BACKGROUND_RUNTIME_CAPABILITIES,
)


class VerificationAdapter:
    @property
    def descriptor(self) -> RuntimeDescriptor:
        return DESCRIPTOR

    def status(self) -> RuntimeStatus:
        return RuntimeStatus(
            runtime_id=DESCRIPTOR.runtime_id,
            available=True,
            dependencies={"verification_module": True},
        )

    @staticmethod
    def validate_native_session_id(_native_session_id: str) -> None:
        raise RuntimeOperationError(
            "verification_runtime_no_native_sessions",
            "Verification Runtime does not provide native Sessions.",
            kind="invalid_request",
        )

    @staticmethod
    def discover_sessions() -> RuntimeSessionDiscoveryResult:
        return RuntimeSessionDiscoveryResult(sessions=())

    @staticmethod
    def native_session_available(_native_session_id: str) -> bool:
        return False


class VerificationRunner:
    @property
    def descriptor(self) -> RuntimeDescriptor:
        return DESCRIPTOR

    @property
    def available(self) -> bool:
        return True

    @property
    def workspace_ids(self) -> tuple[str, ...]:
        return ()

    @staticmethod
    def validate_turn(_workspace_id: str, _request: RuntimeTurnRequest) -> None:
        raise RuntimeOperationError(
            "verification_runtime_not_for_tasks",
            "Verification Runtime only validates module installation.",
            kind="invalid_request",
        )

    @staticmethod
    def build_launch(request: RuntimeWorkerLaunchRequest) -> RuntimeWorkerLaunchSpec:
        program = (
            "import os,sys; fd=int(sys.argv[1]); "
            "os.read(fd, 1); os.close(fd); "
            "print('Verification Runtime registration confirmed')"
        )
        return RuntimeWorkerLaunchSpec(
            argv=(sys.executable, "-c", program, str(request.release_fd)),
            stdin_prompt=False,
        )

    @staticmethod
    def has_active_writer(_native_session_id: str) -> bool:
        return False

    @staticmethod
    def native_session_available(_native_session_id: str) -> bool:
        return False

    @staticmethod
    def parse_event_stream(
        _path: Path,
        *,
        max_event_bytes: int,
        missing_ok: bool = False,
    ) -> RuntimeEventSummary:
        return RuntimeEventSummary()

    @staticmethod
    def read_error(_task_dir: Path, *, max_bytes: int) -> str | None:
        return None

    @staticmethod
    def read_result(task_dir: Path, *, max_bytes: int) -> RuntimeTurnResult:
        path = task_dir / "stdout.txt"
        metadata = path.lstat()
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != os.getuid()
            or stat.S_IMODE(metadata.st_mode) & 0o077
        ):
            raise RuntimeOperationError(
                "verification_runtime_result_unsafe",
                "Verification Runtime result is unsafe.",
            )
        with path.open("rb") as file:
            content = file.read(max_bytes + 1)
        return RuntimeTurnResult(
            text=content[:max_bytes].decode("utf-8", errors="replace"),
            truncated=len(content) > max_bytes,
        )


class VerificationRuntimeModule:
    descriptor = DESCRIPTOR
    display_name = "Verification Runtime"
    description = "Validates Runtime ZIP installation and Worker registration."
    is_default = False

    @staticmethod
    def build_adapter() -> VerificationAdapter:
        return VerificationAdapter()

    @staticmethod
    def build_worker_runner(
        _adapter: VerificationAdapter,
        *,
        workspaces: dict[str, Path],
    ) -> VerificationRunner:
        return VerificationRunner()


def create_runtime_module(_settings: object) -> VerificationRuntimeModule:
    return VerificationRuntimeModule()
