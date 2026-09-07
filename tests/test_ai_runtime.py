import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pydantic import ValidationError

from app.ai_runtime import (
    BACKGROUND_RUNTIME_CAPABILITIES,
    BuiltinRuntimeModuleRegistry,
    RUNTIME_CAPABILITIES,
    RuntimeDescriptor,
    RuntimeEventSummary,
    RuntimeOperationError,
    RuntimeRegistry,
    RuntimeStatus,
    RuntimeTerminalRequest,
    RuntimeTurnRequest,
    RuntimeTurnResult,
    RuntimeWorkerLaunchSpec,
    WorkerRuntimeRegistry,
    validate_runtime_wiring,
)
from app.ai_runtime.external_modules import ExternalRuntimeModuleService
from app.ai_runtime.implementation_preferences import RuntimeImplementationPreferences
from app.ai_runtime.enablement import RuntimeEnablement
from app.ai_session.manager import AiSessionManager
from app.application import create_app
from app.ai_runtime.general_settings import AiRuntimeSettingsStore
from legacy_codex_manager import CodexPtyManager
from app.codex.models import (
    CodexModelCatalogData,
    CodexModelInfo,
    CodexReasoningLevel,
    CodexSession,
)
from chub_codex_runtime.runtime_adapter import CodexRuntimeAdapter
from chub_codex_runtime.runtime_runner import CodexRuntimeRunner
from app.core.config import Settings
from app.core.response import ApiError


class StubRuntime:
    def __init__(
        self,
        runtime_id: str = "test",
        *,
        implementation_id: str | None = None,
        available: bool = True,
    ) -> None:
        self._runtime_id = runtime_id
        self._implementation_id = implementation_id
        self._available = available

    @property
    def descriptor(self) -> RuntimeDescriptor:
        return RuntimeDescriptor(
            runtime_id=self._runtime_id,
            implementation_id=self._implementation_id,
            capabilities=frozenset({"runtime_status"}),
        )

    def status(self) -> RuntimeStatus:
        return RuntimeStatus(
            runtime_id=self._runtime_id,
            available=self._available,
            reason=None if self._available else "test runtime unavailable",
        )


class BrokenWriterRuntime(StubRuntime):
    @property
    def descriptor(self) -> RuntimeDescriptor:
        return RuntimeDescriptor(
            runtime_id="broken",
            capabilities=frozenset({"runtime_status", "writer_probe"}),
        )


class MismatchedStatusRuntime(StubRuntime):
    def status(self) -> RuntimeStatus:
        return RuntimeStatus(runtime_id="other-runtime", available=True)


class StubWorkerRuntime:
    def __init__(
        self,
        runtime_id: str = "worker-test",
        *,
        implementation_id: str | None = None,
        available: bool = True,
        capabilities=frozenset(BACKGROUND_RUNTIME_CAPABILITIES),
    ) -> None:
        self._descriptor = RuntimeDescriptor(
            runtime_id=runtime_id,
            implementation_id=implementation_id,
            capabilities=capabilities,
        )
        self._available = available

    @property
    def descriptor(self) -> RuntimeDescriptor:
        return self._descriptor

    @property
    def available(self) -> bool:
        return self._available

    @property
    def workspace_ids(self) -> tuple[str, ...]:
        return ("workspace",)

    @staticmethod
    def validate_turn(_workspace_id, _request) -> None:
        return None

    @staticmethod
    def build_launch(_request) -> RuntimeWorkerLaunchSpec:
        return RuntimeWorkerLaunchSpec(
            argv=("/fixed/runtime",),
            stdin_prompt=False,
        )

    @staticmethod
    def has_active_writer(_native_session_id: str) -> bool:
        return False

    @staticmethod
    def native_session_available(_native_session_id: str) -> bool:
        return True

    @staticmethod
    def parse_event_stream(
        _path: Path,
        *,
        max_event_bytes: int,
        missing_ok: bool = False,
    ) -> RuntimeEventSummary:
        return RuntimeEventSummary(native_session_id="native-session")

    @staticmethod
    def read_error(_task_dir: Path, *, max_bytes: int) -> str | None:
        return None

    @staticmethod
    def read_result(_task_dir: Path, *, max_bytes: int) -> RuntimeTurnResult:
        return RuntimeTurnResult(text="completed")


class IncompleteWorkerRuntime(StubWorkerRuntime):
    read_result = None


