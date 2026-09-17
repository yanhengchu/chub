from __future__ import annotations

import json
from contextlib import nullcontext
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from app.ai_search.models import SearchRun
from app.ai_search.service import AiSearchService
from app.application import create_app
from app.automations.browser import (
    DebugChromePageContent,
    DebugChromePageReadError,
)
from app.core.response import ApiError


class _Manager:
    def __init__(self, session_id: str = "session-1") -> None:
        self.session_id = session_id
        self.sessions: dict[str, SimpleNamespace] = {}
        self.create_calls = 0
        self.created_workspace_id: str | None = None
        self.permission_mode: str | None = None
        self.title: str | None = None
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

    def submission_available(self) -> tuple[bool, str | None]:
        return True, None

    def get_session(self, session_id: str):
        session = self.sessions.get(session_id)
        if session is None:
            raise ApiError(404, "session_not_found", "AI Session not found")
        return session

    def rename_session(self, _session_id: str, title: str) -> None:
        self.title = title

    def ensure_delete_allowed(self, _session_id: str, *, reconcile: bool) -> None:
        assert reconcile is False

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
        self.operation_tasks: dict[str, SimpleNamespace] = {}

    def session_creation_guard(self):
        return nullcontext()

    def session_operation_guard(self, _session_id: str):
        return nullcontext()

    def destructive_operation_guard(self, _session_id: str):
        return nullcontext()

    def submit(self, session_id: str, prompt: str, **kwargs):
        self.prompt = prompt
        self.task.session_id = session_id
        self.operation_tasks[kwargs["operation_id"]] = self.task
        return self.task

    def get(self, _task_id: str):
        return self.task

    def find_for_operation(self, operation_id: str):
        return self.operation_tasks.get(operation_id)

    def cancel_session_interactions(self, _session_id: str) -> bool:
        return True

    def remove_session_tasks(self, _session_id: str) -> None:
        return None


async def _read_page(url: str, *, max_content_chars: int) -> DebugChromePageContent:
    return DebugChromePageContent(
        source_url=url,
        final_url=url,
        title="Official AI news",
        content="A bounded official update.",
        truncated=False,
    )


def _service(path: Path, **kwargs) -> AiSearchService:
    return AiSearchService(path, page_reader=_read_page, **kwargs)


def test_today_focus_refreshes_one_latest_snapshot(tmp_path: Path) -> None:
    service = _service(tmp_path / "ai-search.json")
    manager = _Manager()
    quick = _QuickInteractions()
    service.refresh(manager, quick, source_ip="127.0.0.1")
    assert manager.permission_mode is None
    quick.task.status = "succeeded"
    quick.task.result = json.dumps({
        "summary": "今天有一条值得关注的模型更新。",
        "results": [{
            "title": "Example update",
            "url": "https://openai.com/news/update",
            "description": "A public AI update.",
            "source": "Example",
        }],
    })

    data = service.current(manager, quick)

    assert data.current is None
    assert data.latest is not None
    assert data.latest.summary == "今天有一条值得关注的模型更新。"
    assert data.latest.results[0].title == "Example update"
    assert data.latest.results[0].source == "OpenAI"
    assert service._state.latest_run is not None


def test_today_focus_replaces_the_previous_snapshot_on_the_next_refresh(tmp_path: Path) -> None:
    service = _service(tmp_path / "ai-search.json")
    manager = _Manager()
    quick = _QuickInteractions()
    service.refresh(manager, quick, source_ip="127.0.0.1")
    quick.task.status = "succeeded"
    quick.task.result = json.dumps({"summary": "第一份摘要", "results": []})
    assert service.current(manager, quick).latest is not None

    quick.task = SimpleNamespace(
        id="task-2", session_id="session-1", kind="standard", status="requested", result=None, error=None,
    )
    refreshed = service.refresh(manager, quick, source_ip="127.0.0.1")

    assert refreshed.current is not None
    assert refreshed.latest is not None
    assert refreshed.latest.summary == "第一份摘要"
    assert manager.create_calls == 1


def test_today_focus_reads_sources_in_chub_before_using_an_existing_session_snapshot(tmp_path: Path) -> None:
    observed_urls: list[str] = []

    async def read_page(url: str, *, max_content_chars: int) -> DebugChromePageContent:
        observed_urls.append(url)
        return await _read_page(url, max_content_chars=max_content_chars)

    service = AiSearchService(tmp_path / "ai-search.json", page_reader=read_page)
    manager = _Manager()
    quick = _QuickInteractions()
    service.refresh(manager, quick, source_ip="127.0.0.1")
    manager.sessions["session-1"].permission_mode = "read-only"
    quick.task.status = "succeeded"
    quick.task.result = json.dumps({"summary": "第一份摘要", "results": []})
    service.current(manager, quick)

    quick.task = SimpleNamespace(
        id="task-2", session_id="session-1", kind="standard", status="requested", result=None, error=None,
    )
    service.refresh(manager, quick, source_ip="127.0.0.1")

    assert manager.create_calls == 1
    assert manager.permission_mode is None
    assert observed_urls == [url for _, url in (
        ("OpenAI", "https://openai.com/news/"),
        ("Anthropic", "https://www.anthropic.com/news"),
        ("Google AI", "https://blog.google/technology/ai/"),
        ("Hugging Face", "https://huggingface.co/blog"),
    )] * 2


