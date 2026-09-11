from __future__ import annotations

import hashlib
import logging
import os
import secrets
import threading
import time
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable, Iterable

from app.ai_runtime import (
    RuntimeDescriptor,
    RuntimeNativeSession,
    RuntimeOperationError,
    RuntimeRegistry,
    RuntimeStatus,
)
from app.ai_runtime.runtime_plugin_packages import (
    RuntimePluginService,
    RuntimePluginActivation,
    RuntimePluginLoadFailure,
    RuntimePluginRemoval,
)
from app.ai_runtime.runtime_plugins import RuntimePluginRegistry
from app.ai_runtime.codex_plugin import load_development_codex_plugin
from app.ai_runtime.enablement import (
    RuntimeEnablement,
    RuntimeEnablementStore,
    RuntimeEnablementStoreUnavailable,
)
from app.ai_runtime.general_settings import (
    AiRuntimeSettingsStore,
    RuntimeSettingsStoreUnavailable,
)
from app.ai_runtime.implementation_preferences import (
    RuntimeImplementationPreferences,
    RuntimeImplementationPreferencesStore,
    RuntimeImplementationPreferencesUnavailable,
)
from app.ai_session.models import (
    ActivitySource,
    AiSession,
    PermissionMode,
    TurnActivity,
    utc_now,
)
from app.ai_session.store import AiSessionStore, AiSessionStoreUnavailable
from app.codex.models import (
    CodexModelCatalogData,
    CodexModelInfo,
    CodexReasoningLevel,
    NativeSessionInfo,
    SessionInfo,
    SessionUsage,
    RuntimeManagementData,
    RuntimeManagementItem,
    RuntimeImplementationData,
    RuntimeImplementationItem,
    WorkspaceInfo,
)
from app.core.config import PROJECT_ROOT, Settings
from app.core.response import ApiError


LOGGER = logging.getLogger("hub.ai_session")
NATIVE_ACTION_REF_TTL_SECONDS = 300
MAX_NATIVE_ACTION_REFS = 256


@dataclass(frozen=True)
class TranslationNativeCleanupResult:
    pending: int = 0
    reason: str | None = None
    retry_required: bool = False



class _UnavailableRuntimeRateLimits:
    def read(self, *, force: bool = False):
        del force
        raise RuntimeOperationError(
            "runtime_unavailable",
            "Codex Runtime is not installed",
            kind="unavailable",
        )


class _UnavailableCodexRuntime:
    descriptor = RuntimeDescriptor(runtime_id="codex", capabilities=frozenset())
    display_name = "Codex"
    rate_limits = _UnavailableRuntimeRateLimits()

    def status(self) -> RuntimeStatus:
        return RuntimeStatus(
            runtime_id="codex",
            available=False,
            reason="Codex Runtime is not installed",
            dependencies={},
        )

    def dependencies(self) -> dict[str, bool]:
        return {}

    @staticmethod
    def runtime_process_matches(_command: tuple[str, ...]) -> bool:
        return False

    def __getattr__(self, _name: str):
        raise RuntimeOperationError(
            "runtime_unavailable",
            "Codex Runtime is not installed",
            kind="unavailable",
        )