class StubBuiltinRuntimeModule:
    def __init__(
        self,
        runtime_id: str,
        *,
        implementation_id: str | None = None,
        is_default: bool = False,
        display_name: str | None = None,
        description: str | None = None,
    ) -> None:
        self._runtime_id = runtime_id
        self._implementation_id = implementation_id
        self._is_default = is_default
        self._display_name = display_name or runtime_id
        self._description = description or f"{runtime_id} description"

    @property
    def descriptor(self) -> RuntimeDescriptor:
        return RuntimeDescriptor(
            runtime_id=self._runtime_id,
            implementation_id=self._implementation_id,
            capabilities=frozenset({"runtime_status"}),
        )

    @property
    def display_name(self) -> str:
        return self._display_name

    @property
    def description(self) -> str:
        return self._description

    @property
    def is_default(self) -> bool:
        return self._is_default

    def build_adapter(self) -> StubRuntime:
        return StubRuntime(
            self._runtime_id,
            implementation_id=self._implementation_id,
        )

    def build_worker_runner(self, _adapter, *, workspaces) -> StubWorkerRuntime:
        return StubWorkerRuntime(self._runtime_id)


class BrokenAdapterRuntimeModule(StubBuiltinRuntimeModule):
    def build_adapter(self) -> StubRuntime:
        raise RuntimeError("adapter construction failed")


class MutableWorkerDescriptorRuntime(StubWorkerRuntime):
    def __init__(self) -> None:
        super().__init__("mutable-runtime")
        self.reported_runtime_id = "mutable-runtime"

    @property
    def descriptor(self) -> RuntimeDescriptor:
        return RuntimeDescriptor(
            runtime_id=self.reported_runtime_id,
            capabilities=BACKGROUND_RUNTIME_CAPABILITIES,
        )


def test_runtime_registry_is_fixed_and_rejects_unknown_or_duplicate() -> None:
    runtime = StubRuntime()
    registry = RuntimeRegistry([runtime])

    assert registry.runtime_ids() == ("test",)
    assert registry.require("test") is runtime
    with pytest.raises(RuntimeOperationError) as duplicate:
        registry.register(runtime)
    assert duplicate.value.code == "runtime_duplicate"
    with pytest.raises(RuntimeOperationError) as missing:
        registry.require("unknown")
    assert missing.value.code == "runtime_unavailable"
    with pytest.raises(RuntimeOperationError) as capability:
        registry.require("test", {"writer_probe"})
    assert capability.value.code == "runtime_capability_unavailable"


def test_runtime_capability_matrix_is_explicit_and_runtime_neutral() -> None:
    registry = RuntimeRegistry(
        [
            StubRuntime(),
            StubRuntime("second-runtime"),
            StubRuntime("offline-runtime", available=False),
        ]
    )

    matrix = registry.capability_matrix()

    assert [item.runtime_id for item in matrix] == [
        "test",
        "second-runtime",
        "offline-runtime",
    ]
    assert matrix[1].available is True
    assert matrix[1].capabilities["runtime_status"] == "supported"
    assert matrix[1].capabilities["interactive_terminal"] == "unsupported"
    assert set(matrix[1].capabilities) == set(RUNTIME_CAPABILITIES)
    assert all(
        state == "unsupported"
        for capability, state in matrix[1].capabilities.items()
        if capability != "runtime_status"
    )
    assert matrix[2].available is False
    assert matrix[2].capabilities["runtime_status"] == "unavailable"
    assert matrix[2].reason == "test runtime unavailable"


def test_runtime_capability_matrix_rejects_mismatched_status_owner() -> None:
    registry = RuntimeRegistry([MismatchedStatusRuntime("declared-runtime")])

    with pytest.raises(RuntimeOperationError) as invalid:
        registry.capability_matrix()

    assert invalid.value.code == "runtime_status_invalid"


def test_runtime_capability_matrix_accepts_multiple_implementations_of_one_runtime() -> None:
    class CodexImplementation(StubRuntime):
        def __init__(self, implementation_id: str) -> None:
            super().__init__("codex")
            self._implementation_id = implementation_id

        @property
        def descriptor(self) -> RuntimeDescriptor:
            return RuntimeDescriptor(
                runtime_id="codex",
                implementation_id=self._implementation_id,
                capabilities=frozenset({"runtime_status"}),
            )

    registry = RuntimeRegistry(
        [CodexImplementation("builtin-dev"), CodexImplementation("codex-010000")]
    )

    assert registry.runtime_ids() == ("codex",)
    assert registry.implementation_ids("codex") == (
        "builtin-dev",
        "codex-010000",
    )
    assert [item.runtime_id for item in registry.capability_matrix()] == [
        "codex",
        "codex",
    ]


def test_runtime_registry_rejects_descriptor_identity_drift() -> None:
    runtime = StubRuntime()
    registry = RuntimeRegistry([runtime])
    runtime._runtime_id = "other-runtime"

    with pytest.raises(RuntimeOperationError) as listed:
        registry.runtime_ids()
    assert listed.value.code == "runtime_identity_invalid"

    with pytest.raises(RuntimeOperationError) as invalid:
        registry.capability_matrix()

    assert invalid.value.code == "runtime_identity_invalid"


