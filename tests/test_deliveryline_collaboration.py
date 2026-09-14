from __future__ import annotations

from contextlib import nullcontext
import json
from types import SimpleNamespace

import pytest

from app.deliveryline.collaboration import CollaborationRound, CollaborationState, DeliverylineCollaboration, RequirementCollaboration, utc_now
from app.deliveryline.store import DeliverylineStore


class FakeManager:
    def __init__(self) -> None:
        self.created = 0
        self.sessions: set[str] = set()
        self.deleted: list[str] = []

    def create_session(self, workspace_id: str):
        assert workspace_id == "chub"
        self.created += 1
        session_id = f"session-{self.created}"
        self.sessions.add(session_id)
        return SimpleNamespace(id=session_id)

    def rename_session(self, session_id: str, title: str):
        assert session_id in self.sessions
        assert title.startswith("Deliveryline · DL-")

    def get_session(self, session_id: str):
        if session_id not in self.sessions:
            from app.core.response import ApiError
            raise ApiError(404, "missing", "missing")
        return SimpleNamespace(id=session_id)

    def discard_unstarted_session(self, session_id: str) -> None:
        self.sessions.discard(session_id)

    def ensure_delete_allowed(self, session_id: str, *, reconcile: bool = True):
        assert session_id in self.sessions

    def delete_native_session(self, session_id: str):
        assert session_id in self.sessions
        self.deleted.append(session_id)

    def finalize_delete_session(self, session_id: str) -> None:
        self.sessions.discard(session_id)


class FakeQuickInteractions:
    def __init__(self) -> None:
        self.tasks: dict[str, SimpleNamespace] = {}
        self.operation_tasks: dict[str, SimpleNamespace] = {}
        self.prompts: list[str] = []
        self.capability_ids: list[tuple[str, ...]] = []

    def session_creation_guard(self):
        return nullcontext()

    def session_operation_guard(self, session_id: str):
        return nullcontext()

    def destructive_operation_guard(self, session_id: str):
        return nullcontext()

    def cancel_codex_session(self, session_id: str) -> bool:
        return True

    def remove_session_tasks(self, session_id: str) -> None:
        return None

    def submit(self, session_id: str, prompt: str, **kwargs):
        task_id = f"task-{len(self.tasks) + 1}"
        self.prompts.append(prompt)
        self.capability_ids.append(tuple(kwargs.get("capability_ids", ())))
        task = SimpleNamespace(id=task_id, status="requested", result=None, error=None)
        self.tasks[task_id] = task
        self.operation_tasks[kwargs["operation_id"]] = task
        return task

    def get(self, task_id: str):
        if task_id not in self.tasks:
            from app.core.response import ApiError
            raise ApiError(404, "missing", "missing")
        return self.tasks[task_id]

    def find_for_operation(self, operation_id: str):
        return self.operation_tasks.get(operation_id)


def _ready_fields() -> dict[str, str]:
    return {
        "title": "需求标题",
        "background": "背景",
        "delivery_goal": "目标",
        "scope": "范围",
        "out_of_scope": "不做什么",
        "constraints": "约束",
        "acceptance_criteria": "验收标准",
        "risks_and_open_items": "待确认",
    }


@pytest.mark.parametrize("title", [
    "AI 建议：待确认：Deliveryline 平台相关需求档案",
    "建议：Deliveryline 平台相关需求档案",
    "展示：Deliveryline 平台相关需求档案",
    "显示：Deliveryline 平台相关需求档案",
])
def test_collaboration_rejects_title_suggestion_with_display_or_pending_prefixes(title: str) -> None:
    with pytest.raises(ValueError, match="标题包含展示或待确认前缀"):
        DeliverylineCollaboration._parse_suggestion(json.dumps({
            "fields": {**_ready_fields(), "title": title},
            "open_questions": [],
        }, ensure_ascii=False))


def test_collaboration_prompt_keeps_title_uncertainty_out_of_the_title_value(tmp_path) -> None:
    requirement = DeliverylineStore(tmp_path / "requirements").create("请整理一个尚未命名的需求")

    prompt = DeliverylineCollaboration._prompt(requirement, None, tuple(_ready_fields()))

    assert "所有不确定、待确认或缺失信息只能写入 open_questions" in prompt
    assert "即使标题信息不完整，也要给出中性的可用工作标题" in prompt
    assert "信息不足时在字段中明确待确认内容" not in prompt
    assert "无效示例：\"AI 建议：Deliveryline 协作标题规范\"" in prompt


