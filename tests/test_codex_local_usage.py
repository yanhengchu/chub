from __future__ import annotations

import json
import os
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from app.codex.local_usage import (
    CodexLocalUsageReader,
    CodexLocalUsageUnavailable,
)


TIMEZONE = ZoneInfo("Asia/Shanghai")
TODAY = date(2026, 8, 15)
TODAY_MTIME = datetime(2026, 8, 15, 12, tzinfo=TIMEZONE).timestamp()


def _session_meta(session_id: str, *, provider: str = "openai") -> dict[str, object]:
    return {
        "timestamp": "2026-08-14T15:00:00Z",
        "type": "session_meta",
        "payload": {
            "id": session_id,
            "model_provider": provider,
        },
    }


def _token_count(timestamp: str, tokens: int) -> dict[str, object]:
    return {
        "timestamp": timestamp,
        "type": "event_msg",
        "payload": {
            "type": "token_count",
            "info": {"total_token_usage": {"total_tokens": tokens}},
        },
    }


def _write_session(
    path: Path,
    values: list[dict[str, object]],
    *,
    suffix: bytes = b"",
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    content = b"".join(
        json.dumps(value, separators=(",", ":")).encode("utf-8") + b"\n"
        for value in values
    )
    path.write_bytes(content + suffix)
    os.utime(path, (TODAY_MTIME, TODAY_MTIME))


def test_local_usage_sums_today_deltas_and_deduplicates_sessions(
    tmp_path: Path,
) -> None:
    active = tmp_path / "sessions" / "2026" / "08" / "14" / "active.jsonl"
    archived = tmp_path / "archived_sessions" / "active-copy.jsonl"
    second = tmp_path / "sessions" / "2026" / "08" / "15" / "second.jsonl"
    other_provider = tmp_path / "sessions" / "2026" / "08" / "15" / "other.jsonl"
    values = [
        _session_meta("session-a"),
        _token_count("2026-08-14T15:59:00Z", 100),
        _token_count("2026-08-14T16:01:00Z", 160),
        _token_count("2026-08-15T02:00:00Z", 230),
    ]
    _write_session(active, values)
    _write_session(archived, values[:-1])
    _write_session(
        second,
        [
            _session_meta("session-b"),
            _token_count("2026-08-14T16:05:00Z", 50),
            _token_count("2026-08-15T02:05:00Z", 90),
        ],
    )
    _write_session(
        other_provider,
        [
            _session_meta("session-c", provider="other"),
            _token_count("2026-08-15T02:05:00Z", 999),
        ],
    )

    result = CodexLocalUsageReader(tmp_path).read_today(
        today=TODAY,
        timezone=TIMEZONE,
        timeout_seconds=1,
    )

    assert result == 220


def test_local_usage_handles_counter_reset_and_partial_trailing_line(
    tmp_path: Path,
) -> None:
    path = tmp_path / "sessions" / "2026" / "08" / "15" / "reset.jsonl"
    _write_session(
        path,
        [
            _session_meta("session-reset"),
            _token_count("2026-08-15T01:00:00Z", 100),
            _token_count("2026-08-15T02:00:00Z", 25),
        ],
        suffix=b'{"timestamp":"unfinished',
    )

    result = CodexLocalUsageReader(tmp_path).read_today(
        today=TODAY,
        timezone=TIMEZONE,
        timeout_seconds=1,
    )

    assert result == 125


def test_local_usage_skips_bounded_oversized_non_usage_lines(
    tmp_path: Path,
) -> None:
    path = tmp_path / "sessions" / "2026" / "08" / "15" / "large-line.jsonl"
    _write_session(
        path,
        [
            _session_meta("session-large-line"),
            {"type": "response_item", "payload": {"text": "x" * 500}},
            _token_count("2026-08-15T02:00:00Z", 80),
        ],
    )
    reader = CodexLocalUsageReader(tmp_path)
    reader.MAX_LINE_BYTES = 200

    assert reader.read_today(
        today=TODAY,
        timezone=TIMEZONE,
        timeout_seconds=1,
    ) == 80


def test_local_usage_returns_zero_when_session_roots_are_empty(
    tmp_path: Path,
) -> None:
    (tmp_path / "sessions").mkdir()

    assert CodexLocalUsageReader(tmp_path).read_today(
        today=TODAY,
        timezone=TIMEZONE,
        timeout_seconds=1,
    ) == 0


def test_local_usage_fails_closed_for_missing_or_invalid_data(
    tmp_path: Path,
) -> None:
    reader = CodexLocalUsageReader(tmp_path)
    with pytest.raises(CodexLocalUsageUnavailable, match="session_root_unavailable"):
        reader.read_today(today=TODAY, timezone=TIMEZONE, timeout_seconds=1)

    invalid = tmp_path / "sessions" / "2026" / "08" / "15" / "invalid.jsonl"
    _write_session(
        invalid,
        [_session_meta("session-invalid")],
        suffix=b'{"type":"event_msg","payload":{"type":"token_count"},"\xff":1}\n',
    )
    with pytest.raises(CodexLocalUsageUnavailable, match="session_line_invalid"):
        reader.read_today(today=TODAY, timezone=TIMEZONE, timeout_seconds=1)


def test_local_usage_enforces_file_and_time_limits(tmp_path: Path) -> None:
    path = tmp_path / "sessions" / "2026" / "08" / "15" / "limited.jsonl"
    _write_session(
        path,
        [
            _session_meta("session-limited"),
            _token_count("2026-08-15T02:00:00Z", 80),
        ],
    )
    reader = CodexLocalUsageReader(tmp_path)
    reader.MAX_FILE_BYTES = 1

    with pytest.raises(CodexLocalUsageUnavailable, match="session_file_too_large"):
        reader.read_today(today=TODAY, timezone=TIMEZONE, timeout_seconds=1)
    with pytest.raises(CodexLocalUsageUnavailable, match="timeout"):
        CodexLocalUsageReader(tmp_path).read_today(
            today=TODAY,
            timezone=TIMEZONE,
            timeout_seconds=0,
        )
