from __future__ import annotations

import argparse
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

from pydantic import ValidationError

from app.ai_runtime import RuntimeOperationError, RuntimeTurnRequest
from app.codex.runtime_runner import CodexRuntimeRunner
from app.quick_worker_tasks import (
    MAX_SPEC_BYTES,
    StoredTaskSpec,
    _digest_stored_spec,
    _read_model,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m app.quick_worker_runner")
    parser.add_argument("--task-dir", required=True)
    parser.add_argument("--release-fd", required=True, type=int)
    parser.add_argument("--runtime-id", required=True, choices=("codex", "fixed-test"))
    parser.add_argument("--runtime-executable")
    parser.add_argument("--working-directory")
    session = parser.add_mutually_exclusive_group()
    session.add_argument("--native-session-id")
    session.add_argument("--start-new-session", action="store_true")
    parser.add_argument(
        "--test-behavior",
        choices=("succeed", "fail", "ignore_term", "orphan_child"),
    )
    parser.add_argument("--test-run-seconds", type=float)
    return parser


def _load_spec(task_dir: Path) -> StoredTaskSpec:
    spec = _read_model(
        task_dir / "spec.json",
        StoredTaskSpec,
        max_bytes=MAX_SPEC_BYTES,
    )
    if _digest_stored_spec(spec) != spec.spec_sha256:
        raise ValueError("task specification digest does not match")
    return spec


def main() -> int:
    args = _parser().parse_args()
    os.umask(0o077)
    with os.fdopen(args.release_fd, "rb", closefd=True) as release:
        if release.read(1) != b"1":
            return 70

    try:
        spec = _load_spec(Path(args.task_dir))
        behavior = spec.test_behavior
        prompt = spec.prompt
        run_seconds = spec.test_run_seconds
    except (OSError, UnicodeError, ValidationError, ValueError):
        print("task runner could not read its task specification", file=sys.stderr)
        return 70

    if spec.runtime_id != args.runtime_id:
        print("task runner Runtime identity does not match", file=sys.stderr)
        return 70

    if spec.runtime_id == "codex":
        if not args.runtime_executable or not args.working_directory:
            print("Runtime Runner configuration is incomplete", file=sys.stderr)
            return 70
        workspace = Path(args.working_directory)
        result_path = Path(args.task_dir) / "result.txt"
        try:
            CodexRuntimeRunner.validate_workspace(workspace)
            CodexRuntimeRunner.create_result_file(result_path)
        except RuntimeOperationError as exc:
            print(exc.message, file=sys.stderr)
            return 70
        native_session_id = (
            None
            if args.start_new_session
            else args.native_session_id or spec.native_session_id
        )
        request = RuntimeTurnRequest(
            permission_profile=spec.permission_profile,
            native_session_id=native_session_id,
            model=spec.model,
            reasoning_effort=spec.reasoning_effort,
        )
        process_spec = CodexRuntimeRunner.command(
            args.runtime_executable,
            result_path,
            request,
            start_new_session=args.start_new_session,
        )
        os.chdir(workspace)
        try:
            os.execvpe(
                args.runtime_executable,
                list(process_spec.argv),
                os.environ.copy(),
            )
        except OSError:
            print("Runtime Runner could not execute its fixed executable", file=sys.stderr)
            return 70

    if (
        spec.runtime_id != "fixed-test"
        or behavior is None
        or run_seconds is None
        or args.test_behavior != behavior
        or args.test_run_seconds != run_seconds
    ):
        return 70
    if behavior == "orphan_child":
        subprocess.Popen(
            [
                sys.executable,
                "-c",
                "import signal,time; "
                "signal.signal(signal.SIGTERM, signal.SIG_IGN); "
                "time.sleep(60)",
            ],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    if behavior == "ignore_term":
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
    if run_seconds > 0:
        time.sleep(float(run_seconds))
    if behavior == "fail":
        print("fixed test runner failed as requested", file=sys.stderr)
        return 23
    print(f"completed: {prompt}", end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
