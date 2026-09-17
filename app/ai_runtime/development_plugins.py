from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from pydantic import ValidationError

from app.ai_runtime.runtime_plugin_packages import (
    MANIFEST_NAME,
    MAX_MANIFEST_BYTES,
    RuntimePluginInstallError,
    RuntimePluginLoadFailure,
    RuntimePluginManifest,
    load_runtime_plugin_from_root,
)
from app.ai_runtime.runtime_plugins import RuntimePlugin, RuntimePluginRegistry
from app.core.config import Settings
from app.core.module_sources import registered_module_sources


@dataclass(frozen=True)
class DevelopmentRuntimePlugin:
    manifest: RuntimePluginManifest
    module: RuntimePlugin
    root: Path


def development_runtime_artifact_id(implementation_id: str) -> str:
    return f"development:{implementation_id}"


def discover_development_runtime_plugins(
    settings: Settings,
    *,
    source_root: Path | None = None,
) -> tuple[RuntimePluginRegistry, tuple[DevelopmentRuntimePlugin, ...], tuple[RuntimePluginLoadFailure, ...]]:
    """Discover checked-out Runtime sources without assigning identity by path."""
    if source_root is not None:
        candidates = ((source_root.name, source_root, "development"),)
    else:
        candidates = tuple(
            (entry.module_id, entry.root, entry.source)
            for entry in registered_module_sources("runtime")
        )
    modules: list[RuntimePlugin] = []
    loaded: list[DevelopmentRuntimePlugin] = []
    failures: list[RuntimePluginLoadFailure] = []
    if source_root is not None:
        try:
            candidates = tuple(
                (candidate.name, candidate, "development")
                for candidate in sorted(source_root.iterdir())
                if not candidate.name.startswith(".")
                and candidate.is_dir()
                and not candidate.is_symlink()
            )
        except FileNotFoundError:
            return RuntimePluginRegistry(), (), ()
        except OSError as exc:
            return RuntimePluginRegistry(), (), (
                RuntimePluginLoadFailure("development", _reason(exc)),
            )
    for registered_id, candidate, source_name in candidates:
        manifest: RuntimePluginManifest | None = None
        try:
            manifest = _read_manifest(settings, candidate)
            if source_root is None and manifest.module_id != registered_id:
                raise _invalid("开发 Runtime 插件清单与模块索引不一致。")
            if source_name == "local" and manifest.chub_version != settings.app.version:
                continue
            namespace = _namespace(candidate, manifest)
            module = load_runtime_plugin_from_root(
                settings,
                candidate,
                manifest,
                namespace=namespace,
            )
            loaded.append(DevelopmentRuntimePlugin(manifest, module, candidate))
            modules.append(module)
        except RuntimePluginInstallError as exc:
            failures.append(
                RuntimePluginLoadFailure(
                    manifest.implementation_id if manifest is not None else registered_id,
                    exc.message,
                    name=manifest.display_name if manifest is not None else None,
                    version=manifest.version if manifest is not None else None,
                    description=manifest.description if manifest is not None else None,
                )
            )
        except Exception as exc:
            failures.append(
                RuntimePluginLoadFailure(
                    manifest.implementation_id if manifest is not None else candidate.name,
                    "开发 Runtime 插件加载失败。",
                    name=manifest.display_name if manifest is not None else None,
                    version=manifest.version if manifest is not None else None,
                    description=manifest.description if manifest is not None else None,
                )
            )
    registry = RuntimePluginRegistry()
    for module in modules:
        try:
            registry.register(module)
        except Exception as exc:
            failures.append(
                RuntimePluginLoadFailure(
                    module.descriptor.effective_implementation_id,
                    _reason(exc),
                    name=module.display_name,
                    description=module.description,
                )
            )
    return registry, tuple(loaded), tuple(failures)


def _read_manifest(settings: Settings, root: Path) -> RuntimePluginManifest:
    path = root / MANIFEST_NAME
    if not path.is_file() or path.is_symlink():
        raise _invalid("开发 Runtime 插件缺少有效清单。")
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise _invalid("开发 Runtime 插件清单不可读取。") from exc
    if len(raw) > MAX_MANIFEST_BYTES:
        raise _invalid("开发 Runtime 插件清单超过固定大小上限。")
    try:
        manifest = RuntimePluginManifest.model_validate(json.loads(raw.decode("utf-8")))
    except (UnicodeError, json.JSONDecodeError, ValidationError) as exc:
        raise _invalid("开发 Runtime 插件清单格式无效。") from exc
    if manifest.dependencies is not None:
        dependency_path = PurePosixPath(manifest.dependencies)
        if dependency_path.is_absolute() or ".." in dependency_path.parts:
            raise _invalid("开发 Runtime 插件依赖清单路径无效。")
        requirements = root.joinpath(*dependency_path.parts)
        if not requirements.is_file() or requirements.is_symlink():
            raise _invalid("开发 Runtime 插件依赖清单不可读取。")
    return manifest


def _namespace(root: Path, manifest: RuntimePluginManifest) -> str:
    digest = hashlib.sha256(str(root.resolve()).encode("utf-8")).hexdigest()[:12]
    return f"_chub_development_runtime_{manifest.module_id.replace('-', '_')}_{digest}"


def _invalid(message: str) -> RuntimePluginInstallError:
    return RuntimePluginInstallError(
        "runtime_plugin_install_invalid",
        message,
        kind="invalid_request",
    )


def _reason(exc: BaseException) -> str:
    detail = " ".join(str(exc).split())
    return detail[:300] or "开发 Runtime 插件加载失败。"
