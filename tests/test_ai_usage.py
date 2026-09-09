from __future__ import annotations

import asyncio
import json
import threading
import time
from dataclasses import replace
from datetime import datetime
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from app.ai_usage.models import AiTodayUsage, AiWeeklyUsage
from app.ai_runtime.general_settings import RuntimeSettingsStoreUnavailable
from app.ai_runtime import RuntimeOperationError
from app.ai_runtime.external_modules import ExternalRuntimeModuleService
from app.ai_runtime.usage import RuntimeUsageService
from chub_codex_runtime.provider_browser import (
    ProviderBrowserAdapter,
    ProviderBrowserCollection,
    ProviderBrowserUnavailable,
)
from app.automations.lock import file_lock
from chub_codex_runtime.usage_service import AiUsageService
from app.application import create_app
from chub_codex_runtime.local_usage import CodexLocalUsageUnavailable
from app.codex.models import CodexQuotaData, CodexQuotaWindow, CodexTokenUsageData
from chub_codex_runtime.rate_limits import CodexAccountCollection, CodexRateLimitService
from chub_codex_runtime.usage_settings import (
    CodexProviderConfigReader,
    CodexProviderConfigUnavailable,
    CodexUsageSettings,
)
from app.ai_runtime.general_settings import AiRuntimeSettingsStore
from app.core.config import Settings


def _authorization(settings: Settings) -> dict[str, str]:
    return {}


def _provider_config() -> CodexUsageSettings:
    return CodexUsageSettings(provider_base_url="http://10.20.30.40")


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


def test_provider_config_reader_uses_active_codex_provider_origin(tmp_path) -> None:
    (tmp_path / "config.toml").write_text(
        """
model_provider = "OpenAI"

[model_providers.OpenAI]
base_url = "http://203.0.113.8:19099/"
wire_api = "responses"
""".strip(),
        encoding="utf-8",
    )

    assert CodexProviderConfigReader(tmp_path).read_base_url() == (
        "http://203.0.113.8:19099"
    )


def test_provider_config_reader_fails_closed_for_invalid_or_missing_config(tmp_path) -> None:
    reader = CodexProviderConfigReader(tmp_path)
    with pytest.raises(CodexProviderConfigUnavailable):
        reader.read_base_url()

    (tmp_path / "config.toml").write_text(
        '[model_providers.OpenAI]\nbase_url = "https://user:secret@example.test"\n',
        encoding="utf-8",
    )
    assert reader.read_base_url() is None

    (tmp_path / "config.toml").write_text(
        'model_provider = "OpenAI"\n[model_providers.OpenAI]\nbase_url = "https://example.test:not-a-port"\n',
        encoding="utf-8",
    )
    assert reader.read_base_url() is None


def test_runtime_usage_service_reads_the_current_runtime_registry() -> None:
    first_registry = MagicMock()
    first_registry.require.side_effect = RuntimeError("runtime removed")
    second_registry = MagicMock()
    adapter = MagicMock()
    expected = MagicMock(runtime_id="codex")
    adapter.read_usage_snapshot.return_value = expected
    second_registry.require.return_value = adapter
    current_registry = first_registry
    usage = RuntimeUsageService(
        lambda: current_registry,
        default_runtime_id="codex",
    )

    current_registry = second_registry

    assert usage.read(force=True) is expected
    second_registry.require.assert_called_once_with("codex", {"usage_snapshot"})
    adapter.read_usage_snapshot.assert_called_once_with(force=True)


def test_runtime_usage_service_routes_login_page_to_runtime_owner() -> None:
    registry = MagicMock()
    adapter = MagicMock()
    registry.require.return_value = adapter
    usage = RuntimeUsageService(lambda: registry, default_runtime_id="codex")

    usage.open_login_page()

    registry.require.assert_called_once_with("codex", {"usage_login_page"})
    adapter.open_usage_login_page.assert_called_once_with()


