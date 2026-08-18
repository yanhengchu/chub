from __future__ import annotations

import json
import os
import re
import stat
from pathlib import Path

from app.ai_runtime import (
    RuntimeEventSummary,
    RuntimeOperationError,
    RuntimeProcessSpec,
    RuntimeTurnRequest,
    RuntimeTurnResult,
)
from app.codex.runtime_adapter import is_valid_codex_session_id


class CodexRuntimeRunner:
    _PERMISSION_ARGS = {
        "auto-review": [
            "-c", 'default_permissions=":workspace"',
            "-c", 'approval_policy="on-request"',
            "-c", 'approvals_reviewer="auto_review"',
        ],
        "read-only": [
            "-c", 'default_permissions=":read-only"',
            "-c", 'approval_policy="on-request"',
            "-c", 'approvals_reviewer="user"',
        ],
        "full-access": [
            "-c", 'default_permissions=":danger-full-access"',
            "-c", 'approval_policy="never"',
        ],
    }

    @staticmethod
    def validate_workspace(workspace: Path) -> None:
        if not workspace.is_dir() or workspace.is_symlink():
            raise RuntimeOperationError(
                "codex_workspace_unavailable",
                "Codex runner workspace is unavailable",
            )

    @staticmethod
    def create_result_file(path: Path) -> None:
        flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        try:
            descriptor = os.open(path, flags, 0o600)
        except OSError as exc:
            raise RuntimeOperationError(
                "codex_result_unavailable",
                "Codex result path is unavailable",
            ) from exc
        os.close(descriptor)

    @classmethod
    def command(
        cls,
        executable: str,
        result_path: Path,
        request: RuntimeTurnRequest,
        *,
        start_new_session: bool = False,
    ) -> RuntimeProcessSpec:
        if (
            request.native_session_id is not None
            and not is_valid_codex_session_id(request.native_session_id)
        ):
            raise RuntimeOperationError(
                "codex_session_invalid",
                "Codex Session ID is invalid",
                kind="invalid_request",
            )
        command = [
            executable,
            "exec",
            "--profile",
            "chub",
            "--json",
            *cls._PERMISSION_ARGS[request.permission_profile],
            "--output-last-message",
            str(result_path),
        ]
        if request.model:
            command.extend(["--model", request.model])
        if request.reasoning_effort:
            command.extend(
                [
                    "-c",
                    f"model_reasoning_effort={json.dumps(request.reasoning_effort)}",
                ]
            )
        if request.native_session_id and not start_new_session:
            command.extend(["resume", request.native_session_id])
        command.append("-")
        return RuntimeProcessSpec(argv=tuple(command))

    @staticmethod
    def parse_event_stream(
        path: Path,
        *,
        native_session_pattern: str,
        max_event_bytes: int,
        missing_ok: bool = False,
    ) -> RuntimeEventSummary:
        try:
            metadata = path.lstat()
        except FileNotFoundError:
            if missing_ok:
                return RuntimeEventSummary()
            raise RuntimeOperationError(
                "codex_event_stream_unavailable",
                "Codex event stream is unavailable",
            ) from None
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != os.getuid()
            or metadata.st_size > max_event_bytes
            or stat.S_IMODE(metadata.st_mode) & 0o077
        ):
            raise RuntimeOperationError(
                "codex_event_stream_unsafe",
                "Codex event stream is unsafe or too large",
            )
        found: set[str] = set()
        try:
            with path.open("rb") as file:
                for raw_line in file:
                    if len(raw_line) > max_event_bytes:
                        raise RuntimeOperationError(
                            "codex_event_line_too_large",
                            "Codex event line exceeds its fixed limit",
                        )
                    try:
                        event = json.loads(raw_line)
                    except (UnicodeDecodeError, json.JSONDecodeError):
                        continue
                    native_id = (
                        event.get("thread_id")
                        if isinstance(event, dict)
                        and event.get("type") == "thread.started"
                        else None
                    )
                    if isinstance(native_id, str) and re.fullmatch(
                        native_session_pattern,
                        native_id,
                    ):
                        found.add(native_id)
        except OSError as exc:
            raise RuntimeOperationError(
                "codex_event_stream_unavailable",
                "Codex event stream is unavailable",
            ) from exc
        if len(found) > 1:
            raise RuntimeOperationError(
                "codex_event_session_conflict",
                "Codex event stream contains conflicting Session IDs",
                kind="conflict",
            )
        return RuntimeEventSummary(native_session_id=next(iter(found), None))

    @staticmethod
    def read_result(path: Path, *, max_bytes: int) -> RuntimeTurnResult:
        try:
            metadata = path.lstat()
            if not stat.S_ISREG(metadata.st_mode) or metadata.st_uid != os.getuid():
                raise OSError("Codex result is unsafe")
            with path.open("rb") as file:
                content = file.read(max_bytes + 1)
        except OSError as exc:
            raise RuntimeOperationError(
                "codex_result_unavailable",
                "Codex result is unavailable",
            ) from exc
        truncated = len(content) > max_bytes
        if truncated:
            content = content[:max_bytes]
        return RuntimeTurnResult(
            text=content.decode("utf-8", errors="replace"),
            truncated=truncated,
        )
