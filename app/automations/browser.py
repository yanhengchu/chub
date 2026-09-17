from __future__ import annotations

import ipaddress
import socket
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit
from urllib.request import urlopen

from app.automations.debug_chrome import (
    chrome_debug,
    chrome_profiles,
    copy_profile,
    profile_store,
)
from app.automations.chrome_maintenance import ChromeLifecycleError, ChromeLifecycleUseCase
from app.automations.debug_chrome.playwright_session import session


DEFAULT_PAGE_READ_TIMEOUT_MS = 30_000
MAX_PAGE_READ_TIMEOUT_MS = 120_000
DEFAULT_PAGE_CONTENT_CHARS = 64 * 1024
MAX_PAGE_CONTENT_CHARS = 256 * 1024
MAX_PAGE_TITLE_CHARS = 512
MAX_PAGE_LINK_TEXT_CHARS = 512


@dataclass(frozen=True)
class BrowserProfileInfo:
    id: str
    name: str
    initialized: bool
    source_available: bool
    active: bool


@dataclass(frozen=True)
class DebugChromePageContent:
    """A bounded, read-only snapshot created from an owned temporary page."""

    source_url: str
    final_url: str
    title: str
    content: str
    truncated: bool


@dataclass(frozen=True)
class DebugChromeOpenedPage:
    """One fixed public page left open in the current headed Debug Chrome."""

    source_url: str
    final_url: str | None
    error: str | None


class DebugChromePageReadError(RuntimeError):
    """Raised when the managed browser cannot produce a bounded page snapshot."""


def session_factory() -> Any:
    return session


def _validate_public_page_url(value: str) -> str:
    try:
        parsed = urlsplit(value)
    except ValueError as exc:
        raise DebugChromePageReadError("网页地址无效") from exc
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
    ):
        raise DebugChromePageReadError("网页地址仅支持不含凭据的 HTTP(S) 地址")
    try:
        port = parsed.port
    except ValueError as exc:
        raise DebugChromePageReadError("网页地址端口无效") from exc
    if port is not None and port != {"http": 80, "https": 443}[parsed.scheme]:
        raise DebugChromePageReadError("网页地址仅支持标准 HTTP(S) 端口")
    _assert_public_host(parsed.hostname)
    return value


def _assert_public_host(host: str) -> None:
    normalized = host.rstrip(".")
    if normalized.lower() == "localhost":
        raise DebugChromePageReadError("网页地址不能访问本机或内网地址")
    try:
        addresses = {ipaddress.ip_address(normalized)}
    except ValueError:
        try:
            resolved = socket.getaddrinfo(normalized, None, type=socket.SOCK_STREAM)
        except OSError as exc:
            raise DebugChromePageReadError("无法解析网页地址") from exc
        try:
            addresses = {ipaddress.ip_address(item[4][0]) for item in resolved}
        except ValueError as exc:
            raise DebugChromePageReadError("网页地址解析结果无效") from exc
    if not addresses or any(not address.is_global for address in addresses):
        raise DebugChromePageReadError("网页地址不能访问本机或内网地址")


def _normalize_page_content(value: str, maximum: int) -> tuple[str, bool]:
    content = "\n".join(line.strip() for line in value.splitlines() if line.strip())
    if len(content) <= maximum:
        return content, False
    return content[:maximum], True


async def _page_snapshot(
    page: Any,
    source_url: str,
    *,
    max_content_chars: int,
) -> DebugChromePageContent:
    final_url = _validate_public_page_url(page.url)
    title = (await page.title()).strip()[:MAX_PAGE_TITLE_CHARS]
    raw_content = await page.evaluate(
        """(maximum) => {
            const content = document.body?.innerText || "";
            return {
                content: content.slice(0, maximum + 1),
                truncated: content.length > maximum,
            };
        }""",
        max_content_chars,
    )
    if not isinstance(raw_content, dict) or not isinstance(
        raw_content.get("content"), str
    ):
        raise DebugChromePageReadError("网页正文读取失败")
    content, normalized_truncated = _normalize_page_content(
        raw_content["content"],
        max_content_chars,
    )
    return DebugChromePageContent(
        source_url=source_url,
        final_url=final_url,
        title=title,
        content=content,
        truncated=bool(raw_content.get("truncated")) or normalized_truncated,
    )


