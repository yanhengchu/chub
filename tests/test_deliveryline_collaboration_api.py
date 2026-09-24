from __future__ import annotations

import json
from contextlib import nullcontext
from types import SimpleNamespace

import httpx
import pytest

from app.application import create_app
from app.core.response import ApiError
from modules.business.deliveryline.store import DeliverylineUnavailable


class _Manager:
    def __init__(self) -> None:
        self.sessions: set[str] = set()
    def create_session(self, workspace_id: str, *, session_kind: str = "user"):
        assert workspace_id == "chub"
        assert session_kind == "internal"
        session_id = f"session-{len(self.sessions) + 1}"; self.sessions.add(session_id)
        return SimpleNamespace(id=session_id)
    def rename_session(self, session_id: str, _title: str): assert session_id in self.sessions
    def get_session(self, session_id: str):
        assert session_id in self.sessions
        return SimpleNamespace(id=session_id)
    def discard_unstarted_session(self, session_id: str): self.sessions.discard(session_id)


class _QuickInteractions:
    def __init__(self) -> None:
        self.tasks: dict[str, SimpleNamespace] = {}
        self.prompts: list[str] = []
    def session_creation_guard(self): return nullcontext()
    def session_operation_guard(self, _session_id: str): return nullcontext()
    def submit(self, _session_id: str, prompt: str, **_kwargs):
        task_id = f"task-{len(self.tasks) + 1}"; self.prompts.append(prompt)
        task = SimpleNamespace(id=task_id, status="requested", result=None, error=None); self.tasks[task_id] = task
        return task
    def get(self, task_id: str): return self.tasks[task_id]


@pytest.mark.anyio
async def test_ai_clarification_requires_explicit_goal_confirmation(settings, monkeypatch) -> None:
    app = create_app(settings)
    manager, quick = _Manager(), _QuickInteractions()
    for name in ("create_session", "rename_session", "get_session", "discard_unstarted_session"):
        monkeypatch.setattr(app.state.ai_session_manager, name, getattr(manager, name))
    app.state.quick_interactions = quick
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        await client.post("/api/plugins/deliveryline/imports", json={"artifact_id": "development:deliveryline"})
        await client.put("/api/plugins/deliveryline/enabled", json={"artifact_id": "development:deliveryline", "enabled": True})
        created = await client.post("/api/deliveryline/lines", json={"source": "http://127.0.0.1:8080/project-docs/deliveryline-platform"})
        line_id = created.json()["data"]["id"]
        started = await client.post(f"/api/deliveryline/lines/{line_id}/ai-clarification")
        assert started.status_code == 200
        quick.tasks["task-1"].status = "succeeded"
        quick.tasks["task-1"].result = json.dumps({"source_role": "持续演进需求", "known_facts": ["资料是项目内链接。"], "title": "Deliveryline 交付线管理", "overall_goal": "先确认整体目标，再规划交付项。", "scope_boundary": "本轮不创建交付项。", "open_questions": ["拆分策略待规划。"], "assumptions": ["后续会有交付项层级。"]}, ensure_ascii=False)
        overview = await client.get("/api/deliveryline")
        candidate = overview.json()["data"]["lines"][0]
        assert candidate["status"] == "待澄清"
        assert candidate["goal_confirmed"] is False
        assert candidate["collaboration"]["status"] == "suggested"
        assert candidate["collaboration"]["suggestion"]["title"] == "Deliveryline 交付线管理"
        confirmed = await client.post(f"/api/deliveryline/lines/{line_id}/confirm-goal", json={"title": "Deliveryline 交付线管理", "source_role": "持续演进需求", "overall_goal": "先确认整体目标，再规划交付项。", "confirmed_facts": ["资料是项目内链接。"], "scope_boundary": "本轮不创建交付项。", "open_questions": ["拆分策略待规划。"]})
    assert confirmed.status_code == 200
    assert confirmed.json()["data"]["status"] == "规划中"
    assert confirmed.json()["data"]["goal_versions"][0]["version"] == 1
    assert "创建交付项" in quick.prompts[0]


