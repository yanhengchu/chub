import io
import json
import subprocess
import sys
import zipfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from app.ai_runtime import RuntimePluginRegistry
from app.ai_runtime.runtime_plugin_packages import RuntimePluginService, RuntimePluginInstallError
from scripts.build_codex_runtime_zip import build as build_codex_runtime_zip


@pytest.fixture(autouse=True)
def _remove_fixture_runtime_implementations(settings) -> None:
    service = RuntimePluginService(settings)
    installed, _failures = service.discover()
    for item in installed:
        removal = service.remove(item.manifest.implementation_id, operation_id="0" * 32)
        service.finalize_removal(removal)


def _runtime_archive(
    settings,
    *,
    module_id: str = "codex-010001",
    chub_version: str | None = None,
    version: str = "1.0.0",
    dependencies: bool = False,
    compatibility_id: str = "codex-v1",
    entry_import: str = "",
) -> bytes:
    manifest = {
        "protocol_version": 1,
        "module_id": module_id,
        "runtime_id": "codex",
        "implementation_id": module_id,
        "native_session_compatibility_id": compatibility_id,
        "module_type": "runtime",
        "display_name": "Local Test",
        "description": "Local Runtime test module.",
        "version": version,
        "chub_version": chub_version or settings.app.version,
        "entry": "runtime_entry:create_runtime_module",
    }
    if dependencies:
        manifest["dependencies"] = "requirements.txt"
    source = f'''\
{entry_import}
from app.ai_runtime import RuntimeDescriptor, RuntimeStatus
from chub_codex_runtime.runtime_adapter import CodexRuntimeAdapter, CODEX_RUNTIME_CAPABILITIES
from chub_codex_runtime.worker_runtime import CodexWorkerRuntime

DESCRIPTOR = RuntimeDescriptor(runtime_id="codex", implementation_id="{module_id}", native_session_compatibility_id="{compatibility_id}", capabilities=CODEX_RUNTIME_CAPABILITIES)

class Adapter(CodexRuntimeAdapter):
    @property
    def descriptor(self):
        return DESCRIPTOR
    def status(self):
        status = super().status()
        return RuntimeStatus(runtime_id="codex", available=status.available, reason=status.reason, dependencies=status.dependencies)

class Module:
    descriptor = DESCRIPTOR
    display_name = "Local Test"
    description = "Local Runtime test module."
    is_default = False
    def __init__(self, settings):
        self.settings = settings
    def build_adapter(self):
        return Adapter(self.settings)
    def build_worker_runner(self, adapter, *, workspaces):
        return CodexWorkerRuntime(adapter, executable="/bin/true", workspaces=dict(workspaces))

def create_runtime_module(settings):
    return Module(settings)
'''
    archive = io.BytesIO()
    with zipfile.ZipFile(archive, "w") as package:
        package.writestr("chub-module.json", json.dumps(manifest))
        package.writestr("runtime_entry.py", source)
        if dependencies:
            package.writestr("requirements.txt", "example-package==1.0.0\n")
    return archive.getvalue()


def _namespaced_runtime_archive(settings, *, module_id: str, label: str) -> bytes:
    manifest = {
        "protocol_version": 1,
        "module_id": module_id,
        "runtime_id": "codex",
        "implementation_id": module_id,
        "native_session_compatibility_id": "codex-v1",
        "module_type": "runtime",
        "display_name": f"{label} Runtime",
        "description": f"{label} Runtime module.",
        "version": "1.0.0",
        "chub_version": settings.app.version,
        "entry": "runtime_package.entry:create_runtime_module",
    }
    source = f'''\
from .helper import DESCRIPTION, DISPLAY_NAME
from app.ai_runtime import RuntimeDescriptor, RuntimeStatus
from chub_codex_runtime.runtime_adapter import CodexRuntimeAdapter, CODEX_RUNTIME_CAPABILITIES
from chub_codex_runtime.worker_runtime import CodexWorkerRuntime

DESCRIPTOR = RuntimeDescriptor(runtime_id="codex", implementation_id="{module_id}", native_session_compatibility_id="codex-v1", capabilities=CODEX_RUNTIME_CAPABILITIES)

class Adapter(CodexRuntimeAdapter):
    @property
    def descriptor(self):
        return DESCRIPTOR
    def status(self):
        status = super().status()
        return RuntimeStatus(runtime_id="codex", available=status.available, reason=status.reason, dependencies=status.dependencies)

class Module:
    descriptor = DESCRIPTOR
    display_name = DISPLAY_NAME
    description = DESCRIPTION
    is_default = False
    def __init__(self, settings):
        self.settings = settings
    def build_adapter(self):
        return Adapter(self.settings)
    def build_worker_runner(self, adapter, *, workspaces):
        return CodexWorkerRuntime(adapter, executable="/bin/true", workspaces=dict(workspaces))

def create_runtime_module(settings):
    return Module(settings)
'''
    archive = io.BytesIO()
    with zipfile.ZipFile(archive, "w") as package:
        package.writestr("chub-module.json", json.dumps(manifest))
        package.writestr("runtime_package/__init__.py", "")
        package.writestr("runtime_package/entry.py", source)
        package.writestr(
            "runtime_package/helper.py",
            f'DISPLAY_NAME = "{label} Runtime"\nDESCRIPTION = "{label} Runtime module."\n',
        )
    return archive.getvalue()


