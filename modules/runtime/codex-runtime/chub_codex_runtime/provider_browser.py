from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any
from urllib.parse import parse_qs, urlencode, urlsplit, urlunsplit
from zoneinfo import ZoneInfo
import websockets

from app.ai_usage.models import AiFiveHourUsage, AiTodayUsage, AiWeeklyUsage
from app.automations.browser import (
    debug_chrome_status,
    debug_chrome_websocket_url,
    session_factory,
)
from .usage_settings import CodexUsageSettings
from app.core.config import AutomationsConfig


LOGGER = logging.getLogger("hub.ai_usage.provider_browser")


class ProviderBrowserUnavailable(Exception):
    pass


@dataclass(frozen=True)
class ProviderBrowserCollection:
    weekly: AiWeeklyUsage
    today: AiTodayUsage
    five_hour: AiFiveHourUsage | None = None
    subscription_id: int | None = None


@dataclass(frozen=True)
class _ProviderPayloads:
    subscription: object
    stats: object | None


class ProviderBrowserAdapter:
    MAX_RESPONSE_BYTES = 512 * 1024
    MAX_SUBSCRIPTIONS = 100
    OPTIONAL_STATS_TIMEOUT_SECONDS = 4
    ACTIVE_PATH = "/api/v1/subscriptions/active"
    DASHBOARD_PATH = "/dashboard"
    STATS_PATH = "/api/v1/usage/dashboard/stats"
    LOGIN_PAGE_NAME = "chub-codex-provider-login"
    PAGE_REQUEST_SCRIPT = """
        async ({ url, maxBytes }) => {
            const response = await fetch(url, { credentials: "include" });
            const contentType = response.headers.get("content-type") || "";
            const contentLength = response.headers.get("content-length");
            if (contentLength && Number(contentLength) > maxBytes) {
                return {
                    url: response.url,
                    status: response.status,
                    contentType,
                    tooLarge: true,
                };
            }
            const body = await response.text();
            if (new TextEncoder().encode(body).length > maxBytes) {
                return {
                    url: response.url,
                    status: response.status,
                    contentType,
                    tooLarge: true,
                };
            }
            return {
                url: response.url,
                status: response.status,
                contentType,
                body,
            };
        }
    """

    def __init__(
        self,
        config: CodexUsageSettings,
        automations: AutomationsConfig,
    ) -> None:
        self._config = config
        self._automations = automations

    def collect(self, *, timeout_seconds: float) -> ProviderBrowserCollection:
        if self._config.provider_base_url is None:
            raise ProviderBrowserUnavailable("provider_base_url_unavailable")
        state, _, _ = debug_chrome_status()
        if state != "running":
            raise ProviderBrowserUnavailable("debug_chrome_not_running")

        deadline = time.monotonic() + max(0.1, timeout_seconds)
        try:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise ProviderBrowserUnavailable("browser_collection_timeout")
            payloads = asyncio.run(self._capture_responses(remaining))
        except ProviderBrowserUnavailable:
            raise
        except Exception as exc:
            raise ProviderBrowserUnavailable("browser_collection_failed") from exc
        today_tokens = None
        if payloads.stats is not None:
            try:
                today_tokens = self._parse_today_tokens(payloads.stats)
            except ProviderBrowserUnavailable as exc:
                LOGGER.info("AI provider token response unavailable: %s", exc)
        return self._parse_payload(
            payloads.subscription,
            today_tokens=today_tokens,
        )

    def open_login_page(self) -> None:
        if self._config.provider_base_url is None:
            raise ProviderBrowserUnavailable("provider_base_url_unavailable")
        state, _, _ = debug_chrome_status()
        if state != "running":
            raise ProviderBrowserUnavailable("debug_chrome_not_running")
        try:
            asyncio.run(self._open_login_page())
        except ProviderBrowserUnavailable:
            raise
        except Exception as exc:
            raise ProviderBrowserUnavailable("provider_login_page_failed") from exc

    async def _open_login_page(self) -> None:
        session = session_factory()
        async with session(ensure_page=True) as chrome:
            for page in chrome.context.pages:
                if page.is_closed():
                    continue
                try:
                    if await page.evaluate("window.name") == self.LOGIN_PAGE_NAME:
                        await page.bring_to_front()
                        return
                except Exception:
                    continue
            page = await chrome.context.new_page()
            try:
                await page.add_init_script(
                    f"window.name = {self.LOGIN_PAGE_NAME!r};"
                )
                await page.goto(
                    self._page_url("/subscriptions"),
                    wait_until="domcontentloaded",
                    timeout=30_000,
                )
                await page.bring_to_front()
            except Exception:
                if not page.is_closed():
                    await page.close()
                raise

    async def _capture_responses(self, timeout_seconds: float) -> _ProviderPayloads:
        return await self._capture_background_page_responses(timeout_seconds)

    async def _capture_background_page_responses(
        self,
        timeout_seconds: float,
    ) -> _ProviderPayloads:
        websocket_url = debug_chrome_websocket_url()
        if websocket_url is None:
            raise ProviderBrowserUnavailable("debug_chrome_unavailable")
        deadline = time.monotonic() + timeout_seconds
        async with websockets.connect(websocket_url, max_size=self.MAX_RESPONSE_BYTES * 2) as socket:
            sequence = 0
            backlog: list[dict[str, object]] = []

            async def request(
                method: str,
                params: dict[str, object] | None = None,
                session_id: str | None = None,
            ) -> dict[str, object]:
                nonlocal sequence
                sequence += 1
                request_id = sequence
                payload: dict[str, object] = {
                    "id": request_id,
                    "method": method,
                    "params": params or {},
                }
                if session_id is not None:
                    payload["sessionId"] = session_id
                await socket.send(json.dumps(payload))
                while True:
                    message = json.loads(await socket.recv())
                    if message.get("id") == request_id:
                        return message
                    if isinstance(message, dict):
                        backlog.append(message)

            created = await request(
                "Target.createTarget",
                {"url": "about:blank", "background": True, "focus": False},
            )
            target_id = (created.get("result") or {}).get("targetId")
            if not isinstance(target_id, str):
                raise ProviderBrowserUnavailable("provider_background_page_failed")
            try:
                attached = await request(
                    "Target.attachToTarget",
                    {"targetId": target_id, "flatten": True},
                )
                session_id = (attached.get("result") or {}).get("sessionId")
                if not isinstance(session_id, str):
                    raise ProviderBrowserUnavailable("provider_background_page_failed")
                for method, params in (
                    ("Network.enable", {}),
                    ("Page.enable", {}),
                    ("Emulation.setFocusEmulationEnabled", {"enabled": True}),
                    ("Page.setWebLifecycleState", {"state": "active"}),
                ):
                    result = await request(method, params, session_id)
                    if result.get("error"):
                        raise ProviderBrowserUnavailable("provider_background_page_failed")
                subscription = await self._capture_background_payload(
                    request,
                    socket,
                    backlog,
                    session_id=session_id,
                    page_url=self._page_url("/subscriptions"),
                    path=self.ACTIVE_PATH,
                    timeout_seconds=max(0.1, deadline - time.monotonic()),
                )
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return _ProviderPayloads(subscription=subscription, stats=None)
                try:
                    stats = await self._capture_background_payload(
                        request,
                        socket,
                        backlog,
                        session_id=session_id,
                        page_url=self._page_url(self.DASHBOARD_PATH),
                        path=self.STATS_PATH,
                        timeout_seconds=min(remaining, self.OPTIONAL_STATS_TIMEOUT_SECONDS),
                    )
                except ProviderBrowserUnavailable as exc:
                    LOGGER.info("AI provider token collection unavailable: %s", exc)
                    stats = None
                return _ProviderPayloads(subscription=subscription, stats=stats)
            finally:
                await request("Target.closeTarget", {"targetId": target_id})

    async def _capture_background_payload(
        self,
        request: Any,
        socket: Any,
        backlog: list[dict[str, object]],
        *,
        session_id: str,
        page_url: str,
        path: str,
        timeout_seconds: float,
    ) -> object:
        navigation = await request("Page.navigate", {"url": page_url}, session_id)
        if navigation.get("error"):
            raise ProviderBrowserUnavailable("provider_background_page_failed")
        deadline = time.monotonic() + timeout_seconds
        while time.monotonic() < deadline:
            try:
                message = (
                    backlog.pop(0)
                    if backlog
                    else json.loads(await asyncio.wait_for(socket.recv(), 0.1))
                )
            except TimeoutError:
                continue
            if (
                message.get("sessionId") != session_id
                or message.get("method") != "Network.responseReceived"
            ):
                continue
            params = message.get("params")
            if not isinstance(params, dict):
                continue
            response = params.get("response")
            request_id = params.get("requestId")
            if not isinstance(response, dict) or not isinstance(request_id, str):
                continue
            url = response.get("url")
            if not isinstance(url, str) or not self._matches_response_url(url, path):
                continue
            status = response.get("status")
            if status in {401, 403} or not 200 <= status < 300:
                raise ProviderBrowserUnavailable("provider_login_unavailable")
            headers = response.get("headers")
            content_type = (
                next(
                    (
                        value
                        for key, value in headers.items()
                        if isinstance(key, str) and key.lower() == "content-type"
                    ),
                    "",
                )
                if isinstance(headers, dict)
                else ""
            )
            if not isinstance(content_type, str) or "json" not in content_type.lower():
                raise ProviderBrowserUnavailable("provider_response_invalid")
            body_response = await request(
                "Network.getResponseBody", {"requestId": request_id}, session_id
            )
            body = ((body_response.get("result") or {}).get("body"))
            if not isinstance(body, str) or len(body.encode("utf-8")) > self.MAX_RESPONSE_BYTES:
                raise ProviderBrowserUnavailable("provider_response_too_large")
            return json.loads(body, parse_float=Decimal)
        raise ProviderBrowserUnavailable("provider_response_timeout")

    async def _capture_optional_stats(
        self,
        context: Any,
        *,
        path: str,
        timeout_seconds: float,
    ) -> object | None:
        try:
            return await self._capture_request_payload(
                context,
                path=path,
                timeout_seconds=timeout_seconds,
            )
        except ProviderBrowserUnavailable as exc:
            LOGGER.info("AI provider token collection unavailable: %s", exc)
            return None

    async def _capture_request_payload(
        self,
        context: Any,
        *,
        path: str,
        timeout_seconds: float,
    ) -> object:
        try:
            page = self._find_provider_page(context)
            timeout_ms = max(1, int(timeout_seconds * 1000))
            response = await asyncio.wait_for(
                page.evaluate(
                    self.PAGE_REQUEST_SCRIPT,
                    {"url": self._api_url(path), "maxBytes": self.MAX_RESPONSE_BYTES},
                ),
                timeout=timeout_seconds,
            )
            if not isinstance(response, dict):
                raise ProviderBrowserUnavailable("provider_response_invalid")
            response_url = response.get("url")
            status = response.get("status")
            content_type = response.get("contentType")
            if not isinstance(response_url, str) or not self._matches_response_url(
                response_url, path
            ):
                raise ProviderBrowserUnavailable("provider_login_unavailable")
            if isinstance(status, bool) or not isinstance(status, int):
                raise ProviderBrowserUnavailable("provider_response_invalid")
            if not 200 <= status < 300:
                raise ProviderBrowserUnavailable("provider_response_failed")
            if not isinstance(content_type, str) or "json" not in content_type.lower():
                raise ProviderBrowserUnavailable("provider_response_invalid")
            if response.get("tooLarge") is True:
                raise ProviderBrowserUnavailable("provider_response_too_large")
            body = response.get("body")
            if not isinstance(body, str):
                raise ProviderBrowserUnavailable("provider_response_invalid")
            body_bytes = body.encode("utf-8")
            if len(body_bytes) > self.MAX_RESPONSE_BYTES:
                raise ProviderBrowserUnavailable("provider_response_too_large")
            return json.loads(body_bytes.decode("utf-8"), parse_float=Decimal)
        except ProviderBrowserUnavailable:
            raise
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
            raise ProviderBrowserUnavailable("provider_response_invalid") from exc
        except TimeoutError as exc:
            raise ProviderBrowserUnavailable("provider_response_timeout") from exc
        except Exception as exc:
            raise ProviderBrowserUnavailable("provider_page_failed") from exc

    def _matches_usage_response(self, response: Any) -> bool:
        return self._matches_response(response, self.ACTIVE_PATH)

    def _matches_stats_response(self, response: Any) -> bool:
        return self._matches_response(response, self.STATS_PATH)

    def _matches_response(self, response: Any, path: str) -> bool:
        return self._matches_response_url(response.url, path)

    def _matches_response_url(self, value: str, path: str) -> bool:
        base_url = self._config.provider_base_url
        if base_url is None:
            return False
        target = urlsplit(base_url)
        candidate = urlsplit(value)
        if self._origin(candidate) != self._origin(target):
            return False
        if candidate.path != path:
            return False
        return parse_qs(candidate.query, keep_blank_values=True) == {
            "timezone": [self._config.timezone]
        }

    def _find_provider_page(self, context: Any) -> Any:
        base_url = self._config.provider_base_url
        if base_url is None:
            raise ProviderBrowserUnavailable("provider_base_url_unavailable")
        expected = urlsplit(base_url)
        for page in context.pages:
            if page.is_closed():
                continue
            if self._origin(urlsplit(page.url)) == self._origin(expected):
                return page
        raise ProviderBrowserUnavailable("provider_browser_page_unavailable")

    def _page_url(self, path: str) -> str:
        target = urlsplit(self._config.provider_base_url or "")
        return urlunsplit((target.scheme, target.netloc, path, "", ""))

    def _api_url(self, path: str) -> str:
        return f"{self._page_url(path)}?{urlencode({'timezone': self._config.timezone})}"

    @staticmethod
    def _origin(value) -> tuple[str, str | None, int | None]:
        scheme = value.scheme.lower()
        port = value.port
        if port is None:
            port = 443 if scheme == "https" else 80 if scheme == "http" else None
        return scheme, value.hostname, port

    def _parse_payload(
        self,
        payload: object,
        *,
        today_tokens: int | None = None,
    ) -> ProviderBrowserCollection:
        if not isinstance(payload, dict):
            raise ProviderBrowserUnavailable("provider_response_invalid")
        values = payload.get("data")
        if not isinstance(values, list) or len(values) > self.MAX_SUBSCRIPTIONS:
            raise ProviderBrowserUnavailable("provider_response_invalid")
        matches = []
        for value in values:
            if not isinstance(value, dict):
                continue
            group = value.get("group")
            if (
                value.get("status") == "active"
                and isinstance(group, dict)
                and group.get("platform") == "openai"
                and isinstance(value.get("id"), int)
                and not isinstance(value.get("id"), bool)
            ):
                matches.append((value, group))
        if not matches:
            raise ProviderBrowserUnavailable("provider_subscription_not_found")
        value, group = matches[0]

        used = self._decimal(value.get("weekly_usage_usd"), minimum=Decimal("0"))
        limit = self._decimal(group.get("weekly_limit_usd"), minimum=Decimal("0.01"))
        daily = self._decimal(value.get("daily_usage_usd"), minimum=Decimal("0"))
        window_start = self._aware_datetime(value.get("weekly_window_start"))
        remaining = max(limit - used, Decimal("0"))
        remaining_percent = int(
            (remaining / limit * Decimal("100")).quantize(
                Decimal("1"),
                rounding=ROUND_HALF_UP,
            )
        )
        remaining_percent = max(0, min(100, remaining_percent))
        timezone = ZoneInfo(self._config.timezone)
        return ProviderBrowserCollection(
            weekly=AiWeeklyUsage(
                remaining_percent=remaining_percent,
                used_usd=used,
                remaining_usd=remaining,
                limit_usd=limit,
                resets_at=window_start + timedelta(days=7),
            ),
            today=AiTodayUsage(
                date=datetime.now(timezone).date(),
                used_usd=daily,
                tokens=today_tokens,
                tokens_scope="account" if today_tokens is not None else None,
            ),
            subscription_id=value["id"],
        )

    def _parse_today_tokens(self, payload: object) -> int:
        if not isinstance(payload, dict) or payload.get("code") != 0:
            raise ProviderBrowserUnavailable("provider_token_response_invalid")
        data = payload.get("data")
        if not isinstance(data, dict):
            raise ProviderBrowserUnavailable("provider_token_response_invalid")
        platforms = data.get("by_platform")
        if not isinstance(platforms, list) or len(platforms) > self.MAX_SUBSCRIPTIONS:
            raise ProviderBrowserUnavailable("provider_token_response_invalid")
        matches = [
            value
            for value in platforms
            if isinstance(value, dict)
                and value.get("platform") == "openai"
        ]
        if len(matches) != 1:
            raise ProviderBrowserUnavailable("provider_token_response_invalid")
        tokens = matches[0].get("today_tokens")
        if isinstance(tokens, bool) or not isinstance(tokens, int) or tokens < 0:
            raise ProviderBrowserUnavailable("provider_token_response_invalid")
        return tokens

    @staticmethod
    def _decimal(value: object, *, minimum: Decimal) -> Decimal:
        if isinstance(value, bool) or not isinstance(value, (int, float, str, Decimal)):
            raise ProviderBrowserUnavailable("provider_response_invalid")
        try:
            parsed = Decimal(str(value))
        except InvalidOperation as exc:
            raise ProviderBrowserUnavailable("provider_response_invalid") from exc
        if not parsed.is_finite() or parsed < minimum:
            raise ProviderBrowserUnavailable("provider_response_invalid")
        return parsed

    @staticmethod
    def _aware_datetime(value: object) -> datetime:
        if not isinstance(value, str) or len(value) > 64:
            raise ProviderBrowserUnavailable("provider_response_invalid")
        try:
            parsed = datetime.fromisoformat(value)
        except ValueError as exc:
            raise ProviderBrowserUnavailable("provider_response_invalid") from exc
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            raise ProviderBrowserUnavailable("provider_response_invalid")
        return parsed
