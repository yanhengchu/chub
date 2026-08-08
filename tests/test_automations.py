from __future__ import annotations

import asyncio
import json
import os
import threading
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from app.automations.config import (
    load_automations,
    load_linked_documents_extension,
)
from app.automations.extensions import (
    ExtensionFailed,
    extract_linked_documents,
    linked_filename,
)
from app.automations.lock import file_lock
from app.automations.manager import AutomationManager, _feishu_environment_for_url
from app.automations.browser import BrowserProfileInfo
from app.automations.models import (
    AutomationState,
    FeishuEnvironmentState,
    LinkedDocumentResult,
)
from app.automations.operations import log_final_operation
from app.automations.runner import (
    AutomationFailed,
    _check_navigation,
    _publish_weekly_inputs,
    _prune_linked_markdown_files,
    _output_path,
    _run_linked_documents,
    _run_browser_task,
    _validate_download,
    _weekly_report_download_task,
    _weekly_report_input_root,
    run_automation,
)
from app.automations.weekly_validation import (
    WeeklyValidationError,
    validate_weekly_linked_document,
)
import app.automations.runner as runner
from app.automations.store import AutomationStateStore
from app.core.config import Settings
from app.core.response import ApiError
from app.services.weekly_reports import reporting_period


def automation_data(**overrides) -> dict:
    task = {
        "name": "月度报表下载",
        "description": "测试任务",
        "enabled": True,
        "browser": {
            "session": "debug-chrome",
            "start_url": "https://example.com/reports",
            "allowed_hosts": ["example.com"],
        },
        "login": {"check": {"selector": "#user"}},
        "steps": [
            {
                "action": "click",
                "selector": "#download",
                "expect": "download",
            }
        ],
        "output": {
            "directory": "monthly",
            "filename": "report-{date:%Y-%m}.pdf",
            "conflict": "replace",
            "timezone": "Asia/Shanghai",
        },
        "validation": {
            "non_empty": True,
            "extensions": [".pdf"],
            "min_bytes": 5,
            "signature": "pdf",
        },
        "execution": {
            "timeout_ms": 10_000,
            "lock_timeout_ms": 0,
            "safe_step_retries": 0,
        },
    }
    task.update(overrides)
    return {"version": 1, "tasks": {"monthly-report": task}}


def configure_automations(settings: Settings, tmp_path: Path) -> Path:
    config_file = tmp_path / "automations.yaml"
    config_file.write_text(
        __import__("yaml").safe_dump(automation_data(), allow_unicode=True),
        encoding="utf-8",
    )
    settings.automations.config_file = config_file
    settings.automations.state_dir = tmp_path / "state"
    settings.automations.runtime_dir = tmp_path / "runtime"
    settings.automations.artifacts_dir = tmp_path / "artifacts"
    return config_file


def linked_documents_template(*, required_current_documents: int = 1):
    template = load_linked_documents_extension("v-weekly-report-linked-documents")
    return template.model_copy(
        update={
            "source": template.source.model_copy(
                update={
                    "required_current_documents": required_current_documents,
                    "required_current_document_roles": [],
                    "required_background_references": 0,
                }
            )
        }
    )


def test_load_automations_rejects_unknown_fields(tmp_path: Path) -> None:
    data = automation_data(unknown=True)
    path = tmp_path / "automations.yaml"
    path.write_text(__import__("yaml").safe_dump(data), encoding="utf-8")

    with pytest.raises(RuntimeError, match="Invalid automations configuration"):
        load_automations(path)


def test_load_automations_requires_exactly_one_download(tmp_path: Path) -> None:
    data = automation_data(steps=[{"action": "wait", "selector": "#ready"}])
    path = tmp_path / "automations.yaml"
    path.write_text(__import__("yaml").safe_dump(data), encoding="utf-8")

    with pytest.raises(RuntimeError, match="exactly one download step"):
        load_automations(path)


def test_load_feishu_document_config_expands_fixed_template(tmp_path: Path) -> None:
    path = tmp_path / "automations.yaml"
    path.write_text(
        """\
version: 2
tasks:
  weekly-report:
    name: 国内业务周报
    url: https://tenant.feishu.cn/wiki/document-id
    extension: v-weekly-report-linked-documents
""",
        encoding="utf-8",
    )

    task = load_automations(path).tasks["weekly-report"]

    assert task.name == "国内业务周报"
    assert task.browser.start_url == "https://tenant.feishu.cn/wiki/document-id"
    assert task.browser.allowed_hosts == ["tenant.feishu.cn"]
    assert task.output.directory == Path("weekly-report")
    assert task.output.filename == "weekly-report-{date:%Y-%m-%d}.md"
    assert task.validation.signature == "markdown"
    assert task.extension == "v-weekly-report-linked-documents"
    assert sum(step.expect == "download" for step in task.steps) == 1


@pytest.mark.parametrize(
    "url",
    [
        "http://tenant.feishu.cn/wiki/document-id",
        "https://example.com/wiki/document-id",
        "https://tenant.feishu.cn/drive/home/",
        "https://user@tenant.feishu.cn/wiki/document-id",
        "https://tenant.feishu.cn:8443/wiki/document-id",
    ],
)
def test_load_feishu_document_config_rejects_unsafe_url(
    tmp_path: Path,
    url: str,
) -> None:
    path = tmp_path / "automations.yaml"
    path.write_text(
        __import__("yaml").safe_dump(
            {
                "version": 2,
                "tasks": {
                    "weekly-report": {
                        "name": "国内业务周报",
                        "url": url,
                    }
                },
            },
            allow_unicode=True,
        ),
        encoding="utf-8",
    )

    with pytest.raises(RuntimeError, match="Feishu Wiki document URL"):
        load_automations(path)


def test_load_feishu_document_config_rejects_unsupported_format(
    tmp_path: Path,
) -> None:
    path = tmp_path / "automations.yaml"
    path.write_text(
        """\
version: 2
tasks:
  weekly-report:
    name: 国内业务周报
    url: https://tenant.feishu.cn/wiki/document-id
    format: pdf
""",
        encoding="utf-8",
    )

    with pytest.raises(RuntimeError, match="markdown"):
        load_automations(path)


def test_load_automations_merges_shared_and_local_files(tmp_path: Path) -> None:
    shared = tmp_path / "automations.yaml"
    local = tmp_path / "automations.local.yaml"
    shared.write_text(
        """\
version: 2
tasks:
  shared-report:
    name: 公共周报
    url: https://tenant.feishu.cn/wiki/shared-document
""",
        encoding="utf-8",
    )
    local.write_text(
        """\
version: 2
tasks:
  local-report:
    name: 本机周报
    url: https://tenant.feishu.cn/wiki/local-document
""",
        encoding="utf-8",
    )

    config = load_automations(shared, local)

    assert list(config.tasks) == ["shared-report", "local-report"]


def test_load_automations_allows_missing_source_file(tmp_path: Path) -> None:
    shared = tmp_path / "automations.yaml"
    shared.write_text(
        """\
version: 2
tasks:
  shared-report:
    name: 公共周报
    url: https://tenant.feishu.cn/wiki/shared-document
""",
        encoding="utf-8",
    )

    config = load_automations(shared, tmp_path / "automations.local.yaml")

    assert list(config.tasks) == ["shared-report"]


