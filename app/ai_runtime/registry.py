from __future__ import annotations

from collections.abc import Iterable

from app.ai_runtime.contracts import (
    AgentRuntimeAdapter,
    RuntimeCapability,
    RuntimeCapabilityMatrix,
    RuntimeDescriptor,
    RuntimeActivityEventAdapter,
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
    "activity_events": RuntimeActivityEventAdapter,
    "model_catalog": RuntimeModelCatalogAdapter,
}


def validate_runtime_wiring(adapter: object, runner: object) -> str:
    """Validate the Runtime identity shared by an Adapter and a Runner."""

    adapter_descriptor = getattr(adapter, "descriptor", None)
    runner_descriptor = getattr(runner, "descriptor", None)
    if not isinstance(adapter_descriptor, RuntimeDescriptor) or not isinstance(
        runner_descriptor,
        RuntimeDescriptor,
    ):
        raise RuntimeOperationError(
            "runtime_wiring_invalid",
            "Runtime Adapter and Runner descriptors are invalid",
            kind="conflict",
        )
    if adapter_descriptor.runtime_id != runner_descriptor.runtime_id:
        raise RuntimeOperationError(
            "runtime_wiring_invalid",
            "Runtime Adapter and Runner owners do not match",
            kind="conflict",
        )
    return adapter_descriptor.runtime_id


class RuntimeRegistry:
    def __init__(self, adapters: Iterable[AgentRuntimeAdapter] = ()) -> None:
        self._adapters: dict[str, AgentRuntimeAdapter] = {}
        self._descriptors: dict[str, RuntimeDescriptor] = {}
        for adapter in adapters:
            self.register(adapter)

    def register(self, adapter: AgentRuntimeAdapter) -> None:
        descriptor = adapter.descriptor
        if not isinstance(descriptor, RuntimeDescriptor):
            raise RuntimeOperationError(
                "runtime_descriptor_invalid",
                "Runtime Adapter descriptor is invalid",
                kind="conflict",
            )
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

    def _require_identity(
        self,
        runtime_id: str,
        adapter: AgentRuntimeAdapter,
    ) -> RuntimeDescriptor:
        descriptor = adapter.descriptor
        registered = self._descriptors[runtime_id]
        if descriptor != registered:
            raise RuntimeOperationError(
                "runtime_identity_invalid",
                f"Runtime Adapter descriptor does not match registration: {runtime_id}",
                kind="conflict",
            )
        return registered

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
        self._require_identity(runtime_id, adapter)
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
        for runtime_id, adapter in self._adapters.items():
            self._require_identity(runtime_id, adapter)
        return tuple(self._adapters)

    def capability_matrix(self) -> tuple[RuntimeCapabilityMatrix, ...]:
        """Return the fixed adapter capability view without selecting a Runtime."""
        matrix: list[RuntimeCapabilityMatrix] = []
        for runtime_id, adapter in self._adapters.items():
            descriptor = self._require_identity(runtime_id, adapter)
            status = adapter.status()
            if status.runtime_id != runtime_id:
                raise RuntimeOperationError(
                    "runtime_status_invalid",
                    f"Runtime Adapter status does not match descriptor: {runtime_id}",
                    kind="conflict",
                )
            matrix.append(
                RuntimeCapabilityMatrix.from_descriptor(
                    descriptor,
                    available=status.available,
                    reason=status.reason,
                )
            )
        return tuple(matrix)