def test_runtime_zip_installs_and_discovers_a_non_default_module(settings) -> None:
    service = RuntimePluginService(settings)

    activation = service.install(_runtime_archive(settings), source_name="local.zip")
    service.finalize(activation)
    installed, failures = service.discover()

    assert activation.installed.manifest.module_id == "codex-010001"
    assert [item.manifest.module_id for item in installed] == ["codex-010001"]
    assert failures == ()


def test_runtime_zip_rejects_cover_with_a_different_native_compatibility_group(
    settings,
) -> None:
    service = RuntimePluginService(settings)
    first = service.install(_runtime_archive(settings), source_name="first.zip")
    service.finalize(first)

    with pytest.raises(RuntimePluginInstallError, match="原生 Session 兼容组"):
        service.install(
            _runtime_archive(settings, compatibility_id="codex-v2"),
            source_name="incompatible.zip",
        )


def test_runtime_plugin_registers_as_a_codex_implementation(settings) -> None:
    service = RuntimePluginService(settings)
    activation = service.install(
        _runtime_archive(settings, module_id="codex-010002"),
        source_name="default.zip",
    )
    service.finalize(activation)

    registry, failures = service.build_registry(RuntimePluginRegistry())

    assert registry.runtime_ids() == ("codex",)
    assert registry.require("codex-010002").descriptor.runtime_id == "codex"
    assert failures == ()


def test_runtime_plugins_with_same_package_name_are_loaded_in_isolated_namespaces(
    settings,
) -> None:
    service = RuntimePluginService(settings)
    first = service.install(
        _namespaced_runtime_archive(settings, module_id="codex-010003", label="First"),
        source_name="first.zip",
    )
    service.finalize(first)
    second = service.install(
        _namespaced_runtime_archive(settings, module_id="codex-010004", label="Second"),
        source_name="second.zip",
    )
    service.finalize(second)

    installed, failures = service.discover()

    assert [(item.manifest.module_id, item.module.display_name) for item in installed] == [
        ("codex-010003", "First Runtime"),
        ("codex-010004", "Second Runtime"),
    ]
    assert failures == ()


def test_runtime_zip_rejects_path_traversal(settings) -> None:
    archive = io.BytesIO()
    with zipfile.ZipFile(archive, "w") as package:
        package.writestr("../chub-module.json", "{}")

    with pytest.raises(RuntimePluginInstallError) as raised:
        RuntimePluginService(settings).install(
            archive.getvalue(),
            source_name="unsafe.zip",
        )

    assert raised.value.code == "runtime_plugin_install_invalid"


def test_runtime_zip_rejects_incompatible_chub_version(settings) -> None:
    archive = _runtime_archive(settings, chub_version="9.9.9")

    with pytest.raises(RuntimePluginInstallError) as raised:
        RuntimePluginService(settings).install(
            archive,
            source_name="old.zip",
        )

    assert "不兼容" in raised.value.message


def test_runtime_zip_reports_shared_contract_mismatch_at_entry_load(settings) -> None:
    archive = _runtime_archive(
        settings,
        entry_import="from app.ai_runtime import RuntimeActivityEvent",
    )

    with pytest.raises(RuntimePluginInstallError) as raised:
        RuntimePluginService(settings).install(
            archive,
            source_name="outdated-contract.zip",
        )

    assert raised.value.message == (
        "模块依赖的 Runtime 共享契约与当前 Chub 不兼容，请使用当前源码重新构建 ZIP。"
    )


def test_codex_runtime_zip_rejects_non_versioned_implementation_id(settings) -> None:
    with pytest.raises(RuntimePluginInstallError) as raised:
        RuntimePluginService(settings).inspect_archive(
            _runtime_archive(settings, module_id="codex-dev"),
            source_name="invalid-id.zip",
        )

    assert raised.value.code == "runtime_plugin_install_invalid"