def test_load_automations_deduplicates_identical_task_ids(tmp_path: Path) -> None:
    shared = tmp_path / "automations.yaml"
    local = tmp_path / "automations.local.yaml"
    content = """\
version: 2
tasks:
  duplicate-report:
    name: 重复周报
    url: https://tenant.feishu.cn/wiki/document-id
"""
    shared.write_text(content, encoding="utf-8")
    local.write_text(content, encoding="utf-8")

    config = load_automations(shared, local)

    assert list(config.tasks) == ["duplicate-report"]


def test_load_automations_rejects_conflicting_task_ids(tmp_path: Path) -> None:
    shared = tmp_path / "automations.yaml"
    local = tmp_path / "automations.local.yaml"
    shared.write_text(
        """\
version: 2
tasks:
  duplicate-report:
    name: 公共周报
    url: https://tenant.feishu.cn/wiki/shared
""",
        encoding="utf-8",
    )
    local.write_text(
        """\
version: 2
tasks:
  duplicate-report:
    name: 本机周报
    url: https://tenant.feishu.cn/wiki/local
""",
        encoding="utf-8",
    )

    with pytest.raises(
        RuntimeError,
        match="Conflicting automation task id 'duplicate-report'",
    ):
        load_automations(shared, local)


def test_extract_linked_documents_uses_section_and_tenant_only(tmp_path: Path) -> None:
    source = tmp_path / "weekly.md"
    source.write_text(
        """\
# 周报

[章节外文档](https://tenant.feishu.cn/wiki/outside)

## 各端周报

- [产品端周报](https://tenant.feishu.cn/wiki/product#heading)
- [销售端周报](https://tenant.feishu.cn/docx/sales)
- [重复文档](https://tenant.feishu.cn/wiki/product)
- [其他租户](https://other.feishu.cn/wiki/other)
- [其他系统](https://example.com/wiki/external)
- https://tenant.feishu.cn/wiki/bare-url

## 后续事项

[后续文档](https://tenant.feishu.cn/wiki/after)
""",
        encoding="utf-8",
    )
    template = linked_documents_template(required_current_documents=2)

    documents = extract_linked_documents(
        source,
        "https://tenant.feishu.cn/wiki/source",
        template,
    )

    assert [(item.name, item.url) for item in documents] == [
        ("产品端周报", "https://tenant.feishu.cn/wiki/product"),
        ("销售端周报", "https://tenant.feishu.cn/docx/sales"),
    ]


def test_extract_linked_documents_marks_background_weekly_reference(tmp_path: Path) -> None:
    source = tmp_path / "weekly.md"
    source.write_text(
        """\
# 上期正式周报

# 各端周报
[2026/07/27\\-2026/07/31（第一百三十四周）](https://tenant.feishu.cn/wiki/previous)
[产品端周报](https://tenant.feishu.cn/wiki/product)
""",
        encoding="utf-8",
    )
    template = linked_documents_template()

    documents = extract_linked_documents(
        source,
        "https://tenant.feishu.cn/wiki/source",
        template,
    )

    assert [(item.name, item.url, item.is_background) for item in documents] == [
        ("2026/07/27\\-2026/07/31（第一百三十四周）", "https://tenant.feishu.cn/wiki/previous", True),
        ("产品端周报", "https://tenant.feishu.cn/wiki/product", False),
    ]


def test_extract_linked_documents_requires_all_configured_current_documents(
    tmp_path: Path,
) -> None:
    source = tmp_path / "weekly.md"
    source.write_text(
        """\
# 各端周报
[2026/07/27\\-2026/07/31（第一百三十四周）](https://tenant.feishu.cn/wiki/previous)
[产品端周报](https://tenant.feishu.cn/wiki/product)
[服务端周报](https://tenant.feishu.cn/wiki/server)
[运营端周报](https://tenant.feishu.cn/wiki/operations)
[数据端周报](https://tenant.feishu.cn/wiki/data)
""",
        encoding="utf-8",
    )
    template = linked_documents_template(required_current_documents=5)

    with pytest.raises(ExtensionFailed, match="本期各端周报不足：需要 5 份，实际 4 份"):
        extract_linked_documents(
            source,
            "https://tenant.feishu.cn/wiki/source",
            template,
        )


def test_extract_linked_documents_requires_each_configured_business_role(
    tmp_path: Path,
) -> None:
    source = tmp_path / "weekly.md"
    source.write_text(
        """\
# 各端周报
[2026/07/27\\-2026/07/31（第一百三十四周）](https://tenant.feishu.cn/wiki/previous)
[vivo产品周报](https://tenant.feishu.cn/docx/WOscdHEyCot8dSxinyNcMBRjnCc)
[vivo音乐产品周报](https://tenant.feishu.cn/wiki/EtQFwaBJOiT0TpkciaYcm0t8nPb)
[vivo运营周报](https://tenant.feishu.cn/wiki/Xr9wwhkWMiYLdFkHSHXcBaOznXP)
[服务端开发部周报](https://tenant.feishu.cn/docx/Cy14d37JVoGe0nxCbJicvfQ7nke)
[重复 vivo产品周报](https://tenant.feishu.cn/docx/different-product)
""",
        encoding="utf-8",
    )
    template = load_linked_documents_extension("v-weekly-report-linked-documents")

    with pytest.raises(ExtensionFailed, match="缺少必需业务端：客户端"):
        extract_linked_documents(
            source,
            "https://tenant.feishu.cn/wiki/source",
            template,
        )


def test_extract_linked_documents_requires_background_reference(tmp_path: Path) -> None:
    source = tmp_path / "weekly.md"
    source.write_text(
        """\
# 各端周报
[vivo产品周报](https://tenant.feishu.cn/docx/WOscdHEyCot8dSxinyNcMBRjnCc)
[vivo音乐产品周报](https://tenant.feishu.cn/wiki/EtQFwaBJOiT0TpkciaYcm0t8nPb)
[vivo运营周报](https://tenant.feishu.cn/wiki/Xr9wwhkWMiYLdFkHSHXcBaOznXP)
[移动端周会](https://tenant.feishu.cn/wiki/HThowHH2GiQmTuk5bwIcvGRTnVp)
[服务端开发部周报](https://tenant.feishu.cn/docx/Cy14d37JVoGe0nxCbJicvfQ7nke)
""",
        encoding="utf-8",
    )
    template = load_linked_documents_extension("v-weekly-report-linked-documents")

    with pytest.raises(ExtensionFailed, match="上周参考不足：需要 1 份，实际 0 份"):
        extract_linked_documents(
            source,
            "https://tenant.feishu.cn/wiki/source",
            template,
        )