def test_collaboration_uses_registered_project_document_as_a_bounded_source(tmp_path, monkeypatch) -> None:
    from app.services.design_documents import DesignDocumentSource

    monkeypatch.setattr(
        "app.deliveryline.collaboration.get_design_document_source",
        lambda document_id, **_kwargs: DesignDocumentSource(
            id=document_id,
            title="Deliveryline 平台设计",
            content="页面正文中的交付目标。",
            truncated=False,
        ),
    )
    store = DeliverylineStore(tmp_path / "requirements")
    requirement = store.create("http://127.0.0.1:8080/project-docs/deliveryline-platform")
    quick = FakeQuickInteractions()

    started = DeliverylineCollaboration(tmp_path / "state").start(
        requirement,
        FakeManager(),
        quick,
        source_ip="127.0.0.1",
    )

    assert started["sources"] == [{
        "kind": "project_document",
        "label": "Deliveryline 平台设计",
        "url": "http://127.0.0.1:8080/project-docs/deliveryline-platform",
    }]
    assert quick.capability_ids == [()]
    assert '"document_id": "deliveryline-platform"' in quick.prompts[0]
    assert "页面正文中的交付目标。" in quick.prompts[0]


def test_collaboration_grants_page_read_only_for_linked_public_pages(tmp_path) -> None:
    store = DeliverylineStore(tmp_path / "requirements")
    requirement = store.create("https://example.com/requirements")
    quick = FakeQuickInteractions()

    started = DeliverylineCollaboration(tmp_path / "state").start(
        requirement,
        FakeManager(),
        quick,
        source_ip="127.0.0.1",
    )

    assert started["sources"] == [{
        "kind": "public_page",
        "label": "公共网页资料",
        "url": "https://example.com/requirements",
    }]
    assert quick.capability_ids == [("chub.debug_chrome.page.read",)]
    assert "必须先用已授予的 chub capability page-read 命令" in quick.prompts[0]


