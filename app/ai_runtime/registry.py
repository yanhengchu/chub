from __future__ import annotations

from collections.abc import Iterable

from app.ai_runtime.contracts import (
    AgentRuntimeAdapter,
    RuntimeCapability,
    RuntimeDescriptor,
    RuntimeInteractiveTerminalAdapter,
    RuntimeModelCatalogAdapter,
    RuntimeNativeSessionAdapter,
    RuntimeOperationError,
    RuntimeSessionArchiveAdapter,
    RuntimeWriterProbeAdapter,
)


_ADAPTER_CAPABILITY_CONTRACTS = {
    "runtime_status": AgentRuntimeAdapter,
    "native_session_mapping": RuntimeNativeSessionAdapter,
    "interactive_terminal": RuntimeInteractiveTerminalAdapter,
    "session_archive": RuntimeSessionArchiveAdapter,
    "writer_probe": RuntimeWriterProbeAdapter,
    "model_catalog": RuntimeModelCatalogAdapter,
}


class RuntimeRegistry:
    def __init__(self, adapters: Iterable[AgentRuntimeAdapter] = ()) -> None:
        self._adapters: dict[str, AgentRuntimeAdapter] = {}
        self._descriptors: dict[str, RuntimeDescriptor] = {}
        for adapter in adapters:
            self.register(adapter)

    def register(self, adapter: AgentRuntimeAdapter) -> None:
        descriptor = adapter.descriptor
        runtime_id = descriptor.runtime_id
        if runtime_id in self._adapters:
            raise RuntimeOperationError(
                "runtime_duplicate",
                f"Runtime is already registered: {runtime_id}",
                kind="conflict",
            )
        for capability, contract in _ADAPTER_CAPABILITY_CONTRACTS.items():
            if capability in descriptor.capabilities and not isinstance(
                adapter,
                contract,
            ):
                raise RuntimeOperationError(
                    "runtime_capability_invalid",
                    f"Runtime {runtime_id} does not implement capability: {capability}",
                    kind="conflict",
                )
        self._adapters[runtime_id] = adapter
        self._descriptors[runtime_id] = descriptor

    def require(
        self,
        runtime_id: str,
        capabilities: Iterable[RuntimeCapability] = (),
    ) -> AgentRuntimeAdapter:
        adapter = self._adapters.get(runtime_id)
        if adapter is None:
            raise RuntimeOperationError(
                "runtime_unavailable",
                f"Runtime is not registered: {runtime_id}",
            )
        missing = (
            frozenset(capabilities)
            - self._descriptors[runtime_id].capabilities
        )
        if missing:
            raise RuntimeOperationError(
                "runtime_capability_unavailable",
                f"Runtime {runtime_id} is missing capabilities: {', '.join(sorted(missing))}",
            )
        return adapter

    def runtime_ids(self) -> tuple[str, ...]:
        return tuple(self._adapters)