def test_runtime_registry_rejects_declared_capability_without_contract() -> None:
    with pytest.raises(RuntimeOperationError) as invalid:
        RuntimeRegistry([BrokenWriterRuntime()])

    assert invalid.value.code == "runtime_capability_invalid"


def test_runtime_wiring_rejects_adapter_runner_owner_mismatch() -> None:
    class DescriptorOnly:
        descriptor = RuntimeDescriptor(
            runtime_id="other-runtime",
            capabilities=frozenset({"runtime_status"}),
        )

    with pytest.raises(RuntimeOperationError) as invalid:
        validate_runtime_wiring(StubRuntime("codex"), DescriptorOnly())

    assert invalid.value.code == "runtime_wiring_invalid"


def test_runtime_wiring_rejects_capability_mismatch() -> None:
    class AdapterDescriptor:
        descriptor = RuntimeDescriptor(
            runtime_id="codex",
            capabilities=frozenset({"runtime_status", "model_catalog"}),
        )

    class DescriptorOnly:
        descriptor = RuntimeDescriptor(
            runtime_id="codex",
            capabilities=frozenset({"runtime_status"}),
        )

    with pytest.raises(RuntimeOperationError) as invalid:
        validate_runtime_wiring(AdapterDescriptor(), DescriptorOnly())

    assert invalid.value.code == "runtime_wiring_invalid"


def test_builtin_runtime_module_registry_requires_one_unique_default() -> None:
    registry = BuiltinRuntimeModuleRegistry(
        [StubBuiltinRuntimeModule("codex", is_default=True)]
    )

    assert registry.runtime_ids() == ("codex",)
    assert registry.default().descriptor.runtime_id == "codex"
    with pytest.raises(RuntimeOperationError) as duplicate:
        registry.register(StubBuiltinRuntimeModule("other", is_default=True))
    assert duplicate.value.code == "runtime_module_default_duplicate"


def test_builtin_runtime_module_registry_keeps_registered_presentation() -> None:
    module = StubBuiltinRuntimeModule(
        "codex",
        is_default=True,
        display_name="Codex",
        description="Initial description",
    )
    registry = BuiltinRuntimeModuleRegistry([module])
    module._display_name = "Changed name"
    module._description = "Changed description"

    presentation = registry.require_navigation("codex")

    assert presentation.name == "Codex"
    assert presentation.description == "Initial description"


def test_builtin_runtime_module_registry_navigation_groups_versions_by_runtime() -> None:
    registry = BuiltinRuntimeModuleRegistry(
        [
            StubBuiltinRuntimeModule(
                "codex",
                implementation_id="builtin-dev",
                is_default=True,
                display_name="Codex",
            ),
            StubBuiltinRuntimeModule(
                "codex",
                implementation_id="codex-010001",
                display_name="Codex",
            ),
        ]
    )

    assert registry.runtime_ids() == ("codex",)
    assert [item.runtime_id for item in registry.navigation()] == ["codex"]
    assert registry.require_navigation("codex").implementation_id == "builtin-dev"


def test_session_manager_isolates_external_adapter_construction_failure(
    settings: Settings,
) -> None:
    manager = AiSessionManager(settings)
    modules = BuiltinRuntimeModuleRegistry(
        [
            manager.runtime_modules.require("codex"),
            BrokenAdapterRuntimeModule("broken-runtime"),
        ]
    )
    manager.runtime_module_service.build_registry = MagicMock(return_value=(modules, ()))

    manager.refresh_external_runtime_modules()

    assert manager.runtime_modules.runtime_ids() == ("codex",)
    assert manager.runtime_module_failures[0].module_id == "broken-runtime"


def test_session_manager_keeps_codex_adapter_when_a_healthy_external_runtime_loads(
    settings: Settings,
) -> None:
    manager = AiSessionManager(settings)
    modules = BuiltinRuntimeModuleRegistry(
        [
            manager.runtime_modules.require("codex"),
            StubBuiltinRuntimeModule("healthy-runtime"),
        ]
    )
    manager.runtime_module_service.build_registry = MagicMock(return_value=(modules, ()))

    manager.refresh_external_runtime_modules()

    assert manager.runtime_adapter.descriptor.runtime_id == "codex"
    assert manager.supervisor.runtime_adapter.descriptor.runtime_id == "codex"
    assert manager.runtime_modules.runtime_ids() == ("codex", "healthy-runtime")


