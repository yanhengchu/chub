from __future__ import annotations

import argparse
import json
import os
import signal
import socket
import stat
from dataclasses import asdict, dataclass
from pathlib import Path
from threading import Event, Lock
from typing import Any

from app.core.config import load_settings


SOCKET_NAME = "debug-chrome-supervisor.sock"
MAX_REQUEST_BYTES = 4096
MAX_RESPONSE_BYTES = 64 * 1024
SOCKET_TIMEOUT_SECONDS = 20.0
ALLOWED_ACTIONS = frozenset({"status", "start", "stop"})
ALLOWED_MODES = frozenset({"headed", "headless"})


@dataclass(frozen=True)
class ChromeStatusSnapshot:
    state: str
    mode: str | None
    endpoint: str
    user_data_dir: str
    profile_directory: str | None
    process_ids: list[int]


class ChromeSupervisorError(RuntimeError):
    pass


def socket_path(runtime_dir: Path) -> Path:
    return runtime_dir / SOCKET_NAME


def _status_snapshot(current: Any) -> ChromeStatusSnapshot:
    return ChromeStatusSnapshot(
        state=str(current.state),
        mode=current.mode if isinstance(current.mode, str) else None,
        endpoint=str(current.endpoint),
        user_data_dir=str(current.user_data_dir),
        profile_directory=(
            current.profile_directory
            if isinstance(current.profile_directory, str)
            else None
        ),
        process_ids=[
            int(process_id)
            for process_id in current.process_ids
            if isinstance(process_id, int) and not isinstance(process_id, bool)
        ],
    )


def _chrome_debug_module():
    from app.automations.browser import _chrome_debug_module as load_module

    return load_module()


def _execute(request: dict[str, Any]) -> ChromeStatusSnapshot:
    action = request.get("action")
    if action not in ALLOWED_ACTIONS:
        raise ChromeSupervisorError("unsupported browser action")
    if action == "start":
        mode = request.get("mode")
        if mode not in ALLOWED_MODES:
            raise ChromeSupervisorError("unsupported browser mode")
        current = _chrome_debug_module().start(headless=mode == "headless")
    elif action == "stop":
        current = _chrome_debug_module().stop()
    else:
        current = _chrome_debug_module().status()
    return _status_snapshot(current)


def _validate_socket_parent(path: Path) -> None:
    if not path.is_absolute() or path.name != SOCKET_NAME:
        raise ChromeSupervisorError("browser supervisor socket path is invalid")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.parent.chmod(0o700)
    metadata = path.parent.stat()
    if metadata.st_uid != os.getuid() or stat.S_IMODE(metadata.st_mode) & 0o077:
        raise ChromeSupervisorError("browser supervisor socket directory is unsafe")


def _prepare_socket(path: Path) -> None:
    _validate_socket_parent(path)
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        return
    if not stat.S_ISSOCK(metadata.st_mode):
        raise ChromeSupervisorError("browser supervisor socket is not a socket")
    if metadata.st_uid != os.getuid() or stat.S_IMODE(metadata.st_mode) & 0o077:
        raise ChromeSupervisorError("browser supervisor socket is unsafe")
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as probe:
            probe.settimeout(0.5)
            probe.connect(str(path))
    except OSError:
        path.unlink()
    else:
        raise ChromeSupervisorError("browser supervisor is already running")


