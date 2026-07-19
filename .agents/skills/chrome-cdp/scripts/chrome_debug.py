#!/usr/bin/env python3
"""Start, stop, and inspect the Chrome instance owned by chrome-cdp."""

from __future__ import annotations

import argparse
import base64
import json
import ntpath
import os
import re
import shutil
import shlex
import signal
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from chrome_process import (
    ChromeProcess,
    is_stable_chrome_executable,
    linux_chrome_processes,
    run_process_command,
)
from profile_store import (
    MANIFEST_FILE,
    active_profile,
    copied_profiles,
    profile_display_name,
    select_profile,
)


DEFAULT_USER_DATA_DIR = Path.home() / "chrome-debug-data"
CDP_HOST = "127.0.0.1"
CDP_PORT = 9222
CDP_ENDPOINT = f"http://{CDP_HOST}:{CDP_PORT}"
START_TIMEOUT = 15
STOP_TIMEOUT = 15
GRACEFUL_STOP_TIMEOUT = 5


@dataclass(frozen=True)
class DebugStatus:
    state: str
    mode: str | None
    endpoint: str
    user_data_dir: str
    profile_directory: str | None
    process_ids: list[int]


def load_profile_directory(user_data_dir: Path) -> str:
    return active_profile(user_data_dir)


def chrome_executable() -> Path:
    if sys.platform == "darwin":
        candidates = [
            Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"),
            Path.home()
            / "Applications"
            / "Google Chrome.app"
            / "Contents"
            / "MacOS"
            / "Google Chrome",
        ]
    elif sys.platform.startswith("linux"):
        discovered = shutil.which("google-chrome-stable") or shutil.which(
            "google-chrome"
        )
        candidates = [Path(discovered)] if discovered else []
    elif sys.platform == "win32":
        candidates = []
        for environment_name in ("PROGRAMFILES", "PROGRAMFILES(X86)", "LOCALAPPDATA"):
            base = os.environ.get(environment_name)
            if base:
                candidates.append(
                    Path(base) / "Google" / "Chrome" / "Application" / "chrome.exe"
                )
        discovered = shutil.which("chrome.exe")
        if discovered:
            candidates.append(Path(discovered))
    else:
        raise RuntimeError(f"Unsupported platform: {sys.platform}")

    for candidate in candidates:
        if candidate.is_file():
            return candidate
    raise RuntimeError("Google Chrome executable was not found")


def build_launch_command(
    executable: Path,
    user_data_dir: Path,
    profile_directory: str,
    *,
    headless: bool = False,
) -> list[str]:
    command = [
        str(executable),
        f"--remote-debugging-address={CDP_HOST}",
        f"--remote-debugging-port={CDP_PORT}",
        f"--user-data-dir={user_data_dir}",
        f"--profile-directory={profile_directory}",
        "--no-first-run",
        "--no-default-browser-check",
    ]
    if headless:
        command.append("--headless")
    return command


def command_arguments(command: str, *, windows: bool = False) -> list[str]:
    try:
        arguments = shlex.split(command, posix=not windows)
    except ValueError:
        return []
    return [argument.strip('"') for argument in arguments]


def command_option(
    command: str, option: str, *, windows: bool = False
) -> str | None:
    arguments = command_arguments(command, windows=windows)
    prefix = f"{option}="
    for index, argument in enumerate(arguments):
        if argument.startswith(prefix):
            return argument[len(prefix) :]
        if argument == option and index + 1 < len(arguments):
            return arguments[index + 1].strip('"')
    return None


def same_path(left: str, right: Path, *, windows: bool) -> bool:
    if windows:
        return ntpath.normcase(ntpath.normpath(left)) == ntpath.normcase(
            ntpath.normpath(str(right))
        )
    return Path(left).expanduser().absolute() == right.expanduser().absolute()


def process_uses_user_data(command: str, user_data_dir: Path, *, windows: bool) -> bool:
    value = command_option(command, "--user-data-dir", windows=windows)
    return value is not None and same_path(value, user_data_dir, windows=windows)


def process_option(process: ChromeProcess, option: str) -> str | None:
    prefix = f"{option}="
    for index, argument in enumerate(process.arguments[1:], start=1):
        if argument.startswith(prefix):
            return argument[len(prefix) :]
        if argument == option and index + 1 < len(process.arguments):
            return process.arguments[index + 1]
    return None


def process_has_option(process: ChromeProcess, option: str) -> bool:
    prefix = f"{option}="
    return any(
        argument == option or argument.startswith(prefix)
        for argument in process.arguments[1:]
    )


