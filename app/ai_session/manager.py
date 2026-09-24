from __future__ import annotations

import hashlib
import logging
import os
import secrets
import threading
import time
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
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
from app.ai_runtime.codex_plugin import DEVELOPMENT_CODEX_IMPLEMENTATION_ID
from app.ai_runtime.development_plugins import (
    DevelopmentRuntimePlugin,
    discover_development_runtime_plugins,
)
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
    SessionKind,
    TurnActivity,
    normalize_utc_datetime,
    utc_now,
)
from app.ai_session.store import AiSessionStore, AiSessionStoreUnavailable
from app.ai_session.api_models import (
    NativeSessionInfo,
    SessionInfo,
    SessionUsage,
    RuntimeManagementData,
    RuntimeManagementItem,
    RuntimeImplementationData,
    RuntimeImplementationItem,
    WorkspaceInfo,
)
from app.ai_runtime.contracts import (
    RuntimeModelCatalogData,
    RuntimeModelInfoData,
    RuntimeModelReasoningLevelData,
)
from app.core.config import PROJECT_ROOT, Settings
from app.core.response import ApiError


LOGGER = logging.getLogger("hub.ai_session")
NATIVE_ACTION_REF_TTL_SECONDS = 300
MAX_NATIVE_ACTION_REFS = 256
NEW_SESSION_RUNTIME_CAPABILITIES = {
    "runtime_status",
    "native_session_mapping",
    "session_resume",
    "session_archive",
    "writer_probe",
    "model_catalog",
    "permission_profiles",
}


class _UnavailableRuntimeRateLimits:
    def read(self, *, force: bool = False):
        del force
        raise RuntimeOperationError(
            "runtime_unavailable",
            "Runtime is not installed",
            kind="unavailable",
        )


