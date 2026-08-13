from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

from pydantic import ValidationError

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
    parser.add_argument("--codex-executable")
    parser.add_argument("--working-directory")
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
        behavior = spec.behavior
        prompt = spec.prompt
        run_seconds = spec.run_seconds
    except (OSError, UnicodeError, ValidationError, ValueError):
        print("task runner could not read its task specification", file=sys.stderr)
        return 70

    if spec.runner_kind == "codex":
        if not args.codex_executable or not args.working_directory:
            print("Codex runner configuration is incomplete", file=sys.stderr)
            return 70
        workspace = Path(args.working_directory)
        if not workspace.is_dir() or workspace.is_symlink():
            print("Codex runner workspace is unavailable", file=sys.stderr)
            return 70
        result_path = Path(args.task_dir) / "result.txt"
        flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        try:
            descriptor = os.open(result_path, flags, 0o600)
        except OSError:
            print("Codex result path is unavailable", file=sys.stderr)
            return 70
        os.close(descriptor)
        permission_args = {
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
        }[spec.permission_mode]
        command = [
            args.codex_executable,
            "exec",
            "--profile",
            "chub",
            "--json",
            *permission_args,
            "--output-last-message",
            str(result_path),
        ]
        if spec.model:
            command.extend(["--model", spec.model])
        if spec.reasoning_effort:
            command.extend(
                ["-c", f"model_reasoning_effort={json.dumps(spec.reasoning_effort)}"]
            )
        if spec.codex_session_id:
            command.extend(["resume", spec.codex_session_id])
        command.append("-")
        os.chdir(workspace)
        try:
            os.execvpe(args.codex_executable, command, os.environ.copy())
        except OSError:
            print("Codex runner could not execute Codex", file=sys.stderr)
            return 70

    if behavior is None or run_seconds is None:
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
