from __future__ import annotations

import fcntl
import os
import re
import stat
import subprocess
import time
from collections.abc import Callable
from pathlib import Path

from app.ai_runtime import (
    RUNTIME_CAPABILITIES,
    RuntimeCapability,
    RuntimeDescriptor,
    RuntimeModelCatalog,
    RuntimeModelInfo,
    RuntimeNativeAction,
    RuntimeNativeSession,
    RuntimeOperationError,
    RuntimeReasoningLevel,
    RuntimeSessionDiscoveryResult,
    RuntimeStatus,
)
from app.ai_runtime.general_settings import (
    AiRuntimeSettingsStore,
)
from .usage_service import AiUsageService
from .provider_browser import ProviderBrowserAdapter, ProviderBrowserUnavailable
from .discovery import CodexSessionDiscovery
from .model_catalog import CodexModelCatalog
from .rate_limits import CodexRateLimitService
from .usage_settings import (
    CodexProviderConfigReader,
    CodexProviderConfigUnavailable,
    CodexUsageSettings,
)
from app.core.config import Settings


CODEX_SESSION_ID_PATTERN = r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}"
CODEX_RUNTIME_CAPABILITIES: frozenset[RuntimeCapability] = (
    RUNTIME_CAPABILITIES - {"runtime_settings"}
)
CODEX_RUNTIME_DESCRIPTOR = RuntimeDescriptor(
    runtime_id="codex",
    implementation_id="builtin-dev",
    native_session_compatibility_id="codex-v1",
    capabilities=CODEX_RUNTIME_CAPABILITIES,
)


def is_valid_codex_session_id(value: object) -> bool:
    return isinstance(value, str) and re.fullmatch(
        CODEX_SESSION_ID_PATTERN,
        value,
    ) is not None


