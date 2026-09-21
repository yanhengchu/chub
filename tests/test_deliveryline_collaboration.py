from __future__ import annotations

import json
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace

import pytest

from modules.business.deliveryline.collaboration import DeliverylineCollaboration
from modules.business.deliveryline.store import DeliverylineStore


def test_clarification_rejects_presentation_prefix_in_title() -> None:
    payload = {"source_role": "混合资料", "known_facts": [], "title": "待确认：Deliveryline", "overall_goal": "目标", "scope_boundary": "", "open_questions": [], "assumptions": []}
    with pytest.raises(ValueError, match="标题包含展示或待确认前缀"):
        DeliverylineCollaboration._parse_suggestion(json.dumps(payload, ensure_ascii=False))


def test_clarification_prompt_forbids_items_and_keeps_source_read_only(tmp_path: Path) -> None:
    store = DeliverylineStore(tmp_path / "requirements")
    line = store.create("https://example.test/design")
    prompt = DeliverylineCollaboration._prompt(line, None, "请先判断资料性质", [])
    assert "原始资料只读" in prompt
    assert "创建交付项" in prompt
    assert "路线图" in prompt


def test_new_local_state_does_not_read_legacy_field_collaboration_file(tmp_path: Path) -> None:
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    (state_dir / "ai-collaboration.json").write_text('{"version":2,"requirements":[]}', encoding="utf-8")
    collaboration = DeliverylineCollaboration(state_dir)
    assert collaboration.show_sessions() is False
    assert not (state_dir / "line-ai-clarification.json").exists()


def test_submission_exception_recovers_task_by_operation_id(tmp_path: Path) -> None:
    class Manager:
        def create_session(self, _workspace_id: str): return SimpleNamespace(id="session-1")
        def rename_session(self, _session_id: str, _title: str): pass
        def get_session(self, _session_id: str): return SimpleNamespace(id="session-1")
        def discard_unstarted_session(self, _session_id: str): pass

    class QuickInteractions:
        def __init__(self) -> None:
            self.task = None
            self.operation_id = None
        def session_creation_guard(self): return nullcontext()
        def session_operation_guard(self, _session_id: str): return nullcontext()
        def submit(self, _session_id: str, _prompt: str, *, operation_id: str, **_kwargs):
            self.operation_id = operation_id
            self.task = SimpleNamespace(id="task-1", status="requested", result=None, error=None)
            raise RuntimeError("response lost after acceptance")
        def find_for_operation(self, operation_id: str): return self.task if operation_id == self.operation_id else None
        def get(self, _task_id: str): return self.task

    store = DeliverylineStore(tmp_path / "requirements")
    line = store.create("待澄清交付线")
    collaboration = DeliverylineCollaboration(tmp_path / "state")
    quick = QuickInteractions()

    started = collaboration.start(line, Manager(), quick, source_ip="127.0.0.1")

    assert started["status"] == "requested"
    assert collaboration.status_for(line, quick)["status"] == "running"
