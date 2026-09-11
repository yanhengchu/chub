from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, runtime_checkable

from app.ai_runtime.contracts import (
    AgentRuntimeAdapter,
    RuntimeDescriptor,
    RuntimeOperationError,
)
from app.ai_runtime.worker import RuntimeWorkerRunner


@runtime_checkable
class RuntimePlugin(Protocol):
    """A trusted, in-process Runtime definition before ZIP loading exists."""

    @property
    def descriptor(self) -> RuntimeDescriptor: ...

    @property
    def display_name(self) -> str: ...

    @property
    def description(self) -> str: ...

    @property
    def is_default(self) -> bool: ...

    def build_adapter(self) -> AgentRuntimeAdapter: ...

    def build_worker_runner(
        self,
        adapter: AgentRuntimeAdapter,
        *,
        workspaces: Mapping[str, Path],
    ) -> RuntimeWorkerRunner: ...


@dataclass(frozen=True)
class RuntimePluginNavigation:
    runtime_id: str
    implementation_id: str
    name: str
    description: str


class RuntimePluginRegistry:
    """Fixed Runtime plugin definitions shared by Web and Worker startup."""

    def __init__(self, modules: Iterable[RuntimePlugin] = ()) -> None:
        self._modules: dict[str, RuntimePlugin] = {}
        self._descriptors: dict[str, RuntimeDescriptor] = {}
        self._navigation: dict[str, RuntimePluginNavigation] = {}
        for module in modules:
            self.register(module)

    def register(self, module: RuntimePlugin) -> None:
        if not isinstance(module, RuntimePlugin):
            raise RuntimeOperationError(
                "runtime_plugin_invalid",
                "Runtime plugin does not implement the fixed plugin contract",
                kind="conflict",
            )
        descriptor = module.descriptor
        if not isinstance(descriptor, RuntimeDescriptor):
            raise RuntimeOperationError(
                "runtime_plugin_invalid",
                "Runtime plugin descriptor is invalid",
                kind="conflict",
            )
        if not isinstance(module.display_name, str) or not module.display_name.strip():
            raise RuntimeOperationError(
                "runtime_plugin_invalid",
                "Runtime plugin display name is invalid",
                kind="conflict",
            )
        if not isinstance(module.description, str) or not module.description.strip():
            raise RuntimeOperationError(
                "runtime_plugin_invalid",
                "Runtime plugin description is invalid",
                kind="conflict",
            )
        if not isinstance(module.is_default, bool):
            raise RuntimeOperationError(
                "runtime_plugin_invalid",
                "Runtime plugin default marker is invalid",
                kind="conflict",
            )
        implementation_id = descriptor.effective_implementation_id
        if implementation_id in self._modules:
            raise RuntimeOperationError(
                "runtime_plugin_duplicate",
                f"Runtime implementation is already registered: {implementation_id}",
                kind="conflict",
            )
        if module.is_default and any(
            candidate.is_default for candidate in self._modules.values()
        ):
            raise RuntimeOperationError(
                "runtime_plugin_default_duplicate",
                "Exactly one default Runtime plugin may be registered",
                kind="conflict",
            )
        self._modules[implementation_id] = module
        self._descriptors[implementation_id] = descriptor
        self._navigation[implementation_id] = RuntimePluginNavigation(
            runtime_id=descriptor.runtime_id,
            implementation_id=implementation_id,
            name=module.display_name,
            description=module.description,
        )

    def _require_identity(
        self,
        runtime_id: str,
        module: RuntimePlugin,
    ) -> RuntimeDescriptor:
        descriptor = module.descriptor
        registered = self._descriptors[runtime_id]
        if descriptor != registered:
            raise RuntimeOperationError(
                "runtime_plugin_identity_invalid",
                f"Runtime plugin descriptor does not match registration: {runtime_id}",
                kind="conflict",
            )
        return registered

    def runtime_ids(self) -> tuple[str, ...]:
        values: list[str] = []
        for implementation_id, module in self._modules.items():
            descriptor = self._require_identity(implementation_id, module)
            if descriptor.runtime_id not in values:
                values.append(descriptor.runtime_id)
        return tuple(values)

    def implementation_ids(self, runtime_id: str | None = None) -> tuple[str, ...]:
        values: list[str] = []
        for implementation_id, module in self._modules.items():
            descriptor = self._require_identity(implementation_id, module)
            if runtime_id is None or descriptor.runtime_id == runtime_id:
                values.append(implementation_id)
        return tuple(values)

    def navigation(self) -> tuple[RuntimePluginNavigation, ...]:
        navigation: list[RuntimePluginNavigation] = []
        runtime_ids: set[str] = set()
        for implementation_id, module in self._modules.items():
            descriptor = self._require_identity(implementation_id, module)
            if descriptor.runtime_id in runtime_ids:
                continue
            runtime_ids.add(descriptor.runtime_id)
            navigation.append(self._navigation[implementation_id])
        return tuple(navigation)

    def require_navigation(self, runtime_id: str) -> RuntimePluginNavigation:
        module = self._modules.get(runtime_id)
        if module is None:
            matches = [
                implementation_id
                for implementation_id, descriptor in self._descriptors.items()
                if descriptor.runtime_id == runtime_id
            ]
            if len(matches) == 1:
                runtime_id = matches[0]
                module = self._modules[runtime_id]
            elif len(matches) > 1:
                defaults = [item for item in matches if self._modules[item].is_default]
                if len(defaults) == 1:
                    return self._navigation[defaults[0]]
        if module is None:
            raise RuntimeOperationError(
                "runtime_plugin_unavailable",
                f"Runtime plugin is not registered: {runtime_id}",
            )
        self._require_identity(runtime_id, module)
        return self._navigation[runtime_id]

    def require(self, runtime_id: str) -> RuntimePlugin:
        module = self._modules.get(runtime_id)
        if module is None:
            matches = [
                implementation_id
                for implementation_id, descriptor in self._descriptors.items()
                if descriptor.runtime_id == runtime_id
            ]
            if len(matches) == 1:
                runtime_id = matches[0]
                module = self._modules[runtime_id]
            elif len(matches) > 1:
                defaults = [item for item in matches if self._modules[item].is_default]
                if len(defaults) == 1:
                    runtime_id = defaults[0]
                    module = self._modules[runtime_id]
        if module is None:
            raise RuntimeOperationError(
                "runtime_plugin_unavailable",
                f"Runtime plugin is not registered: {runtime_id}",
            )
        self._require_identity(runtime_id, module)
        return module

    def default(self) -> RuntimePlugin:
        defaults = [module for module in self._modules.values() if module.is_default]
        if len(defaults) != 1:
            raise RuntimeOperationError(
                "runtime_plugin_default_unavailable",
                "Exactly one default Runtime plugin must be registered",
                kind="conflict",
            )
        return defaults[0]
