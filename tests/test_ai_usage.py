from __future__ import annotations

import asyncio
import threading
import time
from contextlib import nullcontext
from dataclasses import replace
from datetime import datetime
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from app.ai_usage.models import AiTodayUsage, AiWeeklyUsage
from app.ai_usage.provider_browser import (
    ProviderBrowserAdapter,
    ProviderBrowserCollection,
    ProviderBrowserUnavailable,
)
from app.ai_usage.service import AiUsageService
from app.application import create_app
from app.codex.local_usage import CodexLocalUsageUnavailable
from app.codex.models import CodexQuotaData, CodexQuotaWindow, CodexTokenUsageData
from app.codex.rate_limits import CodexAccountCollection, CodexRateLimitService
from app.core.config import AiUsageConfig, AiUsageSub2ApiConfig, Settings


def _authorization(settings: Settings) -> dict[str, str]:
    return {}


def _provider_config() -> AiUsageConfig:
    return AiUsageConfig(
        sub2api=AiUsageSub2ApiConfig(base_url="http://10.20.30.40")
    )


def _provider_collection(*, tokens: int | None = 100_000_000) -> ProviderBrowserCollection:
    return ProviderBrowserCollection(
        weekly=AiWeeklyUsage(
            remaining_percent=78,
            used_usd=Decimal("218.0751702"),
            remaining_usd=Decimal("781.9248298"),
            limit_usd=Decimal("1000"),
            resets_at="2026-08-20T15:45:56+08:00",
        ),
        today=AiTodayUsage(
            date="2026-08-15",
            used_usd=Decimal("181.0185952"),
            tokens=tokens,
            tokens_scope="account" if tokens is not None else None,
        ),
    )


def _subscription_payload() -> dict[str, object]:
    return {
        "data": [
            {
                "id": 179,
                "status": "active",
                "weekly_window_start": "2026-08-13T15:45:56+08:00",
                "weekly_usage_usd": Decimal("218.0751702"),
                "daily_usage_usd": Decimal("181.0185952"),
                "group": {
                    "platform": "openai",
                    "weekly_limit_usd": Decimal("1000"),
                },
            }
        ]
    }


def test_sub2api_configuration_allows_default_subscription_and_safe_base_url() -> None:
    assert AiUsageSub2ApiConfig(
        base_url="http://10.20.30.40",
    ).subscription_id is None
    with pytest.raises(ValueError, match="Sub2API origin"):
        AiUsageSub2ApiConfig(base_url="https://user:secret@service.test/other")
    with pytest.raises(ValueError, match="literal private address"):
        AiUsageSub2ApiConfig(base_url="http://example.com")


def test_today_usage_requires_token_scope_with_tokens() -> None:
    with pytest.raises(ValueError, match="tokens and tokens_scope"):
        AiTodayUsage(date="2026-08-15", tokens=1)
    with pytest.raises(ValueError, match="tokens and tokens_scope"):
        AiTodayUsage(date="2026-08-15", tokens_scope="local_device")


def test_codex_auth_collection_does_not_request_account_usage_for_api_key() -> None:
    service = CodexRateLimitService()
    process = MagicMock()
    process.stdin = MagicMock()
    process.poll.return_value = 0
    service._start_process = MagicMock(return_value=process)
    service._response_queue = MagicMock(return_value=MagicMock())
    service._read_response_queue = MagicMock(
        return_value={
            2: {
                "id": 2,
                "result": {"account": None, "requiresOpenaiAuth": False},
            }
        }
    )
    service._write_messages = MagicMock()

    result = service.collect_ai_account_status(timeout_seconds=2)

    assert result.auth_type == "apiKey"
    assert service._read_response_queue.call_count == 1
    sent = service._write_messages.call_args.args[1]
    assert [item["method"] for item in sent] == [
        "initialize",
        "initialized",
        "account/read",
    ]


def test_account_identity_is_an_internal_irreversible_key() -> None:
    response = {
        "result": {
            "account": {
                "type": "chatgpt",
                "email": "Person@Example.com",
            }
        }
    }

    identity = CodexRateLimitService._parse_account_identity(response)

    assert identity is not None
    assert "person" not in identity
    assert identity == CodexRateLimitService._parse_account_identity(response)


