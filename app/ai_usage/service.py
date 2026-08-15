from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP
from zoneinfo import ZoneInfo

from app.ai_usage.models import (
    AiTodayUsage,
    AiUsageData,
    AiUsageDisplay,
    AiUsageSource,
    AiWeeklyUsage,
)
from app.ai_usage.provider_browser import (
    ProviderBrowserAdapter,
    ProviderBrowserUnavailable,
)
from app.codex.rate_limits import CodexAccountCollection, CodexRateLimitService
from app.core.config import Settings


LOGGER = logging.getLogger("hub.ai_usage")
WEEKLY_WINDOW_MINUTES = 7 * 24 * 60


@dataclass(frozen=True)
class _CollectionOutcome:
    source: AiUsageSource | None
    data: AiUsageData | None
    message: str
    identity_key: str | None = None


class AiUsageService:
    CACHE_SECONDS = 5 * 60
    REFRESH_TIMEOUT_SECONDS = 8

    def __init__(
        self,
        settings: Settings,
        codex_reader: CodexRateLimitService,
        provider_browser: ProviderBrowserAdapter | None = None,
    ) -> None:
        self._settings = settings
        self._codex_reader = codex_reader
        self._provider_browser = provider_browser or ProviderBrowserAdapter(
            settings.ai_usage,
            settings.automations,
        )
        self._lock = threading.Lock()
        self._cached: AiUsageData | None = None
        self._cached_at = 0.0
        self._cached_identity_key: str | None = None
        self._attempt_generation = 0
        self._last_attempt: AiUsageData | None = None

    def read(self, *, force: bool = False) -> AiUsageData:
        observed_generation = self._attempt_generation
        with self._lock:
            if (
                self._attempt_generation != observed_generation
                and self._last_attempt is not None
            ):
                return self._last_attempt
            if not force and self._cache_fresh():
                assert self._cached is not None
                return self._cached

            deadline = time.monotonic() + self.REFRESH_TIMEOUT_SECONDS
            outcome = self._collect(deadline)
            if outcome.data is not None:
                result = outcome.data
                self._cached = result
                self._cached_at = time.monotonic()
                self._cached_identity_key = outcome.identity_key
            else:
                result = self._stale_or_unavailable(outcome)
            self._attempt_generation += 1
            self._last_attempt = result
            return result

    def _cache_fresh(self) -> bool:
        return bool(
            self._cached is not None
            and time.monotonic() - self._cached_at < self.CACHE_SECONDS
        )

    def _collect(self, deadline: float) -> _CollectionOutcome:
        remaining = max(0.1, deadline - time.monotonic())
        account = self._codex_reader.collect_ai_account_status(
            timeout_seconds=remaining
        )
        if account.auth_type == "chatgpt":
            data = self._from_account(account)
            return _CollectionOutcome(
                "account_login",
                data,
                "AI 账号额度暂不可用。",
                account.identity_key,
            )
        if account.auth_type == "apiKey":
            try:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise ProviderBrowserUnavailable("refresh_timeout")
                collected = self._provider_browser.collect(timeout_seconds=remaining)
                return _CollectionOutcome(
                    "provider_api",
                    self._available(
                        source="provider_api",
                        weekly=collected.weekly,
                        today=collected.today,
                    ),
                    "AI API 额度暂不可用。",
                    self._provider_identity_key(),
                )
            except ProviderBrowserUnavailable as exc:
                LOGGER.info("AI provider usage collection unavailable: %s", exc)
                return _CollectionOutcome(
                    "provider_api",
                    None,
                    "AI API 额度暂不可用。",
                    self._provider_identity_key(),
                )
        LOGGER.info("AI authentication type is unavailable: %s", account.message)
        return _CollectionOutcome(None, None, "AI 认证状态暂不可用。")

    def _from_account(self, account: CodexAccountCollection) -> AiUsageData | None:
        quota = account.quota
        if quota is None or quota.status != "available":
            return None
        weekly_window = next(
            (
                window
                for window in quota.windows
                if window.window_duration_minutes == WEEKLY_WINDOW_MINUTES
            ),
            None,
        )
        if weekly_window is None:
            return None

        timezone = ZoneInfo(self._settings.ai_usage.timezone)
        today_date = datetime.now(timezone).date()
        tokens = None
        usage = account.usage
        if usage is not None and usage.status == "available":
            bucket = next(
                (item for item in usage.daily_usage if item.start_date == today_date),
                None,
            )
            if bucket is not None:
                tokens = bucket.tokens
        return self._available(
            source="account_login",
            weekly=AiWeeklyUsage(
                remaining_percent=weekly_window.remaining_percent,
                resets_at=weekly_window.resets_at,
            ),
            today=AiTodayUsage(date=today_date, tokens=tokens),
        )

    def _available(
        self,
        *,
        source: AiUsageSource,
        weekly: AiWeeklyUsage,
        today: AiTodayUsage,
    ) -> AiUsageData:
        checked_at = datetime.now(ZoneInfo(self._settings.ai_usage.timezone))
        data = AiUsageData(
            status="available",
            provider=self._settings.ai_usage.provider,
            source=source,
            timezone=self._settings.ai_usage.timezone,
            checked_at=checked_at,
            weekly=weekly,
            today=today,
        )
        return data.model_copy(update={"display": self._display(data)})

    def _stale_or_unavailable(self, outcome: _CollectionOutcome) -> AiUsageData:
        cached = self._cached
        if (
            cached is not None
            and cached.source == outcome.source
            and cached.weekly
            and outcome.identity_key is not None
            and outcome.identity_key == self._cached_identity_key
        ):
            timezone = ZoneInfo(cached.timezone)
            now = datetime.now(timezone)
            if cached.weekly.resets_at.astimezone(timezone) > now:
                today = cached.today
                if today is not None and today.date != now.date():
                    today = AiTodayUsage(date=now.date())
                stale = cached.model_copy(
                    update={
                        "stale": True,
                        "message": outcome.message,
                        "today": today,
                    }
                )
                return stale.model_copy(update={"display": self._display(stale)})
        return AiUsageData(
            status="unavailable",
            provider=self._settings.ai_usage.provider,
            source=outcome.source,
            timezone=self._settings.ai_usage.timezone,
            message=outcome.message,
        )

    def _provider_identity_key(self) -> str | None:
        subscription_id = self._settings.ai_usage.provider_api.subscription_id
        if subscription_id is None:
            return None
        return f"{self._settings.ai_usage.provider}:{subscription_id}"

    @classmethod
    def _display(cls, data: AiUsageData) -> AiUsageDisplay:
        weekly = data.weekly
        if data.status != "available" or weekly is None:
            return AiUsageDisplay()

        if weekly.remaining_usd is not None and weekly.limit_usd is not None:
            weekly_text = (
                f"Weekly ${cls._money(weekly.remaining_usd, fixed=True)} left "
                f"({weekly.remaining_percent}%)"
            )
            limit_text = f"Limit ${cls._money(weekly.limit_usd, fixed=False)}"
        else:
            weekly_text = f"Weekly {weekly.remaining_percent}% left"
            limit_text = None

        today_parts = []
        if data.today is not None:
            if data.today.used_usd is not None:
                today_parts.append(f"${cls._money(data.today.used_usd, fixed=True)} used")
            if data.today.tokens is not None:
                today_parts.append(f"{cls.compact_tokens(data.today.tokens)} tokens")
        timezone = ZoneInfo(data.timezone)
        reset = weekly.resets_at.astimezone(timezone)
        long_parts = [weekly_text]
        if limit_text:
            long_parts.append(limit_text)
        if today_parts:
            long_parts.append(f"Today {' '.join(today_parts)}")
        long_parts.append(f"Resets {reset.month}/{reset.day} {reset:%H:%M}")

        short_parts = [f"Weekly {weekly.remaining_percent}%"]
        if data.today is not None and data.today.tokens is not None:
            short_parts.append(f"Today {cls.compact_tokens(data.today.tokens)}")
        return AiUsageDisplay(
            long=" · ".join(long_parts),
            short=" · ".join(short_parts),
        )

    @staticmethod
    def _money(value: Decimal, *, fixed: bool) -> str:
        rounded = value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        if not fixed and rounded == rounded.to_integral_value():
            return f"{int(rounded):,}"
        return f"{rounded:,.2f}"

    @staticmethod
    def compact_tokens(tokens: int) -> str:
        for divisor, suffix in (
            (1_000_000_000, "B"),
            (1_000_000, "M"),
            (1_000, "K"),
        ):
            if tokens >= divisor:
                value = Decimal(tokens) / Decimal(divisor)
                rendered = f"{value.quantize(Decimal('0.1'), rounding=ROUND_HALF_UP):f}"
                return f"{rendered.rstrip('0').rstrip('.')}{suffix}"
        return str(tokens)
