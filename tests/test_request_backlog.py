from __future__ import annotations

import json
import stat

import pytest

from app.services.request_backlog import (
    RequestBacklogBusy,
    RequestBacklogFull,
    RequestBacklogNotFound,
    RequestBacklogStore,
    RequestBacklogUnavailable,
)


def test_request_backlog_uses_lowest_free_slot_and_archives_snapshot(tmp_path) -> None:
    store = RequestBacklogStore(tmp_path / "state" / "requests.json")

    first = store.save(title="第一个需求", content="目标：完成第一项。")
    second = store.save(title="第二个需求", content="目标：完成第二项。")
    archived = store.archive(first.slot)
    replacement = store.save(title="替补需求", content="目标：复用空槽位。")

    assert (first.slot, second.slot, replacement.slot) == (1, 2, 1)
    assert archived.title == "第一个需求"
    assert [item.title for item in store.list_active()] == ["替补需求", "第二个需求"]
    assert stat.S_IMODE(store.path.stat().st_mode) == 0o600
    assert stat.S_IMODE(store.path.parent.stat().st_mode) == 0o700


def test_request_backlog_enforces_nine_active_requests(tmp_path) -> None:
    store = RequestBacklogStore(tmp_path / "requests.json")
    for index in range(9):
        store.save(title=f"需求 {index + 1}", content="验收：完成。")

    with pytest.raises(RequestBacklogFull):
        store.save(title="第十个需求", content="不能保存。")


def test_request_backlog_run_is_versioned_and_blocks_archive(tmp_path) -> None:
    store = RequestBacklogStore(tmp_path / "requests.json")
    item = store.save(title="运行需求", content="验收：成功执行。")
    claimed = store.claim_run(item.slot, "message-1")

    with pytest.raises(RequestBacklogBusy):
        store.claim_run(item.slot, "message-2")
    with pytest.raises(RequestBacklogBusy):
        store.archive(item.slot)

    assert store.finish_run(
        item.slot,
        item.generation,
        claimed.active_run_id or "",
        "task-1",
        succeeded=True,
    )
    assert store.get(item.slot).status == "succeeded"
    assert not store.finish_run(
        item.slot,
        item.generation,
        claimed.active_run_id or "",
        "task-1",
        succeeded=False,
    )


def test_request_update_replaces_generation_and_resets_status(tmp_path) -> None:
    store = RequestBacklogStore(tmp_path / "requests.json")
    original = store.save(title="旧标题", content="旧内容")
    updated = store.update(original.slot, title="新标题", content="新内容")

    assert updated.slot == original.slot
    assert updated.generation != original.generation
    assert updated.status == "ready"
    assert updated.title == "新标题"
    assert updated.content == "新内容"


def test_system_upgrade_reset_is_idempotent_for_request_generation(tmp_path) -> None:
    store = RequestBacklogStore(tmp_path / "requests.json")
    original = store.save(title="升级后保留", content="保留标题和正文")
    claimed = store.claim_run(original.slot, "message-1")
    assert store.finish_run(
        original.slot,
        original.generation,
        claimed.active_run_id or "",
        "task-1",
        succeeded=True,
    )

    store.reset_runs_for_system_upgrade("a" * 32)
    first = store.get(original.slot)
    store.reset_runs_for_system_upgrade("a" * 32)
    second = store.get(original.slot)

    assert first.generation == second.generation
    assert second.status == "ready"
    assert second.title == original.title
    assert second.content == original.content
    assert second.last_task_id is None


def test_request_backlog_rejects_invalid_or_oversized_state(tmp_path) -> None:
    path = tmp_path / "requests.json"
    path.write_text("not json", encoding="utf-8")
    store = RequestBacklogStore(path)

    with pytest.raises(RequestBacklogUnavailable):
        store.list_active()

    path.write_text(json.dumps({"version": 1, "active": [], "archived": []}), encoding="utf-8")
    with pytest.raises(ValueError):
        store.save(title="标题", content="x" * 2001)
    with pytest.raises(RequestBacklogNotFound):
        store.get(1)
