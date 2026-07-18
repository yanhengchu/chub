"""Manifest management for the multi-profile Debug Chrome user-data directory."""

from __future__ import annotations

import json
import os
import re
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any


MANIFEST_FILE = ".chrome-cdp.json"
MANIFEST_VERSION = 2
LOCK_TIMEOUT = 5
OWNER_GRACE_SECONDS = 2


def _process_exists(process_id: int) -> bool:
    try:
        os.kill(process_id, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def _remove_stale_lock(lock: Path) -> bool:
    try:
        process_id = int((lock / "owner").read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        try:
            if time.time() - lock.stat().st_mtime < OWNER_GRACE_SECONDS:
                return False
            lock.rmdir()
        except OSError:
            return False
        return True
    if _process_exists(process_id):
        return False
    try:
        (lock / "owner").unlink(missing_ok=True)
        lock.rmdir()
    except OSError:
        return False
    return True


@contextmanager
def profile_store_lock(user_data_dir: Path, timeout: float = LOCK_TIMEOUT):
    """Serialize manifest/profile mutations without external dependencies."""
    lock = user_data_dir.parent / f".{user_data_dir.name}.chrome-cdp.lock"
    deadline = time.monotonic() + timeout
    while True:
        try:
            lock.mkdir()
            break
        except FileExistsError:
            if _remove_stale_lock(lock):
                continue
            if time.monotonic() >= deadline:
                raise RuntimeError(
                    "Another chrome-cdp profile operation is still running"
                )
            time.sleep(0.1)
    try:
        (lock / "owner").write_text(str(os.getpid()), encoding="utf-8")
        yield
    finally:
        try:
            (lock / "owner").unlink(missing_ok=True)
            lock.rmdir()
        except OSError:
            pass


def load_manifest(user_data_dir: Path) -> dict[str, Any]:
    path = user_data_dir / MANIFEST_FILE
    if not path.is_file():
        raise RuntimeError(f"Debug profile is not initialized; missing: {path}")
    try:
        payload: Any = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"Invalid debug profile manifest: {path}") from exc
    if not isinstance(payload, dict):
        raise RuntimeError(f"Invalid debug profile manifest: {path}")

    if "profiles" not in payload and isinstance(payload.get("profile_directory"), str):
        profile = payload["profile_directory"]
        payload = {
            "version": MANIFEST_VERSION,
            "source_user_data": payload.get("source_user_data"),
            "profiles": {
                profile: {
                    "copied_at": payload.get("created_at"),
                }
            },
            "active_profile": profile,
        }

    profiles = payload.get("profiles")
    active_profile = payload.get("active_profile")
    if not isinstance(profiles, dict) or not isinstance(active_profile, str):
        raise RuntimeError(f"Invalid debug profile manifest: {path}")
    return payload


def save_manifest(user_data_dir: Path, manifest: dict[str, Any]) -> None:
    path = user_data_dir / MANIFEST_FILE
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            prefix=f".{MANIFEST_FILE}.",
            suffix=".tmp",
            dir=user_data_dir,
            delete=False,
        ) as temporary:
            temporary.write(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
            temporary.flush()
            os.fsync(temporary.fileno())
            temporary_path = Path(temporary.name)
        temporary_path.replace(path)
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


def active_profile(user_data_dir: Path) -> str:
    manifest = load_manifest(user_data_dir)
    profile = manifest["active_profile"]
    if profile not in manifest["profiles"] or not (user_data_dir / profile).is_dir():
        raise RuntimeError(f"Active Debug Chrome profile not found: {profile}")
    return profile


def copied_profiles(user_data_dir: Path) -> list[str]:
    manifest = load_manifest(user_data_dir)

    def sort_key(profile: str) -> tuple[int, int, str]:
        if profile == "Default":
            return (0, 0, profile)
        match = re.fullmatch(r"Profile (\d+)", profile)
        if match:
            return (1, int(match.group(1)), profile)
        return (2, 0, profile)

    return sorted(
        (
            profile
            for profile in manifest["profiles"]
            if (user_data_dir / profile).is_dir()
        ),
        key=sort_key,
    )


def select_profile(user_data_dir: Path, profile: str) -> None:
    with profile_store_lock(user_data_dir):
        manifest = load_manifest(user_data_dir)
        if profile not in manifest["profiles"] or not (user_data_dir / profile).is_dir():
            raise RuntimeError(f"Debug Chrome profile not found: {profile}")
        manifest["active_profile"] = profile
        manifest["version"] = MANIFEST_VERSION
        save_manifest(user_data_dir, manifest)


def profile_display_name(user_data_dir: Path, profile: str) -> str:
    manifest = load_manifest(user_data_dir)
    metadata = manifest["profiles"].get(profile)
    if isinstance(metadata, dict):
        name = metadata.get("name")
        if isinstance(name, str) and name.strip():
            return name.strip()
    local_state = user_data_dir / "Local State"
    try:
        payload: Any = json.loads(local_state.read_text(encoding="utf-8"))
        name = payload["profile"]["info_cache"][profile]["name"]
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, KeyError, TypeError):
        return profile
    if isinstance(name, str) and name.strip():
        return name.strip()
    return profile
