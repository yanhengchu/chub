from __future__ import annotations

import json
from contextlib import nullcontext
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from app.ai_search.models import AiSearchState, SearchRun
from app.ai_search.service import LEGACY_PROMPT, AiSearchService
from app.application import create_app
from app.core.response import ApiError


class _Manager:
    def __init__(self, session_id: str = "session-1") -> None:
        self.session_id = session_id
        self.sessions: dict[str, SimpleNamespace] = {}
        self.create_calls = 0
        self.created_workspace_id: str | None = None
        self.permission_mode: str | None = None
        self.title: str | None = None
        self.delete_checked: list[str] = []
        self.native_deleted: list[str] = []
        self.finalized_deletes: list[str] = []

    def create_session(self, workspace_id: str, permission_mode: str | None = None):
        self.create_calls += 1
        self.created_workspace_id = workspace_id
        self.permission_mode = permission_mode
        session = SimpleNamespace(
            id=self.session_id,
            workspace_id=workspace_id,
            permission_mode=permission_mode,
            status="new",
        )
        self.sessions[session.id] = session
        return session

    def get_session(self, session_id: str):
        session = self.sessions.get(session_id)
        if session is None:
            raise ApiError(404, "codex_session_not_found", "Codex session not found")
        return session

    def rename_session(self, _session_id: str, title: str) -> None:
        self.title = title

    def ensure_delete_allowed(self, session_id: str, *, reconcile: bool) -> None:
        assert reconcile is False
        self.delete_checked.append(session_id)

    def delete_native_session(self, session_id: str) -> None:
        self.native_deleted.append(session_id)

    def finalize_delete_session(self, session_id: str) -> None:
        self.finalized_deletes.append(session_id)
        self.sessions.pop(session_id, None)


class _QuickInteractions:
    def __init__(self) -> None:
        self.task = SimpleNamespace(
            id="task-1",
            session_id="session-1",
            kind="standard",
            status="requested",
            result=None,
            error=None,
        )
        self.prompt = ""
        self.capability_ids = ()
        self.cancelled_sessions: list[str] = []
        self.removed_task_sessions: list[str] = []
        self.submitted_sessions: set[str] = set()
        self.operation_tasks: dict[str, SimpleNamespace] = {}
        self.list_for_session_calls: list[str] = []

    def session_creation_guard(self):
        return nullcontext()

    def session_operation_guard(self, _session_id: str):
        return nullcontext()

    def destructive_operation_guard(self, _session_id: str):
        return nullcontext()

    def submit(self, session_id: str, prompt: str, **kwargs):
        self.prompt = prompt
        self.capability_ids = kwargs["capability_ids"]
        self.submitted_sessions.add(session_id)
        self.task.session_id = session_id
        self.operation_tasks[kwargs["operation_id"]] = self.task
        return self.task

    def get(self, _task_id: str):
        return self.task

    def list_for_session(self, session_id: str):
        self.list_for_session_calls.append(session_id)
        return [self.task] if session_id in self.submitted_sessions else []

    def find_for_operation(self, operation_id: str):
        return self.operation_tasks.get(operation_id)

    def cancel_codex_session(self, session_id: str) -> bool:
        self.cancelled_sessions.append(session_id)
        return True

    def remove_session_tasks(self, session_id: str) -> None:
        self.removed_task_sessions.append(session_id)


class _RenameFailureManager(_Manager):
    def rename_session(self, _session_id: str, _title: str) -> None:
        raise RuntimeError("rename unavailable")


class _CommitFailingSearchService(AiSearchService):
    def __init__(self, state_path: Path, *, fail_at: int) -> None:
        super().__init__(state_path)
        self.commit_attempts = 0
        self.fail_at: int | None = fail_at

    def _commit(self, state: AiSearchState) -> None:
        self.commit_attempts += 1
        if self.commit_attempts == self.fail_at:
            raise ApiError(503, "ai_search_state_unavailable", "AI 搜索本机状态无法保存。")
        super()._commit(state)


def test_ai_search_creates_a_general_session_and_grants_bounded_browser_reading(tmp_path: Path) -> None:
    service = AiSearchService(tmp_path / "ai-search.json")
    manager = _Manager()
    quick = _QuickInteractions()

    data = service.submit("查找类似 https://www.aihero.dev/skills 的高星技能库", manager, quick, source_ip="127.0.0.1")

    assert data.current is not None and data.current.status == "requested"
    assert manager.created_workspace_id == "home"
    assert manager.permission_mode == "read-only"
    assert manager.title == "搜索"
    assert "https://www.aihero.dev/skills" in quick.prompt
    assert quick.capability_ids == (
        "chub.debug_chrome.page.interact",
        "chub.debug_chrome.page.read",
    )