class AiSessionManager:
    """The sole owner of Chub logical AI Session state.

    Runtime-native IDs are deliberately never returned in the public Session
    view. Chub creates a Session only when a user starts an interaction; native
    discovery only supplies the independent native-session list.
    """

    def __init__(
        self,
        settings: Settings,
        *,
        development_runtime_plugins: RuntimePluginRegistry | None = None,
    ) -> None:
        self.settings = settings
        session_store_path = settings.ai_runtime.codex.data_file.with_name(
            "ai-sessions.json"
        )
        AiSessionStore.discard_legacy_session_state(session_store_path)
        self.store = AiSessionStore(session_store_path)
        self.runtime_enablement = RuntimeEnablementStore(
            settings.ai_runtime.codex.data_file.with_name("runtime-enablement.json")
        )
        self.runtime_implementation_preferences = RuntimeImplementationPreferencesStore(
            settings.ai_runtime.codex.data_file.with_name("runtime-implementation-preferences.json")
        )
        if development_runtime_plugins is None:
            development_plugin = load_development_codex_plugin(settings)
            self._development_runtime_plugins = RuntimePluginRegistry(
                [] if development_plugin is None else [development_plugin]
            )
        else:
            self._development_runtime_plugins = development_runtime_plugins
        self.runtime_plugin_service = RuntimePluginService(settings)
        self.runtime_plugin_recovery = (
            self.runtime_plugin_service.recover_incomplete_activation()
        )
        self.runtime_id = "codex"
        self.runtime_plugins = RuntimePluginRegistry()
        self.runtime_plugin_failures = ()
        self.runtime_registry = RuntimeRegistry([])
        self.runtime_adapters: dict[str, object] = {}
        self.default_implementation_id: str | None = None
        self.runtime_adapter = _UnavailableCodexRuntime()
        self.runtime_settings_store = AiRuntimeSettingsStore()
        self._lock = threading.RLock()
        self._native_action_refs: dict[str, tuple[str, float]] = {}
        self._native_action_refs_by_native_id: dict[str, str] = {}
        self._quick_interaction_is_running: Callable[[str], bool] = lambda _id: False
        self._passive_session_cleanup: Callable[[str], bool] = lambda _id: True
        self._system_upgrade_writes_blocked: Callable[[], bool] = lambda: False
        self.refresh_runtime_plugins()
    def install_runtime_plugin(
        self,
        archive: bytes,
        *,
        source_name: str,
        operation_id: str,
    ) -> RuntimePluginActivation:
        activation = self.runtime_plugin_service.install(
            archive,
            source_name=source_name,
            operation_id=operation_id,
        )
        try:
            self.refresh_runtime_plugins()
            activated = (
                activation.installed.manifest.implementation_id
                in self.runtime_plugins.implementation_ids("codex")
            )
        except Exception:
            self.runtime_plugin_service.rollback(activation)
            self.refresh_runtime_plugins()
            raise
        if not activated:
            self.runtime_plugin_service.rollback(activation)
            self.refresh_runtime_plugins()
            raise ApiError(
                503,
                "runtime_plugin_activation_unconfirmed",
                "Runtime 插件已写入，但 Web 注册表未能确认激活。",
            )
        return activation

    def refresh_runtime_plugins(self) -> None:
        modules, failures = self.runtime_plugin_service.build_registry(
            self._development_runtime_plugins
        )
        available_modules = RuntimePluginRegistry()
        adapters = []
        for implementation_id in modules.implementation_ids():
            module = modules.require(implementation_id)
            try:
                adapter = module.build_adapter()
                RuntimeRegistry([adapter])
                available_modules.register(module)
                adapters.append(adapter)
            except Exception:
                failures += (
                    RuntimePluginLoadFailure(
                        implementation_id,
                        "Runtime 插件装配失败。",
                    ),
                )
        self.runtime_plugins = available_modules
        self.runtime_plugin_failures = failures
        self.runtime_registry = RuntimeRegistry(adapters)
        self.runtime_adapters = {
            adapter.descriptor.effective_implementation_id: adapter
            for adapter in adapters
        }
        self.default_implementation_id = self._resolve_default_implementation_id()
        self._activate_default_implementation()

    def refresh_development_codex_plugin(self) -> RuntimePluginRegistry:
        """Reload the checked-out Codex plugin and return the prior registry."""
        previous_development = self._development_runtime_plugins
        try:
            development_plugin = load_development_codex_plugin(self.settings, reload_source=True)
            if development_plugin is None:
                raise ApiError(503, "development_runtime_plugin_unavailable", "开发版 Runtime 源码未随当前部署包提供。")
            candidate = RuntimePluginRegistry([development_plugin])
            self._development_runtime_plugins = candidate
            self.refresh_runtime_plugins()
            if "builtin-dev" not in self.runtime_plugins.implementation_ids("codex"):
                raise ApiError(503, "development_runtime_plugin_refresh_unconfirmed", "开发版 Runtime 未能通过 Web 注册表校验。")
            return previous_development
        except Exception:
            self._development_runtime_plugins = previous_development
            self.refresh_runtime_plugins()
            raise

    def restore_development_codex_plugin(
        self,
        previous_builtin: RuntimePluginRegistry,
    ) -> None:
        """Restore Web's prior development plugin after Worker rejects a refresh."""
        with self._lock:
            self._development_runtime_plugins = previous_builtin
            self.refresh_runtime_plugins()

    def _activate_default_implementation(self) -> None:
        try:
            adapter = self.runtime_registry.require(
                self.default_implementation_id or "__missing__",
                {
                    "runtime_status",
                    "native_session_mapping",
                    "session_resume",
                    "session_archive",
                    "writer_probe",
                    "model_catalog",
                    "permission_profiles",
                },
            )
        except RuntimeOperationError:
            adapter = _UnavailableCodexRuntime()
        if adapter is not self.runtime_adapter:
            self.runtime_adapter = adapter
            self.runtime_settings_store = (
                AiRuntimeSettingsStore()
                if isinstance(adapter, _UnavailableCodexRuntime)
                else adapter.runtime_settings_store
            )

    def remove_runtime_plugin(
        self,
        module_id: str,
        *,
        operation_id: str,
    ) -> RuntimePluginRemoval:
        if module_id in self._development_runtime_plugins.implementation_ids():
            raise ApiError(422, "runtime_plugin_remove_invalid", "开发版 Runtime 不可移除。")
        if module_id == self.default_implementation_id:
            raise ApiError(
                409,
                "runtime_default_implementation_required",
                "请先选择其他默认 Runtime 版本。",
            )
        removal = self.runtime_plugin_service.remove(module_id, operation_id=operation_id)
        try:
            self.refresh_runtime_plugins()
            if module_id in self.runtime_plugins.implementation_ids("codex"):
                raise ApiError(503, "runtime_plugin_removal_unconfirmed", "Web 注册表未能确认 Runtime 插件已移除。")
        except Exception:
            self.runtime_plugin_service.rollback_removal(removal)
            self.refresh_runtime_plugins()
            raise
        return removal

    def require_implementation_maintenance_available(
        self, implementation_id: str
    ) -> None:
        """Keep physical removal local while a Session still needs this code."""
        with self._lock:
            if any(
                session.implementation_id == implementation_id
                for session in self.store.list()
            ):
                raise ApiError(
                    409,
                    "runtime_implementation_session_bound",
                    "该 Runtime 版本仍被 Chub Session 绑定，归档或删除这些 Session 后再移除。",
                )

    def clear_runtime_plugin_state(self, module_id: str) -> None:
        """Clear Chub-owned state covered by a Runtime upgrade boundary."""
        # Implementations share the logical Codex Session mapping. Replacing
        # or removing one version must never discard shared Session/history.
        enablement = self.runtime_enablement.read()
        disabled = [
            runtime_id
            for runtime_id in enablement.disabled_runtime_ids
            if runtime_id != module_id
        ]
        if len(disabled) != len(enablement.disabled_runtime_ids):
            self.runtime_enablement.save(RuntimeEnablement(disabled_runtime_ids=disabled))

    def runtime_session_ids(self, runtime_id: str) -> tuple[str, ...]:
        with self._lock:
            self._require_store()
            return tuple(
                session.id
                for session in self.store.list()
                if session.runtime_id == runtime_id
            )

    def _resolve_default_implementation_id(self) -> str | None:
        try:
            preferences = self.runtime_implementation_preferences.read()
        except RuntimeImplementationPreferencesUnavailable:
            return None
        available = {
            implementation_id
            for implementation_id, adapter in self.runtime_adapters.items()
            if adapter.status().available
            and implementation_id not in preferences.disabled_implementation_ids
        }
        if preferences.default_implementation_id is not None:
            return (
                preferences.default_implementation_id
                if preferences.default_implementation_id in available
                else None
            )
        selected = "builtin-dev" if "builtin-dev" in available else next(iter(available), None)
        if selected is not None:
            self.runtime_implementation_preferences.save(
                preferences.model_copy(update={"default_implementation_id": selected})
            )
        return selected

    def require_implementation_submission(self, implementation_id: str) -> None:
        try:
            disabled_runtimes = set(self.runtime_enablement.read().disabled_runtime_ids)
        except RuntimeEnablementStoreUnavailable as exc:
            raise ApiError(
                503,
                "ai_runtime_enablement_unavailable",
                "AI Runtime 启用状态不可用，请稍后重试。",
            ) from exc
        if self.runtime_id in disabled_runtimes:
            raise ApiError(
                409,
                "ai_runtime_disabled",
                "当前 AI Runtime 已停用，无法提交新的 AI 任务。",
            )
        self.require_implementation_available(implementation_id)

    def require_implementation_available(self, implementation_id: str) -> None:
        try:
            preferences = self.runtime_implementation_preferences.read()
        except RuntimeImplementationPreferencesUnavailable as exc:
            raise ApiError(503, "runtime_implementation_preferences_unavailable", str(exc)) from exc
        if implementation_id in preferences.disabled_implementation_ids:
            raise ApiError(409, "runtime_implementation_disabled", "当前 Runtime 版本已停用，无法提交新任务。")
        adapter = self.runtime_adapters.get(implementation_id)
        if adapter is None or not adapter.status().available:
            raise ApiError(503, "runtime_implementation_unavailable", "当前 Runtime 版本不可用，无法提交新任务。")

    def default_submission_implementation_id(self) -> str:
        implementation_id = self.default_implementation_id
        if implementation_id is None:
            raise ApiError(503, "runtime_default_implementation_unavailable", "默认 Codex Runtime 版本不可用。")
        self.require_implementation_submission(implementation_id)
        return implementation_id

    def session_implementation_id(self, session_id: str) -> str:
        """Return the implementation pinned at Session creation.

        Legacy persisted Sessions are assigned to the original built-in slot,
        never to whichever default happened to be selected later.
        """
        with self._lock:
            session = self.get_session(session_id, reconcile=False)
            if session.implementation_id is not None:
                return session.implementation_id
            implementation_id = "builtin-dev"
            if implementation_id not in self.runtime_adapters:
                raise ApiError(
                    503,
                    "runtime_implementation_unavailable",
                    "历史 Session 对应的内置 Runtime 版本不可用。",
                )
            session.implementation_id = implementation_id
            session.updated_at = utc_now()
            self.store.save(session)
            return implementation_id

    def read_runtime_implementations(self) -> RuntimeImplementationData:
        try:
            preferences = self.runtime_implementation_preferences.read()
        except RuntimeImplementationPreferencesUnavailable as exc:
            raise ApiError(503, "runtime_implementation_preferences_unavailable", str(exc)) from exc
        installed, _failures = self.runtime_plugin_service.discover()
        manifests = {item.manifest.implementation_id: item.manifest for item in installed}
        items: list[RuntimeImplementationItem] = []
        for implementation_id in self.runtime_plugins.implementation_ids(self.runtime_id):
            module = self.runtime_plugins.require(implementation_id)
            adapter = self.runtime_adapters.get(implementation_id)
            status = adapter.status() if adapter is not None else None
            manifest = manifests.get(implementation_id)
            items.append(
                RuntimeImplementationItem(
                    implementation_id=implementation_id,
                    name=module.display_name,
                    version=manifest.version if manifest is not None else "dev",
                    description=(
                        manifest.description
                        if manifest is not None
                        else module.description
                    ),
                    enabled=implementation_id not in preferences.disabled_implementation_ids,
                    healthy=status is not None and status.available,
                    is_default=implementation_id == self.default_implementation_id,
                    compatibility_id=(
                        module.descriptor.native_session_compatibility_id
                    ),
                    removable=implementation_id != "builtin-dev",
                    reason=None if status is None else status.reason,
                )
            )
        return RuntimeImplementationData(
            runtime_id=self.runtime_id,
            default_implementation_id=self.default_implementation_id,
            implementations=items,
        )

    def update_runtime_implementation_enabled(
        self, implementation_id: str, enabled: bool
    ) -> RuntimeImplementationData:
        with self._lock:
            if implementation_id not in self.runtime_plugins.implementation_ids(self.runtime_id):
                raise ApiError(404, "runtime_implementation_not_found", "Codex Runtime 版本不存在。")
            preferences = self.runtime_implementation_preferences.read()
            disabled = set(preferences.disabled_implementation_ids)
            if enabled:
                adapter = self.runtime_adapters.get(implementation_id)
                if adapter is None or not adapter.status().available:
                    raise ApiError(409, "runtime_implementation_unavailable", "不可用的 Runtime 版本不能启用。")
                disabled.discard(implementation_id)
            else:
                if implementation_id == preferences.default_implementation_id:
                    raise ApiError(409, "runtime_default_implementation_required", "请先选择其他默认 Runtime 版本。")
                disabled.add(implementation_id)
            self.runtime_implementation_preferences.save(
                preferences.model_copy(update={"disabled_implementation_ids": sorted(disabled)})
            )
            self.default_implementation_id = self._resolve_default_implementation_id()
            return self.read_runtime_implementations()

    def update_default_implementation(self, implementation_id: str) -> RuntimeImplementationData:
        with self._lock:
            self.require_implementation_available(implementation_id)
            previous_id = self.default_implementation_id
            previous_adapter = self.runtime_adapter
            preferences = self.runtime_implementation_preferences.read()
            self.default_implementation_id = implementation_id
            try:
                self._activate_default_implementation()
                self.runtime_implementation_preferences.save(
                    preferences.model_copy(
                        update={"default_implementation_id": implementation_id}
                    )
                )
            except Exception:
                self.default_implementation_id = previous_id
                if self.runtime_adapter is not previous_adapter:
                    self._activate_default_implementation()
                raise
            return self.read_runtime_implementations()

    def set_quick_interaction_checker(
        self,
        checker: Callable[[str], bool],
    ) -> None:
        self._quick_interaction_is_running = checker

    def set_passive_session_cleanup(self, cleanup: Callable[[str], bool]) -> None:
        """Register Chub-owned cleanup required before passive removal."""
        self._passive_session_cleanup = cleanup

    def set_system_upgrade_checker(self, checker: Callable[[], bool]) -> None:
        self._system_upgrade_writes_blocked = checker

    def dependencies(self) -> dict[str, bool]:
        return self.runtime_adapter.dependencies()

    @property
    def codex_rate_limits(self):
        return self.runtime_adapter.rate_limits

    def available(self) -> bool:
        return self.store.available and self.runtime_adapter.status().available

    def unavailable_reason(self) -> str | None:
        return self.store.unavailable_reason or self.runtime_adapter.status().reason

    def read_runtime_management(self) -> RuntimeManagementData:
        try:
            disabled = set(self.runtime_enablement.read().disabled_runtime_ids)
        except RuntimeEnablementStoreUnavailable as exc:
            raise ApiError(
                503,
                "ai_runtime_enablement_unavailable",
                "AI Runtime 启用状态不可用，请稍后重试。",
            ) from exc
        runtimes: list[RuntimeManagementItem] = []
        for runtime_id in self.runtime_plugins.runtime_ids():
            implementation_id = (
                self.default_implementation_id
                if runtime_id == self.runtime_id
                else next(iter(self.runtime_plugins.implementation_ids(runtime_id)), None)
            )
            adapter = (
                self.runtime_adapters.get(implementation_id)
                if implementation_id is not None
                else None
            )
            status = adapter.status() if adapter is not None else None
            navigation_id = implementation_id or runtime_id
            runtimes.append(
                RuntimeManagementItem(
                    runtime_id=runtime_id,
                    name=self.runtime_plugins.require_navigation(navigation_id).name,
                    enabled=runtime_id not in disabled,
                    healthy=status is not None and status.available,
                    reason=None if status is None else status.reason,
                )
            )
        return RuntimeManagementData(
            runtimes=runtimes,
            basic_mode=not any(item.enabled for item in runtimes),
        )

    def update_runtime_enabled(
        self,
        runtime_id: str,
        enabled: bool,
    ) -> RuntimeManagementData:
        try:
            state = self.runtime_enablement.read()
            disabled = set(state.disabled_runtime_ids)
            if enabled:
                selected_id = (
                    self.default_implementation_id
                    if runtime_id == self.runtime_id
                    else runtime_id
                )
                if selected_id is None:
                    raise ApiError(
                        503,
                        "runtime_default_implementation_unavailable",
                        "默认 Codex Runtime 版本不可用。",
                    )
                self.require_implementation_available(selected_id)
                self.runtime_registry.require(selected_id)
                disabled.discard(runtime_id)
            else:
                disabled.add(runtime_id)
            self.runtime_enablement.save(
                RuntimeEnablement(disabled_runtime_ids=sorted(disabled))
            )
        except RuntimeEnablementStoreUnavailable as exc:
            raise ApiError(
                503,
                "ai_runtime_enablement_unavailable",
                "AI Runtime 启用状态不可用，请稍后重试。",
            ) from exc
        except RuntimeOperationError as exc:
            raise self._runtime_api_error(exc) from exc
        return self.read_runtime_management()

    def submission_available(self) -> tuple[bool, str | None]:
        try:
            self._require_runtime_submission(self.runtime_id)
        except ApiError as exc:
            return False, exc.message
        return True, None

    def require_runtime_submission(self, runtime_id: str) -> None:
        self._require_runtime_submission(runtime_id)

    def require_runtime_enabled_for_maintenance(self, runtime_id: str) -> None:
        try:
            disabled = set(self.runtime_enablement.read().disabled_runtime_ids)
        except RuntimeEnablementStoreUnavailable as exc:
            raise ApiError(
                503,
                "ai_runtime_enablement_unavailable",
                "AI Runtime 启用状态不可用，请稍后重试。",
            ) from exc
        if runtime_id in disabled:
            raise ApiError(
                409,
                "ai_runtime_disabled",
                "当前 AI Runtime 已停用，无法刷新开发版。",
            )

    def workspaces(self) -> list[WorkspaceInfo]:
        entries = [
            ("home", "用户目录", Path.home()),
            ("workspace", "Workspace", self.settings.ai_runtime.codex.workspace),
            ("chub", "Chub", PROJECT_ROOT),
            *[
                (workspace.id, workspace.name, workspace.path)
                for workspace in self.settings.ai_runtime.codex.extra_workspaces
            ],
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
        sessions, _native_sessions = self.list_sessions_with_native_sessions()
        return sessions

    def list_sessions_with_native_sessions(
        self,
        *,
        include_internal_translation_native_sessions: bool = False,
    ) -> tuple[list[SessionInfo], list[NativeSessionInfo]]:
        with self._lock:
            self._require_store()
            if self._system_upgrade_writes_blocked():
                return [self._public(session) for session in self.store.list()], []
            native_sessions = self._sync_bound_native_sessions()
            for session in self.store.list():
                self._refresh_status(session)
                self._reconcile_quick_activity(session)
            bound_sessions = {
                (session.runtime_id, session.native_session_id): session
                for session in self.store.list()
                if session.native_session_id is not None
            }
            pending_initial_native_claim = any(
                session.native_session_id is None
                and session.quick_native_claim_task_id is not None
                and self._quick_interaction_is_running(session.id)
                for session in self.store.list()
            )
            # A first Chub task creates its Native Session before the Worker
            # can return the ID for the atomic binding. Keep that narrow
            # window out of the unbound list so it never appears as external.
            unbound_native_sessions = (
                []
                if pending_initial_native_claim
                else [
                    item
                    for item in native_sessions
                    if (item.runtime_id, item.native_session_id) not in bound_sessions
                    and (
                        include_internal_translation_native_sessions
                        or not self._is_translation_workspace(item.cwd)
                    )
                ]
            )

            def native_writer_state(native: RuntimeNativeSession) -> str:
                try:
                    return "held" if self.runtime_adapter.has_active_writer(native.native_session_id) else "free"
                except RuntimeOperationError:
                    return "unknown"

            return (
                [self._public(session) for session in self.store.list()],
                [
                    NativeSessionInfo(
                        cwd=self._native_session_cwd_display(item.cwd),
                        title=item.title,
                        created_at=item.created_at,
                        updated_at=item.updated_at,
                        writer_lock_state=(writer_state := native_writer_state(item)),
                        chub_writer_lock_state="free",
                        native_action_ref=(
                            self._issue_native_action_ref(item.native_session_id)
                            if writer_state == "free"
                            else None
                        ),
                    )
                    for item in unbound_native_sessions
                ],
            )

    @staticmethod
    def _native_session_cwd_display(cwd: Path) -> str:
        try:
            relative_path = cwd.relative_to(Path.home())
        except ValueError:
            return str(cwd)
        return "~" if relative_path == Path(".") else f"~/{relative_path}"

    def _issue_native_action_ref(self, native_session_id: str) -> str:
        now = time.monotonic()
        self._prune_native_action_refs(now)
        existing_reference = self._native_action_refs_by_native_id.get(native_session_id)
        if existing_reference is not None:
            existing = self._native_action_refs.get(existing_reference)
            if existing is not None and existing[1] > now:
                return existing_reference
        while len(self._native_action_refs) >= MAX_NATIVE_ACTION_REFS:
            self._discard_native_action_ref(next(iter(self._native_action_refs)))
        reference = secrets.token_urlsafe(24)
        self._native_action_refs[reference] = (
            native_session_id,
            now + NATIVE_ACTION_REF_TTL_SECONDS,
        )
        self._native_action_refs_by_native_id[native_session_id] = reference
        return reference

    def native_action_audit_target(self, native_action_ref: str) -> str:
        """Return a stable, non-sensitive target for an issued action reference."""
        with self._lock:
            self._prune_native_action_refs(time.monotonic())
            record = self._native_action_refs.get(native_action_ref)
            if record is None:
                return "native:unresolved"
            return f"native:{hashlib.sha256(record[0].encode()).hexdigest()[:12]}"

    def _prune_native_action_refs(self, now: float) -> None:
        for reference, (_native_session_id, expires_at) in tuple(self._native_action_refs.items()):
            if expires_at <= now:
                self._discard_native_action_ref(reference)

    def _discard_native_action_ref(self, reference: str) -> tuple[str, float] | None:
        record = self._native_action_refs.pop(reference, None)
        if record is not None and self._native_action_refs_by_native_id.get(record[0]) == reference:
            self._native_action_refs_by_native_id.pop(record[0], None)
        return record

    def run_discovered_native_action(
        self,
        action: str,
        native_action_ref: str,
    ) -> None:
        """Apply one confirmed action to an unbound Runtime-native Session."""
        if action not in {"archive", "delete"}:
            raise ApiError(422, "native_session_action_invalid", "Native Session 操作无效。")
        with self._lock:
            record = self._discard_native_action_ref(native_action_ref)
            if record is None or record[1] <= time.monotonic():
                raise ApiError(
                    409,
                    "native_session_action_stale",
                    "Native Session 列表已变化，请刷新后重试。",
                )
            native_session_id = record[0]
            native_sessions = self._sync_bound_native_sessions()
            bound_native_ids = {
                session.native_session_id
                for session in self.store.list()
                if session.native_session_id is not None
            }
            native = next(
                (item for item in native_sessions if item.native_session_id == native_session_id),
                None,
            )
            if native is None or native_session_id in bound_native_ids:
                raise ApiError(
                    409,
                    "native_session_action_stale",
                    "Native Session 列表已变化，请刷新后重试。",
                )
            try:
                if self.runtime_adapter.has_active_writer(native_session_id):
                    raise ApiError(
                        409,
                        "codex_session_writer_active",
                        "This is open in another app, close it there to continue here.",
                    )
            except RuntimeOperationError as exc:
                raise ApiError(
                    503,
                    "native_session_writer_unknown",
                    "Native Session 占用状态无法确认，请刷新后重试。",
                ) from exc
            try:
                self.runtime_adapter.run_native_action(action, native_session_id)
                confirmed = (
                    self.runtime_adapter.native_session_archive_state(native_session_id) is True
                    if action == "archive"
                    else self.runtime_adapter.native_session_deleted_state(native_session_id) is True
                )
            except RuntimeOperationError as exc:
                raise self._runtime_api_error(exc) from exc
            if not confirmed:
                raise ApiError(
                    503,
                    "native_session_action_unconfirmed",
                    "Native Session 操作结果无法确认，请刷新后重试。",
                )

    def get_session(self, session_id: str, *, reconcile: bool = True) -> AiSession:
        with self._lock:
            self._require_store()
            if reconcile and not self._system_upgrade_writes_blocked():
                self._sync_bound_native_sessions()
            session = self.store.get(session_id)
            if session is None:
                raise ApiError(404, "codex_session_not_found", "Codex session not found")
            if reconcile and not self._system_upgrade_writes_blocked():
                self._refresh_status(session)
                self._reconcile_quick_activity(session)
                session = self.store.get(session_id) or session
            return session

    def read_session(self, session_id: str) -> SessionInfo:
        return self._public(self.get_session(session_id))

    def create_session(
        self,
        workspace_id: str,
        permission_mode: PermissionMode | None = None,
        model: str | None = None,
        reasoning_effort: str | None = None,
    ) -> SessionInfo:
        self._require_runtime_submission(self.runtime_id)
        if permission_mode is None:
            try:
                permission_mode = self.runtime_settings_store.read_general().new_session_permission
            except RuntimeSettingsStoreUnavailable as exc:
                raise ApiError(
                    503,
                    "ai_runtime_settings_unavailable",
                    "无法读取新建 Session 默认权限，请稍后重试。",
                ) from exc
        if permission_mode == "ask":
            raise ApiError(
                409,
                "quick_interaction_ask_not_supported",
                "快速交互不支持 Ask for approval，请选择只读、自动审核或完全访问权限。",
            )
        implementation_id = self.default_submission_implementation_id()
        self.validate_model(
            model,
            reasoning_effort,
            implementation_id=implementation_id,
        )
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
            implementation_id=implementation_id,
            workspace_id=workspace.id,
            workspace_name=workspace.name,
            cwd=Path(workspace.path),
            permission_mode=permission_mode,
            model=model,
            reasoning_effort=reasoning_effort,
            activity="idle",
        )
        self.store.save(session)
        return self._public(session)

    def create_translation_session(self) -> SessionInfo:
        self._require_runtime_submission(self.runtime_id)
        workspace = self.settings.ai_runtime.codex.runtime_dir / "translation-workspace"
        workspace.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(workspace, 0o700)
        session = AiSession(
            id=str(uuid.uuid4()),
            runtime_id=self.runtime_id,
            implementation_id=self.default_submission_implementation_id(),
            workspace_id="weixin-translation",
            workspace_name="微信文本优化与翻译",
            cwd=workspace,
            title="文本优化与翻译",
            permission_mode="read-only",
            activity="idle",
        )
        self.store.save(session)
        return self._public(session)

    def cleanup_translation_sessions_for_replacement(self) -> None:
        """Delete idle internal translation Sessions before creating a replacement."""
        with self._lock:
            self._require_store()
            translation_sessions = [
                session
                for session in self.store.list()
                if session.workspace_id == "weixin-translation"
            ]
            for session in translation_sessions:
                if self._quick_interaction_is_running(session.id):
                    continue
                try:
                    if session.native_session_id and self.has_active_writer(
                        session.native_session_id,
                        implementation_id=session.implementation_id or "builtin-dev",
                    ):
                        continue
                    self.delete_session(session.id)
                except Exception:
                    LOGGER.warning(
                        "Unable to delete stale internal translation Session",
                        extra={"session_id": session.id},
                        exc_info=True,
                    )

        self.cleanup_stale_translation_native_sessions()

    def cleanup_stale_translation_native_sessions(self) -> TranslationNativeCleanupResult:
        """Remove every idle, unbound Native Session in the translation workspace.

        A translation Worker can rotate its Native Session without replacing the
        logical Chub Session.  This reconciliation deliberately uses the
        Runtime discovery result as the source of truth, so a prior failed
        deletion is picked up by a later pass too.
        """
        with self._lock:
            self._require_store()
            bound_native_ids = {
                session.native_session_id
                for session in self.store.list()
                if (
                    session.workspace_id == "weixin-translation"
                    and session.native_session_id is not None
                )
            }
            adapter = self.runtime_adapter
            try:
                discovery = adapter.discover_sessions()
            except RuntimeOperationError:
                LOGGER.warning("Unable to discover stale translation native Sessions", exc_info=True)
                return TranslationNativeCleanupResult(
                    reason="暂时无法读取翻译 Native Session。",
                    retry_required=True,
                )
            pending = 0
            reason = None
            for native in discovery.sessions:
                if (
                    native.native_session_id in bound_native_ids
                    or not self._is_translation_workspace(native.cwd)
                ):
                    continue
                try:
                    if adapter.has_active_writer(native.native_session_id):
                        pending += 1
                        reason = reason or "部分历史翻译 Session 仍在执行。"
                        continue
                    adapter.run_native_action("delete", native.native_session_id)
                    if adapter.native_session_deleted_state(native.native_session_id) is not True:
                        pending += 1
                        reason = reason or "部分历史翻译 Session 的删除结果尚未确认。"
                        LOGGER.warning(
                            "Stale translation native Session deletion was not confirmed",
                            extra={"native_session_id": native.native_session_id},
                        )
                except RuntimeOperationError:
                    pending += 1
                    reason = reason or "部分历史翻译 Session 暂时无法删除。"
                    LOGGER.warning(
                        "Unable to delete stale translation native Session",
                        extra={"native_session_id": native.native_session_id},
                        exc_info=True,
                    )
            return TranslationNativeCleanupResult(
                pending=pending,
                reason=reason,
                retry_required=pending > 0,
            )

    def discard_unstarted_session(self, session_id: str) -> bool:
        with self._lock:
            self._require_store()
            session = self.store.get(session_id)
            if session is None:
                return True
            if session.native_session_id or session.status not in {"new", "error"}:
                return False
            self.store.delete(session_id)
            return True

    def _adapter_for_session(self, session: AiSession):
        implementation_id = self.session_implementation_id(session.id)
        adapter = self.runtime_adapters.get(implementation_id)
        if adapter is None or not adapter.status().available:
            raise ApiError(
                503,
                "runtime_implementation_unavailable",
                "该 Session 绑定的 Runtime 版本不可用。",
            )
        return adapter

    def validate_model(
        self,
        model: str | None,
        reasoning_effort: str | None,
        *,
        implementation_id: str | None = None,
    ) -> None:
        try:
            adapter = (
                self.runtime_adapters[implementation_id]
                if implementation_id is not None
                else self.runtime_adapter
            )
            adapter.validate_model(model, reasoning_effort)
        except RuntimeOperationError as exc:
            raise self._runtime_api_error(exc) from exc

    def validate_native_session_id(
        self,
        native_session_id: str,
        *,
        implementation_id: str | None = None,
    ) -> None:
        try:
            adapter = (
                self.runtime_adapters[implementation_id]
                if implementation_id is not None
                else self.runtime_adapter
            )
            adapter.validate_native_session_id(native_session_id)
        except RuntimeOperationError as exc:
            raise self._runtime_api_error(exc) from exc

    def read_model_catalog(self) -> CodexModelCatalogData:
        try:
            catalog = self.runtime_adapter.read_model_catalog()
        except RuntimeOperationError as exc:
            raise self._runtime_api_error(exc) from exc
        data = CodexModelCatalogData(
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
        return data

    def update_session_configuration(
        self,
        session_id: str,
        permission_mode: PermissionMode,
        model: str | None,
        reasoning_effort: str | None,
    ) -> SessionInfo:
        self._require_available()
        if permission_mode == "ask":
            raise ApiError(
                409,
                "quick_interaction_ask_not_supported",
                "快速交互不支持 Ask for approval，请选择只读、自动审核或完全访问权限。",
            )
        with self._lock:
            session = self.get_session(session_id)
            self.validate_model(
                model,
                reasoning_effort,
                implementation_id=self.session_implementation_id(session.id),
            )
            session.permission_mode = permission_mode
            session.model = model
            session.reasoning_effort = reasoning_effort
            session.updated_at = utc_now()
            self.store.save(session)
            return self._public(session)

    def update_session_model(
        self,
        session_id: str,
        model: str,
        reasoning_effort: str,
    ) -> AiSession:
        """Persist the model for a future quick task."""
        self._require_available()
        with self._lock:
            session = self.get_session(session_id)
            self.validate_model(
                model,
                reasoning_effort,
                implementation_id=self.session_implementation_id(session.id),
            )
            session.model = model
            session.reasoning_effort = reasoning_effort
            session.updated_at = utc_now()
            self.store.save(session)
            return session

    def ensure_session_implementation_compatible(
        self, session_id: str, implementation_id: str
    ) -> None:
        session = self.get_session(session_id)
        pinned_implementation_id = self.session_implementation_id(session_id)
        if implementation_id != pinned_implementation_id:
            raise ApiError(
                409,
                "runtime_implementation_session_bound",
                "该 Chub Session 已绑定其他 Runtime 版本，请新建 Session 后再使用该版本。",
            )
        self.require_implementation_submission(implementation_id)
        if session.native_session_id is None:
            return
        adapter = self.runtime_adapters[implementation_id]
        compatibility_id = adapter.descriptor.native_session_compatibility_id
        if (
            session.native_session_compatibility_id is not None
            and compatibility_id != session.native_session_compatibility_id
        ):
            raise ApiError(
                409,
                "runtime_implementation_incompatible_session",
                "当前 Runtime 版本与该 Session 的原生格式不兼容，请选择兼容版本或新建 Session。",
            )

    def has_active_writer(
        self,
        native_session_id: str | None,
        *,
        implementation_id: str | None = None,
    ) -> bool:
        try:
            adapter = (
                self.runtime_adapters[implementation_id]
                if implementation_id is not None
                else self.runtime_adapter
            )
            return adapter.has_active_writer(native_session_id)
        except RuntimeOperationError as exc:
            LOGGER.warning("Unable to inspect Runtime writer lock", exc_info=True)
            raise self._runtime_api_error(exc) from exc

    def resolve_session_usage(
        self,
        session_id: str,
        *,
        reconcile: bool = True,
    ) -> SessionUsage:
        """Resolve the current Chub/native ownership for one logical Session."""
        with self._lock:
            session = self.get_session(session_id, reconcile=reconcile)
            return self._resolve_session_usage(session)

    def ensure_stop_allowed(self, session_id: str) -> SessionUsage:
        """Validate the minimal business gate for a user-requested stop."""
        usage = self.resolve_session_usage(session_id)
        if usage.owner == "external":
            raise ApiError(
                409,
                "codex_session_writer_active",
                "This is open in another app, close it there to continue here.",
            )
        if usage.owner == "unknown":
            raise ApiError(
                409,
                "codex_session_usage_unknown",
                "无法确认 Session 占用状态，请刷新后重试。",
            )
        if usage.owner == "quick_worker" and usage.phase in {
            "running",
            "waiting_result",
        }:
            return usage
        raise ApiError(
            409,
            "codex_session_not_running",
            "Session 当前没有正在执行的任务。",
        )

    def _resolve_session_usage(self, session: AiSession) -> SessionUsage:
        native_session_present = session.native_session_id is not None
        try:
            implementation_id = session.implementation_id or "builtin-dev"
            adapter = self.runtime_adapters.get(implementation_id)
            if adapter is None or not adapter.status().available:
                return SessionUsage(
                    native_session_present=native_session_present,
                    owner="unknown",
                    phase="unknown",
                )
            if self._quick_interaction_is_running(session.id):
                return SessionUsage(
                    native_session_present=native_session_present,
                    owner="quick_worker",
                    phase="waiting_result",
                )

            if not native_session_present:
                return SessionUsage()

            writer_active = adapter.has_active_writer(
                session.native_session_id
            )
            if writer_active:
                return SessionUsage(
                    native_session_present=True,
                    owner="external",
                    phase="unknown",
                )
            return SessionUsage(
                native_session_present=True,
                owner="none",
                phase="idle",
            )
        except RuntimeOperationError:
            LOGGER.warning(
                "Unable to resolve Session usage ownership",
                extra={"session_id": session.id},
                exc_info=True,
            )
            return SessionUsage(
                native_session_present=native_session_present,
                owner="unknown",
                phase="unknown",
            )

    def wait_for_writer_release(
        self,
        native_session_id: str | None,
        *,
        timeout: float = 3.0,
        implementation_id: str | None = None,
    ) -> bool:
        try:
            adapter = (
                self.runtime_adapters[implementation_id]
                if implementation_id is not None
                else self.runtime_adapter
            )
            return adapter.wait_for_writer_release(
                native_session_id,
                timeout=timeout,
            )
        except RuntimeOperationError as exc:
            raise self._runtime_api_error(exc) from exc

    def require_session_access(self, session_id: str) -> AiSession:
        return self.get_session(session_id)

    def stop_session(
        self,
        session_id: str,
        *,
        reconcile: bool = True,
    ) -> SessionInfo:
        with self._lock:
            session = self.get_session(session_id, reconcile=reconcile)
            session.status = "stopped"
            session.activity = "idle"
            session.activity_source = "none"
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
            activity_at = updated_at or utc_now()
            session.updated_at = max(session.updated_at, activity_at)
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
            usage = self._resolve_session_usage(session)
            if usage.owner == "external":
                raise ApiError(
                    409,
                    "codex_session_writer_active",
                    "This is open in another app, close it there to continue here.",
                )
            session.title = title
            session.updated_at = utc_now()
            self.store.save(session)
            return self._public(session)

    def bind_quick_interaction_native_session(
        self,
        session_id: str,
        native_session_id: str,
        *,
        worker_task_id: str | None = None,
        execution_id: str | None = None,
        implementation_id: str | None = None,
    ) -> None:
        with self._lock:
            self._require_store()
            session = self.store.get(session_id)
            if session is None:
                raise ApiError(404, "codex_session_not_found", "Codex session not found")
            pinned_implementation_id = self.session_implementation_id(session_id)
            if implementation_id is not None:
                self.ensure_session_implementation_compatible(session_id, implementation_id)
            else:
                implementation_id = pinned_implementation_id
            self.validate_native_session_id(
                native_session_id,
                implementation_id=implementation_id,
            )
            if worker_task_id is not None or execution_id is not None:
                if (
                    worker_task_id is None
                    or execution_id is None
                    or session.quick_native_claim_task_id != worker_task_id
                    or session.quick_native_claim_execution_id not in {None, execution_id}
                ):
                    raise ApiError(
                        409,
                        "quick_interaction_native_session_stale",
                        "Quick Worker result no longer belongs to the current Session task.",
                    )
                if session.quick_native_claim_execution_id != execution_id:
                    session.quick_native_claim_execution_id = execution_id
                    session.updated_at = utc_now()
                    self.store.save(session)
            for candidate in self.store.list():
                if (
                    candidate.id != session_id
                    and candidate.runtime_id == session.runtime_id
                    and candidate.native_session_id == native_session_id
                ):
                    raise ApiError(
                        409,
                        "quick_interaction_native_session_conflict",
                        "Codex 原生 Session 已归属于其他 Chub Session。",
                    )
            if (
                session.native_session_id is not None
                and session.native_session_id != native_session_id
            ):
                if session.workspace_id == "weixin-translation":
                    # Translation keeps one logical Chub Session but may rotate
                    # its internal native Session when the Worker starts a new
                    # read-only execution. Never rotate through an active writer.
                    if self.has_active_writer(
                        session.native_session_id,
                        implementation_id=implementation_id,
                    ):
                        raise ApiError(
                            409,
                            "quick_interaction_native_session_conflict",
                            "翻译 Session 仍有原生任务占用，暂不能切换 native Session。",
                        )
                    session.native_session_id = native_session_id
                    session.updated_at = utc_now()
                    try:
                        self.store.save(session)
                    except AiSessionStoreUnavailable as exc:
                        raise ApiError(
                            409,
                            "quick_interaction_native_session_conflict",
                            "Chub Session identity conflict: the translation native Session is already bound to another Session",
                        ) from exc
                    self._clear_native_identity_conflict(session)
                    return
                raise ApiError(
                    409,
                    "quick_interaction_native_session_conflict",
                    "Chub Session identity conflict: Codex session identity does not "
                    "match the Worker result",
                )
            if session.native_session_id is None:
                try:
                    self.store.bind_native_session(
                        session_id,
                        native_session_id,
                        session.runtime_id,
                    )
                except AiSessionStoreUnavailable as exc:
                    raise ApiError(
                        409,
                        "quick_interaction_native_session_conflict",
                        "Chub Session identity conflict: Codex session identity is "
                        "already bound to another Session",
                    ) from exc
            current = self.store.get(session_id)
            if current is not None:
                if implementation_id is not None and current.native_session_compatibility_id is None:
                    current.native_session_compatibility_id = self.runtime_adapters[
                        implementation_id
                    ].descriptor.native_session_compatibility_id
                    current.updated_at = utc_now()
                    self.store.save(current)
                self._clear_native_identity_conflict(current)

    def _clear_native_identity_conflict(self, session: AiSession) -> None:
        if session.error != "native_session_identity_conflict":
            return
        session.error = None
        session.updated_at = utc_now()
        self.store.save(session)

    def register_quick_native_claim(self, session_id: str, worker_task_id: str) -> None:
        """Reserve one current Quick Worker task as the only native-ID claimant."""
        with self._lock:
            session = self.store.get(session_id)
            if session is None:
                raise ApiError(404, "codex_session_not_found", "Codex session not found")
            if session.quick_native_claim_task_id not in {None, worker_task_id}:
                raise ApiError(
                    409,
                    "quick_interaction_native_session_claim_active",
                    "A different Quick Worker task is already claiming this Session.",
                )
            if session.quick_native_claim_task_id == worker_task_id:
                return
            session.quick_native_claim_task_id = worker_task_id
            session.quick_native_claim_execution_id = None
            session.updated_at = utc_now()
            self.store.save(session)

    def clear_quick_native_claim(self, session_id: str, worker_task_id: str) -> None:
        with self._lock:
            session = self.store.get(session_id)
            if (
                session is None
                or session.quick_native_claim_task_id != worker_task_id
            ):
                return
            session.quick_native_claim_task_id = None
            session.quick_native_claim_execution_id = None
            session.updated_at = utc_now()
            self.store.save(session)

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
        self.delete_native_session(session_id)
        self.finalize_delete_session(session_id)

    def ensure_delete_allowed(
        self,
        session_id: str,
        *,
        reconcile: bool = True,
    ) -> SessionUsage:
        """Validate native ownership and Chub execution gates for delete."""
        usage = self.resolve_session_usage(session_id, reconcile=reconcile)
        if usage.owner == "external":
            raise ApiError(
                409,
                "codex_session_writer_active",
                "This is open in another app, close it there to continue here.",
            )
        if usage.owner == "unknown":
            return usage
        if usage.owner == "quick_worker" and usage.phase == "unknown":
            return usage
        return usage

    def delete_native_session(self, session_id: str) -> AiSession:
        """Delete the Runtime Session before clearing Chub-owned state."""
        session = self.get_session(session_id, reconcile=False)
        self.ensure_delete_allowed(session_id, reconcile=False)
        if session.native_session_id:
            adapter = self._adapter_for_session(session)
            self.validate_native_session_id(
                session.native_session_id,
                implementation_id=self.session_implementation_id(session.id),
            )
            try:
                adapter.run_native_action(
                    "delete",
                    session.native_session_id,
                )
            except RuntimeOperationError as exc:
                if exc.code == "codex_session_delete_failed":
                    try:
                        if (
                            adapter.native_session_deleted_state(
                                session.native_session_id
                            )
                            is True
                        ):
                            return session
                    except RuntimeOperationError:
                        pass
                raise self._runtime_api_error(exc) from exc
        return session

    def finalize_delete_session(
        self,
        session_id: str,
    ) -> None:
        """Clear Chub-owned state after native deletion has succeeded."""
        session = self.store.get(session_id)
        self.store.delete(session_id)

    def forget_session(self, session_id: str) -> None:
        """Remove Chub-owned state without changing the Native Session."""
        with self._lock:
            self._require_store()
            if self.store.get(session_id) is None:
                raise ApiError(404, "codex_session_not_found", "Codex session not found")
            self.store.delete(session_id)

    def ensure_archive_allowed(
        self,
        session_id: str,
        *,
        reconcile: bool = True,
    ) -> SessionUsage:
        """Validate native ownership and execution gates for archive."""
        usage = self.resolve_session_usage(session_id, reconcile=reconcile)
        if usage.owner == "external":
            raise ApiError(
                409,
                "codex_session_writer_active",
                "This is open in another app, close it there to continue here.",
            )
        if usage.owner == "quick_worker" and usage.phase in {
            "running",
            "waiting_result",
        }:
            raise ApiError(
                409,
                "codex_session_in_progress",
                "Session 当前正在执行，请等待任务结束后再归档。",
            )
        return usage

    def archive_native_session(self, session_id: str) -> AiSession:
        """Archive the Runtime Session before touching Chub-owned state."""
        session = self.get_session(session_id, reconcile=False)
        self.ensure_archive_allowed(session_id, reconcile=False)
        if session.native_session_id:
            adapter = self._adapter_for_session(session)
            self.validate_native_session_id(
                session.native_session_id,
                implementation_id=self.session_implementation_id(session.id),
            )
            try:
                adapter.run_native_action(
                    "archive",
                    session.native_session_id,
                )
            except RuntimeOperationError as exc:
                if exc.code == "codex_session_archive_failed":
                    try:
                        if (
                            adapter.native_session_archive_state(
                                session.native_session_id
                            )
                            is True
                        ):
                            return session
                    except RuntimeOperationError:
                        pass
                raise self._runtime_api_error(exc) from exc
        return session

    def finalize_archive_session(self, session_id: str) -> None:
        """Clear Chub-owned state after native archive has succeeded."""
        try:
            self.stop_session(session_id)
        except ApiError as exc:
            if exc.code != "codex_session_not_found":
                raise
        session = self.store.get(session_id)
        self.store.delete(session_id)

    def archive_session(self, session_id: str) -> None:
        self.archive_native_session(session_id)
        self.finalize_archive_session(session_id)

    def archive_legacy_translation_sessions(self, quick_interactions) -> int:
        del quick_interactions
        return 0

    def system_upgrade_sessions(self) -> list[AiSession]:
        with self._lock:
            return self.store.validate_for_system_upgrade()

    def verify_system_upgrade_readiness(self) -> None:
        """Exercise the post-upgrade Session read path before declaring success."""
        with self._lock:
            self._require_store()
            self._sync_bound_native_sessions()
            self.store.list()

    def discard_session_for_system_upgrade(self, session_id: str) -> None:
        """Drop Chub management state without inspecting or stopping the Runtime Session."""
        with self._lock:
            current = self.store.get(session_id)
            if current is None:
                return
            self.store.delete(session_id)

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
                implementation_id = self.session_implementation_id(current.id)
                adapter = self._adapter_for_session(current)
                self.validate_native_session_id(
                    native_session_id,
                    implementation_id=implementation_id,
                )
                archive_states = adapter.discovery.session_archive_states()
                if archive_states is None or native_session_id not in archive_states:
                    raise OSError("Native Session archive state cannot be confirmed")
                if archive_states[native_session_id] is False:
                    try:
                        adapter.run_native_action("archive", native_session_id)
                    except RuntimeOperationError as exc:
                        raise self._runtime_api_error(exc) from exc
                outcome = "archived"
            else:
                outcome = "discarded"
            self.store.delete(session_id)
            return outcome

    def close(self) -> None:
        return

    def _require_available(self) -> None:
        reason = self.unavailable_reason()
        if reason:
            raise ApiError(503, "codex_runtime_unavailable", reason)

    def _require_runtime_submission(self, runtime_id: str) -> None:
        self._require_available()
        try:
            selected_id = (
                self.default_implementation_id
                if runtime_id == self.runtime_id and self.default_implementation_id is not None
                else runtime_id
            )
            self.runtime_registry.require(selected_id)
        except RuntimeOperationError as exc:
            raise self._runtime_api_error(exc) from exc
        try:
            disabled = set(self.runtime_enablement.read().disabled_runtime_ids)
        except RuntimeEnablementStoreUnavailable as exc:
            raise ApiError(
                503,
                "ai_runtime_enablement_unavailable",
                "AI Runtime 启用状态不可用，请稍后重试。",
            ) from exc
        if runtime_id in disabled:
            raise ApiError(
                409,
                "ai_runtime_disabled",
                "当前 AI Runtime 已停用，无法提交新的 AI 任务。",
            )

    def _require_store(self) -> None:
        if not self.store.available:
            raise ApiError(
                503,
                "ai_session_state_unavailable",
                self.store.unavailable_reason or "AI Session 状态不可用。",
            )

    def _public(self, session: AiSession) -> SessionInfo:
        try:
            if session.implementation_id is None:
                self._require_runtime_submission(session.runtime_id)
            else:
                self.require_implementation_submission(session.implementation_id)
            runtime_submission_available = True
            runtime_submission_reason = None
        except ApiError as exc:
            runtime_submission_available = False
            runtime_submission_reason = exc.message
        return SessionInfo(
            id=session.id,
            runtime_id=session.runtime_id,
            workspace_id=session.workspace_id,
            workspace_name=session.workspace_name,
            cwd=str(session.cwd),
            title=session.title,
            # Archiving also removes a Chub-only Session that has not bound a
            # native Session yet; the UI must not treat a missing native ID as
            # an archive prohibition.
            can_archive=True,
            status=session.status,
            activity=session.activity,
            activity_source=session.activity_source,
            permission_mode=session.permission_mode,
            model=session.model,
            reasoning_effort=session.reasoning_effort,
            error=session.error,
            created_at=session.created_at,
            updated_at=session.updated_at,
            last_activity_at=session.last_activity_at,
            runtime_submission_available=runtime_submission_available,
            runtime_submission_reason=runtime_submission_reason,
            usage=self._resolve_session_usage(session),
        )

    def _reconcile_quick_activity(self, session: AiSession) -> None:
        if session.activity_source != "quick" or self._quick_interaction_is_running(session.id):
            return
        session.activity = "idle" if session.status == "stopped" else "unknown"
        session.activity_source = "none"
        session.updated_at = utc_now()
        self.store.save(session)

    def _sync_bound_native_sessions(self) -> tuple[RuntimeNativeSession, ...]:
        stored = self.store.list()
        default_implementation_id = self.default_implementation_id
        implementation_ids = {
            session.implementation_id or "builtin-dev" for session in stored
        }
        if default_implementation_id is not None:
            implementation_ids.add(default_implementation_id)
        discoveries = {}
        discovered_by_implementation = {}
        for implementation_id in implementation_ids:
            adapter = self.runtime_adapters.get(implementation_id)
            if adapter is None or not adapter.status().available:
                continue
            try:
                discovery = adapter.discover_sessions()
            except RuntimeOperationError as exc:
                LOGGER.warning(
                    "Unable to discover Sessions for a bound Runtime implementation",
                    extra={"implementation_id": implementation_id},
                    exc_info=True,
                )
                continue
            if any(native.runtime_id != self.runtime_id for native in discovery.sessions):
                raise self._runtime_api_error(
                    RuntimeOperationError(
                        "runtime_session_identity_invalid",
                        "Runtime Session discovery owner does not match the Adapter",
                        kind="conflict",
                    )
                )
            discoveries[implementation_id] = discovery
            discovered_by_implementation[implementation_id] = {
                (item.runtime_id, item.native_session_id): item
                for item in discovery.sessions
            }

        default_discovery = discoveries.get(default_implementation_id)
        for session in stored:
            native_session_id = session.native_session_id
            if native_session_id is None:
                continue
            implementation_id = session.implementation_id or "builtin-dev"
            discovery = discoveries.get(implementation_id)
            if discovery is None:
                continue
            current = discovered_by_implementation[implementation_id].get(
                (session.runtime_id, native_session_id)
            )
            changed = False
            if current is not None:
                changed = self._project_native_state(session, current) or changed
            elif discovery.archive_states is not None and (
                discovery.archive_states.get(native_session_id) is True
                or (
                    discovery.complete
                    and native_session_id not in discovery.archive_states
                )
            ):
                # A complete discovery plus an authoritative state index can
                # distinguish deletion from an unreadable or partial source.
                try:
                    cleaned = self._passive_session_cleanup(session.id)
                except Exception:
                    LOGGER.warning(
                        "Unable to clean Chub state after native Session removal",
                        extra={"session_id": session.id},
                        exc_info=True,
                    )
                    continue
                if not cleaned:
                    continue
                self.store.delete(session.id)
                continue
            if changed:
                session.updated_at = utc_now()
                self.store.save(session)
        return default_discovery.sessions if default_discovery is not None else ()

    def _is_translation_workspace(self, cwd: Path) -> bool:
        try:
            return cwd.expanduser().resolve(strict=False) == (
                self.settings.ai_runtime.codex.runtime_dir / "translation-workspace"
            ).expanduser().resolve(strict=False)
        except OSError:
            return False

    @staticmethod
    def _project_native_state(session: AiSession, native: RuntimeNativeSession) -> bool:
        changed = False
        if native.title and not session.title:
            session.title = native.title[:48]
            changed = True
        if native.updated_at > session.updated_at:
            session.updated_at = native.updated_at
            changed = True
        return changed

    def _refresh_status(self, session: AiSession) -> None:
        running = self._quick_interaction_is_running(session.id)
        if session.status == "error" and not running:
            return
        next_status = "running" if running else ("stopped" if session.native_session_id else "new")
        if session.status != next_status:
            session.status = next_status
            session.updated_at = utc_now()
            self.store.save(session)

    @staticmethod
    def _runtime_api_error(error: RuntimeOperationError) -> ApiError:
        status_code = {
            "invalid_request": 400,
            "conflict": 409,
            "unavailable": 503,
        }[error.kind]
        return ApiError(status_code, error.code, error.message, source="runtime")
