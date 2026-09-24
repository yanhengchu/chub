from __future__ import annotations

from contextlib import contextmanager
from types import SimpleNamespace

import pytest

import app.services.weekly_report_generation as generation
from app.ai_runtime.general_settings import AiRuntimeGeneralSettings
from app.core.response import ApiError


class _SessionManager:
    runtime_id = "codex"

    def __init__(self, settings: AiRuntimeGeneralSettings) -> None:
        self.runtime_settings_store = SimpleNamespace(read_general=lambda: settings)
        self.created: list[tuple[tuple[object, ...], dict[str, object]]] = []
        self.title: str | None = None
        self.sessions: set[str] = set()

    def create_session(self, *values, **kwargs):
        self.created.append((values, kwargs))
        session_id = f"session-{len(self.created)}"
        self.sessions.add(session_id)
        return SimpleNamespace(id=session_id)

    def get_session(self, session_id: str):
        if session_id not in self.sessions:
            raise ApiError(404, "session_not_found", "AI Session not found")
        return SimpleNamespace(id=session_id)

    def rename_session(self, session_id: str, title: str) -> None:
        assert session_id in self.sessions
        self.title = title

    def discard_unstarted_session(self, session_id: str) -> None:
        self.sessions.discard(session_id)

    def require_runtime_submission(self, runtime_id: str) -> None:
        assert runtime_id == self.runtime_id

    def select_new_session_runtime(self) -> tuple[str, str]:
        return self.runtime_id, "codex-runtime-dev"


class _UnavailableSessionManager(_SessionManager):
    def require_runtime_submission(self, runtime_id: str) -> None:
        raise ApiError(503, "runtime_unavailable", "Runtime is not installed")


class _QuickInteractions:
    def __init__(self) -> None:
        self.prompt: str | None = None
        self.tasks = {"task-1": SimpleNamespace(status="running", error=None)}
        self.submissions: list[tuple[str, str]] = []
        self.operation_tasks: dict[str, SimpleNamespace] = {}
        self.raise_after_acceptance = False

    @contextmanager
    def session_creation_guard(self):
        yield

    @contextmanager
    def session_operation_guard(self, _session_id: str):
        yield

    def submit(self, session_id: str, prompt: str, **kwargs):
        self.prompt = prompt
        task_id = f"task-{len(self.submissions) + 1}"
        self.submissions.append((session_id, task_id))
        task = SimpleNamespace(
            id=task_id,
            session_id=session_id,
            kind="standard",
            status="running",
            error=None,
        )
        self.tasks[task_id] = task
        self.operation_tasks[kwargs["operation_id"]] = task
        if self.raise_after_acceptance:
            raise RuntimeError("response lost after acceptance")
        return SimpleNamespace(id=task_id)

    def get(self, task_id: str):
        return self.tasks[task_id]

    def find_for_operation(self, operation_id: str):
        return self.operation_tasks.get(operation_id)


