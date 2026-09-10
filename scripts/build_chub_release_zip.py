#!/usr/bin/env python3
"""Build the current Chub formal deployment package from local release settings."""

from __future__ import annotations

import json
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent
VENV_PYTHON = PROJECT_ROOT / ".venv" / "bin" / "python"
if VENV_PYTHON.is_file() and Path(sys.executable).resolve() != VENV_PYTHON.resolve():
    import os

    os.execv(str(VENV_PYTHON), [str(VENV_PYTHON), __file__, *sys.argv[1:]])

sys.path.insert(0, str(PROJECT_ROOT))

from app.core.config import load_settings  # noqa: E402
from app.services.deployment_package import DeploymentPackageService  # noqa: E402


def main() -> int:
    service = DeploymentPackageService(load_settings())
    configuration = service.status().configuration
    artifact, digest = service._build(configuration)
    print(
        json.dumps(
            {"artifact_name": artifact.name, "artifact_size": artifact.stat().st_size, "sha256": digest},
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