def test_runtime_usage_service_reports_login_recovery_only_when_supported() -> None:
    registry = MagicMock()
    registry.require.return_value = MagicMock()
    usage = RuntimeUsageService(lambda: registry, default_runtime_id="codex")

    assert usage.login_page_available() is True
    registry.require.assert_called_once_with("codex", {"usage_login_page"})

    registry.require.reset_mock()
    registry.require.side_effect = RuntimeOperationError(
        "runtime_capability_unavailable",
        "Runtime does not support this operation",
    )

    assert usage.login_page_available() is False
    registry.require.assert_called_once_with("codex", {"usage_login_page"})


@pytest.mark.anyio
async def test_general_runtime_settings_reject_removed_usage_timezone(
    settings: Settings,
    tmp_path,
) -> None:
    app = create_app(settings)
    store = AiRuntimeSettingsStore(tmp_path / "ai-runtimes.local.yaml")
    app.state.ai_session_manager.runtime_settings_store = store
    transport = httpx.ASGITransport(app=app)

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.put(
            "/api/ai/settings",
            json={"values": {"usage-timezone": "America/Los_Angeles"}},
        )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "ai_runtime_settings_invalid"


@pytest.mark.anyio
async def test_general_runtime_settings_save_weekly_report_session_defaults(
    settings: Settings,
    tmp_path,
) -> None:
    app = create_app(settings)
    store = AiRuntimeSettingsStore(tmp_path / "ai-runtimes.local.yaml")
    app.state.ai_session_manager.runtime_settings_store = store
    transport = httpx.ASGITransport(app=app)

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.put(
            "/api/ai/settings",
            json={
                "values": {
                    "new-session-permission": "read-only",
                    "weekly-report-runtime": "codex",
                    "weekly-report-permission": "auto-review",
                    "weekly-report-model": "__default__",
                    "weekly-report-reasoning": "__default__",
                }
            },
        )

    assert response.status_code == 200
    sections = response.json()["data"]["sections"]
    assert sections[0]["id"] == "new-session-defaults"
    assert sections[0]["fields"][0]["id"] == "new-session-permission"
    assert sections[0]["fields"][0]["value"] == "read-only"
    section = sections[1]
    assert section["id"] == "weekly-report-session"
    assert {field["id"] for field in section["fields"]} == {
        "weekly-report-runtime",
        "weekly-report-permission",
        "weekly-report-model",
        "weekly-report-reasoning",
    }
    saved = store.read_general().weekly_report_session
    assert store.read_general().new_session_permission == "read-only"
    assert saved.permission_mode == "auto-review"
    assert saved.model is None
    assert saved.reasoning_effort is None


@pytest.mark.anyio
async def test_general_runtime_settings_keep_weekly_session_controls_with_builtin_runtime(
    settings: Settings,
) -> None:
    app = create_app(settings)
    manager = app.state.ai_session_manager
    removal = manager.runtime_module_service.remove("codex-010000", operation_id="a" * 32)
    manager.runtime_module_service.finalize_removal(removal)
    manager.refresh_external_runtime_modules()
    transport = httpx.ASGITransport(app=app)

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/api/ai/settings")

    assert response.status_code == 200
    section = response.json()["data"]["sections"][1]
    assert section["id"] == "weekly-report-session"
    assert {field["id"] for field in section["fields"]} == {
        "weekly-report-runtime",
        "weekly-report-permission",
        "weekly-report-model",
        "weekly-report-reasoning",
    }


@pytest.mark.anyio
async def test_external_runtime_settings_failure_returns_service_unavailable(
    settings: Settings,
) -> None:
    app = create_app(settings)
    adapter = app.state.ai_session_manager.runtime_adapter
    assert adapter.__class__.__module__ == "chub_codex_runtime.runtime_adapter"
    app.state.ai_session_manager.runtime_settings_store.read_general = MagicMock(
        side_effect=RuntimeSettingsStoreUnavailable("settings unavailable")
    )
    transport = httpx.ASGITransport(app=app)

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/api/ai/settings")

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "ai_runtime_settings_unavailable"