def test_extract_linked_documents_requires_configured_document_path(tmp_path: Path) -> None:
    source = tmp_path / "weekly.md"
    source.write_text(
        """\
# 各端周报
[2026/07/27\\-2026/07/31（第一百三十四周）](https://tenant.feishu.cn/wiki/previous)
[vivo产品周报](https://tenant.feishu.cn/docx/WOscdHEyCot8dSxinyNcMBRjnCc)
[vivo音乐产品周报](https://tenant.feishu.cn/wiki/EtQFwaBJOiT0TpkciaYcm0t8nPb)
[vivo运营周报](https://tenant.feishu.cn/wiki/Xr9wwhkWMiYLdFkHSHXcBaOznXP)
[移动端周会](https://tenant.feishu.cn/wiki/not-the-client-document)
[服务端开发部周报](https://tenant.feishu.cn/docx/Cy14d37JVoGe0nxCbJicvfQ7nke)
""",
        encoding="utf-8",
    )
    template = load_linked_documents_extension("v-weekly-report-linked-documents")

    with pytest.raises(ExtensionFailed, match="缺少必需业务端：客户端"):
        extract_linked_documents(
            source,
            "https://tenant.feishu.cn/wiki/source",
            template,
        )


def test_weekly_linked_document_rejects_current_date_outside_period_declaration(
    tmp_path: Path,
) -> None:
    source = tmp_path / "weekly.md"
    source.write_text(
        """\
# 产品端周报

日期：2026-07-27至2026-07-31

下周计划于 2026-08-05 完成发布。
""",
        encoding="utf-8",
    )

    with pytest.raises(WeeklyValidationError, match="未检测到本期"):
        validate_weekly_linked_document(source, "2026-08-03至2026-08-09")


@pytest.mark.parametrize(
    "heading",
    [
        "## 日期：2026.8.3-2026.8.9",
        "## **日期：2026\\-8\\-6**",
        "## 日期：2026.7.31-2026.8.6",
        "# 移动端周会 2026-08-07",
        "# 服务端开发部周报（2026-8-3 至 2026-8-7）",
    ],
)
def test_weekly_linked_document_accepts_current_source_title(
    tmp_path: Path,
    heading: str,
) -> None:
    source = tmp_path / "weekly.md"
    prefix = "" if heading.startswith("# ") else "# 周报\n\n"
    source.write_text(f"{prefix}{heading}\n", encoding="utf-8")

    validate_weekly_linked_document(source, "2026-08-03至2026-08-09")


def test_weekly_linked_document_ignores_later_current_date(tmp_path: Path) -> None:
    source = tmp_path / "weekly.md"
    source.write_text(
        """\
# 产品周报

## 日期：2026-07-27至2026-07-31

# 下周计划 2026-08-05
""",
        encoding="utf-8",
    )

    with pytest.raises(WeeklyValidationError, match="未检测到本期"):
        validate_weekly_linked_document(source, "2026-08-03至2026-08-09")


def test_extract_linked_documents_requires_configured_section(tmp_path: Path) -> None:
    source = tmp_path / "weekly.md"
    source.write_text("# 周报\n", encoding="utf-8")
    template = load_linked_documents_extension(
        "v-weekly-report-linked-documents"
    )

    with pytest.raises(ExtensionFailed, match="未找到"):
        extract_linked_documents(
            source,
            "https://tenant.feishu.cn/wiki/source",
            template,
        )


def test_linked_filename_is_safe_and_unique() -> None:
    used = set()

    first = linked_filename('产品/端：周报*', 1, used, identifier="first")
    second = linked_filename('产品/端：周报*', 2, used, identifier="second")

    assert first == "产品-端-周报.md"
    assert second == "产品-端-周报-16367aac.md"


def test_prune_linked_markdown_files_removes_stale_current_sources(
    tmp_path: Path,
) -> None:
    data_dir = tmp_path / "data"
    current = data_dir / "weekly" / "linked"
    other_date = data_dir / "other" / "linked"
    nested = current / "nested"
    nested.mkdir(parents=True)
    other_date.mkdir(parents=True)
    stale = current / "过期周报.md"
    current_source = current / "当前周报.md"
    preserved_file = current / "notes.txt"
    preserved_nested = nested / "manual.md"
    preserved_other_date = other_date / "01-report.md"
    stale.write_text("stale", encoding="utf-8")
    current_source.write_text("keep", encoding="utf-8")
    preserved_file.write_text("keep", encoding="utf-8")
    preserved_nested.write_text("keep", encoding="utf-8")
    preserved_other_date.write_text("keep", encoding="utf-8")

    _prune_linked_markdown_files(
        Path("weekly/linked"),
        data_dir,
        {"当前周报.md"},
    )

    assert not stale.exists()
    assert current_source.exists()
    assert preserved_file.exists()
    assert preserved_nested.exists()
    assert preserved_other_date.exists()


def test_linked_documents_continue_after_one_download_fails(
    settings: Settings,
    tmp_path: Path,
) -> None:
    config_file = tmp_path / "automations.yaml"
    config_file.write_text(
        """\
version: 2
tasks:
  weekly-report:
    name: 国内业务周报
    url: https://tenant.feishu.cn/wiki/source
    extension: v-weekly-report-linked-documents
""",
        encoding="utf-8",
    )
    task = load_automations(config_file).tasks["weekly-report"]
    source = tmp_path / "weekly.md"
    source.write_text(
        """\
# 各端周报
[产品端](https://tenant.feishu.cn/wiki/product)
[销售端](https://tenant.feishu.cn/wiki/sales)
""",
        encoding="utf-8",
    )
    output = tmp_path / "sales.md"
    output.write_text("# 销售端", encoding="utf-8")
    calls = []

    def fake_run(linked_task, *_args):
        calls.append(linked_task)
        if linked_task.name == "产品端":
            raise AutomationFailed("页面入口不可用")
        return output, output.stat().st_size, False

    with (
        patch("app.automations.runner._run_task_once", fake_run),
        patch(
            "app.automations.runner.load_linked_documents_extension",
            return_value=linked_documents_template(required_current_documents=2),
        ),
    ):
        results = _run_linked_documents(
            task,
            source,
            settings,
            "run-1",
        )

    assert [item.status for item in results] == ["failed", "success"]
    assert len(calls) == 2
    assert calls[1].output.directory == task.output.directory / "linked"
    assert calls[1].output.filename == "销售端.md"


def test_linked_documents_keep_previous_file_when_renamed_source_fails(
    settings: Settings,
    tmp_path: Path,
) -> None:
    config_file = tmp_path / "automations.yaml"
    config_file.write_text(
        """\
version: 2
tasks:
  weekly-report:
    name: 国内业务周报
    url: https://tenant.feishu.cn/wiki/source
    extension: v-weekly-report-linked-documents
""",
        encoding="utf-8",
    )
    task = load_automations(config_file).tasks["weekly-report"]
    source = tmp_path / "weekly.md"
    source.write_text(
        "# 各端周报\n[新版产品周报](https://tenant.feishu.cn/wiki/product)\n",
        encoding="utf-8",
    )
    linked = settings.automations.artifacts_dir / task.output.directory / "linked"
    linked.mkdir(parents=True)
    old_file = linked / "产品周报.md"
    old_file.write_text("# 旧材料", encoding="utf-8")
    (linked / ".sources.json").write_text(
        '{"https://tenant.feishu.cn/wiki/product": "产品周报.md"}',
        encoding="utf-8",
    )

    with (
        patch(
            "app.automations.runner._run_task_once",
            side_effect=AutomationFailed("页面入口不可用"),
        ),
        patch(
            "app.automations.runner.load_linked_documents_extension",
            return_value=linked_documents_template(),
        ),
    ):
        results = _run_linked_documents(task, source, settings, "run-1")

    assert [item.status for item in results] == ["failed"]
    assert old_file.exists()
    assert not (linked / "新版产品周报.md").exists()


