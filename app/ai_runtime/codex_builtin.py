from __future__ import annotations

import importlib
import sys
from pathlib import Path

from app.ai_runtime.modules import BuiltinRuntimeModule
from app.core.config import PROJECT_ROOT, Settings


def load_builtin_codex_module(settings: Settings) -> BuiltinRuntimeModule:
    """Load the checked-out Codex implementation only during explicit startup/refresh."""
    source_root = PROJECT_ROOT / "runtime-modules" / "codex-runtime"
    if str(source_root) not in sys.path:
        sys.path.insert(0, str(source_root))
    module = importlib.import_module("chub_codex_runtime.entry")
    factory = getattr(module, "create_runtime_module")
    return factory(settings)
