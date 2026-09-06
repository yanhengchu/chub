from __future__ import annotations

from collections.abc import Mapping
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

    @property
    def descriptor(self) -> RuntimeDescriptor:
        return CODEX_RUNTIME_DESCRIPTOR

    @property
    def display_name(self) -> str:
        return "Codex"

    @property
    def description(self) -> str:
        return "使用 Codex CLI 运行快速交互、实时终端和后台 AI 任务。"

    @property
    def is_default(self) -> bool:
        return True

    def build_adapter(self) -> CodexRuntimeAdapter:
        return CodexRuntimeAdapter(self._settings)

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