def process_uses_managed_user_data(
    process: ChromeProcess, user_data_dir: Path, *, windows: bool = False
) -> bool:
    value = process_option(process, "--user-data-dir")
    return value is not None and same_path(value, user_data_dir, windows=windows)


def debug_processes_linux(user_data_dir: Path) -> list[ChromeProcess]:
    return [
        process
        for process in linux_chrome_processes()
        if process_uses_managed_user_data(process, user_data_dir)
    ]


def macos_process_arguments(command: str) -> list[str]:
    match = re.match(
        r"^(?P<executable>/.+?/Google Chrome\.app/Contents/"
        r"(?:MacOS/Google Chrome|Frameworks/"
        r"Google Chrome Framework\.framework/Versions/[^/]+/Helpers/"
        r"(?P<helper>Google Chrome Helper(?: \([^)]+\))?)\.app/"
        r"Contents/MacOS/(?P=helper)))"
        r"(?:\s+(?P<arguments>.*))?$",
        command,
    )
    if not match:
        return []
    executable = match.group("executable")
    trailing = match.group("arguments") or ""
    arguments = command_arguments(trailing) if trailing else []
    return [executable, *arguments]


def debug_processes_macos(user_data_dir: Path) -> list[ChromeProcess]:
    result = run_process_command(["ps", "-ax", "-o", "pid=", "-o", "command="])
    if result.returncode != 0:
        raise RuntimeError(f"Unable to inspect Chrome processes: ps exit {result.returncode}")

    processes: list[ChromeProcess] = []
    for line in result.stdout.splitlines():
        fields = line.strip().split(maxsplit=1)
        if len(fields) != 2:
            continue
        try:
            arguments = macos_process_arguments(fields[1])
            if not arguments:
                continue
            process = ChromeProcess(
                pid=int(fields[0]),
                executable=Path(arguments[0]),
                arguments=arguments,
            )
        except ValueError:
            continue
        if is_stable_chrome_executable(
            process.executable, platform="darwin"
        ) and process_uses_managed_user_data(process, user_data_dir):
            processes.append(process)
    return processes


def debug_processes_windows(user_data_dir: Path) -> list[ChromeProcess]:
    command = (
        "Get-CimInstance Win32_Process -Filter \"Name='chrome.exe'\" | "
        "Select-Object ProcessId,ExecutablePath,CommandLine | ConvertTo-Json -Compress"
    )
    result = run_process_command(
        ["powershell.exe", "-NoProfile", "-Command", command]
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"Unable to inspect Chrome processes: PowerShell exit {result.returncode}"
        )
    if not result.stdout.strip():
        return []
    try:
        payload: Any = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError("Unable to parse Chrome process information") from exc
    entries = payload if isinstance(payload, list) else [payload]
    processes: list[ChromeProcess] = []
    for entry in entries:
        if (
            not isinstance(entry, dict)
            or not isinstance(entry.get("CommandLine"), str)
            or not isinstance(entry.get("ExecutablePath"), str)
        ):
            continue
        process = ChromeProcess(
            pid=int(entry["ProcessId"]),
            executable=Path(entry["ExecutablePath"]),
            arguments=command_arguments(entry["CommandLine"], windows=True),
        )
        if is_stable_chrome_executable(
            process.executable, platform="win32"
        ) and process_uses_managed_user_data(
            process, user_data_dir, windows=True
        ):
            processes.append(process)
    return processes


def debug_processes(user_data_dir: Path) -> list[ChromeProcess]:
    if sys.platform == "darwin":
        return debug_processes_macos(user_data_dir)
    if sys.platform.startswith("linux"):
        return debug_processes_linux(user_data_dir)
    if sys.platform == "win32":
        return debug_processes_windows(user_data_dir)
    raise RuntimeError(f"Unsupported platform: {sys.platform}")


def debug_process_ids(user_data_dir: Path) -> list[int]:
    """Return every Chrome process using the managed user-data directory."""
    return [process.pid for process in debug_processes(user_data_dir)]


def debug_main_process_ids(user_data_dir: Path) -> list[int]:
    return [
        process.pid
        for process in debug_processes(user_data_dir)
        if process.process_type is None
    ]


def debug_cdp_main_process_ids(user_data_dir: Path) -> list[int]:
    return [
        process.pid
        for process in debug_processes(user_data_dir)
        if process.process_type is None
        and process_option(process, "--remote-debugging-port") == str(CDP_PORT)
    ]


