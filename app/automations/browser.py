from __future__ import annotations

import ipaddress
import socket
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit
from urllib.request import urlopen

from app.core.config import PROJECT_ROOT


SKILL_SCRIPTS = PROJECT_ROOT / ".agents" / "skills" / "chrome-cdp" / "scripts"
DEFAULT_PAGE_READ_TIMEOUT_MS = 30_000
MAX_PAGE_READ_TIMEOUT_MS = 120_000
DEFAULT_PAGE_CONTENT_CHARS = 64 * 1024
MAX_PAGE_CONTENT_CHARS = 256 * 1024
MAX_PAGE_TITLE_CHARS = 512


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


class DebugChromePageReadError(RuntimeError):
    """Raised when the shared browser cannot safely produce a page snapshot."""


def _load_skill_scripts() -> None:
    scripts = str(SKILL_SCRIPTS)
    if scripts not in sys.path:
        sys.path.insert(0, scripts)


def session_factory() -> Any:
    _load_skill_scripts()
    from playwright_session import session

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


async def read_debug_chrome_page(
    url: str,
    *,
    timeout_ms: int = DEFAULT_PAGE_READ_TIMEOUT_MS,
    max_content_chars: int = DEFAULT_PAGE_CONTENT_CHARS,
) -> DebugChromePageContent:
    """Read one public webpage through the currently running managed Debug Chrome.

    This intentionally owns only the temporary page it creates.  It neither starts,
    stops nor reconfigures Debug Chrome, and it exposes no browser context, cookies,
    storage or interaction controls to callers.
    """

    source_url = _validate_public_page_url(url.strip())
    if not 100 <= timeout_ms <= MAX_PAGE_READ_TIMEOUT_MS:
        raise DebugChromePageReadError("网页读取超时设置无效")
    if not 1 <= max_content_chars <= MAX_PAGE_CONTENT_CHARS:
        raise DebugChromePageReadError("网页正文长度限制无效")
    state, _, _ = debug_chrome_status()
    if state != "running":
        raise DebugChromePageReadError("Debug Chrome 未运行")

    page = None
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
            page = await chrome.context.new_page()
            await page.route("**/*", allow_public_request)
            await page.goto(
                source_url,
                timeout=timeout_ms,
                wait_until="domcontentloaded",
            )
            if blocked_navigation is not None:
                raise blocked_navigation
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
            truncated = bool(raw_content.get("truncated")) or normalized_truncated
            return DebugChromePageContent(
                source_url=source_url,
                final_url=final_url,
                title=title,
                content=content,
                truncated=truncated,
            )
    except DebugChromePageReadError:
        raise
    except Exception as exc:
        if blocked_navigation is not None:
            raise blocked_navigation from exc
        raise DebugChromePageReadError("网页内容读取失败") from exc
    finally:
        if page is not None and not page.is_closed():
            try:
                await page.close()
            except Exception:
                pass


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


def initialize_and_start_debug_chrome(
    profile_id: str,
    mode: str = "headless",
    *,
    supervisor_socket: Path | None = None,
):
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
    current = start_debug_chrome(mode, supervisor_socket=supervisor_socket)
    if current.state != "running" or current.profile_directory != profile_id:
        raise RuntimeError("Debug Chrome did not start with the selected profile")
    return current


def cleanup_interrupted_profile_copy() -> None:
    chrome_debug = _chrome_debug_module()
    _, copy_profile, profile_store = _profile_modules()
    target = chrome_debug.DEFAULT_USER_DATA_DIR
    with profile_store.profile_store_lock(target):
        copy_profile.cleanup_stale_staging(target)


def select_and_start_debug_chrome(
    profile_id: str,
    mode: str = "headless",
    *,
    supervisor_socket: Path | None = None,
):
    chrome_debug = _chrome_debug_module()
    _, _, profile_store = _profile_modules()
    profiles, _ = browser_profiles()
    selected = next((profile for profile in profiles if profile.id == profile_id), None)
    if selected is None or not selected.initialized:
        raise RuntimeError("Debug Chrome profile is not initialized")
    profile_store.select_profile(chrome_debug.DEFAULT_USER_DATA_DIR, profile_id)
    current = start_debug_chrome(mode, supervisor_socket=supervisor_socket)
    if current.state != "running" or current.profile_directory != profile_id:
        raise RuntimeError("Debug Chrome did not start with the selected profile")
    return current


def debug_chrome_status(
    *,
    supervisor_socket: Path | None = None,
) -> tuple[str, str, str | None]:
    try:
        if supervisor_socket is not None:
            from app.automations.chrome_supervisor import request

            current = request(supervisor_socket, "status")
        else:
            current = _chrome_debug_module().status()
    except Exception:
        return "unavailable", "无法检查状态", None
    if current.state == "running":
        mode = {
            "headed": "有界面",
            "headless": "无界面",
        }.get(current.mode)
        return "running", "已运行", mode
    if current.state == "stopped":
        return "stopped", "未启动", None
    return "invalid", "状态异常", None


def debug_chrome_websocket_url() -> str | None:
    """Return the validated browser CDP websocket without exposing credentials."""
    try:
        current = _chrome_debug_module().status()
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
        if supervisor_socket is not None:
            from app.automations.chrome_supervisor import request

            current = request(supervisor_socket, "status")
        else:
            current = _chrome_debug_module().status()
    except Exception:
        return None
    return current.profile_directory if current.state == "running" else None


def start_debug_chrome(
    mode: str = "headless",
    *,
    supervisor_socket: Path | None = None,
):
    if supervisor_socket is not None:
        from app.automations.chrome_supervisor import request

        return request(supervisor_socket, "start", mode=mode)
    return _chrome_debug_module().start(headless=mode == "headless")


def stop_debug_chrome(*, supervisor_socket: Path | None = None):
    if supervisor_socket is not None:
        from app.automations.chrome_supervisor import request

        return request(supervisor_socket, "stop")
    return _chrome_debug_module().stop()
