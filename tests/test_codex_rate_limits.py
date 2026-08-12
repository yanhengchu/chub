from datetime import UTC, datetime
from unittest.mock import MagicMock, patch

from app.codex.models import CodexQuotaData, CodexQuotaWindow, CodexTokenUsageData
from app.codex.rate_limits import CodexRateLimitService


def test_rate_limit_response_only_exposes_quota_windows() -> None:
    service = CodexRateLimitService()

    result = service._parse_response(
        {
            "result": {
                "rateLimits": {
                    "limitId": "private-metered-bucket",
                    "primary": {
                        "usedPercent": 24.6,
                        "windowDurationMins": 15,
                        "resetsAt": 1_786_047_300,
                    },
                    "secondary": {
                        "usedPercent": 100,
                        "windowDurationMins": 60,
                        "resetsAt": 1_786_050_000,
                    },
                },
                "rateLimitResetCredits": {"credits": [{"id": "secret-credit"}]},
            }
        }
    )

    assert result.status == "available"
    assert [(item.remaining_percent, item.window_duration_minutes) for item in result.windows] == [
        (75, 15),
        (0, 60),
    ]
    assert result.windows[0].resets_at == datetime.fromtimestamp(1_786_047_300, tz=UTC)
    assert "private" not in result.model_dump_json()
    assert "secret" not in result.model_dump_json()


def test_rate_limit_service_keeps_last_success_when_refresh_fails() -> None:
    service = CodexRateLimitService()
    available = CodexQuotaData(
        status="available",
        windows=[
            CodexQuotaWindow(
                remaining_percent=80,
                window_duration_minutes=15,
                resets_at="2026-08-06T10:15:00Z",
            )
        ],
    )
    service._read_from_codex = MagicMock(
        side_effect=[available, CodexQuotaData(status="unavailable", message="暂不可用")]
    )

    assert service.read(force=True).windows[0].remaining_percent == 80
    retained = service.read(force=True)

    assert retained.status == "available"
    assert retained.windows[0].remaining_percent == 80
    assert retained.message == "暂不可用"


def test_token_usage_response_exposes_daily_buckets_only() -> None:
    service = CodexRateLimitService()

    result = service._parse_usage_response(
        {
            "result": {
                "summary": {"lifetimeTokens": 9_999_999},
                "dailyUsageBuckets": [
                    {"startDate": "2026-08-12", "tokens": 123_456},
                    {"startDate": "invalid", "tokens": 10},
                ],
            }
        }
    )

    assert result.status == "available"
    assert result.daily_usage[0].start_date.isoformat() == "2026-08-12"
    assert result.daily_usage[0].tokens == 123_456
    assert "lifetime" not in result.model_dump_json()


def test_token_usage_service_keeps_last_success_when_refresh_fails() -> None:
    service = CodexRateLimitService()
    available = CodexTokenUsageData(
        status="available",
        daily_usage=[{"start_date": "2026-08-12", "tokens": 123_456}],
    )
    service._read_usage_from_codex = MagicMock(
        side_effect=[
            available,
            CodexTokenUsageData(status="unavailable", message="暂不可用"),
        ]
    )

    assert service.read_usage(force=True).daily_usage[0].tokens == 123_456
    retained = service.read_usage(force=True)

    assert retained.status == "available"
    assert retained.daily_usage[0].tokens == 123_456
    assert retained.message == "暂不可用"


def test_account_status_reads_quota_and_usage_in_one_app_server_session() -> None:
    service = CodexRateLimitService()
    service._request_codex_many = MagicMock(
        return_value={
            2: {
                "result": {
                    "rateLimits": {
                        "primary": {
                            "usedPercent": 59,
                            "windowDurationMins": 10_080,
                            "resetsAt": 1_786_047_300,
                        }
                    }
                }
            },
            3: {
                "result": {
                    "dailyUsageBuckets": [
                        {"startDate": "2026-08-12", "tokens": 123_456}
                    ]
                }
            },
        }
    )

    with patch("app.codex.rate_limits.shutil.which", return_value="/usr/bin/codex"):
        quota, usage = service.read_account_status(force=True)

    assert quota.windows[0].remaining_percent == 41
    assert usage.daily_usage[0].tokens == 123_456
    service._request_codex_many.assert_called_once_with(
        {2: "account/rateLimits/read", 3: "account/usage/read"}
    )