def test_session_manager_allows_default_version_change_when_runtime_is_stopped(
    settings: Settings,
) -> None:
    manager = AiSessionManager(settings)
    modules = BuiltinRuntimeModuleRegistry(
        [
            StubBuiltinRuntimeModule(
                "codex",
                implementation_id="builtin-dev",
                is_default=True,
                display_name="Codex",
            ),
            StubBuiltinRuntimeModule(
                "codex",
                implementation_id="codex-010001",
                display_name="Codex",
            ),
        ]
    )
    builtin = StubRuntime("codex", implementation_id="builtin-dev")
    formal = StubRuntime("codex", implementation_id="codex-010001")
    manager.runtime_modules = modules
    manager.runtime_registry = RuntimeRegistry([builtin, formal])
    manager.runtime_adapters = {
        "builtin-dev": builtin,
        "codex-010001": formal,
    }
    manager.default_implementation_id = "builtin-dev"
    manager.runtime_adapter = builtin
    manager.runtime_implementation_preferences.save(
        RuntimeImplementationPreferences(default_implementation_id="builtin-dev")
    )
    manager.runtime_enablement.save(RuntimeEnablement(disabled_runtime_ids=["codex"]))

    result = manager.update_default_implementation("codex-010001")

    assert result.default_implementation_id == "codex-010001"
    assert manager.default_implementation_id == "codex-010001"
    with pytest.raises(ApiError) as rejected:
        manager.require_implementation_submission("codex-010001")
    assert rejected.value.code == "ai_runtime_disabled"


def test_session_manager_starts_with_builtin_runtime_when_no_formal_version_is_installed(settings: Settings) -> None:
    service = ExternalRuntimeModuleService(settings)
    removal = service.remove("codex-010000", operation_id="0" * 32)
    service.finalize_removal(removal)
    manager = AiSessionManager(settings)

    available, reason = manager.submission_available()

    assert manager.runtime_modules.implementation_ids("codex") == ("builtin-dev",)
    assert manager.runtime_registry.runtime_ids() == ("codex",)
    assert available is True
    assert reason is None


@pytest.mark.anyio
async def test_application_lifespan_starts_with_builtin_runtime_when_no_formal_version_is_installed(
    settings: Settings,
) -> None:
    service = ExternalRuntimeModuleService(settings)
    removal = service.remove("codex-010000", operation_id="1" * 32)
    service.finalize_removal(removal)
    application = create_app(settings)

    async with application.router.lifespan_context(application):
        available, reason = application.state.ai_session_manager.submission_available()

    assert available is True
    assert reason is None


@pytest.mark.anyio
async def test_application_lifespan_recovers_pending_runtime_state_cleanup(
    settings: Settings,
) -> None:
    service = ExternalRuntimeModuleService(settings)
    service.begin_state_cleanup(
        operation_id="e" * 32,
        action="remove_runtime_module",
        module_id="codex",
        session_ids=("session-1", "session-2"),
    )
    application = create_app(settings)
    manager = application.state.ai_session_manager
    manager.clear_runtime_module_state = MagicMock()
    application.state.quick_interactions.remove_session_tasks = MagicMock()

    with patch(
        "app.application.clear_runtime_state",
        new=AsyncMock(return_value={"success": True}),
    ) as clear_runtime_state:
        async with application.router.lifespan_context(application):
            for _ in range(20):
                if service.pending_state_cleanup() is None:
                    break
                await asyncio.sleep(0.01)

    assert service.pending_state_cleanup() is None
    clear_runtime_state.assert_awaited_once_with(settings, runtime_id="codex")
    assert application.state.quick_interactions.remove_session_tasks.call_args_list == [
        (("session-1",), {}),
        (("session-2",), {}),
    ]
    manager.clear_runtime_module_state.assert_called_once_with("codex")


def test_external_codex_module_builds_matching_adapter_and_worker_runner(
    settings: Settings,
    tmp_path: Path,
) -> None:
    registry, failures = ExternalRuntimeModuleService(settings).build_registry(
        BuiltinRuntimeModuleRegistry()
    )
    module = registry.require("codex-010000")
    adapter = module.build_adapter()
    module.configure_worker_adapter(
        adapter,
        codex_home=tmp_path / "codex-home",
        executable="/fixed/codex",
    )
    runner = module.build_worker_runner(adapter, workspaces={"chub": tmp_path})

    assert failures == ()
    assert registry.runtime_ids() == ("codex",)
    assert module.display_name == "Codex"
    assert module.is_default is False
    assert adapter.descriptor == module.descriptor == runner.descriptor
    assert validate_runtime_wiring(adapter, runner) == "codex"