def _track_owned_popups(page: Any) -> list[Any]:
    """Keep cleanup scoped to windows spawned by an owned temporary page."""

    popups: list[Any] = []

    def track(popup: Any) -> None:
        popups.append(popup)
        on = getattr(popup, "on", None)
        if callable(on):
            on("popup", track)

    on = getattr(page, "on", None)
    if callable(on):
        on("popup", track)
    return popups


async def _close_owned_pages(page: Any | None, popups: list[Any]) -> None:
    cleanup_failed = False
    for owned_page in reversed([*popups, page]):
        if owned_page is None:
            continue
        try:
            if not owned_page.is_closed():
                await owned_page.close()
        except Exception:
            cleanup_failed = True
            continue
        try:
            if not owned_page.is_closed():
                cleanup_failed = True
        except Exception:
            cleanup_failed = True
    if cleanup_failed:
        raise DebugChromePageReadError("网页临时页面未能关闭")


def _require_running_debug_chrome() -> str | None:
    state, message, mode = debug_chrome_status()
    if state != "running":
        raise DebugChromePageReadError(message)
    return mode


async def read_debug_chrome_page(
    url: str,
    *,
    timeout_ms: int = DEFAULT_PAGE_READ_TIMEOUT_MS,
    max_content_chars: int = DEFAULT_PAGE_CONTENT_CHARS,
) -> DebugChromePageContent:
    """Read one network-reachable webpage through managed Debug Chrome.

    This intentionally owns only the temporary page it creates.  It neither starts,
    stops nor reconfigures Debug Chrome. The page uses the selected Debug Chrome
    Profile, including its existing website sign-in state, but exposes only a bounded
    text snapshot to callers.
    """

    source_url = _validate_public_page_url(url.strip())
    if not 100 <= timeout_ms <= MAX_PAGE_READ_TIMEOUT_MS:
        raise DebugChromePageReadError("网页读取超时设置无效")
    if not 1 <= max_content_chars <= MAX_PAGE_CONTENT_CHARS:
        raise DebugChromePageReadError("网页正文长度限制无效")
    _require_running_debug_chrome()

    page = None
    popups: list[Any] = []
    blocked_navigation: DebugChromePageReadError | None = None

    async def allow_public_request(route) -> None:
        nonlocal blocked_navigation
        request = route.request
        parsed = urlsplit(request.url)
        if parsed.scheme not in {"http", "https"}:
            await route.continue_()
            return
        try:
            _validate_public_page_url(request.url)
        except DebugChromePageReadError as exc:
            if request.is_navigation_request():
                blocked_navigation = exc
            await route.abort()
            return
        await route.continue_()

    try:
        async with session_factory()(ensure_page=False, retry_connection=True) as chrome:
            try:
                page = await chrome.context.new_page()
                popups = _track_owned_popups(page)
                await page.route("**/*", allow_public_request)
                await page.goto(
                    source_url,
                    timeout=timeout_ms,
                    wait_until="domcontentloaded",
                )
                if blocked_navigation is not None:
                    raise blocked_navigation
                return await _page_snapshot(
                    page,
                    source_url,
                    max_content_chars=max_content_chars,
                )
            finally:
                await _close_owned_pages(page, popups)
    except DebugChromePageReadError:
        raise
    except Exception as exc:
        if blocked_navigation is not None:
            raise blocked_navigation from exc
        raise DebugChromePageReadError("网页内容读取失败") from exc