def test_account_mode_uses_local_today_without_neighbor_fallback(
    settings: Settings,
) -> None:
    codex = MagicMock()
    codex.collect_ai_account_status.return_value = CodexAccountCollection(
        "chatgpt",
        quota=CodexQuotaData(
            status="available",
            windows=[
                CodexQuotaWindow(
                    remaining_percent=78,
                    window_duration_minutes=10080,
                    resets_at="2026-08-20T15:45:56+08:00",
                ),
            ],
        ),
        usage=CodexTokenUsageData(
            status="available",
            daily_usage=[{"start_date": "2026-08-14", "tokens": 100_000_000}],
        ),
    )
    browser = MagicMock()
    local_usage = MagicMock()
    local_usage.read_today.return_value = 2_500_000
    service = AiUsageService(
        settings,
        codex,
        browser,
        local_usage_reader=local_usage,
    )

    with patch("app.ai_usage.service.datetime") as current:
        current.now.return_value = datetime.fromisoformat("2026-08-15T09:00:00+08:00")
        result = service.read(force=True)

    assert result.source == "account_login"
    assert result.today is not None and result.today.tokens == 2_500_000
    assert result.today.tokens_scope == "local_device"
    assert result.display.short == "Weekly 78% · Today 2.5M (local)"
    assert "Today 2.5M tokens (local)" in (result.display.long or "")
    local_usage.read_today.assert_called_once()
    browser.collect.assert_not_called()


def test_account_mode_prefers_exact_account_today(settings: Settings) -> None:
    codex = MagicMock()
    codex.collect_ai_account_status.return_value = CodexAccountCollection(
        "chatgpt",
        quota=CodexQuotaData(
            status="available",
            windows=[
                CodexQuotaWindow(
                    remaining_percent=78,
                    window_duration_minutes=10080,
                    resets_at="2026-08-20T15:45:56+08:00",
                ),
                CodexQuotaWindow(
                    remaining_percent=42,
                    window_duration_minutes=300,
                    resets_at="2026-08-15T18:20:00+08:00",
                ),
            ],
        ),
        usage=CodexTokenUsageData(
            status="available",
            daily_usage=[{"start_date": "2026-08-15", "tokens": 3_000_000}],
        ),
    )
    local_usage = MagicMock()
    service = AiUsageService(
        settings,
        codex,
        MagicMock(),
        local_usage_reader=local_usage,
    )

    with patch("app.ai_usage.service.datetime") as current:
        current.now.return_value = datetime.fromisoformat("2026-08-15T09:00:00+08:00")
        result = service.read(force=True)

    assert result.today is not None
    assert result.today.tokens == 3_000_000
    assert result.today.tokens_scope == "account"
    assert result.display.short == "Weekly 78% · Today 3M"
    assert result.five_hour is not None
    assert result.five_hour.remaining_percent == 42
    assert result.display.home[0].text == "Weekly 78% left"
    assert result.display.home[1].text == "Reset 8/20 15:45"
    assert result.display.home[2].text == "5h 42% left"
    assert result.display.home[3].text == "Reset 8/15 18:20"
    assert result.display.home[4].text == "Today 3M tokens"
    local_usage.read_today.assert_not_called()


def test_account_mode_omits_today_when_local_usage_is_unavailable(
    settings: Settings,
) -> None:
    codex = MagicMock()
    codex.collect_ai_account_status.return_value = CodexAccountCollection(
        "chatgpt",
        quota=CodexQuotaData(
            status="available",
            windows=[
                CodexQuotaWindow(
                    remaining_percent=78,
                    window_duration_minutes=10080,
                    resets_at="2026-08-20T15:45:56+08:00",
                )
            ],
        ),
        usage=CodexTokenUsageData(
            status="available",
            daily_usage=[{"start_date": "2026-08-14", "tokens": 100_000_000}],
        ),
    )
    local_usage = MagicMock()
    local_usage.read_today.side_effect = CodexLocalUsageUnavailable("unavailable")
    service = AiUsageService(
        settings,
        codex,
        MagicMock(),
        local_usage_reader=local_usage,
    )

    with patch("app.ai_usage.service.datetime") as current:
        current.now.return_value = datetime.fromisoformat("2026-08-15T09:00:00+08:00")
        result = service.read(force=True)

    assert result.today is not None and result.today.tokens is None
    assert result.today.tokens_scope is None
    assert result.display.short == "Weekly 78%"


