from datetime import date
from pathlib import Path

import app.services.weekly_reports as service


def _write_report(root: Path, period: str, report_type: str, content: str) -> Path:
    prefix = service._REPORT_TYPES[report_type][0]
    path = root / period / "output" / f"{prefix}-{period}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def test_latest_weekly_reports_include_available_and_pending_slots(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(service, "WEEKLY_REPORTS_ROOT", tmp_path)
    _write_report(tmp_path, "2026-07-27至2026-08-02", "focus", "# 本期重点")

    reports = service.list_latest_weekly_reports(today=date(2026, 8, 4))

    assert [report.report_type for report in reports] == ["focus", "report"]
    assert all(report.period == "2026-07-27至2026-08-02" for report in reports)
    assert reports[0].available is True
    assert reports[0].status == "可查看"
    assert reports[1].available is False
    assert reports[1].status == "待生成"


def test_latest_weekly_reports_ignore_invalid_and_reversed_periods(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(service, "WEEKLY_REPORTS_ROOT", tmp_path)
    (tmp_path / "2026-99-99至2026-99-99").mkdir()
    (tmp_path / "2026-08-07至2026-08-03").mkdir()
    _write_report(tmp_path, "2026-07-27至2026-08-02", "focus", "# 本期重点")

    reports = service.list_latest_weekly_reports(today=date(2026, 8, 4))

    assert reports[0].period == "2026-07-27至2026-08-02"


def test_latest_weekly_reports_degrade_when_file_inspection_fails(
    monkeypatch,
) -> None:
    class UnreadablePath:
        def is_file(self) -> bool:
            raise OSError("unavailable")

    monkeypatch.setattr(service, "_report_path", lambda *_: UnreadablePath())

    reports = service.list_latest_weekly_reports(today=date(2026, 8, 4))

    assert len(reports) == 2
    assert all(report.available is False for report in reports)
    assert all(report.status == "待生成" for report in reports)


def test_weekly_reports_switch_on_wednesday_and_remain_stable_through_tuesday(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(service, "WEEKLY_REPORTS_ROOT", tmp_path)
    _write_report(tmp_path, "2026-07-27至2026-08-02", "focus", "# 上期重点")
    _write_report(tmp_path, "2026-08-03至2026-08-09", "focus", "# 本期重点")

    before_switch = service.list_latest_weekly_reports(today=date(2026, 8, 4))
    at_switch = service.list_latest_weekly_reports(today=date(2026, 8, 5))
    before_handover = service.list_latest_weekly_reports(today=date(2026, 8, 11))

    assert before_switch[0].period == "2026-07-27至2026-08-02"
    assert at_switch[0].period == "2026-08-03至2026-08-09"
    assert before_handover[0].period == "2026-08-03至2026-08-09"


def test_reporting_period_uses_current_week_after_wednesday() -> None:
    assert service.reporting_period(date(2026, 8, 4)) == "2026-07-27至2026-08-02"
    assert service.reporting_period(date(2026, 8, 5)) == "2026-08-03至2026-08-09"
    assert service.reporting_period(date(2026, 8, 11)) == "2026-08-03至2026-08-09"
    assert service.reporting_period(date(2026, 8, 12)) == "2026-08-10至2026-08-16"


def test_weekly_report_detail_keeps_legacy_output_files_readable(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(service, "WEEKLY_REPORTS_ROOT", tmp_path)
    period = "2026-08-03至2026-08-09"
    output = tmp_path / period / "output"
    output.mkdir(parents=True)
    (output / f"本周工作重点确认清单-{period}.md").write_text(
        "# 本期重点确认",
        encoding="utf-8",
    )

    report = service.get_weekly_report(period, "focus")

    assert report is not None
    assert report.title == "本期重点确认"


def test_weekly_report_detail_renders_sanitized_markdown(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(service, "WEEKLY_REPORTS_ROOT", tmp_path)
    _write_report(
        tmp_path,
        "2026-07-27至2026-07-31",
        "focus",
        "# 本周重点\n\n<script>alert('x')</script>\n\n[外链](javascript:alert(1))",
    )

    report = service.get_weekly_report("2026-07-27至2026-07-31", "focus")

    assert report is not None
    assert "<h1" in report.html
    assert "<script" not in report.html
    assert "javascript:" not in report.html
    assert service.get_weekly_report("../../etc", "focus") is None
    assert service.get_weekly_report("2026-07-27至2026-07-31", "unknown") is None


def test_weekly_report_detail_rejects_oversized_file(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(service, "WEEKLY_REPORTS_ROOT", tmp_path)
    path = _write_report(
        tmp_path,
        "2026-07-27至2026-07-31",
        "report",
        "# 周报",
    )
    path.write_bytes(b"a" * (service.MAX_DOCUMENT_BYTES + 1))

    assert service.get_weekly_report("2026-07-27至2026-07-31", "report") is None


def test_weekly_report_detail_rejects_too_many_lines_and_long_line(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(service, "WEEKLY_REPORTS_ROOT", tmp_path)
    period = "2026-07-27至2026-07-31"
    path = _write_report(tmp_path, period, "focus", "line\n")
    path.write_text("x\n" * (service.MAX_REPORT_LINES + 1), encoding="utf-8")
    assert service.get_weekly_report(period, "focus") is None

    path.write_text("x" * (service.MAX_REPORT_LINE_BYTES + 1), encoding="utf-8")
    assert service.get_weekly_report(period, "focus") is None


def test_weekly_report_detail_rejects_non_utf8_content(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(service, "WEEKLY_REPORTS_ROOT", tmp_path)
    period = "2026-07-27至2026-07-31"
    path = _write_report(tmp_path, period, "focus", "# 周报")
    path.write_bytes(b"\xff\xfe")

    assert service.get_weekly_report(period, "focus") is None