def test_runtime_zip_preview_reads_manifest_without_installing(settings) -> None:
    service = RuntimePluginService(settings)

    preview = service.inspect_archive(_runtime_archive(settings), source_name="local.zip")

    assert preview.module_id == "codex-010001"
    assert preview.name == "Local Test"
    assert list((service.runtimes_dir / "codex").iterdir()) == []
    assert list(service.staging_dir.iterdir()) == []


def test_runtime_plugin_removal_can_be_rolled_back_or_finalized(settings) -> None:
    service = RuntimePluginService(settings)
    activation = service.install(_runtime_archive(settings), source_name="local.zip")
    service.finalize(activation)

    removal = service.remove("codex-010001", operation_id="b" * 32)
    service.rollback_removal(removal)
    restored, _ = service.discover()
    assert [item.manifest.module_id for item in restored] == ["codex-010001"]

    removal = service.remove("codex-010001", operation_id="c" * 32)
    service.finalize_removal(removal)
    removed, failures = service.discover()
    assert removed == ()
    assert failures == ()


def test_runtime_plugin_removal_rejects_an_invalid_plugin_id(settings) -> None:
    service = RuntimePluginService(settings)

    with pytest.raises(RuntimePluginInstallError) as raised:
        service.remove("invalid_module", operation_id="a" * 32)

    assert raised.value.code == "runtime_plugin_install_invalid"


def test_runtime_zip_dependency_install_is_required_before_activation(settings) -> None:
    archive = _runtime_archive(settings, dependencies=True)

    with patch(
        "app.ai_runtime.runtime_plugin_packages.subprocess.run",
        return_value=SimpleNamespace(returncode=0),
    ) as install:
        activation = RuntimePluginService(settings).install(
            archive,
            source_name="dependencies.zip",
        )

    assert "--target" in install.call_args.args[0]
    RuntimePluginService(settings).rollback(activation)

    with patch(
        "app.ai_runtime.runtime_plugin_packages.subprocess.run",
        return_value=SimpleNamespace(returncode=1),
    ), pytest.raises(RuntimePluginInstallError) as raised:
        RuntimePluginService(settings).install(
            archive,
            source_name="dependencies.zip",
        )

    assert "依赖安装失败" in raised.value.message


def test_dependency_failure_during_replacement_preserves_installed_runtime(
    settings,
) -> None:
    service = RuntimePluginService(settings)
    initial = service.install(
        _runtime_archive(settings, version="1.0.0"),
        source_name="v1.zip",
    )
    service.finalize(initial)

    with (
        patch(
            "app.ai_runtime.runtime_plugin_packages.subprocess.run",
            return_value=SimpleNamespace(returncode=1),
        ),
        pytest.raises(RuntimePluginInstallError, match="依赖安装失败"),
    ):
        service.install(
            _runtime_archive(settings, version="2.0.0", dependencies=True),
            source_name="v2.zip",
        )

    installed, failures = service.discover()

    assert [item.manifest.version for item in installed] == ["1.0.0"]
    assert failures == ()
    assert not service.activation_journal_path.exists()


def test_damaged_installed_runtime_is_isolated_from_other_runtimes(settings) -> None:
    service = RuntimePluginService(settings)
    healthy = service.install(
        _runtime_archive(settings, module_id="codex-010005"),
        source_name="healthy.zip",
    )
    service.finalize(healthy)
    damaged = service.install(
        _runtime_archive(settings, module_id="codex-010006"),
        source_name="damaged.zip",
    )
    service.finalize(damaged)
    (damaged.installed.root / "runtime_entry.py").unlink()

    installed, failures = service.discover()
    registry, registry_failures = service.build_registry(RuntimePluginRegistry())

    assert [item.manifest.module_id for item in installed] == ["codex-010005"]
    assert [failure.module_id for failure in failures] == ["codex-010006"]
    assert registry.runtime_ids() == ("codex",)
    assert [failure.module_id for failure in registry_failures] == ["codex-010006"]


