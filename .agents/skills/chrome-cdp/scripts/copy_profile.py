#!/usr/bin/env python3
"""Copy one regular Chrome profile into an isolated CDP user-data directory."""

from __future__ import annotations

import argparse
import copy
import json
import shutil
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from chrome_process import close_running_chrome, is_chrome_running
from chrome_profiles import default_user_data_dir, is_user_profile_directory
from chrome_profiles import read_profile_names
from profile_store import (
    MANIFEST_VERSION,
    load_manifest,
    profile_store_lock,
    save_manifest,
)


DEFAULT_TARGET = Path.home() / "chrome-debug-data"
CACHE_DIRECTORIES = {
    "Cache",
    "Code Cache",
    "Media Cache",
    "DawnCache",
    "GPUCache",
    "GrShaderCache",
    "ShaderCache",
    "CacheStorage",
}


def cleanup_stale_staging(target: Path) -> None:
    prefix = f".{target.name}.tmp-"
    try:
        candidates = list(target.parent.iterdir())
    except FileNotFoundError:
        return
    for candidate in candidates:
        if (
            candidate.name.startswith(prefix)
            and candidate.is_dir()
            and not candidate.is_symlink()
        ):
            shutil.rmtree(candidate)


def ignore_cache_directories(
    _directory: str, names: list[str]
) -> set[str]:
    return {name for name in names if name in CACHE_DIRECTORIES}


def ensure_valid_source(user_data_dir: Path, profile: str) -> Path:
    if not is_user_profile_directory(profile):
        raise RuntimeError(
            "Profile must be 'Default' or a directory named like 'Profile 2'"
        )

    source_profile = user_data_dir / profile
    if not source_profile.is_dir():
        raise RuntimeError(f"Chrome profile not found: {source_profile}")
    return source_profile


def existing_manifest(target: Path) -> dict[str, object] | None:
    if target.is_symlink():
        raise RuntimeError(f"Target must not be a symbolic link: {target}")
    if not target.exists():
        return None
    if not target.is_dir():
        raise RuntimeError(f"Target exists and is not a directory: {target}")
    if not any(target.iterdir()):
        return None
    try:
        return load_manifest(target)
    except RuntimeError as exc:
        raise RuntimeError(
            f"Target contains data not managed by chrome-cdp: {target}"
        ) from exc


