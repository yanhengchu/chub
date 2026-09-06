from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

from app.ai_runtime import (
    AgentRuntimeAdapter,
    BuiltinRuntimeModuleRegistry,
    RuntimeDescriptor,
    RuntimeOperationError,
    RuntimeWorkerRunner,
)
from app.codex.runtime_adapter import CODEX_RUNTIME_DESCRIPTOR, CodexRuntimeAdapter
from app.codex.worker_runtime import CodexWorkerRuntime
from app.core.config import Settings


class CodexBuiltinRuntimeModule:
    """The current trusted Codex Runtime registered without external loading."""

    def __init__(
        self,
        settings: Settings,
        *,
        codex_home: Path | None = None,
        executable: str | None = None,
    ) -> None:
        self._settings = settings
        self._codex_home = codex_home
        self._executable = executable

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
        return CodexRuntimeAdapter(
            self._settings,
            codex_home=self._codex_home,
            executable=self._executable,
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
            executable=self._executable,
            workspaces=dict(workspaces),
        )


def create_builtin_runtime_modules(
    settings: Settings,
    *,
    codex_home: Path | None = None,
    codex_executable: str | None = None,
) -> BuiltinRuntimeModuleRegistry:
    return BuiltinRuntimeModuleRegistry(
        [
            CodexBuiltinRuntimeModule(
                settings,
                codex_home=codex_home,
                executable=codex_executable,
            )
        ]
    )