def test_focus_generation_creates_configured_quick_session_after_download(
    tmp_path,
    monkeypatch,
) -> None:
    period = "2026-08-31至2026-09-06"
    monkeypatch.setattr(generation, "reporting_period", lambda: period)
    monkeypatch.setattr(generation, "weekly_report_inputs_available", lambda _: True)
    settings = AiRuntimeGeneralSettings(
        new_session_permission="auto-review",
        model="gpt-5.2",
        reasoning_effort="high",
    )
    manager = _SessionManager(settings)
    quick = _QuickInteractions()
    service = generation.WeeklyReportGenerationService(
        tmp_path / "weekly-report-generation.json", manager, quick
    )

    step = service.start("focus", source_ip="127.0.0.1")

    assert manager.created == [(("chub",), {"session_kind": "internal"})]
    assert manager.title == f"V 国内业务周报 · {period}"
    assert step.status == "running"
    assert "generate-weekly-report" in (quick.prompt or "")
    assert "不得打开飞书" in (quick.prompt or "")
    assert "不得生成正式周报" in (quick.prompt or "")
    assert "本周需要同步的事项" in (quick.prompt or "")
    assert "需要维护者确认的重点事项" in (quick.prompt or "")
    assert "需要确认的事项清单" in (quick.prompt or "")
    assert "无待确认事项" in (quick.prompt or "")
    assert "已完成 Stage A，生成本期工作重点确认清单" in (quick.prompt or "")
    assert "当前等待维护者确认后再进入 Stage B" in (quick.prompt or "")
    assert "不列入维护者确认事项" in (quick.prompt or "")
    assert "对正式周报的影响" in (quick.prompt or "")

    quick.tasks["task-1"] = SimpleNamespace(status="succeeded", error=None)
    assert service.read_current()["focus"].status == "succeeded"

    confirmed = {"value": False}

    def confirm_focus(_period: str) -> None:
        confirmed["value"] = True

    monkeypatch.setattr(generation, "confirm_weekly_report_focus", confirm_focus)
    monkeypatch.setattr(generation, "weekly_report_focus_confirmed", lambda _: confirmed["value"])
    report_step = service.confirm_and_start_report(source_ip="127.0.0.1")

    assert len(manager.created) == 1
    assert quick.submissions == [("session-1", "task-1"), ("session-1", "task-2")]
    assert report_step.session_id == "session-1"
    assert "已有重点确认清单和有效确认记录" in (quick.prompt or "")
    assert "不得重新生成重点确认清单" in (quick.prompt or "")
    assert "business_metrics_source_role" in (quick.prompt or "")
    assert "大盘数据表现" in (quick.prompt or "")
    assert "阶段与月度 DAU 均值" in (quick.prompt or "")
    assert "不写死 H1 或具体月份" in (quick.prompt or "")
    assert "有白牌迁移事项时" in (quick.prompt or "")
    assert "没有相关事项时省略该章节" in (quick.prompt or "")
    assert "来源标题：source_url" in (quick.prompt or "")
    assert "变化时写清可比基准" in (quick.prompt or "")
    assert "已确认时写实际值" in (quick.prompt or "")
    assert "迁移范围、当前阶段、本期完成和下一里程碑" in (quick.prompt or "")
    assert confirmed["value"] is True


def test_missing_weekly_report_session_hides_view_link_and_is_recreated(
    tmp_path,
    monkeypatch,
) -> None:
    period = "2026-08-31至2026-09-06"
    monkeypatch.setattr(generation, "reporting_period", lambda: period)
    monkeypatch.setattr(generation, "weekly_report_inputs_available", lambda _: True)
    manager = _SessionManager(AiRuntimeGeneralSettings())
    quick = _QuickInteractions()
    service = generation.WeeklyReportGenerationService(
        tmp_path / "weekly-report-generation.json", manager, quick
    )

    service.start("focus", source_ip="127.0.0.1")
    manager.sessions.clear()
    quick.tasks.clear()

    stale_step = service.read_current()["focus"]
    assert stale_step.session_id is None
    assert stale_step.status == "failed"

    restarted_step = service.start("focus", source_ip="127.0.0.1")
    assert len(manager.created) == 2
    assert manager.created[1] == manager.created[0]
    assert quick.submissions[-1][0] == "session-2"
    assert restarted_step.session_id == "session-2"


