"""Trusted local ZIP implementations for the Weixin refinement stage.

The coordinator owns the registry, activation preference and all Chub state.
An installed module receives only the one bounded refinement callback for its
current request; it cannot receive route, Session, Worker or filesystem input
from the external message path.
"""

from __future__ import annotations

import hashlib
import importlib
import importlib.machinery
import json
import os
import re
import shutil
import sys
import threading
import types
import zipfile
from collections.abc import Callable
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.core.config import Settings
from app.core.response import ApiError


MANIFEST_NAME = "chub-capability-orchestration.json"
REGISTRY_NAME = "registry.json"
REGISTRY_VERSION = 1
MODULE_PROTOCOL_VERSION = 1
ORCHESTRATION_PROTOCOL_VERSION = 1
CAPABILITY_PROTOCOL_VERSION = 1
MAX_MANIFEST_BYTES = 32 * 1024
MAX_ARCHIVE_MEMBERS = 500
MODULE_ID_PATTERN = r"^[a-z][a-z0-9-]{0,31}$"
ENTRY_PATTERN = r"^[A-Za-z_][A-Za-z0-9_.]*:[A-Za-z_][A-Za-z0-9_]*$"
_IMPORT_LOCK = threading.RLock()


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class _Manifest(_StrictModel):
    protocol_version: int = Field(default=MODULE_PROTOCOL_VERSION)
    module_id: str = Field(pattern=MODULE_ID_PATTERN)
    version: str = Field(min_length=1, max_length=64)
    module_type: str = Field(default="capability-orchestration")
    scope: str = Field(default="weixin-normal-text")
    orchestration_protocol_version: int = Field(
        default=ORCHESTRATION_PROTOCOL_VERSION
    )
    capability_protocol_version: int = Field(default=CAPABILITY_PROTOCOL_VERSION)
    display_name: str = Field(min_length=1, max_length=100)
    description: str = Field(min_length=1, max_length=300)
    chub_version: str = Field(min_length=1, max_length=64)
    entry: str = Field(pattern=ENTRY_PATTERN, max_length=200)

    def validate_supported(self, settings: Settings) -> None:
        if self.protocol_version != MODULE_PROTOCOL_VERSION:
            raise ValueError("模块协议版本不受支持。")
        if self.module_type != "capability-orchestration":
            raise ValueError("模块类型必须为 capability-orchestration。")
        if self.scope != "weixin-normal-text":
            raise ValueError("模块范围不支持当前微信任务。")
        if self.orchestration_protocol_version != ORCHESTRATION_PROTOCOL_VERSION:
            raise ValueError("编排协议版本不兼容。")
        if self.capability_protocol_version != CAPABILITY_PROTOCOL_VERSION:
            raise ValueError("能力协议版本不兼容。")
        if self.chub_version != settings.app.version:
            raise ValueError("模块与当前 Chub 版本不兼容。")


class _RegistryArtifact(_StrictModel):
    implementation_ref: str = Field(min_length=68, max_length=180)
    module_id: str = Field(pattern=MODULE_ID_PATTERN)
    version: str = Field(min_length=1, max_length=64)
    content_sha256: str = Field(min_length=64, max_length=64)
    display_name: str = Field(min_length=1, max_length=100)
    description: str = Field(min_length=1, max_length=300)


class _Registry(_StrictModel):
    version: int = Field(default=REGISTRY_VERSION)
    artifacts: tuple[_RegistryArtifact, ...] = Field(default=(), max_length=256)

    def validate_supported(self) -> None:
        if self.version != REGISTRY_VERSION:
            raise ValueError("编排模块注册表版本不受支持。")
        refs = [item.implementation_ref for item in self.artifacts]
        if len(refs) != len(set(refs)):
            raise ValueError("编排模块注册表包含重复产物。")


@dataclass(frozen=True)
class WeixinOrchestrationModulePreview:
    module_id: str
    version: str
    name: str
    description: str
    implementation_ref: str


@dataclass(frozen=True)
class WeixinOrchestrationModuleArtifact:
    implementation_ref: str
    module_id: str
    version: str
    name: str
    description: str
    available: bool
    reason: str | None = None


@dataclass(frozen=True)
class _LoadedModule:
    artifact: _RegistryArtifact
    root: Path
    execute_refinement: Callable[..., object]


@contextmanager
def _module_import_path(root: Path):
    original = list(sys.path)
    sys.path[:0] = [str(root)]
    try:
        yield
    finally:
        sys.path[:] = original


