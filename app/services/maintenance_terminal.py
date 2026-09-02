from __future__ import annotations

import shutil
import socket
import subprocess
import threading
import time

from app.ai_session.terminal import TerminalConnectionRegistry, TerminalTicketStore
from app.core.config import PROJECT_ROOT, Settings
from app.core.response import ApiError


class MaintenanceTerminalManager:
    """Own the transient ttyd bridge for the high-privilege maintenance shell."""

    terminal_id = "maintenance-terminal"
    mount_path = "/maintenance-terminal/terminal"

    def __init__(self, settings: Settings) -> None:
        self.tickets = TerminalTicketStore(settings.codex_pty.ticket_ttl_seconds)
        self.connections = TerminalConnectionRegistry()
        self._lock = threading.RLock()
        self._process: subprocess.Popen[bytes] | None = None
        self._port: int | None = None

    def open(self) -> str:
        """Replace the prior shell and issue one short-lived browser ticket."""
        with self._lock:
            self.tickets.revoke_session(self.terminal_id)
            self.connections.close_session(self.terminal_id)
            self._stop_backend()
            ttyd = shutil.which("ttyd")
            shell = shutil.which("zsh")
            if ttyd is None or shell is None:
                missing = ", ".join(
                    name
                    for name, path in (("ttyd", ttyd), ("zsh", shell))
                    if path is None
                )
                raise ApiError(
                    503,
                    "maintenance_terminal_unavailable",
                    f"维护终端不可用，缺少 {missing}。",
                )
            port = self._available_port()
            command = [
                ttyd,
                "-W",
                "-O",
                "-m",
                "1",
                "-i",
                "127.0.0.1",
                "-p",
                str(port),
                "-b",
                self.mount_path,
                shell,
            ]
            process = subprocess.Popen(
                command,
                cwd=PROJECT_ROOT,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
            try:
                self._wait_for_port(process, port)
            except Exception:
                self._terminate(process)
                raise
            self._process = process
            self._port = port
            return self.tickets.issue(self.terminal_id)

    def backend_url(self, path: str, query: str = "") -> str:
        port = self._backend_port()
        suffix = f"?{query}" if query else ""
        return f"http://127.0.0.1:{port}{self.mount_path}/{path}{suffix}"

    def backend_ws_url(self) -> str:
        return f"ws://127.0.0.1:{self._backend_port()}{self.mount_path}/ws"

    def backend_origin(self) -> str:
        return f"http://127.0.0.1:{self._backend_port()}"

    def close(self) -> None:
        with self._lock:
            self.tickets.revoke_session(self.terminal_id)
            self.connections.close_session(self.terminal_id)
            self._stop_backend()

    def _backend_port(self) -> int:
        with self._lock:
            if (
                self._process is None
                or self._process.poll() is not None
                or self._port is None
            ):
                raise ApiError(
                    503,
                    "maintenance_terminal_unavailable",
                    "维护终端后端不可用。",
                )
            return self._port

    def _stop_backend(self) -> None:
        process = self._process
        self._process = None
        self._port = None
        if process is not None:
            self._terminate(process)

    @staticmethod
    def _available_port() -> int:
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            return int(listener.getsockname()[1])

    @staticmethod
    def _wait_for_port(process: subprocess.Popen[bytes], port: int) -> None:
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            if process.poll() is not None:
                raise ApiError(
                    503,
                    "maintenance_terminal_failed",
                    "维护终端无法启动。",
                )
            try:
                with socket.create_connection(("127.0.0.1", port), timeout=0.1):
                    return
            except OSError:
                time.sleep(0.05)
        raise ApiError(
            503,
            "maintenance_terminal_timeout",
            "维护终端启动超时。",
        )

    @staticmethod
    def _terminate(process: subprocess.Popen[bytes]) -> None:
        if process.poll() is not None:
            return
        process.terminate()
        try:
            process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            process.kill()
