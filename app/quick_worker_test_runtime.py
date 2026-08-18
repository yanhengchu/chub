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
    RuntimeTurnRequest,
    RuntimeTurnResult,
    RuntimeWorkerLaunchRequest,
    RuntimeWorkerLaunchSpec,
)


class FixedTestWorkerRuntime:
    @property
    def descriptor(self) -> RuntimeDescriptor:
        return RuntimeDescriptor(
            runtime_id="fixed-test",
            capabilities=BACKGROUND_RUNTIME_CAPABILITIES,
        )

    @property
    def available(self) -> bool:
        return True

    @property
    def workspace_ids(self) -> tuple[str, ...]:
        return ()

    @staticmethod
    def validate_turn(workspace_id: str, request: RuntimeTurnRequest) -> None:
        raise RuntimeOperationError(
            "runtime_test_request_invalid",
            "Fixed test tasks do not accept Runtime Turn settings",
            kind="invalid_request",
        )

    @staticmethod
    def build_launch(request: RuntimeWorkerLaunchRequest) -> RuntimeWorkerLaunchSpec:
        if request.test_behavior is None or request.test_run_seconds is None:
            raise RuntimeOperationError(
                "runtime_test_request_invalid",
                "Fixed test Runner configuration is incomplete",
                kind="invalid_request",
            )
        return RuntimeWorkerLaunchSpec(
            argv=(
                sys.executable,
                "-m",
                "app.quick_worker_runner",
                "--task-dir",
                str(request.task_dir),
                "--release-fd",
                str(request.release_fd),
                "--runtime-id",
                "fixed-test",
                "--test-behavior",
                request.test_behavior,
                "--test-run-seconds",
                str(request.test_run_seconds),
            ),
            stdin_prompt=False,
            environment={},
        )

    @staticmethod
    def has_active_writer(native_session_id: str) -> bool:
        return False

    @staticmethod
    def native_session_available(native_session_id: str) -> bool:
        return False

    @staticmethod
    def parse_event_stream(
        path: Path,
        *,
        max_event_bytes: int,
        missing_ok: bool = False,
    ) -> RuntimeEventSummary:
        return RuntimeEventSummary()

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
                "runtime_test_result_unsafe",
                "Fixed test result is unsafe",
            )
        with path.open("rb") as file:
            content = file.read(max_bytes + 1)
        truncated = len(content) > max_bytes
        return RuntimeTurnResult(
            text=content[:max_bytes].decode("utf-8", errors="replace"),
            truncated=truncated,
        )
