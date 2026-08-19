from __future__ import annotations

import os
import sys
from pathlib import Path

from app.ai_runtime import (
    RuntimeDescriptor,
    RuntimeEventSummary,
    RuntimeOperationError,
    RuntimeTurnRequest,
    RuntimeTurnResult,
    RuntimeWorkerLaunchRequest,
    RuntimeWorkerLaunchSpec,
)
from app.codex.runtime_adapter import CodexRuntimeAdapter
from app.codex.runtime_runner import CodexRuntimeRunner


DISCOVERED_RUNTIME_WORKSPACE_ID = "runtime-session"


class CodexWorkerRuntime:
    def __init__(
        self,
        adapter: CodexRuntimeAdapter,
        *,
        executable: str | None,
        workspaces: dict[str, Path],
    ) -> None:
        self._adapter = adapter
        self._executable = executable
        self._workspaces = {
            workspace_id: workspace.resolve()
            for workspace_id, workspace in workspaces.items()
        }

    @property
    def descriptor(self) -> RuntimeDescriptor:
        return self._adapter.descriptor

    @property
    def available(self) -> bool:
        if not self._executable:
            return False
        executable = Path(self._executable)
        # Fixed workspace paths are validated when a turn uses them. Native
        # Sessions resolve their own trusted working directory at submission
        # time, so one missing shortcut must not disable the Runtime globally.
        return executable.is_file() and os.access(executable, os.X_OK)

    @property
    def workspace_ids(self) -> tuple[str, ...]:
        return tuple(sorted((*self._workspaces, DISCOVERED_RUNTIME_WORKSPACE_ID)))

    def validate_turn(self, workspace_id: str, request: RuntimeTurnRequest) -> None:
        self._workspace_for_turn(workspace_id, request)
        if request.native_session_id is not None:
            self._adapter.validate_native_session_id(request.native_session_id)
        self._adapter.validate_model(request.model, request.reasoning_effort)

    def build_launch(self, request: RuntimeWorkerLaunchRequest) -> RuntimeWorkerLaunchSpec:
        if self._executable is None or request.workspace_id is None or request.turn is None:
            raise RuntimeOperationError(
                "runtime_runner_unavailable",
                "Codex background Runner configuration is incomplete",
            )
        self.validate_turn(request.workspace_id, request.turn)
        workspace = self._workspace_for_turn(request.workspace_id, request.turn)
        argv = [
            sys.executable,
            "-m",
            "app.quick_worker_runner",
            "--task-dir",
            str(request.task_dir),
            "--release-fd",
            str(request.release_fd),
            "--runtime-id",
            "codex",
            "--runtime-executable",
            self._executable,
            "--working-directory",
            str(workspace),
        ]
        if request.start_new_session:
            argv.append("--start-new-session")
        elif request.turn.native_session_id is not None:
            argv.extend(["--native-session-id", request.turn.native_session_id])
        environment: dict[str, str] = {}
        if request.session_id is not None:
            environment = {
                "CHUB_PTY_SESSION_ID": request.session_id,
                "CHUB_PTY_HOOK_DIR": str(request.hook_dir),
                "CHUB_ACTIVITY_SOURCE": "quick",
            }
            if request.task_kind != "translation":
                environment.update(
                    {
                        "CHUB_QUICK_TASK_ID": request.task_id,
                        "CHUB_QUICK_RESTART_DIR": str(request.restart_request_dir),
                    }
                )
        return RuntimeWorkerLaunchSpec(
            argv=tuple(argv),
            stdin_prompt=True,
            environment=environment,
        )

    def _workspace_for_turn(
        self,
        workspace_id: str,
        request: RuntimeTurnRequest,
    ) -> Path:
        if workspace_id == DISCOVERED_RUNTIME_WORKSPACE_ID:
            return self._discovered_native_workspace(request)
        workspace = self._workspaces.get(workspace_id)
        if workspace is None:
            raise RuntimeOperationError(
                "worker_workspace_unavailable",
                "The requested workspace is unavailable",
                kind="invalid_request",
            )
        CodexRuntimeRunner.validate_workspace(workspace)
        return workspace

    def _discovered_native_workspace(self, request: RuntimeTurnRequest) -> Path:
        native_session_id = request.native_session_id
        if native_session_id is None:
            raise RuntimeOperationError(
                "worker_workspace_unavailable",
                "The recovered Session does not have a native Runtime mapping",
                kind="invalid_request",
            )
        discovery = self._adapter.discover_sessions()
        native = next(
            (
                candidate
                for candidate in discovery.sessions
                if candidate.native_session_id == native_session_id
            ),
            None,
        )
        if native is None:
            raise RuntimeOperationError(
                "worker_workspace_unavailable",
                "The recovered Session working directory is unavailable",
                kind="invalid_request",
            )
        try:
            workspace = native.cwd.expanduser().resolve(strict=True)
        except OSError as exc:
            raise RuntimeOperationError(
                "worker_workspace_unavailable",
                "The recovered Session working directory is unavailable",
                kind="invalid_request",
            ) from exc
        if not workspace.is_dir():
            raise RuntimeOperationError(
                "worker_workspace_unavailable",
                "The recovered Session working directory is unavailable",
                kind="invalid_request",
            )
        return workspace

    def has_active_writer(self, native_session_id: str) -> bool:
        return self._adapter.has_active_writer(native_session_id)

    def native_session_available(self, native_session_id: str) -> bool:
        return self._adapter.native_session_available(native_session_id)

    def parse_event_stream(
        self,
        path: Path,
        *,
        max_event_bytes: int,
        missing_ok: bool = False,
    ) -> RuntimeEventSummary:
        return CodexRuntimeRunner.parse_event_stream(
            path,
            native_session_pattern=(
                r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
                r"[89abAB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}$"
            ),
            max_event_bytes=max_event_bytes,
            missing_ok=missing_ok,
        )

    @staticmethod
    def read_error(task_dir: Path, *, max_bytes: int) -> str | None:
        return CodexRuntimeRunner.read_error(
            task_dir / "stdout.txt",
            max_bytes=max_bytes,
        )

    @staticmethod
    def read_result(task_dir: Path, *, max_bytes: int) -> RuntimeTurnResult:
        return CodexRuntimeRunner.read_result(
            task_dir / "result.txt",
            max_bytes=max_bytes,
        )
