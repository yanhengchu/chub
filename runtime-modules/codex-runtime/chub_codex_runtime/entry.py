from __future__ import annotations

from collections.abc import Mapping
import json
from pathlib import Path

from app.ai_runtime import (
    AgentRuntimeAdapter,
    RuntimeDescriptor,
    RuntimeOperationError,
    RuntimeWorkerRunner,
)
from app.core.config import Settings

from .runtime_adapter import CODEX_RUNTIME_DESCRIPTOR, CodexRuntimeAdapter
from .worker_runtime import CodexWorkerRuntime


class CodexRuntimeModule:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        manifest = json.loads(Path(__file__).parent.parent.joinpath("chub-module.json").read_text("utf-8"))
        self._descriptor = CODEX_RUNTIME_DESCRIPTOR.model_copy(
            update={
                "runtime_id": manifest["runtime_id"],
                "implementation_id": manifest["implementation_id"],
                "native_session_compatibility_id": manifest["native_session_compatibility_id"],
            }
        )
        self._description = manifest["description"]

    @property
    def descriptor(self) -> RuntimeDescriptor:
        return self._descriptor

    @property
    def display_name(self) -> str:
        return "Codex"

    @property
    def description(self) -> str:
        return self._description

    @property
    def is_default(self) -> bool:
        return self._descriptor.effective_implementation_id == "builtin-dev"

    def build_adapter(self) -> CodexRuntimeAdapter:
        return CodexRuntimeAdapter(self._settings, descriptor=self._descriptor)

    def configure_worker_adapter(
        self,
        adapter: AgentRuntimeAdapter,
        *,
        executable: str | None,
        codex_home: Path,
    ) -> None:
        if not isinstance(adapter, CodexRuntimeAdapter):
            raise RuntimeOperationError(
                "runtime_module_wiring_invalid",
                "Codex Runtime module requires its own Adapter",
                kind="conflict",
            )
        adapter.configure_worker_environment(
            executable=executable,
            codex_home=codex_home,
        )

    def build_worker_runner(
        self,
        adapter: AgentRuntimeAdapter,
        *,
        workspaces: Mapping[str, Path],
    ) -> RuntimeWorkerRunner:
        if not isinstance(adapter, CodexRuntimeAdapter):
            raise RuntimeOperationError(
                "runtime_module_wiring_invalid",
                "Codex Runtime module requires its own Adapter",
                kind="conflict",
            )
        return CodexWorkerRuntime(
            adapter,
            executable=adapter.executable,
            workspaces=dict(workspaces),
        )


def create_runtime_module(settings: Settings) -> CodexRuntimeModule:
    return CodexRuntimeModule(settings)