def test_generated_verification_runtime_zip_is_installable(settings, tmp_path: Path) -> None:
    output = tmp_path / "verification-runtime.zip"
    result = subprocess.run(
        [
            sys.executable,
            "scripts/build_runtime_verification_zip.py",
            "--output",
            str(output),
        ],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0
    activation = RuntimePluginService(settings).install(
        output.read_bytes(),
        source_name=output.name,
    )
    RuntimePluginService(settings).finalize(activation)
    installed, failures = RuntimePluginService(settings).discover()

    assert [item.manifest.module_id for item in installed] == ["verification-runtime"]
    assert failures == ()


def test_generated_codex_runtime_zip_loads_the_packaged_runtime_implementation(
    settings,
    tmp_path: Path,
) -> None:
    output = tmp_path / "codex-runtime.zip"
    result = subprocess.run(
        [
            sys.executable,
            "scripts/build_codex_runtime_zip.py",
            "--output",
            str(output),
            "--description",
            "测试构建的 Codex Runtime 正式版本。",
        ],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0
    service = RuntimePluginService(settings)
    activation = service.install(output.read_bytes(), source_name=output.name)
    service.finalize(activation)
    registry, failures = service.build_registry(RuntimePluginRegistry())
    module = registry.require("codex-010000")
    adapter = module.build_adapter()
    runner = module.build_worker_runner(adapter, workspaces={})

    assert failures == ()
    assert module.descriptor.runtime_id == "codex"
    assert adapter.__class__.__module__.startswith("_chub_runtime_codex_010000.")
    assert runner.__class__.__module__.startswith("_chub_runtime_codex_010000.")
    assert runner.__class__.__module__.endswith("worker_runtime")
    assert module.description == "测试构建的 Codex Runtime 正式版本。"
    assert "worker_entry.py" in runner.build_launch.__code__.co_consts
    with zipfile.ZipFile(output) as archive:
        for name in archive.namelist():
            if name.endswith(".py"):
                assert b"app.codex" not in archive.read(name)
        models = archive.read("chub_codex_runtime/models.py")
        assert b"class SessionListData" not in models
        assert b"class QuickInteractionTask" not in models
        assert b"class RuntimeManagementData" not in models


def test_codex_runtime_zip_builder_requires_a_release_summary(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="release summary"):
        build_codex_runtime_zip(
            tmp_path / "codex-runtime.zip",
            description="   ",
        )


def test_incomplete_runtime_activation_is_restored_on_next_service_instance(
    settings,
) -> None:
    service = RuntimePluginService(settings)
    initial = service.install(_runtime_archive(settings, version="1.0.0"), source_name="v1.zip")
    service.finalize(initial)
    replacement = service.install(
        _runtime_archive(settings, version="2.0.0"),
        source_name="v2.zip",
        operation_id="a" * 32,
    )

    recovery = RuntimePluginService(settings).recover_incomplete_activation()
    installed, failures = RuntimePluginService(settings).discover()

    assert replacement.installed.manifest.version == "2.0.0"
    assert recovery is not None
    assert recovery.operation_id == "a" * 32
    assert recovery.module_id == "codex-010001"
    assert recovery.action == "install_runtime_module"
    assert [item.manifest.version for item in installed] == ["1.0.0"]
    assert failures == ()


def test_incomplete_runtime_removal_is_restored_with_removal_action(settings) -> None:
    service = RuntimePluginService(settings)
    activation = service.install(_runtime_archive(settings), source_name="local.zip")
    service.finalize(activation)
    service.remove("codex-010001", operation_id="b" * 32)

    recovery = RuntimePluginService(settings).recover_incomplete_activation()
    installed, failures = RuntimePluginService(settings).discover()

    assert recovery is not None
    assert recovery.action == "remove_runtime_module"
    assert [item.manifest.module_id for item in installed] == ["codex-010001"]
    assert failures == ()


def test_finalized_runtime_replacement_has_no_recovery_record(settings) -> None:
    service = RuntimePluginService(settings)
    initial = service.install(_runtime_archive(settings, version="1.0.0"), source_name="v1.zip")
    service.finalize(initial)
    replacement = service.install(_runtime_archive(settings, version="2.0.0"), source_name="v2.zip")
    service.finalize(replacement)

    recovery = RuntimePluginService(settings).recover_incomplete_activation()
    installed, failures = RuntimePluginService(settings).discover()

    assert recovery is None
    assert [item.manifest.version for item in installed] == ["2.0.0"]
    assert failures == ()


def test_runtime_state_cleanup_record_survives_service_recreation(settings) -> None:
    service = RuntimePluginService(settings)

    service.begin_state_cleanup(
        operation_id="d" * 32,
        action="remove_runtime_module",
        module_id="codex-010001",
        session_ids=("session-1", "session-2"),
    )

    pending = RuntimePluginService(settings).pending_state_cleanup()

    assert pending is not None
    assert pending.operation_id == "d" * 32
    assert pending.action == "remove_runtime_module"
    assert pending.module_id == "codex-010001"
    assert pending.session_ids == ("session-1", "session-2")

    RuntimePluginService(settings).complete_state_cleanup("d" * 32)

    assert RuntimePluginService(settings).pending_state_cleanup() is None