class WeixinOrchestrationModuleService:
    """Manage immutable, trusted ZIP artifacts for one fixed Chub scope."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.root = settings.openclaw.weixin_chub_mode.orchestration_modules_dir
        self.artifacts_dir = self.root / "artifacts"
        self.staging_dir = self.root / ".staging"
        self.registry_path = self.root / REGISTRY_NAME
        self._lock = threading.RLock()

    def recover(self) -> None:
        """Discard only incomplete staging data; the registry remains authority."""
        with self._lock:
            self._prepare_root()
            for entry in self.staging_dir.iterdir():
                if entry.is_dir() and not entry.is_symlink():
                    shutil.rmtree(entry, ignore_errors=True)
                elif entry.is_file() and not entry.is_symlink():
                    entry.unlink(missing_ok=True)

    def inspect_archive(
        self,
        archive: bytes,
        *,
        source_name: str,
    ) -> WeixinOrchestrationModulePreview:
        with self._lock:
            source_name = self._validate_archive_input(archive, source_name)
            del source_name
            self._prepare_root()
            candidate = self.staging_dir / uuid4().hex
            try:
                content = self._extract_candidate(candidate, archive)
                manifest = self._read_manifest(content)
                content_hash = self._content_hash(content)
                self._load_entry(content, manifest, content_hash)
                return self._preview(manifest, content_hash)
            finally:
                shutil.rmtree(candidate, ignore_errors=True)

    def install(
        self,
        archive: bytes,
        *,
        source_name: str,
    ) -> WeixinOrchestrationModulePreview:
        with self._lock:
            source_name = self._validate_archive_input(archive, source_name)
            del source_name
            self._prepare_root()
            candidate = self.staging_dir / uuid4().hex
            destination: Path | None = None
            try:
                content = self._extract_candidate(candidate, archive)
                manifest = self._read_manifest(content)
                content_hash = self._content_hash(content)
                self._load_entry(content, manifest, content_hash)
                preview = self._preview(manifest, content_hash)
                registry = self._read_registry()
                if any(
                    item.implementation_ref == preview.implementation_ref
                    for item in registry.artifacts
                ):
                    return preview
                destination = self.artifacts_dir / content_hash
                if destination.exists():
                    raise self._invalid("模块产物目录状态无法确认。")
                os.replace(content, destination)
                artifact = _RegistryArtifact(
                    implementation_ref=preview.implementation_ref,
                    module_id=preview.module_id,
                    version=preview.version,
                    content_sha256=content_hash,
                    display_name=preview.name,
                    description=preview.description,
                )
                try:
                    self._write_registry(
                        _Registry(artifacts=(*registry.artifacts, artifact))
                    )
                except Exception:
                    shutil.rmtree(destination, ignore_errors=True)
                    raise
                return preview
            finally:
                shutil.rmtree(candidate, ignore_errors=True)

    def list_artifacts(self) -> tuple[WeixinOrchestrationModuleArtifact, ...]:
        with self._lock:
            registry = self._read_registry()
            listed: list[WeixinOrchestrationModuleArtifact] = []
            for artifact in registry.artifacts:
                try:
                    self._load_registered(artifact)
                except ApiError as exc:
                    listed.append(
                        WeixinOrchestrationModuleArtifact(
                            artifact.implementation_ref,
                            artifact.module_id,
                            artifact.version,
                            artifact.display_name,
                            artifact.description,
                            False,
                            exc.message,
                        )
                    )
                else:
                    listed.append(
                        WeixinOrchestrationModuleArtifact(
                            artifact.implementation_ref,
                            artifact.module_id,
                            artifact.version,
                            artifact.display_name,
                            artifact.description,
                            True,
                        )
                    )
            return tuple(listed)

    def require(self, implementation_ref: str | None) -> _LoadedModule:
        if not isinstance(implementation_ref, str):
            raise self._invalid("编排模块引用无效。")
        with self._lock:
            registry = self._read_registry()
            artifact = next(
                (
                    item
                    for item in registry.artifacts
                    if item.implementation_ref == implementation_ref
                ),
                None,
            )
            if artifact is None:
                raise self._unavailable("已绑定的编排模块产物不可用。")
            return self._load_registered(artifact)

    def execute_refinement(
        self,
        *,
        implementation_ref: str | None,
        enqueue_refinement: Callable[[], object],
    ) -> object:
        loaded = self.require(implementation_ref)
        try:
            return loaded.execute_refinement(enqueue_refinement=enqueue_refinement)
        except ApiError:
            raise
        except Exception as exc:
            raise self._unavailable("编排模块执行失败，本次任务未执行。") from exc

    def remove(self, implementation_ref: str) -> None:
        with self._lock:
            registry = self._read_registry()
            artifact = next(
                (
                    item
                    for item in registry.artifacts
                    if item.implementation_ref == implementation_ref
                ),
                None,
            )
            if artifact is None:
                raise self._invalid("编排模块不存在或不可移除。")
            root = self.artifacts_dir / artifact.content_sha256
            if not root.is_dir() or root.is_symlink():
                raise self._unavailable("编排模块产物目录不可用，无法确认移除状态。")
            removed = self.staging_dir / f"{artifact.content_sha256}.removed.{uuid4().hex}"
            try:
                os.replace(root, removed)
                self._write_registry(
                    _Registry(
                        artifacts=tuple(
                            item
                            for item in registry.artifacts
                            if item.implementation_ref != implementation_ref
                        )
                    )
                )
            except Exception as exc:
                if removed.exists() and not root.exists():
                    os.replace(removed, root)
                raise self._invalid("编排模块移除失败，当前产物已保持不变。") from exc
            shutil.rmtree(removed, ignore_errors=True)

    def _load_registered(self, artifact: _RegistryArtifact) -> _LoadedModule:
        root = self.artifacts_dir / artifact.content_sha256
        if not root.is_dir() or root.is_symlink():
            raise self._unavailable("编排模块产物不可用。")
        manifest = self._read_manifest(root)
        if self._content_hash(root) != artifact.content_sha256:
            raise self._unavailable("编排模块产物内容已变化。")
        expected = self._implementation_ref(manifest, artifact.content_sha256)
        if (
            expected != artifact.implementation_ref
            or manifest.module_id != artifact.module_id
            or manifest.version != artifact.version
        ):
            raise self._unavailable("编排模块注册信息与产物不一致。")
        return _LoadedModule(
            artifact=artifact,
            root=root,
            execute_refinement=self._load_entry(root, manifest, artifact.content_sha256),
        )

    def _validate_archive_input(self, archive: bytes, source_name: str) -> str:
        if not archive or len(archive) > self.settings.openclaw.weixin_chub_mode.orchestration_module_max_archive_bytes:
            raise self._invalid("编排模块压缩包无效或超过固定大小上限。")
        safe_name = Path(source_name).name
        if not safe_name.lower().endswith(".zip"):
            raise self._invalid("只接受 ZIP 格式的编排模块。")
        return safe_name

    def _extract_candidate(self, candidate: Path, archive: bytes) -> Path:
        candidate.mkdir(mode=0o700)
        archive_path = candidate / "module.zip"
        archive_path.write_bytes(archive)
        os.chmod(archive_path, 0o600)
        content = candidate / "content"
        try:
            with zipfile.ZipFile(archive_path) as package:
                members = package.infolist()
                if len(members) > MAX_ARCHIVE_MEMBERS:
                    raise self._invalid("编排模块压缩包包含过多文件。")
                if sum(item.file_size for item in members) > self.settings.openclaw.weixin_chub_mode.orchestration_module_max_archive_bytes:
                    raise self._invalid("编排模块解压后超过固定大小上限。")
                manifest_count = sum(item.filename == MANIFEST_NAME for item in members)
                if manifest_count != 1:
                    raise self._invalid("编排模块包根目录必须包含唯一清单。")
                for member in members:
                    path = PurePosixPath(member.filename)
                    if (
                        not member.filename
                        or path.is_absolute()
                        or ".." in path.parts
                        or (member.external_attr >> 16) & 0o170000 == 0o120000
                    ):
                        raise self._invalid("编排模块压缩包包含不允许的路径。")
                content.mkdir(mode=0o700)
                for member in members:
                    if member.is_dir():
                        continue
                    target = content.joinpath(*PurePosixPath(member.filename).parts)
                    target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
                    with package.open(member) as source, target.open("wb") as output:
                        shutil.copyfileobj(source, output)
                    os.chmod(target, 0o600)
        except (OSError, zipfile.BadZipFile) as exc:
            raise self._invalid("编排模块压缩包不可读取。") from exc
        return content

    def _read_manifest(self, root: Path) -> _Manifest:
        path = root / MANIFEST_NAME
        try:
            if path.is_symlink() or not path.is_file():
                raise OSError("manifest unavailable")
            raw = path.read_bytes()
        except OSError as exc:
            raise self._unavailable("编排模块清单不可读取。") from exc
        if len(raw) > MAX_MANIFEST_BYTES:
            raise self._invalid("编排模块清单超过固定大小上限。")
        try:
            manifest = _Manifest.model_validate_json(raw)
            manifest.validate_supported(self.settings)
        except (ValidationError, ValueError) as exc:
            raise self._invalid("编排模块清单格式或协议无效。") from exc
        return manifest

    def _read_registry(self) -> _Registry:
        self._prepare_root()
        try:
            raw = self.registry_path.read_bytes()
        except FileNotFoundError:
            return _Registry()
        except OSError as exc:
            raise self._unavailable("编排模块注册表不可读取。") from exc
        if len(raw) > MAX_MANIFEST_BYTES:
            raise self._unavailable("编排模块注册表无效。")
        try:
            registry = _Registry.model_validate_json(raw)
            registry.validate_supported()
        except (ValidationError, ValueError) as exc:
            raise self._unavailable("编排模块注册表无效。") from exc
        return registry

    def _write_registry(self, registry: _Registry) -> None:
        registry.validate_supported()
        temporary = self.registry_path.with_suffix(".tmp")
        try:
            temporary.write_text(registry.model_dump_json(), encoding="utf-8")
            os.chmod(temporary, 0o600)
            os.replace(temporary, self.registry_path)
        except OSError as exc:
            temporary.unlink(missing_ok=True)
            raise self._unavailable("编排模块注册表无法保存。") from exc

    def _prepare_root(self) -> None:
        for path in (self.root, self.artifacts_dir, self.staging_dir):
            if path.exists() and path.is_symlink():
                raise self._unavailable("编排模块安装目录不可用。")
            path.mkdir(mode=0o700, parents=True, exist_ok=True)
            os.chmod(path, 0o700)

    def _load_entry(
        self,
        root: Path,
        manifest: _Manifest,
        content_hash: str,
    ) -> Callable[..., object]:
        module_name, function_name = manifest.entry.split(":", 1)
        namespace = f"_chub_orchestration_{content_hash[:16]}"
        qualified_name = f"{namespace}.{module_name}"
        try:
            with _IMPORT_LOCK, _module_import_path(root):
                importlib.invalidate_caches()
                for name in tuple(sys.modules):
                    if name == namespace or name.startswith(f"{namespace}."):
                        del sys.modules[name]
                spec = importlib.machinery.ModuleSpec(namespace, loader=None, is_package=True)
                spec.submodule_search_locations = [str(root)]
                package = types.ModuleType(namespace)
                package.__package__ = namespace
                package.__path__ = spec.submodule_search_locations
                package.__spec__ = spec
                sys.modules[namespace] = package
                loaded = importlib.import_module(qualified_name)
                callback = getattr(loaded, function_name)
                if not callable(callback):
                    raise TypeError("module entry is not callable")
                return callback
        except Exception as exc:
            for name in tuple(sys.modules):
                if name == namespace or name.startswith(f"{namespace}."):
                    del sys.modules[name]
            raise self._invalid("编排模块入口加载失败。") from exc

    def _content_hash(self, root: Path) -> str:
        digest = hashlib.sha256()
        total_bytes = 0
        try:
            files = sorted(
                path
                for path in root.rglob("*")
                if path.is_file()
                and "__pycache__" not in path.relative_to(root).parts
                and path.suffix != ".pyc"
            )
        except OSError as exc:
            raise self._unavailable("编排模块产物不可读取。") from exc
        for path in files:
            if path.is_symlink():
                raise self._unavailable("编排模块产物包含不允许的链接。")
            try:
                relative = path.relative_to(root).as_posix().encode("utf-8")
                content = path.read_bytes()
            except (OSError, UnicodeError) as exc:
                raise self._unavailable("编排模块产物不可读取。") from exc
            total_bytes += len(content)
            if total_bytes > self.settings.openclaw.weixin_chub_mode.orchestration_module_max_archive_bytes:
                raise self._unavailable("编排模块产物超过固定大小上限。")
            digest.update(len(relative).to_bytes(4, "big"))
            digest.update(relative)
            digest.update(len(content).to_bytes(8, "big"))
            digest.update(content)
        return digest.hexdigest()

    @staticmethod
    def _implementation_ref(manifest: _Manifest, content_hash: str) -> str:
        return f"{manifest.module_id}@{manifest.version}+{content_hash}"

    def _preview(
        self,
        manifest: _Manifest,
        content_hash: str,
    ) -> WeixinOrchestrationModulePreview:
        return WeixinOrchestrationModulePreview(
            module_id=manifest.module_id,
            version=manifest.version,
            name=manifest.display_name,
            description=manifest.description,
            implementation_ref=self._implementation_ref(manifest, content_hash),
        )

    @staticmethod
    def _invalid(message: str) -> ApiError:
        return ApiError(422, "weixin_orchestration_module_invalid", message)

    @staticmethod
    def _unavailable(message: str) -> ApiError:
        return ApiError(503, "weixin_orchestration_module_unavailable", message)