def probe_cdp() -> str | None:
    try:
        with urllib.request.urlopen(f"{CDP_ENDPOINT}/json/version", timeout=1) as response:
            payload: Any = json.load(response)
    except (
        OSError,
        TimeoutError,
        urllib.error.URLError,
        json.JSONDecodeError,
    ):
        return None
    websocket = payload.get("webSocketDebuggerUrl") if isinstance(payload, dict) else None
    return websocket if isinstance(websocket, str) else None


def listener_process_ids_macos() -> list[int]:
    result = run_process_command(
        [
            "lsof",
            "-nP",
            f"-iTCP@{CDP_HOST}:{CDP_PORT}",
            "-sTCP:LISTEN",
            "-t",
        ]
    )
    if result.returncode not in (0, 1):
        raise RuntimeError(f"Unable to inspect CDP listener: lsof exit {result.returncode}")
    return [int(value) for value in result.stdout.split() if value.isdigit()]


def listener_process_ids_linux() -> list[int]:
    socket_inodes: set[str] = set()
    for table in (Path("/proc/net/tcp"), Path("/proc/net/tcp6")):
        try:
            lines = table.read_text(encoding="utf-8").splitlines()[1:]
        except OSError:
            continue
        for line in lines:
            fields = line.split()
            if len(fields) > 9 and fields[1].endswith(f":{CDP_PORT:04X}") and fields[3] == "0A":
                socket_inodes.add(fields[9])

    process_ids: list[int] = []
    try:
        process_directories = [entry for entry in Path("/proc").iterdir() if entry.name.isdigit()]
    except OSError as exc:
        raise RuntimeError(f"Unable to inspect CDP listener processes: {exc}") from exc
    for process_directory in process_directories:
        pid = int(process_directory.name)
        try:
            descriptors = (process_directory / "fd").iterdir()
            if any(
                descriptor.is_symlink()
                and descriptor.readlink().as_posix().removeprefix("socket:[")
                .removesuffix("]") in socket_inodes
                for descriptor in descriptors
            ):
                process_ids.append(pid)
        except OSError:
            continue
    return process_ids


def listener_process_ids_windows() -> list[int]:
    command = (
        f"Get-NetTCPConnection -LocalAddress '{CDP_HOST}' -LocalPort {CDP_PORT} "
        "-State Listen -ErrorAction SilentlyContinue | "
        "Select-Object -ExpandProperty OwningProcess | ConvertTo-Json -Compress"
    )
    result = run_process_command(["powershell.exe", "-NoProfile", "-Command", command])
    if result.returncode != 0 or not result.stdout.strip():
        return []
    try:
        payload: Any = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError("Unable to parse CDP listener information") from exc
    values = payload if isinstance(payload, list) else [payload]
    return [int(value) for value in values]


def listener_process_ids() -> list[int]:
    if sys.platform == "darwin":
        return listener_process_ids_macos()
    if sys.platform.startswith("linux"):
        return listener_process_ids_linux()
    if sys.platform == "win32":
        return listener_process_ids_windows()
    raise RuntimeError(f"Unsupported platform: {sys.platform}")


def port_is_in_use() -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as connection:
        connection.settimeout(0.5)
        return connection.connect_ex((CDP_HOST, CDP_PORT)) == 0


def status(user_data_dir: Path = DEFAULT_USER_DATA_DIR) -> DebugStatus:
    root = user_data_dir.expanduser().absolute()
    profile_directory: str | None
    try:
        profile_directory = load_profile_directory(root)
    except RuntimeError:
        profile_directory = None

    processes = debug_processes(root)
    process_ids = [process.pid for process in processes]
    cdp_main_processes = [
        process
        for process in processes
        if process.process_type is None
        and process_option(process, "--remote-debugging-port") == str(CDP_PORT)
    ]
    cdp_process_ids = [process.pid for process in cdp_main_processes]
    cdp_modes = {
        "headless" if process_has_option(process, "--headless") else "headed"
        for process in cdp_main_processes
    }
    websocket = probe_cdp()
    cdp_owned = bool(
        websocket and set(cdp_process_ids).intersection(listener_process_ids())
    )
    if cdp_owned:
        state = "running"
    elif process_ids or websocket:
        state = "broken"
    else:
        state = "stopped"
    return DebugStatus(
        state=state,
        mode=next(iter(cdp_modes)) if cdp_owned and len(cdp_modes) == 1 else None,
        endpoint=CDP_ENDPOINT,
        user_data_dir=str(root),
        profile_directory=profile_directory,
        process_ids=process_ids,
    )


