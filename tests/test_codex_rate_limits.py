from datetime import UTC, datetime
from unittest.mock import MagicMock

from app.codex.models import CodexQuotaData, CodexQuotaWindow
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