def test_today_focus_reads_sources_before_creating_a_session(tmp_path: Path) -> None:
    events: list[str] = []

    class OrderedManager(_Manager):
        def create_session(self, workspace_id: str, permission_mode: str | None = None):
            events.append("session-created")
            return super().create_session(workspace_id, permission_mode)

    async def read_page(url: str, *, max_content_chars: int) -> DebugChromePageContent:
        events.append(f"read:{url}")
        return await _read_page(url, max_content_chars=max_content_chars)

    service = AiSearchService(tmp_path / "ai-search.json", page_reader=read_page)
    service.refresh(OrderedManager(), _QuickInteractions(), source_ip="127.0.0.1")

    assert events[-1] == "session-created"
    assert len(events) == 5


def test_today_focus_rejects_unavailable_runtime_before_reading_sources(tmp_path: Path) -> None:
    reads: list[str] = []

    class UnavailableManager(_Manager):
        def submission_available(self) -> tuple[bool, str | None]:
            return False, "当前 Runtime 插件尚未导入，无法提交新的 AI 任务。"

    async def read_page(url: str, *, max_content_chars: int) -> DebugChromePageContent:
        reads.append(url)
        return await _read_page(url, max_content_chars=max_content_chars)

    service = AiSearchService(tmp_path / "ai-search.json", page_reader=read_page)

    with pytest.raises(ApiError) as raised:
        service.refresh(UnavailableManager(), _QuickInteractions(), source_ip="127.0.0.1")

    assert raised.value.code == "today_focus_runtime_unavailable"
    assert reads == []


def test_today_focus_rejects_source_snapshots_redirected_outside_official_hosts(tmp_path: Path) -> None:
    async def read_page(url: str, *, max_content_chars: int) -> DebugChromePageContent:
        if url == "https://openai.com/news/":
            return DebugChromePageContent(
                source_url=url,
                final_url="https://example.com/redirected",
                title="Unexpected redirect",
                content="Untrusted page content.",
                truncated=False,
            )
        return await _read_page(url, max_content_chars=max_content_chars)

    service = AiSearchService(tmp_path / "ai-search.json", page_reader=read_page)

    snapshots = service._read_source_snapshots()

    assert isinstance(snapshots[0], DebugChromePageReadError)
    assert all(isinstance(snapshot, DebugChromePageContent) for snapshot in snapshots[1:])


def test_today_focus_visibility_defaults_to_hidden_and_persists(tmp_path: Path) -> None:
    service = _service(tmp_path / "ai-search.json")
    manager = _Manager()
    quick = _QuickInteractions()
    service.refresh(manager, quick, source_ip="127.0.0.1")

    assert service.show_sessions() is False
    assert service.hidden_session_ids() == {"session-1"}
    assert service.set_show_sessions(True) is True
    assert service.hidden_session_ids() == set()
    assert _service(tmp_path / "ai-search.json").show_sessions() is True


def test_today_focus_discards_legacy_state_without_touching_its_bound_session(tmp_path: Path) -> None:
    path = tmp_path / "ai-search.json"
    legacy = SearchRun(
        id="a" * 32,
        session_id="session-1",
        operation_id="operation-1",
        task_id="task-1",
        query="旧搜索",
        prompt="旧提示词",
        status="succeeded",
        summary="旧摘要",
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    path.write_text(json.dumps({
        "version": 6,
        "show_sessions": True,
        "session_id": "session-1",
        "runs": [legacy.model_dump(mode="json")],
    }), encoding="utf-8")

    manager = _Manager()
    service = _service(path)
    data = service.current(manager, _QuickInteractions())

    assert data.current is None
    assert data.latest is None
    assert manager.native_deleted == []
    assert json.loads(path.read_text(encoding="utf-8")) == {
        "version": 7,
        "show_sessions": False,
        "session_id": None,
        "pending_run": None,
        "latest_run": None,
    }


def test_today_focus_rejects_result_links_outside_fixed_sources(tmp_path: Path) -> None:
    service = _service(tmp_path / "ai-search.json")

    with pytest.raises(ValueError, match="固定来源之外"):
        service._parse_result(json.dumps({
            "summary": "摘要",
            "results": [{
                "title": "Unexpected",
                "url": "https://example.com/update",
                "description": "Unexpected source.",
                "source": "Example",
            }],
        }))


@pytest.mark.anyio
async def test_today_focus_api_requires_trusted_network(settings) -> None:
    app = create_app(settings)
    transport = httpx.ASGITransport(app=app, client=("192.0.2.1", 12345))

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/api/today-focus")

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "trusted_network_required"


@pytest.mark.anyio
async def test_today_focus_no_longer_exposes_page_opening(settings) -> None:
    app = create_app(settings)
    transport = httpx.ASGITransport(app=app)

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post("/api/today-focus/open-pages")

    assert response.status_code == 404


@pytest.mark.anyio
async def test_today_focus_session_visibility_setting_is_independent_of_snapshot(settings) -> None:
    app = create_app(settings)
    transport = httpx.ASGITransport(app=app)

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        current = await client.get("/api/today-focus/settings")
        updated = await client.put("/api/today-focus/settings", json={"show_sessions": True})
        restored = await client.get("/api/today-focus/settings")

    assert current.json()["data"] == {"show_sessions": False}
    assert updated.json()["data"] == {"show_sessions": True}
    assert restored.json()["data"] == {"show_sessions": True}