def test_worker_runtime_registry_is_fixed_and_fails_before_submission() -> None:
    runtime = StubWorkerRuntime()
    registry = WorkerRuntimeRegistry([runtime])

    assert registry.runtime_ids() == ("worker-test",)
    assert registry.available_runtime_ids() == ("worker-test",)
    assert registry.workspace_ids() == {"worker-test": ("workspace",)}
    assert registry.require("worker-test") is runtime
    with pytest.raises(RuntimeOperationError) as duplicate:
        registry.register(runtime)
    assert duplicate.value.code == "runtime_runner_duplicate"
    with pytest.raises(RuntimeOperationError) as missing:
        registry.require("unknown")
    assert missing.value.code == "runtime_unavailable"

    unavailable = WorkerRuntimeRegistry(
        [StubWorkerRuntime("offline", available=False)]
    )
    with pytest.raises(RuntimeOperationError) as offline:
        unavailable.require("offline")
    assert offline.value.code == "runtime_unavailable"


def test_worker_runtime_registry_reports_logical_runtime_ids_for_versions() -> None:
    registry = WorkerRuntimeRegistry(
        [
            StubWorkerRuntime("codex", implementation_id="builtin-dev"),
            StubWorkerRuntime("codex", implementation_id="codex-010000"),
        ]
    )

    assert registry.runtime_ids() == ("codex",)
    assert registry.available_runtime_ids() == ("codex",)
    assert registry.available_implementation_ids() == (
        "builtin-dev",
        "codex-010000",
    )


def test_worker_runtime_capability_matrix_allows_second_runtime_runner() -> None:
    registry = WorkerRuntimeRegistry(
        [StubWorkerRuntime("codex"), StubWorkerRuntime("second-runtime")]
    )

    matrix = registry.capability_matrix()

    assert [item.runtime_id for item in matrix] == ["codex", "second-runtime"]
    assert all(item.available for item in matrix)
    assert all(
        item.capabilities[capability] == "supported"
        for item in matrix
        for capability in BACKGROUND_RUNTIME_CAPABILITIES
    )


def test_worker_runtime_registry_rejects_missing_or_false_capability() -> None:
    with pytest.raises(RuntimeOperationError) as missing_capability:
        WorkerRuntimeRegistry(
            [
                StubWorkerRuntime(
                    "limited",
                    capabilities=frozenset({"runtime_status"}),
                )
            ]
        )
    assert missing_capability.value.code == "runtime_runner_capability_invalid"

    with pytest.raises(RuntimeOperationError) as false_capability:
        WorkerRuntimeRegistry([IncompleteWorkerRuntime("incomplete")])
    assert false_capability.value.code == "runtime_runner_invalid"


def test_worker_runtime_registry_rejects_descriptor_identity_drift() -> None:
    runtime = MutableWorkerDescriptorRuntime()
    registry = WorkerRuntimeRegistry([runtime])
    runtime.reported_runtime_id = "other-runtime"

    with pytest.raises(RuntimeOperationError) as listed:
        registry.runtime_ids()
    assert listed.value.code == "runtime_runner_identity_invalid"

    with pytest.raises(RuntimeOperationError) as invalid:
        registry.capability_matrix()

    assert invalid.value.code == "runtime_runner_identity_invalid"


def test_codex_adapter_declares_current_capabilities(settings: Settings) -> None:
    adapter = CodexRuntimeAdapter(
        settings,
        which=lambda _name: "/available",
    )

    assert adapter.descriptor.runtime_id == "codex"
    assert adapter.descriptor.capabilities == frozenset(
        {
            "runtime_status",
            "background_turn",
            "task_cancel",
            "native_session_mapping",
            "interactive_terminal",
            "session_resume",
                "session_archive",
                "structured_events",
                "activity_events",
                "writer_probe",
                "model_catalog",
                "permission_profiles",
                "usage_snapshot",
                "usage_login_page",
        }
    )
    assert adapter.status().available is True


def test_codex_runtime_usage_settings_follow_active_provider_config(
    settings: Settings,
    tmp_path: Path,
) -> None:
    store = AiRuntimeSettingsStore(tmp_path / "ai-runtimes.local.yaml")
    codex_home = tmp_path / "codex"
    codex_home.mkdir()
    config = codex_home / "config.toml"
    config.write_text(
        'model_provider = "OpenAI"\n[model_providers.OpenAI]\nbase_url = "http://10.20.30.40"\n',
        encoding="utf-8",
    )
    adapter = CodexRuntimeAdapter(
        settings,
        codex_home=codex_home,
        runtime_settings_store=store,
    )

    assert adapter._read_usage_settings().provider_base_url == "http://10.20.30.40"
    config.write_text(
        'model_provider = "OpenAI"\n[model_providers.OpenAI]\nbase_url = "https://provider.example"\n',
        encoding="utf-8",
    )

    assert adapter._read_usage_settings().provider_base_url == "https://provider.example"


