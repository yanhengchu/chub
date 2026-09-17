#!/usr/bin/env python3
"""Canonical executable for the formal Codex Runtime module ZIP."""

from __future__ import annotations

import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

from scripts.build.codex_runtime_zip import main  # noqa: E402


if __name__ == "__main__":
    raise SystemExit(main())
