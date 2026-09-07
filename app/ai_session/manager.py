from __future__ import annotations

import logging
import os
import threading
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import Callable, Iterable

from app.ai_runtime import (
    DISCOVERED_RUNTIME_WORKSPACE_ID,
    RuntimeDescriptor,
    RuntimeNativeSession,
    RuntimeOperationError,
    RuntimeRegistry,
    RuntimeStatus,
)
from app.ai_runtime.external_modules import (
    ExternalRuntimeModuleService,
    RuntimeModuleActivation,
    RuntimeModuleLoadFailure,
    RuntimeModuleRemoval,
)
from app.ai_runtime.modules import BuiltinRuntimeModuleRegistry
from app.ai_runtime.codex_builtin import load_builtin_codex_module
from app.ai_runtime.enablement import (
    RuntimeEnablement,
    RuntimeEnablementStore,
    RuntimeEnablementStoreUnavailable,
)
from app.ai_runtime.general_settings import AiRuntimeSettingsStore
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
from app.ai_session.session_defaults import (
    SessionDefaults,
    SessionDefaultsStore,
    SessionDefaultsStoreUnavailable,
)
from app.ai_session.supervisor import InteractiveSupervisor
from app.codex.models import (
    CodexModelCatalogData,
    CodexModelInfo,
    CodexReasoningLevel,
    SessionMode,
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


_LEGACY_TRANSLATION_WORKSPACE = (
    PROJECT_ROOT / "data/runtime/codex/translation-workspace"
)
_LEGACY_TRANSLATION_TITLE_PREFIX = "You are a text editor and translator."
_MAX_LEGACY_TRANSLATION_ARCHIVES_PER_START = 20
_NATIVE_DISCOVERY_CLAIM_GRACE_SECONDS = 5


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

    @staticmethod
    def terminal_backend_matches(_command: tuple[str, ...], _session_id: str) -> bool:
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
    view. Worker results and terminal Hooks establish Chub-created Session
    ownership; discovery creates records only for native Sessions that remain
    unclaimed after a short, non-blocking observation window.
    """

    def __init__(
        self,
        settings: Settings,
        *,
        builtin_runtime_modules: BuiltinRuntimeModuleRegistry | None = None,
    ) -> None:
        self.settings = settings
        self.store = AiSessionStore(
            settings.ai_runtime.codex.data_file.with_name("ai-sessions.json")
        )
        self.session_defaults = SessionDefaultsStore(
            settings.ai_runtime.codex.data_file.with_name("session-creation-defaults.json")
        )
        self.runtime_enablement = RuntimeEnablementStore(
            settings.ai_runtime.codex.data_file.with_name("runtime-enablement.json")
        )
        self.runtime_implementation_preferences = RuntimeImplementationPreferencesStore(
            settings.ai_runtime.codex.data_file.with_name("runtime-implementation-preferences.json")
        )
        self._builtin_runtime_modules = (
            BuiltinRuntimeModuleRegistry([load_builtin_codex_module(settings)])
            if builtin_runtime_modules is None
            else builtin_runtime_modules
        )
        self.runtime_module_service = ExternalRuntimeModuleService(settings)
        self.runtime_module_recovery = (
            self.runtime_module_service.recover_incomplete_activation()
        )
        self.runtime_id = "codex"
        self.runtime_modules = BuiltinRuntimeModuleRegistry()
        self.runtime_module_failures = ()
        self.runtime_registry = RuntimeRegistry([])
        self.runtime_adapters: dict[str, object] = {}
        self.default_implementation_id: str | None = None
        self.runtime_adapter = _UnavailableCodexRuntime()
        self.runtime_settings_store = AiRuntimeSettingsStore()
        self.supervisor = InteractiveSupervisor(
            self.runtime_adapter,
            ticket_ttl_seconds=settings.ai_runtime.codex.ticket_ttl_seconds,
        )
        self._lock = threading.RLock()
        self._quick_interaction_is_running: Callable[[str], bool] = lambda _id: False
        self._system_upgrade_writes_blocked: Callable[[], bool] = lambda: False
        self._pending_native_discoveries: dict[tuple[str, str], datetime] = {}
        self.refresh_external_runtime_modules()
        self._reconcile_saved_terminals()

    def install_runtime_module(
        self,
        archive: bytes,
        *,
        source_name: str,
        operation_id: str,
    ) -> RuntimeModuleActivation:
        activation = self.runtime_module_service.install(
            archive,
            source_name=source_name,
            operation_id=operation_id,
        )
        try:
            self.refresh_external_runtime_modules()
            activated = (
                activation.installed.manifest.implementation_id
                in self.runtime_modules.implementation_ids("codex")
            )
        except Exception:
            self.runtime_module_service.rollback(activation)
            self.refresh_external_runtime_modules()
            raise
        if not activated:
            self.runtime_module_service.rollback(activation)
            self.refresh_external_runtime_modules()
            raise ApiError(
                503,
                "runtime_module_activation_unconfirmed",
                "Runtime 模块已写入，但 Web 注册表未能确认激活。",
            )
        return activation

    def refresh_external_runtime_modules(self) -> None:
        modules, failures = self.runtime_module_service.build_registry(
            self._builtin_runtime_modules
        )
        available_modules = BuiltinRuntimeModuleRegistry()
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
                    RuntimeModuleLoadFailure(
                        implementation_id,
                        "Runtime 模块装配失败。",
                    ),
                )
        self.runtime_modules = available_modules
        self.runtime_module_failures = failures
        self.runtime_registry = RuntimeRegistry(adapters)
        self.runtime_adapters = {
            adapter.descriptor.effective_implementation_id: adapter
            for adapter in adapters
        }
        self.default_implementation_id = self._resolve_default_implementation_id()
        self._activate_default_implementation()

    def _activate_default_implementation(self) -> None:
        try:
            adapter = self.runtime_registry.require(
                self.default_implementation_id or "__missing__",
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
        except RuntimeOperationError:
            adapter = _UnavailableCodexRuntime()
        if adapter is not self.runtime_adapter:
            self.supervisor.close()
            self.runtime_adapter = adapter
            self.runtime_settings_store = (
                AiRuntimeSettingsStore()
                if isinstance(adapter, _UnavailableCodexRuntime)
                else adapter.runtime_settings_store
            )
            self.supervisor = InteractiveSupervisor(
                adapter,
                ticket_ttl_seconds=self.settings.ai_runtime.codex.ticket_ttl_seconds,
            )

    def remove_runtime_module(
        self,
        module_id: str,
        *,
        operation_id: str,
    ) -> RuntimeModuleRemoval:
        if module_id in self._builtin_runtime_modules.implementation_ids():
            raise ApiError(422, "runtime_module_remove_invalid", "内置 Runtime 不可移除。")
        if module_id == self.default_implementation_id:
            raise ApiError(
                409,
                "runtime_default_implementation_required",
                "请先选择其他默认 Runtime 版本。",
            )
        removal = self.runtime_module_service.remove(module_id, operation_id=operation_id)
        try:
            self.refresh_external_runtime_modules()
            if module_id in self.runtime_modules.implementation_ids("codex"):
                raise ApiError(503, "runtime_module_removal_unconfirmed", "Web 注册表未能确认 Runtime 已移除。")
        except Exception:
            self.runtime_module_service.rollback_removal(removal)
            self.refresh_external_runtime_modules()
            raise
        return removal

    def clear_runtime_module_state(self, module_id: str) -> None:
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
        self._pending_native_discoveries = {
            key: observed_at
            for key, observed_at in self._pending_native_discoveries.items()
            if key[0] != self.runtime_id
        }

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

    def read_runtime_implementations(self) -> RuntimeImplementationData:
        try:
            preferences = self.runtime_implementation_preferences.read()
        except RuntimeImplementationPreferencesUnavailable as exc:
            raise ApiError(503, "runtime_implementation_preferences_unavailable", str(exc)) from exc
        installed, _failures = self.runtime_module_service.discover()
        manifests = {item.manifest.implementation_id: item.manifest for item in installed}
        items: list[RuntimeImplementationItem] = []
        for implementation_id in self.runtime_modules.implementation_ids(self.runtime_id):
            module = self.runtime_modules.require(implementation_id)
            adapter = self.runtime_adapters.get(implementation_id)
            status = adapter.status() if adapter is not None else None
            manifest = manifests.get(implementation_id)
            items.append(
                RuntimeImplementationItem(
                    implementation_id=implementation_id,
                    name=module.display_name,
                    version=manifest.version if manifest is not None else "dev",
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
            if implementation_id not in self.runtime_modules.implementation_ids(self.runtime_id):
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
            if any(
                self.supervisor.owns_terminal_writer(session.id)
                for session in self.store.list()
                if session.runtime_id == self.runtime_id
            ):
                raise ApiError(
                    409,
                    "runtime_default_implementation_terminal_active",
                    "实时终端正在使用当前 Runtime 版本，请结束终端任务后再切换默认版本。",
                )
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
        for runtime_id in self.runtime_modules.runtime_ids():
            implementation_id = (
                self.default_implementation_id
                if runtime_id == self.runtime_id
                else next(iter(self.runtime_modules.implementation_ids(runtime_id)), None)
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
                    name=self.runtime_modules.require_navigation(navigation_id).name,
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

    def workspaces(self) -> list[WorkspaceInfo]:
        entries = [
            ("chub", "Chub", PROJECT_ROOT),
            ("home", "用户目录", Path.home()),
            ("workspace", "Workspace", self.settings.ai_runtime.codex.workspace),
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
            if self._system_upgrade_writes_blocked() or not self.runtime_adapter.status().available:
                return [self._public(session) for session in self.store.list()]
            self._consume_hook_results()
            self._sync_bound_native_sessions()
            for session in self.store.list():
                self._refresh_status(session)
                self._reconcile_quick_activity(session)
            return [self._public(session) for session in self.store.list()]

    def get_session(self, session_id: str, *, reconcile: bool = True) -> AiSession:
        with self._lock:
            self._require_store()
            runtime_available = self.runtime_adapter.status().available
            if reconcile and not self._system_upgrade_writes_blocked() and runtime_available:
                self._consume_hook_result_safely(session_id)
                self._sync_bound_native_sessions()
            session = self.store.get(session_id)
            if session is None:
                raise ApiError(404, "codex_session_not_found", "Codex session not found")
            if reconcile and not self._system_upgrade_writes_blocked() and runtime_available:
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
        session_mode: SessionMode = "terminal",
    ) -> SessionInfo:
        self._require_runtime_submission(self.runtime_id)
        if permission_mode is None:
            try:
                permission_mode = self.session_defaults.read().permission_mode
            except SessionDefaultsStoreUnavailable as exc:
                raise ApiError(
                    503,
                    "codex_session_defaults_unavailable",
                    "无法读取新建 Session 默认权限，请稍后重试。",
                ) from exc
        if session_mode == "quick" and permission_mode == "ask":
            raise ApiError(
                409,
                "quick_interaction_ask_not_supported",
                "快速交互不支持 Ask for approval，请选择只读、自动审核或完全访问权限。",
            )
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
            session_mode=session_mode,
            workspace_id=workspace.id,
            workspace_name=workspace.name,
            cwd=Path(workspace.path),
            permission_mode=permission_mode,
            model=model,
            reasoning_effort=reasoning_effort,
            activity="idle" if session_mode == "quick" else "unknown",
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
            session_mode="quick",
            workspace_id="weixin-translation",
            workspace_name="微信文本优化与翻译",
            cwd=workspace,
            title="文本优化与翻译",
            permission_mode="read-only",
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

    def read_session_defaults(self) -> str:
        try:
            return self.session_defaults.read().permission_mode
        except SessionDefaultsStoreUnavailable as exc:
            raise ApiError(
                503,
                "codex_session_defaults_unavailable",
                "无法读取新建 Session 默认权限，请稍后重试。",
            ) from exc

    def update_session_defaults(self, permission_mode: str) -> str:
        self._require_available()
        try:
            self.session_defaults.save(
                SessionDefaults(permission_mode=permission_mode)
            )
        except OSError as exc:
            raise ApiError(
                503,
                "codex_session_defaults_unavailable",
                "无法保存新建 Session 默认权限，请稍后重试。",
            ) from exc
        return permission_mode

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
        self.validate_model(model, reasoning_effort)
        with self._lock:
            session = self.get_session(session_id)
            self._require_quick_access(session)
            usage = self._resolve_session_usage(session)
            if usage.owner != "none" or usage.phase != "idle":
                raise ApiError(
                    409,
                    "codex_session_configuration_update_busy",
                    "Session 正在执行或被占用，请等待任务结束后重试。",
                )
            session.permission_mode = permission_mode
            session.model = model
            session.reasoning_effort = reasoning_effort
            session.updated_at = utc_now()
            self.store.save(session)
            return self._public(session)

    def update_quick_session_model(
        self,
        session_id: str,
        model: str,
        reasoning_effort: str,
    ) -> AiSession:
        """Persist the model for a future quick task after final writer checks."""
        self._require_available()
        self.validate_model(model, reasoning_effort)
        with self._lock:
            session = self.get_session(session_id)
            self._require_quick_access(session)
            usage = self._resolve_session_usage(session)
            if usage.owner != "none" or usage.phase != "idle":
                raise ApiError(
                    409,
                    "codex_session_model_update_busy",
                    "Session 正在执行或被占用，请等待任务结束后重试。",
                )
            session.model = model
            session.reasoning_effort = reasoning_effort
            session.updated_at = utc_now()
            self.store.save(session)
            return session

    def prepare_quick_interaction(self, runtime_id: str | None = None) -> None:
        if runtime_id is None or runtime_id == self.runtime_id:
            self.default_submission_implementation_id()
        else:
            self.require_implementation_submission(runtime_id)
        with self._lock:
            self._ensure_profile()

    def ensure_session_implementation_compatible(
        self, session_id: str, implementation_id: str
    ) -> None:
        self.require_implementation_submission(implementation_id)
        session = self.get_session(session_id)
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

    def has_active_writer(self, native_session_id: str | None) -> bool:
        try:
            return self.runtime_adapter.has_active_writer(native_session_id)
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
        if usage.owner == "terminal" and usage.phase == "running":
            return usage
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
            if self._quick_interaction_is_running(session.id):
                return SessionUsage(
                    native_session_present=native_session_present,
                    owner="quick_worker",
                    phase="waiting_result",
                )

            if self.supervisor.owns_terminal_writer(session.id):
                phase = {
                    "working": "running",
                    "idle": "idle",
                }.get(session.activity, "unknown")
                return SessionUsage(
                    native_session_present=native_session_present,
                    owner="terminal",
                    phase=phase,
                )

            if not native_session_present:
                return SessionUsage()

            writer_active = self.runtime_adapter.has_active_writer(
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
    ) -> bool:
        try:
            return self.runtime_adapter.wait_for_writer_release(
                native_session_id,
                timeout=timeout,
            )
        except RuntimeOperationError as exc:
            raise self._runtime_api_error(exc) from exc

    def ensure_terminal(self, session_id: str) -> AiSession:
        with self._lock:
            session = self.get_session(session_id, reconcile=False)
            self._require_terminal_access(session)
            self._require_runtime_submission(session.runtime_id)
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
            if not self.supervisor.is_terminal_running(session.id):
                # A Hook is accepted only for this launch. Persist before the
                # Runtime starts so a late Hook from a previous carrier cannot
                # bind an unbound Session.
                session.terminal_launch_id = uuid.uuid4().hex
                session.updated_at = utc_now()
                self.store.save(session)
                self._consume_hook_result_safely(session.id)
            try:
                self._ensure_profile()
                self.supervisor.ensure_terminal(
                    session,
                    max_running=self.settings.ai_runtime.codex.max_running,
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

    def require_quick_access(self, session_id: str) -> AiSession:
        session = self.get_session(session_id)
        self._require_quick_access(session)
        return session

    @staticmethod
    def _require_terminal_access(session: AiSession) -> None:
        if session.session_mode != "terminal":
            raise ApiError(
                409,
                "codex_terminal_access_disabled",
                "快速交互 Session 仅支持快速交互入口。",
            )

    @staticmethod
    def _require_quick_access(session: AiSession) -> None:
        if session.session_mode != "quick":
            raise ApiError(
                409,
                "codex_quick_access_disabled",
                "实时终端 Session 仅支持实时终端入口。",
            )

    def restart_terminal_backend(self, session_id: str) -> AiSession:
        self._require_available()
        with self._lock:
            session = self.get_session(session_id, reconcile=False)
            self._require_terminal_access(session)
            self._ensure_profile()
            # Restarting the bridge creates a new terminal carrier. Advance the
            # generation before it starts so a Hook from the carrier being
            # stopped cannot affect this new instance.
            session.terminal_launch_id = uuid.uuid4().hex
            session.updated_at = utc_now()
            self.store.save(session)
            self._consume_hook_result_safely(session.id)
            self.supervisor.restart_terminal_backend(
                session,
                max_running=self.settings.ai_runtime.codex.max_running,
            )
            return session

    def stop_session(
        self,
        session_id: str,
        *,
        reconcile: bool = True,
    ) -> SessionInfo:
        with self._lock:
            session = self.get_session(session_id, reconcile=reconcile)
            self.supervisor.stop_terminal(session.id)
            session.status = "stopped"
            session.activity = "idle"
            session.activity_source = "none"
            session.terminal_launch_id = None
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
            activity_at = updated_at or utc_now()
            session.updated_at = max(session.updated_at, activity_at)
            if source == "terminal":
                session.last_activity_at = max(
                    session.last_activity_at or activity_at,
                    activity_at,
                )
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
            self._require_quick_access(session)
            self.validate_native_session_id(native_session_id)
            if implementation_id is not None:
                self.ensure_session_implementation_compatible(session_id, implementation_id)
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
                    if (
                        candidate.discovered
                        and not self.supervisor.owns_terminal_writer(candidate.id)
                    ):
                        if (
                            self.has_active_writer(native_session_id)
                            and not self._quick_interaction_is_running(session.id)
                        ):
                            raise ApiError(
                                409,
                                "quick_interaction_native_session_conflict",
                                "Codex Session 正在由其他进程使用，请等待任务结束。",
                            )
                        try:
                            self.store.adopt_discovered_native_session(
                                session.id,
                                candidate.id,
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
                        adopted = self.store.get(session.id)
                        if adopted is not None:
                            self._clear_native_identity_conflict(adopted)
                        return
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
                    if self.has_active_writer(session.native_session_id):
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
            self._require_quick_access(session)
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
        if usage.owner in {"terminal", "quick_worker"} and usage.phase == "unknown":
            return usage
        return usage

    def delete_native_session(self, session_id: str) -> AiSession:
        """Delete the Runtime Session before clearing Chub-owned state."""
        session = self.get_session(session_id, reconcile=False)
        self.ensure_delete_allowed(session_id, reconcile=False)
        if session.native_session_id:
            self.validate_native_session_id(session.native_session_id)
            try:
                self.runtime_adapter.run_native_action(
                    "delete",
                    session.native_session_id,
                )
            except RuntimeOperationError as exc:
                if exc.code == "codex_session_delete_failed":
                    try:
                        if (
                            self.runtime_adapter.native_session_deleted_state(
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
        *,
        terminal_already_closed: bool = False,
    ) -> None:
        """Clear Chub-owned state after native deletion has succeeded."""
        if not terminal_already_closed:
            try:
                self.stop_session(session_id)
            except ApiError as exc:
                if exc.code != "codex_session_not_found":
                    raise
        self.store.delete(session_id)
        self._remove_hook_file(session_id)

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
        if (
            usage.owner == "terminal" and usage.phase == "running"
        ) or (
            usage.owner == "quick_worker"
            and usage.phase in {"running", "waiting_result"}
        ):
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
            self.validate_native_session_id(session.native_session_id)
            try:
                self.runtime_adapter.run_native_action(
                    "archive",
                    session.native_session_id,
                )
            except RuntimeOperationError as exc:
                if exc.code == "codex_session_archive_failed":
                    try:
                        if (
                            self.runtime_adapter.native_session_archive_state(
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
        self.store.delete(session_id)
        self._remove_hook_file(session_id)

    def archive_session(self, session_id: str) -> None:
        self.archive_native_session(session_id)
        self.finalize_archive_session(session_id)

    def archive_legacy_translation_sessions(self, quick_interactions) -> int:
        """Archive old translation Sessions that were incorrectly imported.

        This is intentionally a bounded startup-maintenance action.  A legacy
        record must match the old private workspace and the fixed translation
        prompt before it can be considered; any uncertain or active Session is
        left untouched for a later startup.
        """
        if not self.runtime_adapter.status().available:
            return 0
        try:
            self._require_available()
            if (
                self._system_upgrade_writes_blocked()
                or not quick_interactions.recovery_ready
            ):
                return 0
            with self._lock:
                candidates = [
                    session
                    for session in self.store.list()
                    if self._is_legacy_translation_session(session)
                ][:_MAX_LEGACY_TRANSLATION_ARCHIVES_PER_START]
        except (ApiError, OSError):
            LOGGER.warning(
                "Skipping legacy translation Session cleanup because state is unavailable",
                exc_info=True,
            )
            return 0

        archived = 0
        for session in candidates:
            try:
                if quick_interactions.is_running(session.id):
                    continue
                usage = self.resolve_session_usage(session.id)
                if usage.owner != "none" or usage.phase != "idle":
                    continue
                # Import lazily to keep the Session manager independent from
                # the Web/API entry points that also use this operation.
                from app.ai_session.operations import archive_session

                archive_session(
                    session.id,
                    manager=self,
                    quick_interactions=quick_interactions,
                    terminal_tickets=self.supervisor.tickets,
                    terminal_connections=self.supervisor.connections,
                )
            except Exception:
                # This cleanup must never make Web startup unavailable.  The
                # native archive and Chub-side removal are both idempotent, so
                # a later startup can retry a record that remains in storage.
                LOGGER.warning(
                    "Unable to archive a legacy translation Session",
                    exc_info=True,
                )
                continue
            archived += 1
        if archived:
            LOGGER.info("Archived %d legacy translation Sessions", archived)
        return archived

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

    def rebind_upgrade_terminal_carriers(
        self,
        session_mappings: Iterable[tuple[str, str | None]],
    ) -> None:
        """Reattach old Chub tmux carriers to freshly discovered Sessions."""
        with self._lock:
            self._require_store()
            self._sync_bound_native_sessions()
            discovered_by_native = {
                session.native_session_id: session
                for session in self.store.list()
                if session.discovered and session.native_session_id is not None
            }
            for old_session_id, native_session_id in session_mappings:
                if native_session_id is None:
                    continue
                session = discovered_by_native.get(native_session_id)
                if session is None:
                    continue
                rebound = self.supervisor.rebind_terminal_carrier(
                    old_session_id,
                    session.id,
                )
                if not rebound:
                    rebound = self.supervisor.rebind_terminal_carrier_by_native_session(
                        native_session_id,
                        session.id,
                    )
                if rebound:
                    self.runtime_adapter.rebind_activity_session(
                        old_session_id,
                        session.id,
                    )
                    self._refresh_status(session)

    def discard_session_for_system_upgrade(self, session_id: str) -> None:
        """Drop Chub management state without inspecting or stopping the Runtime Session."""
        with self._lock:
            current = self.store.get(session_id)
            if current is None:
                return
            self.supervisor.stop_backend(session_id)
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
            self._require_runtime_submission(session.runtime_id)
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
            last_activity_at=session.last_activity_at,
            session_mode=session.session_mode,
            terminal_access_allowed=session.session_mode == "terminal",
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

    def _consume_hook_results(self) -> None:
        for session in self.store.list():
            self._consume_hook_result_safely(session.id)

    def _consume_hook_result_safely(self, session_id: str) -> None:
        """Keep one malformed or stale Hook from blocking Session management."""
        try:
            self._consume_hook_result(session_id)
        except ApiError as exc:
            if exc.code != "codex_session_native_conflict":
                raise
            LOGGER.warning(
                "Discarding conflicting Runtime activity Hook for managed Session %s",
                session_id,
            )
            session = self.store.get(session_id)
            if session is not None:
                # Keep the conflict local and visible without changing the
                # native writer or turning an otherwise usable Session into a
                # global service failure. A fresh terminal launch clears this
                # diagnostic before it can claim an identity again.
                session.error = "native_session_identity_conflict"
                session.updated_at = utc_now()
                self.store.save(session)
            self._remove_hook_file(session_id)

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
        if session and session.session_mode == "terminal":
            # New terminal launches carry a persisted generation. Legacy Hooks
            # may still update an already-bound Session, but may never claim an
            # unbound Session because their origin cannot be proven.
            if session.native_session_id is None:
                launch_matches = (
                    session.terminal_launch_id is not None
                    and event.launch_id == session.terminal_launch_id
                )
            else:
                # Existing pre-generation bindings are tolerated, but a
                # generation-aware Session never accepts an unlabelled or old
                # Hook as activity from its current terminal.
                launch_matches = (
                    session.terminal_launch_id is None
                    and event.launch_id is None
                ) or event.launch_id == session.terminal_launch_id
            if not launch_matches:
                self._remove_hook_file(session_id)
                return
        if session and session.session_mode == "quick":
            # The Quick Worker result is the sole authority for native Session
            # identity. A nested Runtime command inherits the hook environment,
            # and idle hooks are normalized to ``none`` by the adapter, so the
            # Session mode—not the event source—must decide this boundary.
            native_session_id = None
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
                        "Chub Session identity conflict: Runtime Session identity "
                        "does not match the managed Session",
                    )
                duplicate = next(
                    (
                        candidate
                        for candidate in self.store.list()
                        if candidate.id != session.id
                        and candidate.runtime_id == session.runtime_id
                        and candidate.native_session_id == native_session_id
                    ),
                    None,
                )
                if (
                    duplicate
                    and duplicate.discovered
                    and session.session_mode == "terminal"
                    and self.supervisor.owns_terminal_writer(session.id)
                ):
                    try:
                        self.store.adopt_discovered_native_session(
                            session.id,
                            duplicate.id,
                            native_session_id,
                            session.runtime_id,
                        )
                    except AiSessionStoreUnavailable as exc:
                        raise ApiError(
                            409,
                            "codex_session_native_conflict",
                            "Runtime Session identity is already bound to another managed Session",
                        ) from exc
                    session = self.store.get(session.id)
                else:
                    # A native Session already represented by another Chub record
                    # is never reclassified across terminal and quick modes.
                    try:
                        self.store.bind_native_session(
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
        if session and activity in {"working", "idle"}:
            activity_at = utc_now()
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
            if activity_source == "terminal":
                session.last_activity_at = max(
                    session.last_activity_at or activity_at,
                    activity_at,
                )
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
        stored = self._remove_stale_translation_discoveries(self.store.list())
        pending_native_claim = any(
            (
                session.session_mode == "quick"
                and session.native_session_id is None
                and self._quick_interaction_is_running(session.id)
            )
            or (
                session.session_mode == "terminal"
                and session.native_session_id is None
                and self.supervisor.owns_terminal_writer(session.id)
            )
            for session in stored
        )
        bound_native_keys = {
            (session.runtime_id, session.native_session_id)
            for session in stored
            if session.native_session_id is not None
        }
        now = utc_now()
        seen_pending_keys: set[tuple[str, str]] = set()
        for native in discovery.sessions:
            native_key = (native.runtime_id, native.native_session_id)
            if native_key in bound_native_keys:
                self._pending_native_discoveries.pop(native_key, None)
                continue
            # The translation Runtime Session is an internal quick Session,
            # not a user-owned terminal Session. Its native record can outlive
            # the Worker task briefly, so never import it as a discovered
            # terminal record during that binding/retirement window.
            if self._is_translation_workspace(native.cwd):
                continue
            if pending_native_claim:
                first_seen = self._pending_native_discoveries.setdefault(native_key, now)
                seen_pending_keys.add(native_key)
                if now - first_seen < timedelta(
                    seconds=_NATIVE_DISCOVERY_CLAIM_GRACE_SECONDS
                ):
                    continue
            self._pending_native_discoveries.pop(native_key, None)
            self.store.save(self._session_from_native(native))
            bound_native_keys.add(native_key)
        self._pending_native_discoveries = {
            native_key: first_seen
            for native_key, first_seen in self._pending_native_discoveries.items()
            if native_key in seen_pending_keys
        }
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
            session_mode="terminal",
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
                self.settings.ai_runtime.codex.runtime_dir / "translation-workspace",
            ),
            ("chub", "Chub", PROJECT_ROOT),
            ("workspace", "Workspace", self.settings.ai_runtime.codex.workspace),
            ("home", "用户目录", Path.home()),
        )
        for workspace_id, workspace_name, path in candidates:
            try:
                if resolved_cwd == path.expanduser().resolve(strict=False):
                    return workspace_id, workspace_name
            except OSError:
                continue
        return None

    def _is_translation_workspace(self, cwd: Path) -> bool:
        return self._is_current_translation_workspace(
            cwd
        ) or self._is_legacy_translation_workspace(cwd)

    def _is_current_translation_workspace(self, cwd: Path) -> bool:
        try:
            return cwd.expanduser().resolve(strict=False) == (
                self.settings.ai_runtime.codex.runtime_dir / "translation-workspace"
            ).expanduser().resolve(strict=False)
        except OSError:
            return False

    @staticmethod
    def _is_legacy_translation_workspace(cwd: Path) -> bool:
        try:
            return cwd.expanduser().resolve(strict=False) == (
                _LEGACY_TRANSLATION_WORKSPACE.expanduser().resolve(strict=False)
            )
        except OSError:
            return False

    def _is_legacy_translation_session(self, session: AiSession) -> bool:
        return bool(
            session.discovered
            and session.session_mode == "terminal"
            and session.workspace_id == DISCOVERED_RUNTIME_WORKSPACE_ID
            and session.native_session_id is not None
            and self._is_legacy_translation_workspace(session.cwd)
            and (session.title or "").startswith(_LEGACY_TRANSLATION_TITLE_PREFIX)
        )

    def _remove_stale_translation_discoveries(
        self,
        sessions: list[AiSession],
    ) -> list[AiSession]:
        """Remove only old auto-discovery duplicates from the private translation workspace."""
        retained: list[AiSession] = []
        for session in sessions:
            if not (
                session.discovered
                and session.session_mode == "terminal"
                and session.native_session_id is not None
                and self._is_current_translation_workspace(session.cwd)
            ):
                retained.append(session)
                continue
            # A live writer is never taken over, even in the reserved workspace.
            # Keep the duplicate so the normal binding path fails closed.
            try:
                active_writer = self.has_active_writer(session.native_session_id)
            except ApiError:
                retained.append(session)
                continue
            if active_writer:
                retained.append(session)
                continue
            self.store.delete(session.id)
            self._remove_hook_file(session.id)
        return retained

    @staticmethod
    def _project_native_state(session: AiSession, native: RuntimeNativeSession) -> bool:
        changed = False
        if native.active_model and session.active_model != native.active_model:
            session.active_model = native.active_model
            changed = True
        if (
            native.active_reasoning_effort
            and session.active_reasoning_effort != native.active_reasoning_effort
        ):
            session.active_reasoning_effort = native.active_reasoning_effort
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
            # A Web restart or a Chub-owned state reset can leave a tmux
            # carrier with its previous logical Session ID. Its native resume
            # target remains stable, so rebind that narrowly verified carrier
            # before treating its native writer as external.
            for session in sessions:
                if (
                    session.id in running
                    or session.session_mode != "terminal"
                    or session.native_session_id is None
                ):
                    continue
                if self.supervisor.rebind_terminal_carrier_by_native_session(
                    session.native_session_id,
                    session.id,
                ):
                    running.add(session.id)
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
        return ApiError(status_code, error.code, error.message, source="runtime")
