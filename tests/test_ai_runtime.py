from pathlib import Path
from unittest.mock import MagicMock

import pytest
from pydantic import ValidationError

from app.ai_runtime import (
    BACKGROUND_RUNTIME_CAPABILITIES,
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
from app.codex.runtime_adapter import CodexRuntimeAdapter
from app.codex.manager import CodexPtyManager
from app.codex.models import (
    CodexModelCatalogData,
    CodexModelInfo,
    CodexReasoningLevel,
    CodexSession,
)
from app.codex.runtime_runner import CodexRuntimeRunner
from app.core.config import Settings


class StubRuntime:
    def __init__(self, runtime_id: str = "test", *, available: bool = True) -> None:
        self._runtime_id = runtime_id
        self._available = available

    @property
    def descriptor(self) -> RuntimeDescriptor:
        return RuntimeDescriptor(
            runtime_id=self._runtime_id,
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
        available: bool = True,
        capabilities=frozenset(BACKGROUND_RUNTIME_CAPABILITIES),
    ) -> None:
        self._descriptor = RuntimeDescriptor(
            runtime_id=runtime_id,
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
    settings.server.host = "100.64.0.1"
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
        }
    )
    assert adapter.status().available is True


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

    assert command[0:4] == ["/fixed/codex", "exec", "--profile", "chub"]
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
    settings.codex_pty.runtime_dir = tmp_path / "runtime"
    adapter = CodexRuntimeAdapter(settings)
    adapter.hook_dir.mkdir(parents=True)
    hook = adapter.hook_dir / "123e4567-e89b-12d3-a456-426614174000.json"
    hook.write_text(
        '{"codex_session_id":"native-1","activity":"working",'
        '"activity_source":"terminal"}',
        encoding="utf-8",
    )
    hook.chmod(0o600)

    event = adapter.read_activity_event("123e4567-e89b-12d3-a456-426614174000")

    assert event is not None
    assert event.native_session_id == "native-1"
    assert event.activity == "working"
    assert event.activity_source == "terminal"
    adapter.clear_activity_event("123e4567-e89b-12d3-a456-426614174000")
    assert adapter.read_activity_event("123e4567-e89b-12d3-a456-426614174000") is None


def test_codex_adapter_preserves_quick_origin_for_idle_hook(
    settings: Settings,
    tmp_path: Path,
) -> None:
    settings.codex_pty.runtime_dir = tmp_path / "runtime"
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


def test_codex_adapter_terminal_spec_uses_runtime_request(
    settings: Settings,
    tmp_path: Path,
) -> None:
    adapter = CodexRuntimeAdapter(settings)
    process_spec = adapter.terminal_command(
        RuntimeTerminalRequest(
            session_id="session-1",
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