def _read_request(connection: socket.socket) -> dict[str, Any]:
    payload = bytearray()
    while len(payload) <= MAX_REQUEST_BYTES:
        chunk = connection.recv(min(1024, MAX_REQUEST_BYTES + 1 - len(payload)))
        if not chunk:
            break
        payload.extend(chunk)
        if b"\n" in chunk:
            break
    if len(payload) > MAX_REQUEST_BYTES or b"\n" not in payload:
        raise ChromeSupervisorError("browser supervisor request is invalid")
    line = bytes(payload).split(b"\n", 1)[0]
    try:
        request = json.loads(line.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ChromeSupervisorError("browser supervisor request is invalid") from exc
    if not isinstance(request, dict):
        raise ChromeSupervisorError("browser supervisor request is invalid")
    return request


def _serve_connection(connection: socket.socket, lock: Lock) -> None:
    connection.settimeout(SOCKET_TIMEOUT_SECONDS)
    try:
        try:
            request = _read_request(connection)
            with lock:
                result = _execute(request)
            response: dict[str, Any] = {"ok": True, "data": asdict(result)}
        except Exception as exc:
            response = {
                "ok": False,
                "error": str(exc)[:512] or "browser supervisor request failed",
            }
        encoded = (json.dumps(response, separators=(",", ":")) + "\n").encode(
            "utf-8"
        )
        if len(encoded) > MAX_RESPONSE_BYTES:
            encoded = b'{"ok":false,"error":"browser supervisor response too large"}\n'
        connection.sendall(encoded)
    except OSError:
        return


def serve(socket_file: Path) -> None:
    _prepare_socket(socket_file)
    stopping = Event()

    def request_stop(_signum: int, _frame: Any) -> None:
        stopping.set()

    previous_term = signal.signal(signal.SIGTERM, request_stop)
    previous_int = signal.signal(signal.SIGINT, request_stop)
    listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    lock = Lock()
    try:
        listener.bind(str(socket_file))
        socket_file.chmod(0o600)
        listener.listen(8)
        listener.settimeout(0.5)
        while not stopping.is_set():
            try:
                connection, _ = listener.accept()
            except socket.timeout:
                continue
            with connection:
                _serve_connection(connection, lock)
    finally:
        listener.close()
        try:
            socket_file.unlink()
        except FileNotFoundError:
            pass
        try:
            current = _chrome_debug_module().status()
            if current.state in {"running", "broken"}:
                _chrome_debug_module().stop()
        except Exception:
            pass
        signal.signal(signal.SIGTERM, previous_term)
        signal.signal(signal.SIGINT, previous_int)


def request(socket_file: Path, action: str, *, mode: str | None = None) -> ChromeStatusSnapshot:
    if action not in ALLOWED_ACTIONS:
        raise ChromeSupervisorError("unsupported browser action")
    payload: dict[str, Any] = {"action": action}
    if mode is not None:
        payload["mode"] = mode
    encoded = (json.dumps(payload, separators=(",", ":")) + "\n").encode("utf-8")
    if len(encoded) > MAX_REQUEST_BYTES:
        raise ChromeSupervisorError("browser supervisor request is too large")
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
            connection.settimeout(SOCKET_TIMEOUT_SECONDS)
            connection.connect(str(socket_file))
            connection.sendall(encoded)
            response = bytearray()
            while len(response) <= MAX_RESPONSE_BYTES:
                chunk = connection.recv(min(4096, MAX_RESPONSE_BYTES + 1 - len(response)))
                if not chunk:
                    break
                response.extend(chunk)
                if b"\n" in chunk:
                    break
    except OSError as exc:
        raise ChromeSupervisorError("browser supervisor is unavailable") from exc
    if len(response) > MAX_RESPONSE_BYTES or b"\n" not in response:
        raise ChromeSupervisorError("browser supervisor response is invalid")
    try:
        result = json.loads(bytes(response).split(b"\n", 1)[0].decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ChromeSupervisorError("browser supervisor response is invalid") from exc
    if not isinstance(result, dict) or result.get("ok") is not True:
        message = result.get("error") if isinstance(result, dict) else None
        raise ChromeSupervisorError(
            message if isinstance(message, str) and message else "browser supervisor request failed"
        )
    data = result.get("data")
    if not isinstance(data, dict):
        raise ChromeSupervisorError("browser supervisor response is invalid")
    try:
        return ChromeStatusSnapshot(
            state=str(data["state"]),
            mode=data["mode"] if isinstance(data["mode"], str) else None,
            endpoint=str(data["endpoint"]),
            user_data_dir=str(data["user_data_dir"]),
            profile_directory=(
                data["profile_directory"]
                if isinstance(data["profile_directory"], str)
                else None
            ),
            process_ids=[int(value) for value in data["process_ids"]],
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise ChromeSupervisorError("browser supervisor response is invalid") from exc


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("serve",))
    args = parser.parse_args()
    settings = load_settings()
    serve(socket_path(settings.automations.runtime_dir))


if __name__ == "__main__":
    main()