def test_codex_runtime_usage_uses_default_timezone_despite_legacy_general_setting(
    settings: Settings,
    tmp_path: Path,
) -> None:
    settings_path = tmp_path / "ai-runtimes.local.yaml"
    store = AiRuntimeSettingsStore(settings_path)
    settings_path.write_text(
        "general:\n  timezone: America/Los_Angeles\n",
        encoding="utf-8",
    )
    adapter = CodexRuntimeAdapter(settings, runtime_settings_store=store)

    assert store.read_general().weekly_report_session.runtime_id == "codex"
    assert adapter._read_usage_settings().timezone == "Asia/Shanghai"


def test_codex_manager_production_registry_only_exposes_codex(
    settings: Settings,
) -> None:
    manager = CodexPtyManager(settings)

    assert manager.runtime_registry.runtime_ids() == ("codex",)
    assert manager.runtime_adapter is manager.runtime_registry.require("codex")


@pytest.mark.parametrize(
    ("permission_profile", "expected"),
    [
        ("auto-review", 'default_permissions=":workspace"'),
        ("read-only", 'default_permissions=":read-only"'),
        ("full-access", 'default_permissions=":danger-full-access"'),
    ],
)
def test_codex_runner_maps_permissions_without_elevation(
    permission_profile: str,
    expected: str,
    tmp_path: Path,
) -> None:
    request = RuntimeTurnRequest.model_validate(
        {
            "permission_profile": permission_profile,
            "native_session_id": "native-session",
            "model": "gpt-test",
            "reasoning_effort": "high",
        }
    )

    process_spec = CodexRuntimeRunner.command(
        "/fixed/codex",
        tmp_path / "result.txt",
        request,
    )
    command = list(process_spec.argv)

    assert command[0:5] == [
        "/fixed/codex",
        "exec",
        "--skip-git-repo-check",
        "--profile",
        "chub",
    ]
    assert command.count("--skip-git-repo-check") == 1
    assert expected in command
    assert command[-3:] == ["resume", "native-session", "-"]


def test_runtime_turn_request_rejects_unmappable_permission() -> None:
    with pytest.raises(ValidationError):
        RuntimeTurnRequest.model_validate({"permission_profile": "ask"})
    with pytest.raises(ValidationError):
        RuntimeTurnRequest.model_validate(
            {
                "permission_profile": "read-only",
                "native_session_id": "x" * 129,
            }
        )


def test_codex_runner_rejects_invalid_resume_session_id(tmp_path: Path) -> None:
    request = RuntimeTurnRequest(
        permission_profile="read-only",
        native_session_id="--help",
    )

    with pytest.raises(RuntimeOperationError) as invalid:
        CodexRuntimeRunner.command(
            "/fixed/codex",
            tmp_path / "result.txt",
            request,
        )

    assert invalid.value.code == "codex_session_invalid"


def test_codex_runner_extracts_one_native_session_and_rejects_conflicts(
    tmp_path: Path,
) -> None:
    event_path = tmp_path / "events.jsonl"
    event_path.write_text(
        '{"type":"thread.started","thread_id":"native-1"}\n',
        encoding="utf-8",
    )
    event_path.chmod(0o600)

    assert CodexRuntimeRunner.parse_event_stream(
        event_path,
        native_session_pattern=r"native-[0-9]+",
        max_event_bytes=1024,
    ).native_session_id == "native-1"

    event_path.write_text(
        (
            '{"type":"thread.started","thread_id":"native-1"}\n'
            '{"type":"thread.started","thread_id":"native-2"}\n'
        ),
        encoding="utf-8",
    )
    with pytest.raises(RuntimeOperationError) as conflict:
        CodexRuntimeRunner.parse_event_stream(
            event_path,
            native_session_pattern=r"native-[0-9]+",
            max_event_bytes=1024,
        )
    assert conflict.value.code == "codex_event_session_conflict"


def test_codex_runner_rejects_unsafe_result_and_event_paths(tmp_path: Path) -> None:
    result_path = tmp_path / "result.txt"
    result_path.write_text("existing", encoding="utf-8")
    with pytest.raises(RuntimeOperationError) as result_error:
        CodexRuntimeRunner.create_result_file(result_path)
    assert result_error.value.code == "codex_result_unavailable"

    event_path = tmp_path / "events.jsonl"
    event_path.write_text("{}\n", encoding="utf-8")
    event_path.chmod(0o644)
    with pytest.raises(RuntimeOperationError) as unsafe_event:
        CodexRuntimeRunner.parse_event_stream(
            event_path,
            native_session_pattern=r"native-[0-9]+",
            max_event_bytes=1024,
        )
    assert unsafe_event.value.code == "codex_event_stream_unsafe"
    event_path.chmod(0o600)
    event_path.write_bytes(b"x" * 1025)
    with pytest.raises(RuntimeOperationError) as oversized_event:
        CodexRuntimeRunner.parse_event_stream(
            event_path,
            native_session_pattern=r"native-[0-9]+",
            max_event_bytes=1024,
        )
    assert oversized_event.value.code == "codex_event_stream_unsafe"