def test_ai_search_session_visibility_defaults_to_hidden_and_persists(tmp_path: Path) -> None:
    service = AiSearchService(tmp_path / "ai-search.json")

    assert service.show_sessions() is False
    assert service.set_show_sessions(True) is True
    quick = _QuickInteractions()
    manager = _Manager()
    service.submit("浏览器自动化技能", manager, quick, source_ip="127.0.0.1")
    quick.task.status = "running"
    service.current(manager, quick)

    assert service.show_sessions() is True
    assert service.hidden_session_ids() == set()
    assert service.set_show_sessions(False) is False
    assert service.hidden_session_ids() == {"session-1"}

    restored = AiSearchService(tmp_path / "ai-search.json")
    assert restored.show_sessions() is False


def test_ai_search_refreshes_structured_task_result(tmp_path: Path) -> None:
    service = AiSearchService(tmp_path / "ai-search.json")
    manager = _Manager()
    quick = _QuickInteractions()
    service.submit("浏览器自动化技能", manager, quick, source_ip="127.0.0.1")
    quick.task.status = "succeeded"
    quick.task.result = json.dumps({
        "summary": "找到一个候选。",
        "results": [{
            "title": "Example Skill",
            "url": "https://github.com/example/skill",
            "description": "Public browser automation skill.",
            "source": "GitHub",
        }],
    })

    data = service.current(manager, quick)

    assert data.current is None
    assert data.runs[0].status == "succeeded"
    assert data.runs[0].results[0].title == "Example Skill"


def test_ai_search_keeps_the_shared_session_when_submission_fails(tmp_path: Path) -> None:
    service = AiSearchService(tmp_path / "ai-search.json")
    manager = _RenameFailureManager()
    quick = _QuickInteractions()

    with pytest.raises(ApiError) as raised:
        service.submit("浏览器自动化技能", manager, quick, source_ip="127.0.0.1")

    assert raised.value.status_code == 503
    assert raised.value.code == "ai_search_submit_failed"
    data = service.current(manager, quick)
    assert data.current is None
    assert data.runs == []
    assert manager.native_deleted == []
    assert service.hidden_session_ids() == {"session-1"}
    assert quick.prompt == ""


def test_ai_search_rolls_back_a_new_session_when_pending_state_cannot_be_saved(tmp_path: Path) -> None:
    service = _CommitFailingSearchService(tmp_path / "ai-search.json", fail_at=1)
    manager = _Manager()
    quick = _QuickInteractions()

    with pytest.raises(ApiError) as raised:
        service.submit("浏览器自动化技能", manager, quick, source_ip="127.0.0.1")

    assert raised.value.code == "ai_search_state_unavailable"
    assert quick.prompt == ""
    assert manager.native_deleted == ["session-1"]
    assert manager.finalized_deletes == ["session-1"]


def test_ai_search_reports_an_accepted_task_when_task_state_recording_is_pending(tmp_path: Path) -> None:
    service = _CommitFailingSearchService(tmp_path / "ai-search.json", fail_at=2)
    manager = _Manager()
    quick = _QuickInteractions()

    with pytest.raises(ApiError) as raised:
        service.submit("浏览器自动化技能", manager, quick, source_ip="127.0.0.1")

    assert raised.value.code == "ai_search_submission_recording_pending"
    assert quick.submitted_sessions == {"session-1"}
    assert manager.native_deleted == []
    service.fail_at = None

    data = service.current(manager, quick)

    assert data.current is not None and data.current.session_id == "session-1"
    assert len(data.runs) == 1


