from __future__ import annotations

import tempfile
import unittest
from io import BytesIO
from pathlib import Path
from unittest.mock import Mock, patch

import playwright_session
from chrome_debug import DebugStatus


ORIGINAL_ENSURE_PAGE_TARGET = playwright_session._ensure_page_target


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


class RecoveringChromium:
    def __init__(self, browser: FakeBrowser) -> None:
        self.browser = browser
        self.calls = 0

    async def connect_over_cdp(self, _endpoint: str) -> FakeBrowser:
        self.calls += 1
        if self.calls == 1:
            raise RuntimeError(
                "Protocol error (Browser.setDownloadBehavior): "
                "Browser context management is not supported"
            )
        return self.browser


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
    async def asyncSetUp(self) -> None:
        self.ensure_target_patch = patch("playwright_session._ensure_page_target")
        self.ensure_target = self.ensure_target_patch.start()

    async def asyncTearDown(self) -> None:
        self.ensure_target_patch.stop()

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

    async def test_recovers_closed_headed_window_when_page_is_allowed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            browser = FakeBrowser(FakeContext([object()]))
            playwright = FakePlaywright(browser)
            chromium = RecoveringChromium(browser)
            playwright.chromium = chromium
            self.ensure_target.reset_mock()
            with patch(
                "playwright_session.status",
                return_value=running_status(root),
            ):
                async with playwright_session.session(
                    root,
                    ensure_page=True,
                    _playwright_factory=lambda: FakeManager(playwright),
                ) as active:
                    self.assertIs(active.context, browser.contexts[0])

            self.assertEqual(self.ensure_target.call_count, 2)
            self.ensure_target.assert_called_with("http://127.0.0.1:9222")
            self.assertEqual(chromium.calls, 2)

    async def test_does_not_recover_closed_window_for_read_only_session(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            browser = FakeBrowser(FakeContext())
            playwright = FakePlaywright(browser)
            chromium = RecoveringChromium(browser)
            playwright.chromium = chromium
            self.ensure_target.reset_mock()
            with patch(
                "playwright_session.status",
                return_value=running_status(root),
            ):
                with self.assertRaisesRegex(
                    RuntimeError,
                    "Browser context management is not supported",
                ):
                    async with playwright_session.session(
                        root,
                        ensure_page=False,
                        _playwright_factory=lambda: FakeManager(playwright),
                    ):
                        pass

            self.ensure_target.assert_not_called()
            self.assertEqual(chromium.calls, 1)

    def test_ensure_page_target_creates_only_when_page_is_missing(self) -> None:
        no_targets = BytesIO(b"[]")
        existing_page = BytesIO(
            b'[{"type":"page","webSocketDebuggerUrl":"ws://target"}]'
        )
        with patch("playwright_session.urlopen", return_value=no_targets):
            with patch("playwright_session._create_page_target") as create_target:
                ORIGINAL_ENSURE_PAGE_TARGET("http://127.0.0.1:9222")
                create_target.assert_called_once_with("http://127.0.0.1:9222")
        with patch("playwright_session.urlopen", return_value=existing_page):
            with patch("playwright_session._create_page_target") as create_target:
                ORIGINAL_ENSURE_PAGE_TARGET("http://127.0.0.1:9222")
                create_target.assert_not_called()

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
