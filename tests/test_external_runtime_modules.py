import io
import json
import subprocess
import sys
import zipfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from app.ai_runtime.external_modules import ExternalRuntimeModuleService, RuntimeModuleInstallError


def _runtime_archive(
    settings,
    *,
    module_id: str = "local-test",
    chub_version: str | None = None,
    version: str = "1.0.0",
    dependencies: bool = False,
) -> bytes:
    manifest = {
        "protocol_version": 1,
        "module_id": module_id,
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
from app.ai_runtime import RuntimeDescriptor, RuntimeStatus
from app.codex.runtime_adapter import CodexRuntimeAdapter, CODEX_RUNTIME_CAPABILITIES
from app.codex.worker_runtime import CodexWorkerRuntime

DESCRIPTOR = RuntimeDescriptor(runtime_id="{module_id}", capabilities=CODEX_RUNTIME_CAPABILITIES)

class Adapter(CodexRuntimeAdapter):
    @property
    def descriptor(self):
        return DESCRIPTOR
    def status(self):
        status = super().status()
        return RuntimeStatus(runtime_id="{module_id}", available=status.available, reason=status.reason, dependencies=status.dependencies)

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
from app.codex.runtime_adapter import CodexRuntimeAdapter, CODEX_RUNTIME_CAPABILITIES
from app.codex.worker_runtime import CodexWorkerRuntime

DESCRIPTOR = RuntimeDescriptor(runtime_id="{module_id}", capabilities=CODEX_RUNTIME_CAPABILITIES)

class Adapter(CodexRuntimeAdapter):
    @property
    def descriptor(self):
        return DESCRIPTOR
    def status(self):
        status = super().status()
        return RuntimeStatus(runtime_id="{module_id}", available=status.available, reason=status.reason, dependencies=status.dependencies)

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
    service = ExternalRuntimeModuleService(settings)

    activation = service.install(_runtime_archive(settings), source_name="local.zip")
    service.finalize(activation)
    installed, failures = service.discover()

    assert activation.installed.manifest.module_id == "local-test"
    assert [item.manifest.module_id for item in installed] == ["local-test"]
    assert failures == ()


def test_runtime_modules_with_same_package_name_are_loaded_in_isolated_namespaces(
    settings,
) -> None:
    service = ExternalRuntimeModuleService(settings)
    first = service.install(
        _namespaced_runtime_archive(settings, module_id="first-test", label="First"),
        source_name="first.zip",
    )
    service.finalize(first)
    second = service.install(
        _namespaced_runtime_archive(settings, module_id="second-test", label="Second"),
        source_name="second.zip",
    )
    service.finalize(second)

    installed, failures = service.discover()

    assert [(item.manifest.module_id, item.module.display_name) for item in installed] == [
        ("first-test", "First Runtime"),
        ("second-test", "Second Runtime"),
    ]
    assert failures == ()


def test_runtime_zip_rejects_path_traversal(settings) -> None:
    archive = io.BytesIO()
    with zipfile.ZipFile(archive, "w") as package:
        package.writestr("../chub-module.json", "{}")

    with pytest.raises(RuntimeModuleInstallError) as raised:
        ExternalRuntimeModuleService(settings).install(
            archive.getvalue(),
            source_name="unsafe.zip",
        )

    assert raised.value.code == "runtime_module_install_invalid"


def test_runtime_zip_rejects_incompatible_chub_version(settings) -> None:
    archive = _runtime_archive(settings, chub_version="9.9.9")

    with pytest.raises(RuntimeModuleInstallError) as raised:
        ExternalRuntimeModuleService(settings).install(
            archive,
            source_name="old.zip",
        )

    assert "不兼容" in raised.value.message


def test_runtime_zip_preview_reads_manifest_without_installing(settings) -> None:
    service = ExternalRuntimeModuleService(settings)

    preview = service.inspect_archive(_runtime_archive(settings), source_name="local.zip")

    assert preview.module_id == "local-test"
    assert preview.name == "Local Test"
    assert list(service.runtimes_dir.iterdir()) == []
    assert list(service.staging_dir.iterdir()) == []


def test_runtime_module_removal_can_be_rolled_back_or_finalized(settings) -> None:
    service = ExternalRuntimeModuleService(settings)
    activation = service.install(_runtime_archive(settings), source_name="local.zip")
    service.finalize(activation)

    removal = service.remove("local-test", operation_id="b" * 32)
    service.rollback_removal(removal)
    restored, _ = service.discover()
    assert [item.manifest.module_id for item in restored] == ["local-test"]

    removal = service.remove("local-test", operation_id="c" * 32)
    service.finalize_removal(removal)
    removed, failures = service.discover()
    assert removed == ()
    assert failures == ()


def test_runtime_module_removal_rejects_an_invalid_module_id(settings) -> None:
    service = ExternalRuntimeModuleService(settings)

    with pytest.raises(RuntimeModuleInstallError) as raised:
        service.remove("invalid_module", operation_id="a" * 32)

    assert raised.value.code == "runtime_module_install_invalid"


def test_runtime_zip_dependency_install_is_required_before_activation(settings) -> None:
    archive = _runtime_archive(settings, dependencies=True)

    with patch(
        "app.ai_runtime.external_modules.subprocess.run",
        return_value=SimpleNamespace(returncode=0),
    ) as install:
        activation = ExternalRuntimeModuleService(settings).install(
            archive,
            source_name="dependencies.zip",
        )

    assert "--target" in install.call_args.args[0]
    ExternalRuntimeModuleService(settings).rollback(activation)

    with patch(
        "app.ai_runtime.external_modules.subprocess.run",
        return_value=SimpleNamespace(returncode=1),
    ), pytest.raises(RuntimeModuleInstallError) as raised:
        ExternalRuntimeModuleService(settings).install(
            archive,
            source_name="dependencies.zip",
        )

    assert "依赖安装失败" in raised.value.message


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
    activation = ExternalRuntimeModuleService(settings).install(
        output.read_bytes(),
        source_name=output.name,
    )
    ExternalRuntimeModuleService(settings).finalize(activation)
    installed, failures = ExternalRuntimeModuleService(settings).discover()

    assert [item.manifest.module_id for item in installed] == ["verification-runtime"]
    assert failures == ()


def test_incomplete_runtime_activation_is_restored_on_next_service_instance(
    settings,
) -> None:
    service = ExternalRuntimeModuleService(settings)
    initial = service.install(_runtime_archive(settings, version="1.0.0"), source_name="v1.zip")
    service.finalize(initial)
    replacement = service.install(
        _runtime_archive(settings, version="2.0.0"),
        source_name="v2.zip",
        operation_id="a" * 32,
    )

    recovery = ExternalRuntimeModuleService(settings).recover_incomplete_activation()
    installed, failures = ExternalRuntimeModuleService(settings).discover()

    assert replacement.installed.manifest.version == "2.0.0"
    assert recovery is not None
    assert recovery.operation_id == "a" * 32
    assert recovery.module_id == "local-test"
    assert recovery.action == "install_runtime_module"
    assert [item.manifest.version for item in installed] == ["1.0.0"]
    assert failures == ()


def test_incomplete_runtime_removal_is_restored_with_removal_action(settings) -> None:
    service = ExternalRuntimeModuleService(settings)
    activation = service.install(_runtime_archive(settings), source_name="local.zip")
    service.finalize(activation)
    service.remove("local-test", operation_id="b" * 32)

    recovery = ExternalRuntimeModuleService(settings).recover_incomplete_activation()
    installed, failures = ExternalRuntimeModuleService(settings).discover()

    assert recovery is not None
    assert recovery.action == "remove_runtime_module"
    assert [item.manifest.module_id for item in installed] == ["local-test"]
    assert failures == ()


def test_finalized_runtime_replacement_has_no_recovery_record(settings) -> None:
    service = ExternalRuntimeModuleService(settings)
    initial = service.install(_runtime_archive(settings, version="1.0.0"), source_name="v1.zip")
    service.finalize(initial)
    replacement = service.install(_runtime_archive(settings, version="2.0.0"), source_name="v2.zip")
    service.finalize(replacement)

    recovery = ExternalRuntimeModuleService(settings).recover_incomplete_activation()
    installed, failures = ExternalRuntimeModuleService(settings).discover()

    assert recovery is None
    assert [item.manifest.version for item in installed] == ["2.0.0"]
    assert failures == ()
