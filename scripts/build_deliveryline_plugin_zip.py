from __future__ import annotations

import argparse
import json
import tomllib
import zipfile
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "business-modules" / "deliveryline"

def build(output: Path, version: str = "1.0.0") -> Path:
    output.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for source in SOURCE.rglob("*"):
            if not source.is_file(): continue
            if source.name == "chub-business-module.json":
                manifest = json.loads(source.read_text("utf-8"))
                manifest["version"] = version
                manifest["chub_version"] = str(tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["version"])
                archive.writestr(source.name, json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
            else: archive.write(source, source.relative_to(SOURCE).as_posix())
    output.chmod(0o600); return output

if __name__ == "__main__":
    parser = argparse.ArgumentParser(); parser.add_argument("--output", type=Path); parser.add_argument("--version", default="1.0.0")
    args = parser.parse_args(); output = args.output or ROOT / "data/local/artifacts/plugins/deliveryline" / f"deliveryline-release-{args.version}-{datetime.now(timezone.utc):%Y%m%d%H%M%S}.zip"
    print(build(output, args.version))
