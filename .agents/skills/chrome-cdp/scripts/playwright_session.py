#!/usr/bin/env python3
"""Connect Playwright to the Chrome instance owned by chrome-cdp."""

from __future__ import annotations

from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, AsyncIterator, Callable

try:
    from .chrome_debug import DEFAULT_USER_DATA_DIR, DebugStatus, status
except ImportError:
    from chrome_debug import DEFAULT_USER_DATA_DIR, DebugStatus, status


@dataclass(frozen=True)
class ChromeSession:
    browser: Any
    context: Any
    page: Any

    @property
    def pages(self) -> list[Any]:
        return list(self.context.pages)


def require_running_debug_chrome(
    user_data_dir: Path = DEFAULT_USER_DATA_DIR,
) -> DebugStatus:
    current = status(user_data_dir)
    if current.state == "stopped":
        raise RuntimeError(
            "Debug Chrome is not running; start it with chrome_debug.py start"
        )
    if current.state != "running":
        raise RuntimeError(
            "Debug Chrome ownership or CDP state is invalid; check chrome_debug.py status"
        )
    return current


def playwright_factory() -> Callable[[], Any]:
    try:
        from playwright.async_api import async_playwright
    except ImportError as exc:
        raise RuntimeError(
            "Playwright is not installed; install the chrome-cdp requirements first"
        ) from exc
    return async_playwright


@asynccontextmanager
async def session(
    user_data_dir: Path = DEFAULT_USER_DATA_DIR,
    *,
    ensure_page: bool = True,
    _playwright_factory: Callable[[], Any] | None = None,
) -> AsyncIterator[ChromeSession]:
    current = require_running_debug_chrome(user_data_dir)
    factory = _playwright_factory or playwright_factory()
    playwright = await factory().start()
    browser = None
    try:
        browser = await playwright.chromium.connect_over_cdp(current.endpoint)
        verified = require_running_debug_chrome(user_data_dir)
        if verified.endpoint != current.endpoint:
            raise RuntimeError("Debug Chrome CDP endpoint changed while connecting")
        if not browser.contexts:
            raise RuntimeError("Debug Chrome did not expose a browser context")
        context = browser.contexts[0]
        pages = list(context.pages)
        if pages:
            page = pages[0]
        elif ensure_page:
            page = await context.new_page()
        else:
            page = None
        yield ChromeSession(browser=browser, context=context, page=page)
    finally:
        try:
            if browser is not None:
                await browser.close()
        finally:
            await playwright.stop()
