from __future__ import annotations

import json
import logging
import os
import shutil
import socket
import subprocess
import threading
import time
import tomllib
import uuid
from datetime import datetime
from pathlib import Path
from typing import Callable

import psutil

from app.codex.discovery import CodexSessionDiscovery
from app.codex.models import (
    ActivitySource,
    CodexSession,
    PermissionMode,
    SessionInfo,
    TurnActivity,
    WorkspaceInfo,
    utc_now,
)
from app.codex.store import CodexSessionStore
from app.core.config import PROJECT_ROOT, Settings
from app.core.network import is_tailscale_ip
from app.core.response import ApiError


LOGGER = logging.getLogger("hub.codex")
PROFILE_MARKER = "# Managed by Chub Codex PTY"


class CodexPtyManager:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.store = CodexSessionStore(settings.codex_pty.data_file)
        self.codex_home = Path(os.environ.get("CODEX_HOME", Path.home() / ".codex"))
        self.discovery = CodexSessionDiscovery(self.codex_home)
        self.hook_dir = settings.codex_pty.runtime_dir / "hooks"
        self._processes: dict[str, subprocess.Popen[bytes]] = {}
        self._lock = threading.RLock()
        self._quick_interaction_is_running: Callable[[str], bool] = lambda _id: False
        self._reconcile_saved_backends()

    def set_quick_interaction_checker(
        self,
        checker: Callable[[str], bool],
    ) -> None:
        self._quick_interaction_is_running = checker

    @property
    def network_available(self) -> bool:
        return is_tailscale_ip(self.settings.server.host)

    def dependencies(self) -> dict[str, bool]:
        return {
            name: shutil.which(name) is not None
            for name in ("codex", "ttyd", "tmux")
        }

    def available(self) -> bool:
        return (
            self.settings.codex_pty.enabled
            and self.network_available
            and all(self.dependencies().values())
        )

    def unavailable_reason(self) -> str | None:
        if not self.settings.codex_pty.enabled:
            return "Codex PTY is disabled"
        if not self.network_available:
            return "Codex PTY requires a Tailscale listen address"
        missing = [name for name, found in self.dependencies().items() if not found]
        if missing:
            return f"Missing dependencies: {', '.join(missing)}"
        return None

    def workspaces(self) -> list[WorkspaceInfo]:
        entries = [
            ("home", "用户目录", Path.home()),
            ("workspace", "Workspace", self.settings.codex_pty.workspace),
            ("chub", "Chub", PROJECT_ROOT),
        ]
        return [
            WorkspaceInfo(
                id=workspace_id,
                name=name,
                path=str(path),
                available=path.is_dir(),
            )
            for workspace_id, name, path in entries
        ]

    def list_sessions(self) -> list[SessionInfo]:
        self._consume_hook_results()
        self._sync_native_sessions()
        sessions = self.store.list()
        for session in sessions:
            self._refresh_status(session)
            self._reconcile_quick_activity(session)
        return [self._public(session) for session in self.store.list()]

    def get_session(self, session_id: str) -> CodexSession:
        self._consume_hook_result(session_id)
        self._sync_native_sessions()
        session = self.store.get(session_id)
        if session is None:
            raise ApiError(404, "codex_session_not_found", "Codex session not found")
        self._refresh_status(session)
        self._reconcile_quick_activity(session)
        return session

    def _reconcile_quick_activity(self, session: CodexSession) -> None:
        if (
            session.activity_source != "quick"
            or self._quick_interaction_is_running(session.id)
        ):
            return
        session.activity = "idle" if session.status == "stopped" else "unknown"
        session.activity_source = "none"
        session.updated_at = utc_now()
        self.store.save(session)

    def create_session(
        self,
        workspace_id: str,
        permission_mode: PermissionMode = "full-access",
    ) -> SessionInfo:
        self._require_available()
        workspace = next(
            (item for item in self.workspaces() if item.id == workspace_id),
            None,
        )
        if workspace is None or not workspace.available:
            raise ApiError(
                400,
                "codex_workspace_unavailable",
                "Selected workspace is unavailable",
            )
        session = CodexSession(
            id=str(uuid.uuid4()),
            workspace_id=workspace.id,
            workspace_name=workspace.name,
            cwd=Path(workspace.path),
            permission_mode=permission_mode,
        )
        self.store.save(session)
        return self._public(session)

    def prepare_quick_interaction(self) -> None:
        """Ensure headless Codex runs use the managed profile and session hook."""
        self._require_available()
        with self._lock:
            self._ensure_profile()

    def ensure_terminal(self, session_id: str) -> CodexSession:
        self._require_available()
        with self._lock:
            session = self.get_session(session_id)
            tmux_was_running = session.status == "running"
            process = self._processes.get(session.id)
            if process is not None and process.poll() is None and session.ttyd_port:
                return session
            if (
                session.status != "running"
                and self._running_tmux_count()
                >= self.settings.codex_pty.max_running
            ):
                raise ApiError(
                    409,
                    "codex_session_limit",
                    "Too many Codex terminals are running",
                )
            self._ensure_profile()
            port = self._available_port()
            command = self._ttyd_command(session, port)
            try:
                process = subprocess.Popen(
                    command,
                    cwd=PROJECT_ROOT,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    start_new_session=True,
                )
                self._wait_for_port(process, port)
            except Exception:
                if not tmux_was_running:
                    session.status = "error"
                session.error = "terminal_backend_failed"
                session.updated_at = utc_now()
                self.store.save(session)
                raise
            self._processes[session.id] = process
            session.ttyd_pid = process.pid
            session.ttyd_port = port
            session.status = "running"
            if not tmux_was_running:
                session.activity = "unknown"
                session.activity_source = "none"
                session.active_permission_mode = session.permission_mode
            session.error = None
            session.updated_at = utc_now()
            self.store.save(session)
            return session

    def restart_terminal_backend(self, session_id: str) -> CodexSession:
        """Recycle ttyd while preserving the tmux session and Codex process."""
        self._require_available()
        with self._lock:
            session = self.get_session(session_id)
            self._stop_backend(session)
            return self.ensure_terminal(session_id)

    def stop_session(self, session_id: str) -> SessionInfo:
        with self._lock:
            session = self.get_session(session_id)
            if shutil.which("tmux"):
                subprocess.run(
                    ["tmux", "kill-session", "-t", self._tmux_name(session.id)],
                    check=False,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                still_running = subprocess.run(
                    ["tmux", "has-session", "-t", self._tmux_name(session.id)],
                    check=False,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                if still_running.returncode == 0:
                    raise ApiError(
                        503,
                        "codex_session_stop_failed",
                        "Codex session is still running",
                    )
            self._stop_backend(session)
            session.status = "stopped"
            session.activity = "idle"
            session.activity_source = "none"
            session.active_permission_mode = None
            session.error = None
            session.updated_at = utc_now()
            self.store.save(session)
            return self._public(session)

    def update_session_timestamp(self, session_id: str, updated_at: datetime) -> None:
        with self._lock:
            session = self.store.get(session_id)
            if session is None or session.updated_at >= updated_at:
                return
            session.updated_at = updated_at
            self.store.save(session)

    def set_activity(
        self,
        session_id: str,
        activity: TurnActivity,
        source: ActivitySource,
        *,
        updated_at: datetime | None = None,
    ) -> None:
        if (activity == "working") != (source != "none"):
            raise ValueError("Working activity requires a source; other activity must not have one")
        with self._lock:
            session = self.store.get(session_id)
            if session is None:
                raise ApiError(404, "codex_session_not_found", "Codex session not found")
            session.activity = activity
            session.activity_source = source
            session.updated_at = max(session.updated_at, updated_at or utc_now())
            self.store.save(session)

    def set_initial_quick_interaction_title(
        self,
        session_id: str,
        title: str,
    ) -> None:
        """Keep a useful local title while Codex creates its native session."""
        with self._lock:
            session = self.store.get(session_id)
            if session is None or session.codex_session_id or session.title:
                return
            session.title = title
            session.updated_at = utc_now()
            self.store.save(session)

    def recover_interrupted_quick_interaction(self, session_id: str) -> None:
        with self._lock:
            session = self.store.get(session_id)
            if session is None or session.activity_source != "quick":
                return
            session.activity = "unknown" if session.status == "running" else "idle"
            session.activity_source = "none"
            session.updated_at = utc_now()
            self.store.save(session)

    def delete_session(self, session_id: str) -> None:
        session = self.get_session(session_id)
        self.stop_session(session_id)
        if session.codex_session_id:
            result = subprocess.run(
                ["codex", "delete", "--force", session.codex_session_id],
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                text=True,
            )
            if result.returncode != 0:
                raise ApiError(
                    503,
                    "codex_session_delete_failed",
                    "Unable to delete Codex session",
                )
        self.store.delete(session_id)
        hook_file = self.hook_dir / f"{session_id}.json"
        try:
            hook_file.unlink()
        except FileNotFoundError:
            pass

    def archive_session(self, session_id: str) -> None:
        session = self.get_session(session_id)
        if not session.codex_session_id:
            raise ApiError(
                409,
                "codex_session_not_started",
                "Codex session has not started yet",
            )
        self.stop_session(session_id)
        result = subprocess.run(
            ["codex", "archive", session.codex_session_id],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
        )
        if result.returncode != 0:
            raise ApiError(
                503,
                "codex_session_archive_failed",
                "Unable to archive Codex session",
            )
        self.store.delete(session_id)
        hook_file = self.hook_dir / f"{session_id}.json"
        try:
            hook_file.unlink()
        except FileNotFoundError:
            pass

    def backend_url(self, session_id: str, path: str, query: str = "") -> str:
        session = self.ensure_terminal(session_id)
        suffix = f"?{query}" if query else ""
        return (
            f"http://127.0.0.1:{session.ttyd_port}"
            f"/codex/{session.id}/terminal/{path}{suffix}"
        )

    def backend_ws_url(self, session_id: str) -> str:
        session = self.ensure_terminal(session_id)
        return (
            f"ws://127.0.0.1:{session.ttyd_port}"
            f"/codex/{session.id}/terminal/ws"
        )

    def close(self) -> None:
        with self._lock:
            for session in self.store.list():
                self._stop_backend(session)

    def _require_available(self) -> None:
        reason = self.unavailable_reason()
        if reason:
            raise ApiError(503, "codex_pty_unavailable", reason)

    def _public(self, session: CodexSession) -> SessionInfo:
        return SessionInfo(
            id=session.id,
            workspace_id=session.workspace_id,
            workspace_name=session.workspace_name,
            cwd=str(session.cwd),
            title=session.title,
            codex_session_id=session.codex_session_id,
            status=session.status,
            activity=session.activity,
            activity_source=session.activity_source,
            permission_mode=session.permission_mode,
            active_permission_mode=session.active_permission_mode,
            permission_pending=(
                session.status == "running"
                and session.permission_mode
                != (session.active_permission_mode or "ask")
            ),
            error=session.error,
            created_at=session.created_at,
            updated_at=session.updated_at,
        )

    def _consume_hook_results(self) -> None:
        for session in self.store.list():
            self._consume_hook_result(session.id)

    def _consume_hook_result(self, session_id: str) -> None:
        target = self.hook_dir / f"{session_id}.json"
        try:
            payload = json.loads(target.read_text(encoding="utf-8"))
        except (FileNotFoundError, OSError, json.JSONDecodeError):
            return
        session = self.store.get(session_id)
        codex_session_id = payload.get("codex_session_id")
        activity = payload.get("activity")
        activity_source = payload.get("activity_source", "terminal")
        changed = False
        if session and isinstance(codex_session_id, str) and codex_session_id:
            if session.codex_session_id != codex_session_id:
                session.codex_session_id = codex_session_id
                changed = True
        if session and activity in {"working", "idle"}:
            expected_source = (
                activity_source
                if activity == "working" and activity_source in {"terminal", "quick"}
                else "none"
            )
            if (
                activity == "working"
                and expected_source == "quick"
                and not self._quick_interaction_is_running(session_id)
            ):
                activity = "idle" if session.status == "stopped" else "unknown"
                expected_source = "none"
            if (
                session.activity != activity
                or session.activity_source != expected_source
            ):
                session.activity = activity
                session.activity_source = expected_source
                changed = True
        if session and changed:
            session.updated_at = utc_now()
            self.store.save(session)
        try:
            target.unlink()
        except FileNotFoundError:
            pass

    def _refresh_status(self, session: CodexSession) -> None:
        if not shutil.which("tmux"):
            status = "stopped" if session.codex_session_id else "new"
            if session.status != status:
                session.status = status
                session.updated_at = utc_now()
                self.store.save(session)
            return
        tmux = subprocess.run(
            ["tmux", "has-session", "-t", self._tmux_name(session.id)],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        if session.status == "error" and tmux.returncode != 0:
            return
        status = "running" if tmux.returncode == 0 else (
            "stopped" if session.codex_session_id else "new"
        )
        if session.status != status:
            session.status = status
            session.updated_at = utc_now()
            self.store.save(session)

    def _ensure_profile(self) -> None:
        profile = self.codex_home / "chub.config.toml"
        hook = PROJECT_ROOT / "scripts" / "chub-codex-hook"
        content = (
            f"{PROFILE_MARKER}\n"
            "[[hooks.SessionStart]]\n"
            'matcher = "startup|resume"\n\n'
            "[[hooks.SessionStart.hooks]]\n"
            'type = "command"\n'
            f"command = {json.dumps(str(hook))}\n"
            "timeout = 5\n"
            f"{self._activity_hook_content(hook)}"
        )
        if profile.exists():
            existing = profile.read_text(encoding="utf-8")
            if existing == content or (
                self._profile_has_managed_hook(existing, hook)
                and self._profile_has_activity_hooks(existing, hook)
            ):
                return
            if PROFILE_MARKER not in existing.splitlines():
                raise ApiError(
                    409,
                    "codex_profile_conflict",
                    f"Existing Codex profile is not managed by Chub: {profile}",
                )
            if not self._profile_has_managed_hook(existing, hook):
                raise ApiError(
                    409,
                    "codex_profile_conflict",
                    f"Existing Codex profile is not managed by Chub: {profile}",
                )
            missing_hooks = "".join(
                self._event_hook_content(event, hook)
                for event in ("UserPromptSubmit", "Stop")
                if not self._profile_has_event_hook(existing, hook, event)
            )
            content = f"{existing.rstrip()}\n{missing_hooks}"
        self.codex_home.mkdir(parents=True, exist_ok=True)
        temporary = profile.with_suffix(".tmp")
        temporary.write_text(content, encoding="utf-8")
        temporary.chmod(0o600)
        temporary.replace(profile)

    @staticmethod
    def _profile_has_managed_hook(existing: str, hook: Path) -> bool:
        if PROFILE_MARKER not in existing.splitlines():
            return False
        try:
            profile = tomllib.loads(existing)
            session_start = profile["hooks"]["SessionStart"]
        except (KeyError, TypeError, tomllib.TOMLDecodeError):
            return False
        if not isinstance(session_start, list):
            return False
        return any(
            entry.get("matcher") == "startup|resume"
            and any(
                hook_entry.get("type") == "command"
                and hook_entry.get("command") == str(hook)
                for hook_entry in entry.get("hooks", [])
                if isinstance(hook_entry, dict)
            )
            for entry in session_start
            if isinstance(entry, dict)
        )

    @staticmethod
    def _activity_hook_content(hook: Path) -> str:
        return "".join(
            CodexPtyManager._event_hook_content(event, hook)
            for event in ("UserPromptSubmit", "Stop")
        )

    @staticmethod
    def _event_hook_content(event: str, hook: Path) -> str:
        return (
            f"\n[[hooks.{event}]]\n"
            f"\n[[hooks.{event}.hooks]]\n"
            'type = "command"\n'
            f"command = {json.dumps(str(hook))}\n"
            "timeout = 5\n"
        )

    @staticmethod
    def _profile_has_activity_hooks(existing: str, hook: Path) -> bool:
        return all(
            CodexPtyManager._profile_has_event_hook(existing, hook, event)
            for event in ("UserPromptSubmit", "Stop")
        )

    @staticmethod
    def _profile_has_event_hook(
        existing: str,
        hook: Path,
        event: str,
    ) -> bool:
        try:
            profile = tomllib.loads(existing)
            hooks = profile["hooks"]
        except (KeyError, TypeError, tomllib.TOMLDecodeError):
            return False
        entries = hooks.get(event)
        return isinstance(entries, list) and any(
            any(
                hook_entry.get("type") == "command"
                and hook_entry.get("command") == str(hook)
                for hook_entry in entry.get("hooks", [])
                if isinstance(hook_entry, dict)
            )
            for entry in entries
            if isinstance(entry, dict)
        )

    def _ttyd_command(self, session: CodexSession, port: int) -> list[str]:
        launcher = PROJECT_ROOT / "scripts" / "chub-codex-launcher"
        command = [
            "ttyd",
            "-W",
            "-O",
            "-m",
            "1",
            "-i",
            "127.0.0.1",
            "-p",
            str(port),
            "-b",
            f"/codex/{session.id}/terminal",
            str(launcher),
            "--name",
            self._tmux_name(session.id),
            "--cwd",
            str(session.cwd),
            "--chub-session",
            session.id,
            "--hook-dir",
            str(self.hook_dir),
            "--permission-mode",
            session.permission_mode,
        ]
        if session.codex_session_id:
            command.extend(["--codex-session", session.codex_session_id])
        return command

    @staticmethod
    def _tmux_name(session_id: str) -> str:
        return f"chub-{session_id}"

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
                    "codex_terminal_failed",
                    "Unable to start Codex terminal",
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
            "Codex terminal did not become ready",
        )

    def _stop_backend(self, session: CodexSession) -> None:
        process = self._processes.pop(session.id, None)
        if process is not None and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                process.kill()
        elif session.ttyd_pid and self._is_our_ttyd(session):
            try:
                psutil.Process(session.ttyd_pid).terminate()
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass
        session.ttyd_pid = None
        session.ttyd_port = None
        self.store.save(session)

    def _is_our_ttyd(self, session: CodexSession) -> bool:
        try:
            command = psutil.Process(session.ttyd_pid).cmdline()
        except (psutil.NoSuchProcess, psutil.AccessDenied, TypeError):
            return False
        return "ttyd" in Path(command[0]).name and session.id in " ".join(command)

    def _reconcile_saved_backends(self) -> None:
        for session in self.store.list():
            if session.ttyd_pid and self._is_our_ttyd(session):
                try:
                    psutil.Process(session.ttyd_pid).terminate()
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    pass
            session.ttyd_pid = None
            session.ttyd_port = None
            self._refresh_status(session)
            refreshed = self.store.get(session.id)
            if refreshed and refreshed.status == "running":
                refreshed.activity = "unknown"
                refreshed.activity_source = "none"
                self.store.save(refreshed)

    def _sync_native_sessions(self) -> None:
        with self._lock:
            stored = self.store.list()
            stored = self._deduplicate_native_sessions(stored)
            by_codex_id = {
                session.codex_session_id: session
                for session in stored
                if session.codex_session_id
            }
            discovered_sessions = self.discovery.discover()
            active_ids = {
                session.codex_session_id
                for session in discovered_sessions
                if session.codex_session_id
            }
            pending_quick_cwds = {
                session.cwd
                for session in stored
                if (
                    not session.codex_session_id
                    and self._quick_interaction_is_running(session.id)
                )
            }
            for discovered in discovered_sessions:
                existing = by_codex_id.get(discovered.codex_session_id)
                if existing is None:
                    # A fresh `codex exec` creates its native thread before the
                    # SessionStart hook binds it to Chub's local session.
                    if discovered.cwd in pending_quick_cwds:
                        continue
                    self.store.save(discovered)
                    continue
                changed = False
                if existing.cwd != discovered.cwd:
                    existing.cwd = discovered.cwd
                    existing.workspace_name = discovered.workspace_name
                    changed = True
                if discovered.title and not existing.title:
                    existing.title = discovered.title
                    changed = True
                if (
                    existing.status == "running"
                    and discovered.active_permission_mode
                    and existing.active_permission_mode
                    != discovered.active_permission_mode
                ):
                    previous_active = existing.active_permission_mode or "ask"
                    had_pending_change = existing.permission_mode != previous_active
                    existing.active_permission_mode = discovered.active_permission_mode
                    if not had_pending_change:
                        existing.permission_mode = discovered.active_permission_mode
                    changed = True
                if discovered.updated_at > existing.updated_at:
                    existing.updated_at = discovered.updated_at
                    changed = True
                if changed:
                    self.store.save(existing)

            archive_states = self.discovery.session_archive_states()
            if archive_states is None:
                return
            for session in stored:
                native_id = session.codex_session_id
                if not native_id or native_id in active_ids:
                    continue
                if native_id in archive_states and not archive_states[native_id]:
                    continue
                self._remove_stale_session(session)

    def _deduplicate_native_sessions(
        self,
        sessions: list[CodexSession],
    ) -> list[CodexSession]:
        """Collapse discovery records duplicated before a Chub hook was consumed."""
        grouped: dict[str, list[CodexSession]] = {}
        unique: list[CodexSession] = []
        for session in sessions:
            if session.codex_session_id:
                grouped.setdefault(session.codex_session_id, []).append(session)
            else:
                unique.append(session)

        for native_id, duplicates in grouped.items():
            canonical = next(
                (session for session in duplicates if session.id != native_id),
                duplicates[0],
            )
            changed = False
            for duplicate in duplicates:
                if duplicate.id == canonical.id:
                    continue
                if not canonical.title and duplicate.title:
                    canonical.title = duplicate.title
                    changed = True
                if (
                    canonical.active_permission_mode is None
                    and duplicate.active_permission_mode is not None
                ):
                    canonical.active_permission_mode = duplicate.active_permission_mode
                    changed = True
                if duplicate.updated_at > canonical.updated_at:
                    canonical.updated_at = duplicate.updated_at
                    changed = True
                self.store.delete(duplicate.id)
            if changed:
                self.store.save(canonical)
            unique.append(canonical)
        return unique

    def _remove_stale_session(self, session: CodexSession) -> None:
        if shutil.which("tmux"):
            subprocess.run(
                ["tmux", "kill-session", "-t", self._tmux_name(session.id)],
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        self._stop_backend(session)
        self.store.delete(session.id)
        try:
            (self.hook_dir / f"{session.id}.json").unlink()
        except FileNotFoundError:
            pass

    def _running_tmux_count(self) -> int:
        if not shutil.which("tmux"):
            return 0
        return sum(
            subprocess.run(
                ["tmux", "has-session", "-t", self._tmux_name(session.id)],
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            ).returncode
            == 0
            for session in self.store.list()
        )
