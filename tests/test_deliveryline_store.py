from __future__ import annotations

from pathlib import Path
from subprocess import CompletedProcess

import pytest

from modules.business.deliveryline.store import DeliverylineNotFound, DeliverylineStore, DeliverylineTransitionNotAllowed, DeliverylineUnavailable


def test_source_becomes_pending_line_and_confirmed_goal_creates_a_version(tmp_path: Path) -> None:
    store = DeliverylineStore(tmp_path / "requirements")
    line = store.create("http://127.0.0.1:8080/project-docs/deliveryline-platform")

    assert line.status == "待澄清"
    assert line.goal_confirmed is False
    assert line.title == ""
    assert line.original_request_content.startswith("http://")

    confirmed = store.confirm_goal(line.id, {
        "title": "Deliveryline 交付线管理",
        "source_role": "持续演进需求",
        "overall_goal": "让维护者确认整体目标后再规划交付项。",
        "confirmed_facts": ["原始资料是一个项目资料链接。"],
        "scope_boundary": "本轮只确认整体目标。",
        "open_questions": ["后续交付项拆分规则待规划。"],
    })

    assert confirmed.status == "规划中"
    assert confirmed.goal_confirmed is True
    assert confirmed.goal_versions[0].version == 1
    assert confirmed.goal_versions[0].title == "Deliveryline 交付线管理"
    assert [item.action for item in confirmed.activity] == ["created", "goal_confirmed"]


def test_ended_line_is_preserved_but_not_in_default_queue(tmp_path: Path) -> None:
    store = DeliverylineStore(tmp_path / "requirements")
    line = store.create("需要结束的交付线")
    ended = store.end(line.id)

    assert ended.status == "已结束"
    assert store.list() == []
    assert [item.id for item in store.list(include_ended=True)] == [line.id]


def test_ended_line_cannot_confirm_another_goal(tmp_path: Path) -> None:
    store = DeliverylineStore(tmp_path / "requirements")
    line = store.create("需要结束的交付线")
    store.end(line.id)

    with pytest.raises(DeliverylineTransitionNotAllowed):
        store.confirm_goal(line.id, {"title": "标题", "source_role": "混合资料", "overall_goal": "目标", "confirmed_facts": [], "scope_boundary": "", "open_questions": []})


def test_confirmed_line_cannot_bypass_future_change_assessment(tmp_path: Path) -> None:
    store = DeliverylineStore(tmp_path / "requirements")
    line = store.create("待澄清交付线")
    values = {"title": "标题", "source_role": "混合资料", "overall_goal": "目标", "confirmed_facts": [], "scope_boundary": "", "open_questions": []}
    store.confirm_goal(line.id, values)

    with pytest.raises(DeliverylineTransitionNotAllowed, match="变更评估"):
        store.confirm_goal(line.id, values)


def test_confirm_goal_requires_a_confirmed_source_role(tmp_path: Path) -> None:
    store = DeliverylineStore(tmp_path / "requirements")
    line = store.create("待澄清交付线")

    with pytest.raises(ValueError, match="资料定位无效"):
        store.confirm_goal(line.id, {"title": "标题", "source_role": "未澄清", "overall_goal": "目标", "confirmed_facts": [], "scope_boundary": "", "open_questions": []})


def test_line_can_be_deleted(tmp_path: Path) -> None:
    store = DeliverylineStore(tmp_path / "requirements")
    line = store.create("需要删除的交付线")
    store.delete(line.id)
    with pytest.raises(DeliverylineNotFound):
        store.get(line.id)


def test_conflicted_shared_line_is_not_overwritten(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store = DeliverylineStore(tmp_path / "requirements")
    line = store.create("冲突交付线")
    monkeypatch.setattr(store, "_git_repository_root", lambda _path: tmp_path)
    monkeypatch.setattr("modules.business.deliveryline.store.subprocess.run", lambda args, **_kwargs: CompletedProcess(args, 0, b"100644 conflict\trequirements/record.json\n", b""))
    with pytest.raises(DeliverylineUnavailable, match="未解决的 Git 冲突"):
        store.confirm_goal(line.id, {"title": "标题", "source_role": "混合资料", "overall_goal": "目标", "confirmed_facts": [], "scope_boundary": "", "open_questions": []})
