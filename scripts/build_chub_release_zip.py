#!/usr/bin/env python3
"""Publish the current Chub formal deployment package from local release settings."""

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
from app.core.response import ApiError  # noqa: E402
from app.services.deployment_package import DeploymentPackageService  # noqa: E402


def main() -> int:
    service = DeploymentPackageService(load_settings())
    try:
        status = service.publish(source_ip="127.0.0.1")
    except ApiError as exc:
        print(
            json.dumps(
                {"success": False, "code": exc.code, "message": exc.message},
                ensure_ascii=False,
            ),
            file=sys.stderr,
        )
        return 1
    operation = status.operation
    if operation is None or operation.status != "succeeded":
        message = operation.message if operation is not None else "版本发布未完成。"
        print(json.dumps({"success": False, "message": message}, ensure_ascii=False), file=sys.stderr)
        return 1
    print(
        json.dumps(
            {
                "artifact_name": operation.artifact_name,
                "artifact_size": operation.artifact_size,
                "sha256": operation.sha256,
                "build_id": operation.build_id,
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
