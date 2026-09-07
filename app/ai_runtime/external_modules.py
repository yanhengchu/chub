from __future__ import annotations

import importlib
import importlib.machinery
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import types
import zipfile
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.ai_runtime.contracts import RuntimeOperationError
from app.ai_runtime.modules import BuiltinRuntimeModule, BuiltinRuntimeModuleRegistry
from app.ai_runtime.registry import RuntimeRegistry, validate_runtime_wiring
from app.ai_runtime.worker import WorkerRuntimeRegistry
from app.core.config import Settings


MANIFEST_NAME = "chub-module.json"
INSTALL_METADATA_NAME = ".chub-install.json"
ACTIVATION_JOURNAL_NAME = "runtime-module-activation.json"
STATE_CLEANUP_NAME = "runtime-module-state-cleanup.json"
MODULE_PROTOCOL_VERSION = 1
MAX_MANIFEST_BYTES = 32 * 1024
MAX_ARCHIVE_MEMBERS = 500
RUNTIME_MODULE_ID_PATTERN = r"^[a-z][a-z0-9-]{0,31}$"
_MODULE_IMPORT_LOCK = threading.RLock()


class RuntimeModuleInstallError(RuntimeOperationError):
    pass


def is_runtime_module_id(value: str) -> bool:
    return re.fullmatch(RUNTIME_MODULE_ID_PATTERN, value) is not None


class _Manifest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    protocol_version: Literal[MODULE_PROTOCOL_VERSION]
    module_id: str = Field(pattern=RUNTIME_MODULE_ID_PATTERN)
    runtime_id: str = Field(pattern=RUNTIME_MODULE_ID_PATTERN)
    implementation_id: str = Field(pattern=RUNTIME_MODULE_ID_PATTERN)
    native_session_compatibility_id: str = Field(min_length=1, max_length=64)
    module_type: Literal["runtime"]
    display_name: str = Field(min_length=1, max_length=100)
    description: str = Field(min_length=1, max_length=300)
    version: str = Field(min_length=1, max_length=64)
    chub_version: str = Field(min_length=1, max_length=64)
    entry: str = Field(
        pattern=r"^[A-Za-z_][A-Za-z0-9_.]*:[A-Za-z_][A-Za-z0-9_]*$",
        max_length=200,
    )
    dependencies: str | None = Field(default=None, max_length=160)