def test_api_key_mode_formats_provider_usage_and_does_not_fallback(
    settings: Settings,
) -> None:
    codex = MagicMock()
    codex.collect_ai_account_status.return_value = CodexAccountCollection("apiKey")
    browser = MagicMock()
    browser.collect.return_value = _provider_collection()
    service = AiUsageService(settings, codex, browser)

    result = service.read(force=True)

    assert result.source == "sub2api"
    assert result.weekly is not None
    assert result.weekly.remaining_percent == 78
    assert result.display.long == (
        "Weekly $781.92 left (78%) · Limit $1,000 · "
        "Today $181.02 used 100M tokens · Resets 8/20 15:45"
    )
    assert result.display.short == "Weekly 78% · Today 100M"
    browser.collect.assert_called_once()


def test_refresh_failure_only_retains_same_source_snapshot(settings: Settings) -> None:
    settings.ai_usage = _provider_config()
    settings.ai_usage.sub2api.subscription_id = 179
    codex = MagicMock()
    codex.collect_ai_account_status.return_value = CodexAccountCollection("apiKey")
    browser = MagicMock()
    fresh = _provider_collection()
    fresh = replace(
        fresh,
        weekly=fresh.weekly.model_copy(
            update={
                "resets_at": datetime.fromisoformat(
                    "2099-08-22T15:45:56+08:00"
                )
            }
        ),
    )
    browser.collect.side_effect = [
        fresh,
        ProviderBrowserUnavailable("provider_response_timeout"),
    ]
    service = AiUsageService(settings, codex, browser)

    first = service.read(force=True)
    second = service.read(force=True)

    assert first.stale is False
    assert second.status == "available"
    assert second.stale is True
    assert second.source == "sub2api"
    codex.collect_ai_account_status.return_value = CodexAccountCollection(None)
    third = service.read(force=True)
    assert third.status == "unavailable"
    assert third.source is None


def test_fresh_cache_refreshes_after_today_date_changes(settings: Settings) -> None:
    codex = MagicMock()
    codex.collect_ai_account_status.return_value = CodexAccountCollection(
        "chatgpt",
        quota=CodexQuotaData(
            status="available",
            windows=[
                CodexQuotaWindow(
                    remaining_percent=78,
                    window_duration_minutes=10080,
                    resets_at="2026-08-20T15:45:56+08:00",
                )
            ],
        ),
    )
    local_usage = MagicMock()
    local_usage.read_today.side_effect = [2_500_000, 120_000]
    service = AiUsageService(
        settings,
        codex,
        MagicMock(),
        local_usage_reader=local_usage,
    )

    with patch("app.ai_usage.service.datetime") as current:
        current.now.return_value = datetime.fromisoformat("2026-08-15T23:59:59+08:00")
        first = service.read(force=True)
        current.now.return_value = datetime.fromisoformat("2026-08-16T00:00:01+08:00")
        second = service.read()

    assert first.today is not None and first.today.date.isoformat() == "2026-08-15"
    assert second.today is not None and second.today.date.isoformat() == "2026-08-16"
    assert second.today.tokens == 120_000
    assert codex.collect_ai_account_status.call_count == 2


def test_fresh_cache_refreshes_when_weekly_window_resets(settings: Settings) -> None:
    codex = MagicMock()
    codex.collect_ai_account_status.side_effect = [
        CodexAccountCollection(
            "chatgpt",
            quota=CodexQuotaData(
                status="available",
                windows=[
                    CodexQuotaWindow(
                        remaining_percent=5,
                        window_duration_minutes=10080,
                        resets_at="2026-08-15T10:00:00+08:00",
                    ),
                    CodexQuotaWindow(
                        remaining_percent=5,
                        window_duration_minutes=300,
                        resets_at="2026-08-15T10:00:00+08:00",
                    ),
                ],
            ),
        ),
        CodexAccountCollection(
            "chatgpt",
            quota=CodexQuotaData(
                status="available",
                windows=[
                    CodexQuotaWindow(
                        remaining_percent=100,
                        window_duration_minutes=10080,
                        resets_at="2026-08-22T10:00:00+08:00",
                    ),
                    CodexQuotaWindow(
                        remaining_percent=100,
                        window_duration_minutes=300,
                        resets_at="2026-08-15T15:00:00+08:00",
                    ),
                ],
            ),
        ),
    ]
    service = AiUsageService(
        settings,
        codex,
        MagicMock(),
        local_usage_reader=MagicMock(read_today=MagicMock(return_value=0)),
    )

    with patch("app.ai_usage.service.datetime") as current:
        current.now.return_value = datetime.fromisoformat("2026-08-15T09:59:59+08:00")
        first = service.read(force=True)
        current.now.return_value = datetime.fromisoformat("2026-08-15T10:00:01+08:00")
        second = service.read()

    assert first.weekly is not None and first.weekly.remaining_percent == 5
    assert second.weekly is not None and second.weekly.remaining_percent == 100
    assert codex.collect_ai_account_status.call_count == 2


