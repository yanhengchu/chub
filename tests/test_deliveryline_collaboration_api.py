from __future__ import annotations

import json
from contextlib import nullcontext
from types import SimpleNamespace

import httpx
import pytest

from app.application import create_app
from app.deliveryline.store import DeliverylineUnavailable


class _Manager:
    def __init__(self) -> None:
        self.sessions: set[str] = set()

    def create_session(self, workspace_id: str):
        assert workspace_id == "chub"
        session_id = f"session-{len(self.sessions) + 1}"
        self.sessions.add(session_id)
        return SimpleNamespace(id=session_id)

    def rename_session(self, session_id: str, _title: str):
        assert session_id in self.sessions

    def get_session(self, session_id: str):
        assert session_id in self.sessions
        return SimpleNamespace(id=session_id)

    def discard_unstarted_session(self, session_id: str):
        self.sessions.discard(session_id)


class _QuickInteractions:
    def __init__(self) -> None:
        self.tasks: dict[str, SimpleNamespace] = {}
        self.prompts: list[str] = []

    def session_creation_guard(self):
        return nullcontext()

    def session_operation_guard(self, _session_id: str):
        return nullcontext()

    def submit(self, _session_id: str, prompt: str, **_kwargs):
        task_id = f"task-{len(self.tasks) + 1}"
        self.prompts.append(prompt)
        task = SimpleNamespace(id=task_id, status="requested", result=None, error=None)
        self.tasks[task_id] = task
        return task

    def get(self, task_id: str):
        return self.tasks[task_id]


@pytest.mark.anyio
async def test_deliveryline_ai_collaboration_api_applies_only_confirmed_field(settings, monkeypatch) -> None:
    app = create_app(settings)
    manager = _Manager()
    quick = _QuickInteractions()
    for name in ("create_session", "rename_session", "get_session", "discard_unstarted_session"):
        monkeypatch.setattr(app.state.ai_session_manager, name, getattr(manager, name))
    app.state.quick_interactions = quick
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        await client.post("/api/plugins/deliveryline/imports", json={"artifact_id": "development:deliveryline"})
        await client.put("/api/plugins/deliveryline/enabled", json={"artifact_id": "development:deliveryline", "enabled": True})
        created = await client.post("/api/deliveryline/requirements", json={"description": "只有一个链接的需求"})
        requirement_id = created.json()["data"]["id"]
        started = await client.post(f"/api/deliveryline/requirements/{requirement_id}/ai-collaboration")
        assert started.status_code == 200
        assert started.json()["data"]["collaboration"]["status"] == "requested"
        quick.tasks["task-1"].status = "succeeded"
        quick.tasks["task-1"].result = json.dumps({
            "fields": {
                "title": "链接需求", "background": "背景", "delivery_goal": "目标", "scope": "范围",
                "out_of_scope": "不做什么", "constraints": "约束", "acceptance_criteria": "验收", "risks_and_open_items": "暂无",
            },
            "open_questions": ["请确认范围。"],
        }, ensure_ascii=False)
        overview = await client.get("/api/deliveryline")
        suggested = overview.json()["data"]["requirements"][0]
        assert overview.json()["data"]["workflow_stages"][0]["substages"][1]["objective"] == "保留原始需求，补全为可提交评审的正式档案。"
        assert suggested["title"] == ""
        assert suggested["collaboration"]["status"] == "suggested"
        assert suggested["collaboration"]["suggestion"]["fields"]["title"]["value"] == "链接需求"
        blocked = await client.post(f"/api/deliveryline/requirements/{requirement_id}/submit-review")
        accepted = None
        for field in ("title", "delivery_goal", "scope", "out_of_scope", "constraints", "acceptance_criteria", "risks_and_open_items"):
            accepted = await client.post(f"/api/deliveryline/requirements/{requirement_id}/ai-collaboration/fields/{field}/accept")
            assert accepted.status_code == 200
        continued = await client.post(
            f"/api/deliveryline/requirements/{requirement_id}/ai-collaboration",
            json={"comments": {"background": "请补充实际使用对象。"}},
        )
        setting = await client.put("/api/deliveryline/settings", json={"show_sessions": True})

    assert accepted is not None
    assert accepted.json()["data"]["title"] == "链接需求"
    assert continued.status_code == 200
    assert continued.json()["data"]["collaboration"]["status"] == "requested"
    assert '"fields_to_generate": ["background"]' in quick.prompts[-1]
    assert blocked.status_code == 409
    assert accepted.json()["data"]["current_stage"] == "需求提出"
    assert setting.json()["data"]["show_sessions"] is True


@pytest.mark.anyio
async def test_deliveryline_delete_keeps_its_final_result_when_local_collaboration_cleanup_fails(settings, monkeypatch) -> None:
    app = create_app(settings)
    monkeypatch.setattr(app.state.deliveryline_collaboration, "remove", lambda _requirement_id: (_ for _ in ()).throw(DeliverylineUnavailable("local state unavailable")))
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        await client.post("/api/plugins/deliveryline/imports", json={"artifact_id": "development:deliveryline"})
        await client.put("/api/plugins/deliveryline/enabled", json={"artifact_id": "development:deliveryline", "enabled": True})
        created = await client.post("/api/deliveryline/requirements", json={"description": "删除时本机协作状态不可写"})
        requirement_id = created.json()["data"]["id"]

        deleted = await client.delete(f"/api/deliveryline/requirements/{requirement_id}")

    assert deleted.status_code == 200
    assert deleted.json()["data"]["id"] == requirement_id


@pytest.mark.anyio
async def test_deliveryline_collaboration_session_visibility_setting_is_available_after_import(settings) -> None:
    app = create_app(settings)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        await client.post("/api/plugins/deliveryline/imports", json={"artifact_id": "development:deliveryline"})

        current = await client.get("/api/deliveryline/settings")
        updated = await client.put("/api/deliveryline/settings", json={"show_sessions": True})

    assert current.status_code == 200
    assert current.json()["data"]["show_sessions"] is False
    assert updated.status_code == 200
    assert updated.json()["data"]["show_sessions"] is True
