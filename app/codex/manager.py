"""Legacy Codex-only test fixture.

Production startup uses :class:`app.ai_session.manager.AiSessionManager`.
This module remains for focused regression coverage of the pre-3B Codex
implementation and must not be imported by production application code.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import socket
import subprocess
import threading
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Callable

import psutil

from app.ai_runtime import (
    RuntimeNativeSession,
    RuntimeOperationError,
    RuntimeRegistry,
    RuntimeTerminalRequest,
)
from app.codex.models import (
    ActivitySource,
    CodexModelCatalogData,
    CodexModelInfo,
    CodexReasoningLevel,
    CodexSession,
    PermissionMode,
    SessionInfo,
    TurnActivity,
    WorkspaceInfo,
    utc_now,
)
from app.codex.store import CodexSessionStore
from app.codex.runtime_adapter import (
    PROFILE_MARKER,
    CodexRuntimeAdapter,
)
from app.codex.worker_runtime import DISCOVERED_RUNTIME_WORKSPACE_ID
from app.core.config import PROJECT_ROOT, Settings
from app.core.response import ApiError


LOGGER = logging.getLogger("hub.codex")


class CodexPtyManager:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.store = CodexSessionStore(settings.codex_pty.data_file)
        adapter = CodexRuntimeAdapter(
            settings,
            which=lambda name: shutil.which(name),
            run=lambda *args, **kwargs: subprocess.run(*args, **kwargs),
        )
        self.runtime_registry = RuntimeRegistry([adapter])
        self.runtime_adapter = self.runtime_registry.require(
            "codex",
            {
                "runtime_status",
                "native_session_mapping",
                "interactive_terminal",
                "session_resume",
                "session_archive",
                "writer_probe",
                "model_catalog",
                "permission_profiles",
            },
        )
        self.codex_home = adapter.codex_home
        self.hook_dir = adapter.hook_dir
        self._processes: dict[str, subprocess.Popen[bytes]] = {}
        self._lock = threading.RLock()
        self._quick_interaction_is_running: Callable[[str], bool] = lambda _id: False
        self._system_upgrade_writes_blocked: Callable[[], bool] = lambda: False
        self._reconcile_saved_backends()

    def set_quick_interaction_checker(
        self,
        checker: Callable[[str], bool],
    ) -> None:
        self._quick_interaction_is_running = checker

    def set_system_upgrade_checker(self, checker: Callable[[], bool]) -> None:
        self._system_upgrade_writes_blocked = checker

    @property
    def network_available(self) -> bool:
        return self.runtime_adapter.network_available

    def dependencies(self) -> dict[str, bool]:
        return self.runtime_adapter.dependencies()

    def available(self) -> bool:
        return self.runtime_adapter.status().available

    def unavailable_reason(self) -> str | None:
        return self.runtime_adapter.status().reason

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
        with self._lock:
            if self._system_upgrade_writes_blocked():
                return [self._public(session) for session in self.store.list()]
            self._consume_hook_results()
            self._sync_native_sessions()
            sessions = self.store.list()
            for session in sessions:
                self._refresh_status(session)
                self._reconcile_quick_activity(session)
            return [self._public(session) for session in self.store.list()]

    def get_session(self, session_id: str) -> CodexSession:
        with self._lock:
            if not self._system_upgrade_writes_blocked():
                self._consume_hook_result(session_id)
                self._sync_native_sessions()
            session = self.store.get(session_id)
            if session is None:
                raise ApiError(404, "codex_session_not_found", "Codex session not found")
            if not self._system_upgrade_writes_blocked():
                self._refresh_status(session)
                self._reconcile_quick_activity(session)
            return session

    def read_session(self, session_id: str) -> SessionInfo:
        return self._public(self.get_session(session_id))

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
        model: str | None = None,
        reasoning_effort: str | None = None,
    ) -> SessionInfo:
        self._require_available()
        self.validate_model(model, reasoning_effort)
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
            model=model,
            reasoning_effort=reasoning_effort,
        )
        self.store.save(session)
        return self._public(session)

    def create_translation_session(self) -> SessionInfo:
        """Create the fixed, read-only Session used for text transformation."""
        self._require_available()
        workspace = self.settings.codex_pty.runtime_dir / "translation-workspace"
        workspace.mkdir(parents=True, exist_ok=True)
        os.chmod(workspace, 0o700)
        session = CodexSession(
            id=str(uuid.uuid4()),
            workspace_id="weixin-translation",
            workspace_name="微信文本优化与翻译",
            cwd=workspace,
            title="文本优化与翻译",
            permission_mode="read-only",
        )
        self.store.save(session)
        return self._public(session)

    def discard_unstarted_session(self, session_id: str) -> bool:
        """Remove a newly-created local Session before it gains native state."""
        with self._lock:
            session = self.store.get(session_id)
            if session is None:
                return True
            if (
                session.codex_session_id
                or session.status not in {"new", "stopped"}
                or session_id in self._processes
            ):
                return False
            self.store.delete(session_id)
            hook_file = self.hook_dir / f"{session_id}.json"
            try:
                hook_file.unlink()
            except FileNotFoundError:
                pass
            return True

    def validate_model(
        self,
        model: str | None,
        reasoning_effort: str | None,
    ) -> None:
        try:
            self.runtime_adapter.validate_model(model, reasoning_effort)
        except RuntimeOperationError as exc:
            raise self._runtime_api_error(exc) from exc

    def validate_native_session_id(self, native_session_id: str) -> None:
        try:
            self.runtime_adapter.validate_native_session_id(native_session_id)
        except RuntimeOperationError as exc:
            raise self._runtime_api_error(exc) from exc

    def read_model_catalog(self) -> CodexModelCatalogData:
        try:
            catalog = self.runtime_adapter.read_model_catalog()
        except RuntimeOperationError as exc:
            raise self._runtime_api_error(exc) from exc
        return CodexModelCatalogData(
            models=[
                CodexModelInfo(
                    id=model.id,
                    name=model.name,
                    description=model.description,
                    default_level=model.default_level,
                    levels=[
                        CodexReasoningLevel(
                            id=level.id,
                            description=level.description,
                        )
                        for level in model.levels
                    ],
                )
                for model in catalog.models
            ],
            default_model=catalog.default_model,
            default_reasoning_effort=catalog.default_reasoning_effort,
        )

    def prepare_quick_interaction(self) -> None:
        """Ensure headless Codex runs use the managed profile and session hook."""
        self._require_available()
        with self._lock:
            self._ensure_profile()

    def has_active_writer(self, codex_session_id: str | None) -> bool:
        """Probe Codex's local writer lock without creating or modifying it."""
        try:
            self.runtime_adapter.codex_home = self.codex_home
            return self.runtime_adapter.has_active_writer(codex_session_id)
        except RuntimeOperationError as exc:
            LOGGER.warning("Unable to inspect Codex writer lock", exc_info=True)
            raise self._runtime_api_error(exc) from exc

    def wait_for_writer_release(
        self,
        codex_session_id: str | None,
        *,
        timeout: float = 3.0,
    ) -> bool:
        try:
            self.runtime_adapter.codex_home = self.codex_home
            return self.runtime_adapter.wait_for_writer_release(
                codex_session_id,
                timeout=timeout,
            )
        except RuntimeOperationError as exc:
            raise self._runtime_api_error(exc) from exc

    def ensure_terminal(self, session_id: str) -> CodexSession:
        self._require_available()
        with self._lock:
            session = self.get_session(session_id)
            self._require_terminal_access(session)
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
                session.active_model = session.model
                session.active_reasoning_effort = session.reasoning_effort
            session.error = None
            session.updated_at = utc_now()
            self.store.save(session)
            return session

    def require_terminal_access(self, session_id: str) -> CodexSession:
        session = self.get_session(session_id)
        self._require_terminal_access(session)
        return session

    @staticmethod
    def _require_terminal_access(session: CodexSession) -> None:
        if session.workspace_id == "weixin-translation":
            raise ApiError(
                409,
                "codex_terminal_access_disabled",
                "文本优化与翻译 Session 仅支持快速交互。",
            )

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

    def rename_session(self, session_id: str, title: str) -> SessionInfo:
        with self._lock:
            session = self.get_session(session_id)
            if session.workspace_id == "weixin-translation":
                raise ApiError(
                    409,
                    "codex_session_rename_not_allowed",
                    "内部翻译 Session 标题固定，不支持重命名。",
                )
            session.title = title
            session.updated_at = utc_now()
            self.store.save(session)
            return self._public(session)

    def bind_quick_interaction_native_session(
        self,
        session_id: str,
        native_session_id: str,
    ) -> None:
        """Persist the Worker-confirmed native ID without trusting a Web hook."""
        with self._lock:
            session = self.store.get(session_id)
            if session is None:
                raise ApiError(404, "codex_session_not_found", "Codex session not found")
            self.validate_native_session_id(native_session_id)
            if (
                session.codex_session_id is not None
                and session.codex_session_id != native_session_id
            ):
                raise ApiError(
                    409,
                    "quick_interaction_native_session_conflict",
                    "Chub Session identity conflict: Codex session identity does not "
                    "match the Worker result",
                )
            if session.codex_session_id is None:
                session.codex_session_id = native_session_id
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
        if session.codex_session_id:
            self.validate_native_session_id(session.codex_session_id)
        self.stop_session(session_id)
        if session.codex_session_id:
            try:
                self.runtime_adapter.run_native_action(
                    "delete",
                    session.codex_session_id,
                )
            except RuntimeOperationError as exc:
                raise self._runtime_api_error(exc) from exc
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
        self.validate_native_session_id(session.codex_session_id)
        self.stop_session(session_id)
        try:
            self.runtime_adapter.run_native_action(
                "archive",
                session.codex_session_id,
            )
        except RuntimeOperationError as exc:
            raise self._runtime_api_error(exc) from exc
        self.store.delete(session_id)
        hook_file = self.hook_dir / f"{session_id}.json"
        try:
            hook_file.unlink()
        except FileNotFoundError:
            pass

    def system_upgrade_sessions(self) -> list[CodexSession]:
        """Freeze the local records without discovering or importing native history."""
        with self._lock:
            return self.store.validate_for_system_upgrade()

    def discard_session_for_system_upgrade(self, session_id: str) -> None:
        """Remove only Chub's local Session state, preserving the Codex Session."""
        with self._lock:
            current = self.store.get(session_id)
            if current is None:
                return
            self.stop_session(session_id)
            self.store.delete(session_id)
            try:
                (self.hook_dir / f"{session_id}.json").unlink()
            except FileNotFoundError:
                pass

    def archive_session_for_system_upgrade(
        self,
        session_id: str,
        native_session_id: str | None,
    ) -> str:
        """Archive one frozen record with crash-safe native archive reconciliation."""
        with self._lock:
            current = self.store.get(session_id)
            if current is None:
                return "archived" if native_session_id else "discarded"
            if current.codex_session_id != native_session_id:
                raise OSError("Session native identity changed during system upgrade")
            self.stop_session(session_id)
            if native_session_id is not None:
                self.validate_native_session_id(native_session_id)
                archive_states = self.runtime_adapter.discovery.session_archive_states()
                if archive_states is None or native_session_id not in archive_states:
                    raise OSError("Native Session archive state cannot be confirmed")
                if archive_states[native_session_id] is False:
                    try:
                        self.runtime_adapter.run_native_action(
                            "archive",
                            native_session_id,
                        )
                    except RuntimeOperationError as exc:
                        raise self._runtime_api_error(exc) from exc
                self.store.delete(session_id)
                outcome = "archived"
            else:
                self.store.delete(session_id)
                outcome = "discarded"
            hook_file = self.hook_dir / f"{session_id}.json"
            try:
                hook_file.unlink()
            except FileNotFoundError:
                pass
            return outcome

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
            runtime_id="codex",
            workspace_id=session.workspace_id,
            workspace_name=session.workspace_name,
            cwd=str(session.cwd),
            title=session.title,
            can_archive=session.codex_session_id is not None,
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
            model=session.model,
            reasoning_effort=session.reasoning_effort,
            active_model=session.active_model,
            active_reasoning_effort=session.active_reasoning_effort,
            error=session.error,
            created_at=session.created_at,
            updated_at=session.updated_at,
            terminal_access_allowed=session.workspace_id != "weixin-translation",
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
            try:
                self.runtime_adapter.validate_native_session_id(codex_session_id)
            except RuntimeOperationError:
                codex_session_id = None
            if codex_session_id and session.codex_session_id != codex_session_id:
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
        self.runtime_adapter.codex_home = self.codex_home
        try:
            self.runtime_adapter.ensure_profile()
        except RuntimeOperationError as exc:
            raise self._runtime_api_error(exc) from exc

    @staticmethod
    def _profile_has_managed_hook(existing: str, hook: Path) -> bool:
        return CodexRuntimeAdapter.profile_has_managed_hook(existing, hook)

    @staticmethod
    def _activity_hook_content(hook: Path) -> str:
        return CodexRuntimeAdapter.activity_hook_content(hook)

    @staticmethod
    def _event_hook_content(event: str, hook: Path) -> str:
        return CodexRuntimeAdapter.event_hook_content(event, hook)

    @staticmethod
    def _profile_has_activity_hooks(existing: str, hook: Path) -> bool:
        return CodexRuntimeAdapter.profile_has_activity_hooks(existing, hook)

    @staticmethod
    def _profile_has_event_hook(
        existing: str,
        hook: Path,
        event: str,
    ) -> bool:
        return CodexRuntimeAdapter.profile_has_event_hook(existing, hook, event)

    def _ttyd_command(self, session: CodexSession, port: int) -> list[str]:
        self.runtime_adapter.hook_dir = self.hook_dir
        process_spec = self.runtime_adapter.terminal_command(
            RuntimeTerminalRequest(
                session_id=session.id,
                cwd=session.cwd,
                permission_mode=session.permission_mode,
                native_session_id=session.codex_session_id,
                model=session.model,
                reasoning_effort=session.reasoning_effort,
            ),
            port,
        )
        return list(process_spec.argv)

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
            try:
                discovery_result = self.runtime_adapter.discover_sessions()
            except RuntimeOperationError as exc:
                raise self._runtime_api_error(exc) from exc
            discovered_sessions = [
                self._codex_session_from_runtime(session)
                for session in discovery_result.sessions
            ]
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
                if existing.workspace_id == "codex":
                    # Older discovery records used a synthetic workspace ID.
                    # Keep them on the same dynamic-directory path as newly
                    # discovered native Sessions.
                    existing.workspace_id = DISCOVERED_RUNTIME_WORKSPACE_ID
                    existing.workspace_name = discovered.workspace_name
                    changed = True
                if (
                    discovered.active_model
                    and existing.active_model != discovered.active_model
                ):
                    existing.active_model = discovered.active_model
                    existing.model = discovered.active_model
                    changed = True
                if (
                    discovered.active_reasoning_effort
                    and existing.active_reasoning_effort
                    != discovered.active_reasoning_effort
                ):
                    existing.active_reasoning_effort = (
                        discovered.active_reasoning_effort
                    )
                    existing.reasoning_effort = discovered.active_reasoning_effort
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

            archive_states = discovery_result.archive_states
            if archive_states is None:
                return
            for session in stored:
                native_id = session.codex_session_id
                if not native_id or native_id in active_ids:
                    continue
                # Discovery can briefly miss a native JSONL file while Codex is
                # still finishing or moving it.  The quick-interaction registry
                # is authoritative for the local Session lifetime during that
                # window, so never prune an actively executing Session here.
                if self._quick_interaction_is_running(session.id):
                    continue
                if native_id in archive_states and not archive_states[native_id]:
                    continue
                self._remove_stale_session(session)

    @staticmethod
    def _codex_session_from_runtime(session: RuntimeNativeSession) -> CodexSession:
        return CodexSession(
            id=session.native_session_id,
            workspace_id=DISCOVERED_RUNTIME_WORKSPACE_ID,
            workspace_name=session.cwd.name or str(session.cwd),
            cwd=session.cwd,
            title=session.title,
            codex_session_id=session.native_session_id,
            status="stopped",
            active_permission_mode=session.active_permission_mode,
            model=session.active_model,
            reasoning_effort=session.active_reasoning_effort,
            active_model=session.active_model,
            active_reasoning_effort=session.active_reasoning_effort,
            created_at=session.created_at,
            updated_at=session.updated_at,
        )

    @staticmethod
    def _runtime_api_error(error: RuntimeOperationError) -> ApiError:
        status_code = {
            "invalid_request": 400,
            "conflict": 409,
            "unavailable": 503,
        }[error.kind]
        return ApiError(status_code, error.code, error.message, source="runtime")

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
                if (
                    canonical.active_model is None
                    and duplicate.active_model is not None
                ):
                    canonical.active_model = duplicate.active_model
                    if canonical.model is None:
                        canonical.model = duplicate.model
                    changed = True
                if (
                    canonical.active_reasoning_effort is None
                    and duplicate.active_reasoning_effort is not None
                ):
                    canonical.active_reasoning_effort = (
                        duplicate.active_reasoning_effort
                    )
                    if canonical.reasoning_effort is None:
                        canonical.reasoning_effort = duplicate.reasoning_effort
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
