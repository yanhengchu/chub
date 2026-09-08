from __future__ import annotations

import importlib
import sys
from pathlib import Path

from app.ai_runtime.modules import BuiltinRuntimeModule
from app.core.config import PROJECT_ROOT, Settings


def load_builtin_codex_module(
    settings: Settings,
    *,
    reload_source: bool = False,
) -> BuiltinRuntimeModule:
    """Load the checked-out Codex implementation only during explicit startup/refresh."""
    source_root = PROJECT_ROOT / "runtime-modules" / "codex-runtime"
    if str(source_root) not in sys.path:
        sys.path.insert(0, str(source_root))
    if reload_source:
        importlib.invalidate_caches()
        for module_name in tuple(sys.modules):
            if module_name == "chub_codex_runtime" or module_name.startswith("chub_codex_runtime."):
                sys.modules.pop(module_name, None)
    module = importlib.import_module("chub_codex_runtime.entry")
    factory = getattr(module, "create_runtime_module")
    return factory(settings)
