from __future__ import annotations

import argparse
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
    parser.add_argument("--runtime-id", required=True, choices=("fixed-test",))
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
