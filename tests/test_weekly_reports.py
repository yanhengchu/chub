from datetime import date
import hashlib
import json
from pathlib import Path

import pytest

import app.services.weekly_reports as service


def _write_report(root: Path, period: str, report_type: str, content: str) -> Path:
    prefix = service._REPORT_TYPES[report_type][0]
    path = root / period / "output" / f"{prefix}-{period}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def _write_ready_inputs_manifest(root: Path, period: str) -> Path:
    workspace = root / period
    source = workspace / "inputs" / "linked" / "product.md"
    source.parent.mkdir(parents=True)
    source.write_text("# 产品周报\n\n本期资料\n", encoding="utf-8")
    (workspace / ".inputs-updated").write_text("updated", encoding="utf-8")
    manifest = {
        "version": 1,
        "report_period": {
            "start": "2026-08-03",
            "end": "2026-08-09",
            "timezone": "Asia/Shanghai",
        },
        "data_root": "..",
        "source_root": "inputs",
        "required_roles": ["product"],
        "documents": [
            {
                "role": "product",
                "path": "linked/product.md",
                "download_status": "succeeded",
                "content_status": "ready",
                "usage_period": {"start": "2026-08-03", "end": "2026-08-09"},
                "usage": {"mode": "whole-document"},
                "file_size": source.stat().st_size,
                "sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
            }
        ],
    }
    manifest["fingerprint"] = service._manifest_fingerprint(manifest)
    manifest_path = workspace / "manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    return source


def test_weekly_report_inputs_require_current_manifest_and_source_hashes(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(service, "WEEKLY_REPORTS_ROOT", tmp_path)
    period = "2026-08-03至2026-08-09"
    workspace = tmp_path / period
    inputs = workspace / "inputs"
    inputs.mkdir(parents=True)
    (workspace / ".inputs-updated").write_text("updated", encoding="utf-8")

    assert service.weekly_report_inputs_available(period) is False

    source = _write_ready_inputs_manifest(tmp_path, period)
    assert service.weekly_report_inputs_available(period) is True

    source.write_text("# 产品周报\n\n内容已变化\n", encoding="utf-8")
    assert service.weekly_report_inputs_available(period) is False


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
    assert reports[0].title == "本期工作重点确认清单"
    assert reports[0].status == "可查看"
    assert reports[1].available is False
    assert reports[1].title == "本期业务周报"
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


def test_weekly_report_focus_confirmation_requires_current_checklist_and_manifest(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(service, "WEEKLY_REPORTS_ROOT", tmp_path)
    period = "2026-07-27至2026-08-02"
    checklist = _write_report(tmp_path, period, "focus", "## 维护者确认结果\n")
    (tmp_path / period / "manifest.json").write_text(
        json.dumps({"fingerprint": "manifest-fingerprint"}), encoding="utf-8"
    )
    (tmp_path / period / "output" / "weekly-report-confirmation.json").write_text(
        json.dumps(
            {
                "status": "confirmed",
                "confirmed_at": "2026-08-03T10:00:00+08:00",
                "manifest_fingerprint": "manifest-fingerprint",
                "decisions": ["纳入重点"],
                "checklist": {
                    "path": checklist.name,
                    "sha256": hashlib.sha256(checklist.read_bytes()).hexdigest(),
                },
            }
        ),
        encoding="utf-8",
    )

    assert service.weekly_report_focus_confirmed(period) is True

    checklist.write_text("# 已变更的重点", encoding="utf-8")

    assert service.weekly_report_focus_confirmed(period) is False


def test_maintainer_confirmation_writes_current_confirmation_record(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(service, "WEEKLY_REPORTS_ROOT", tmp_path)
    period = "2026-08-03至2026-08-09"
    _write_ready_inputs_manifest(tmp_path, period)
    _write_report(tmp_path, period, "focus", "## 维护者确认结果\n")

    service.confirm_weekly_report_focus(period)

    confirmation = json.loads(
        (tmp_path / period / "output" / "weekly-report-confirmation.json").read_text(
            encoding="utf-8"
        )
    )
    assert confirmation["status"] == "confirmed"
    assert confirmation["decisions"] == ["维护者已确认按当前工作重点确认清单生成正式周报。"]
    assert service.weekly_report_focus_confirmed(period) is True


def test_weekly_report_confirmation_requires_configured_checklist_sections(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(service, "WEEKLY_REPORTS_ROOT", tmp_path)
    period = "2026-08-03至2026-08-09"
    _write_ready_inputs_manifest(tmp_path, period)
    manifest_path = tmp_path / period / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["report_validation"] = {
        "checklist_required_sections": [
            "本周需要同步的事项",
            "需要维护者确认的重点事项",
        ]
    }
    manifest["fingerprint"] = service._manifest_fingerprint(manifest)
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    _write_report(tmp_path, period, "focus", "## 维护者确认结果\n")

    with pytest.raises(ValueError, match="缺少必需章节"):
        service.confirm_weekly_report_focus(period)

    _write_report(
        tmp_path,
        period,
        "focus",
        "## 本周需要同步的事项\n\n"
        "## 需要维护者确认的重点事项\n\n"
        "## 维护者确认结果\n",
    )
    service.confirm_weekly_report_focus(period)

    assert service.weekly_report_focus_confirmed(period) is True


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


def test_weekly_report_detail_ignores_legacy_output_files(
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

    assert report is None


def test_weekly_report_is_pending_after_inputs_are_updated(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(service, "WEEKLY_REPORTS_ROOT", tmp_path)
    period = "2026-08-03至2026-08-09"
    _write_report(tmp_path, period, "focus", "# 本期重点确认")
    marker = tmp_path / period / ".inputs-updated"
    marker.write_text("updated", encoding="utf-8")
    marker.touch()

    report = service.get_weekly_report(period, "focus")

    assert report is None


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
