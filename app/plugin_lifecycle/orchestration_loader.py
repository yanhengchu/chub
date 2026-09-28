from __future__ import annotations

import importlib.util
import json
import logging
import sys
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Callable

from app.ai_interactions.models import PromptOptimizationVersions
from app.core.module_sources import RegisteredModuleSource
from app.plugin_lifecycle.orchestration_manifest import inspect_development_root

LOGGER = logging.getLogger("hub.orchestration_plugins")


@dataclass(frozen=True)
class OrchestrationPluginDescriptor:
    """Validated host descriptor for one loaded orchestration plugin."""

    module_id: str
    version: str
    scope: str
    stage_kinds: tuple[str, ...] = ()
    stage_runner: Callable[[str, str], str] | None = None
    implementation_ref: str | None = None
    optimization_prompt_builder: Callable[[str], str] | None = None
    optimization_result_reader: Callable[[str], PromptOptimizationVersions] | None = None


@dataclass(frozen=True)
class OrchestrationPluginLoadResult:
    artifact_id: str
    state: str
    descriptor: OrchestrationPluginDescriptor | None = None
    reason: str | None = None


def load_development_orchestration_plugin(
    source: RegisteredModuleSource,
    expected_artifact_id: str,
    chub_version: str,
) -> OrchestrationPluginLoadResult:
    """Load one fixed, indexed development entry after revalidating its source ref."""
    namespace: str | None = None
    try:
        metadata = inspect_development_root(source.root, source.module_id, chub_version)
        if metadata.get("development_ref") != expected_artifact_id:
            return _failed(expected_artifact_id, "开发源码引用与请求快照不匹配。")
        manifest_path = source.root / "chub-capability-orchestration.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        entry = manifest["entry"]
        module_name, _, attribute = entry.partition(":")
        relative = Path(*module_name.split("."))
        package_path = source.root / relative / "__init__.py"
        module_path = (
            package_path
            if package_path.is_file()
            else source.root / relative.with_suffix(".py")
        )
        if module_path.is_symlink() or not module_path.is_file():
            return _failed(expected_artifact_id, "插件入口文件不可用。")
        module_path.resolve(strict=True).relative_to(source.root.resolve(strict=True))
        namespace = (
            f"_chub_orchestration_{source.module_id.replace('-', '_')}"
            f"_{expected_artifact_id.rsplit('+', 1)[-1][:16]}"
        )
        spec = importlib.util.spec_from_file_location(
            namespace,
            module_path,
            submodule_search_locations=(
                [str(module_path.parent)] if module_path.name == "__init__.py" else None
            ),
        )
        if spec is None or spec.loader is None:
            return _failed(expected_artifact_id, "插件入口无法由宿主装配。")
        module = importlib.util.module_from_spec(spec)
        sys.modules[namespace] = module
        try:
            spec.loader.exec_module(module)
            factory = getattr(module, attribute, None)
            if not callable(factory):
                raise TypeError("entry factory unavailable")
            descriptor = factory()
        except Exception:
            unload_development_orchestration_plugin(expected_artifact_id)
            raise
        current = inspect_development_root(source.root, source.module_id, chub_version)
        if current.get("development_ref") != expected_artifact_id:
            unload_development_orchestration_plugin(expected_artifact_id)
            return _failed(expected_artifact_id, "插件源码在装配期间发生变化；请稍后重试。")
        if (
            not isinstance(descriptor, OrchestrationPluginDescriptor)
            or descriptor.module_id != source.module_id
            or descriptor.version != metadata.get("version")
            or descriptor.scope != metadata.get("scope")
            or descriptor.stage_kinds != ("prompt_optimization",)
            or not callable(descriptor.stage_runner)
            or descriptor.implementation_ref is not None
        ):
            unload_development_orchestration_plugin(expected_artifact_id)
            return _failed(expected_artifact_id, "插件入口返回了不兼容的模块描述。")
        descriptor = replace(descriptor, implementation_ref=expected_artifact_id)
        return OrchestrationPluginLoadResult(expected_artifact_id, "loaded", descriptor)
    except Exception:
        if namespace is not None:
            unload_development_orchestration_plugin(expected_artifact_id)
        LOGGER.warning("Unable to assemble orchestration plugin %s", source.module_id)
        return _failed(expected_artifact_id, "当前插件源码入口装配失败。")


def unload_development_orchestration_plugin(implementation_ref: str) -> None:
    """Unload a namespaced development module and any package submodules."""
    module_id, separator, digest = implementation_ref.partition("+")
    if (
        not separator
        or not module_id.startswith("development:")
        or len(digest) != 64
        or any(char not in "0123456789abcdef" for char in digest)
    ):
        return
    plugin_id = module_id.removeprefix("development:")
    if not plugin_id or any(not (part.isalnum() or part == "-") for part in plugin_id.split("-")):
        return
    namespace = f"_chub_orchestration_{plugin_id.replace('-', '_')}_{digest[:16]}"
    for name in tuple(sys.modules):
        if name == namespace or name.startswith(f"{namespace}."):
            sys.modules.pop(name, None)


def _failed(artifact_id: str, reason: str) -> OrchestrationPluginLoadResult:
    return OrchestrationPluginLoadResult(artifact_id, "failed", reason=reason)
