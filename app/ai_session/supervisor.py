from __future__ import annotations

import socket
import subprocess
import threading
import time
from collections.abc import Iterable

import psutil

from app.ai_runtime import RuntimeOperationError, RuntimeTerminalRequest
from app.ai_session.models import AiSession
from app.ai_session.terminal import TerminalConnectionRegistry, TerminalTicketStore
from app.core.response import ApiError


class InteractiveSupervisor:
    """Own the transient ttyd/tmux terminal lifecycle outside the Session Store."""

    def __init__(self, runtime_adapter, *, ticket_ttl_seconds: int = 600) -> None:
        self.runtime_adapter = runtime_adapter
        display_name = getattr(runtime_adapter, "display_name", None)
        descriptor = getattr(runtime_adapter, "descriptor", None)
        descriptor_id = getattr(descriptor, "runtime_id", None)
        self.runtime_name = (
            display_name
            if isinstance(display_name, str) and display_name
            else descriptor_id
            if isinstance(descriptor_id, str) and descriptor_id
            else "Runtime"
        )
        self.tickets = TerminalTicketStore(ticket_ttl_seconds)
        self.connections = TerminalConnectionRegistry()
        self._lock = threading.RLock()
        self._processes: dict[str, subprocess.Popen[bytes]] = {}
        self._ports: dict[str, int] = {}
        self._known_session_ids: set[str] = set()

    def reconcile_after_restart(self, sessions: Iterable[AiSession]) -> set[str]:
        """Stop stale Web-owned ttyd children and report still-running tmux Sessions."""
        running: set[str] = set()
        with self._lock:
            for session in sessions:
                self._known_session_ids.add(session.id)
                self._terminate_orphaned_backend(session.id)
                if self._tmux_running(session.id):
                    running.add(session.id)
        return running

    def ensure_terminal(self, session: AiSession, *, max_running: int) -> int:
        with self._lock:
            self._known_session_ids.add(session.id)
            existing = self._processes.get(session.id)
            port = self._ports.get(session.id)
            if existing is not None and existing.poll() is None and port is not None:
                return port
            if not self._tmux_running(session.id) and self.running_terminal_count() >= max_running:
                raise ApiError(
                    409,
                    "codex_session_limit",
                    f"Too many {self.runtime_name} terminals are running",
                )
            port = self._available_port()
            try:
                process_spec = self.runtime_adapter.terminal_command(
                    RuntimeTerminalRequest(
                        session_id=session.id,
                        cwd=session.cwd,
                        permission_mode=session.permission_mode,
                        native_session_id=session.native_session_id,
                        model=session.model,
                        reasoning_effort=session.reasoning_effort,
                    ),
                    port,
                )
                process = subprocess.Popen(
                    list(process_spec.argv),
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    start_new_session=True,
                )
                self._wait_for_port(process, port)
            except RuntimeOperationError as exc:
                raise self._runtime_api_error(exc) from exc
            self._processes[session.id] = process
            self._ports[session.id] = port
            return port

    def restart_terminal_backend(self, session: AiSession, *, max_running: int) -> int:
        with self._lock:
            self.stop_backend(session.id)
            return self.ensure_terminal(session, max_running=max_running)

    def stop_backend(self, session_id: str) -> None:
        with self._lock:
            process = self._processes.pop(session_id, None)
            self._ports.pop(session_id, None)
            if process is not None and process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    process.kill()
            else:
                self._terminate_orphaned_backend(session_id)

    def stop_terminal(self, session_id: str) -> None:
        with self._lock:
            self.tickets.revoke_session(session_id)
            self.connections.close_session(session_id)
            if self._tmux_available():
                subprocess.run(
                    ["tmux", "kill-session", "-t", self._tmux_name(session_id)],
                    check=False,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                if self._tmux_running(session_id):
                    raise ApiError(
                        503,
                        "codex_session_stop_failed",
                        f"{self.runtime_name} Session is still running",
                    )
            self.stop_backend(session_id)

    def backend_port(self, session_id: str) -> int:
        with self._lock:
            process = self._processes.get(session_id)
            port = self._ports.get(session_id)
            if process is None or process.poll() is not None or port is None:
                raise ApiError(
                    503,
                    "codex_terminal_failed",
                    f"{self.runtime_name} terminal backend is unavailable",
                )
            return port

    def is_terminal_running(self, session_id: str) -> bool:
        with self._lock:
            return self._tmux_running(session_id)

    def owns_terminal_writer(self, session_id: str) -> bool:
        """Identify a live terminal writer that was launched by Chub.

        A detached tmux session is the normal proof. If a browser disconnect
        tears down the tmux client first, the Codex child retains Chub's fixed
        Session marker; use that as the narrow recovery proof. Worker writers
        carry an explicit ``quick`` source and never qualify here.
        """
        with self._lock:
            if self._tmux_running(session_id):
                return True
            for process in psutil.process_iter(("cmdline",)):
                try:
                    environment = process.environ()
                    command = process.info.get("cmdline") or []
                except (psutil.AccessDenied, psutil.NoSuchProcess, OSError):
                    continue
                if environment.get("CHUB_PTY_SESSION_ID") != session_id:
                    continue
                if environment.get("CHUB_ACTIVITY_SOURCE") == "quick":
                    continue
                if self.runtime_adapter.runtime_process_matches(tuple(command)):
                    return True
            return False

    def running_terminal_count(self) -> int:
        if not self._tmux_available():
            return 0
        return sum(
            1
            for session_id in self._known_terminal_ids()
            if self._tmux_running(session_id)
        )

    def close(self) -> None:
        with self._lock:
            for session_id in tuple(self._processes):
                self.stop_backend(session_id)

    def _known_terminal_ids(self) -> tuple[str, ...]:
        return tuple(self._known_session_ids)

    @staticmethod
    def _tmux_name(session_id: str) -> str:
        return f"chub-{session_id}"

    @staticmethod
    def _tmux_available() -> bool:
        import shutil

        return shutil.which("tmux") is not None

    def _tmux_running(self, session_id: str) -> bool:
        if not self._tmux_available():
            return False
        return (
            subprocess.run(
                ["tmux", "has-session", "-t", self._tmux_name(session_id)],
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            ).returncode
            == 0
        )

    @staticmethod
    def _available_port() -> int:
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            return int(listener.getsockname()[1])

    def _wait_for_port(self, process: subprocess.Popen[bytes], port: int) -> None:
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            if process.poll() is not None:
                raise ApiError(
                    503,
                    "codex_terminal_failed",
                    f"Unable to start {self.runtime_name} terminal",
                )
            try:
                with socket.create_connection(("127.0.0.1", port), timeout=0.1):
                    return
            except OSError:
                time.sleep(0.05)
        process.terminate()
        raise ApiError(
            503,
            "codex_terminal_timeout",
            f"{self.runtime_name} terminal did not become ready",
        )

    @staticmethod
    def _runtime_api_error(error: RuntimeOperationError) -> ApiError:
        status_code = {
            "invalid_request": 400,
            "conflict": 409,
            "unavailable": 503,
        }[error.kind]
        return ApiError(status_code, error.code, error.message, source="runtime")

    @staticmethod
    def _terminate_process(process: psutil.Process) -> None:
        try:
            process.terminate()
            process.wait(timeout=3)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            return
        except psutil.TimeoutExpired:
            try:
                process.kill()
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                return

    def _terminate_orphaned_backend(self, session_id: str) -> None:
        for process in psutil.process_iter(("pid", "name", "cmdline")):
            try:
                command = process.info.get("cmdline") or []
                command = tuple(command)
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
            if not self.runtime_adapter.terminal_backend_matches(command, session_id):
                continue
            self._terminate_process(process)
