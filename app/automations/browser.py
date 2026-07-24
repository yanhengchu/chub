from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.core.config import PROJECT_ROOT


SKILL_SCRIPTS = PROJECT_ROOT / ".agents" / "skills" / "chrome-cdp" / "scripts"


@dataclass(frozen=True)
class BrowserProfileInfo:
    id: str
    name: str
    initialized: bool
    source_available: bool
    active: bool


def _load_skill_scripts() -> None:
    scripts = str(SKILL_SCRIPTS)
    if scripts not in sys.path:
        sys.path.insert(0, scripts)


def session_factory() -> Any:
    _load_skill_scripts()
    from playwright_session import session

    return session


def _chrome_debug_module():
    _load_skill_scripts()
    import chrome_debug

    return chrome_debug


def _profile_modules():
    _load_skill_scripts()
    import chrome_profiles
    import copy_profile
    import profile_store

    return chrome_profiles, copy_profile, profile_store


def browser_profiles() -> tuple[list[BrowserProfileInfo], str | None]:
    chrome_debug = _chrome_debug_module()
    chrome_profiles, _, profile_store = _profile_modules()
    target = chrome_debug.DEFAULT_USER_DATA_DIR
    source_error = None
    try:
        source = {
            profile.directory: profile
            for profile in chrome_profiles.list_profiles()
        }
    except (OSError, RuntimeError):
        source = {}
        source_error = "无法读取默认 Chrome 用户"

    try:
        initialized = set(profile_store.copied_profiles(target))
        active = profile_store.active_profile(target)
    except (OSError, RuntimeError):
        initialized = set()
        active = None

    profile_ids = set(source) | initialized
    profiles = []
    for profile_id in sorted(profile_ids, key=chrome_profiles.profile_sort_key):
        source_profile = source.get(profile_id)
        if source_profile is not None:
            name = source_profile.name
        else:
            try:
                name = profile_store.profile_display_name(target, profile_id)
            except (OSError, RuntimeError):
                name = profile_id
        profiles.append(
            BrowserProfileInfo(
                id=profile_id,
                name=name,
                initialized=profile_id in initialized,
                source_available=source_profile is not None,
                active=profile_id == active,
            )
        )
    return profiles, source_error


def initialize_and_start_debug_chrome(profile_id: str, mode: str = "headed"):
    chrome_debug = _chrome_debug_module()
    chrome_profiles, copy_profile, profile_store = _profile_modules()
    profiles, _ = browser_profiles()
    selected = next((profile for profile in profiles if profile.id == profile_id), None)
    if selected is None:
        raise RuntimeError("Chrome profile is not available")
    if not selected.initialized:
        if not selected.source_available:
            raise RuntimeError("Chrome profile source is not available")
        copy_profile.copy_profile(
            profile_id,
            target=chrome_debug.DEFAULT_USER_DATA_DIR,
            close_running=False,
        )
    profile_store.select_profile(chrome_debug.DEFAULT_USER_DATA_DIR, profile_id)
    current = chrome_debug.start(headless=mode == "headless")
    if current.state != "running" or current.profile_directory != profile_id:
        raise RuntimeError("Debug Chrome did not start with the selected profile")
    return current


def cleanup_interrupted_profile_copy() -> None:
    chrome_debug = _chrome_debug_module()
    _, copy_profile, profile_store = _profile_modules()
    target = chrome_debug.DEFAULT_USER_DATA_DIR
    with profile_store.profile_store_lock(target):
        copy_profile.cleanup_stale_staging(target)


def select_and_start_debug_chrome(profile_id: str, mode: str = "headed"):
    chrome_debug = _chrome_debug_module()
    _, _, profile_store = _profile_modules()
    profiles, _ = browser_profiles()
    selected = next((profile for profile in profiles if profile.id == profile_id), None)
    if selected is None or not selected.initialized:
        raise RuntimeError("Debug Chrome profile is not initialized")
    profile_store.select_profile(chrome_debug.DEFAULT_USER_DATA_DIR, profile_id)
    current = chrome_debug.start(headless=mode == "headless")
    if current.state != "running" or current.profile_directory != profile_id:
        raise RuntimeError("Debug Chrome did not start with the selected profile")
    return current


def debug_chrome_status() -> tuple[str, str, str | None]:
    try:
        current = _chrome_debug_module().status()
    except Exception:
        return "unavailable", "无法检查 Debug Chrome 状态", None
    if current.state == "running":
        mode = {
            "headed": "有界面",
            "headless": "无界面",
        }.get(current.mode)
        return "running", "Debug Chrome 已运行", mode
    if current.state == "stopped":
        return "stopped", "Debug Chrome 未启动", None
    return "invalid", "Debug Chrome 状态异常", None


def current_debug_chrome_profile() -> str | None:
    try:
        current = _chrome_debug_module().status()
    except Exception:
        return None
    return current.profile_directory if current.state == "running" else None


def start_debug_chrome(mode: str = "headed"):
    return _chrome_debug_module().start(headless=mode == "headless")


def stop_debug_chrome():
    return _chrome_debug_module().stop()