class _UnavailableRuntime:
    rate_limits = _UnavailableRuntimeRateLimits()

    def __init__(self, runtime_id: str) -> None:
        self.descriptor = RuntimeDescriptor(
            runtime_id=runtime_id,
            capabilities=frozenset(),
        )
        self.display_name = runtime_id
        self._runtime_id = runtime_id

    def status(self) -> RuntimeStatus:
        return RuntimeStatus(
            runtime_id=self._runtime_id,
            available=False,
            reason=f"{self._runtime_id} Runtime is not installed",
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
            f"{self._runtime_id} Runtime is not installed",
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
        session_store_path = settings.ai_runtime.shared.state_dir / "ai-sessions.json"
        AiSessionStore.discard_legacy_session_state(session_store_path)
        self.store = AiSessionStore(session_store_path)
        self.runtime_enablement = RuntimeEnablementStore(
            settings.ai_runtime.shared.state_dir / "runtime-enablement.json"
        )
        self.runtime_implementation_preferences = RuntimeImplementationPreferencesStore(
            settings.ai_runtime.shared.state_dir / "runtime-implementation-preferences.json"
        )
        if development_runtime_plugins is None:
            (
                self._development_runtime_plugins,
                self._development_runtime_plugin_records,
                self._development_runtime_plugin_failures,
            ) = discover_development_runtime_plugins(settings)
        else:
            self._development_runtime_plugins = development_runtime_plugins
            self._development_runtime_plugin_records = ()
            self._development_runtime_plugin_failures = ()
        self.runtime_plugin_service = RuntimePluginService(settings)
        self.runtime_plugin_recovery = (
            self.runtime_plugin_service.recover_incomplete_activation()
        )
        self.runtime_id = ""
        self.runtime_plugins = RuntimePluginRegistry()
        self.runtime_plugin_failures = ()
        self.runtime_registry = RuntimeRegistry([])
        self.runtime_adapters: dict[str, object] = {}
        self.default_implementation_ids: dict[str, str] = {}
        self.default_implementation_id: str | None = None
        self.runtime_adapter = _UnavailableRuntime("ai-runtime")
        self.runtime_settings_store = AiRuntimeSettingsStore()
        self._lock = threading.RLock()
        self._native_action_refs: dict[str, tuple[str, str, str, float]] = {}
        self._native_action_refs_by_native_id: dict[tuple[str, str], str] = {}
        self._native_actions_in_progress: set[tuple[str, str]] = set()
        self._native_discovery_implementations: dict[tuple[str, str], str] = {}
        self._quick_interaction_is_running: Callable[[str], bool] = lambda _id: False
        self._passive_session_cleanup: Callable[[str], bool] = lambda _id: True
        self._system_upgrade_writes_blocked: Callable[[], bool] = lambda: False
        # The plugin lifecycle service is composed after this manager.  Keep the
        # standalone manager usable in focused tests, then let the application
        # install the authoritative import/enablement reader.
        self._runtime_plugin_lifecycle_state: Callable[[str], tuple[bool, bool]] = (
            lambda _implementation_id: (True, True)
        )
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
                in self.runtime_plugins.implementation_ids()
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
        failures = self._development_runtime_plugin_failures + failures
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
        self.default_implementation_ids = self._resolve_default_implementation_ids()
        self.sync_default_runtime_selection()

    def development_runtime_plugins(self) -> tuple[DevelopmentRuntimePlugin, ...]:
        return self._development_runtime_plugin_records

    def development_runtime_implementation_ids(self) -> tuple[str, ...]:
        return self._development_runtime_plugins.implementation_ids()

    def refresh_development_runtime_plugins(
        self,
        implementation_id: str,
    ) -> tuple[
        RuntimePluginRegistry,
        tuple[DevelopmentRuntimePlugin, ...],
        tuple[RuntimePluginLoadFailure, ...],
    ]:
        """Reload all checked-out sources and confirm one requested slot."""
        previous = (
            self._development_runtime_plugins,
            self._development_runtime_plugin_records,
            self._development_runtime_plugin_failures,
        )
        try:
            (
                self._development_runtime_plugins,
                self._development_runtime_plugin_records,
                self._development_runtime_plugin_failures,
            ) = discover_development_runtime_plugins(
                self.settings,
            )
            self.refresh_runtime_plugins()
            if implementation_id not in self.runtime_plugins.implementation_ids():
                raise ApiError(
                    503,
                    "development_runtime_plugin_refresh_unconfirmed",
                    "开发 Runtime 插件未能通过 Web 注册表校验。",
                )
            return previous
        except Exception:
            (
                self._development_runtime_plugins,
                self._development_runtime_plugin_records,
                self._development_runtime_plugin_failures,
            ) = previous
            self.refresh_runtime_plugins()
            raise

    def restore_development_runtime_plugins(
        self,
        previous: tuple[
            RuntimePluginRegistry,
            tuple[DevelopmentRuntimePlugin, ...],
            tuple[RuntimePluginLoadFailure, ...],
        ],
    ) -> None:
        """Restore Web's prior development plugin after Worker rejects a refresh."""
        with self._lock:
            (
                self._development_runtime_plugins,
                self._development_runtime_plugin_records,
                self._development_runtime_plugin_failures,
            ) = previous
            self.refresh_runtime_plugins()

    def _activate_default_implementation(self) -> None:
        try:
            adapter = self.runtime_registry.require(
                self.default_implementation_id or "__missing__",
                NEW_SESSION_RUNTIME_CAPABILITIES,
            )
        except RuntimeOperationError:
            # The control plane must remain available before any Runtime module
            # is installed.  ``runtime_id`` is empty in that state, while a
            # RuntimeDescriptor requires a valid identifier.
            adapter = _UnavailableRuntime(self.runtime_id or "ai-runtime")
        if adapter is not self.runtime_adapter:
            self.runtime_adapter = adapter

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
            if module_id in self.runtime_plugins.implementation_ids():
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

    def clear_runtime_plugin_state(self, runtime_id: str) -> None:
        """Clear Chub-owned state covered by a Runtime upgrade boundary."""
        # Implementations share one logical Runtime mapping. Replacing or
        # removing one version must never discard shared Session/history.
        enablement = self.runtime_enablement.read()
        disabled = [
            item
            for item in enablement.disabled_runtime_ids
            if item != runtime_id
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

    def _resolve_default_implementation_ids(self) -> dict[str, str]:
        try:
            preferences = self.runtime_implementation_preferences.read()
        except RuntimeImplementationPreferencesUnavailable:
            return {}
        defaults = dict(preferences.default_implementation_ids)

        selected_defaults: dict[str, str] = {}
        for runtime_id in self.runtime_plugins.runtime_ids():
            available = sorted(
                implementation_id
                for implementation_id in self.runtime_plugins.implementation_ids(runtime_id)
                if (
                    (adapter := self.runtime_adapters.get(implementation_id))
                    is not None
                    and adapter.status().available
                    and implementation_id
                    not in preferences.disabled_implementation_ids
                    and self._implementation_supports_new_session(implementation_id)
                )
            )
            configured = defaults.get(runtime_id)
            registered = set(self.runtime_plugins.implementation_ids(runtime_id))
            # A saved default remains the user's preference while its module is
            # registered and not explicitly disabled. A transient dependency or
            # health failure must not silently replace it with another version.
            if (
                configured in registered
                and configured not in preferences.disabled_implementation_ids
            ):
                selected_defaults[runtime_id] = configured
            elif configured is None and available:
                selected_defaults[runtime_id] = (
                    DEVELOPMENT_CODEX_IMPLEMENTATION_ID
                    if runtime_id == "codex"
                    and DEVELOPMENT_CODEX_IMPLEMENTATION_ID in available
                    else available[0]
                )

        if selected_defaults != defaults:
            self.runtime_implementation_preferences.save(
                preferences.model_copy(
                    update={"default_implementation_ids": selected_defaults}
                )
            )
        return selected_defaults

    def _resolve_default_runtime_id(self) -> str:
        try:
            general = self.runtime_settings_store.read_general()
        except RuntimeSettingsStoreUnavailable:
            # The configured default remains authoritative for new Sessions.
            # When that configuration cannot be read, retain a deterministic
            # in-memory view for read-only projections without identifying a
            # particular Runtime or silently persisting a replacement.
            candidates = sorted(self.default_implementation_ids)
            return candidates[0] if candidates else ""
        known_runtime_ids = set(self.runtime_plugins.runtime_ids())
        if general.default_runtime_id in known_runtime_ids:
            return general.default_runtime_id
        candidates = sorted(self.default_implementation_ids)
        if not candidates:
            return general.default_runtime_id or ""
        runtime_id = candidates[0]
        self.runtime_settings_store.save_general(
            general.model_copy(update={"default_runtime_id": runtime_id})
        )
        return runtime_id

    def configured_default_runtime_id(self) -> str:
        """Read the live default used by future Session creation.

        This deliberately does not require the target Runtime to be healthy:
        status pages may still show the configured choice while submission is
        unavailable, and the submission path performs the strict gate.
        """
        try:
            runtime_id = self.runtime_settings_store.read_general().default_runtime_id
            if runtime_id:
                return runtime_id
            raise ApiError(
                409,
                "session_default_runtime_unavailable",
                "当前没有可用于新建 Session 的默认 Runtime。",
            )
        except RuntimeSettingsStoreUnavailable as exc:
            raise ApiError(
                503,
                "ai_runtime_settings_unavailable",
                "无法读取新建 Session 默认配置，请稍后重试。",
            ) from exc

    def sync_default_runtime_selection(self) -> None:
        """Refresh the active default view after a general-settings update.

        This only changes the adapter used for unbound/default operations.
        Persisted Sessions and accepted tasks continue to resolve their own
        Runtime and implementation snapshots.
        """
        with self._lock:
            self.runtime_id = self._resolve_default_runtime_id()
            self.default_implementation_id = self._default_implementation_for_runtime(
                self.runtime_id
            )
            self._activate_default_implementation()

    def select_new_session_runtime(
        self,
        *,
        required_capabilities: frozenset[str] | set[str] = NEW_SESSION_RUNTIME_CAPABILITIES,
    ) -> tuple[str, str]:
        """Resolve and validate the Runtime snapshot for one new Session."""
        runtime_id = self.configured_default_runtime_id()
        implementation_id = self.new_session_implementation_id(
            runtime_id,
            required_capabilities=required_capabilities,
        )
        return runtime_id, implementation_id

    def new_session_implementation_id(
        self,
        runtime_id: str,
        *,
        required_capabilities: frozenset[str] | set[str] = NEW_SESSION_RUNTIME_CAPABILITIES,
    ) -> str:
        """Validate one Runtime as a target for a newly created Session."""
        if runtime_id not in self.runtime_plugins.runtime_ids():
            raise ApiError(
                409,
                "session_default_runtime_unavailable",
                "当前默认 Runtime 不可用于新建 Session。",
            )
        self._require_runtime_submission(runtime_id)
        implementation_id = self.default_submission_implementation_id(runtime_id)
        try:
            self.runtime_registry.require(
                implementation_id,
                NEW_SESSION_RUNTIME_CAPABILITIES | set(required_capabilities),
            )
        except RuntimeOperationError as exc:
            raise self._runtime_api_error(exc) from exc
        return implementation_id

    def _default_implementation_for_runtime(self, runtime_id: str) -> str | None:
        return self.default_implementation_ids.get(runtime_id)

    def _runtime_id_for_implementation(self, implementation_id: str) -> str | None:
        adapter = self.runtime_adapters.get(implementation_id)
        return None if adapter is None else adapter.descriptor.runtime_id

    def _implementation_supports_new_session(self, implementation_id: str) -> bool:
        try:
            self.runtime_registry.require(
                implementation_id,
                NEW_SESSION_RUNTIME_CAPABILITIES,
            )
        except RuntimeOperationError:
            return False
        return True

    def require_implementation_submission(self, implementation_id: str) -> None:
        try:
            disabled_runtimes = set(self.runtime_enablement.read().disabled_runtime_ids)
        except RuntimeEnablementStoreUnavailable as exc:
            raise ApiError(
                503,
                "ai_runtime_enablement_unavailable",
                "AI Runtime 启用状态不可用，请稍后重试。",
            ) from exc
        runtime_id = self._runtime_id_for_implementation(implementation_id)
        if runtime_id is None:
            raise ApiError(503, "runtime_implementation_unavailable", "当前 Runtime 版本不可用，无法提交新任务。")
        if runtime_id in disabled_runtimes:
            raise ApiError(
                409,
                "ai_runtime_disabled",
                "当前 AI Runtime 已停用，无法提交新的 AI 任务。",
            )
        self.require_implementation_lifecycle_available(implementation_id)

    def set_runtime_plugin_lifecycle_state_reader(
        self,
        reader: Callable[[str], tuple[bool, bool]],
    ) -> None:
        """Install the lifecycle-owned import and enablement state reader."""
        self._runtime_plugin_lifecycle_state = reader

    def require_implementation_lifecycle_available(
        self, implementation_id: str
    ) -> None:
        imported, enabled = self._runtime_plugin_lifecycle_state(implementation_id)
        if not imported:
            raise ApiError(409, "runtime_plugin_not_imported", "当前 Runtime 插件尚未导入，无法使用。")
        if not enabled:
            raise ApiError(409, "runtime_plugin_disabled", "当前 Runtime 插件已停用，无法使用。")
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

    def default_submission_implementation_id(self, runtime_id: str | None = None) -> str:
        runtime_id = runtime_id or self.runtime_id
        implementation_id = self._default_implementation_for_runtime(runtime_id)
        if implementation_id is None:
            raise ApiError(503, "runtime_default_implementation_unavailable", "默认 Runtime 版本不可用。")
        self.require_implementation_submission(implementation_id)
        return implementation_id

    def session_implementation_id(self, session_id: str) -> str:
        """Return the implementation pinned at Session creation."""
        with self._lock:
            session = self.get_session(session_id, reconcile=False)
            if session.implementation_id is not None:
                return session.implementation_id
            raise ApiError(
                409,
                "session_implementation_missing",
                "该 Session 属于已清理的旧 Runtime 运行态。",
            )

    def read_runtime_implementations(
        self,
        runtime_id: str | None = None,
    ) -> RuntimeImplementationData:
        selected_runtime_id = runtime_id or self.runtime_id
        if selected_runtime_id not in self.runtime_plugins.runtime_ids():
            raise ApiError(404, "runtime_not_found", "Runtime 不存在。")
        try:
            preferences = self.runtime_implementation_preferences.read()
        except RuntimeImplementationPreferencesUnavailable as exc:
            raise ApiError(503, "runtime_implementation_preferences_unavailable", str(exc)) from exc
        installed, _failures = self.runtime_plugin_service.discover()
        manifests = {item.manifest.implementation_id: item.manifest for item in installed}
        items: list[RuntimeImplementationItem] = []
        development_implementation_ids = set(
            self._development_runtime_plugins.implementation_ids()
        )
        for implementation_id in self.runtime_plugins.implementation_ids(
            selected_runtime_id
        ):
            module = self.runtime_plugins.require(implementation_id)
            adapter = self.runtime_adapters.get(implementation_id)
            status = adapter.status() if adapter is not None else None
            manifest = manifests.get(implementation_id)
            imported, lifecycle_enabled = self._runtime_plugin_lifecycle_state(implementation_id)
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
                    imported=imported,
                    enabled=(
                        imported
                        and lifecycle_enabled
                        and implementation_id not in preferences.disabled_implementation_ids
                    ),
                    healthy=status is not None and status.available,
                    is_default=(
                        implementation_id
                        == self._default_implementation_for_runtime(
                            selected_runtime_id
                        )
                    ),
                    compatibility_id=(
                        module.descriptor.native_session_compatibility_id
                    ),
                    removable=implementation_id not in development_implementation_ids,
                    reason=None if status is None else status.reason,
                )
            )
        return RuntimeImplementationData(
            runtime_id=selected_runtime_id,
            default_implementation_id=self._default_implementation_for_runtime(
                selected_runtime_id
            ),
            implementations=items,
        )

    def update_runtime_implementation_enabled(
        self, implementation_id: str, enabled: bool
    ) -> RuntimeImplementationData:
        with self._lock:
            runtime_id = self._runtime_id_for_implementation(implementation_id)
            if runtime_id is None:
                raise ApiError(404, "runtime_implementation_not_found", "Runtime 版本不存在。")
            preferences = self.runtime_implementation_preferences.read()
            disabled = set(preferences.disabled_implementation_ids)
            if enabled:
                adapter = self.runtime_adapters.get(implementation_id)
                if adapter is None or not adapter.status().available:
                    raise ApiError(409, "runtime_implementation_unavailable", "不可用的 Runtime 版本不能启用。")
                disabled.discard(implementation_id)
            else:
                # A checked-out development implementation can be cancelled even
                # when it is the only candidate.  This only changes the default
                # for future work; existing Sessions keep their implementation
                # snapshot and the source directory is deliberately untouched.
                defaults = dict(preferences.default_implementation_ids)
                default_implementation_id = defaults.get(runtime_id)
                if implementation_id == default_implementation_id:
                    defaults.pop(runtime_id, None)
                disabled.add(implementation_id)
            next_preferences = preferences.model_copy(
                update={
                    "disabled_implementation_ids": sorted(disabled),
                    "default_implementation_ids": (
                        defaults
                        if not enabled
                        else preferences.default_implementation_ids
                    ),
                }
            )
            self.runtime_implementation_preferences.save(next_preferences)
            try:
                self.default_implementation_ids = self._resolve_default_implementation_ids()
                self.default_implementation_id = self._default_implementation_for_runtime(
                    self.runtime_id
                )
                self._activate_default_implementation()
                return self.read_runtime_implementations(runtime_id)
            except Exception as update_error:
                try:
                    self.restore_runtime_implementation_preferences(preferences)
                except Exception as rollback_error:
                    raise ApiError(
                        503,
                        "runtime_implementation_state_unconfirmed",
                        "Runtime 实现启停状态无法确认，请恢复本机状态存储后重试。",
                    ) from rollback_error
                raise update_error

    def runtime_id_for_implementation(self, implementation_id: str) -> str:
        runtime_id = self._runtime_id_for_implementation(implementation_id)
        if runtime_id is None:
            raise ApiError(404, "runtime_implementation_not_found", "Runtime 版本不存在。")
        return runtime_id

    def restore_runtime_implementation_preferences(
        self,
        preferences: RuntimeImplementationPreferences,
    ) -> None:
        """Restore a lifecycle operation's exact pre-change implementation state."""
        with self._lock:
            self.runtime_implementation_preferences.save(preferences)
            self.default_implementation_ids = self._resolve_default_implementation_ids()
            self.default_implementation_id = self._default_implementation_for_runtime(
                self.runtime_id
            )
            self._activate_default_implementation()

    def update_default_implementation(
        self,
        implementation_id: str,
        *,
        runtime_id: str | None = None,
    ) -> RuntimeImplementationData:
        with self._lock:
            selected_runtime_id = runtime_id or self.runtime_id
            if implementation_id not in self.runtime_plugins.implementation_ids(
                selected_runtime_id
            ):
                raise ApiError(
                    404,
                    "runtime_implementation_not_found",
                    "目标 Runtime 不包含指定的 Runtime 版本。",
                )
            self.require_implementation_lifecycle_available(implementation_id)
            previous_id = self.default_implementation_id
            previous_defaults = self.default_implementation_ids
            previous_adapter = self.runtime_adapter
            preferences = self.runtime_implementation_preferences.read()
            if selected_runtime_id == self.runtime_id:
                self.default_implementation_id = implementation_id
            self.default_implementation_ids = {
                **self.default_implementation_ids,
                selected_runtime_id: implementation_id,
            }
            try:
                if selected_runtime_id == self.runtime_id:
                    self._activate_default_implementation()
                self.runtime_implementation_preferences.save(
                    preferences.model_copy(
                        update={
                            "default_implementation_ids": self.default_implementation_ids
                        }
                    )
                )
            except Exception:
                self.default_implementation_id = previous_id
                self.default_implementation_ids = previous_defaults
                if (
                    selected_runtime_id == self.runtime_id
                    and self.runtime_adapter is not previous_adapter
                ):
                    self._activate_default_implementation()
                raise
            return self.read_runtime_implementations(selected_runtime_id)

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
        """Return Codex-private quota support without following the global default.

        The default Runtime may be another implementation.  Codex account
        authentication and quota capability remain Codex-specific integrations,
        so they must never be redirected to that other Runtime.
        """
        implementation_id = self.default_submission_implementation_id("codex")
        adapter = self.runtime_adapters.get(implementation_id)
        rate_limits = getattr(adapter, "rate_limits", None)
        if rate_limits is None:
            raise ApiError(
                503,
                "codex_rate_limits_unavailable",
                "Codex Runtime 额度能力暂时不可用。",
            )
        return rate_limits

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
            implementation_ids = self.runtime_plugins.implementation_ids(runtime_id)
            if not any(
                self._runtime_plugin_lifecycle_state(implementation_id)[0]
                for implementation_id in implementation_ids
            ):
                continue
            implementation_id = self._default_implementation_for_runtime(runtime_id)
            display_implementation_id = implementation_id or next(
                iter(implementation_ids),
                None,
            )
            adapter = (
                self.runtime_adapters.get(implementation_id)
                if implementation_id is not None
                else None
            )
            status = adapter.status() if adapter is not None else None
            imported, lifecycle_enabled = (
                self._runtime_plugin_lifecycle_state(implementation_id)
                if implementation_id is not None
                else (False, False)
            )
            runtimes.append(
                RuntimeManagementItem(
                    runtime_id=runtime_id,
                    name=self.runtime_plugins.require_navigation(
                        display_implementation_id or runtime_id
                    ).name,
                    enabled=(
                        runtime_id not in disabled
                        and imported
                        and lifecycle_enabled
                    ),
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
                selected_id = self._default_implementation_for_runtime(runtime_id)
                if selected_id is None:
                    raise ApiError(
                        503,
                        "runtime_default_implementation_unavailable",
                        "默认 Runtime 版本不可用。",
                    )
                self.require_implementation_lifecycle_available(selected_id)
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
            ("workspace", "Workspace", self.settings.ai_runtime.shared.workspace),
            ("chub", "Chub", PROJECT_ROOT),
            *[
                (workspace.id, workspace.name, workspace.path)
                for workspace in self.settings.ai_runtime.shared.extra_workspaces
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

    def show_internal_sessions(self) -> bool:
        with self._lock:
            self._require_store()
            try:
                return self.store.show_internal_sessions
            except OSError as exc:
                raise ApiError(
                    503,
                    "ai_session_state_unavailable",
                    "内部会话显示设置暂时无法读取。",
                ) from exc

    def set_show_internal_sessions(self, show: bool) -> bool:
        with self._lock:
            self._require_store()
            try:
                return self.store.set_show_internal_sessions(show)
            except OSError as exc:
                raise ApiError(
                    503,
                    "ai_session_state_unavailable",
                    "内部会话显示设置未能保存。",
                ) from exc

    def list_sessions_with_native_sessions(self) -> tuple[list[SessionInfo], list[NativeSessionInfo]]:
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
                ]
            )

            def native_writer_state(native: RuntimeNativeSession) -> str:
                try:
                    implementation_id = self._native_discovery_implementations.get(
                        (native.runtime_id, native.native_session_id)
                    )
                    adapter = self.runtime_adapters.get(implementation_id)
                    if adapter is None:
                        adapter = self.runtime_adapter
                    return "held" if adapter.has_active_writer(native.native_session_id) else "free"
                except RuntimeOperationError:
                    return "unknown"

            return (
                [self._public(session) for session in self.store.list()],
                [
                    NativeSessionInfo(
                        runtime_id=item.runtime_id,
                        cwd=self._native_session_cwd_display(item.cwd),
                        title=item.title,
                        created_at=item.created_at,
                        updated_at=item.updated_at,
                        writer_lock_state=(writer_state := native_writer_state(item)),
                        chub_writer_lock_state=(
                            "held"
                            if (item.runtime_id, item.native_session_id)
                            in self._native_actions_in_progress
                            else "free"
                        ),
                        native_action_ref=(
                            self._issue_native_action_ref(
                                item.runtime_id,
                                item.native_session_id,
                            )
                            if writer_state == "free"
                            and (item.runtime_id, item.native_session_id)
                            not in self._native_actions_in_progress
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

    def _issue_native_action_ref(
        self,
        runtime_id: str,
        native_session_id: str | None = None,
    ) -> str:
        if native_session_id is None:
            native_session_id = runtime_id
            runtime_id = self.runtime_id
        now = time.monotonic()
        self._prune_native_action_refs(now)
        key = (runtime_id, native_session_id)
        existing_reference = self._native_action_refs_by_native_id.get(key)
        if existing_reference is not None:
            existing = self._native_action_refs.get(existing_reference)
            if existing is not None and existing[3] > now:
                return existing_reference
        while len(self._native_action_refs) >= MAX_NATIVE_ACTION_REFS:
            self._discard_native_action_ref(next(iter(self._native_action_refs)))
        implementation_id = self._native_discovery_implementations.get(
            key,
            self._default_implementation_for_runtime(runtime_id),
        )
        if implementation_id is None:
            raise ApiError(409, "native_session_action_stale", "Native Session 列表已变化，请刷新后重试。")
        reference = secrets.token_urlsafe(24)
        self._native_action_refs[reference] = (
            runtime_id,
            native_session_id,
            implementation_id,
            now + NATIVE_ACTION_REF_TTL_SECONDS,
        )
        self._native_action_refs_by_native_id[key] = reference
        return reference

    def native_action_audit_target(self, native_action_ref: str) -> str:
        """Return a stable, non-sensitive target for an issued action reference."""
        with self._lock:
            self._prune_native_action_refs(time.monotonic())
            record = self._native_action_refs.get(native_action_ref)
            if record is None:
                return "native:unresolved"
            target = f"{record[0]}:{record[1]}"
            return f"native:{hashlib.sha256(target.encode()).hexdigest()[:12]}"

    def _prune_native_action_refs(self, now: float) -> None:
        for reference, (_runtime_id, _native_session_id, _implementation_id, expires_at) in tuple(self._native_action_refs.items()):
            if expires_at <= now:
                self._discard_native_action_ref(reference)

    def _discard_native_action_ref(self, reference: str) -> tuple[str, str, str, float] | None:
        record = self._native_action_refs.pop(reference, None)
        if record is not None:
            key = (record[0], record[1])
            if self._native_action_refs_by_native_id.get(key) == reference:
                self._native_action_refs_by_native_id.pop(key, None)
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
            if record is None or record[3] <= time.monotonic():
                raise ApiError(
                    409,
                    "native_session_action_stale",
                    "Native Session 列表已变化，请刷新后重试。",
                )
            runtime_id, native_session_id, implementation_id, _expires_at = record
            native_sessions = self._sync_bound_native_sessions()
            bound_native_ids = {
                (session.runtime_id, session.native_session_id)
                for session in self.store.list()
                if session.native_session_id is not None
            }
            native = next(
                (
                    item
                    for item in native_sessions
                    if item.runtime_id == runtime_id
                    and item.native_session_id == native_session_id
                ),
                None,
            )
            if native is None or (runtime_id, native_session_id) in bound_native_ids:
                raise ApiError(
                    409,
                    "native_session_action_stale",
                    "Native Session 列表已变化，请刷新后重试。",
                )
            if self._native_discovery_implementations.get(
                (runtime_id, native_session_id)
            ) != implementation_id:
                raise ApiError(
                    409,
                    "native_session_action_stale",
                    "Native Session 列表已变化，请刷新后重试。",
                )
            try:
                adapter = self.runtime_adapters.get(implementation_id)
                if adapter is None:
                    raise RuntimeOperationError("runtime_unavailable", "Runtime implementation is unavailable")
                if adapter.has_active_writer(native_session_id):
                    raise ApiError(
                        409,
                        "session_writer_active",
                        "This is open in another app, close it there to continue here.",
                    )
            except RuntimeOperationError as exc:
                raise ApiError(
                    503,
                    "native_session_writer_unknown",
                    "Native Session 占用状态无法确认，请刷新后重试。",
                ) from exc
            key = (runtime_id, native_session_id)
            if key in self._native_actions_in_progress:
                raise ApiError(
                    409,
                    "native_session_action_in_progress",
                    "Native Session 操作正在进行，请等待后刷新。",
                )
            self._native_actions_in_progress.add(key)
        try:
            adapter.run_native_action(action, native_session_id)
            confirmed = (
                adapter.native_session_archive_state(native_session_id) is True
                if action == "archive"
                else adapter.native_session_deleted_state(native_session_id) is True
            )
        except RuntimeOperationError as exc:
            raise self._runtime_api_error(exc) from exc
        finally:
            with self._lock:
                self._native_actions_in_progress.discard(key)
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
                raise ApiError(404, "session_not_found", "AI Session not found")
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
        *,
        session_kind: SessionKind = "user",
        creation_request_id: str | None = None,
        creation_request_fingerprint: str | None = None,
    ) -> SessionInfo:
        # This is the only shared writer for browser Session creation.  Keep
        # lookup and persistence in one critical section so duplicate HTTP
        # delivery with the same request ID replays one logical Session.
        with self._lock:
            self._require_store()
            if creation_request_id is not None:
                if creation_request_fingerprint is None:
                    raise ValueError(
                        "Session creation request IDs require a request fingerprint"
                    )
                existing = next(
                    (
                        session
                        for session in self.store.list()
                        if session.creation_request_id == creation_request_id
                    ),
                    None,
                )
                if existing is not None:
                    if (
                        existing.creation_request_fingerprint
                        != creation_request_fingerprint
                    ):
                        raise ApiError(
                            409,
                            "session_creation_request_conflict",
                            "本次 Session 创建请求与已有结果不一致，请关闭窗口后重新创建。",
                        )
                    return self._public(existing)
            try:
                defaults = self.runtime_settings_store.read_general()
            except RuntimeSettingsStoreUnavailable as exc:
                raise ApiError(
                    503,
                    "ai_runtime_settings_unavailable",
                    "无法读取新建 Session 默认配置，请稍后重试。",
                ) from exc
            runtime_id, implementation_id = self.select_new_session_runtime()
            if permission_mode is None or model is None or reasoning_effort is None:
                permission_mode = permission_mode or defaults.new_session_permission
                model = model if model is not None else defaults.model
                reasoning_effort = (
                    reasoning_effort
                    if reasoning_effort is not None
                    else defaults.reasoning_effort
                )
            if permission_mode == "ask":
                raise ApiError(
                    409,
                    "quick_interaction_ask_not_supported",
                    "快速交互不支持 Ask for approval，请选择只读、自动审核或完全访问权限。",
                )
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
                    "workspace_unavailable",
                    "Selected workspace is unavailable",
                )
            session = AiSession(
                id=str(uuid.uuid4()),
                session_kind=session_kind,
                creation_request_id=creation_request_id,
                creation_request_fingerprint=creation_request_fingerprint,
                runtime_id=runtime_id,
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

    def read_model_catalog(
        self,
        *,
        implementation_id: str | None = None,
    ) -> RuntimeModelCatalogData:
        try:
            adapter = (
                self.runtime_adapters[implementation_id]
                if implementation_id is not None
                else self.runtime_adapter
            )
            catalog = adapter.read_model_catalog()
        except RuntimeOperationError as exc:
            raise self._runtime_api_error(exc) from exc
        return RuntimeModelCatalogData(
            models=[
                RuntimeModelInfoData(
                    id=model.id,
                    name=model.name,
                    description=model.description,
                    default_level=model.default_level,
                    levels=[
                        RuntimeModelReasoningLevelData(
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

    def read_session_model_catalog(self, session_id: str) -> RuntimeModelCatalogData:
        """Read the catalog from the implementation pinned to one Session."""
        return self.read_model_catalog(
            implementation_id=self.session_implementation_id(session_id)
        )

    def update_session_configuration(
        self,
        session_id: str,
        permission_mode: PermissionMode,
        model: str | None,
        reasoning_effort: str | None,
    ) -> SessionInfo:
        if permission_mode == "ask":
            raise ApiError(
                409,
                "quick_interaction_ask_not_supported",
                "快速交互不支持 Ask for approval，请选择只读、自动审核或完全访问权限。",
            )
        with self._lock:
            session = self.get_session(session_id)
            self.require_implementation_submission(
                self.session_implementation_id(session.id)
            )
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
        with self._lock:
            session = self.get_session(session_id)
            self.require_implementation_submission(
                self.session_implementation_id(session.id)
            )
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
        self.require_implementation_lifecycle_available(implementation_id)
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
                "session_writer_active",
                "This is open in another app, close it there to continue here.",
            )
        if usage.owner == "unknown":
            raise ApiError(
                409,
                "session_usage_unknown",
                "无法确认 Session 占用状态，请刷新后重试。",
            )
        if usage.owner == "quick_worker" and usage.phase in {
            "running",
            "waiting_result",
        }:
            return usage
        raise ApiError(
            409,
            "session_not_running",
            "Session 当前没有正在执行的任务。",
        )

    def _resolve_session_usage(self, session: AiSession) -> SessionUsage:
        native_session_present = session.native_session_id is not None
        try:
            implementation_id = self.session_implementation_id(session.id)
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
        updated_at = normalize_utc_datetime(updated_at)
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
                raise ApiError(404, "session_not_found", "AI Session not found")
            session.activity = activity
            session.activity_source = source
            activity_at = normalize_utc_datetime(updated_at) if updated_at else utc_now()
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
            usage = self._resolve_session_usage(session)
            if usage.owner == "external":
                raise ApiError(
                    409,
                    "session_writer_active",
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
                raise ApiError(404, "session_not_found", "AI Session not found")
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
                        "该 Runtime 原生 Session 已归属于其他 Chub Session。",
                    )
            if (
                session.native_session_id is not None
                and session.native_session_id != native_session_id
            ):
                raise ApiError(
                    409,
                    "quick_interaction_native_session_conflict",
                    "Chub Session identity conflict: Runtime session identity does not "
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
                        "Chub Session identity conflict: Runtime session identity is "
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
                raise ApiError(404, "session_not_found", "AI Session not found")
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

    def discard_quick_native_claims(self) -> int:
        """Clear Chub-owned Quick Worker claims after its task projection is discarded."""
        with self._lock:
            self._require_store()
            cleared = 0
            for session in self.store.list():
                if session.quick_native_claim_task_id is None:
                    continue
                session.quick_native_claim_task_id = None
                session.quick_native_claim_execution_id = None
                session.updated_at = utc_now()
                self.store.save(session)
                cleared += 1
            return cleared

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
                "session_writer_active",
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
                if exc.code == "session_delete_failed":
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
                raise ApiError(404, "session_not_found", "AI Session not found")
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
                "session_writer_active",
                "This is open in another app, close it there to continue here.",
            )
        if usage.owner == "quick_worker" and usage.phase in {
            "running",
            "waiting_result",
        }:
            raise ApiError(
                409,
                "session_in_progress",
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
                if exc.code == "session_archive_failed":
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
            if exc.code != "session_not_found":
                raise
        session = self.store.get(session_id)
        self.store.delete(session_id)

    def archive_session(self, session_id: str) -> None:
        self.archive_native_session(session_id)
        self.finalize_archive_session(session_id)

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
            raise ApiError(503, "ai_runtime_unavailable", reason)

    def _require_runtime_submission(self, runtime_id: str) -> None:
        try:
            selected_id = self._default_implementation_for_runtime(runtime_id)
            if selected_id is None:
                raise RuntimeOperationError(
                    "runtime_default_implementation_unavailable",
                    "默认 Runtime 版本不可用。",
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
        imported, enabled = self._runtime_plugin_lifecycle_state(selected_id)
        if not imported:
            raise ApiError(409, "runtime_plugin_not_imported", "当前 Runtime 插件尚未导入，无法提交新的 AI 任务。")
        if not enabled:
            raise ApiError(409, "runtime_plugin_disabled", "当前 Runtime 插件已停用，无法提交新的 AI 任务。")

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
            session_kind=session.session_kind,
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
        implementation_ids = {
            session.implementation_id
            for session in stored
            if session.implementation_id is not None
        }
        implementation_ids.update(self.default_implementation_ids.values())
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
            if any(
                native.runtime_id != adapter.descriptor.runtime_id
                for native in discovery.sessions
            ):
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

        discovered_native_sessions: dict[tuple[str, str], RuntimeNativeSession] = {}
        discovery_implementations: dict[tuple[str, str], str] = {}
        for implementation_id, discovery in discoveries.items():
            for native in discovery.sessions:
                key = (native.runtime_id, native.native_session_id)
                current_implementation_id = discovery_implementations.get(key)
                if current_implementation_id is None or (
                    implementation_id
                    == self._default_implementation_for_runtime(native.runtime_id)
                ):
                    discovered_native_sessions[key] = native
                    discovery_implementations[key] = implementation_id
        self._native_discovery_implementations = discovery_implementations
        for session in stored:
            native_session_id = session.native_session_id
            if native_session_id is None:
                continue
            implementation_id = session.implementation_id
            if implementation_id is None:
                continue
            discovery = discoveries.get(implementation_id)
            if discovery is None:
                continue
            current = discovered_by_implementation[implementation_id].get(
                (session.runtime_id, native_session_id)
            )
            changed = False
            if current is not None:
                try:
                    changed = self._project_native_state(session, current) or changed
                except (TypeError, ValueError, OverflowError):
                    LOGGER.warning(
                        "Unable to project one discovered native Session",
                        extra={
                            "session_id": session.id,
                            "runtime_id": session.runtime_id,
                            "native_session_id": native_session_id,
                        },
                        exc_info=True,
                    )
                    continue
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
        return tuple(discovered_native_sessions.values())

    @staticmethod
    def _project_native_state(session: AiSession, native: RuntimeNativeSession) -> bool:
        changed = False
        if native.title and not session.title:
            session.title = native.title[:48]
            changed = True
        native_updated = native.updated_at
        session_updated = session.updated_at
        if native_updated.tzinfo is None:
            native_updated = native_updated.replace(tzinfo=UTC)
        if session_updated.tzinfo is None:
            session_updated = session_updated.replace(tzinfo=UTC)
        if native_updated > session_updated:
            session.updated_at = native_updated
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