async def open_debug_chrome_pages(
    urls: tuple[str, ...],
    *,
    timeout_ms: int = DEFAULT_PAGE_READ_TIMEOUT_MS,
) -> tuple[DebugChromeOpenedPage, ...]:
    """Open fixed public pages in the current headed Debug Chrome and leave them open."""

    source_urls = tuple(_validate_public_page_url(url.strip()) for url in urls)
    if not source_urls:
        raise DebugChromePageReadError("没有可打开的网页地址")
    if not 100 <= timeout_ms <= MAX_PAGE_READ_TIMEOUT_MS:
        raise DebugChromePageReadError("网页读取超时设置无效")
    mode = _require_running_debug_chrome()
    if mode != "有界面":
        raise DebugChromePageReadError("Debug Chrome 未以有界面模式运行")

    opened: list[DebugChromeOpenedPage] = []
    async with session_factory()(ensure_page=False, retry_connection=True) as chrome:
        for source_url in source_urls:
            page = await chrome.context.new_page()
            blocked_navigation: DebugChromePageReadError | None = None

            async def allow_public_request(route) -> None:
                nonlocal blocked_navigation
                request = route.request
                parsed = urlsplit(request.url)
                if parsed.scheme not in {"http", "https"}:
                    await route.continue_()
                    return
                try:
                    _validate_public_page_url(request.url)
                except DebugChromePageReadError as exc:
                    if request.is_navigation_request():
                        blocked_navigation = exc
                    await route.abort()
                    return
                await route.continue_()

            try:
                await page.route("**/*", allow_public_request)
                await page.bring_to_front()
                await page.goto(
                    source_url,
                    timeout=timeout_ms,
                    wait_until="domcontentloaded",
                )
                if blocked_navigation is not None:
                    raise blocked_navigation
                final_url = _validate_public_page_url(page.url)
                opened.append(DebugChromeOpenedPage(source_url, final_url, None))
            except DebugChromePageReadError as exc:
                if not page.is_closed():
                    await page.close()
                opened.append(DebugChromeOpenedPage(source_url, None, str(exc)))
            except Exception:
                # Keep a browser-generated error page open for maintenance inspection.
                opened.append(DebugChromeOpenedPage(source_url, None, "页面未能完成打开"))
    return tuple(opened)


async def interact_debug_chrome_page(
    url: str,
    *,
    follow_link_text: str,
    timeout_ms: int = DEFAULT_PAGE_READ_TIMEOUT_MS,
    max_content_chars: int = DEFAULT_PAGE_CONTENT_CHARS,
) -> DebugChromePageContent:
    """Follow one exact visible link on a network-reachable page.

    This is intentionally limited to GET navigation. It reuses the selected Debug
    Chrome Profile's existing website sign-in state, but does not expose
    selectors, forms, scripts, downloads, storage, or existing browser pages.
    """

    source_url = _validate_public_page_url(url.strip())
    link_text = follow_link_text.strip()
    if not link_text or len(link_text) > MAX_PAGE_LINK_TEXT_CHARS:
        raise DebugChromePageReadError("网页链接文字无效")
    if not 100 <= timeout_ms <= MAX_PAGE_READ_TIMEOUT_MS:
        raise DebugChromePageReadError("网页读取超时设置无效")
    if not 1 <= max_content_chars <= MAX_PAGE_CONTENT_CHARS:
        raise DebugChromePageReadError("网页正文长度限制无效")
    _require_running_debug_chrome()

    page = None
    popups: list[Any] = []
    blocked_navigation: DebugChromePageReadError | None = None

    async def allow_public_request(route) -> None:
        nonlocal blocked_navigation
        request = route.request
        parsed = urlsplit(request.url)
        if parsed.scheme not in {"http", "https"}:
            await route.continue_()
            return
        try:
            _validate_public_page_url(request.url)
        except DebugChromePageReadError as exc:
            if request.is_navigation_request():
                blocked_navigation = exc
            await route.abort()
            return
        await route.continue_()

    try:
        async with session_factory()(ensure_page=False, retry_connection=True) as chrome:
            try:
                page = await chrome.context.new_page()
                popups = _track_owned_popups(page)
                await page.route("**/*", allow_public_request)
                await page.goto(
                    source_url,
                    timeout=timeout_ms,
                    wait_until="domcontentloaded",
                )
                if blocked_navigation is not None:
                    raise blocked_navigation
                link = await page.evaluate(
                    """(expected) => {
                        const matches = [...document.querySelectorAll("a[href]")]
                            .filter((anchor) => (anchor.innerText || anchor.textContent || "").trim() === expected);
                        if (matches.length !== 1) {
                            return { count: matches.length, href: null };
                        }
                        return { count: 1, href: matches[0].href };
                    }""",
                    link_text,
                )
                if (
                    not isinstance(link, dict)
                    or link.get("count") != 1
                    or not isinstance(link.get("href"), str)
                ):
                    raise DebugChromePageReadError("未找到唯一匹配的网页链接")
                target_url = _validate_public_page_url(link["href"])
                await page.goto(
                    target_url,
                    timeout=timeout_ms,
                    wait_until="domcontentloaded",
                )
                if blocked_navigation is not None:
                    raise blocked_navigation
                return await _page_snapshot(
                    page,
                    source_url,
                    max_content_chars=max_content_chars,
                )
            finally:
                await _close_owned_pages(page, popups)
    except DebugChromePageReadError:
        raise
    except Exception as exc:
        if blocked_navigation is not None:
            raise blocked_navigation from exc
        raise DebugChromePageReadError("网页链接操作失败") from exc


