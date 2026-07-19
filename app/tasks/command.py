from __future__ import annotations

import os
import signal
import subprocess
import sys
import tempfile
from dataclasses import dataclass


MAX_OUTPUT_BYTES = 64 * 1024


@dataclass(frozen=True)
class CommandResult:
    returncode: int
    stdout: str
    stderr: str


class CommandNotFound(Exception):
    pass


class CommandPermissionDenied(Exception):
    pass


class CommandTimedOut(Exception):
    pass


def _decode_limited(value: bytes, limit: int) -> str:
    truncated = len(value) > limit
    decoded = value[:limit].decode("utf-8", errors="replace").strip()
    return f"{decoded}\n[output truncated]" if truncated else decoded


def run_command(
    command: list[str],
    *,
    timeout: float,
    max_output_bytes: int = MAX_OUTPUT_BYTES,
) -> CommandResult:
    if not command or timeout <= 0:
        raise CommandTimedOut
    with tempfile.TemporaryFile() as stdout_file, tempfile.TemporaryFile() as stderr_file:
        process_options: dict[str, object] = {}
        if sys.platform == "win32":
            process_options["creationflags"] = getattr(
                subprocess,
                "CREATE_NEW_PROCESS_GROUP",
                0x00000200,
            )
        else:
            process_options["start_new_session"] = True
        try:
            process = subprocess.Popen(
                command,
                stdin=subprocess.DEVNULL,
                stdout=stdout_file,
                stderr=stderr_file,
                shell=False,
                **process_options,
            )
        except FileNotFoundError as exc:
            raise CommandNotFound from exc
        except PermissionError as exc:
            raise CommandPermissionDenied from exc

        try:
            process.wait(timeout=timeout)
        except subprocess.TimeoutExpired as exc:
            if sys.platform == "win32":
                process.kill()
            else:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            process.wait()
            raise CommandTimedOut from exc

        stdout_file.seek(0)
        stderr_file.seek(0)
        stdout = stdout_file.read(max_output_bytes + 1)
        stderr = stderr_file.read(max_output_bytes + 1)
        return CommandResult(
            returncode=process.returncode,
            stdout=_decode_limited(stdout, max_output_bytes),
            stderr=_decode_limited(stderr, max_output_bytes),
        )
