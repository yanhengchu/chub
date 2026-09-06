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
class BuiltinRuntimeModule(Protocol):
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
class BuiltinRuntimeNavigation:
    runtime_id: str
    name: str
    description: str


class BuiltinRuntimeModuleRegistry:
    """Fixed Runtime module definitions shared by Web and Worker startup."""

    def __init__(self, modules: Iterable[BuiltinRuntimeModule] = ()) -> None:
        self._modules: dict[str, BuiltinRuntimeModule] = {}
        self._descriptors: dict[str, RuntimeDescriptor] = {}
        self._navigation: dict[str, BuiltinRuntimeNavigation] = {}
        for module in modules:
            self.register(module)

    def register(self, module: BuiltinRuntimeModule) -> None:
        if not isinstance(module, BuiltinRuntimeModule):
            raise RuntimeOperationError(
                "runtime_module_invalid",
                "Runtime module does not implement the fixed module contract",
                kind="conflict",
            )
        descriptor = module.descriptor
        if not isinstance(descriptor, RuntimeDescriptor):
            raise RuntimeOperationError(
                "runtime_module_invalid",
                "Runtime module descriptor is invalid",
                kind="conflict",
            )
        if not isinstance(module.display_name, str) or not module.display_name.strip():
            raise RuntimeOperationError(
                "runtime_module_invalid",
                "Runtime module display name is invalid",
                kind="conflict",
            )
        if not isinstance(module.description, str) or not module.description.strip():
            raise RuntimeOperationError(
                "runtime_module_invalid",
                "Runtime module description is invalid",
                kind="conflict",
            )
        if not isinstance(module.is_default, bool):
            raise RuntimeOperationError(
                "runtime_module_invalid",
                "Runtime module default marker is invalid",
                kind="conflict",
            )
        runtime_id = descriptor.runtime_id
        if runtime_id in self._modules:
            raise RuntimeOperationError(
                "runtime_module_duplicate",
                f"Runtime module is already registered: {runtime_id}",
                kind="conflict",
            )
        if module.is_default and any(item.is_default for item in self._modules.values()):
            raise RuntimeOperationError(
                "runtime_module_default_duplicate",
                "More than one default Runtime module is registered",
                kind="conflict",
            )
        self._modules[runtime_id] = module
        self._descriptors[runtime_id] = descriptor
        self._navigation[runtime_id] = BuiltinRuntimeNavigation(
            runtime_id=runtime_id,
            name=module.display_name,
            description=module.description,
        )

    def _require_identity(
        self,
        runtime_id: str,
        module: BuiltinRuntimeModule,
    ) -> RuntimeDescriptor:
        descriptor = module.descriptor
        registered = self._descriptors[runtime_id]
        if descriptor != registered:
            raise RuntimeOperationError(
                "runtime_module_identity_invalid",
                f"Runtime module descriptor does not match registration: {runtime_id}",
                kind="conflict",
            )
        return registered

    def runtime_ids(self) -> tuple[str, ...]:
        for runtime_id, module in self._modules.items():
            self._require_identity(runtime_id, module)
        return tuple(self._modules)

    def navigation(self) -> tuple[BuiltinRuntimeNavigation, ...]:
        navigation: list[BuiltinRuntimeNavigation] = []
        for runtime_id, module in self._modules.items():
            self._require_identity(runtime_id, module)
            navigation.append(self._navigation[runtime_id])
        return tuple(navigation)

    def require_navigation(self, runtime_id: str) -> BuiltinRuntimeNavigation:
        module = self._modules.get(runtime_id)
        if module is None:
            raise RuntimeOperationError(
                "runtime_module_unavailable",
                f"Runtime module is not registered: {runtime_id}",
            )
        self._require_identity(runtime_id, module)
        return self._navigation[runtime_id]

    def require(self, runtime_id: str) -> BuiltinRuntimeModule:
        module = self._modules.get(runtime_id)
        if module is None:
            raise RuntimeOperationError(
                "runtime_module_unavailable",
                f"Runtime module is not registered: {runtime_id}",
            )
        self._require_identity(runtime_id, module)
        return module

    def default(self) -> BuiltinRuntimeModule:
        defaults: list[BuiltinRuntimeModule] = []
        for runtime_id, module in self._modules.items():
            self._require_identity(runtime_id, module)
            if module.is_default:
                defaults.append(module)
        if len(defaults) != 1:
            raise RuntimeOperationError(
                "runtime_module_default_unavailable",
                "Exactly one default Runtime module must be registered",
                kind="conflict",
            )
        return defaults[0]
