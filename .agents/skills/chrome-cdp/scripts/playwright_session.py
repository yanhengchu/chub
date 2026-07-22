#!/usr/bin/env python3
"""Connect Playwright to the Chrome instance owned by chrome-cdp."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any, AsyncIterator, Callable
from urllib.error import URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

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


def _create_page_target(endpoint: str) -> None:
    target = quote("about:blank", safe="")
    request = Request(f"{endpoint}/json/new?{target}", method="PUT")
    try:
        with urlopen(request, timeout=5) as response:
            payload = json.load(response)
    except (OSError, URLError, ValueError) as exc:
        raise RuntimeError(
            "Debug Chrome is running without a usable window and recovery failed"
        ) from exc
    if payload.get("type") != "page" or not payload.get("webSocketDebuggerUrl"):
        raise RuntimeError(
            "Debug Chrome did not create a usable recovery page target"
        )


def _ensure_page_target(endpoint: str) -> None:
    request = Request(f"{endpoint}/json/list", method="GET")
    try:
        with urlopen(request, timeout=5) as response:
            payload = json.load(response)
    except (OSError, URLError, ValueError) as exc:
        raise RuntimeError("Unable to inspect Debug Chrome page targets") from exc
    if not isinstance(payload, list):
        raise RuntimeError("Debug Chrome returned an invalid target list")
    if any(
        target.get("type") == "page" and target.get("webSocketDebuggerUrl")
        for target in payload
        if isinstance(target, dict)
    ):
        return
    _create_page_target(endpoint)


async def _connect_over_cdp(chromium: Any, current: DebugStatus, ensure_page: bool) -> Any:
    if ensure_page:
        await asyncio.to_thread(_ensure_page_target, current.endpoint)
    try:
        return await chromium.connect_over_cdp(current.endpoint)
    except Exception:
        if not ensure_page:
            raise
        # A user can close the last page between the target check and connection.
        # Recheck once and retry without depending on Playwright's error wording.
        await asyncio.to_thread(_ensure_page_target, current.endpoint)
        return await chromium.connect_over_cdp(current.endpoint)


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
        browser = await _connect_over_cdp(
            playwright.chromium,
            current,
            ensure_page,
        )
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
