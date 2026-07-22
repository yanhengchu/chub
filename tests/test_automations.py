from __future__ import annotations

import asyncio
import json
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
from app.automations.models import (
    AutomationState,
    FeishuEnvironmentState,
    LinkedDocumentResult,
)
from app.automations.operations import log_final_operation
from app.automations.runner import (
    AutomationFailed,
    _check_navigation,
    _clear_linked_markdown_files,
    _run_linked_documents,
    _run_browser_task,
    _validate_download,
    run_automation,
)
from app.automations.store import AutomationStateStore
from app.core.config import Settings
from app.core.response import ApiError


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
    settings.automations.data_dir = tmp_path / "data"
    return config_file


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


def test_load_automations_rejects_duplicate_task_ids(tmp_path: Path) -> None:
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

    with pytest.raises(RuntimeError, match="Duplicate automation task id"):
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
    template = load_linked_documents_extension(
        "v-weekly-report-linked-documents"
    )

    documents = extract_linked_documents(
        source,
        "https://tenant.feishu.cn/wiki/source",
        template,
    )

    assert [(item.name, item.url) for item in documents] == [
        ("产品端周报", "https://tenant.feishu.cn/wiki/product"),
        ("销售端周报", "https://tenant.feishu.cn/docx/sales"),
    ]


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

    first = linked_filename('产品/端：周报*', 1, used)
    second = linked_filename('产品/端：周报*', 1, used)

    assert first == "01-产品-端-周报.md"
    assert second == "01-产品-端-周报-2.md"


def test_clear_linked_markdown_files_is_limited_to_current_directory(
    tmp_path: Path,
) -> None:
    data_dir = tmp_path / "data"
    current = data_dir / "downloads" / "weekly" / "linked" / "2026-07-22"
    other_date = data_dir / "downloads" / "weekly" / "linked" / "2026-07-21"
    nested = current / "nested"
    nested.mkdir(parents=True)
    other_date.mkdir(parents=True)
    stale = current / "01-stale.md"
    preserved_file = current / "notes.txt"
    preserved_nested = nested / "manual.md"
    preserved_other_date = other_date / "01-report.md"
    stale.write_text("stale", encoding="utf-8")
    preserved_file.write_text("keep", encoding="utf-8")
    preserved_nested.write_text("keep", encoding="utf-8")
    preserved_other_date.write_text("keep", encoding="utf-8")

    _clear_linked_markdown_files(
        Path("weekly/linked/2026-07-22"),
        data_dir,
    )

    assert not stale.exists()
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

    with patch("app.automations.runner._run_task_once", fake_run):
        results = _run_linked_documents(
            task,
            source,
            settings,
            "run-1",
        )

    assert [item.status for item in results] == ["failed", "success"]
    assert len(calls) == 2
    assert calls[1].output.directory.parts[-3:-1] == (
        "weekly-report",
        "linked",
    )
    assert len(calls[1].output.directory.parts[-1]) == 10
    assert calls[1].output.filename == "02-销售端.md"


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
    settings.automations.data_dir = tmp_path / "data"
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
    assert result.output_file == str(source)
    assert result.message == "下载完成 · 主周报成功 · 关联文档 1/2 成功"
    assert result.linked_documents == linked_results


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
    output = tmp_path / "data" / "downloads" / "monthly" / "report.pdf"
    output.parent.mkdir(parents=True)
    output.write_bytes(b"%PDF-content")

    async def fake_browser_task(*_args):
        return output, output.stat().st_size, False

    with patch("app.automations.runner._run_browser_task", fake_browser_task):
        result = run_automation(settings, "monthly-report", run_id="run-1")

    assert result.status == "success"
    assert result.output_file == str(output)
    stored = AutomationStateStore(settings.automations.data_dir).read("monthly-report")
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
    lock_path = settings.automations.data_dir / "locks" / "task-monthly-report.lock"

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
    settings.automations.data_dir = tmp_path / "data"
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
    settings.automations.data_dir = tmp_path / "data"
    settings.automations.max_home_tasks = 1
    store = AutomationStateStore(settings.automations.data_dir)
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
            return_value=("running", "Debug Chrome 已运行", "有界面模式"),
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
            return_value=("running", "Debug Chrome 已运行", "有界面模式"),
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
    path = settings.automations.data_dir / "runtime" / "feishu-login-qr.png"

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
    store = AutomationStateStore(settings.automations.data_dir)
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
    store = AutomationStateStore(settings.automations.data_dir)
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
        return_value=SimpleNamespace(state="running", mode="headed"),
    ) as start:
        result = manager.control_browser("start")

    assert result.state == "running"
    assert result.mode == "有界面模式"
    start.assert_called_once_with("headed")


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
    assert result.mode == "无界面模式"
    start.assert_called_once_with("headless")


def test_manager_refuses_to_stop_browser_while_automation_lock_is_held(
    settings: Settings,
    tmp_path: Path,
) -> None:
    configure_automations(settings, tmp_path)
    manager = AutomationManager(settings)
    lock_path = settings.automations.data_dir / "locks" / "debug-chrome.lock"

    with file_lock(lock_path, 0):
        with pytest.raises(ApiError, match="自动化任务正在使用"):
            manager.control_browser("stop")
