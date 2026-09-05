from __future__ import annotations

from contextlib import contextmanager
from types import SimpleNamespace

import pytest

import app.services.weekly_report_generation as generation
from app.codex.usage_settings import (
    AiRuntimeGeneralSettings,
    WeeklyReportSessionSettings,
)
from app.core.response import ApiError


class _SessionManager:
    runtime_id = "codex"

    def __init__(self, settings: AiRuntimeGeneralSettings) -> None:
        self.runtime_settings_store = SimpleNamespace(read_general=lambda: settings)
        self.created: list[tuple[object, ...]] = []
        self.title: str | None = None
        self.sessions: set[str] = set()

    def create_session(self, *values):
        self.created.append(values)
        session_id = f"session-{len(self.created)}"
        self.sessions.add(session_id)
        return SimpleNamespace(id=session_id)

    def get_session(self, session_id: str):
        if session_id not in self.sessions:
            raise ApiError(404, "codex_session_not_found", "Codex session not found")
        return SimpleNamespace(id=session_id)

    def rename_session(self, session_id: str, title: str) -> None:
        assert session_id in self.sessions
        self.title = title

    def discard_unstarted_session(self, session_id: str) -> None:
        self.sessions.discard(session_id)


class _QuickInteractions:
    def __init__(self) -> None:
        self.prompt: str | None = None
        self.tasks = {"task-1": SimpleNamespace(status="running", error=None)}
        self.submissions: list[tuple[str, str]] = []

    @contextmanager
    def session_creation_guard(self, _kind: str):
        yield

    @contextmanager
    def session_operation_guard(self, _session_id: str):
        yield

    def submit(self, session_id: str, prompt: str, **_kwargs):
        self.prompt = prompt
        task_id = f"task-{len(self.submissions) + 1}"
        self.submissions.append((session_id, task_id))
        self.tasks[task_id] = SimpleNamespace(status="running", error=None)
        return SimpleNamespace(id=task_id)

    def get(self, task_id: str):
        return self.tasks[task_id]


def test_focus_generation_creates_configured_quick_session_after_download(
    tmp_path,
    monkeypatch,
) -> None:
    period = "2026-08-31至2026-09-06"
    monkeypatch.setattr(generation, "reporting_period", lambda: period)
    monkeypatch.setattr(generation, "weekly_report_inputs_available", lambda _: True)
    settings = AiRuntimeGeneralSettings(
        weekly_report_session=WeeklyReportSessionSettings(
            permission_mode="auto-review",
            model="gpt-5.2",
            reasoning_effort="high",
        )
    )
    manager = _SessionManager(settings)
    quick = _QuickInteractions()
    service = generation.WeeklyReportGenerationService(
        tmp_path / "weekly-report-generation.json", manager, quick
    )

    step = service.start("focus", source_ip="127.0.0.1")

    assert manager.created == [("chub", "auto-review", "gpt-5.2", "high", "quick")]
    assert manager.title == f"V 国内业务周报 · {period}"
    assert step.status == "running"
    assert "generate-weekly-report" in (quick.prompt or "")
    assert "不得打开飞书" in (quick.prompt or "")
    assert "不得生成正式周报" in (quick.prompt or "")
    assert "本周需要同步的事项" in (quick.prompt or "")
    assert "需要维护者确认的重点事项" in (quick.prompt or "")

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
        weekly_report_session=WeeklyReportSessionSettings(
            permission_mode="auto-review",
            model="gpt-5.2",
            reasoning_effort="high",
        )
    )
    manager = _SessionManager(initial_settings)
    quick = _QuickInteractions()
    service = generation.WeeklyReportGenerationService(
        tmp_path / "weekly-report-generation.json", manager, quick
    )

    service.start("focus", source_ip="127.0.0.1")
    quick.tasks["task-1"] = SimpleNamespace(status="succeeded", error=None)
    manager.runtime_settings_store.read_general = lambda: AiRuntimeGeneralSettings(
        weekly_report_session=WeeklyReportSessionSettings(permission_mode="read-only")
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


def test_read_only_weekly_report_session_is_not_runnable(tmp_path) -> None:
    service = generation.WeeklyReportGenerationService(
        tmp_path / "weekly-report-generation.json",
        _SessionManager(
            AiRuntimeGeneralSettings(
                weekly_report_session=WeeklyReportSessionSettings(
                    permission_mode="read-only"
                )
            )
        ),
        _QuickInteractions(),
    )

    assert service.configuration_ready() == (
        False,
        "当前周报自动化会话为只读权限，无法生成周报产物。",
    )
