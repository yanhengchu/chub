from __future__ import annotations

import logging
import os
import threading
import uuid
from datetime import datetime
from pathlib import Path
from typing import Callable

from app.ai_runtime import RuntimeNativeSession, RuntimeOperationError, RuntimeRegistry
from app.ai_session.models import (
    ActivitySource,
    AiSession,
    PermissionMode,
    TurnActivity,
    utc_now,
)
from app.ai_session.store import AiSessionStore, AiSessionStoreUnavailable
from app.ai_session.supervisor import InteractiveSupervisor
from app.codex.models import (
    CodexModelCatalogData,
    CodexModelInfo,
    CodexReasoningLevel,
    SessionInfo,
    WorkspaceInfo,
)
from app.codex.runtime_adapter import CodexRuntimeAdapter
from app.codex.worker_runtime import DISCOVERED_RUNTIME_WORKSPACE_ID
from app.core.config import PROJECT_ROOT, Settings
from app.core.response import ApiError


LOGGER = logging.getLogger("hub.ai_session")


class AiSessionManager:
    """The sole owner of Chub logical AI Session state.

    Runtime-native IDs are accepted only from the fixed adapter or Worker
    result path. They are deliberately never returned in the public Session
    view. Unarchived Runtime sessions are discovered into fresh Chub records
    so a runtime-data reset does not hide sessions created outside Chub.
    """

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.store = AiSessionStore(
            settings.codex_pty.data_file.with_name("ai-sessions.json")
        )
        adapter = CodexRuntimeAdapter(settings)
        self.runtime_registry = RuntimeRegistry([adapter])
        self.runtime_id = adapter.descriptor.runtime_id
        self.runtime_adapter = self.runtime_registry.require(
            self.runtime_id,
            {
                "runtime_status",
                "native_session_mapping",
                "interactive_terminal",
                "session_resume",
                "session_archive",
                "writer_probe",
                "activity_events",
                "model_catalog",
                "permission_profiles",
            },
        )
        self.supervisor = InteractiveSupervisor(
            adapter,
            ticket_ttl_seconds=settings.codex_pty.ticket_ttl_seconds,
        )
        self._lock = threading.RLock()
        self._quick_interaction_is_running: Callable[[str], bool] = lambda _id: False
        self._system_upgrade_writes_blocked: Callable[[], bool] = lambda: False
        self._reconcile_saved_terminals()

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
        return self.store.available and self.runtime_adapter.status().available

    def unavailable_reason(self) -> str | None:
        return self.store.unavailable_reason or self.runtime_adapter.status().reason

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
            self._require_store()
            if self._system_upgrade_writes_blocked():
                return [self._public(session) for session in self.store.list()]
            self._consume_hook_results()
            self._sync_bound_native_sessions()
            for session in self.store.list():
                self._refresh_status(session)
                self._reconcile_quick_activity(session)
            return [self._public(session) for session in self.store.list()]

    def get_session(self, session_id: str) -> AiSession:
        with self._lock:
            self._require_store()
            if not self._system_upgrade_writes_blocked():
                self._consume_hook_result(session_id)
                self._sync_bound_native_sessions()
            session = self.store.get(session_id)
            if session is None:
                raise ApiError(404, "codex_session_not_found", "Codex session not found")
            if not self._system_upgrade_writes_blocked():
                self._refresh_status(session)
                self._reconcile_quick_activity(session)
                session = self.store.get(session_id) or session
            return session

    def read_session(self, session_id: str) -> SessionInfo:
        return self._public(self.get_session(session_id))

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
        session = AiSession(
            id=str(uuid.uuid4()),
            runtime_id=self.runtime_id,
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
        self._require_available()
        workspace = self.settings.codex_pty.runtime_dir / "translation-workspace"
        workspace.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(workspace, 0o700)
        session = AiSession(
            id=str(uuid.uuid4()),
            runtime_id=self.runtime_id,
            workspace_id="weixin-translation",
            workspace_name="微信文本优化与翻译",
            cwd=workspace,
            title="文本优化与翻译",
            permission_mode="read-only",
        )
        self.store.save(session)
        return self._public(session)

    def discard_unstarted_session(self, session_id: str) -> bool:
        with self._lock:
            self._require_store()
            session = self.store.get(session_id)
            if session is None:
                return True
            if session.native_session_id or session.status not in {"new", "error"}:
                return False
            self.store.delete(session_id)
            self._remove_hook_file(session_id)
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
                        CodexReasoningLevel(id=level.id, description=level.description)
                        for level in model.levels
                    ],
                )
                for model in catalog.models
            ],
            default_model=catalog.default_model,
            default_reasoning_effort=catalog.default_reasoning_effort,
        )

    def prepare_quick_interaction(self) -> None:
        self._require_available()
        with self._lock:
            self._ensure_profile()

    def has_active_writer(self, native_session_id: str | None) -> bool:
        try:
            return self.runtime_adapter.has_active_writer(native_session_id)
        except RuntimeOperationError as exc:
            LOGGER.warning("Unable to inspect Runtime writer lock", exc_info=True)
            raise self._runtime_api_error(exc) from exc

    def wait_for_writer_release(
        self,
        native_session_id: str | None,
        *,
        timeout: float = 3.0,
    ) -> bool:
        try:
            return self.runtime_adapter.wait_for_writer_release(
                native_session_id,
                timeout=timeout,
            )
        except RuntimeOperationError as exc:
            raise self._runtime_api_error(exc) from exc

    def ensure_terminal(self, session_id: str) -> AiSession:
        self._require_available()
        with self._lock:
            session = self.get_session(session_id)
            self._require_terminal_access(session)
            if self._quick_interaction_is_running(session.id):
                raise ApiError(
                    409,
                    "quick_interaction_in_progress",
                    "该会话已有快速交互任务正在执行。",
                )
            if (
                session.native_session_id
                and not self.supervisor.owns_terminal_writer(session.id)
                and self.has_active_writer(session.native_session_id)
            ):
                raise ApiError(
                    409,
                    "codex_session_writer_active",
                    "Codex Session 正在由其他进程使用，请等待任务结束。",
                )
            was_running = session.status == "running"
            try:
                self._ensure_profile()
                self.supervisor.ensure_terminal(
                    session,
                    max_running=self.settings.codex_pty.max_running,
                )
            except Exception:
                if not was_running:
                    session.status = "error"
                session.error = "terminal_backend_failed"
                session.updated_at = utc_now()
                self.store.save(session)
                raise
            session.status = "running"
            if not was_running:
                session.activity = "unknown"
                session.activity_source = "none"
                session.active_permission_mode = session.permission_mode
                session.active_model = session.model
                session.active_reasoning_effort = session.reasoning_effort
            session.error = None
            session.updated_at = utc_now()
            self.store.save(session)
            return session

    def require_terminal_access(self, session_id: str) -> AiSession:
        session = self.get_session(session_id)
        self._require_terminal_access(session)
        return session

    @staticmethod
    def _require_terminal_access(session: AiSession) -> None:
        if session.workspace_id == "weixin-translation":
            raise ApiError(
                409,
                "codex_terminal_access_disabled",
                "文本优化与翻译 Session 仅支持快速交互。",
            )

    def restart_terminal_backend(self, session_id: str) -> AiSession:
        self._require_available()
        with self._lock:
            session = self.get_session(session_id)
            self._require_terminal_access(session)
            self._ensure_profile()
            self.supervisor.restart_terminal_backend(
                session,
                max_running=self.settings.codex_pty.max_running,
            )
            return session

    def stop_session(self, session_id: str) -> SessionInfo:
        with self._lock:
            session = self.get_session(session_id)
            self.supervisor.stop_terminal(session.id)
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
            self._require_store()
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
            self._require_store()
            session = self.store.get(session_id)
            if session is None:
                raise ApiError(404, "codex_session_not_found", "Codex session not found")
            session.activity = activity
            session.activity_source = source
            session.updated_at = max(session.updated_at, updated_at or utc_now())
            self.store.save(session)

    def set_initial_quick_interaction_title(self, session_id: str, title: str) -> None:
        with self._lock:
            self._require_store()
            session = self.store.get(session_id)
            if session is None or session.native_session_id or session.title:
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
        with self._lock:
            self._require_store()
            session = self.store.get(session_id)
            if session is None:
                raise ApiError(404, "codex_session_not_found", "Codex session not found")
            self.validate_native_session_id(native_session_id)
            if (
                session.native_session_id is not None
                and session.native_session_id != native_session_id
            ):
                raise ApiError(
                    409,
                    "quick_interaction_native_session_conflict",
                    "Codex session identity does not match the Worker result",
                )
            if session.native_session_id is None:
                try:
                    duplicates = self.store.bind_native_session(
                        session_id,
                        native_session_id,
                        session.runtime_id,
                    )
                except AiSessionStoreUnavailable as exc:
                    raise ApiError(
                        409,
                        "quick_interaction_native_session_conflict",
                        "Codex session identity is already bound to another Session",
                    ) from exc
                for duplicate in duplicates:
                    self.supervisor.stop_terminal(duplicate.id)
                    self._remove_hook_file(duplicate.id)

    def recover_interrupted_quick_interaction(self, session_id: str) -> None:
        with self._lock:
            self._require_store()
            session = self.store.get(session_id)
            if session is None or session.activity_source != "quick":
                return
            session.activity = "unknown" if session.status == "running" else "idle"
            session.activity_source = "none"
            session.updated_at = utc_now()
            self.store.save(session)

    def delete_session(self, session_id: str) -> None:
        session = self.get_session(session_id)
        if session.native_session_id:
            self.validate_native_session_id(session.native_session_id)
        self.stop_session(session_id)
        if session.native_session_id:
            try:
                self.runtime_adapter.run_native_action("delete", session.native_session_id)
            except RuntimeOperationError as exc:
                raise self._runtime_api_error(exc) from exc
        self.store.delete(session_id)
        self._remove_hook_file(session_id)

    def archive_session(self, session_id: str) -> None:
        session = self.get_session(session_id)
        if not session.native_session_id:
            raise ApiError(
                409,
                "codex_session_not_started",
                "Codex session has not started yet",
            )
        self.validate_native_session_id(session.native_session_id)
        self.stop_session(session_id)
        try:
            self.runtime_adapter.run_native_action("archive", session.native_session_id)
        except RuntimeOperationError as exc:
            raise self._runtime_api_error(exc) from exc
        self.store.delete(session_id)
        self._remove_hook_file(session_id)

    def system_upgrade_sessions(self) -> list[AiSession]:
        with self._lock:
            return self.store.validate_for_system_upgrade()

    def verify_system_upgrade_readiness(self) -> None:
        """Exercise the post-upgrade Session read path before declaring success."""
        with self._lock:
            self._require_store()
            self._consume_hook_results()
            self._sync_bound_native_sessions()
            self.store.list()

    def discard_session_for_system_upgrade(self, session_id: str) -> None:
        """Remove only Chub's local Session state, preserving the Runtime Session."""
        with self._lock:
            current = self.store.get(session_id)
            if current is None:
                return
            self.supervisor.stop_terminal(session_id)
            self.store.delete(session_id)
            self._remove_hook_file(session_id)

    def archive_session_for_system_upgrade(
        self,
        session_id: str,
        native_session_id: str | None,
    ) -> str:
        with self._lock:
            current = self.store.get(session_id)
            if current is None:
                return "archived" if native_session_id else "discarded"
            if current.native_session_id != native_session_id:
                raise OSError("Session native identity changed during system upgrade")
            self.stop_session(session_id)
            if native_session_id is not None:
                self.validate_native_session_id(native_session_id)
                archive_states = self.runtime_adapter.discovery.session_archive_states()
                if archive_states is None or native_session_id not in archive_states:
                    raise OSError("Native Session archive state cannot be confirmed")
                if archive_states[native_session_id] is False:
                    try:
                        self.runtime_adapter.run_native_action("archive", native_session_id)
                    except RuntimeOperationError as exc:
                        raise self._runtime_api_error(exc) from exc
                outcome = "archived"
            else:
                outcome = "discarded"
            self.store.delete(session_id)
            self._remove_hook_file(session_id)
            return outcome

    def backend_url(self, session_id: str, path: str, query: str = "") -> str:
        self.ensure_terminal(session_id)
        port = self.supervisor.backend_port(session_id)
        suffix = f"?{query}" if query else ""
        return f"http://127.0.0.1:{port}/codex/{session_id}/terminal/{path}{suffix}"

    def backend_ws_url(self, session_id: str) -> str:
        self.ensure_terminal(session_id)
        port = self.supervisor.backend_port(session_id)
        return f"ws://127.0.0.1:{port}/codex/{session_id}/terminal/ws"

    def backend_origin(self, session_id: str) -> str:
        return f"http://127.0.0.1:{self.supervisor.backend_port(session_id)}"

    def close(self) -> None:
        self.supervisor.close()

    def _require_available(self) -> None:
        reason = self.unavailable_reason()
        if reason:
            raise ApiError(503, "codex_pty_unavailable", reason)

    def _require_store(self) -> None:
        if not self.store.available:
            raise ApiError(
                503,
                "ai_session_state_unavailable",
                self.store.unavailable_reason or "AI Session 状态不可用。",
            )

    @staticmethod
    def _public(session: AiSession) -> SessionInfo:
        return SessionInfo(
            id=session.id,
            runtime_id=session.runtime_id,
            workspace_id=session.workspace_id,
            workspace_name=session.workspace_name,
            cwd=str(session.cwd),
            title=session.title,
            can_archive=session.native_session_id is not None,
            status=session.status,
            activity=session.activity,
            activity_source=session.activity_source,
            permission_mode=session.permission_mode,
            active_permission_mode=session.active_permission_mode,
            permission_pending=(
                session.status == "running"
                and session.permission_mode != (session.active_permission_mode or "ask")
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

    def _reconcile_quick_activity(self, session: AiSession) -> None:
        if session.activity_source != "quick" or self._quick_interaction_is_running(session.id):
            return
        session.activity = "idle" if session.status == "stopped" else "unknown"
        session.activity_source = "none"
        session.updated_at = utc_now()
        self.store.save(session)

    def _consume_hook_results(self) -> None:
        for session in self.store.list():
            self._consume_hook_result(session.id)

    def _consume_hook_result(self, session_id: str) -> None:
        try:
            event = self.runtime_adapter.read_activity_event(session_id)
        except RuntimeOperationError:
            LOGGER.warning("Ignoring unsafe Runtime activity event", exc_info=True)
            self._remove_hook_file(session_id)
            return
        if event is None:
            return
        session = self.store.get(session_id)
        native_session_id = event.native_session_id
        activity = event.activity
        activity_source = event.activity_source
        changed = False
        if session and isinstance(native_session_id, str) and native_session_id:
            try:
                self.runtime_adapter.validate_native_session_id(native_session_id)
            except RuntimeOperationError:
                native_session_id = None
            if native_session_id and session.native_session_id != native_session_id:
                if session.native_session_id is not None:
                    raise ApiError(
                        409,
                        "codex_session_native_conflict",
                        "Runtime Session identity does not match the managed Session",
                    )
                # Runtime discovery can win the race with a Worker hook and
                # create a second Chub record for the same native Session.
                # Keep the hook's logical Session (the one the user started)
                # and discard only the duplicate discovery record.
                try:
                    duplicates = self.store.bind_native_session(
                        session.id,
                        native_session_id,
                        session.runtime_id,
                    )
                except AiSessionStoreUnavailable as exc:
                    raise ApiError(
                        409,
                        "codex_session_native_conflict",
                        "Runtime Session identity is already bound to another managed Session",
                    ) from exc
                for duplicate in duplicates:
                    self.supervisor.stop_terminal(duplicate.id)
                    self._remove_hook_file(duplicate.id)
                session = self.store.get(session.id)
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
            if session.activity != activity or session.activity_source != expected_source:
                session.activity = activity
                session.activity_source = expected_source
                changed = True
        if session and changed:
            session.updated_at = utc_now()
            self.store.save(session)
        self._remove_hook_file(session_id)

    def _sync_bound_native_sessions(self) -> None:
        try:
            discovery = self.runtime_adapter.discover_sessions()
        except RuntimeOperationError as exc:
            raise self._runtime_api_error(exc) from exc
        for native in discovery.sessions:
            if native.runtime_id != self.runtime_id:
                raise self._runtime_api_error(
                    RuntimeOperationError(
                        "runtime_session_identity_invalid",
                        "Runtime Session discovery owner does not match the Adapter",
                        kind="conflict",
                    )
                )
        discovered = {
            (item.runtime_id, item.native_session_id): item
            for item in discovery.sessions
        }
        stored = self.store.list()
        bound_native_keys = {
            (session.runtime_id, session.native_session_id)
            for session in stored
            if session.native_session_id is not None
        }
        for native in discovery.sessions:
            native_key = (native.runtime_id, native.native_session_id)
            if native_key in bound_native_keys:
                continue
            self.store.save(self._session_from_native(native))
            bound_native_keys.add(native_key)
        for session in stored:
            native_session_id = session.native_session_id
            if native_session_id is None:
                continue
            current = discovered.get((session.runtime_id, native_session_id))
            changed = False
            if current is not None:
                changed = self._reconcile_discovered_workspace(session, current) or changed
                changed = self._project_native_state(session, current) or changed
            elif discovery.archive_states is not None:
                # A present, unarchived database record can briefly outlive its
                # JSONL file. All other absent discovery results are stale.
                if discovery.archive_states.get(native_session_id) is False:
                    continue
                self.supervisor.stop_terminal(session.id)
                self.store.delete(session.id)
                self._remove_hook_file(session.id)
                continue
            if changed:
                session.updated_at = utc_now()
                self.store.save(session)

    def _session_from_native(self, native: RuntimeNativeSession) -> AiSession:
        permission_mode = native.active_permission_mode or "ask"
        workspace = self._workspace_for_native_cwd(native.cwd) or (
            DISCOVERED_RUNTIME_WORKSPACE_ID,
            native.cwd.name or str(native.cwd),
        )
        return AiSession(
            id=str(uuid.uuid4()),
            runtime_id=native.runtime_id,
            native_session_id=native.native_session_id,
            workspace_id=workspace[0],
            workspace_name=workspace[1],
            cwd=native.cwd,
            discovered=True,
            title=native.title[:48] if native.title else None,
            status="stopped",
            permission_mode=permission_mode,
            active_permission_mode=native.active_permission_mode,
            model=native.active_model,
            reasoning_effort=native.active_reasoning_effort,
            active_model=native.active_model,
            active_reasoning_effort=native.active_reasoning_effort,
            error=None,
            created_at=native.created_at,
            updated_at=native.updated_at,
        )

    def _reconcile_discovered_workspace(
        self,
        session: AiSession,
        native: RuntimeNativeSession,
    ) -> bool:
        # Only discovery records may inherit their workspace from Runtime data.
        # Existing managed Sessions retain the workspace selected at creation.
        if not session.discovered and session.workspace_id != "codex":
            return False
        workspace = self._workspace_for_native_cwd(native.cwd) or (
            DISCOVERED_RUNTIME_WORKSPACE_ID,
            native.cwd.name or str(native.cwd),
        )
        workspace_id, workspace_name = workspace
        changed = (
            session.workspace_id != workspace_id
            or session.workspace_name != workspace_name
            or session.cwd != native.cwd
            or not session.discovered
            or session.error == "worker_workspace_unavailable"
        )
        if not changed:
            return False
        session.workspace_id = workspace_id
        session.workspace_name = workspace_name
        session.cwd = native.cwd
        session.discovered = True
        if session.status == "error" and session.error == "worker_workspace_unavailable":
            session.status = "stopped"
        if session.error == "worker_workspace_unavailable":
            session.error = None
        return True

    def _workspace_for_native_cwd(self, cwd: Path) -> tuple[str, str] | None:
        try:
            resolved_cwd = cwd.expanduser().resolve(strict=False)
        except OSError:
            return None
        candidates = (
            (
                "weixin-translation",
                "微信文本优化与翻译",
                self.settings.codex_pty.runtime_dir / "translation-workspace",
            ),
            ("chub", "Chub", PROJECT_ROOT),
            ("workspace", "Workspace", self.settings.codex_pty.workspace),
            ("home", "用户目录", Path.home()),
        )
        for workspace_id, workspace_name, path in candidates:
            try:
                if resolved_cwd == path.expanduser().resolve(strict=False):
                    return workspace_id, workspace_name
            except OSError:
                continue
        return None

    @staticmethod
    def _project_native_state(session: AiSession, native: RuntimeNativeSession) -> bool:
        changed = False
        if native.active_model and session.active_model != native.active_model:
            session.active_model = native.active_model
            session.model = native.active_model
            changed = True
        if (
            native.active_reasoning_effort
            and session.active_reasoning_effort != native.active_reasoning_effort
        ):
            session.active_reasoning_effort = native.active_reasoning_effort
            session.reasoning_effort = native.active_reasoning_effort
            changed = True
        if native.title and not session.title:
            session.title = native.title[:48]
            changed = True
        if (
            session.status == "running"
            and native.active_permission_mode
            and session.active_permission_mode != native.active_permission_mode
        ):
            previous_active = session.active_permission_mode or "ask"
            pending_change = session.permission_mode != previous_active
            session.active_permission_mode = native.active_permission_mode
            if not pending_change:
                session.permission_mode = native.active_permission_mode
            changed = True
        if native.updated_at > session.updated_at:
            session.updated_at = native.updated_at
            changed = True
        return changed

    def _refresh_status(self, session: AiSession) -> None:
        running = self.supervisor.is_terminal_running(session.id)
        if session.status == "error" and not running:
            return
        next_status = "running" if running else ("stopped" if session.native_session_id else "new")
        if session.status != next_status:
            session.status = next_status
            session.updated_at = utc_now()
            self.store.save(session)

    def _ensure_profile(self) -> None:
        try:
            self.runtime_adapter.ensure_profile()
        except RuntimeOperationError as exc:
            raise self._runtime_api_error(exc) from exc

    def _reconcile_saved_terminals(self) -> None:
        if not self.store.available:
            return
        try:
            sessions = self.store.list()
            running = self.supervisor.reconcile_after_restart(sessions)
            for session in sessions:
                if session.id in running:
                    session.status = "running"
                    session.activity = "unknown"
                    session.activity_source = "none"
                    session.updated_at = utc_now()
                    self.store.save(session)
        except (AiSessionStoreUnavailable, OSError):
            LOGGER.warning("Unable to reconcile AI terminal backends", exc_info=True)

    def _remove_hook_file(self, session_id: str) -> None:
        try:
            self.runtime_adapter.clear_activity_event(session_id)
        except RuntimeOperationError:
            LOGGER.warning("Unable to clear Runtime activity event", exc_info=True)

    @staticmethod
    def _runtime_api_error(error: RuntimeOperationError) -> ApiError:
        status_code = {
            "invalid_request": 400,
            "conflict": 409,
            "unavailable": 503,
        }[error.kind]
        return ApiError(status_code, error.code, error.message)