def test_codex_adapter_fails_closed_when_writer_cannot_be_confirmed(
    settings: Settings,
    tmp_path: Path,
) -> None:
    native_id = "native-session"
    lock_dir = tmp_path / "thread-writer-locks"
    lock_dir.mkdir()
    target = tmp_path / "target.lock"
    target.write_text("", encoding="utf-8")
    (lock_dir / f"{native_id}.lock").symlink_to(target)
    adapter = CodexRuntimeAdapter(settings, codex_home=tmp_path)

    with pytest.raises(RuntimeOperationError) as writer:
        adapter.has_active_writer(native_id)

    assert writer.value.code == "codex_writer_status_unavailable"
    with pytest.raises(RuntimeOperationError) as invalid_id:
        adapter.has_active_writer("../../unexpected")
    assert invalid_id.value.code == "codex_writer_status_unavailable"


def test_codex_adapter_status_does_not_depend_on_tailnet_listener(
    settings: Settings,
) -> None:
    adapter = CodexRuntimeAdapter(
        settings,
        which=lambda _name: "/fixed/dependency",
    )

    assert adapter.status().available is True

    status = adapter.status()
    assert status.available is True
    assert status.reason is None


def test_codex_runner_normalizes_malformed_events_and_truncated_results(
    tmp_path: Path,
) -> None:
    event_path = tmp_path / "events.jsonl"
    event_path.write_bytes(b"not-json\n")
    event_path.chmod(0o600)

    event = CodexRuntimeRunner.parse_event_stream(
        event_path,
        native_session_pattern=r"native-[0-9]+",
        max_event_bytes=1024,
    )

    assert event.native_session_id is None
    result_path = tmp_path / "result.txt"
    result_path.write_text("abcdef", encoding="utf-8")
    result = CodexRuntimeRunner.read_result(result_path, max_bytes=4)
    assert result.text == "abcd"
    assert result.truncated is True


def test_codex_runner_preserves_upstream_error_text(tmp_path: Path) -> None:
    event_path = tmp_path / "events.jsonl"
    event_path.write_text(
        '{"type":"thread.started","thread_id":"native-1"}\n'
        '{"type":"turn.failed","error":{"message":"unexpected status 503 '
        'Service Unavailable: Service temporarily unavailable"}}\n',
        encoding="utf-8",
    )
    event_path.chmod(0o600)

    assert CodexRuntimeRunner.read_error(event_path, max_bytes=4096) == (
        "unexpected status 503 Service Unavailable: Service temporarily unavailable"
    )

    event_path.write_text(
        '{"type":"provider.transport_failure","message":"provider raw error"}\n',
        encoding="utf-8",
    )
    assert CodexRuntimeRunner.read_error(event_path, max_bytes=4096) == (
        "provider raw error"
    )

    event_path.write_text(
        '{"type":"provider.error","payload":{"status":503}}\n',
        encoding="utf-8",
    )
    assert CodexRuntimeRunner.read_error(event_path, max_bytes=4096) == (
        '{"type":"provider.error","payload":{"status":503}}'
    )

    event_path.write_text(
        '{"type":"provider.error","message":"Authorization: Bearer '
        'super-secret"}\n',
        encoding="utf-8",
    )
    assert CodexRuntimeRunner.read_error(event_path, max_bytes=4096) == (
        "Authorization: Bearer [REDACTED]"
    )


def test_codex_adapter_owns_activity_event_file_boundary(
    settings: Settings,
    tmp_path: Path,
) -> None:
    settings.ai_runtime.codex.runtime_dir = tmp_path / "runtime"
    adapter = CodexRuntimeAdapter(settings)
    adapter.hook_dir.mkdir(parents=True)
    hook = adapter.hook_dir / "123e4567-e89b-12d3-a456-426614174000.json"
    hook.write_text(
        '{"codex_session_id":"native-1","activity":"working",'
        '"activity_source":"terminal","launch_id":"' + "a" * 32 + '"}',
        encoding="utf-8",
    )
    hook.chmod(0o600)

    event = adapter.read_activity_event("123e4567-e89b-12d3-a456-426614174000")

    assert event is not None
    assert event.native_session_id == "native-1"
    assert event.activity == "working"
    assert event.activity_source == "terminal"
    assert event.launch_id == "a" * 32
    adapter.clear_activity_event("123e4567-e89b-12d3-a456-426614174000")
    assert adapter.read_activity_event("123e4567-e89b-12d3-a456-426614174000") is None