def test_run_automation_reports_partial_linked_download_failure(
    settings: Settings,
    tmp_path: Path,
) -> None:
    config_file = tmp_path / "automations.yaml"
    config_file.write_text(
        """\
version: 2
tasks:
  weekly-report:
    name: 国内业务周报
    url: https://tenant.feishu.cn/wiki/source
    extension: v-weekly-report-linked-documents
""",
        encoding="utf-8",
    )
    settings.automations.config_file = config_file
    settings.automations.state_dir = tmp_path / "state"
    settings.automations.runtime_dir = tmp_path / "runtime"
    settings.automations.artifacts_dir = tmp_path / "artifacts"
    source = tmp_path / "weekly.md"
    source.write_text("# 各端周报\n", encoding="utf-8")
    linked_results = [
        LinkedDocumentResult(
            name="产品端",
            status="success",
            message="下载完成",
            output_file=str(tmp_path / "product.md"),
        ),
        LinkedDocumentResult(
            name="销售端",
            status="failed",
            message="下载超时",
        ),
    ]

    with (
        patch(
            "app.automations.runner._run_task_once",
            return_value=(source, source.stat().st_size, False),
        ),
        patch(
            "app.automations.runner._run_linked_documents",
            return_value=linked_results,
        ),
    ):
        result = run_automation(settings, "weekly-report", run_id="run-1")

    assert result.status == "failed"
    assert result.output_file is None
    assert result.validation_status == "failed"
    assert result.message.startswith("本期校验失败：")
    assert [item.status for item in result.linked_documents] == ["failed", "failed"]
    assert result.linked_documents[1].message == "下载超时"


def test_weekly_report_downloads_use_the_active_period_input_directory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    settings: Settings,
) -> None:
    monkeypatch.setattr(runner, "WEEKLY_REPORTS_ROOT", tmp_path / "weekly-reports")

    root = _weekly_report_input_root(datetime(2026, 8, 5, 7, 5, 51))

    assert root == (
        tmp_path
        / "weekly-reports"
        / "2026-08-03至2026-08-09"
        / "inputs"
    )
    assert root.parent.stat().st_mode & 0o777 == 0o700

    task = _weekly_report_download_task(
        load_automations(
            Path(__file__).parents[1] / "config" / "automations.yaml"
        ).tasks["v-domestic-weekly-report"]
    )
    assert _output_path(
        task,
        settings.automations.artifacts_dir,
        output_root=root,
    ) == root / "V 国内业务周报.md"


def test_weekly_report_state_records_period_and_main_document(
    settings: Settings,
    tmp_path: Path,
) -> None:
    config_file = tmp_path / "automations.yaml"
    config_file.write_text(
        """\
version: 2
tasks:
  weekly-report:
    name: 国内业务周报
    title: 国内业务周报下载
    url: https://tenant.feishu.cn/wiki/source
    extension: v-weekly-report-linked-documents
""",
        encoding="utf-8",
    )
    settings.automations.config_file = config_file
    settings.automations.state_dir = tmp_path / "state"
    settings.automations.runtime_dir = tmp_path / "runtime"
    settings.automations.artifacts_dir = tmp_path / "artifacts"
    output = tmp_path / "国内业务周报.md"
    output.write_text("# 周报", encoding="utf-8")

    with (
        patch(
            "app.automations.runner._run_task_once",
            return_value=(output, output.stat().st_size, False),
        ),
        patch("app.automations.runner._run_linked_documents", return_value=[]),
    ):
        result = run_automation(settings, "weekly-report", run_id="run-1")

    assert result.period == reporting_period(result.started_at.date())
    assert result.main_document_name == "国内业务周报"