class _InstallMetadata(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    module_id: str
    runtime_id: str
    implementation_id: str
    version: str
    source_name: str = Field(min_length=1, max_length=255)


class _ActivationJournal(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    version: Literal[1] = 1
    operation_id: str = Field(pattern=r"^[a-f0-9]{32}$")
    runtime_id: str = Field(default="codex", pattern=RUNTIME_MODULE_ID_PATTERN)
    module_id: str = Field(pattern=RUNTIME_MODULE_ID_PATTERN)
    previous_name: str | None = Field(default=None, max_length=100)
    phase: Literal["activated", "worker_reload_requested", "removed"] = "activated"


class _StateCleanupRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    version: Literal[1] = 1
    operation_id: str = Field(pattern=r"^[a-f0-9]{32}$")
    action: Literal["install_runtime_module", "remove_runtime_module"]
    module_id: str = Field(pattern=RUNTIME_MODULE_ID_PATTERN)
    session_ids: tuple[str, ...] = Field(default=(), max_length=500)


@dataclass(frozen=True)
class InstalledRuntimeModule:
    manifest: _Manifest
    module: BuiltinRuntimeModule
    root: Path


@dataclass(frozen=True)
class RuntimeModuleActivation:
    installed: InstalledRuntimeModule
    previous_root: Path | None
    operation_id: str


@dataclass(frozen=True)
class RuntimeModuleRecovery:
    operation_id: str
    runtime_id: str
    module_id: str
    action: Literal["install_runtime_module", "remove_runtime_module"]


@dataclass(frozen=True)
class RuntimeModuleStateCleanup:
    operation_id: str
    action: Literal["install_runtime_module", "remove_runtime_module"]
    module_id: str
    session_ids: tuple[str, ...]


@dataclass(frozen=True)
class RuntimeModulePreview:
    module_id: str
    runtime_id: str
    implementation_id: str
    version: str
    name: str
    description: str


@dataclass(frozen=True)
class RuntimeModuleRemoval:
    runtime_id: str
    module_id: str
    previous_root: Path
    operation_id: str


@dataclass(frozen=True)
class RuntimeModuleLoadFailure:
    module_id: str
    reason: str
    name: str | None = None
    version: str | None = None
    description: str | None = None


@contextmanager
def _module_import_paths(root: Path):
    dependencies = root / "dependencies"
    paths: list[str] = []
    if dependencies.is_dir():
        paths.append(str(dependencies))
    original = list(sys.path)
    sys.path[:0] = paths
    try:
        yield
    finally:
        sys.path[:] = original


class ExternalRuntimeModuleService:
    """Install and discover trusted local Runtime modules from one fixed root."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.root = settings.ai_runtime.modules.install_dir
        self.runtimes_dir = self.root / "runtimes"
        self.staging_dir = self.root / ".staging"
        self.activation_journal_path = self.staging_dir / ACTIVATION_JOURNAL_NAME
        self.state_cleanup_path = self.staging_dir / STATE_CLEANUP_NAME

    def recover_incomplete_activation(self) -> RuntimeModuleRecovery | None:
        """Restore the previous module after a process stopped mid-activation."""
        try:
            raw = self.activation_journal_path.read_bytes()
        except FileNotFoundError:
            return None
        except OSError as exc:
            raise self._invalid("模块激活恢复记录不可读取。") from exc
        if len(raw) > MAX_MANIFEST_BYTES:
            raise self._invalid("模块激活恢复记录无效。")
        try:
            journal = _ActivationJournal.model_validate_json(raw)
        except ValidationError as exc:
            raise self._invalid("模块激活恢复记录无效。") from exc
        destination = self.runtimes_dir / journal.runtime_id / journal.module_id
        backup = (
            self.staging_dir / journal.previous_name
            if journal.previous_name is not None
            else None
        )
        failed = self.staging_dir / f"{journal.module_id}.recovered.{uuid4().hex}"
        try:
            if destination.exists():
                os.replace(destination, failed)
            if backup is not None and backup.exists():
                os.replace(backup, destination)
            else:
                shutil.rmtree(failed, ignore_errors=True)
            self.activation_journal_path.unlink(missing_ok=True)
        except OSError as exc:
            raise self._invalid("模块激活恢复失败。") from exc
        action: Literal["install_runtime_module", "remove_runtime_module"] = (
            "remove_runtime_module"
            if journal.phase == "removed"
            else "install_runtime_module"
        )
        return RuntimeModuleRecovery(
            journal.operation_id,
            journal.runtime_id,
            journal.module_id,
            action,
        )

    def discover(self) -> tuple[tuple[InstalledRuntimeModule, ...], tuple[RuntimeModuleLoadFailure, ...]]:
        loaded: list[InstalledRuntimeModule] = []
        failures: list[RuntimeModuleLoadFailure] = []
        try:
            self._discard_legacy_codex_layout()
        except RuntimeModuleInstallError as exc:
            return (), (RuntimeModuleLoadFailure("codex", exc.message),)
        try:
            entries = sorted(self.runtimes_dir.iterdir())
        except FileNotFoundError:
            return (), ()
        except OSError as exc:
            return (), (RuntimeModuleLoadFailure("unknown", self._reason(exc)),)
        for runtime_root in entries:
            if not runtime_root.is_dir() or runtime_root.is_symlink():
                continue
            for root in sorted(runtime_root.iterdir()):
                if not root.is_dir() or root.is_symlink():
                    continue
                manifest: _Manifest | None = None
                try:
                    manifest = self._read_manifest(root)
                    loaded.append(self._load_installed(root, manifest=manifest))
                except RuntimeModuleInstallError as exc:
                    failures.append(
                        RuntimeModuleLoadFailure(
                            manifest.implementation_id if manifest is not None else root.name,
                            exc.message,
                            name=manifest.display_name if manifest is not None else None,
                            version=manifest.version if manifest is not None else None,
                            description=manifest.description if manifest is not None else None,
                        )
                    )
        return tuple(loaded), tuple(failures)

    def inspect_archive(self, archive: bytes, *, source_name: str) -> RuntimeModulePreview:
        if not archive or len(archive) > self.settings.ai_runtime.modules.max_archive_bytes:
            raise self._invalid("模块压缩包无效或超过固定大小上限。")
        if not Path(source_name).name.lower().endswith(".zip"):
            raise self._invalid("只接受 ZIP 格式的 Runtime 模块。")
        self._prepare_root()
        candidate = self.staging_dir / uuid4().hex
        candidate.mkdir(mode=0o700)
        try:
            archive_path = candidate / "module.zip"
            archive_path.write_bytes(archive)
            self._extract_archive(archive_path, candidate / "content")
            manifest = self._read_manifest(candidate / "content")
            return RuntimeModulePreview(
                module_id=manifest.module_id,
                runtime_id=manifest.runtime_id,
                implementation_id=manifest.implementation_id,
                version=manifest.version,
                name=manifest.display_name,
                description=manifest.description,
            )
        except RuntimeModuleInstallError:
            raise
        except (OSError, zipfile.BadZipFile, ValidationError) as exc:
            raise self._invalid(self._reason(exc)) from exc
        finally:
            shutil.rmtree(candidate, ignore_errors=True)

    def build_registry(
        self,
        builtin: BuiltinRuntimeModuleRegistry,
    ) -> tuple[BuiltinRuntimeModuleRegistry, tuple[RuntimeModuleLoadFailure, ...]]:
        modules, failures = self.discover()
        registry = BuiltinRuntimeModuleRegistry()
        for implementation_id in builtin.implementation_ids():
            registry.register(builtin.require(implementation_id))
        for installed in modules:
            try:
                registry.register(installed.module)
            except RuntimeOperationError as exc:
                failures += (RuntimeModuleLoadFailure(installed.manifest.implementation_id, exc.message),)
        return registry, failures

    def install(
        self,
        archive: bytes,
        *,
        source_name: str,
        operation_id: str | None = None,
    ) -> RuntimeModuleActivation:
        if not archive:
            raise self._invalid("模块压缩包为空。")
        if len(archive) > self.settings.ai_runtime.modules.max_archive_bytes:
            raise self._invalid("模块压缩包超过固定大小上限。")
        safe_name = Path(source_name).name
        if not safe_name.lower().endswith(".zip"):
            raise self._invalid("只接受 ZIP 格式的 Runtime 模块。")
        self._prepare_root()
        candidate = self.staging_dir / uuid4().hex
        candidate.mkdir(mode=0o700)
        activation_journal: _ActivationJournal | None = None
        try:
            archive_path = candidate / "module.zip"
            archive_path.write_bytes(archive)
            self._extract_archive(archive_path, candidate / "content")
            content = candidate / "content"
            manifest = self._read_manifest(content)
            self._install_dependencies(content, manifest)
            installed = self._load_installed(content, manifest=manifest)
            self._validate_worker_wiring(installed)
            self._write_metadata(content, manifest, safe_name)
            destination = self.runtimes_dir / manifest.runtime_id / manifest.implementation_id
            destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            backup = self.staging_dir / f"{manifest.implementation_id}.previous.{uuid4().hex}"
            previous_root: Path | None = None
            resolved_operation_id = operation_id or uuid4().hex
            activation_journal = _ActivationJournal(
                operation_id=resolved_operation_id,
                runtime_id=manifest.runtime_id,
                module_id=manifest.implementation_id,
                previous_name=backup.name if destination.exists() else None,
            )
            self._write_activation_journal(activation_journal)
            if destination.exists():
                os.replace(destination, backup)
                previous_root = backup
            try:
                os.replace(content, destination)
            except Exception:
                if backup.exists():
                    os.replace(backup, destination)
                raise
            return RuntimeModuleActivation(
                InstalledRuntimeModule(manifest, installed.module, destination),
                previous_root,
                resolved_operation_id,
            )
        except RuntimeModuleInstallError:
            raise
        except Exception as exc:
            if activation_journal is not None:
                self.recover_incomplete_activation()
            if isinstance(exc, (OSError, zipfile.BadZipFile, ValidationError)):
                raise self._invalid(self._reason(exc)) from exc
            raise self._invalid("模块安装或装配失败。") from exc
        finally:
            shutil.rmtree(candidate, ignore_errors=True)

    def finalize(self, activation: RuntimeModuleActivation) -> None:
        self._clear_activation_journal(activation.operation_id)
        if activation.previous_root is not None:
            shutil.rmtree(activation.previous_root, ignore_errors=True)

    def mark_worker_reload_requested(self, activation: RuntimeModuleActivation) -> None:
        journal = self._read_activation_journal()
        if journal is None or journal.operation_id != activation.operation_id:
            raise self._invalid("模块激活恢复记录已变化。")
        self._write_activation_journal(journal.model_copy(update={"phase": "worker_reload_requested"}))

    def rollback(self, activation: RuntimeModuleActivation) -> None:
        destination = activation.installed.root
        failed = self.staging_dir / f"{activation.installed.manifest.module_id}.failed.{uuid4().hex}"
        if destination.exists():
            os.replace(destination, failed)
        if activation.previous_root is not None and activation.previous_root.exists():
            os.replace(activation.previous_root, destination)
        else:
            shutil.rmtree(failed, ignore_errors=True)
        self._clear_activation_journal(activation.operation_id)

    def remove(self, module_id: str, *, operation_id: str) -> RuntimeModuleRemoval:
        if not is_runtime_module_id(module_id):
            raise self._invalid("Runtime 模块标识无效。")
        self._prepare_root()
        destination = self.runtimes_dir / "codex" / module_id
        if not destination.is_dir() or destination.is_symlink():
            raise self._invalid("Runtime 模块不存在或不可移除。")
        manifest = self._read_manifest(destination)
        if manifest.implementation_id != module_id or manifest.module_id != module_id:
            raise self._invalid("Runtime 模块安装标识无效。")
        destination = self.runtimes_dir / manifest.runtime_id / module_id
        backup = self.staging_dir / f"{module_id}.removed.{uuid4().hex}"
        journal = _ActivationJournal(
            operation_id=operation_id,
            runtime_id=manifest.runtime_id,
            module_id=module_id,
            previous_name=backup.name,
            phase="removed",
        )
        self._write_activation_journal(journal)
        try:
            os.replace(destination, backup)
        except OSError:
            self._clear_activation_journal(operation_id)
            raise
        return RuntimeModuleRemoval(manifest.runtime_id, module_id, backup, operation_id)

    def finalize_removal(self, removal: RuntimeModuleRemoval) -> None:
        self._clear_activation_journal(removal.operation_id)
        shutil.rmtree(removal.previous_root, ignore_errors=True)

    def begin_state_cleanup(
        self,
        *,
        operation_id: str,
        action: Literal["install_runtime_module", "remove_runtime_module"],
        module_id: str,
        session_ids: tuple[str, ...],
    ) -> None:
        self._prepare_root()
        record = _StateCleanupRecord(
            operation_id=operation_id,
            action=action,
            module_id=module_id,
            session_ids=session_ids,
        )
        existing = self.pending_state_cleanup()
        if existing is not None and existing.operation_id != operation_id:
            raise self._invalid("Runtime 模块状态清理仍在恢复。")
        temporary = self.state_cleanup_path.with_suffix(".tmp")
        try:
            temporary.write_text(record.model_dump_json(), encoding="utf-8")
            os.chmod(temporary, 0o600)
            os.replace(temporary, self.state_cleanup_path)
        except OSError as exc:
            temporary.unlink(missing_ok=True)
            raise self._invalid("Runtime 模块状态清理记录不可写入。") from exc

    def pending_state_cleanup(self) -> RuntimeModuleStateCleanup | None:
        try:
            raw = self.state_cleanup_path.read_bytes()
        except FileNotFoundError:
            return None
        except OSError as exc:
            raise self._invalid("Runtime 模块状态清理记录不可读取。") from exc
        if len(raw) > MAX_MANIFEST_BYTES:
            raise self._invalid("Runtime 模块状态清理记录无效。")
        try:
            record = _StateCleanupRecord.model_validate_json(raw)
        except ValidationError as exc:
            raise self._invalid("Runtime 模块状态清理记录无效。") from exc
        return RuntimeModuleStateCleanup(
            operation_id=record.operation_id,
            action=record.action,
            module_id=record.module_id,
            session_ids=record.session_ids,
        )

    def complete_state_cleanup(self, operation_id: str) -> None:
        record = self.pending_state_cleanup()
        if record is None:
            return
        if record.operation_id != operation_id:
            raise self._invalid("Runtime 模块状态清理记录已变化。")
        try:
            self.state_cleanup_path.unlink(missing_ok=True)
        except OSError as exc:
            raise self._invalid("Runtime 模块状态清理记录无法清理。") from exc

    def rollback_removal(self, removal: RuntimeModuleRemoval) -> None:
        if not is_runtime_module_id(removal.module_id):
            raise self._invalid("Runtime 模块标识无效。")
        destination = self.runtimes_dir / removal.runtime_id / removal.module_id
        if removal.previous_root.exists():
            os.replace(removal.previous_root, destination)
        self._clear_activation_journal(removal.operation_id)

    def _prepare_root(self) -> None:
        for path in (self.root, self.runtimes_dir, self.staging_dir):
            if path.exists() and path.is_symlink():
                raise self._invalid("模块安装目录不可用。")
            path.mkdir(mode=0o700, parents=True, exist_ok=True)
            os.chmod(path, 0o700)

    def _discard_legacy_codex_layout(self) -> None:
        """Drop the pre-R1 single-version Codex installation as one fixed boundary."""
        self._prepare_root()
        legacy_root = self.runtimes_dir / "codex"
        manifest = legacy_root / MANIFEST_NAME
        if not manifest.exists():
            return
        if legacy_root.is_symlink() or manifest.is_symlink() or not manifest.is_file():
            raise self._invalid("旧 Runtime 安装目录不可安全清理。")
        try:
            shutil.rmtree(legacy_root)
        except OSError as exc:
            raise self._invalid("旧 Runtime 安装目录无法清理。") from exc

    def _read_activation_journal(self) -> _ActivationJournal | None:
        try:
            raw = self.activation_journal_path.read_bytes()
        except FileNotFoundError:
            return None
        except OSError as exc:
            raise self._invalid("模块激活恢复记录不可读取。") from exc
        if len(raw) > MAX_MANIFEST_BYTES:
            raise self._invalid("模块激活恢复记录无效。")
        try:
            return _ActivationJournal.model_validate_json(raw)
        except ValidationError as exc:
            raise self._invalid("模块激活恢复记录无效。") from exc

    def _write_activation_journal(self, journal: _ActivationJournal) -> None:
        temporary = self.activation_journal_path.with_suffix(".tmp")
        try:
            temporary.write_text(journal.model_dump_json(), encoding="utf-8")
            os.chmod(temporary, 0o600)
            os.replace(temporary, self.activation_journal_path)
        except OSError as exc:
            temporary.unlink(missing_ok=True)
            raise self._invalid("模块激活恢复记录不可写入。") from exc

    def _clear_activation_journal(self, operation_id: str) -> None:
        journal = self._read_activation_journal()
        if journal is None:
            return
        if journal.operation_id != operation_id:
            raise self._invalid("模块激活恢复记录已变化。")
        try:
            self.activation_journal_path.unlink(missing_ok=True)
        except OSError as exc:
            raise self._invalid("模块激活恢复记录无法清理。") from exc

    def _extract_archive(self, archive_path: Path, destination: Path) -> None:
        with zipfile.ZipFile(archive_path) as archive:
            members = archive.infolist()
            if len(members) > MAX_ARCHIVE_MEMBERS:
                raise self._invalid("模块压缩包包含过多文件。")
            if sum(item.file_size for item in members) > self.settings.ai_runtime.modules.max_archive_bytes:
                raise self._invalid("模块解压后超过固定大小上限。")
            for member in members:
                path = PurePosixPath(member.filename)
                if (
                    not member.filename
                    or path.is_absolute()
                    or ".." in path.parts
                    or member.is_dir() and len(path.parts) == 0
                    or (member.external_attr >> 16) & 0o170000 == 0o120000
                ):
                    raise self._invalid("模块压缩包包含不允许的路径。")
            destination.mkdir(mode=0o700)
            for member in members:
                if member.is_dir():
                    continue
                target = destination.joinpath(*PurePosixPath(member.filename).parts)
                target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
                with archive.open(member) as source, target.open("wb") as output:
                    shutil.copyfileobj(source, output)
                os.chmod(target, 0o600)

    def _read_manifest(self, root: Path) -> _Manifest:
        manifest_path = root / MANIFEST_NAME
        try:
            raw = manifest_path.read_bytes()
        except OSError as exc:
            raise self._invalid("模块清单不可读取。") from exc
        if len(raw) > MAX_MANIFEST_BYTES:
            raise self._invalid("模块清单超过固定大小上限。")
        try:
            value = json.loads(raw.decode("utf-8"))
            manifest = _Manifest.model_validate(value)
        except (UnicodeError, json.JSONDecodeError, ValidationError) as exc:
            raise self._invalid("模块清单格式无效。") from exc
        if manifest.chub_version != self.settings.app.version:
            raise self._invalid("模块与当前 Chub 版本不兼容。")
        if manifest.dependencies is not None:
            dependency_path = PurePosixPath(manifest.dependencies)
            if dependency_path.is_absolute() or ".." in dependency_path.parts:
                raise self._invalid("模块依赖清单路径无效。")
        return manifest

    def _install_dependencies(self, root: Path, manifest: _Manifest) -> None:
        if manifest.dependencies is None:
            return
        requirements = root.joinpath(*PurePosixPath(manifest.dependencies).parts)
        if not requirements.is_file() or requirements.is_symlink():
            raise self._invalid("模块依赖清单不可读取。")
        dependencies = root / "dependencies"
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "pip",
                "install",
                "--disable-pip-version-check",
                "--no-input",
                "--target",
                str(dependencies),
                "-r",
                str(requirements),
            ],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=120,
            check=False,
        )
        if result.returncode != 0:
            raise self._invalid("模块依赖安装失败。")

    def _load_installed(
        self,
        root: Path,
        *,
        manifest: _Manifest | None = None,
    ) -> InstalledRuntimeModule:
        manifest = manifest or self._read_manifest(root)
        module_name, factory_name = manifest.entry.split(":", 1)
        namespace = f"_chub_runtime_{manifest.module_id.replace('-', '_')}"
        qualified_module_name = f"{namespace}.{module_name}"
        try:
            # ZIP imports manipulate process-global sys.modules and sys.path.
            # Requests can discover the same implementation concurrently.
            with _MODULE_IMPORT_LOCK, _module_import_paths(root):
                importlib.invalidate_caches()
                for loaded_name in tuple(sys.modules):
                    if loaded_name == namespace or loaded_name.startswith(
                        f"{namespace}."
                    ):
                        del sys.modules[loaded_name]
                package_spec = importlib.machinery.ModuleSpec(
                    namespace,
                    loader=None,
                    is_package=True,
                )
                package_spec.submodule_search_locations = [str(root)]
                package = types.ModuleType(namespace)
                package.__package__ = namespace
                package.__path__ = package_spec.submodule_search_locations
                package.__spec__ = package_spec
                sys.modules[namespace] = package
                imported = importlib.import_module(qualified_module_name)
                factory = getattr(imported, factory_name)
                if not callable(factory):
                    raise TypeError("module entry is not callable")
                module = factory(self.settings)
        except Exception as exc:
            for loaded_name in tuple(sys.modules):
                if loaded_name == namespace or loaded_name.startswith(f"{namespace}."):
                    del sys.modules[loaded_name]
            raise self._invalid("模块入口加载失败。") from exc
        if not isinstance(module, BuiltinRuntimeModule):
            raise self._invalid("模块入口未返回 Runtime 注册对象。")
        if (
            module.descriptor.runtime_id != manifest.runtime_id
            or module.descriptor.effective_implementation_id != manifest.implementation_id
            or manifest.module_id != manifest.implementation_id
            or module.descriptor.native_session_compatibility_id
            != manifest.native_session_compatibility_id
        ):
            raise self._invalid("模块清单与 Runtime 标识不一致。")
        if module.display_name != manifest.display_name or module.description != manifest.description:
            raise self._invalid("模块清单与 Runtime 展示信息不一致。")
        return InstalledRuntimeModule(manifest, module, root)

    def _validate_worker_wiring(self, installed: InstalledRuntimeModule) -> None:
        try:
            adapter = installed.module.build_adapter()
            runner = installed.module.build_worker_runner(adapter, workspaces={})
            validate_runtime_wiring(adapter, runner)
            RuntimeRegistry([adapter])
            WorkerRuntimeRegistry([runner])
            if adapter.status().runtime_id != installed.manifest.runtime_id:
                raise RuntimeOperationError(
                    "runtime_status_invalid",
                    "Runtime Adapter status does not match its module ID",
                    kind="conflict",
                )
        except RuntimeOperationError as exc:
            raise self._invalid(exc.message) from exc

    def _write_metadata(self, root: Path, manifest: _Manifest, source_name: str) -> None:
        metadata = _InstallMetadata(
            module_id=manifest.module_id,
            runtime_id=manifest.runtime_id,
            implementation_id=manifest.implementation_id,
            version=manifest.version,
            source_name=source_name,
        )
        path = root / INSTALL_METADATA_NAME
        path.write_text(metadata.model_dump_json(), encoding="utf-8")
        os.chmod(path, 0o600)

    @staticmethod
    def _reason(exc: BaseException) -> str:
        detail = " ".join(str(exc).split())
        return detail[:300] or "模块安装失败。"

    @staticmethod
    def _invalid(message: str) -> RuntimeModuleInstallError:
        return RuntimeModuleInstallError(
            "runtime_module_install_invalid",
            message,
            kind="invalid_request",
        )
