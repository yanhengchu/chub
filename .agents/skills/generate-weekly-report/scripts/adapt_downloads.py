#!/usr/bin/env python3
"""Create a weekly-report manifest without modifying automation downloads."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import Any

ALLOWED_STATUSES = {
    "ready", "needs-review", "incomplete", "still-editing", "stale",
    "manually-approved",
}
ALLOWED_MODES = {"reference-only", "whole-document", "heading-range"}
ALLOWED_DOWNLOAD_STATUSES = {"succeeded", "failed"}
HEADING = re.compile(r"^(#{1,6})\s+(.+?)\s*$")
DATE = re.compile(r"(?<!\d)(20\d{2})[./年-](\d{1,2})[./月-](\d{1,2})日?")


def fail(message: str) -> None:
    raise ValueError(message)


def relative_file(root: Path, value: str) -> Path:
    pure = PurePosixPath(value)
    if pure.is_absolute() or ".." in pure.parts or not value.endswith(".md"):
        fail(f"不安全的文档路径：{value}")
    target = (root / Path(*pure.parts)).resolve()
    if not target.is_relative_to(root) or not target.is_file():
        fail(f"文档不存在或超出来源目录：{value}")
    return target


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def normalize_heading(value: str) -> str:
    value = re.sub(r"^#{1,6}\s*", "", value.strip())
    value = value.replace("\\", "")
    value = value.replace("*", "").replace("_", "").replace("`", "")
    value = re.sub(r"\s+", " ", value).strip()
    return DATE.sub(
        lambda match: (
            f"{int(match.group(1)):04d}-{int(match.group(2)):02d}-"
            f"{int(match.group(3)):02d}"
        ),
        value,
    )


def resolve_usage(path: Path, usage: dict[str, Any]) -> dict[str, Any] | None:
    if usage.get("mode") != "heading-range":
        return None
    text = path.read_text(encoding="utf-8")
    all_headings = [
        {
            "line": line_no,
            "original": match.group(2),
            "normalized": normalize_heading(match.group(2)),
        }
        for line_no, line in enumerate(text.splitlines(), start=1)
        if (match := HEADING.match(line))
    ]

    def unique_boundary(key: str) -> dict[str, Any]:
        original = usage.get(key)
        if not isinstance(original, str) or not original.strip():
            fail(f"{path.name} 缺少 {key}")
        normalized = normalize_heading(original)
        matches = [
            heading for heading in all_headings
            if heading["normalized"] == normalized
        ]
        if len(matches) != 1:
            fail(f"{path.name} {key} 匹配数量为 {len(matches)}")
        return {
            "configured": original,
            "normalized": normalized,
            "matched_original": matches[0]["original"],
            "line": matches[0]["line"],
        }

    start = unique_boundary("start_heading")
    if usage.get("to_end_of_document") is True:
        return {"mode": "heading-range", "start": start, "end": None}
    end = unique_boundary("end_heading")
    if start["line"] >= end["line"]:
        fail(f"{path.name} 标题边界顺序错误")
    return {"mode": "heading-range", "start": start, "end": end}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-root", required=True, type=Path)
    parser.add_argument("--source-root", required=True, type=Path)
    parser.add_argument("--workspace", required=True, type=Path)
    parser.add_argument("--mapping", required=True, type=Path)
    args = parser.parse_args()

    data_root = args.data_root.resolve()
    source_root = args.source_root.resolve()
    workspace = args.workspace.resolve()
    if not data_root.is_dir() or not source_root.is_relative_to(data_root):
        fail("source_root 必须位于现有 data_root 内")
    if workspace != data_root and not workspace.is_relative_to(data_root):
        fail("workspace 必须位于 data_root 内")
    mapping = json.loads(args.mapping.read_text(encoding="utf-8"))
    if mapping.get("version") != 1:
        fail("mapping.version 必须为 1")
    period = mapping.get("report_period", {})
    for key in ("start", "end", "timezone"):
        if not isinstance(period.get(key), str) or not period[key]:
            fail(f"缺少 report_period.{key}")
    required = mapping.get("required_roles")
    documents = mapping.get("documents")
    if not isinstance(required, list) or not required or not isinstance(documents, list):
        fail("required_roles 和 documents 必须是非空数组")

    output_docs: list[dict[str, Any]] = []
    seen_roles: set[str] = set()
    for item in documents:
        role = item.get("role")
        path_value = item.get("path")
        if not isinstance(role, str) or not role or role in seen_roles:
            fail(f"文档角色无效或重复：{role}")
        if not isinstance(path_value, str):
            fail(f"{role} 缺少 path")
        status = item.get("content_status")
        download_status = item.get("download_status")
        usage = item.get("usage", {})
        if download_status not in ALLOWED_DOWNLOAD_STATUSES:
            fail(f"{role} 必须显式设置有效 download_status")
        if status not in ALLOWED_STATUSES:
            fail(f"{role} 必须显式设置有效 content_status")
        if usage.get("mode") not in ALLOWED_MODES:
            fail(f"{role} usage.mode 无效")
        target = relative_file(source_root, path_value)
        stat = target.stat()
        output = dict(item)
        output["file_size"] = stat.st_size
        output["modified_at"] = datetime.fromtimestamp(
            stat.st_mtime
        ).astimezone().isoformat()
        output["sha256"] = sha256(target)
        resolved_usage = resolve_usage(target, usage)
        if resolved_usage is not None:
            output["resolved_usage"] = resolved_usage
        output_docs.append(output)
        seen_roles.add(role)

    missing = [role for role in required if role not in seen_roles]
    if missing:
        fail("缺少必需角色：" + "、".join(missing))
    workspace.mkdir(parents=True, exist_ok=True)
    output_dir = workspace / "output"
    output_dir.mkdir(exist_ok=True)
    source_relative = os.path.relpath(source_root, workspace)
    manifest: dict[str, Any] = {
        "version": 1,
        "report_period": period,
        "data_root": Path(os.path.relpath(data_root, workspace)).as_posix(),
        "source_root": Path(source_relative).as_posix(),
        "required_roles": required,
        "documents": output_docs,
        "generated_at": datetime.now().astimezone().isoformat(),
    }
    canonical = json.dumps(manifest, ensure_ascii=False, sort_keys=True).encode()
    manifest["fingerprint"] = hashlib.sha256(canonical).hexdigest()
    manifest_path = workspace / "manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(manifest_path)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        raise SystemExit(f"ERROR: {exc}") from exc
