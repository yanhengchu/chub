#!/usr/bin/env python3
"""Detect and normally close stable Google Chrome across supported platforms."""

from __future__ import annotations

import os
import re
import shlex
import signal
import subprocess
import sys
import time
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path


DEFAULT_CLOSE_TIMEOUT = 15
MACOS_MAIN_EXECUTABLE = re.compile(
    r"^/.*/Google Chrome\.app/Contents/MacOS/Google Chrome$"
)
MACOS_HELPER_EXECUTABLE = re.compile(
    r"^/.*/Google Chrome\.app/Contents/Frameworks/"
    r"Google Chrome Framework\.framework/Versions/[^/]+/Helpers/"
    r"(Google Chrome Helper(?: \([^)]+\))?)\.app/Contents/MacOS/\1$"
)


@dataclass(frozen=True)
class ChromeProcess:
    pid: int
    executable: Path
    arguments: list[str]

    @property
    def process_type(self) -> str | None:
        prefix = "--type="
        return next(
            (
                argument[len(prefix) :]
                for argument in self.arguments[1:]
                if argument.startswith(prefix)
            ),
            None,
        )


def is_stable_chrome_executable(executable: Path, *, platform: str) -> bool:
    normalized = executable.as_posix()
    if platform == "linux":
        return (
            executable.name in {"google-chrome", "google-chrome-stable"}
            or (
                executable.name == "chrome"
                and "/google/chrome/" in normalized
            )
        )
    if platform == "darwin":
        return bool(
            MACOS_MAIN_EXECUTABLE.fullmatch(normalized)
            or MACOS_HELPER_EXECUTABLE.fullmatch(normalized)
        )
    if platform == "win32":
        normalized = normalized.replace("\\", "/").lower()
        return normalized.endswith("/google/chrome/application/chrome.exe")
    return False


def normalize_linux_arguments(
    executable: Path, arguments: list[str]
) -> list[str]:
    """Expand Chrome's rewritten single-field process title when necessary."""
    if len(arguments) != 1:
        return arguments

    process_title = arguments[0]
    executable_prefix = f"{executable} "
    if not process_title.startswith(executable_prefix):
        return arguments
    try:
        parsed = shlex.split(process_title)
    except ValueError:
        return arguments
    return parsed if parsed and parsed[0] == str(executable) else arguments


def run_process_command(command: Sequence[str]) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            command,
            capture_output=True,
            text=True,
            check=False,
            timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RuntimeError(f"Unable to manage Chrome processes: {exc}") from exc


def is_chrome_running_macos() -> bool:
    result = run_process_command(
        [
            "pgrep",
            "-f",
            r"(^|/)(Applications/Google Chrome\.app/Contents/)",
        ]
    )
    if result.returncode not in (0, 1):
        raise RuntimeError(
            f"Unable to check Chrome processes: pgrep exit {result.returncode}"
        )
    return result.returncode == 0


def linux_chrome_processes(proc_root: Path = Path("/proc")) -> list[ChromeProcess]:
    processes: list[ChromeProcess] = []
    try:
        entries = sorted(
            (entry for entry in proc_root.iterdir() if entry.name.isdigit()),
            key=lambda entry: int(entry.name),
        )
    except OSError as exc:
        raise RuntimeError(f"Unable to inspect Linux processes: {exc}") from exc

    for entry in entries:
        try:
            executable = (entry / "exe").readlink()
            raw_command = (entry / "cmdline").read_bytes()
        except OSError:
            continue
        arguments = [
            value.decode(errors="replace")
            for value in raw_command.split(b"\0")
            if value
        ]
        arguments = normalize_linux_arguments(executable, arguments)
        if not arguments:
            continue

        if is_stable_chrome_executable(executable, platform="linux"):
            processes.append(
                ChromeProcess(
                    pid=int(entry.name),
                    executable=executable,
                    arguments=arguments,
                )
            )
    return processes


def is_chrome_running_ubuntu() -> bool:
    return bool(linux_chrome_processes())


def is_chrome_running_windows() -> bool:
    result = run_process_command(
        ["tasklist", "/FI", "IMAGENAME eq chrome.exe", "/FO", "CSV", "/NH"]
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"Unable to check Chrome processes: tasklist exit {result.returncode}"
        )
    return '"chrome.exe"' in result.stdout.lower()


def is_chrome_running() -> bool:
    if sys.platform == "darwin":
        return is_chrome_running_macos()
    if sys.platform.startswith("linux"):
        return is_chrome_running_ubuntu()
    if sys.platform == "win32":
        return is_chrome_running_windows()
    raise RuntimeError(f"Unsupported platform: {sys.platform}")


def request_chrome_close_macos() -> None:
    result = run_process_command(
        ["osascript", "-e", 'tell application "Google Chrome" to quit']
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"Unable to request Chrome exit: osascript exit {result.returncode}"
        )


def request_chrome_close_ubuntu() -> None:
    main_processes = [
        process.pid
        for process in linux_chrome_processes()
        if process.process_type is None
    ]
    if not main_processes:
        raise RuntimeError("Chrome helpers are running but no main process was found")

    for pid in main_processes:
        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            continue
        except OSError as exc:
            raise RuntimeError(f"Unable to request Chrome exit: {exc}") from exc


def request_chrome_close_windows() -> None:
    command = (
        "$processes = Get-Process chrome -ErrorAction SilentlyContinue; "
        "$processes | Where-Object { $_.MainWindowHandle -ne 0 } | "
        "ForEach-Object { $null = $_.CloseMainWindow() }"
    )
    result = run_process_command(
        ["powershell.exe", "-NoProfile", "-Command", command]
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"Unable to request Chrome exit: PowerShell exit {result.returncode}"
        )


def request_chrome_close() -> None:
    if sys.platform == "darwin":
        request_chrome_close_macos()
        return
    if sys.platform.startswith("linux"):
        request_chrome_close_ubuntu()
        return
    if sys.platform == "win32":
        request_chrome_close_windows()
        return
    raise RuntimeError(f"Unsupported platform: {sys.platform}")


def wait_for_chrome_exit(timeout: float = DEFAULT_CLOSE_TIMEOUT) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not is_chrome_running():
            return True
        time.sleep(0.25)
    return not is_chrome_running()


def close_running_chrome(timeout: float = DEFAULT_CLOSE_TIMEOUT) -> None:
    if not is_chrome_running():
        return

    request_chrome_close()
    if not wait_for_chrome_exit(timeout):
        raise RuntimeError(
            f"Chrome did not exit within {timeout:g} seconds; profile was not copied"
        )