@pytest.mark.anyio
async def test_ai_usage_uses_builtin_runtime_when_no_formal_version_is_installed(
    settings: Settings,
) -> None:
    service = ExternalRuntimeModuleService(settings)
    removal = service.remove("codex-010000", operation_id="a" * 32)
    service.finalize_removal(removal)
    app = create_app(settings)
    transport = httpx.ASGITransport(app=app)

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/api/ai/usage")

    assert response.status_code in {200, 503}
    if response.status_code == 503:
        assert response.json()["error"]["code"] != "ai_runtime_unavailable"


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

    with patch("chub_codex_runtime.usage_service.datetime") as current:
        current.now.return_value = datetime.fromisoformat("2026-08-15T09:00:00+08:00")
        result = service.read(force=True)

    assert result.source == "account_login"
    assert result.today is not None and result.today.tokens == 2_500_000
    assert result.today.tokens_scope == "local_device"
    assert result.display.short == "Weekly 78% · 8/20 · Today 2.5M (local)"
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

    with patch("chub_codex_runtime.usage_service.datetime") as current:
        current.now.return_value = datetime.fromisoformat("2026-08-15T09:00:00+08:00")
        result = service.read(force=True)

    assert result.today is not None
    assert result.today.tokens == 3_000_000
    assert result.today.tokens_scope == "account"
    assert result.display.short == "5h 42% · 18:20 · Weekly 78% · 8/20"
    assert result.five_hour is not None
    assert result.five_hour.remaining_percent == 42
    assert result.display.long == (
        "5h 42% · 8/15 18:20 · Weekly 78% · 8/20 15:45 · Today 3M tokens"
    )
    assert result.display.home[0].text == "5h 42%"
    assert result.display.home[1].text == "8/15 18:20"
    assert result.display.home[2].text == "Weekly 78%"
    assert result.display.home[3].text == "8/20 15:45"
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

    with patch("chub_codex_runtime.usage_service.datetime") as current:
        current.now.return_value = datetime.fromisoformat("2026-08-15T09:00:00+08:00")
        result = service.read(force=True)

    assert result.today is not None and result.today.tokens is None
    assert result.today.tokens_scope is None
    assert result.display.short == "Weekly 78% · 8/20"


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
        "Weekly 78% · $781.92 / $1,000 · 8/20 15:45 · "
        "Today $181.02 · 100M tokens"
    )
    assert result.display.short == "Weekly 78% · 8/20 · Today 100M"
    browser.collect.assert_called_once()


def test_api_key_mode_reports_provider_account_login_required(
    settings: Settings,
) -> None:
    codex = MagicMock()
    codex.collect_ai_account_status.return_value = CodexAccountCollection("apiKey")
    browser = MagicMock()
    browser.collect.side_effect = ProviderBrowserUnavailable("provider_login_unavailable")
    service = AiUsageService(settings, codex, browser)

    result = service.read(force=True)

    assert result.status == "unavailable"
    assert result.source == "sub2api"
    assert result.message == "AI API 额度账户未登录。"


def test_refresh_failure_only_retains_same_source_snapshot(settings: Settings) -> None:
    usage_settings = _provider_config().model_copy(
        update={"provider_base_url": "http://10.20.30.40"}
    )
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
    service = AiUsageService(usage_settings, codex, settings.automations, browser)

    first = service.read(force=True)
    second = service.read(force=True)

    assert first.stale is False
    assert second.status == "unavailable"
    assert second.stale is False
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

    with patch("chub_codex_runtime.usage_service.datetime") as current:
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

    with patch("chub_codex_runtime.usage_service.datetime") as current:
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
    adapter = ProviderBrowserAdapter(_provider_config(), settings.automations)
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
    adapter = ProviderBrowserAdapter(_provider_config(), settings.automations)
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
    adapter = ProviderBrowserAdapter(_provider_config(), settings.automations)

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
    adapter = ProviderBrowserAdapter(_provider_config(), settings.automations)
    adapter._capture_responses = AsyncMock(
        return_value=SimpleNamespace(
            subscription=_subscription_payload(),
            stats={"code": 0, "data": {"by_platform": []}},
        )
    )

    browser_lock = settings.automations.runtime_dir / "locks" / "debug-chrome.lock"
    with file_lock(browser_lock, 0):
        with patch(
            "chub_codex_runtime.provider_browser.debug_chrome_status",
            return_value=("running", None, None),
        ):
            result = adapter.collect(timeout_seconds=1)

    assert result.weekly.remaining_percent == 78
    assert result.today.tokens is None


