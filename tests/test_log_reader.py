from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from app.services.log_reader import LogReadError, read_log_page, tail_log


def test_tail_log_returns_last_lines_in_original_order(tmp_path: Path) -> None:
    log = tmp_path / "hub.log"
    log.write_text("one\ntwo\nthree\nfour\n", encoding="utf-8")

    assert tail_log(log, 2) == ["three", "four"]


def test_tail_log_returns_empty_for_missing_or_empty_file(tmp_path: Path) -> None:
    missing = tmp_path / "missing.log"
    empty = tmp_path / "empty.log"
    empty.touch()

    assert tail_log(missing, 10) == []
    assert tail_log(empty, 10) == []


def test_tail_log_does_not_read_rotated_file(tmp_path: Path) -> None:
    active = tmp_path / "hub.log"
    rotated = tmp_path / "hub.log.1"
    active.write_text("active\n", encoding="utf-8")
    rotated.write_text("rotated-secret\n", encoding="utf-8")

    assert tail_log(active, 10) == ["active"]


def test_tail_log_limits_read_size_and_line_size(tmp_path: Path) -> None:
    log = tmp_path / "hub.log"
    log.write_bytes(b"ignored-line\n" + b"x" * 50 + b"\nlast\n")

    result = tail_log(
        log,
        10,
        max_read_bytes=60,
        max_line_bytes=20,
    )

    assert result[-1] == "last"
    assert len(result[-2].encode("utf-8")) <= 20
    assert result[-2].endswith("[truncated]")


def test_tail_log_preserves_complete_line_at_read_boundary(tmp_path: Path) -> None:
    log = tmp_path / "hub.log"
    log.write_bytes(b"old\nkeep\nend\n")

    result = tail_log(log, 10, max_read_bytes=9)

    assert result == ["keep", "end"]


def test_tail_log_discards_partial_line_at_read_boundary(tmp_path: Path) -> None:
    log = tmp_path / "hub.log"
    log.write_bytes(b"old\nkeep\nend\n")

    result = tail_log(log, 10, max_read_bytes=8)

    assert result == ["end"]


def test_tail_log_keeps_utf8_line_within_exact_byte_limit(
    tmp_path: Path,
) -> None:
    log = tmp_path / "hub.log"
    log.write_text("中文中文中文中文\n", encoding="utf-8")

    result = tail_log(log, 1, max_line_bytes=20)

    assert len(result[0].encode("utf-8")) <= 20
    assert "\ufffd" not in result[0]
    assert result[0].endswith("[truncated]")


def test_tail_log_replaces_invalid_utf8_and_redacts_credentials(
    tmp_path: Path,
) -> None:
    log = tmp_path / "hub.log"
    log.write_bytes(
        b"token=known-secret\n"
        b"Authorization: Bearer another-secret\n"
        b"invalid=\xff\n"
    )

    result = tail_log(
        log,
        10,
        sensitive_values=("known-secret",),
    )

    assert result[0] == "token=[REDACTED]"
    assert result[1] == "Authorization: Bearer [REDACTED]"
    assert "\ufffd" in result[2]


def test_tail_log_redacts_before_truncating_long_line(tmp_path: Path) -> None:
    log = tmp_path / "hub.log"
    secret = "secret-crossing-the-truncation-boundary"
    log.write_text(f"prefix={secret}\n", encoding="utf-8")

    result = tail_log(
        log,
        1,
        sensitive_values=(secret,),
        max_line_bytes=20,
    )

    assert "secret-" not in result[0]
    assert len(result[0].encode("utf-8")) <= 20


def test_tail_log_wraps_read_errors(tmp_path: Path) -> None:
    log = tmp_path / "hub.log"
    with patch.object(Path, "open", side_effect=PermissionError):
        with pytest.raises(LogReadError):
            tail_log(log, 10)


def test_log_page_advances_past_a_line_larger_than_read_window(
    tmp_path: Path,
) -> None:
    log = tmp_path / "hub.log"
    log.write_text("x" * (300 * 1024), encoding="utf-8")

    first, first_cursor = read_log_page(log, 500)
    assert len(first) == 1
    assert first[0].endswith("[truncated]")
    assert first_cursor is not None
    assert first_cursor < log.stat().st_size

    second, second_cursor = read_log_page(log, 500, before=first_cursor)
    assert len(second) == 1
    assert second_cursor is None