def merge_profile_local_state(source_root: Path, target: Path, profile: str) -> None:
    """Merge only the selected profile metadata, preserving Debug Chrome state."""
    source_path = source_root / "Local State"
    target_path = target / "Local State"
    if not source_path.is_file():
        return
    if not target_path.is_file():
        shutil.copy2(source_path, target_path)
        return

    try:
        source_state = json.loads(source_path.read_text(encoding="utf-8"))
        target_state = json.loads(target_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimeError("Unable to merge Chrome Local State profile metadata") from exc

    if not isinstance(source_state, dict):
        raise RuntimeError("Invalid source Chrome Local State")
    source_profile_state = source_state.get("profile", {})
    if not isinstance(source_profile_state, dict):
        raise RuntimeError("Invalid profile metadata in source Chrome Local State")
    source_cache = source_profile_state.get("info_cache", {})
    if not isinstance(source_cache, dict) or profile not in source_cache:
        return
    if not isinstance(target_state, dict):
        raise RuntimeError("Invalid Debug Chrome Local State")
    target_profile = target_state.setdefault("profile", {})
    if not isinstance(target_profile, dict):
        raise RuntimeError("Invalid profile metadata in Debug Chrome Local State")
    target_cache = target_profile.setdefault("info_cache", {})
    if not isinstance(target_cache, dict):
        raise RuntimeError("Invalid profile cache in Debug Chrome Local State")
    target_cache[profile] = copy.deepcopy(source_cache[profile])

    temporary = target_path.with_name(f".{target_path.name}.chrome-cdp.tmp")
    try:
        temporary.write_text(
            json.dumps(target_state, ensure_ascii=False, separators=(",", ":")),
            encoding="utf-8",
        )
        temporary.replace(target_path)
    finally:
        temporary.unlink(missing_ok=True)


def copy_profile(
    profile: str,
    *,
    source_user_data: Path | None = None,
    target: Path = DEFAULT_TARGET,
    close_running: bool = True,
) -> Path:
    source_root = (source_user_data or default_user_data_dir()).expanduser().resolve()
    source_profile = ensure_valid_source(source_root, profile)

    target = target.expanduser().absolute()
    target.parent.mkdir(parents=True, exist_ok=True)
    manifest = existing_manifest(target)
    if (target / profile).exists():
        raise RuntimeError(
            f"Debug Chrome profile already exists; refusing to overwrite: {target / profile}"
        )
    if manifest and manifest.get("source_user_data") not in (None, str(source_root)):
        raise RuntimeError(
            "Existing Debug Chrome profiles use a different source User Data"
        )

    from chrome_debug import debug_process_ids

    if debug_process_ids(target):
        raise RuntimeError("Stop Debug Chrome before copying a regular Chrome profile")
    regular_chrome_running = is_chrome_running()
    if regular_chrome_running and not close_running:
        raise RuntimeError("Close regular Chrome before copying a profile")
    if regular_chrome_running:
        print("Chrome is running; requesting a normal exit before copying.")
        close_running_chrome()

    with profile_store_lock(target):
        cleanup_stale_staging(target)
        manifest = existing_manifest(target)
        is_initialized = manifest is not None
        if (target / profile).exists():
            raise RuntimeError(
                f"Debug Chrome profile already exists; refusing to overwrite: {target / profile}"
            )
        if manifest and manifest.get("source_user_data") not in (None, str(source_root)):
            raise RuntimeError(
                "Existing Debug Chrome profiles use a different source User Data"
            )

        staging = Path(
            tempfile.mkdtemp(prefix=f".{target.name}.tmp-", dir=target.parent)
        )
        installed_profile: Path | None = None
        local_state_backup: bytes | None = None
        local_state_existed = False
        try:
            shutil.copytree(
                source_profile,
                staging / profile,
                ignore=ignore_cache_directories,
            )

            if is_initialized:
                installed_profile = target / profile
                (staging / profile).replace(installed_profile)
                staging.rmdir()
                local_state_path = target / "Local State"
                local_state_existed = local_state_path.is_file()
                if local_state_existed:
                    local_state_backup = local_state_path.read_bytes()
                merge_profile_local_state(source_root, target, profile)
            else:
                local_state = source_root / "Local State"
                if local_state.is_file():
                    shutil.copy2(local_state, staging / "Local State")
                if target.exists():
                    target.rmdir()
                staging.replace(target)

            now = datetime.now(timezone.utc).isoformat()
            names = read_profile_names(source_root)
            updated_manifest = copy.deepcopy(manifest) if manifest else {
                "version": MANIFEST_VERSION,
                "source_user_data": str(source_root),
                "profiles": {},
                "active_profile": profile,
            }
            profiles = updated_manifest.setdefault("profiles", {})
            if not isinstance(profiles, dict):
                raise RuntimeError("Invalid profiles in Debug Chrome manifest")
            profiles[profile] = {
                "copied_at": now,
                "name": names.get(profile, profile),
            }
            updated_manifest["version"] = MANIFEST_VERSION
            updated_manifest["source_user_data"] = str(source_root)
            updated_manifest["active_profile"] = profile
            save_manifest(target, updated_manifest)
        except Exception:
            shutil.rmtree(staging, ignore_errors=True)
            if is_initialized and installed_profile is not None:
                shutil.rmtree(installed_profile, ignore_errors=True)
                local_state_path = target / "Local State"
                if local_state_existed and local_state_backup is not None:
                    local_state_path.write_bytes(local_state_backup)
                elif not local_state_existed:
                    local_state_path.unlink(missing_ok=True)
            elif not is_initialized and target.exists():
                shutil.rmtree(target, ignore_errors=True)
            raise

    return target


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Copy one Chrome profile into an isolated CDP user-data directory."
    )
    parser.add_argument(
        "profile",
        help="Profile directory returned by chrome_profiles.py, such as Default or Profile 2.",
    )
    parser.add_argument(
        "--source-user-data",
        type=Path,
        help="Override the detected stable Chrome user-data directory.",
    )
    parser.add_argument(
        "--target",
        type=Path,
        default=DEFAULT_TARGET,
        help="Target user-data directory (default: ~/chrome-debug-data).",
    )
    parser.add_argument(
        "--require-stopped",
        action="store_true",
        help="Refuse to copy instead of requesting regular Chrome to exit.",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        target = copy_profile(
            args.profile,
            source_user_data=args.source_user_data,
            target=args.target,
            close_running=not args.require_stopped,
        )
    except (OSError, RuntimeError) as exc:
        print(f"chrome-cdp: {exc}", file=sys.stderr)
        return 1

    print(f"Copied profile '{args.profile}' to: {target}")
    print(f"Active Debug Chrome profile: {args.profile}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
