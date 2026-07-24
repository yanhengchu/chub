#!/usr/bin/env python3
"""Deterministic validation for weekly-report inputs and formal Markdown."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path, PurePosixPath
from typing import Any

PASS_STATUSES = {"ready", "manually-approved"}
HEADING = re.compile(r"^(#{1,6})\s+(.+?)\s*$")
DATE = re.compile(r"(?<!\d)(20\d{2})[./年-](\d{1,2})[./月-](\d{1,2})日?")


def normalize_heading(value: str) -> str:
    value = value.strip()
    value = re.sub(r"^#{1,6}\s*", "", value)
    value = value.replace("\\", "")
    value = value.replace("*", "").replace("_", "").replace("`", "")
    value = re.sub(r"\s+", " ", value).strip()

    def date_value(match: re.Match[str]) -> str:
        return f"{int(match.group(1)):04d}-{int(match.group(2)):02d}-{int(match.group(3)):02d}"

    return DATE.sub(date_value, value)


def headings(text: str) -> list[tuple[int, str]]:
    return [
        (line_no, normalize_heading(match.group(2)))
        for line_no, line in enumerate(text.splitlines(), start=1)
        if (match := HEADING.match(line))
    ]


def safe_source(manifest_path: Path, source_root_value: Any) -> Path:
    if not isinstance(source_root_value, str):
        raise ValueError("manifest 缺少 source_root")
    root = (manifest_path.parent / source_root_value).resolve()
    if not root.is_dir():
        raise ValueError("source_root 不存在")
    data_root_value = json.loads(manifest_path.read_text(encoding="utf-8")).get(
        "data_root"
    )
    if not isinstance(data_root_value, str):
        raise ValueError("manifest 缺少 data_root")
    data_root = (manifest_path.parent / data_root_value).resolve()
    if not data_root.is_dir() or not root.is_relative_to(data_root):
        raise ValueError("source_root 超出 data_root")
    return root


def safe_document(root: Path, value: Any) -> Path:
    if not isinstance(value, str):
        raise ValueError("文档缺少 path")
    pure = PurePosixPath(value)
    if pure.is_absolute() or ".." in pure.parts or pure.suffix.lower() != ".md":
        raise ValueError(f"不安全的文档路径：{value}")
    target = (root / Path(*pure.parts)).resolve()
    if not target.is_relative_to(root) or not target.is_file():
        raise ValueError(f"文档不存在或超出来源目录：{value}")
    return target


def digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(chunk)
    return value.hexdigest()


def manifest_fingerprint(manifest: dict[str, Any]) -> str:
    payload = dict(manifest)
    payload.pop("fingerprint", None)
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode()
    return hashlib.sha256(canonical).hexdigest()


def load_and_validate(manifest_path: Path) -> tuple[dict[str, Any], list[str]]:
    errors: list[str] = []
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if not isinstance(manifest, dict):
        raise ValueError("Manifest 根节点必须是对象")
    if manifest.get("version") != 1:
        errors.append("manifest.version 必须为 1")
    if manifest.get("fingerprint") != manifest_fingerprint(manifest):
        errors.append("Manifest fingerprint 无效或内容已被修改")
    root = safe_source(manifest_path, manifest.get("source_root"))
    documents = manifest.get("documents")
    required = manifest.get("required_roles")
    if not isinstance(documents, list) or not isinstance(required, list):
        return manifest, errors + ["documents 或 required_roles 格式错误"]
    if not all(isinstance(role, str) and role for role in required):
        errors.append("required_roles 必须只包含非空字符串")
    roles = [item.get("role") for item in documents if isinstance(item, dict)]
    for role in required:
        if role not in roles:
            errors.append(f"缺少必需角色：{role}")
    if len(roles) != len(set(roles)):
        errors.append("文档角色重复")

    for item in documents:
        if not isinstance(item, dict):
            errors.append("documents 每一项都必须是对象")
            continue
        role = item.get("role", "<unknown>")
        try:
            target = safe_document(root, item.get("path"))
            text = target.read_text(encoding="utf-8")
        except (OSError, UnicodeError, ValueError) as exc:
            errors.append(f"{role}: {exc}")
            continue
        if not text.strip():
            errors.append(f"{role}: 文件为空")
        if item.get("download_status") != "succeeded":
            errors.append(f"{role}: 下载未成功")
        if item.get("content_status") not in PASS_STATUSES:
            errors.append(f"{role}: 内容状态阻塞：{item.get('content_status')}")
        expected = item.get("sha256")
        if not isinstance(expected, str) or digest(target) != expected:
            errors.append(f"{role}: 文件哈希已变化")
        usage = item.get("usage", {})
        mode = usage.get("mode")
        if mode not in {"reference-only", "whole-document", "heading-range"}:
            errors.append(f"{role}: usage.mode 无效")
            continue
        if mode == "heading-range":
            all_headings = headings(text)
            start = normalize_heading(str(usage.get("start_heading", "")))
            starts = [line for line, title in all_headings if title == start]
            if len(starts) != 1:
                errors.append(f"{role}: 开始标题匹配数量为 {len(starts)}")
                continue
            if usage.get("to_end_of_document") is True:
                continue
            end = normalize_heading(str(usage.get("end_heading", "")))
            ends = [line for line, title in all_headings if title == end]
            if len(ends) != 1:
                errors.append(f"{role}: 结束标题匹配数量为 {len(ends)}")
            elif starts[0] >= ends[0]:
                errors.append(f"{role}: 标题边界顺序错误")
            resolved = item.get("resolved_usage")
            if not isinstance(resolved, dict):
                errors.append(f"{role}: 缺少 resolved_usage 边界记录")
    return manifest, errors


def load_confirmation(
    path: Path, manifest: dict[str, Any]
) -> tuple[dict[str, Any], list[str]]:
    errors: list[str] = []
    confirmation = json.loads(path.read_text(encoding="utf-8"))
    if confirmation.get("status") != "confirmed":
        errors.append("重点清单尚未确认")
    if not isinstance(confirmation.get("confirmed_at"), str):
        errors.append("确认记录缺少 confirmed_at")
    if confirmation.get("manifest_fingerprint") != manifest.get("fingerprint"):
        errors.append("确认记录与当前 Manifest 不一致")
    if not isinstance(confirmation.get("decisions"), list) or not confirmation[
        "decisions"
    ]:
        errors.append("确认记录缺少 decisions")
    for key in ("approved_gaps", "allowed_markers"):
        if not isinstance(confirmation.get(key), list):
            errors.append(f"确认记录缺少 {key}")
    return confirmation, errors


def unescaped_pipe_count(line: str) -> int:
    count = 0
    escaped = False
    for character in line:
        if character == "\\" and not escaped:
            escaped = True
            continue
        if character == "|" and not escaped:
            count += 1
        escaped = False
    return count


def validate_report(
    manifest: dict[str, Any],
    confirmation: dict[str, Any],
    report: Path,
) -> list[str]:
    errors: list[str] = []
    text = report.read_text(encoding="utf-8")
    titles = [title for _, title in headings(text)]
    for required in ("业务摘要", "各端周报"):
        if not any(required in title for title in titles):
            errors.append(f"正式稿缺少章节：{required}")
    allowed_markers = confirmation.get("allowed_markers", [])
    text_without_allowed_markers = text
    for allowed_marker in allowed_markers:
        if isinstance(allowed_marker, str) and allowed_marker:
            text_without_allowed_markers = text_without_allowed_markers.replace(
                allowed_marker, ""
            )
    for marker in ("待核对", "口径待确认"):
        if marker in text_without_allowed_markers:
            errors.append(f"正式稿残留未处理标记：{marker}")
    source_urls = list(dict.fromkeys(
        item.get("source_url")
        for item in manifest.get("documents", [])
        if item.get("source_url") and item.get("role") != "previous-report"
    ))
    source_heading_lines = [
        line for line, title in headings(text) if "各端周报" in title
    ]
    source_section_line = source_heading_lines[-1] if source_heading_lines else None
    report_lines = text.splitlines()
    for url in source_urls:
        occurrences = [
            line_no
            for line_no, line in enumerate(report_lines, start=1)
            if url in line
        ]
        if not occurrences:
            errors.append(f"正式稿缺少来源链接：{url}")
        elif source_section_line and any(
            line_no <= source_section_line for line_no in occurrences
        ):
            errors.append(f"来源链接未统一放在各端周报章节：{url}")

    summary_lines = [
        line for line, title in headings(text) if "业务摘要" in title
    ]
    if summary_lines:
        start = summary_lines[0]
        following = [line for line, _ in headings(text) if line > start]
        end = following[0] if following else len(report_lines) + 1
        item_count = sum(
            bool(re.match(r"^\s*(?:[-*+]|\d+[.)、])\s+", line))
            for line in report_lines[start:end - 1]
        )
        if not 4 <= item_count <= 6:
            errors.append(f"业务摘要应为 4—6 条，当前为 {item_count} 条")
    expected_cells: int | None = None
    previous_table_line = 0
    for line_no, line in enumerate(text.splitlines(), start=1):
        if line.startswith("|") and line.endswith("|"):
            cells = unescaped_pipe_count(line) - 1
            if cells < 2:
                errors.append(f"第 {line_no} 行 Markdown 表格列数无效")
            if previous_table_line != line_no - 1:
                expected_cells = cells
            elif expected_cells != cells:
                errors.append(
                    f"第 {line_no} 行 Markdown 表格列数为 {cells}，"
                    f"应为 {expected_cells}"
                )
            previous_table_line = line_no
        else:
            expected_cells = None
            previous_table_line = 0
    return errors


def main() -> int:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    inputs = subparsers.add_parser("inputs")
    inputs.add_argument("--manifest", required=True, type=Path)
    report = subparsers.add_parser("report")
    report.add_argument("--manifest", required=True, type=Path)
    report.add_argument("--confirmation", required=True, type=Path)
    report.add_argument("--report", required=True, type=Path)
    args = parser.parse_args()

    try:
        manifest, errors = load_and_validate(args.manifest.resolve())
        if args.command == "report" and not errors:
            confirmation, confirmation_errors = load_confirmation(
                args.confirmation.resolve(), manifest
            )
            errors.extend(confirmation_errors)
            if not confirmation_errors:
                errors.extend(
                    validate_report(manifest, confirmation, args.report.resolve())
                )
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError) as exc:
        errors = [str(exc)]
    if errors:
        for error in errors:
            print(f"ERROR: {error}")
        return 1
    print("OK: 校验通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
