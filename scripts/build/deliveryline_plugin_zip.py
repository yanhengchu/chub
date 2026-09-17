"""Build the formal Deliveryline business-module ZIP."""

from __future__ import annotations

import argparse
import json
import tomllib
import zipfile
from datetime import datetime, timezone
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SOURCE = PROJECT_ROOT / "modules" / "business" / "deliveryline"
MANIFEST_NAME = "chub-business-module.json"


def default_output(version: str, *, built_at: datetime | None = None) -> Path:
    timestamp = (built_at or datetime.now(timezone.utc)).strftime("%Y%m%d%H%M%S")
    return (
        PROJECT_ROOT
        / "data/local/artifacts/plugins/deliveryline"
        / f"deliveryline-release-{version}-{timestamp}.zip"
    )


def _chub_version() -> str:
    with (PROJECT_ROOT / "pyproject.toml").open("rb") as source:
        project = tomllib.load(source).get("project")
    if not isinstance(project, dict) or not isinstance(project.get("version"), str):
        raise ValueError("Chub project version is unavailable")
    return project["version"]


def build(output: Path, version: str = "1.0.0") -> Path:
    normalized_version = version.strip()
    if not normalized_version or len(normalized_version) > 64:
        raise ValueError("version must contain at most 64 characters")
    if not SOURCE.is_dir() or not (SOURCE / MANIFEST_NAME).is_file():
        raise ValueError("Deliveryline module source is unavailable")
    output.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for source in sorted(SOURCE.rglob("*")):
            if not source.is_file():
                continue
            relative = source.relative_to(SOURCE).as_posix()
            if relative == MANIFEST_NAME:
                manifest = json.loads(source.read_text(encoding="utf-8"))
                if not isinstance(manifest, dict):
                    raise ValueError("Deliveryline module manifest is invalid")
                manifest["version"] = normalized_version
                manifest["chub_version"] = _chub_version()
                archive.writestr(
                    relative,
                    json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
                )
            else:
                archive.write(source, relative)
    output.chmod(0o600)
    return output


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Build the formal Deliveryline business-module ZIP."
    )
    parser.add_argument("--output", type=Path)
    parser.add_argument("--version", default="1.0.0")
    arguments = parser.parse_args()
    normalized_version = arguments.version.strip()
    output = arguments.output or default_output(normalized_version)
    print(build(output, normalized_version))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
