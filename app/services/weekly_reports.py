from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
import logging
from pathlib import Path
import re

from app.services.design_documents import (
    ALLOWED_ATTRIBUTES,
    ALLOWED_TAGS,
    MAX_DOCUMENT_BYTES,
)
import bleach
import markdown


PROJECT_ROOT = Path(__file__).resolve().parents[2]
WEEKLY_REPORTS_ROOT = PROJECT_ROOT / "data" / "weekly-reports"
MAX_REPORT_LINES = 4_000
MAX_REPORT_LINE_BYTES = 16 * 1024
LOGGER = logging.getLogger("hub.weekly_reports")
_PERIOD_PATTERN = re.compile(r"^(\d{4}-\d{2}-\d{2})至(\d{4}-\d{2}-\d{2})$")
_REPORT_TYPES = {
    "focus": ("本期工作重点确认清单", "本期重点确认", "重点范围与取舍确认"),
    "report": ("本期业务周报", "本期周报", "各端进展汇总"),
}
_LEGACY_REPORT_PREFIXES = {
    "focus": "本周工作重点确认清单",
    "report": "本周业务周报",
}


def _today() -> date:
    return datetime.now().astimezone().date()


def reporting_period(today: date | None = None) -> str:
    """Return the current period from Wednesday through next Tuesday.

    A period covers the Monday-through-Sunday week in which its processing
    window opens on Wednesday. It remains current through the following
    Tuesday, the fixed reporting day.
    """
    value = today or _today()
    current_monday = value - timedelta(days=value.weekday())
    start = current_monday if value.weekday() >= 2 else current_monday - timedelta(days=7)
    end = start + timedelta(days=6)
    return f"{start:%Y-%m-%d}至{end:%Y-%m-%d}"


@dataclass(frozen=True)
class WeeklyReportView:
    period: str
    report_type: str
    title: str
    summary: str
    status: str
    updated_at: datetime | None
    available: bool
    html: str | None = None


def _report_path(period: str, report_type: str) -> Path | None:
    if not _PERIOD_PATTERN.fullmatch(period) or report_type not in _REPORT_TYPES:
        return None
    prefix, _, _ = _REPORT_TYPES[report_type]
    root = WEEKLY_REPORTS_ROOT.resolve()
    output = (root / period / "output").resolve()
    if not output.is_relative_to(root):
        return None
    for candidate_prefix in (prefix, _LEGACY_REPORT_PREFIXES[report_type]):
        path = (output / f"{candidate_prefix}-{period}.md").resolve()
        if not path.is_relative_to(root):
            return None
        try:
            if path.is_file():
                return path
        except OSError:
            return path
    return (output / f"{prefix}-{period}.md").resolve()


def list_latest_weekly_reports(*, today: date | None = None) -> list[WeeklyReportView]:
    period = reporting_period(today)
    reports = []
    for report_type, (_, title, summary) in _REPORT_TYPES.items():
        path = _report_path(period, report_type)
        updated_at = None
        available = False
        if path is not None:
            try:
                if path.is_file() and path.stat().st_size <= MAX_DOCUMENT_BYTES:
                    updated_at = datetime.fromtimestamp(path.stat().st_mtime)
                    available = True
            except OSError:
                LOGGER.warning(
                    "Unable to inspect weekly report: period=%s type=%s",
                    period,
                    report_type,
                )
        reports.append(
            WeeklyReportView(
                period=period,
                report_type=report_type,
                title=title,
                summary=summary,
                status="可查看" if available else "待生成",
                updated_at=updated_at,
                available=available,
            )
        )
    return reports


def get_weekly_report(period: str, report_type: str) -> WeeklyReportView | None:
    path = _report_path(period, report_type)
    if path is None:
        return None
    try:
        file_size = path.stat().st_size
        if not path.is_file() or file_size > MAX_DOCUMENT_BYTES:
            return None
        lines = []
        total_bytes = 0
        with path.open("r", encoding="utf-8") as file:
            for line_number, line in enumerate(file, start=1):
                if line_number > MAX_REPORT_LINES:
                    return None
                line_bytes = len(line.encode("utf-8"))
                if line_bytes > MAX_REPORT_LINE_BYTES:
                    return None
                total_bytes += line_bytes
                if total_bytes > MAX_DOCUMENT_BYTES:
                    return None
                lines.append(line)
        source = "".join(lines)
        updated_at = datetime.fromtimestamp(path.stat().st_mtime)
    except (OSError, UnicodeDecodeError):
        LOGGER.warning(
            "Unable to read weekly report: period=%s type=%s",
            period,
            report_type,
        )
        return None
    rendered = markdown.markdown(
        source,
        extensions=["fenced_code", "tables", "toc"],
        output_format="html",
    )
    cleaned = bleach.clean(
        rendered,
        tags=ALLOWED_TAGS,
        attributes=ALLOWED_ATTRIBUTES,
        protocols={"http", "https", "mailto"},
        strip=True,
    )
    _, title, summary = _REPORT_TYPES[report_type]
    return WeeklyReportView(
        period=period,
        report_type=report_type,
        title=title,
        summary=summary,
        status="可查看",
        updated_at=updated_at,
        available=True,
        html=cleaned,
    )