def test_formal_generation_requires_focus_confirmation(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(generation, "reporting_period", lambda: "2026-08-31至2026-09-06")
    monkeypatch.setattr(generation, "weekly_report_inputs_available", lambda _: True)
    monkeypatch.setattr(generation, "weekly_report_focus_confirmed", lambda _: False)
    quick = _QuickInteractions()
    service = generation.WeeklyReportGenerationService(
        tmp_path / "weekly-report-generation.json",
        _SessionManager(AiRuntimeGeneralSettings()),
        quick,
    )

    with pytest.raises(ApiError, match="重点确认清单"):
        service.start("report", source_ip="127.0.0.1")


def test_existing_weekly_session_keeps_its_creation_settings(
    tmp_path,
    monkeypatch,
) -> None:
    period = "2026-08-31至2026-09-06"
    monkeypatch.setattr(generation, "reporting_period", lambda: period)
    monkeypatch.setattr(generation, "weekly_report_inputs_available", lambda _: True)
    monkeypatch.setattr(generation, "weekly_report_focus_confirmed", lambda _: True)
    initial_settings = AiRuntimeGeneralSettings(
        new_session_permission="auto-review",
        model="gpt-5.2",
        reasoning_effort="high",
    )
    manager = _SessionManager(initial_settings)
    quick = _QuickInteractions()
    service = generation.WeeklyReportGenerationService(
        tmp_path / "weekly-report-generation.json", manager, quick
    )

    service.start("focus", source_ip="127.0.0.1")
    quick.tasks["task-1"] = SimpleNamespace(status="succeeded", error=None)
    manager.runtime_settings_store.read_general = lambda: AiRuntimeGeneralSettings(
        new_session_permission="read-only"
    )

    assert service.configuration_ready() == (True, None)
    service.start("report", source_ip="127.0.0.1")
    assert len(manager.created) == 1
    assert quick.submissions == [("session-1", "task-1"), ("session-1", "task-2")]


def test_started_generation_task_remains_running_and_blocks_new_submission(
    tmp_path,
    monkeypatch,
) -> None:
    period = "2026-08-31至2026-09-06"
    monkeypatch.setattr(generation, "reporting_period", lambda: period)
    monkeypatch.setattr(generation, "weekly_report_inputs_available", lambda _: True)
    quick = _QuickInteractions()
    service = generation.WeeklyReportGenerationService(
        tmp_path / "weekly-report-generation.json",
        _SessionManager(AiRuntimeGeneralSettings()),
        quick,
    )

    service.start("focus", source_ip="127.0.0.1")
    service._quick_interactions.tasks["task-1"] = SimpleNamespace(
        status="started", error=None
    )

    assert service.read_current()["focus"].status == "running"
    with pytest.raises(ApiError, match="正在执行"):
        service.start("focus", source_ip="127.0.0.1")
    with pytest.raises(ApiError, match="正在执行"):
        service.confirm_and_start_report(source_ip="127.0.0.1")
    assert quick.submissions == [("session-1", "task-1")]


def test_focus_generation_requires_valid_manifest_inputs(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(generation, "reporting_period", lambda: "2026-08-31至2026-09-06")
    monkeypatch.setattr(generation, "weekly_report_inputs_available", lambda _: False)
    service = generation.WeeklyReportGenerationService(
        tmp_path / "weekly-report-generation.json",
        _SessionManager(AiRuntimeGeneralSettings()),
        _QuickInteractions(),
    )

    with pytest.raises(ApiError, match="Manifest"):
        service.start("focus", source_ip="127.0.0.1")


def test_read_only_session_default_is_not_runnable_for_weekly_reports(tmp_path) -> None:
    service = generation.WeeklyReportGenerationService(
        tmp_path / "weekly-report-generation.json",
        _SessionManager(
            AiRuntimeGeneralSettings(new_session_permission="read-only")
        ),
        _QuickInteractions(),
    )

    assert service.configuration_ready() == (
        False,
        "当前周报自动化会话为只读权限，无法生成周报产物。",
    )


def test_missing_runtime_disables_weekly_generation_even_with_existing_session(
    tmp_path,
    monkeypatch,
) -> None:
    period = "2026-08-31至2026-09-06"
    monkeypatch.setattr(generation, "reporting_period", lambda: period)
    manager = _UnavailableSessionManager(AiRuntimeGeneralSettings())
    manager.sessions.add("session-1")
    service = generation.WeeklyReportGenerationService(
        tmp_path / "weekly-report-generation.json", manager, _QuickInteractions()
    )
    service._save_stage(period, "focus", session_id="session-1", task_id="task-1")

    assert service.configuration_ready() == (False, "Runtime is not installed")


def test_weekly_generation_recovers_accepted_task_when_submission_response_is_lost(
    tmp_path,
    monkeypatch,
) -> None:
    period = "2026-08-31至2026-09-06"
    monkeypatch.setattr(generation, "reporting_period", lambda: period)
    monkeypatch.setattr(generation, "weekly_report_inputs_available", lambda _: True)
    quick = _QuickInteractions()
    quick.raise_after_acceptance = True
    service = generation.WeeklyReportGenerationService(
        tmp_path / "weekly-report-generation.json",
        _SessionManager(AiRuntimeGeneralSettings()),
        quick,
    )

    step = service.start("focus", source_ip="127.0.0.1")

    assert step.task_id == "task-1"
    assert step.status == "running"
    assert quick.submissions == [("session-1", "task-1")]


def test_weekly_generation_marks_unlinked_submission_as_failed(tmp_path, monkeypatch) -> None:
    period = "2026-08-31至2026-09-06"
    monkeypatch.setattr(generation, "reporting_period", lambda: period)
    monkeypatch.setattr(generation, "weekly_report_inputs_available", lambda _: True)
    quick = _QuickInteractions()
    service = generation.WeeklyReportGenerationService(
        tmp_path / "weekly-report-generation.json",
        _SessionManager(AiRuntimeGeneralSettings()),
        quick,
    )
    service._save_stage(
        period,
        "focus",
        session_id="session-1",
        task_id=None,
        operation_id="missing-operation",
        status="submitting",
    )

    step = service.read_current()["focus"]

    assert step.status == "failed"
    assert step.task_id is None
