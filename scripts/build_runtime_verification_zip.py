from __future__ import annotations

import argparse
import json
import tomllib
import zipfile
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SOURCE_DIR = PROJECT_ROOT / "examples" / "runtime-modules" / "verification-runtime"
DEFAULT_OUTPUT = (
    PROJECT_ROOT / "data" / "local" / "artifacts" / "runtime-modules" / "verification-runtime.zip"
)


def current_version() -> str:
    with (PROJECT_ROOT / "pyproject.toml").open("rb") as file:
        data = tomllib.load(file)
    return str(data["project"]["version"])


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    manifest = json.loads((SOURCE_DIR / "chub-module.json").read_text("utf-8"))
    manifest["chub_version"] = current_version()
    output = args.output.expanduser().resolve()
    output.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("chub-module.json", json.dumps(manifest, indent=2) + "\n")
        archive.write(
            SOURCE_DIR / "chub_verification_runtime.py",
            "chub_verification_runtime.py",
        )
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