def test_refresh_failure_does_not_retain_another_account_snapshot(
    settings: Settings,
) -> None:
    available = CodexAccountCollection(
        "chatgpt",
        quota=CodexQuotaData(
            status="available",
            windows=[
                CodexQuotaWindow(
                    remaining_percent=78,
                    window_duration_minutes=10080,
                    resets_at="2026-08-20T15:45:56+08:00",
                )
            ],
        ),
        identity_key="account-a",
    )
    unavailable = CodexAccountCollection(
        "chatgpt",
        quota=CodexQuotaData(status="unavailable"),
        identity_key="account-b",
    )
    codex = MagicMock()
    codex.collect_ai_account_status.side_effect = [available, unavailable]
    service = AiUsageService(
        settings,
        codex,
        MagicMock(),
        local_usage_reader=MagicMock(read_today=MagicMock(return_value=0)),
    )

    assert service.read(force=True).status == "available"
    result = service.read(force=True)

    assert result.status == "unavailable"
    assert result.source == "account_login"


def test_concurrent_forced_refreshes_share_one_collection(settings: Settings) -> None:
    codex = MagicMock()
    codex.collect_ai_account_status.return_value = CodexAccountCollection("apiKey")
    browser = MagicMock()

    def collect(*, timeout_seconds: float) -> ProviderBrowserCollection:
        time.sleep(0.1)
        return _provider_collection()

    browser.collect.side_effect = collect
    service = AiUsageService(settings, codex, browser)
    barrier = threading.Barrier(3)
    results = []

    def read() -> None:
        barrier.wait()
        results.append(service.read(force=True))

    threads = [threading.Thread(target=read), threading.Thread(target=read)]
    for thread in threads:
        thread.start()
    barrier.wait()
    for thread in threads:
        thread.join(timeout=2)

    assert len(results) == 2
    assert browser.collect.call_count == 1


def test_provider_browser_filters_fixed_request_and_subscription(
    settings: Settings,
) -> None:
    settings.ai_usage = _provider_config()
    adapter = ProviderBrowserAdapter(settings.ai_usage, settings.automations)
    assert adapter._matches_usage_response(
        SimpleNamespace(
            url=(
                "http://10.20.30.40/api/v1/subscriptions/active"
                "?timezone=Asia%2FShanghai"
            )
        )
    )
    assert not adapter._matches_usage_response(
        SimpleNamespace(
            url=(
                "http://10.20.30.41/api/v1/subscriptions/active"
                "?timezone=Asia%2FShanghai"
            )
        )
    )
    assert adapter._matches_stats_response(
        SimpleNamespace(
            url=(
                "http://10.20.30.40/api/v1/usage/dashboard/stats"
                "?timezone=Asia%2FShanghai"
            )
        )
    )
    today_tokens = adapter._parse_today_tokens(
        {
            "code": 0,
            "data": {
                "today_tokens": 35_023_210,
                "by_platform": [
                    {"platform": "openai", "today_tokens": 35_023_210}
                ],
            },
        }
    )
    result = adapter._parse_payload(
        _subscription_payload(),
        today_tokens=today_tokens,
    )
    assert result.weekly.remaining_percent == 78
    assert result.weekly.resets_at.isoformat() == "2026-08-20T15:45:56+08:00"
    assert result.today.tokens == 35_023_210
    assert result.today.tokens_scope == "account"
    assert result.subscription_id == 179


def test_provider_browser_uses_first_active_openai_subscription_by_default(
    settings: Settings,
) -> None:
    settings.ai_usage = _provider_config()
    adapter = ProviderBrowserAdapter(settings.ai_usage, settings.automations)
    payload = _subscription_payload()
    payload["data"].insert(
        0,
        {
            **payload["data"][0],
            "id": 178,
            "weekly_usage_usd": Decimal("100"),
        },
    )

    result = adapter._parse_payload(payload)

    assert result.subscription_id == 178