def test_provider_browser_collects_background_page_responses(
    settings: Settings,
) -> None:
    adapter = ProviderBrowserAdapter(_provider_config(), settings.automations)
    adapter._capture_background_page_responses = AsyncMock(
        return_value=SimpleNamespace(
            subscription=_subscription_payload(),
            stats={"code": 0, "data": {"by_platform": []}},
        )
    )

    with patch(
        "chub_codex_runtime.provider_browser.debug_chrome_status",
        return_value=("running", None, None),
    ):
        result = adapter.collect(timeout_seconds=1)

    assert result.weekly.remaining_percent == 78
    adapter._capture_background_page_responses.assert_awaited_once()


def test_provider_browser_keeps_login_redirect_as_login_required(
    settings: Settings,
) -> None:
    adapter = ProviderBrowserAdapter(_provider_config(), settings.automations)
    adapter._capture_background_page_responses = AsyncMock(
        side_effect=ProviderBrowserUnavailable("provider_login_unavailable")
    )

    with patch(
        "chub_codex_runtime.provider_browser.debug_chrome_status",
        return_value=("running", None, None),
    ):
        with pytest.raises(ProviderBrowserUnavailable, match="login_unavailable"):
            adapter.collect(timeout_seconds=1)



def test_provider_browser_does_not_infer_logout_from_subscription_authentication_failure(
    settings: Settings,
) -> None:
    adapter = ProviderBrowserAdapter(_provider_config(), settings.automations)

    adapter._capture_background_page_responses = AsyncMock(
        side_effect=ProviderBrowserUnavailable("provider_response_failed")
    )

    with patch(
        "chub_codex_runtime.provider_browser.debug_chrome_status",
        return_value=("running", None, None),
    ):
        with pytest.raises(ProviderBrowserUnavailable, match="response_failed"):
            adapter.collect(timeout_seconds=1)


def test_provider_browser_opens_or_reuses_fixed_login_page(
    settings: Settings,
) -> None:
    adapter = ProviderBrowserAdapter(_provider_config(), settings.automations)

    class Page:
        def __init__(self, name: str = "") -> None:
            self.name = name
            self.goto_url = None
            self.brought_to_front = False
            self.closed = False

        def is_closed(self) -> bool:
            return self.closed

        async def evaluate(self, _script: str) -> str:
            return self.name

        async def add_init_script(self, script: str) -> None:
            assert ProviderBrowserAdapter.LOGIN_PAGE_NAME in script
            self.name = ProviderBrowserAdapter.LOGIN_PAGE_NAME

        async def goto(self, url: str, **_kwargs) -> None:
            self.goto_url = url

        async def bring_to_front(self) -> None:
            self.brought_to_front = True

        async def close(self) -> None:
            self.closed = True

    existing = Page(ProviderBrowserAdapter.LOGIN_PAGE_NAME)
    created = Page()

    class Context:
        pages = [existing]

        async def new_page(self) -> Page:
            return created

    class Session:
        async def __aenter__(self):
            return SimpleNamespace(context=Context())

        async def __aexit__(self, *_args):
            return None

    with patch(
        "chub_codex_runtime.provider_browser.session_factory",
        return_value=lambda **_kwargs: Session(),
    ):
        asyncio.run(adapter._open_login_page())

    assert existing.brought_to_front is True
    assert created.goto_url is None


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
