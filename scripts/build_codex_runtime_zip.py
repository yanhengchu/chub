from __future__ import annotations

import argparse
import json
import re
import sys
import tomllib
import zipfile
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = PROJECT_ROOT / "runtime-modules" / "codex-runtime"
DEFAULT_OUTPUT = PROJECT_ROOT / "data" / "local" / "artifacts" / "runtime-modules" / "codex-runtime.zip"
IMPLEMENTATION_ID_PATTERN = re.compile(r"^codex-[0-9]{6}$")


def current_version() -> str:
    with (PROJECT_ROOT / "pyproject.toml").open("rb") as file:
        return str(tomllib.load(file)["project"]["version"])


def build(
    output: Path,
    *,
    implementation_id: str = "codex-010000",
    version: str = "1.0.0",
    description: str,
) -> Path:
    if IMPLEMENTATION_ID_PATTERN.fullmatch(implementation_id) is None:
        raise ValueError("implementation_id must be codex- followed by six digits")
    normalized_description = description.strip()
    if not normalized_description or len(normalized_description) > 300:
        raise ValueError("description must contain a release summary of at most 300 characters")
    output.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for source in sorted(SOURCE_ROOT.rglob("*")):
            if not source.is_file() or "__pycache__" in source.parts:
                continue
            relative = source.relative_to(SOURCE_ROOT)
            if relative.name == "chub-module.json":
                manifest = json.loads(source.read_text("utf-8"))
                manifest["chub_version"] = current_version()
                manifest["module_id"] = implementation_id
                manifest["implementation_id"] = implementation_id
                manifest["version"] = version
                manifest["description"] = normalized_description
                archive.writestr(
                    str(relative),
                    json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
                )
            else:
                archive.write(source, str(relative))
    output.chmod(0o600)
    return output


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--implementation-id", default="codex-010000")
    parser.add_argument("--version", default="1.0.0")
    parser.add_argument(
        "--description",
        required=True,
        help="面向维护者的简短发版特性说明（最多 300 字符）。",
    )
    args = parser.parse_args()
    output = build(
        args.output.expanduser().resolve(),
        implementation_id=args.implementation_id,
        version=args.version,
        description=args.description,
    )
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
