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
    "focus": ("本周工作重点确认清单", "本周重点确认", "重点范围与取舍确认"),
    "report": ("本周业务周报", "本周汇总周报", "各端进展汇总"),
}


def _today() -> date:
    return datetime.now().astimezone().date()


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


def _period_directories() -> list[Path]:
    try:
        values = list(WEEKLY_REPORTS_ROOT.iterdir())
    except (FileNotFoundError, OSError):
        return []
    periods = []
    for path in values:
        match = _PERIOD_PATTERN.fullmatch(path.name)
        if match is None:
            continue
        try:
            start = datetime.strptime(match.group(1), "%Y-%m-%d").date()
            end = datetime.strptime(match.group(2), "%Y-%m-%d").date()
            if start > end or not path.is_dir():
                continue
        except (OSError, ValueError):
            continue
        periods.append(path)
    return sorted(
        periods,
        key=lambda path: path.name,
        reverse=True,
    )


def _report_path(period: str, report_type: str) -> Path | None:
    if not _PERIOD_PATTERN.fullmatch(period) or report_type not in _REPORT_TYPES:
        return None
    prefix, _, _ = _REPORT_TYPES[report_type]
    root = WEEKLY_REPORTS_ROOT.resolve()
    path = (root / period / "output" / f"{prefix}-{period}.md").resolve()
    if not path.is_relative_to(root):
        return None
    return path


def _activation_date(period: str) -> date | None:
    match = _PERIOD_PATTERN.fullmatch(period)
    if match is None:
        return None
    try:
        end = datetime.strptime(match.group(2), "%Y-%m-%d").date()
    except ValueError:
        return None
    days_until_tuesday = (1 - end.weekday()) % 7
    if days_until_tuesday == 0:
        days_until_tuesday = 7
    return end + timedelta(days=days_until_tuesday)


def list_latest_weekly_reports(*, today: date | None = None) -> list[WeeklyReportView]:
    effective_date = today or _today()
    periods = [
        path
        for path in _period_directories()
        if (_activation_date(path.name) or date.max) <= effective_date
    ]
    if not periods:
        return []
    period = periods[0].name
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
