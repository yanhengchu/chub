from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
import hashlib
import json
import logging
import os
from pathlib import Path, PurePosixPath
import re
from tempfile import NamedTemporaryFile

from app.services.design_documents import (
    ALLOWED_ATTRIBUTES,
    ALLOWED_TAGS,
    MAX_DOCUMENT_BYTES,
)
import bleach
import markdown


PROJECT_ROOT = Path(__file__).resolve().parents[2]
WEEKLY_REPORTS_ROOT = PROJECT_ROOT / "data" / "artifacts" / "weekly-reports"
MAX_REPORT_LINES = 4_000
MAX_REPORT_LINE_BYTES = 16 * 1024
MAX_CONFIRMATION_BYTES = 64 * 1024
MAX_MANIFEST_BYTES = 64 * 1024
MAX_INPUT_SOURCE_BYTES = 2 * 1024 * 1024
LOGGER = logging.getLogger("hub.weekly_reports")
_PERIOD_PATTERN = re.compile(r"^(\d{4}-\d{2}-\d{2})至(\d{4}-\d{2}-\d{2})$")
_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_USABLE_INPUT_STATUSES = frozenset({"ready", "manually-approved"})
_REPORT_TYPES = {
    "focus": ("本期工作重点确认清单", "本期工作重点确认清单", "重点范围与取舍确认"),
    "report": ("本期业务周报", "本期业务周报", "各端进展汇总"),
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
    path = (output / f"{prefix}-{period}.md").resolve()
    marker = (root / period / ".inputs-updated").resolve()
    if not marker.is_relative_to(root):
        return None
    try:
        if marker.is_file() and (not path.is_file() or marker.stat().st_mtime > path.stat().st_mtime):
            return path.with_name(f".{path.name}.stale")
    except OSError:
        return path
    return path


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


def weekly_report_inputs_available(period: str) -> bool:
    if not _PERIOD_PATTERN.fullmatch(period):
        return False
    root = WEEKLY_REPORTS_ROOT.resolve()
    workspace = (root / period).resolve()
    inputs = (workspace / "inputs").resolve()
    marker = (workspace / ".inputs-updated").resolve()
    manifest_path = (workspace / "manifest.json").resolve()
    if (
        not inputs.is_relative_to(root)
        or not marker.is_relative_to(root)
        or not manifest_path.is_relative_to(root)
    ):
        return False
    try:
        if (
            not inputs.is_dir()
            or not marker.is_file()
            or not any(inputs.iterdir())
            or not manifest_path.is_file()
            or manifest_path.stat().st_size > MAX_MANIFEST_BYTES
        ):
            return False
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        return _manifest_inputs_are_current(manifest, workspace, period)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return False


def _manifest_inputs_are_current(
    manifest: object,
    workspace: Path,
    period: str,
) -> bool:
    if not isinstance(manifest, dict) or manifest.get("version") != 1:
        return False
    if manifest.get("fingerprint") != _manifest_fingerprint(manifest):
        return False
    if not _manifest_period_matches(manifest.get("report_period"), period):
        return False

    source_root = _manifest_source_root(manifest, workspace)
    documents = manifest.get("documents")
    required_roles = manifest.get("required_roles")
    if (
        source_root is None
        or not isinstance(documents, list)
        or not documents
        or not isinstance(required_roles, list)
        or not required_roles
        or not all(isinstance(role, str) and role for role in required_roles)
    ):
        return False

    roles: set[str] = set()
    for document in documents:
        if not isinstance(document, dict):
            return False
        role = document.get("role")
        if not isinstance(role, str) or not role or role in roles:
            return False
        roles.add(role)
        if not _manifest_document_is_current(document, source_root, period):
            return False
    return all(role in roles for role in required_roles)


def _manifest_fingerprint(manifest: dict[str, object]) -> str:
    payload = dict(manifest)
    payload.pop("fingerprint", None)
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode()
    return hashlib.sha256(canonical).hexdigest()


def _manifest_period_matches(value: object, period: str) -> bool:
    if not isinstance(value, dict):
        return False
    match = _PERIOD_PATTERN.fullmatch(period)
    if match is None or value.get("start") != match.group(1) or value.get("end") != match.group(2):
        return False
    if not isinstance(value.get("timezone"), str) or not value["timezone"]:
        return False
    try:
        start = date.fromisoformat(match.group(1))
        end = date.fromisoformat(match.group(2))
    except ValueError:
        return False
    return start.weekday() == 0 and end.weekday() == 6 and (end - start).days == 6


def _manifest_source_root(manifest: dict[str, object], workspace: Path) -> Path | None:
    source_root_value = manifest.get("source_root")
    data_root_value = manifest.get("data_root")
    if not isinstance(source_root_value, str) or not isinstance(data_root_value, str):
        return None
    source_root = (workspace / source_root_value).resolve()
    data_root = (workspace / data_root_value).resolve()
    if (
        not source_root.is_dir()
        or not data_root.is_dir()
        or not source_root.is_relative_to(data_root)
        or source_root != (workspace / "inputs").resolve()
    ):
        return None
    return source_root


def _manifest_document_is_current(
    document: dict[str, object],
    source_root: Path,
    period: str,
) -> bool:
    path_value = document.get("path")
    if not isinstance(path_value, str):
        return False
    pure_path = PurePosixPath(path_value)
    if pure_path.is_absolute() or ".." in pure_path.parts or pure_path.suffix.lower() != ".md":
        return False
    path = (source_root / Path(*pure_path.parts)).resolve()
    if not path.is_relative_to(source_root) or not path.is_file():
        return False
    try:
        source_size = path.stat().st_size
    except OSError:
        return False
    if (
        not isinstance(document.get("file_size"), int)
        or document["file_size"] != source_size
        or source_size > MAX_INPUT_SOURCE_BYTES
        or document.get("download_status") != "succeeded"
        or document.get("content_status") not in _USABLE_INPUT_STATUSES
        or not isinstance(document.get("sha256"), str)
        or not _SHA256_PATTERN.fullmatch(document["sha256"])
        or _digest(path) != document["sha256"]
    ):
        return False
    usage = document.get("usage")
    if not isinstance(usage, dict) or usage.get("mode") not in {
        "reference-only",
        "whole-document",
        "heading-range",
    }:
        return False
    if usage["mode"] == "heading-range" and not isinstance(document.get("resolved_usage"), dict):
        return False
    return usage["mode"] == "reference-only" or _usage_period_matches(
        document.get("usage_period"), period
    )


def _usage_period_matches(value: object, period: str) -> bool:
    if not isinstance(value, dict):
        return False
    start_value = value.get("start")
    end_value = value.get("end")
    if not isinstance(start_value, str) or not isinstance(end_value, str):
        return False
    try:
        start = date.fromisoformat(start_value)
        end = date.fromisoformat(end_value)
        report_end = date.fromisoformat(period.split("至", maxsplit=1)[1])
        report_start = date.fromisoformat(period.split("至", maxsplit=1)[0])
    except ValueError:
        return False
    return start <= end and report_start <= end <= report_end


def _digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(chunk)
    return value.hexdigest()


def _checklist_has_required_sections(checklist_path: Path, manifest: dict[str, object]) -> bool:
    validation = manifest.get("report_validation")
    if validation is None:
        configured: list[object] = []
    elif not isinstance(validation, dict):
        return False
    else:
        configured = validation.get("checklist_required_sections", [])
        if not isinstance(configured, list):
            return False
    required = ["维护者确认结果", *configured]
    if not all(isinstance(value, str) and value for value in required):
        return False
    try:
        checklist_text = checklist_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return False
    headings = re.findall(r"^#{1,6}\s+(.+?)\s*#*\s*$", checklist_text, re.MULTILINE)
    return all(any(section in heading for heading in headings) for section in required)


def weekly_report_focus_confirmed(period: str) -> bool:
    """Return whether the current focus checklist has a usable confirmation record.

    This is a display-level readiness signal. Formal-report generation still runs
    its complete validation immediately before producing an artifact.
    """
    checklist_path = _report_path(period, "focus")
    if checklist_path is None:
        return False
    root = WEEKLY_REPORTS_ROOT.resolve()
    output = checklist_path.parent
    confirmation_path = (output / "weekly-report-confirmation.json").resolve()
    manifest_path = (root / period / "manifest.json").resolve()
    if not confirmation_path.is_relative_to(root) or not manifest_path.is_relative_to(root):
        return False
    try:
        if (
            not checklist_path.is_file()
            or checklist_path.stat().st_size > MAX_DOCUMENT_BYTES
            or not confirmation_path.is_file()
            or confirmation_path.stat().st_size > MAX_CONFIRMATION_BYTES
            or not manifest_path.is_file()
            or manifest_path.stat().st_size > MAX_CONFIRMATION_BYTES
        ):
            return False
        confirmation = json.loads(confirmation_path.read_text(encoding="utf-8"))
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if not isinstance(confirmation, dict) or not isinstance(manifest, dict):
            return False
        checklist = confirmation.get("checklist")
        if (
            confirmation.get("status") != "confirmed"
            or not isinstance(confirmation.get("confirmed_at"), str)
            or not confirmation["confirmed_at"]
            or confirmation.get("manifest_fingerprint") != manifest.get("fingerprint")
            or not isinstance(confirmation.get("decisions"), list)
            or not confirmation["decisions"]
            or not isinstance(checklist, dict)
            or checklist.get("path") != checklist_path.name
        ):
            return False
        digest = hashlib.sha256(checklist_path.read_bytes()).hexdigest()
        return checklist.get("sha256") == digest and _checklist_has_required_sections(
            checklist_path, manifest
        )
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        LOGGER.warning("Unable to inspect weekly report confirmation: period=%s", period)
        return False


def confirm_weekly_report_focus(period: str) -> None:
    """Persist the maintainer's approval of the current focus checklist."""
    if not weekly_report_inputs_available(period):
        raise ValueError("本期资料尚未完整发布或 Manifest 校验未通过。")
    checklist_path = _report_path(period, "focus")
    if checklist_path is None or not checklist_path.is_file():
        raise ValueError("工作重点确认清单尚未生成。")
    root = WEEKLY_REPORTS_ROOT.resolve()
    output = checklist_path.parent
    confirmation_path = (output / "weekly-report-confirmation.json").resolve()
    manifest_path = (root / period / "manifest.json").resolve()
    if not confirmation_path.is_relative_to(root) or not manifest_path.is_relative_to(root):
        raise ValueError("周报确认记录路径无效。")
    try:
        if (
            checklist_path.stat().st_size > MAX_DOCUMENT_BYTES
            or manifest_path.stat().st_size > MAX_CONFIRMATION_BYTES
        ):
            raise ValueError("确认清单或 Manifest 超出允许大小。")
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        fingerprint = manifest.get("fingerprint") if isinstance(manifest, dict) else None
        if not isinstance(fingerprint, str) or not fingerprint:
            raise ValueError("Manifest 指纹无效。")
        if not _checklist_has_required_sections(checklist_path, manifest):
            raise ValueError("工作重点确认清单缺少必需章节。")
        payload = {
            "status": "confirmed",
            "confirmed_at": datetime.now().astimezone().isoformat(),
            "manifest_fingerprint": fingerprint,
            "decisions": ["维护者已确认按当前工作重点确认清单生成正式周报。"],
            "approved_gaps": [],
            "allowed_markers": [],
            "checklist": {
                "path": checklist_path.name,
                "sha256": hashlib.sha256(checklist_path.read_bytes()).hexdigest(),
            },
        }
        output.mkdir(mode=0o700, parents=True, exist_ok=True)
        with NamedTemporaryFile(
            dir=output,
            prefix=f".{confirmation_path.name}.",
            suffix=".tmp",
            delete=False,
        ) as file:
            temporary = Path(file.name)
            file.write(json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8"))
        try:
            os.chmod(temporary, 0o600)
            temporary.replace(confirmation_path)
        except OSError:
            temporary.unlink(missing_ok=True)
            raise
    except ValueError:
        raise
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("维护者确认记录无法写入。") from exc


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