def test_collaboration_marks_unavailable_project_document_for_ai_follow_up(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr("app.deliveryline.collaboration.get_design_document_source", lambda *_args, **_kwargs: None)
    store = DeliverylineStore(tmp_path / "requirements")
    requirement = store.create("https://chub.example.test/project-docs/missing")
    quick = FakeQuickInteractions()

    started = DeliverylineCollaboration(tmp_path / "state").start(
        requirement,
        FakeManager(),
        quick,
        source_ip="127.0.0.1",
    )

    assert started["sources"][0]["kind"] == "project_document_unavailable"
    assert quick.capability_ids == [()]
    assert "不要臆测正文" in quick.prompts[0]


def test_collaboration_hides_a_persisted_invalid_suggestion_on_read(tmp_path) -> None:
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    now = utc_now()
    invalid = CollaborationRound(
        id="round-1",
        stage="需求提出",
        status="suggested",
        suggestion=DeliverylineCollaboration._parse_suggestion(json.dumps({
            "fields": _ready_fields(),
            "open_questions": ["请确认范围。"],
        }, ensure_ascii=False)),
        created_at=now,
        updated_at=now,
    )
    invalid.suggestion.fields["title"].value = "待确认：需求标题"
    state = CollaborationState(requirements=[RequirementCollaboration(
        requirement_id="DL-test",
        session_id="session-1",
        rounds=[invalid],
    )])
    (state_dir / "ai-collaboration.json").write_text(state.model_dump_json(), encoding="utf-8")

    latest = DeliverylineCollaboration(state_dir)._latest("DL-test")

    assert latest.status == "failed"
    assert latest.suggestion is None


def test_collaboration_reuses_one_session_and_requires_field_confirmation(tmp_path) -> None:
    store = DeliverylineStore(tmp_path / "requirements")
    requirement = store.create("https://example.test/requirement")
    collaboration = DeliverylineCollaboration(tmp_path / "state")
    manager = FakeManager()
    quick = FakeQuickInteractions()

    started = collaboration.start(requirement, manager, quick, source_ip="127.0.0.1")

    assert started["status"] == "requested"
    assert manager.created == 1
    quick.tasks["task-1"].status = "succeeded"
    quick.tasks["task-1"].result = json.dumps({"fields": _ready_fields(), "open_questions": ["请确认范围。"]}, ensure_ascii=False)

    suggested = collaboration.status_for(requirement, quick)

    assert suggested is not None
    assert suggested["status"] == "suggested"
    assert suggested["suggestion"]["fields"]["title"]["value"] == "需求标题"
    assert store.get(requirement.id).title == ""

    title = collaboration.prepare_field_accept(requirement, "title")
    collaboration.reconcile_field_accept(store.update(requirement.id, {"title": title}), "title")

    for field, value in _ready_fields().items():
        if field in {"title", "background"}:
            continue
        accepted = collaboration.prepare_field_accept(requirement, field)
        collaboration.reconcile_field_accept(store.update(requirement.id, {field: accepted}), field)

    continued = collaboration.start(requirement, manager, quick, comments={"background": "范围仅包含网页端。"}, source_ip="127.0.0.1")

    assert continued["status"] == "requested"
    assert manager.created == 1
    assert "范围仅包含网页端" in quick.prompts[-1]
    assert '"fields_to_generate": ["background"]' in quick.prompts[-1]
    quick.tasks["task-2"].status = "succeeded"
    quick.tasks["task-2"].result = json.dumps({"fields": {"background": "仅包含网页端使用场景。"}, "open_questions": []}, ensure_ascii=False)
    revised = collaboration.status_for(requirement, quick)
    assert revised is not None
    assert set(revised["suggestion"]["fields"]) == {"background"}


def test_collaboration_can_continue_without_commenting_every_unaccepted_field(tmp_path) -> None:
    store = DeliverylineStore(tmp_path / "requirements")
    requirement = store.create("https://example.test/requirement")
    collaboration = DeliverylineCollaboration(tmp_path / "state")
    manager = FakeManager()
    quick = FakeQuickInteractions()
    collaboration.start(requirement, manager, quick, source_ip="127.0.0.1")
    quick.tasks["task-1"].status = "succeeded"
    quick.tasks["task-1"].result = json.dumps(
        {"fields": _ready_fields(), "open_questions": []},
        ensure_ascii=False,
    )
    collaboration.status_for(requirement, quick)

    title = collaboration.prepare_field_accept(requirement, "title")
    updated = store.update(requirement.id, {"title": title})
    collaboration.reconcile_field_accept(updated, "title")
    continued = collaboration.start(updated, manager, quick, source_ip="127.0.0.1")

    assert continued["status"] == "requested"
    assert manager.created == 1
    assert '"fields_to_generate": ["background", "delivery_goal", "scope", "out_of_scope", "constraints", "acceptance_criteria", "risks_and_open_items"]' in quick.prompts[-1]


def test_collaboration_visibility_is_local_and_defaults_to_hidden(tmp_path) -> None:
    collaboration = DeliverylineCollaboration(tmp_path / "state")

    assert collaboration.show_sessions() is False
    assert collaboration.set_show_sessions(True) is True
    assert DeliverylineCollaboration(tmp_path / "state").show_sessions() is True


def test_collaboration_v1_state_replaces_legacy_session_and_keeps_visibility_setting(tmp_path) -> None:
    store = DeliverylineStore(tmp_path / "requirements")
    requirement = store.create("https://example.test/requirement")
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    (state_dir / "ai-collaboration.json").write_text(json.dumps({
        "version": 1,
        "show_sessions": True,
        "requirements": [{"requirement_id": requirement.id, "session_id": "legacy-session", "rounds": []}],
    }), encoding="utf-8")

    collaboration = DeliverylineCollaboration(state_dir)
    manager = FakeManager()
    manager.sessions.add("legacy-session")

    assert collaboration.show_sessions() is True
    assert collaboration.status_for(requirement, FakeQuickInteractions()) is None
    started = collaboration.start(requirement, manager, FakeQuickInteractions(), source_ip="127.0.0.1")
    assert manager.deleted == ["legacy-session"]
    assert manager.created == 1
    assert started["session_id"] == "session-1"


def test_collaboration_does_not_replace_session_after_a_temporary_read_failure(tmp_path) -> None:
    from app.core.response import ApiError

    class UnavailableManager(FakeManager):
        def get_session(self, session_id: str):
            raise ApiError(503, "codex_session_unavailable", "temporary failure")

    store = DeliverylineStore(tmp_path / "requirements")
    requirement = store.create("https://example.test/requirement")
    manager = UnavailableManager()
    manager.sessions.add("session-1")
    now = utc_now()
    state = CollaborationState(requirements=[RequirementCollaboration(
        requirement_id=requirement.id,
        session_id="session-1",
        rounds=[CollaborationRound(id="round-1", stage="需求提出", status="failed", created_at=now, updated_at=now)],
    )])
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    (state_dir / "ai-collaboration.json").write_text(state.model_dump_json(), encoding="utf-8")

    try:
        DeliverylineCollaboration(state_dir).start(requirement, manager, FakeQuickInteractions(), source_ip="127.0.0.1")
    except ApiError as exc:
        assert exc.code == "codex_session_unavailable"
    else:
        raise AssertionError("temporary Session read failures must not create a replacement")
    assert manager.created == 0


def test_collaboration_recovers_a_submitting_round_from_its_operation(tmp_path) -> None:
    store = DeliverylineStore(tmp_path / "requirements")
    requirement = store.create("https://example.test/requirement")
    state_dir = tmp_path / "state"
    manager = FakeManager()
    manager.sessions.add("session-1")
    quick = FakeQuickInteractions()
    task = SimpleNamespace(id="task-1", status="requested", result=None, error=None)
    quick.tasks[task.id] = task
    quick.operation_tasks["operation-1"] = task
    now = utc_now()
    state = CollaborationState(requirements=[RequirementCollaboration(
        requirement_id=requirement.id,
        session_id="session-1",
        rounds=[CollaborationRound(id="round-1", stage="需求提出", operation_id="operation-1", status="submitting", created_at=now, updated_at=now)],
    )])
    state_dir.mkdir()
    (state_dir / "ai-collaboration.json").write_text(state.model_dump_json(), encoding="utf-8")

    recovered = DeliverylineCollaboration(state_dir).status_for(requirement, quick)

    assert recovered is not None
    assert recovered["status"] == "running"
    assert recovered["session_id"] == "session-1"


def test_collaboration_marks_missing_task_failed_with_retry(tmp_path) -> None:
    store = DeliverylineStore(tmp_path / "requirements")
    requirement = store.create("https://example.test/requirement")
    collaboration = DeliverylineCollaboration(tmp_path / "state")
    manager = FakeManager()
    quick = FakeQuickInteractions()
    collaboration.start(requirement, manager, quick, source_ip="127.0.0.1")
    quick.tasks.clear()

    status = collaboration.status_for(requirement, quick)

    assert status is not None
    assert status["status"] == "failed"
    assert "再次 AI 协作" in status["error"]


def test_collaboration_blocks_stage_confirmation_while_a_task_is_running(tmp_path) -> None:
    store = DeliverylineStore(tmp_path / "requirements")
    requirement = store.create("https://example.test/requirement")
    collaboration = DeliverylineCollaboration(tmp_path / "state")
    collaboration.start(requirement, FakeManager(), FakeQuickInteractions(), source_ip="127.0.0.1")

    from app.deliveryline.store import DeliverylineTransitionNotAllowed
    try:
        collaboration.ensure_stage_confirmation_ready(requirement)
    except DeliverylineTransitionNotAllowed:
        pass
    else:
        raise AssertionError("running collaboration must block stage confirmation")


def test_collaboration_recovers_a_task_when_submit_reports_an_uncertain_error(tmp_path) -> None:
    class UncertainQuickInteractions(FakeQuickInteractions):
        def submit(self, session_id: str, prompt: str, **kwargs):
            super().submit(session_id, prompt, **kwargs)
            raise RuntimeError("connection closed after submission")

    store = DeliverylineStore(tmp_path / "requirements")
    requirement = store.create("https://example.test/requirement")
    collaboration = DeliverylineCollaboration(tmp_path / "state")

    status = collaboration.start(requirement, FakeManager(), UncertainQuickInteractions(), source_ip="127.0.0.1")

    assert status["status"] == "requested"