def start(
    user_data_dir: Path = DEFAULT_USER_DATA_DIR, *, headless: bool = False
) -> DebugStatus:
    root = user_data_dir.expanduser().absolute()
    profile_directory = load_profile_directory(root)
    current = status(root)
    if current.state == "running":
        requested_mode = "headless" if headless else "headed"
        if current.mode != requested_mode:
            current_mode = current.mode or "unknown"
            raise RuntimeError(
                f"Debug Chrome is already running in {current_mode} mode; "
                f"stop it before starting in {requested_mode} mode"
            )
        return current
    if current.process_ids:
        raise RuntimeError("Debug Chrome process exists but CDP is not available")
    if port_is_in_use():
        raise RuntimeError(f"Port {CDP_PORT} is already used by another process")

    command = build_launch_command(
        chrome_executable(), root, profile_directory, headless=headless
    )
    popen_options: dict[str, Any] = {
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
        "close_fds": True,
    }
    if sys.platform == "win32":
        popen_options["creationflags"] = (
            subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
        )
    else:
        popen_options["start_new_session"] = True
    launched_process = subprocess.Popen(command, **popen_options)

    deadline = time.monotonic() + START_TIMEOUT
    while time.monotonic() < deadline:
        current = status(root)
        if current.state == "running":
            return current
        time.sleep(0.25)
    if debug_process_ids(root):
        close_owned_debug_processes(root)
    elif launched_process.poll() is None:
        launched_process.terminate()
    if not wait_for_debug_exit(root, timeout=STOP_TIMEOUT):
        raise RuntimeError(
            "Debug Chrome failed to start and its processes did not exit during cleanup"
        )
    raise RuntimeError("Debug Chrome did not expose CDP within 15 seconds")


def send_browser_close(websocket_url: str) -> None:
    parsed = urllib.parse.urlparse(websocket_url)
    if (
        parsed.scheme != "ws"
        or parsed.hostname != CDP_HOST
        or parsed.port != CDP_PORT
        or not parsed.path.startswith("/devtools/browser/")
    ):
        raise RuntimeError("Refusing to close Chrome through an unexpected CDP endpoint")
    port = parsed.port
    path = parsed.path or "/"
    if parsed.query:
        path = f"{path}?{parsed.query}"

    key = base64.b64encode(os.urandom(16)).decode("ascii")
    with socket.create_connection((parsed.hostname, port), timeout=2) as connection:
        request = (
            f"GET {path} HTTP/1.1\r\n"
            f"Host: {parsed.hostname}:{port}\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {key}\r\n"
            "Sec-WebSocket-Version: 13\r\n\r\n"
        )
        connection.sendall(request.encode("ascii"))
        response = b""
        while b"\r\n\r\n" not in response and len(response) < 8192:
            chunk = connection.recv(1024)
            if not chunk:
                break
            response += chunk
        if not response.startswith(b"HTTP/1.1 101"):
            raise RuntimeError("Chrome rejected the CDP WebSocket connection")

        payload = b'{"id":1,"method":"Browser.close"}'
        mask = os.urandom(4)
        masked_payload = bytes(
            value ^ mask[index % 4] for index, value in enumerate(payload)
        )
        connection.sendall(
            bytes((0x81, 0x80 | len(payload))) + mask + masked_payload
        )


def request_cdp_close(user_data_dir: Path) -> bool:
    websocket = probe_cdp()
    if not websocket:
        return False
    owned_processes = set(debug_cdp_main_process_ids(user_data_dir))
    if not owned_processes.intersection(listener_process_ids()):
        return False
    try:
        send_browser_close(websocket)
    except (OSError, RuntimeError):
        return False
    return True


def request_debug_close(user_data_dir: Path, process_ids: list[int]) -> None:
    if sys.platform == "win32":
        pid_list = ",".join(str(pid) for pid in process_ids)
        command = (
            f"$ids = @({pid_list}); "
            "Get-Process -Id $ids -ErrorAction SilentlyContinue | "
            "ForEach-Object { $null = $_.CloseMainWindow() }"
        )
        result = run_process_command(
            ["powershell.exe", "-NoProfile", "-Command", command]
        )
        if result.returncode != 0:
            raise RuntimeError(
                f"Unable to stop Debug Chrome: PowerShell exit {result.returncode}"
            )
        return

    for pid in process_ids:
        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            continue
        except OSError as exc:
            raise RuntimeError(f"Unable to stop Debug Chrome: {exc}") from exc


