from __future__ import annotations

import platform as stdlib_platform
from pathlib import Path
from typing import Literal


PlatformName = Literal["macos", "ubuntu", "windows", "unknown"]


def _read_os_release(path: Path = Path("/etc/os-release")) -> dict[str, str]:
    if not path.exists():
        return {}

    values: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if "=" not in line or line.startswith("#"):
            continue
        key, value = line.split("=", 1)
        values[key] = value.strip().strip('"')
    return values


def detect_platform(system_name: str | None = None) -> PlatformName:
    system = (system_name or stdlib_platform.system()).lower()
    if system == "darwin":
        return "macos"
    if system == "windows":
        return "windows"
    if system == "linux":
        os_release = _read_os_release()
        identifiers = f"{os_release.get('ID', '')} {os_release.get('ID_LIKE', '')}".lower()
        return "ubuntu" if "ubuntu" in identifiers else "unknown"
    return "unknown"
