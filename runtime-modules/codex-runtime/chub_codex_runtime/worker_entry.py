from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from pydantic import ValidationError


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="chub-codex-runtime-worker")
    parser.add_argument("--task-dir", required=True)
    parser.add_argument("--release-fd", required=True, type=int)
    parser.add_argument("--runtime-id", required=True, choices=("codex",))
    parser.add_argument("--runtime-executable", required=True)
    parser.add_argument("--chub-root", required=True, type=Path)
    parser.add_argument("--working-directory", required=True)
    session = parser.add_mutually_exclusive_group()
    session.add_argument("--native-session-id")
    session.add_argument("--start-new-session", action="store_true")
    return parser


def main() -> int:
    args = _parser().parse_args()
    os.umask(0o077)
    chub_root = args.chub_root.resolve(strict=True)
    if not (chub_root / "app").is_dir():
        return 70
    package_root = Path(__file__).resolve().parent.parent
    for path in (chub_root, package_root):
        if str(path) not in sys.path:
            sys.path.insert(0, str(path))
    from app.ai_runtime import RuntimeOperationError, RuntimeTurnRequest
    from app.quick_worker_tasks import (
        MAX_SPEC_BYTES,
        StoredTaskSpec,
        _digest_stored_spec,
        _read_model,
    )
    from chub_codex_runtime.runtime_runner import CodexRuntimeRunner

    with os.fdopen(args.release_fd, "rb", closefd=True) as release:
        if release.read(1) != b"1":
            return 70
    try:
        spec = _read_model(Path(args.task_dir) / "spec.json", StoredTaskSpec, max_bytes=MAX_SPEC_BYTES)
        if _digest_stored_spec(spec) != spec.spec_sha256 or spec.runtime_id != args.runtime_id:
            return 70
    except (OSError, UnicodeError, ValidationError, ValueError):
        return 70
    workspace = Path(args.working_directory)
    result_path = Path(args.task_dir) / "result.txt"
    try:
        CodexRuntimeRunner.validate_workspace(workspace)
        CodexRuntimeRunner.create_result_file(result_path)
    except RuntimeOperationError as exc:
        print(exc.message, file=sys.stderr)
        return 70
    native_session_id = None if args.start_new_session else args.native_session_id or spec.native_session_id
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
        os.execvpe(args.runtime_executable, list(process_spec.argv), os.environ.copy())
    except OSError:
        return 70


if __name__ == "__main__":
    raise SystemExit(main())