def test_codex_adapter_preserves_quick_origin_for_idle_hook(
    settings: Settings,
    tmp_path: Path,
) -> None:
    settings.ai_runtime.codex.runtime_dir = tmp_path / "runtime"
    adapter = CodexRuntimeAdapter(settings)
    adapter.hook_dir.mkdir(parents=True)
    hook = adapter.hook_dir / "123e4567-e89b-12d3-a456-426614174000.json"
    hook.write_text(
        '{"codex_session_id":"native-1","activity":"idle",'
        '"activity_source":"quick"}',
        encoding="utf-8",
    )
    hook.chmod(0o600)

    event = adapter.read_activity_event("123e4567-e89b-12d3-a456-426614174000")

    assert event is not None
    assert event.activity == "idle"
    assert event.activity_source == "quick"


def test_codex_adapter_persists_activity_session_rebind(
    settings: Settings,
    tmp_path: Path,
) -> None:
    settings.ai_runtime.codex.runtime_dir = tmp_path / "runtime"
    adapter = CodexRuntimeAdapter(settings)
    old_session_id = "123e4567-e89b-12d3-a456-426614174000"
    new_session_id = "123e4567-e89b-12d3-a456-426614174001"

    adapter.rebind_activity_session(old_session_id, new_session_id)

    alias = adapter.hook_dir / f".{old_session_id}.rebind"
    assert alias.read_text(encoding="ascii") == f"{new_session_id}\n"
    assert alias.stat().st_mode & 0o777 == 0o600


def test_codex_adapter_terminal_spec_uses_runtime_request(
    settings: Settings,
    tmp_path: Path,
) -> None:
    adapter = CodexRuntimeAdapter(settings)
    process_spec = adapter.terminal_command(
        RuntimeTerminalRequest(
            session_id="session-1",
            launch_id="a" * 32,
            cwd=tmp_path,
            permission_mode="read-only",
            native_session_id="native-1",
            model="gpt-test",
            reasoning_effort="high",
        ),
        12345,
    )

    command = list(process_spec.argv)
    assert command[command.index("--permission-mode") + 1] == "read-only"
    assert command[command.index("--terminal-launch-id") + 1] == "a" * 32
    assert command[command.index("--codex-session") + 1] == "native-1"


def test_codex_adapter_rejects_invalid_cli_session_id(
    settings: Settings,
    tmp_path: Path,
) -> None:
    run = MagicMock()
    adapter = CodexRuntimeAdapter(settings, run=run)
    request = RuntimeTerminalRequest(
        session_id="session-1",
        cwd=tmp_path,
        permission_mode="read-only",
        native_session_id="--help",
    )

    with pytest.raises(RuntimeOperationError) as terminal:
        adapter.terminal_command(request, 12345)
    with pytest.raises(RuntimeOperationError) as action:
        adapter.run_native_action("archive", "--help")

    assert terminal.value.code == "codex_session_invalid"
    assert action.value.code == "codex_session_invalid"
    run.assert_not_called()


def test_codex_adapter_normalizes_discovery_and_model_catalog(
    settings: Settings,
    tmp_path: Path,
) -> None:
    adapter = CodexRuntimeAdapter(settings, codex_home=tmp_path)
    native = CodexSession(
        id="native-1",
        workspace_id="codex",
        workspace_name="workspace",
        cwd=tmp_path,
        title="x" * 501,
        codex_session_id="native-1",
        active_permission_mode="read-only",
        active_model="gpt-test",
        active_reasoning_effort="high",
    )
    adapter.discovery = MagicMock()
    adapter.discovery.discover.return_value = [native]
    adapter.discovery.session_archive_states.return_value = {"native-1": False}
    adapter.model_catalog = MagicMock()
    adapter.model_catalog.data.return_value = CodexModelCatalogData(
        models=[
            CodexModelInfo(
                id="gpt-test",
                name="GPT Test",
                description="Test model",
                default_level="high",
                levels=[
                    CodexReasoningLevel(id="high", description="Thorough")
                ],
            )
        ],
        default_model="gpt-test",
        default_reasoning_effort="high",
    )

    discovery = adapter.discover_sessions()
    catalog = adapter.read_model_catalog()

    assert discovery.sessions[0].native_session_id == "native-1"
    assert discovery.sessions[0].title == "x" * 500
    assert discovery.sessions[0].active_permission_mode == "read-only"
    assert discovery.archive_states == {"native-1": False}
    assert catalog.models[0].id == "gpt-test"
    assert catalog.models[0].levels[0].id == "high"
