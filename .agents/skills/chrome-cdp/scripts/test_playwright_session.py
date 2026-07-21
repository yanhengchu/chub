from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import playwright_session
from chrome_debug import DebugStatus


class FakeContext:
    def __init__(self, pages: list[object] | None = None) -> None:
        self.pages = pages or []
        self.created_page = object()

    async def new_page(self) -> object:
        self.pages.append(self.created_page)
        return self.created_page


class FakeBrowser:
    def __init__(self, context: FakeContext) -> None:
        self.contexts = [context]
        self.closed = False

    async def close(self) -> None:
        self.closed = True


class FakeChromium:
    def __init__(self, browser: FakeBrowser) -> None:
        self.browser = browser
        self.endpoints: list[str] = []

    async def connect_over_cdp(self, endpoint: str) -> FakeBrowser:
        self.endpoints.append(endpoint)
        return self.browser


class FailingChromium:
    async def connect_over_cdp(self, _endpoint: str) -> FakeBrowser:
        raise RuntimeError("connection failed")


class FakePlaywright:
    def __init__(self, browser: FakeBrowser) -> None:
        self.chromium = FakeChromium(browser)
        self.stopped = False

    async def stop(self) -> None:
        self.stopped = True


class FakeManager:
    def __init__(self, playwright: FakePlaywright) -> None:
        self.playwright = playwright

    async def start(self) -> FakePlaywright:
        return self.playwright


def running_status(root: Path, state: str = "running") -> DebugStatus:
    return DebugStatus(
        state=state,
        mode="headed" if state == "running" else None,
        endpoint="http://127.0.0.1:9222",
        user_data_dir=str(root),
        profile_directory="Default",
        process_ids=[123] if state != "stopped" else [],
    )


class PlaywrightSessionTest(unittest.IsolatedAsyncioTestCase):
    async def test_reuses_existing_page_and_disconnects(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            existing_page = object()
            context = FakeContext([existing_page])
            browser = FakeBrowser(context)
            playwright = FakePlaywright(browser)
            with patch(
                "playwright_session.status", return_value=running_status(root)
            ):
                async with playwright_session.session(
                    root,
                    _playwright_factory=lambda: FakeManager(playwright),
                ) as active:
                    self.assertIs(active.page, existing_page)
                    self.assertEqual(active.pages, [existing_page])
                    self.assertFalse(browser.closed)

            self.assertTrue(browser.closed)
            self.assertTrue(playwright.stopped)
            self.assertEqual(
                playwright.chromium.endpoints,
                ["http://127.0.0.1:9222"],
            )

    async def test_creates_page_when_context_is_empty(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            context = FakeContext()
            browser = FakeBrowser(context)
            playwright = FakePlaywright(browser)
            with patch(
                "playwright_session.status", return_value=running_status(root)
            ):
                async with playwright_session.session(
                    root,
                    _playwright_factory=lambda: FakeManager(playwright),
                ) as active:
                    self.assertIs(active.page, context.created_page)

    async def test_cleanup_runs_when_caller_fails(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            browser = FakeBrowser(FakeContext([object()]))
            playwright = FakePlaywright(browser)
            with patch(
                "playwright_session.status", return_value=running_status(root)
            ):
                with self.assertRaisesRegex(RuntimeError, "caller failed"):
                    async with playwright_session.session(
                        root,
                        _playwright_factory=lambda: FakeManager(playwright),
                    ):
                        raise RuntimeError("caller failed")

            self.assertTrue(browser.closed)
            self.assertTrue(playwright.stopped)

    async def test_playwright_stops_when_connection_fails(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            playwright = FakePlaywright(FakeBrowser(FakeContext()))
            playwright.chromium = FailingChromium()
            with patch(
                "playwright_session.status", return_value=running_status(root)
            ):
                with self.assertRaisesRegex(RuntimeError, "connection failed"):
                    async with playwright_session.session(
                        root,
                        _playwright_factory=lambda: FakeManager(playwright),
                    ):
                        pass

            self.assertTrue(playwright.stopped)

    async def test_refuses_stopped_or_broken_debug_chrome(self) -> None:
        for state in ("stopped", "broken"):
            with patch(
                "playwright_session.status",
                return_value=running_status(Path("/tmp/debug"), state),
            ):
                with self.assertRaises(RuntimeError):
                    async with playwright_session.session(
                        _playwright_factory=Mock()
                    ):
                        pass

    def test_missing_playwright_has_actionable_error(self) -> None:
        with patch.dict(
            "sys.modules", {"playwright": None, "playwright.async_api": None}
        ):
            with self.assertRaisesRegex(RuntimeError, "Playwright is not installed"):
                playwright_session.playwright_factory()
