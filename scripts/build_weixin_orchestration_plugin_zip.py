from __future__ import annotations

import argparse
import json
import tomllib
import zipfile
from datetime import datetime, timezone
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = PROJECT_ROOT / "orchestration-modules" / "weixin-refinement"
def current_version() -> str:
    with (PROJECT_ROOT / "pyproject.toml").open("rb") as file:
        return str(tomllib.load(file)["project"]["version"])


def default_output(version: str, *, built_at: datetime | None = None) -> Path:
    timestamp = (built_at or datetime.now(timezone.utc)).strftime("%Y%m%d%H%M%S")
    return (
        PROJECT_ROOT
        / "data"
        / "local"
        / "artifacts"
        / "plugins"
        / "weixin-orchestration"
        / f"weixin-refinement-release-{version}-{timestamp}.zip"
    )


def build(output: Path, *, version: str = "1.0.0") -> Path:
    normalized_version = version.strip()
    if not normalized_version or len(normalized_version) > 64:
        raise ValueError("version must contain at most 64 characters")
    output.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for source in sorted(SOURCE_ROOT.rglob("*")):
            if not source.is_file() or "__pycache__" in source.parts:
                continue
            relative = source.relative_to(SOURCE_ROOT)
            if relative.name == "chub-capability-orchestration.json":
                manifest = json.loads(source.read_text("utf-8"))
                manifest["chub_version"] = current_version()
                manifest["version"] = normalized_version
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
    parser.add_argument("--output", type=Path)
    parser.add_argument("--version", default="1.0.0")
    args = parser.parse_args()
    output = (args.output or default_output(args.version)).expanduser().resolve()
    print(build(output, version=args.version))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