def test_ai_search_recovers_a_pending_submission_by_its_exact_operation_id(tmp_path: Path) -> None:
    path = tmp_path / "ai-search.json"
    pending = SearchRun(
        id="a" * 32,
        session_id="session-1",
        operation_id="search-operation",
        task_id=None,
        query="恢复中的搜索",
        prompt="搜索提示词",
        status="submitting",
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    path.write_text(AiSearchState(session_id="session-1", pending_run=pending).model_dump_json(), encoding="utf-8")
    service = AiSearchService(path)
    manager = _Manager()
    manager.sessions["session-1"] = SimpleNamespace(
        id="session-1", workspace_id="home", permission_mode="read-only", status="new"
    )
    quick = _QuickInteractions()
    search_task = SimpleNamespace(
        id="task-search", session_id="session-1", kind="standard", status="requested", result=None, error=None
    )
    quick.task = search_task
    quick.operation_tasks["search-operation"] = search_task
    quick.submitted_sessions.add("session-1")

    data = service.current(manager, quick)

    assert data.current is not None and data.current.task_id == "task-search"
    assert quick.list_for_session_calls == []


def test_ai_search_keeps_only_the_latest_eight_records_without_deleting_the_shared_session(tmp_path: Path) -> None:
    path = tmp_path / "ai-search.json"
    existing = [
        SearchRun(
            id=f"{index:032x}",
            session_id=f"session-{index}",
            task_id=f"task-{index}",
            query=f"历史搜索 {index}",
            prompt="搜索提示词",
            status="succeeded",
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )
        for index in range(8, 0, -1)
    ]
    path.write_text(
        AiSearchState(show_sessions=True, session_id="session-shared", runs=[
            run.model_copy(update={"session_id": "session-shared"}) for run in existing
        ]).model_dump_json(),
        encoding="utf-8",
    )
    service = AiSearchService(path)
    manager = _Manager(session_id="session-new")
    manager.sessions["session-shared"] = SimpleNamespace(
        id="session-shared", workspace_id="home", permission_mode="read-only", status="stopped"
    )
    quick = _QuickInteractions()

    data = service.submit("最新搜索", manager, quick, source_ip="127.0.0.1")

    assert len(data.runs) == 8
    assert data.runs[0].query == "最新搜索"
    assert [run.query for run in data.runs[1:]] == [f"历史搜索 {index}" for index in range(8, 1, -1)]
    assert data.runs[0].session_id == "session-shared"
    assert manager.create_calls == 0
    assert manager.native_deleted == []
    assert quick.cancelled_sessions == []
    assert service.show_sessions() is True


class _ReplacementFailureManager(_Manager):
    def ensure_delete_allowed(self, _session_id: str, *, reconcile: bool) -> None:
        assert reconcile is False
        raise ApiError(409, "codex_session_writer_active", "Session is active")


def test_ai_search_refuses_to_replace_an_unusable_session_until_the_old_one_is_deleted(tmp_path: Path) -> None:
    path = tmp_path / "ai-search.json"
    path.write_text(AiSearchState(session_id="session-old").model_dump_json(), encoding="utf-8")
    service = AiSearchService(path)
    manager = _ReplacementFailureManager(session_id="session-new")
    manager.sessions["session-old"] = SimpleNamespace(
        id="session-old", workspace_id="home", permission_mode="read-only", status="error"
    )
    quick = _QuickInteractions()
    with pytest.raises(ApiError) as raised:
        service.submit("最新搜索", manager, quick, source_ip="127.0.0.1")

    assert raised.value.code == "ai_search_session_replacement_failed"
    assert manager.create_calls == 0


def test_ai_search_replaces_an_unusable_session_only_after_deleting_it(tmp_path: Path) -> None:
    path = tmp_path / "ai-search.json"
    path.write_text(AiSearchState(session_id="session-old").model_dump_json(), encoding="utf-8")
    service = AiSearchService(path)
    manager = _Manager(session_id="session-new")
    manager.sessions["session-old"] = SimpleNamespace(
        id="session-old", workspace_id="home", permission_mode="read-only", status="error"
    )
    quick = _QuickInteractions()

    data = service.submit("最新搜索", manager, quick, source_ip="127.0.0.1")

    assert data.current is not None and data.current.session_id == "session-new"
    assert manager.native_deleted == ["session-old"]
    assert manager.finalized_deletes == ["session-old"]
    assert manager.create_calls == 1


def test_ai_search_reuses_the_shared_session_for_later_searches(tmp_path: Path) -> None:
    service = AiSearchService(tmp_path / "ai-search.json")
    manager = _Manager()
    quick = _QuickInteractions()
    service.submit("第一次搜索", manager, quick, source_ip="127.0.0.1")
    quick.task.status = "failed"
    quick.task.error = "任务结束"
    service.current(manager, quick)

    data = service.submit("第二次搜索", manager, quick, source_ip="127.0.0.1")

    assert data.current is not None
    assert data.current.session_id == "session-1"
    assert manager.create_calls == 1
    assert manager.native_deleted == []


def test_ai_search_marks_an_unconfirmed_legacy_submission_as_failed_after_the_recovery_window(tmp_path: Path) -> None:
    path = tmp_path / "ai-search.json"
    path.write_text(
        AiSearchState(
            show_sessions=True,
            runs=[SearchRun(
                id="a" * 32,
                session_id="session-1",
                task_id=None,
                query="浏览器自动化技能",
                prompt="搜索提示词",
                status="submitting",
                created_at=datetime.now(UTC) - timedelta(seconds=61),
                updated_at=datetime.now(UTC) - timedelta(seconds=61),
            )],
        ).model_dump_json(),
        encoding="utf-8",
    )
    service = AiSearchService(path)
    manager = _Manager()
    quick = _QuickInteractions()

    data = service.current(manager, quick)

    assert data.current is None
    assert data.runs[0].status == "failed"
    assert data.runs[0].error == "搜索任务提交状态未能确认，可再次发起。"
    assert manager.native_deleted == []
    assert service.show_sessions() is True


def test_ai_search_recovers_an_accepted_pending_run_without_removing_history_session(tmp_path: Path) -> None:
    path = tmp_path / "ai-search.json"
    history = [
        SearchRun(
            id=f"{index:032x}", session_id=f"session-{index}", task_id=f"task-{index}",
            query=f"历史搜索 {index}", prompt="搜索提示词", status="succeeded",
            created_at=datetime.now(UTC), updated_at=datetime.now(UTC),
        )
        for index in range(8, 0, -1)
    ]
    pending = SearchRun(
        id="f" * 32, session_id="session-new", task_id=None, query="恢复中的搜索",
        prompt="搜索提示词", status="submitting", created_at=datetime.now(UTC), updated_at=datetime.now(UTC),
    )
    path.write_text(
        AiSearchState(session_id="session-new", runs=history, pending_run=pending).model_dump_json(),
        encoding="utf-8",
    )
    service = AiSearchService(path)
    manager = _Manager(session_id="session-new")
    quick = _QuickInteractions()
    quick.submitted_sessions.add("session-new")

    data = service.current(manager, quick)

    assert data.current is not None and data.current.query == "恢复中的搜索"
    assert [run.query for run in data.runs] == [
        "恢复中的搜索", *[f"历史搜索 {index}" for index in range(8, 1, -1)]
    ]
    assert manager.native_deleted == []


def test_ai_search_retires_legacy_per_search_sessions_before_establishing_shared_session(tmp_path: Path) -> None:
    path = tmp_path / "ai-search.json"
    history = [
        SearchRun(
            id=f"{index:032x}", session_id=f"session-{index}", task_id=f"task-{index}",
            query=f"历史搜索 {index}", prompt="搜索提示词", status="succeeded",
            created_at=datetime.now(UTC), updated_at=datetime.now(UTC),
        )
        for index in range(8, 0, -1)
    ]
    path.write_text(AiSearchState(runs=history).model_dump_json(), encoding="utf-8")
    service = AiSearchService(path)
    manager = _Manager(session_id="session-new")
    data = service.submit("最新搜索", manager, _QuickInteractions(), source_ip="127.0.0.1")

    assert data.current is not None and data.current.session_id == "session-new"
    assert manager.native_deleted == [f"session-{index}" for index in range(1, 9)]
    assert manager.create_calls == 1


def test_ai_search_preserves_a_completed_v1_record_in_history(tmp_path: Path) -> None:
    path = tmp_path / "ai-search.json"
    path.write_text(json.dumps({
        "version": 1,
        "current": {
            "session_id": "session-1",
            "task_id": "task-1",
            "query": "查找公开 AI 技能库",
            "status": "succeeded",
            "summary": "找到一个历史候选。",
            "results": [{
                "title": "Example Skill",
                "url": "https://github.com/example/skill",
                "description": "Public AI skill directory.",
                "source": "GitHub",
            }],
            "error": None,
            "created_at": "2026-09-15T11:52:53.600036Z",
            "updated_at": "2026-09-15T11:53:48.199282Z",
        },
    }), encoding="utf-8")

    service = AiSearchService(path)

    manager = _Manager()
    data = service.current(manager, _QuickInteractions())

    assert data.current is None
    assert len(data.runs) == 1
    assert data.runs[0].query == "查找公开 AI 技能库"
    assert data.runs[0].prompt == LEGACY_PROMPT
    assert data.runs[0].results[0].title == "Example Skill"
    assert service.get(data.runs[0].id, manager, _QuickInteractions()).summary == "找到一个历史候选。"


@pytest.mark.anyio
async def test_ai_search_api_requires_trusted_network(settings) -> None:
    app = create_app(settings)
    transport = httpx.ASGITransport(app=app, client=("192.0.2.1", 12345))

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/api/search/session")

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "trusted_network_required"


@pytest.mark.anyio
async def test_ai_search_session_visibility_setting_is_independent_of_search_runs(settings) -> None:
    app = create_app(settings)
    transport = httpx.ASGITransport(app=app)

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        current = await client.get("/api/search/settings")
        updated = await client.put("/api/search/settings", json={"show_sessions": True})
        restored = await client.get("/api/search/settings")

    assert current.status_code == 200
    assert current.json()["data"] == {"show_sessions": False}
    assert updated.status_code == 200
    assert updated.json()["data"] == {"show_sessions": True}
    assert restored.json()["data"] == {"show_sessions": True}
