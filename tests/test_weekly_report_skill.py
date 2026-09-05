from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path


SKILL = Path(".agents/skills/generate-weekly-report")
ADAPTER = SKILL / "scripts/adapt_downloads.py"
VALIDATOR = SKILL / "scripts/validate_weekly_report.py"


def run_script(script: Path, *args: object) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(script), *(str(arg) for arg in args)],
        check=False,
        capture_output=True,
        text=True,
    )


def build_mapping(source: Path, mapping_path: Path) -> None:
    (source / "previous.md").write_text("# 上周周报\n", encoding="utf-8")
    (source / "product.md").write_text(
        "# 产品周报\n\n## **日期：2026\\-7\\-23**\n\n本周内容\n\n"
        "## 日期：2026.7.16\n\n历史内容\n",
        encoding="utf-8",
    )
    mapping_path.write_text(
        json.dumps(
            {
                "version": 1,
                "report_period": {
                    "start": "2026-07-20",
                    "end": "2026-07-26",
                    "timezone": "Asia/Shanghai",
                },
                "required_roles": ["previous-report", "product"],
                "documents": [
                    {
                        "role": "previous-report",
                        "path": "previous.md",
                        "download_status": "succeeded",
                        "content_status": "ready",
                        "usage": {"mode": "reference-only"},
                    },
                    {
                        "role": "product",
                        "path": "product.md",
                        "source_url": "https://tenant.example/docx/product",
                        "download_status": "succeeded",
                        "content_status": "ready",
                        "usage_period": {
                            "start": "2026-07-20",
                            "end": "2026-07-23",
                        },
                        "usage": {
                            "mode": "heading-range",
                            "start_heading": "日期：2026-7-23",
                            "end_heading": "日期：2026-7-16",
                        },
                    },
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


def write_confirmation(
    workspace: Path, fingerprint: str, **overrides: object
) -> Path:
    checklist = workspace / "output" / "本期工作重点确认清单.md"
    checklist.write_text(
        "## 本周需要同步的事项\n\n"
        "## 需要维护者确认的重点事项\n\n"
        "## 维护者确认结果\n\n"
        f"- 输入指纹：{fingerprint}\n",
        encoding="utf-8",
    )
    payload: dict[str, object] = {
        "status": "confirmed",
        "confirmed_at": "2026-07-24T20:00:00+08:00",
        "manifest_fingerprint": fingerprint,
        "decisions": ["按确认重点生成"],
        "approved_gaps": [],
        "allowed_markers": [],
        "checklist": {
            "path": checklist.name,
            "sha256": hashlib.sha256(checklist.read_bytes()).hexdigest(),
        },
    }
    payload.update(overrides)
    confirmation = workspace / "output" / "weekly-report-confirmation.json"
    confirmation.write_text(
        json.dumps(payload, ensure_ascii=False), encoding="utf-8"
    )
    return confirmation


def test_adapter_and_input_validator(tmp_path: Path) -> None:
    data_root = tmp_path / "data"
    source = data_root / "automations" / "downloads" / "weekly"
    workspace = data_root / "weekly-reports" / "2026-07-20至2026-07-24"
    source.mkdir(parents=True)
    mapping = tmp_path / "mapping.json"
    build_mapping(source, mapping)

    adapted = run_script(
        ADAPTER,
        "--data-root",
        data_root,
        "--source-root",
        source,
        "--workspace",
        workspace,
        "--mapping",
        mapping,
    )
    assert adapted.returncode == 0, adapted.stderr
    manifest = workspace / "manifest.json"
    manifest_data = json.loads(manifest.read_text(encoding="utf-8"))
    assert manifest_data["documents"][1]["resolved_usage"]["start"]["line"] == 3
    validated = run_script(VALIDATOR, "inputs", "--manifest", manifest)
    assert validated.returncode == 0, validated.stdout


def test_adapter_rejects_source_period_outside_current_week(tmp_path: Path) -> None:
    data_root = tmp_path / "data"
    source = data_root / "downloads"
    source.mkdir(parents=True)
    mapping = tmp_path / "mapping.json"
    build_mapping(source, mapping)
    data = json.loads(mapping.read_text(encoding="utf-8"))
    data["documents"][1]["usage_period"]["end"] = "2026-07-27"
    mapping.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

    result = run_script(
        ADAPTER,
        "--data-root",
        data_root,
        "--source-root",
        source,
        "--workspace",
        data_root / "weekly-reports" / "period",
        "--mapping",
        mapping,
    )
    assert result.returncode == 1
    assert "usage_period 结束日期不在本期内" in result.stderr


def test_validator_detects_source_change(tmp_path: Path) -> None:
    data_root = tmp_path / "data"
    source = data_root / "downloads"
    workspace = data_root / "weekly-reports" / "period"
    source.mkdir(parents=True)
    mapping = tmp_path / "mapping.json"
    build_mapping(source, mapping)
    assert run_script(
        ADAPTER,
        "--data-root",
        data_root,
        "--source-root",
        source,
        "--workspace",
        workspace,
        "--mapping",
        mapping,
    ).returncode == 0
    (source / "product.md").write_text("# 产品周报\n已变化\n", encoding="utf-8")

    result = run_script(
        VALIDATOR, "inputs", "--manifest", workspace / "manifest.json"
    )
    assert result.returncode == 1
    assert "文件哈希已变化" in result.stdout


def test_report_validator_requires_core_sections(tmp_path: Path) -> None:
    data_root = tmp_path / "data"
    source = data_root / "downloads"
    workspace = data_root / "weekly-reports" / "period"
    source.mkdir(parents=True)
    mapping = tmp_path / "mapping.json"
    build_mapping(source, mapping)
    assert run_script(
        ADAPTER,
        "--data-root",
        data_root,
        "--source-root",
        source,
        "--workspace",
        workspace,
        "--mapping",
        mapping,
    ).returncode == 0
    report = workspace / "output" / "report.md"
    report.write_text("## 业务摘要\n\n- 内容\n", encoding="utf-8")
    manifest = workspace / "manifest.json"
    fingerprint = json.loads(manifest.read_text(encoding="utf-8"))["fingerprint"]
    confirmation = write_confirmation(
        workspace, fingerprint, decisions=["纳入本周重点"]
    )

    result = run_script(
        VALIDATOR,
        "report",
        "--manifest",
        manifest,
        "--confirmation",
        confirmation,
        "--report",
        report,
    )
    assert result.returncode == 1
    assert "各端周报" in result.stdout


def test_report_validator_rejects_stale_confirmation(tmp_path: Path) -> None:
    data_root = tmp_path / "data"
    source = data_root / "downloads"
    workspace = data_root / "weekly-reports" / "period"
    source.mkdir(parents=True)
    mapping = tmp_path / "mapping.json"
    build_mapping(source, mapping)
    assert run_script(
        ADAPTER,
        "--data-root",
        data_root,
        "--source-root",
        source,
        "--workspace",
        workspace,
        "--mapping",
        mapping,
    ).returncode == 0
    confirmation = workspace / "output" / "weekly-report-confirmation.json"
    confirmation.write_text(
        json.dumps(
            {
                "status": "confirmed",
                "confirmed_at": "2026-07-24T20:00:00+08:00",
                "manifest_fingerprint": "stale",
                "decisions": ["纳入本周重点"],
                "approved_gaps": [],
                "allowed_markers": [],
            }
        ),
        encoding="utf-8",
    )
    report = workspace / "output" / "report.md"
    report.write_text(
        "## 业务摘要\n\n- 一\n- 二\n- 三\n- 四\n\n## 各端周报\n",
        encoding="utf-8",
    )

    result = run_script(
        VALIDATOR,
        "report",
        "--manifest",
        workspace / "manifest.json",
        "--confirmation",
        confirmation,
        "--report",
        report,
    )
    assert result.returncode == 1
    assert "确认记录与当前 Manifest 不一致" in result.stdout


def test_adapter_requires_explicit_readiness(tmp_path: Path) -> None:
    data_root = tmp_path / "data"
    source = data_root / "downloads"
    workspace = data_root / "weekly-reports" / "period"
    source.mkdir(parents=True)
    mapping = tmp_path / "mapping.json"
    build_mapping(source, mapping)
    data = json.loads(mapping.read_text(encoding="utf-8"))
    del data["documents"][0]["content_status"]
    mapping.write_text(json.dumps(data), encoding="utf-8")

    result = run_script(
        ADAPTER,
        "--data-root",
        data_root,
        "--source-root",
        source,
        "--workspace",
        workspace,
        "--mapping",
        mapping,
    )
    assert result.returncode == 1
    assert "显式设置有效 content_status" in result.stderr


def test_report_validator_accepts_complete_report(tmp_path: Path) -> None:
    data_root = tmp_path / "data"
    source = data_root / "downloads"
    workspace = data_root / "weekly-reports" / "period"
    source.mkdir(parents=True)
    mapping = tmp_path / "mapping.json"
    build_mapping(source, mapping)
    assert run_script(
        ADAPTER,
        "--data-root",
        data_root,
        "--source-root",
        source,
        "--workspace",
        workspace,
        "--mapping",
        mapping,
    ).returncode == 0
    manifest = workspace / "manifest.json"
    fingerprint = json.loads(manifest.read_text(encoding="utf-8"))["fingerprint"]
    confirmation = write_confirmation(
        workspace,
        fingerprint,
        allowed_markers=["负责人明确保留：口径待确认（等待周一反馈）"],
    )
    report = workspace / "output" / "report.md"
    report.write_text(
        "## 指标\n\n"
        "| 项目 | 说明 |\n|---|---|\n| A | 保留转义竖线 \\| 内容 |\n\n"
        "负责人明确保留：口径待确认（等待周一反馈）\n\n"
        "## 各端周报\n\n"
        "- [产品周报](https://tenant.example/docx/product)\n",
        encoding="utf-8",
    )

    result = run_script(
        VALIDATOR,
        "report",
        "--manifest",
        manifest,
        "--confirmation",
        confirmation,
        "--report",
        report,
    )
    assert result.returncode == 0, result.stdout


def test_report_validator_enforces_profile_template_and_checklist(tmp_path: Path) -> None:
    data_root = tmp_path / "data"
    source = data_root / "downloads"
    workspace = data_root / "weekly-reports" / "period"
    source.mkdir(parents=True)
    mapping = tmp_path / "mapping.json"
    build_mapping(source, mapping)
    data = json.loads(mapping.read_text(encoding="utf-8"))
    data["report_validation"] = {
        "required_sections": ["业务关键指标", "产品体验提升"],
        "required_section_text": {
            "产品体验提升": ["目标：", "当前进展："],
        },
        "checklist_required_sections": ["需要维护者确认的重点事项"],
    }
    mapping.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    assert run_script(
        ADAPTER,
        "--data-root",
        data_root,
        "--source-root",
        source,
        "--workspace",
        workspace,
        "--mapping",
        mapping,
    ).returncode == 0
    manifest = workspace / "manifest.json"
    fingerprint = json.loads(manifest.read_text(encoding="utf-8"))["fingerprint"]
    confirmation = write_confirmation(workspace, fingerprint)
    report = workspace / "output" / "report.md"
    report.write_text(
        "## 产品体验提升\n\n目标：提升体验\n\n## 各端周报\n\n"
        "- [产品周报](https://tenant.example/docx/product)\n",
        encoding="utf-8",
    )

    result = run_script(
        VALIDATOR,
        "report",
        "--manifest",
        manifest,
        "--confirmation",
        confirmation,
        "--report",
        report,
    )
    assert result.returncode == 1
    assert "正式稿缺少章节：业务关键指标" in result.stdout
    assert "产品体验提升 缺少必备内容：当前进展：" in result.stdout

    checklist = workspace / "output" / "本期工作重点确认清单.md"
    checklist.write_text("## 维护者确认结果\n", encoding="utf-8")
    result = run_script(
        VALIDATOR,
        "report",
        "--manifest",
        manifest,
        "--confirmation",
        confirmation,
        "--report",
        report,
    )
    assert result.returncode == 1
    assert "重点清单哈希已变化" in result.stdout


def test_validator_reports_non_object_document(tmp_path: Path) -> None:
    data_root = tmp_path / "data"
    source = data_root / "downloads"
    workspace = data_root / "weekly-reports" / "period"
    source.mkdir(parents=True)
    mapping = tmp_path / "mapping.json"
    build_mapping(source, mapping)
    assert run_script(
        ADAPTER,
        "--data-root",
        data_root,
        "--source-root",
        source,
        "--workspace",
        workspace,
        "--mapping",
        mapping,
    ).returncode == 0
    manifest_path = workspace / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["documents"].append("invalid")
    payload = dict(manifest)
    payload.pop("fingerprint")
    manifest["fingerprint"] = hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True).encode()
    ).hexdigest()
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False), encoding="utf-8"
    )

    result = run_script(VALIDATOR, "inputs", "--manifest", manifest_path)
    assert result.returncode == 1
    assert "documents 每一项都必须是对象" in result.stdout
    assert "Traceback" not in result.stderr