def _chrome_debug_module():
    return chrome_debug


def _profile_modules():
    return chrome_profiles, copy_profile, profile_store


def browser_profiles() -> tuple[list[BrowserProfileInfo], str | None]:
    profiles, source_error = ChromeLifecycleUseCase().browser_profiles()
    return [
        BrowserProfileInfo(
            id=item.profile_id,
            name=item.name,
            initialized=item.initialized,
            source_available=item.source_available,
            active=item.active,
        )
        for item in profiles
    ], source_error


def initialize_and_start_debug_chrome(
    profile_id: str,
    mode: str = "headless",
    *,
    supervisor_socket: Path | None = None,
):
    try:
        return ChromeLifecycleUseCase(
            supervisor_socket=supervisor_socket
        ).initialize_and_start(profile_id, mode)
    except ChromeLifecycleError as exc:
        raise RuntimeError(str(exc)) from exc


def cleanup_interrupted_profile_copy() -> None:
    ChromeLifecycleUseCase().cleanup_interrupted_profile_copy()


def select_and_start_debug_chrome(
    profile_id: str,
    mode: str = "headless",
    *,
    supervisor_socket: Path | None = None,
):
    try:
        return ChromeLifecycleUseCase(
            supervisor_socket=supervisor_socket
        ).select_and_start(profile_id, mode)
    except ChromeLifecycleError as exc:
        raise RuntimeError(str(exc)) from exc


def debug_chrome_status(
    *,
    supervisor_socket: Path | None = None,
) -> tuple[str, str, str | None]:
    return ChromeLifecycleUseCase(supervisor_socket=supervisor_socket).public_status()


def debug_chrome_websocket_url() -> str | None:
    """Return the validated browser CDP websocket without exposing credentials."""
    try:
        current = ChromeLifecycleUseCase().require_running()
        if current.state != "running":
            return None
        endpoint = urlsplit(current.endpoint)
        if endpoint.scheme != "http" or endpoint.hostname not in {"127.0.0.1", "::1"}:
            return None
        with urlopen(f"{current.endpoint}/json/version", timeout=3) as response:
            import json

            payload = json.load(response)
        websocket_url = payload.get("webSocketDebuggerUrl")
        parsed = urlsplit(websocket_url) if isinstance(websocket_url, str) else None
        if (
            parsed is None
            or parsed.scheme != "ws"
            or parsed.hostname != endpoint.hostname
            or parsed.port != endpoint.port
            or not parsed.path.startswith("/devtools/browser/")
        ):
            return None
        return websocket_url
    except Exception:
        return None


def current_debug_chrome_profile(
    *,
    supervisor_socket: Path | None = None,
) -> str | None:
    try:
        return ChromeLifecycleUseCase(
            supervisor_socket=supervisor_socket
        ).current_profile()
    except ChromeLifecycleError:
        return None


def start_debug_chrome(
    mode: str = "headless",
    *,
    supervisor_socket: Path | None = None,
):
    try:
        return ChromeLifecycleUseCase(supervisor_socket=supervisor_socket).start(mode)
    except ChromeLifecycleError as exc:
        raise RuntimeError(str(exc)) from exc


def stop_debug_chrome(*, supervisor_socket: Path | None = None):
    try:
        return ChromeLifecycleUseCase(supervisor_socket=supervisor_socket).stop()
    except ChromeLifecycleError as exc:
        raise RuntimeError(str(exc)) from exc
