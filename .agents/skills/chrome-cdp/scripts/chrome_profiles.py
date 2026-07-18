#!/usr/bin/env python3
"""List reusable user profiles from the default Google Chrome installation."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


PROFILE_DIRECTORY_PATTERN = re.compile(r"^Profile \d+$")


@dataclass(frozen=True)
class ChromeProfile:
    directory: str
    name: str
    path: str


def default_user_data_dir() -> Path:
    """Return the stable Google Chrome user-data directory for this platform."""
    home = Path.home()
    if sys.platform == "darwin":
        return home / "Library" / "Application Support" / "Google" / "Chrome"
    if sys.platform.startswith("linux"):
        return home / ".config" / "google-chrome"
    if sys.platform == "win32":
        local_app_data = os.environ.get("LOCALAPPDATA")
        if not local_app_data:
            raise RuntimeError("LOCALAPPDATA is not set")
        return Path(local_app_data) / "Google" / "Chrome" / "User Data"
    raise RuntimeError(f"Unsupported platform: {sys.platform}")


def is_user_profile_directory(name: str) -> bool:
    """Exclude Chrome's Guest and System profiles."""
    return name == "Default" or PROFILE_DIRECTORY_PATTERN.fullmatch(name) is not None


def read_profile_names(user_data_dir: Path) -> dict[str, str]:
    """Read display names only; do not expose account identifiers."""
    local_state = user_data_dir / "Local State"
    if not local_state.is_file():
        return {}

    try:
        data: dict[str, Any] = json.loads(local_state.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return {}

    if not isinstance(data, dict):
        return {}
    profile_data = data.get("profile", {})
    if not isinstance(profile_data, dict):
        return {}
    info_cache = profile_data.get("info_cache", {})
    if not isinstance(info_cache, dict):
        return {}

    names: dict[str, str] = {}
    for directory, metadata in info_cache.items():
        if not is_user_profile_directory(directory) or not isinstance(metadata, dict):
            continue
        display_name = metadata.get("name")
        if isinstance(display_name, str) and display_name.strip():
            names[directory] = display_name.strip()
    return names


def profile_sort_key(directory: str) -> tuple[int, int]:
    if directory == "Default":
        return (0, 0)
    return (1, int(directory.removeprefix("Profile ")))


def list_profiles(user_data_dir: Path | None = None) -> list[ChromeProfile]:
    root = (user_data_dir or default_user_data_dir()).expanduser().resolve()
    if not root.is_dir():
        raise RuntimeError(f"Chrome user-data directory not found: {root}")

    names = read_profile_names(root)
    directories = {
        child.name
        for child in root.iterdir()
        if child.is_dir() and is_user_profile_directory(child.name)
    }

    return [
        ChromeProfile(
            directory=directory,
            name=names.get(directory, directory),
            path=str(root / directory),
        )
        for directory in sorted(directories, key=profile_sort_key)
    ]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="List user profiles from the default Google Chrome installation."
    )
    parser.add_argument(
        "--user-data-dir",
        type=Path,
        help="Override the detected Chrome user-data directory.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Return machine-readable JSON.",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        root = (args.user_data_dir or default_user_data_dir()).expanduser().resolve()
        profiles = list_profiles(root)
    except (OSError, RuntimeError) as exc:
        print(f"chrome-cdp: {exc}", file=sys.stderr)
        return 1

    if args.json:
        print(
            json.dumps(
                {
                    "user_data_dir": str(root),
                    "profiles": [asdict(profile) for profile in profiles],
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0

    print(f"Chrome User Data: {root}")
    if not profiles:
        print("No user profiles found.")
        return 0

    for profile in profiles:
        print(f"- {profile.directory}: {profile.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
