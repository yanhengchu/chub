from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any
from urllib.parse import parse_qs, urlsplit, urlunsplit
from zoneinfo import ZoneInfo

from app.ai_usage.models import AiFiveHourUsage, AiTodayUsage, AiWeeklyUsage
from app.automations.browser import debug_chrome_status, session_factory
from app.automations.lock import LockBusy, file_lock
from app.codex.usage_settings import CodexUsageSettings
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

    def __init__(
        self,
        config: CodexUsageSettings,
        automations: AutomationsConfig,
    ) -> None:
        self._config = config
        self._automations = automations

    def collect(self, *, timeout_seconds: float) -> ProviderBrowserCollection:
        if self._config.sub2api.base_url is None:
            raise ProviderBrowserUnavailable("sub2api_not_configured")
        state, _, _ = debug_chrome_status()
        if state != "running":
            raise ProviderBrowserUnavailable("debug_chrome_not_running")

        deadline = time.monotonic() + max(0.1, timeout_seconds)
        lock_path = self._automations.runtime_dir / "locks" / "debug-chrome.lock"
        try:
            with file_lock(lock_path, max(0.0, deadline - time.monotonic())):
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise ProviderBrowserUnavailable("browser_lock_timeout")
                payloads = asyncio.run(self._capture_responses(remaining))
        except LockBusy as exc:
            raise ProviderBrowserUnavailable("browser_lock_timeout") from exc
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

    async def _capture_responses(self, timeout_seconds: float) -> _ProviderPayloads:
        page_url = self._page_url("/subscriptions")
        dashboard_url = self._page_url(self.DASHBOARD_PATH)
        session = session_factory()
        async with session(ensure_page=True) as chrome:
            subscription_task = asyncio.create_task(
                self._capture_page_payload(
                    chrome.context,
                    page_url=page_url,
                    expected_page_path="/subscriptions",
                    response_matcher=self._matches_usage_response,
                    timeout_seconds=timeout_seconds,
                )
            )
            stats_task = asyncio.create_task(
                self._capture_optional_stats(
                    chrome.context,
                    page_url=dashboard_url,
                    timeout_seconds=min(
                        timeout_seconds,
                        self.OPTIONAL_STATS_TIMEOUT_SECONDS,
                    ),
                )
            )
            tasks = (subscription_task, stats_task)
            try:
                subscription = await subscription_task
                stats = await stats_task
                return _ProviderPayloads(subscription=subscription, stats=stats)
            finally:
                for task in tasks:
                    if not task.done():
                        task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)

    async def _capture_optional_stats(
        self,
        context: Any,
        *,
        page_url: str,
        timeout_seconds: float,
    ) -> object | None:
        try:
            return await self._capture_page_payload(
                context,
                page_url=page_url,
                expected_page_path=self.DASHBOARD_PATH,
                response_matcher=self._matches_stats_response,
                timeout_seconds=timeout_seconds,
            )
        except ProviderBrowserUnavailable as exc:
            LOGGER.info("AI provider token collection unavailable: %s", exc)
            return None

    async def _capture_page_payload(
        self,
        context: Any,
        *,
        page_url: str,
        expected_page_path: str,
        response_matcher: Any,
        timeout_seconds: float,
    ) -> object:
        async def capture() -> bytes:
            page = await context.new_page()
            try:
                timeout_ms = max(1, int(timeout_seconds * 1000))
                response_ready = asyncio.get_running_loop().create_future()

                def capture_response(response: Any) -> None:
                    if not response_ready.done() and response_matcher(response):
                        response_ready.set_result(response)

                page.on("response", capture_response)
                await page.goto(
                    page_url,
                    wait_until="domcontentloaded",
                    timeout=timeout_ms,
                )
                while not response_ready.done():
                    if not self._is_expected_page(page.url, expected_page_path):
                        raise ProviderBrowserUnavailable("provider_login_unavailable")
                    await asyncio.sleep(0.05)
                response = response_ready.result()
                if not 200 <= response.status < 300:
                    raise ProviderBrowserUnavailable("provider_response_failed")
                content_type = response.headers.get("content-type", "").lower()
                if "json" not in content_type:
                    raise ProviderBrowserUnavailable("provider_response_invalid")
                if not self._is_expected_page(page.url, expected_page_path):
                    raise ProviderBrowserUnavailable("provider_login_unavailable")
                length = response.headers.get("content-length")
                if length and int(length) > self.MAX_RESPONSE_BYTES:
                    raise ProviderBrowserUnavailable("provider_response_too_large")
                body = await response.body()
                if len(body) > self.MAX_RESPONSE_BYTES:
                    raise ProviderBrowserUnavailable("provider_response_too_large")
                return body
            finally:
                await page.close()

        try:
            body = await asyncio.wait_for(capture(), timeout=timeout_seconds)
            return json.loads(body.decode("utf-8"), parse_float=Decimal)
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
        base_url = self._config.sub2api.base_url
        if base_url is None:
            return False
        target = urlsplit(base_url)
        candidate = urlsplit(response.url)
        if self._origin(candidate) != self._origin(target):
            return False
        if candidate.path != path:
            return False
        return parse_qs(candidate.query, keep_blank_values=True) == {
            "timezone": [self._config.timezone]
        }

    def _is_subscription_page(self, value: str) -> bool:
        return self._is_expected_page(value, "/subscriptions")

    def _is_expected_page(self, value: str, path: str) -> bool:
        expected = urlsplit(self._config.sub2api.base_url or "")
        actual = urlsplit(value)
        return (
            self._origin(actual) == self._origin(expected)
            and actual.path.rstrip("/") == path
        )

    def _page_url(self, path: str) -> str:
        target = urlsplit(self._config.sub2api.base_url or "")
        return urlunsplit((target.scheme, target.netloc, path, "", ""))

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
        subscription_id = self._config.sub2api.subscription_id
        if subscription_id is not None:
            matches = [item for item in matches if item[0]["id"] == subscription_id]
        if len(matches) != 1:
            if subscription_id is None and matches:
                value, group = matches[0]
            else:
                raise ProviderBrowserUnavailable("sub2api_subscription_not_found")
        else:
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