class CodexRuntimeAdapter:
    def __init__(
        self,
        settings: Settings,
        *,
        descriptor: RuntimeDescriptor = CODEX_RUNTIME_DESCRIPTOR,
        codex_home: Path | None = None,
        executable: str | Path | None = None,
        which: Callable[[str], str | None] | None = None,
        run: Callable[..., subprocess.CompletedProcess] | None = None,
        runtime_settings_store: AiRuntimeSettingsStore | None = None,
        provider_config_reader: CodexProviderConfigReader | None = None,
        rate_limits: CodexRateLimitService | None = None,
    ) -> None:
        self._descriptor = descriptor
        self.settings = settings
        self.codex_home = codex_home or Path(
            os.environ.get("CODEX_HOME", Path.home() / ".codex")
        )
        self.executable = str(executable) if executable is not None else "codex"
        self.discovery = CodexSessionDiscovery(self.codex_home)
        self.model_catalog = CodexModelCatalog(self.codex_home)
        self._which = which
        self._run = run
        self._runtime_settings_store = runtime_settings_store or AiRuntimeSettingsStore()
        self._provider_config_reader = provider_config_reader or CodexProviderConfigReader(
            self.codex_home
        )
        self.rate_limits = rate_limits or CodexRateLimitService()
        self._usage_service: AiUsageService | None = None
        self._usage_settings: CodexUsageSettings | None = None

    @property
    def descriptor(self) -> RuntimeDescriptor:
        return self._descriptor

    @property
    def display_name(self) -> str:
        return "Codex"

    @property
    def runtime_settings_store(self) -> AiRuntimeSettingsStore:
        return self._runtime_settings_store

    def configure_worker_environment(
        self,
        *,
        executable: str | None,
        codex_home: Path,
    ) -> None:
        """Apply the Worker-only executable override without host type coupling."""
        self.codex_home = codex_home
        self.executable = executable or "codex"
        self.discovery = CodexSessionDiscovery(codex_home)
        self.model_catalog = CodexModelCatalog(codex_home)
        self._provider_config_reader = CodexProviderConfigReader(codex_home)

    @staticmethod
    def runtime_process_matches(command: tuple[str, ...]) -> bool:
        return any("codex" in Path(part).name.lower() for part in command)

    def dependencies(self) -> dict[str, bool]:
        import shutil

        which = self._which or shutil.which
        return {"codex": which("codex") is not None}

    def status(self) -> RuntimeStatus:
        dependencies = self.dependencies()
        reason = None
        if not self.settings.ai_runtime.codex.enabled:
            reason = "Codex Runtime is disabled"
        else:
            missing = [name for name, found in dependencies.items() if not found]
            if missing:
                reason = f"Missing dependencies: {', '.join(missing)}"
        return RuntimeStatus(
            runtime_id="codex",
            available=reason is None,
            reason=reason,
            dependencies=dependencies,
        )

    def validate_model(self, model: str | None, reasoning_effort: str | None) -> None:
        self.model_catalog.validate(model, reasoning_effort)

    def read_model_catalog(self) -> RuntimeModelCatalog:
        catalog = self.model_catalog.data()
        return RuntimeModelCatalog(
            models=tuple(
                RuntimeModelInfo(
                    id=model.id,
                    name=model.name,
                    description=model.description,
                    default_level=model.default_level,
                    levels=tuple(
                        RuntimeReasoningLevel(
                            id=level.id,
                            description=level.description,
                        )
                        for level in model.levels
                    ),
                )
                for model in catalog.models
            ),
            default_model=catalog.default_model,
            default_reasoning_effort=catalog.default_reasoning_effort,
        )

    def read_usage_snapshot(self, *, force: bool = False):
        settings = self._read_usage_settings()
        if self._usage_service is None or settings != self._usage_settings:
            self._usage_service = AiUsageService(
                settings,
                self.rate_limits,
                self.settings.automations,
            )
            self._usage_settings = settings
        return self._usage_service.read(force=force).model_copy(
            update={"runtime_id": self.descriptor.runtime_id}
        )

    def open_usage_login_page(self) -> None:
        settings = self._read_usage_settings()
        try:
            ProviderBrowserAdapter(settings, self.settings.automations).open_login_page()
        except ProviderBrowserUnavailable as exc:
            raise RuntimeOperationError(
                "codex_usage_login_unavailable",
                "Codex Runtime 登录页面当前不可用",
            ) from exc

    def _read_usage_settings(self) -> CodexUsageSettings:
        try:
            provider_base_url = self._provider_config_reader.read_base_url()
        except CodexProviderConfigUnavailable:
            provider_base_url = None
        return CodexUsageSettings(
            provider_base_url=provider_base_url,
        )

    @staticmethod
    def validate_native_session_id(native_session_id: str) -> None:
        if not is_valid_codex_session_id(native_session_id):
            raise RuntimeOperationError(
                "codex_session_invalid",
                "Codex Session ID is invalid",
                kind="invalid_request",
            )

    def discover_sessions(self) -> RuntimeSessionDiscoveryResult:
        try:
            discovered = self.discovery.discover()
            archive_states = self.discovery.session_archive_states()
        except OSError as exc:
            raise RuntimeOperationError(
                "codex_session_discovery_unavailable",
                "Unable to discover Codex sessions",
            ) from exc
        sessions: list[RuntimeNativeSession] = []
        for session in discovered:
            native_session_id = session.codex_session_id
            if not is_valid_codex_session_id(native_session_id):
                raise RuntimeOperationError(
                    "codex_session_invalid",
                    "Codex Session ID is invalid",
                )
            sessions.append(
                RuntimeNativeSession(
                    runtime_id="codex",
                    native_session_id=native_session_id,
                    cwd=session.cwd,
                    title=session.title[:500] if session.title is not None else None,
                    created_at=session.created_at,
                    updated_at=session.updated_at,
                )
            )
        return RuntimeSessionDiscoveryResult(
            sessions=tuple(sessions),
            archive_states=archive_states,
            complete=self.discovery.last_discovery_complete is True,
        )

    def has_active_writer(
        self,
        native_session_id: str | None,
    ) -> bool:
        if not native_session_id:
            return False
        if not is_valid_codex_session_id(native_session_id):
            raise RuntimeOperationError(
                "codex_writer_status_unavailable",
                "Unable to verify Codex session writer state",
            )
        lock_path = self.codex_home / "thread-writer-locks" / f"{native_session_id}.lock"
        flags = os.O_RDWR | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
        try:
            descriptor = os.open(lock_path, flags)
        except FileNotFoundError:
            return False
        except OSError as exc:
            raise RuntimeOperationError(
                "codex_writer_status_unavailable",
                "Unable to verify Codex session writer state",
            ) from exc
        try:
            if not stat.S_ISREG(os.fstat(descriptor).st_mode):
                raise OSError("Codex writer lock is not a regular file")
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                return True
            fcntl.flock(descriptor, fcntl.LOCK_UN)
            return False
        except OSError as exc:
            raise RuntimeOperationError(
                "codex_writer_status_unavailable",
                "Unable to verify Codex session writer state",
            ) from exc
        finally:
            os.close(descriptor)

    def wait_for_writer_release(
        self,
        native_session_id: str | None,
        *,
        timeout: float = 3.0,
    ) -> bool:
        deadline = time.monotonic() + max(0.0, timeout)
        while self.has_active_writer(native_session_id):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return False
            time.sleep(min(0.05, remaining))
        return True

    def native_session_available(self, native_session_id: str) -> bool:
        self.validate_native_session_id(native_session_id)
        archive_states = self.discovery.session_archive_states()
        return archive_states is not None and archive_states.get(native_session_id) is False

    def native_session_archive_state(self, native_session_id: str) -> bool | None:
        self.validate_native_session_id(native_session_id)
        archive_states = self.discovery.session_archive_states()
        if archive_states is None:
            return None
        return archive_states.get(native_session_id)

    def native_session_deleted_state(self, native_session_id: str) -> bool | None:
        self.validate_native_session_id(native_session_id)
        archive_states = self.discovery.session_archive_states()
        if archive_states is None:
            return None
        if native_session_id in archive_states:
            return False
        sessions = self.discovery.discover()
        if not self.discovery.last_discovery_complete:
            return None
        return not any(
            session.codex_session_id == native_session_id
            for session in sessions
        )

    def run_native_action(
        self,
        action: RuntimeNativeAction,
        native_session_id: str,
    ) -> None:
        if action not in {"archive", "delete"}:
            raise RuntimeOperationError(
                "runtime_action_unsupported",
                "Runtime action is unsupported",
                kind="invalid_request",
            )
        self.validate_native_session_id(native_session_id)
        run = self._run or subprocess.run
        command = [self.executable, action]
        if action == "delete":
            command.append("--force")
        command.append(native_session_id)
        result = run(
            command,
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
        )
        if result.returncode != 0:
            raise RuntimeOperationError(
                f"codex_session_{action}_failed",
                f"Unable to {action} Codex session",
            )