def test_weekly_report_publishes_inputs_only_after_current_period_validation(
    settings: Settings,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_file = tmp_path / "automations.yaml"
    config_file.write_text(
        """\
version: 2
tasks:
  weekly-report:
    name: 国内业务周报
    url: https://tenant.feishu.cn/wiki/source
    extension: v-weekly-report-linked-documents
""",
        encoding="utf-8",
    )
    settings.automations.config_file = config_file
    settings.automations.state_dir = tmp_path / "state"
    settings.automations.runtime_dir = tmp_path / "runtime"
    settings.automations.artifacts_dir = tmp_path / "artifacts"
    monkeypatch.setattr(runner, "WEEKLY_REPORTS_ROOT", tmp_path / "weekly-reports")
    period = reporting_period()

    def fake_run(task, _settings, _run_id, *, output_root=None):
        target = output_root / task.output.directory / task.output.filename
        target.parent.mkdir(parents=True, exist_ok=True)
        if task.name == "国内业务周报":
            target.write_text(
                "# 2026/07/27-2026/07/31（第一百三十四周）\n\n# 各端周报\n[2026/07/27\\-2026/07/31（第一百三十四周）](https://tenant.feishu.cn/wiki/previous)\n[vivo产品周报](https://tenant.feishu.cn/docx/WOscdHEyCot8dSxinyNcMBRjnCc)\n[vivo音乐产品周报](https://tenant.feishu.cn/wiki/EtQFwaBJOiT0TpkciaYcm0t8nPb)\n[vivo运营周报](https://tenant.feishu.cn/wiki/Xr9wwhkWMiYLdFkHSHXcBaOznXP)\n[移动端周会](https://tenant.feishu.cn/wiki/HThowHH2GiQmTuk5bwIcvGRTnVp)\n[服务端开发部周报](https://tenant.feishu.cn/docx/Cy14d37JVoGe0nxCbJicvfQ7nke)\n",
                encoding="utf-8",
            )
        else:
            target.write_text(
                f"# 产品端周报\n\n## 日期：{period}\n",
                encoding="utf-8",
            )
        return target, target.stat().st_size, False

    with patch("app.automations.runner._run_task_once", side_effect=fake_run):
        result = run_automation(settings, "weekly-report", run_id="run-1")

    input_root = tmp_path / "weekly-reports" / period / "inputs"
    assert result.status == "success"
    assert result.validation_status == "passed"
    assert (input_root / "国内业务周报.md").is_file()
    assert (input_root / "linked" / "vivo产品周报.md").is_file()
    assert (input_root.parent / ".inputs-updated").is_file()
    assert not list(input_root.parent.glob(".inputs-run-1.*"))


def test_weekly_input_publish_restores_inputs_and_marker_after_replace_failure(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "weekly"
    input_root = workspace / "inputs"
    staging_root = workspace / ".inputs-run-1.staging"
    input_root.mkdir(parents=True)
    staging_root.mkdir()
    (input_root / "previous.md").write_text("previous", encoding="utf-8")
    (staging_root / "current.md").write_text("current", encoding="utf-8")
    marker = workspace / ".inputs-updated"
    marker.write_text("previous-marker", encoding="utf-8")
    original_replace = runner.os.replace

    def fail_staging_replace(source, destination):
        if Path(source) == staging_root and Path(destination) == input_root:
            raise OSError("replace failed")
        return original_replace(source, destination)

    with patch("app.automations.runner.os.replace", side_effect=fail_staging_replace):
        with pytest.raises(AutomationFailed, match="输入发布失败"):
            _publish_weekly_inputs(staging_root, input_root, "run-1")

    assert (input_root / "previous.md").read_text(encoding="utf-8") == "previous"
    assert marker.read_text(encoding="utf-8") == "previous-marker"
    assert staging_root.is_dir()


def test_weekly_report_rejects_old_period_without_replacing_inputs(
    settings: Settings,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_file = tmp_path / "automations.yaml"
    config_file.write_text(
        """\
version: 2
tasks:
  weekly-report:
    name: 国内业务周报
    url: https://tenant.feishu.cn/wiki/source
    extension: v-weekly-report-linked-documents
""",
        encoding="utf-8",
    )
    settings.automations.config_file = config_file
    settings.automations.state_dir = tmp_path / "state"
    settings.automations.runtime_dir = tmp_path / "runtime"
    settings.automations.artifacts_dir = tmp_path / "artifacts"
    monkeypatch.setattr(runner, "WEEKLY_REPORTS_ROOT", tmp_path / "weekly-reports")
    period = reporting_period()
    input_root = tmp_path / "weekly-reports" / period / "inputs"
    input_root.mkdir(parents=True)
    (input_root / "保留材料.md").write_text("旧的有效输入", encoding="utf-8")

    def fake_run(task, _settings, _run_id, *, output_root=None):
        target = output_root / task.output.directory / task.output.filename
        target.parent.mkdir(parents=True, exist_ok=True)
        if task.name == "国内业务周报":
            target.write_text(
                "# 2026/07/27-2026/07/31（第一百三十四周）\n\n# 各端周报\n[2026/07/27\\-2026/07/31（第一百三十四周）](https://tenant.feishu.cn/wiki/previous)\n[vivo产品周报](https://tenant.feishu.cn/docx/WOscdHEyCot8dSxinyNcMBRjnCc)\n[vivo音乐产品周报](https://tenant.feishu.cn/wiki/EtQFwaBJOiT0TpkciaYcm0t8nPb)\n[vivo运营周报](https://tenant.feishu.cn/wiki/Xr9wwhkWMiYLdFkHSHXcBaOznXP)\n[移动端周会](https://tenant.feishu.cn/wiki/HThowHH2GiQmTuk5bwIcvGRTnVp)\n[服务端开发部周报](https://tenant.feishu.cn/docx/Cy14d37JVoGe0nxCbJicvfQ7nke)\n",
                encoding="utf-8",
            )
        else:
            target.write_text("# 产品端\n日期：2026-01-01\n", encoding="utf-8")
        return target, target.stat().st_size, False

    with patch("app.automations.runner._run_task_once", side_effect=fake_run):
        result = run_automation(settings, "weekly-report", run_id="run-1")

    assert result.status == "waiting"
    assert result.validation_status == "waiting"
    assert result.validation_message == (
        f"关联文档“vivo产品周报”等待各端更新：未检测到本期（{period}）有效周期标题"
    )
    assert (input_root / "保留材料.md").is_file()
    assert not (input_root.parent / ".inputs-updated").exists()
    assert not list(input_root.parent.glob(".inputs-run-1.*"))


def test_manager_resets_weekly_report_display_on_a_new_period(
    settings: Settings,
    tmp_path: Path,
) -> None:
    task = automation_data()["tasks"]["monthly-report"]
    task.update(
        {
            "name": "V 国内业务周报",
            "title": "V 国内业务周报下载",
            "extension": "v-weekly-report-linked-documents",
        }
    )
    config_file = tmp_path / "automations.yaml"
    config_file.write_text(
        __import__("yaml").safe_dump(
            {"version": 1, "tasks": {"weekly-report": task}},
            allow_unicode=True,
        ),
        encoding="utf-8",
    )
    settings.automations.config_file = config_file
    settings.automations.state_dir = tmp_path / "state"
    settings.automations.runtime_dir = tmp_path / "runtime"
    settings.automations.artifacts_dir = tmp_path / "artifacts"
    AutomationStateStore(settings.automations.state_dir).write(
        AutomationState(
            task_id="weekly-report",
            status="success",
            period="2026-08-03至2026-08-09",
            main_document_name="上期周报",
            linked_documents=[
                LinkedDocumentResult(
                    name="上期产品端周报",
                    status="success",
                    message="下载完成",
                )
            ],
        )
    )
    manager = AutomationManager(settings)

    with (
        patch(
            "app.automations.manager.debug_chrome_status",
            return_value=("stopped", "Debug Chrome 未启动", None),
        ),
        patch(
            "app.automations.manager.reporting_period",
            return_value="2026-08-10至2026-08-16",
        ),
    ):
        result = manager.list(home_only=False)

    weekly = result.tasks[0]
    assert weekly.title == "V 国内业务周报下载"
    assert weekly.reporting_period == "2026-08-10至2026-08-16"
    assert weekly.main_document_name == "V 国内业务周报"
    assert weekly.state.status == "idle"
    assert weekly.state.linked_documents == []


def test_manager_keeps_an_active_weekly_report_visible_across_the_boundary(
    settings: Settings,
    tmp_path: Path,
) -> None:
    task = automation_data()["tasks"]["monthly-report"]
    task.update(
        {
            "name": "V 国内业务周报",
            "title": "V 国内业务周报下载",
            "extension": "v-weekly-report-linked-documents",
        }
    )
    config_file = tmp_path / "automations.yaml"
    config_file.write_text(
        __import__("yaml").safe_dump(
            {"version": 1, "tasks": {"weekly-report": task}},
            allow_unicode=True,
        ),
        encoding="utf-8",
    )
    settings.automations.config_file = config_file
    settings.automations.state_dir = tmp_path / "state"
    settings.automations.runtime_dir = tmp_path / "runtime"
    settings.automations.artifacts_dir = tmp_path / "artifacts"
    AutomationStateStore(settings.automations.state_dir).write(
        AutomationState(
            task_id="weekly-report",
            status="running",
            period="2026-08-03至2026-08-09",
            process_id=os.getpid(),
        )
    )
    manager = AutomationManager(settings)

    with (
        patch(
            "app.automations.manager.debug_chrome_status",
            return_value=("stopped", "Debug Chrome 未启动", None),
        ),
        patch(
            "app.automations.manager.reporting_period",
            return_value="2026-08-10至2026-08-16",
        ),
    ):
        result = manager.list(home_only=False)

    weekly = result.tasks[0]
    assert weekly.reporting_period == "2026-08-03至2026-08-09"
    assert weekly.state.status == "running"


def test_run_automation_rejects_an_unsafe_run_id(
    settings: Settings,
    tmp_path: Path,
) -> None:
    configure_automations(settings, tmp_path)

    with pytest.raises(AutomationFailed, match="运行标识"):
        run_automation(settings, "monthly-report", run_id="../outside")


def test_login_redirect_host_uses_expired_message(
    settings: Settings,
    tmp_path: Path,
) -> None:
    data = automation_data()
    data["tasks"]["monthly-report"]["login"] = {
        "redirect_hosts": ["ACCOUNTS.EXAMPLE.COM."],
        "check": {"selector": "#user"},
        "expired_message": "登录状态已失效",
    }
    config_file = tmp_path / "automations.yaml"
    config_file.write_text(
        __import__("yaml").safe_dump(data, allow_unicode=True),
        encoding="utf-8",
    )
    task = load_automations(config_file).tasks["monthly-report"]

    with pytest.raises(AutomationFailed, match="登录状态已失效"):
        _check_navigation("https://accounts.example.com/login", task)


def test_unlisted_redirect_host_remains_disallowed(
    settings: Settings,
    tmp_path: Path,
) -> None:
    config_file = configure_automations(settings, tmp_path)
    task = load_automations(config_file).tasks["monthly-report"]

    with pytest.raises(AutomationFailed, match="未允许的域名"):
        _check_navigation("https://unexpected.example.net/login", task)


def test_state_store_writes_atomic_private_json(tmp_path: Path) -> None:
    store = AutomationStateStore(tmp_path)
    state = AutomationState(task_id="task", status="queued", run_id="run")

    store.write(state)

    path = store.path_for("task")
    assert store.read("task") == state
    assert path.stat().st_mode & 0o777 == 0o600
    assert not list(path.parent.glob("*.tmp"))


def test_download_validation_rejects_html_saved_as_pdf(
    settings: Settings,
    tmp_path: Path,
) -> None:
    config_file = configure_automations(settings, tmp_path)
    task = load_automations(config_file).tasks["monthly-report"]
    path = tmp_path / "report.pdf"
    path.write_text("<html>login</html>", encoding="utf-8")

    with pytest.raises(AutomationFailed, match="签名校验失败"):
        _validate_download(path, task)


def test_download_validation_accepts_utf8_markdown(
    settings: Settings,
    tmp_path: Path,
) -> None:
    data = automation_data()
    task_data = data["tasks"]["monthly-report"]
    task_data["output"]["filename"] = "report.md"
    task_data["validation"] = {
        "non_empty": True,
        "extensions": [".md"],
        "min_bytes": 1,
        "signature": "markdown",
    }
    config_file = tmp_path / "automations.yaml"
    config_file.write_text(
        __import__("yaml").safe_dump(data, allow_unicode=True),
        encoding="utf-8",
    )
    task = load_automations(config_file).tasks["monthly-report"]
    path = tmp_path / "report.md"
    path.write_text("# 国内业务周报\n\n本周进展。\n", encoding="utf-8")

    assert _validate_download(path, task) == path.stat().st_size


def test_download_validation_rejects_binary_saved_as_markdown(
    settings: Settings,
    tmp_path: Path,
) -> None:
    data = automation_data()
    task_data = data["tasks"]["monthly-report"]
    task_data["output"]["filename"] = "report.md"
    task_data["validation"] = {
        "non_empty": True,
        "extensions": [".md"],
        "min_bytes": 1,
        "signature": "markdown",
    }
    config_file = tmp_path / "automations.yaml"
    config_file.write_text(
        __import__("yaml").safe_dump(data, allow_unicode=True),
        encoding="utf-8",
    )
    task = load_automations(config_file).tasks["monthly-report"]
    path = tmp_path / "report.md"
    path.write_bytes(b"\x00\x01binary")

    with pytest.raises(AutomationFailed, match="Markdown"):
        _validate_download(path, task)


def test_run_automation_updates_persistent_state(
    settings: Settings,
    tmp_path: Path,
) -> None:
    configure_automations(settings, tmp_path)
    output = settings.automations.artifacts_dir / "monthly" / "report.pdf"
    output.parent.mkdir(parents=True)
    output.write_bytes(b"%PDF-content")

    async def fake_browser_task(*_args):
        return output, output.stat().st_size, False

    with patch("app.automations.runner._run_browser_task", fake_browser_task):
        result = run_automation(settings, "monthly-report", run_id="run-1")

    assert result.status == "success"
    assert result.output_file == str(output)
    stored = AutomationStateStore(settings.automations.state_dir).read("monthly-report")
    assert stored.status == "success"
    assert stored.run_id == "run-1"


def test_browser_task_uses_and_closes_its_own_page(
    settings: Settings,
    tmp_path: Path,
) -> None:
    config_file = configure_automations(settings, tmp_path)
    task = load_automations(config_file).tasks["monthly-report"]

    class FakeLocator:
        async def wait_for(self, **_kwargs):
            return None

        async def click(self, **_kwargs):
            return None

    class FakeDownload:
        async def save_as(self, path):
            Path(path).write_bytes(b"%PDF-test")

    class FakeDownloadInfo:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        @property
        def value(self):
            async def result():
                return FakeDownload()

            return result()

    class FakePage:
        def __init__(self):
            self.url = "about:blank"
            self.closed = False

        def on(self, *_args):
            return None

        async def goto(self, url, **_kwargs):
            self.url = url

        def locator(self, _selector):
            return FakeLocator()

        def expect_download(self, **_kwargs):
            return FakeDownloadInfo()

        def is_closed(self):
            return self.closed

        async def close(self):
            self.closed = True

    task_page = FakePage()
    existing_page = FakePage()

    class FakeContext:
        pages = [existing_page]

        async def new_page(self):
            return task_page

    @asynccontextmanager
    async def fake_session(**_kwargs):
        yield SimpleNamespace(context=FakeContext())

    with patch(
        "app.automations.runner.session_factory",
        return_value=fake_session,
    ):
        target, size, skipped = asyncio.run(
            _run_browser_task(task, settings, "run-1")
        )

    assert target.read_bytes() == b"%PDF-test"
    assert size == len(b"%PDF-test")
    assert skipped is False
    assert target.stat().st_mode & 0o777 == 0o600
    assert target.parent.stat().st_mode & 0o777 == 0o700
    assert task_page.closed is True
    assert existing_page.closed is False


def test_run_automation_rejects_duplicate_task_lock(
    settings: Settings,
    tmp_path: Path,
) -> None:
    configure_automations(settings, tmp_path)
    lock_path = settings.automations.runtime_dir / "locks" / "task-monthly-report.lock"

    with file_lock(lock_path, 0):
        with pytest.raises(AutomationFailed, match="正在执行"):
            run_automation(settings, "monthly-report")


def test_state_file_contains_no_extra_runtime_files(tmp_path: Path) -> None:
    store = AutomationStateStore(tmp_path)
    store.write(AutomationState(task_id="task", status="idle"))

    content = json.loads(store.path_for("task").read_text(encoding="utf-8"))
    assert content["task_id"] == "task"
    assert "token" not in content


def test_manager_reports_total_enabled_count_before_home_limit(
    settings: Settings,
    tmp_path: Path,
) -> None:
    data = automation_data()
    data["tasks"]["second"] = {
        **data["tasks"]["monthly-report"],
        "name": "第二个任务",
    }
    config_file = tmp_path / "automations.yaml"
    config_file.write_text(
        __import__("yaml").safe_dump(data, allow_unicode=True),
        encoding="utf-8",
    )
    settings.automations.config_file = config_file
    settings.automations.state_dir = tmp_path / "state"
    settings.automations.runtime_dir = tmp_path / "runtime"
    settings.automations.artifacts_dir = tmp_path / "artifacts"
    settings.automations.max_home_tasks = 1
    manager = AutomationManager(settings)

    with patch(
        "app.automations.manager.debug_chrome_status",
        return_value=("stopped", "Debug Chrome 未启动", None),
    ):
        result = manager.list()

    assert result.enabled_count == 2
    assert len(result.tasks) == 1


def test_manager_home_tasks_are_sorted_by_recent_activity(
    settings: Settings,
    tmp_path: Path,
) -> None:
    data = automation_data()
    data["tasks"]["second"] = {
        **data["tasks"]["monthly-report"],
        "name": "第二个任务",
    }
    config_file = tmp_path / "automations.yaml"
    config_file.write_text(
        __import__("yaml").safe_dump(data, allow_unicode=True),
        encoding="utf-8",
    )
    settings.automations.config_file = config_file
    settings.automations.state_dir = tmp_path / "state"
    settings.automations.runtime_dir = tmp_path / "runtime"
    settings.automations.artifacts_dir = tmp_path / "artifacts"
    settings.automations.max_home_tasks = 1
    store = AutomationStateStore(settings.automations.state_dir)
    now = datetime.now().astimezone()
    store.write(
        AutomationState(
            task_id="monthly-report",
            status="success",
            finished_at=now - timedelta(days=1),
        )
    )
    store.write(
        AutomationState(
            task_id="second",
            status="success",
            finished_at=now,
        )
    )
    manager = AutomationManager(settings)

    with patch(
        "app.automations.manager.debug_chrome_status",
        return_value=("stopped", "Debug Chrome 未启动", None),
    ):
        result = manager.list()

    assert [task.id for task in result.tasks] == ["second"]


def test_manager_does_not_pass_hub_token_value_to_runner(
    settings: Settings,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    configure_automations(settings, tmp_path)
    manager = AutomationManager(settings)
    monkeypatch.setenv("HUB_TOKEN", "sensitive-token")

    with (
        patch(
            "app.automations.manager.debug_chrome_status",
            return_value=("running", "Debug Chrome 已运行", "有界面"),
        ),
        patch("app.automations.manager.subprocess.Popen") as popen,
    ):
        manager.start(
            "monthly-report",
            operation_id="operation-1",
            source_ip="100.64.0.1",
        )

    assert popen.call_args.kwargs["env"]["HUB_TOKEN"] == ""


def test_feishu_environment_url_classification() -> None:
    checked_at = datetime.now().astimezone()

    available = _feishu_environment_for_url(
        "https://qw6xxurweq.feishu.cn/drive/home/",
        checked_at=checked_at,
    )
    login_required = _feishu_environment_for_url(
        "https://accounts.feishu.cn/accounts/page/login?redirect=1",
        checked_at=checked_at,
    )
    failed = _feishu_environment_for_url(
        "https://unexpected.example.com/",
        checked_at=checked_at,
    )

    assert available.state == "available"
    assert login_required.state == "login_required"
    assert failed.state == "failed"


def test_manager_checks_and_caches_feishu_environment(
    settings: Settings,
    tmp_path: Path,
) -> None:
    configure_automations(settings, tmp_path)
    manager = AutomationManager(settings)
    checked_at = datetime.now().astimezone()

    async def fake_check():
        return FeishuEnvironmentState(
            state="available",
            message="登录有效",
            checked_at=checked_at,
        )

    with (
        patch(
            "app.automations.manager.debug_chrome_status",
            return_value=("running", "Debug Chrome 已运行", "有界面"),
        ),
        patch.object(manager, "_check_feishu_page", fake_check),
    ):
        result = manager.check_feishu_environment()
        listing = manager.list()

    assert result.state == "available"
    assert listing.feishu_environment == result


def test_manager_resets_feishu_environment_when_browser_stops(
    settings: Settings,
    tmp_path: Path,
) -> None:
    configure_automations(settings, tmp_path)
    manager = AutomationManager(settings)
    manager._set_feishu_environment(
        FeishuEnvironmentState(state="available", message="登录有效")
    )

    with patch(
        "app.automations.manager.debug_chrome_status",
        return_value=("stopped", "Debug Chrome 未启动", None),
    ):
        stopped = manager.list()

    assert stopped.feishu_environment.state == "browser_stopped"


def test_manager_stores_private_feishu_qr_and_clears_it_on_browser_stop(
    settings: Settings,
    tmp_path: Path,
) -> None:
    configure_automations(settings, tmp_path)
    manager = AutomationManager(settings)
    manager._save_feishu_qr(b"\x89PNG\r\n\x1a\ncontent")
    manager._set_feishu_environment(
        FeishuEnvironmentState(
            state="login_required",
            message="需要登录",
            qr_available=True,
        )
    )
    content = manager.feishu_qr_content()
    path = settings.automations.runtime_dir / "feishu-login-qr.png"

    assert content == b"\x89PNG\r\n\x1a\ncontent"
    assert path.stat().st_mode & 0o777 == 0o600

    with patch(
        "app.automations.manager.debug_chrome_status",
        return_value=("stopped", "Debug Chrome 未启动", None),
    ):
        manager.list()

    assert not path.exists()


def test_manager_logs_final_web_operation_once(
    settings: Settings,
    tmp_path: Path,
) -> None:
    configure_automations(settings, tmp_path)
    manager = AutomationManager(settings)
    store = AutomationStateStore(settings.automations.state_dir)
    store.write(
        AutomationState(
            task_id="monthly-report",
            status="success",
            run_id="run-1",
            operation_id="operation-1",
            source_ip="100.64.0.1",
            message="下载完成",
        )
    )

    with patch("app.automations.operations.LOGGER.info") as log_info:
        first = manager._current_state("monthly-report")
        second = manager._current_state("monthly-report")

    assert first.operation_logged is True
    assert second.operation_logged is True
    log_info.assert_called_once()


def test_runner_logs_final_web_operation_without_manager_poll(
    settings: Settings,
    tmp_path: Path,
) -> None:
    configure_automations(settings, tmp_path)
    store = AutomationStateStore(settings.automations.state_dir)
    store.write(
        AutomationState(
            task_id="monthly-report",
            status="queued",
            run_id="run-1",
            operation_id="operation-1",
            source_ip="100.64.0.1",
        )
    )
    output = tmp_path / "report.pdf"
    output.write_bytes(b"%PDF-content")

    async def fake_browser_task(*_args):
        return output, output.stat().st_size, False

    with (
        patch("app.automations.runner._run_browser_task", fake_browser_task),
        patch("app.automations.operations.LOGGER.info") as log_info,
    ):
        result = run_automation(
            settings,
            "monthly-report",
            trigger="web",
            run_id="run-1",
        )

    assert result.status == "success"
    assert result.operation_logged is True
    log_info.assert_called_once()


def test_log_final_operation_ignores_cli_state_without_operation_id() -> None:
    state = AutomationState(task_id="task", status="success")

    with patch("app.automations.operations.LOGGER.info") as log_info:
        result = log_final_operation(state)

    assert result.operation_logged is False
    log_info.assert_not_called()


def test_manager_starts_debug_chrome_and_confirms_final_state(
    settings: Settings,
    tmp_path: Path,
) -> None:
    configure_automations(settings, tmp_path)
    manager = AutomationManager(settings)

    with patch(
        "app.automations.manager.start_debug_chrome",
        return_value=SimpleNamespace(state="running", mode="headless"),
    ) as start:
        result = manager.control_browser("start")

    assert result.state == "running"
    assert result.mode == "无界面"
    start.assert_called_once_with("headless")


def test_manager_starts_debug_chrome_headless(
    settings: Settings,
    tmp_path: Path,
) -> None:
    configure_automations(settings, tmp_path)
    manager = AutomationManager(settings)

    with patch(
        "app.automations.manager.start_debug_chrome",
        return_value=SimpleNamespace(state="running", mode="headless"),
    ) as start:
        result = manager.control_browser("start", "headless")

    assert result.state == "running"
    assert result.mode == "无界面"
    start.assert_called_once_with("headless")


def test_manager_starts_selected_initialized_profile(
    settings: Settings,
    tmp_path: Path,
) -> None:
    configure_automations(settings, tmp_path)
    manager = AutomationManager(settings)

    with (
        patch(
            "app.automations.manager.select_and_start_debug_chrome",
            return_value=SimpleNamespace(
                state="running",
                mode="headed",
                profile_directory="Profile 2",
            ),
        ) as start,
        patch(
            "app.automations.manager.browser_profiles",
            return_value=(
                [
                    BrowserProfileInfo(
                        id="Profile 2",
                        name="工作",
                        initialized=True,
                        source_available=True,
                        active=True,
                    )
                ],
                None,
            ),
        ),
    ):
        result = manager.control_browser("start", "headed", "Profile 2")

    assert result.profile_id == "Profile 2"
    assert result.profile_name == "工作"
    start.assert_called_once_with("Profile 2", "headed")


def test_manager_lists_browser_profiles_and_current_profile(
    settings: Settings,
    tmp_path: Path,
) -> None:
    configure_automations(settings, tmp_path)
    manager = AutomationManager(settings)
    profile = BrowserProfileInfo(
        id="Default",
        name="默认用户",
        initialized=True,
        source_available=True,
        active=True,
    )

    with (
        patch(
            "app.automations.manager.debug_chrome_status",
            return_value=("running", "Debug Chrome 已运行", "有界面"),
        ),
        patch(
            "app.automations.manager.current_debug_chrome_profile",
            return_value="Default",
        ),
        patch(
            "app.automations.manager.browser_profiles",
            return_value=([profile], None),
        ),
    ):
        result = manager.list()

    assert result.browser_profile_id == "Default"
    assert result.browser_profile_name == "默认用户"
    assert result.browser_profiles[0].initialized is True


def test_manager_initializes_profile_in_background_and_logs_final_state(
    settings: Settings,
    tmp_path: Path,
) -> None:
    configure_automations(settings, tmp_path)
    manager = AutomationManager(settings)
    profile = BrowserProfileInfo(
        id="Profile 2",
        name="工作",
        initialized=False,
        source_available=True,
        active=False,
    )
    finished = threading.Event()

    def initialize(_profile_id: str, _mode: str):
        finished.set()
        return SimpleNamespace(
            state="running",
            mode="headed",
            profile_directory="Profile 2",
        )

    with (
        patch(
            "app.automations.manager.browser_profiles",
            return_value=([profile], None),
        ),
        patch(
            "app.automations.manager.debug_chrome_status",
            return_value=("stopped", "Debug Chrome 未启动", None),
        ),
        patch(
            "app.automations.manager.initialize_and_start_debug_chrome",
            side_effect=initialize,
        ),
        patch("app.automations.manager.write_operation") as write_operation,
    ):
        accepted = manager.initialize_browser(
            "Profile 2",
            "headed",
            operation_id="operation-1",
            source_ip="100.64.0.1",
        )
        assert finished.wait(2)
        for _ in range(100):
            if write_operation.call_count >= 2:
                break
            __import__("time").sleep(0.01)

    assert accepted.status == "initializing"
    assert [call.kwargs["status"] for call in write_operation.call_args_list] == [
        "started",
        "succeeded",
    ]


def test_manager_recovers_interrupted_browser_initialization(
    settings: Settings,
    tmp_path: Path,
) -> None:
    configure_automations(settings, tmp_path)
    state_path = (
        settings.automations.state_dir
        / "browser-profile-initialization.json"
    )
    state_path.parent.mkdir(parents=True)
    state_path.write_text(
        json.dumps(
            {
                "profile_id": "Profile 2",
                "state": "running",
                "message": "正在初始化浏览器用户",
                "operation_id": "operation-1",
                "source_ip": "100.64.0.1",
                "target": "debug-chrome:Profile 2:headed",
                "operation_logged": False,
            }
        ),
        encoding="utf-8",
    )

    with (
        patch("app.automations.manager.write_operation") as write_operation,
        patch("app.automations.manager.cleanup_interrupted_profile_copy") as cleanup,
    ):
        manager = AutomationManager(settings)

    assert manager._browser_initialization["state"] == "failed"
    assert "重启中断" in str(manager._browser_initialization["message"])
    assert state_path.stat().st_mode & 0o777 == 0o600
    write_operation.assert_called_once()
    assert write_operation.call_args.kwargs["status"] == "failed"
    cleanup.assert_called_once_with()


def test_manager_logs_failed_when_initialization_thread_cannot_start(
    settings: Settings,
    tmp_path: Path,
) -> None:
    configure_automations(settings, tmp_path)
    manager = AutomationManager(settings)
    profile = BrowserProfileInfo(
        id="Profile 2",
        name="工作",
        initialized=False,
        source_available=True,
        active=False,
    )

    with (
        patch(
            "app.automations.manager.browser_profiles",
            return_value=([profile], None),
        ),
        patch(
            "app.automations.manager.debug_chrome_status",
            return_value=("stopped", "Debug Chrome 未启动", None),
        ),
        patch("app.automations.manager.threading.Thread.start", side_effect=OSError),
        patch("app.automations.manager.write_operation") as write_operation,
    ):
        with pytest.raises(ApiError, match="无法启动浏览器用户初始化") as raised:
            manager.initialize_browser(
                "Profile 2",
                "headed",
                operation_id="operation-1",
                source_ip="100.64.0.1",
            )

    assert getattr(raised.value, "operation_logged", False) is True
    assert [call.kwargs["status"] for call in write_operation.call_args_list] == [
        "started",
        "failed",
    ]
    assert manager._browser_initialization["operation_logged"] is True


def test_manager_refuses_to_stop_browser_while_automation_lock_is_held(
    settings: Settings,
    tmp_path: Path,
) -> None:
    configure_automations(settings, tmp_path)
    manager = AutomationManager(settings)
    lock_path = settings.automations.runtime_dir / "locks" / "debug-chrome.lock"

    with file_lock(lock_path, 0):
        with pytest.raises(ApiError, match="自动化任务正在使用"):
            manager.control_browser("stop")
