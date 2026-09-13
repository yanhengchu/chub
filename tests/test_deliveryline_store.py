from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.deliveryline.store import DeliverylineNotFound, DeliverylineReviewNotReady, DeliverylineStore


def test_requirement_can_start_from_one_sentence_and_then_enter_review(tmp_path: Path) -> None:
    store = DeliverylineStore(tmp_path / "requirements")

    record = store.create("希望统一管理每项需求的交付进度。")

    assert record.id.startswith("DL-")
    assert record.title == ""
    assert record.original_request_content == "希望统一管理每项需求的交付进度。"
    assert record.background == ""
    assert record.workflow.current_stage == "需求提出"
    assert record.workflow.delivery_status == "待我处理"
    assert record.workflow.next_action == "需求已入库，等待后续处理"
    assert record.is_initialized is True
    assert len(list((tmp_path / "requirements").glob("DL-*.json"))) == 1
    assert not (tmp_path / "requirements" / ".requirements.lock").exists()
    assert (tmp_path / ".state" / "requirements.lock").exists()
    with pytest.raises(DeliverylineReviewNotReady) as error:
        store.submit_for_review(record.id)
    assert error.value.fields == [
        "标题",
        "背景与问题",
        "交付目标",
        "本次范围",
        "不做什么",
        "约束与依赖",
        "验收标准",
        "风险与待确认事项",
    ]

    updated = store.update(
        record.id,
        {
            "title": "需求交付台账",
            "background": "需求交付缺少统一入口和状态。",
            "delivery_goal": "集中查看并推进需求。",
            "scope": "需求提出与评审准备。",
            "out_of_scope": "不接入开发任务。",
            "constraints": "暂无",
            "acceptance_criteria": "可创建、补全并提交评审。",
            "risks_and_open_items": "暂无",
        },
    )
    submitted = store.submit_for_review(record.id)

    assert updated.workflow.next_action == "提交需求评审"
    assert updated.is_initialized is False
    assert updated.original_request_content == "希望统一管理每项需求的交付进度。"
    assert submitted.workflow.current_stage == "需求评审"
    assert submitted.workflow.delivery_status == "进行中"
    assert submitted.workflow.next_action == "开展需求评审"
    persisted = json.loads((tmp_path / "requirements" / f"{record.id}.json").read_text())
    assert persisted["workflow"]["current_stage"] == "需求评审"
    assert persisted["original_request_content"] == "希望统一管理每项需求的交付进度。"
    assert [item["action"] for item in persisted["activity"]] == [
        "created",
        "updated",
        "submitted_for_review",
    ]


def test_historical_requirement_without_original_request_content_remains_readable(tmp_path: Path) -> None:
    store = DeliverylineStore(tmp_path / "requirements")
    record = store.create("历史需求。")
    path = tmp_path / "requirements" / f"{record.id}.json"
    persisted = json.loads(path.read_text())
    persisted.pop("original_request_content")
    path.write_text(json.dumps(persisted, ensure_ascii=False))

    historical = store.get(record.id)

    assert historical.original_request_content is None


def test_archived_requirement_is_preserved_but_not_in_default_queue(tmp_path: Path) -> None:
    store = DeliverylineStore(tmp_path / "requirements")
    record = store.create("归档后仍需要保留需求档案。")

    archived = store.archive(record.id)

    assert archived.workflow.delivery_status == "已归档"
    assert store.list() == []
    assert [item.id for item in store.list(include_archived=True)] == [record.id]
    assert store.get(record.id).activity[-1].action == "archived"


def test_requirement_can_be_permanently_deleted(tmp_path: Path) -> None:
    store = DeliverylineStore(tmp_path / "requirements")
    record = store.create("需要删除的需求。")

    store.delete(record.id)

    assert not (tmp_path / "requirements" / f"{record.id}.json").exists()
    with pytest.raises(DeliverylineNotFound):
        store.get(record.id)