@pytest.mark.anyio
async def test_confirmed_line_cannot_restart_overall_clarification(settings, monkeypatch) -> None:
    app = create_app(settings)
    manager, quick = _Manager(), _QuickInteractions()
    for name in ("create_session", "rename_session", "get_session", "discard_unstarted_session"):
        monkeypatch.setattr(app.state.ai_session_manager, name, getattr(manager, name))
    app.state.quick_interactions = quick
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        await client.post("/api/plugins/deliveryline/imports", json={"artifact_id": "development:deliveryline"})
        await client.put("/api/plugins/deliveryline/enabled", json={"artifact_id": "development:deliveryline", "enabled": True})
        created = await client.post("/api/deliveryline/lines", json={"source": "资料"})
        line_id = created.json()["data"]["id"]
        await client.post(f"/api/deliveryline/lines/{line_id}/ai-clarification")
        quick.tasks["task-1"].status = "succeeded"
        quick.tasks["task-1"].result = json.dumps({"source_role": "混合资料", "known_facts": [], "title": "交付线", "overall_goal": "目标", "scope_boundary": "", "open_questions": [], "assumptions": []}, ensure_ascii=False)
        await client.get("/api/deliveryline")
        confirmed = await client.post(f"/api/deliveryline/lines/{line_id}/confirm-goal", json={"title": "交付线", "source_role": "混合资料", "overall_goal": "目标", "confirmed_facts": [], "scope_boundary": "", "open_questions": []})
        restarted = await client.post(f"/api/deliveryline/lines/{line_id}/ai-clarification")

    assert confirmed.status_code == 200
    assert restarted.status_code == 409
    assert restarted.json()["error"]["code"] == "deliveryline_transition_not_allowed"


@pytest.mark.anyio
async def test_delete_keeps_final_result_when_local_cleanup_fails(settings, monkeypatch) -> None:
    app = create_app(settings)
    monkeypatch.setattr(app.state.deliveryline_collaboration, "remove", lambda _line_id: (_ for _ in ()).throw(DeliverylineUnavailable("local state unavailable")))
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        await client.post("/api/plugins/deliveryline/imports", json={"artifact_id": "development:deliveryline"})
        await client.put("/api/plugins/deliveryline/enabled", json={"artifact_id": "development:deliveryline", "enabled": True})
        created = await client.post("/api/deliveryline/lines", json={"source": "删除时本机协作状态不可写"})
        deleted = await client.delete(f"/api/deliveryline/lines/{created.json()['data']['id']}")
    assert deleted.status_code == 200


@pytest.mark.anyio
async def test_delete_removes_associated_collaboration_session_before_line(settings, monkeypatch) -> None:
    app = create_app(settings)
    manager, quick = _Manager(), _QuickInteractions()
    for name in ("create_session", "rename_session", "get_session", "discard_unstarted_session"):
        monkeypatch.setattr(app.state.ai_session_manager, name, getattr(manager, name))
    app.state.quick_interactions = quick
    deleted_session_ids: list[str] = []
    monkeypatch.setattr(
        "modules.business.deliveryline.collaboration.delete_session",
        lambda session_id, **_kwargs: deleted_session_ids.append(session_id),
    )
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        await client.post("/api/plugins/deliveryline/imports", json={"artifact_id": "development:deliveryline"})
        await client.put("/api/plugins/deliveryline/enabled", json={"artifact_id": "development:deliveryline", "enabled": True})
        created = await client.post("/api/deliveryline/lines", json={"source": "删除关联会话"})
        line_id = created.json()["data"]["id"]
        await client.post(f"/api/deliveryline/lines/{line_id}/ai-clarification")
        deleted = await client.delete(f"/api/deliveryline/lines/{line_id}")

    assert deleted.status_code == 200
    assert deleted_session_ids == ["session-1"]


@pytest.mark.anyio
async def test_delete_preserves_line_when_associated_session_cannot_be_deleted(settings, monkeypatch) -> None:
    app = create_app(settings)
    manager, quick = _Manager(), _QuickInteractions()
    for name in ("create_session", "rename_session", "get_session", "discard_unstarted_session"):
        monkeypatch.setattr(app.state.ai_session_manager, name, getattr(manager, name))
    app.state.quick_interactions = quick
    monkeypatch.setattr(
        "modules.business.deliveryline.collaboration.delete_session",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(ApiError(409, "session_busy", "关联 Session 仍在执行。")),
    )
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        await client.post("/api/plugins/deliveryline/imports", json={"artifact_id": "development:deliveryline"})
        await client.put("/api/plugins/deliveryline/enabled", json={"artifact_id": "development:deliveryline", "enabled": True})
        created = await client.post("/api/deliveryline/lines", json={"source": "删除失败时保留档案"})
        line_id = created.json()["data"]["id"]
        await client.post(f"/api/deliveryline/lines/{line_id}/ai-clarification")
        deleted = await client.delete(f"/api/deliveryline/lines/{line_id}")
        overview = await client.get("/api/deliveryline")

    assert deleted.status_code == 409
    assert deleted.json()["error"]["code"] == "session_busy"
    assert line_id in {item["id"] for item in overview.json()["data"]["lines"]}


@pytest.mark.anyio
async def test_collaboration_session_visibility_setting_was_removed(settings) -> None:
    app = create_app(settings)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/api/deliveryline/settings")
    assert response.status_code == 404