def force_debug_close_windows(user_data_dir: Path) -> None:
    process_ids = debug_process_ids(user_data_dir)
    if not process_ids:
        return
    pid_list = ",".join(str(pid) for pid in process_ids)
    command = (
        f"$ids = @({pid_list}); "
        "Get-Process -Id $ids -ErrorAction SilentlyContinue | "
        "Stop-Process -Force -ErrorAction Stop"
    )
    result = run_process_command(
        ["powershell.exe", "-NoProfile", "-Command", command]
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"Unable to force stop Debug Chrome: PowerShell exit {result.returncode}"
        )


def wait_for_debug_exit(user_data_dir: Path, timeout: float = STOP_TIMEOUT) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not debug_process_ids(user_data_dir):
            return True
        time.sleep(0.25)
    return not debug_process_ids(user_data_dir)


def close_owned_debug_processes(user_data_dir: Path) -> bool:
    if not debug_process_ids(user_data_dir):
        return True

    if request_cdp_close(user_data_dir) and wait_for_debug_exit(
        user_data_dir, timeout=GRACEFUL_STOP_TIMEOUT
    ):
        return True

    main_process_ids = debug_main_process_ids(user_data_dir)
    all_process_ids = debug_process_ids(user_data_dir)
    request_debug_close(user_data_dir, main_process_ids or all_process_ids)
    fallback_timeout = (
        GRACEFUL_STOP_TIMEOUT if sys.platform == "win32" else STOP_TIMEOUT
    )
    if wait_for_debug_exit(user_data_dir, timeout=fallback_timeout):
        return True

    if sys.platform == "win32":
        force_debug_close_windows(user_data_dir)
        return wait_for_debug_exit(user_data_dir, timeout=STOP_TIMEOUT)
    return False


def stop(user_data_dir: Path = DEFAULT_USER_DATA_DIR) -> DebugStatus:
    root = user_data_dir.expanduser().absolute()
    if not debug_process_ids(root):
        return status(root)

    if close_owned_debug_processes(root):
        return status(root)
    raise RuntimeError("Debug Chrome did not exit during cleanup")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Manage the persistent Chrome CDP debug instance."
    )
    parser.add_argument(
        "command",
        choices=("start", "stop", "status", "profiles", "select"),
    )
    parser.add_argument(
        "profile",
        nargs="?",
        help="Profile directory for the select command.",
    )
    parser.add_argument(
        "--user-data-dir",
        type=Path,
        default=DEFAULT_USER_DATA_DIR,
        help="Debug Chrome user-data directory (default: ~/chrome-debug-data).",
    )
    parser.add_argument("--json", action="store_true", help="Return JSON status.")
    parser.add_argument(
        "--headless",
        action="store_true",
        help="Start Debug Chrome without a visible window (start only).",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        if args.headless and args.command != "start":
            raise RuntimeError("--headless is only valid with the start command")
        if args.command == "start":
            result = start(args.user_data_dir, headless=args.headless)
        elif args.command == "stop":
            result = stop(args.user_data_dir)
        elif args.command == "profiles":
            root = args.user_data_dir.expanduser().absolute()
            selected = active_profile(root)
            profiles = copied_profiles(root)
            if args.json:
                print(
                    json.dumps(
                        {
                            "user_data_dir": str(root),
                            "active_profile": selected,
                            "profiles": [
                                {
                                    "directory": profile,
                                    "name": profile_display_name(root, profile),
                                    "active": profile == selected,
                                }
                                for profile in profiles
                            ],
                        },
                        ensure_ascii=False,
                        indent=2,
                    )
                )
                return 0
            for profile in profiles:
                marker = "*" if profile == selected else " "
                display_name = profile_display_name(root, profile)
                suffix = f"  {display_name}" if display_name != profile else ""
                print(f"{marker} {profile}{suffix}")
            return 0
        elif args.command == "select":
            if not args.profile:
                raise RuntimeError("select requires a profile directory")
            root = args.user_data_dir.expanduser().absolute()
            if debug_process_ids(root):
                raise RuntimeError("Stop Debug Chrome before changing profiles")
            select_profile(root, args.profile)
            print(f"Active Debug Chrome profile: {args.profile}")
            return 0
        else:
            result = status(args.user_data_dir)
    except (OSError, RuntimeError) as exc:
        print(f"chrome-cdp: {exc}", file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps(asdict(result), ensure_ascii=False, indent=2))
    else:
        print(f"Debug Chrome: {result.state}")
        if result.mode:
            print(f"Mode: {result.mode}")
        print(f"CDP endpoint: {result.endpoint}")
        print(f"User Data: {result.user_data_dir}")
        if result.profile_directory:
            print(f"Profile: {result.profile_directory}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
