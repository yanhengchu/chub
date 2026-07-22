from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

from app.core.config import PROJECT_ROOT


SKILL_SCRIPTS = PROJECT_ROOT / ".agents" / "skills" / "chrome-cdp" / "scripts"


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


def debug_chrome_status() -> tuple[str, str, str | None]:
    try:
        current = _chrome_debug_module().status()
    except Exception:
        return "unavailable", "无法检查 Debug Chrome 状态", None
    if current.state == "running":
        mode = {
            "headed": "有界面模式",
            "headless": "无界面模式",
        }.get(current.mode)
        return "running", "Debug Chrome 已运行", mode
    if current.state == "stopped":
        return "stopped", "Debug Chrome 未启动", None
    return "invalid", "Debug Chrome 状态异常", None


def start_debug_chrome():
    return _chrome_debug_module().start(headless=False)


def stop_debug_chrome():
    return _chrome_debug_module().stop()
