from __future__ import annotations

from datetime import date
from pathlib import Path
import re


MAX_VALIDATION_BYTES = 2 * 1024 * 1024
MAX_VALIDATION_LINES = 240
MAX_PERIOD_DECLARATION_LINES = 20
_DATE_PATTERN = re.compile(
    r"(?<!\d)(20\d{2})[./-](\d{1,2})[./-](\d{1,2})(?!\d)"
)
_HEADING_PATTERN = re.compile(r"^#{1,6}\s+(.+?)\s*#*\s*$")
_PERIOD_DECLARATION_PATTERN = re.compile(r"^(?:日期|统计周期|汇报周期|周期)\s*[：:]")
_MARKDOWN_FORMATTING_PATTERN = re.compile(r"[*_`]")


class WeeklyValidationError(Exception):
    pass


def _period_dates(period: str) -> tuple[date, date]:
    try:
        start_text, end_text = period.split("至", maxsplit=1)
        return date.fromisoformat(start_text), date.fromisoformat(end_text)
    except ValueError as exc:
        raise WeeklyValidationError("本期周期格式无效") from exc


def _leading_lines(path: Path) -> list[str]:
    try:
        if path.stat().st_size > MAX_VALIDATION_BYTES:
            raise WeeklyValidationError("文档超过周报校验大小限制")
        with path.open("r", encoding="utf-8-sig") as file:
            lines = []
            for line_number, line in enumerate(file, start=1):
                if line_number > MAX_VALIDATION_LINES:
                    break
                lines.append(line)
    except WeeklyValidationError:
        raise
    except (OSError, UnicodeError) as exc:
        raise WeeklyValidationError("文档无法读取，不能进行本期校验") from exc
    return lines


def _heading_dates(line: str) -> list[date]:
    normalized = line.replace("\\-", "-").replace("\\.", ".")
    dates = []
    for year, month, day in _DATE_PATTERN.findall(normalized):
        try:
            dates.append(date(int(year), int(month), int(day)))
        except ValueError:
            continue
    return dates


def _leading_period_dates(lines: list[str]) -> list[date] | None:
    first_h1_seen = False
    for line in lines[:MAX_PERIOD_DECLARATION_LINES]:
        match = _HEADING_PATTERN.match(line.strip())
        if not match:
            continue
        heading = _MARKDOWN_FORMATTING_PATTERN.sub("", match.group(1)).strip()
        dates = _heading_dates(heading)
        is_h1 = match.group(0).startswith("# ")
        if is_h1:
            if first_h1_seen:
                continue
            first_h1_seen = True
            if dates:
                return dates
            continue
        if _PERIOD_DECLARATION_PATTERN.match(heading):
            return dates or None
    return None


def _contains_current_period_declaration(lines: list[str], period: str) -> bool:
    start, end = _period_dates(period)
    dates = _leading_period_dates(lines)
    if not dates:
        return False
    if len(dates) == 1:
        return start <= dates[0] <= end
    return dates[0] <= dates[-1] and start <= dates[-1] <= end


def _contains_heading(lines: list[str], expected: str) -> bool:
    return any(
        (match := _HEADING_PATTERN.match(line.strip()))
        and match.group(1).strip() == expected
        for line in lines
    )


def validate_weekly_main_document(path: Path, linked_section: str) -> None:
    lines = _leading_lines(path)
    if not _contains_heading(lines, linked_section):
        raise WeeklyValidationError(f"主文档缺少“{linked_section}”章节")


def validate_weekly_linked_document(path: Path, period: str) -> None:
    lines = _leading_lines(path)
    if not any(_HEADING_PATTERN.match(line.strip()) for line in lines):
        raise WeeklyValidationError("关联文档缺少 Markdown 标题")
    if not _contains_current_period_declaration(lines, period):
        raise WeeklyValidationError(f"未检测到本期（{period}）有效周期标题")