def test_provider_browser_rejects_invalid_or_other_platform_token_stats(
    settings: Settings,
) -> None:
    settings.ai_usage = _provider_config()
    adapter = ProviderBrowserAdapter(settings.ai_usage, settings.automations)

    with pytest.raises(ProviderBrowserUnavailable, match="token_response_invalid"):
        adapter._parse_today_tokens(
            {
                "code": 0,
                "data": {
                    "by_platform": [
                        {"platform": "anthropic", "today_tokens": 35_023_210}
                    ]
                },
            }
        )
    with pytest.raises(ProviderBrowserUnavailable, match="token_response_invalid"):
        adapter._parse_today_tokens(
            {
                "code": 0,
                "data": {
                    "by_platform": [
                        {"platform": "openai", "today_tokens": "35023210"}
                    ]
                },
            }
        )


def test_provider_browser_invalid_stats_does_not_fail_fresh_quota(
    settings: Settings,
) -> None:
    settings.ai_usage = _provider_config()
    adapter = ProviderBrowserAdapter(settings.ai_usage, settings.automations)
    adapter._capture_responses = AsyncMock(
        return_value=SimpleNamespace(
            subscription=_subscription_payload(),
            stats={"code": 0, "data": {"by_platform": []}},
        )
    )

    with (
        patch(
            "app.ai_usage.provider_browser.debug_chrome_status",
            return_value=("running", None, None),
        ),
        patch(
            "app.ai_usage.provider_browser.file_lock",
            return_value=nullcontext(),
        ),
    ):
        result = adapter.collect(timeout_seconds=1)

    assert result.weekly.remaining_percent == 78
    assert result.today.tokens is None


def test_provider_browser_closes_its_page_on_login_redirect(
    settings: Settings,
) -> None:
    settings.ai_usage = _provider_config()
    adapter = ProviderBrowserAdapter(settings.ai_usage, settings.automations)

    pages = []

    class Page:
        url = "about:blank"

        def __init__(self) -> None:
            self.closed = False
            pages.append(self)

        def on(self, _event: str, _callback) -> None:
            return None

        async def goto(self, *_args, **_kwargs) -> None:
            self.url = "http://10.20.30.40/login?redirect=/subscriptions"

        async def close(self) -> None:
            self.closed = True

    class Context:
        async def new_page(self):
            return Page()

    class Session:
        async def __aenter__(self):
            return SimpleNamespace(context=Context())

        async def __aexit__(self, *_args):
            return None

    with patch(
        "app.ai_usage.provider_browser.session_factory",
        return_value=lambda **_kwargs: Session(),
    ):
        with pytest.raises(ProviderBrowserUnavailable, match="login_unavailable"):
            asyncio.run(adapter._capture_responses(1))

    assert len(pages) == 2
    assert all(page.closed for page in pages)


@pytest.mark.anyio
async def test_ai_usage_api_is_protected_and_supports_refresh(
    settings: Settings,
) -> None:
    app = create_app(settings)
    assert app.state.weixin_chub_mode.ai_usage_reader is app.state.ai_usage
    usage = MagicMock()
    usage.read.return_value = AiUsageService(
        settings,
        MagicMock(
            collect_ai_account_status=MagicMock(
                return_value=CodexAccountCollection("apiKey")
            )
        ),
        MagicMock(collect=MagicMock(return_value=_provider_collection())),
    ).read(force=True)
    app.state.ai_usage = usage
    transport = httpx.ASGITransport(app=app)
    untrusted_transport = httpx.ASGITransport(
        app=app,
        client=("192.0.2.1", 12345),
    )

    async with httpx.AsyncClient(
        transport=untrusted_transport,
        base_url="http://test",
    ) as client:
        denied = await client.get("/api/ai/usage")
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(
            "/api/ai/usage?refresh=true",
            headers=_authorization(settings),
        )

    assert denied.status_code == 403
    assert response.status_code == 200
    assert response.json()["data"]["weekly"]["remaining_usd"] == "781.9248298"
    assert response.json()["data"]["today"]["tokens_scope"] == "account"
    usage.read.assert_called_once_with(force=True)
