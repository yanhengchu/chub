from __future__ import annotations

from datetime import timedelta

from app.core.response import ApiError
from app.codex.models import CodexSession, QuickInteractionTask, utc_now
from app.services import openclaw_weixin_chub_mode as chub_mode_module
from app.services.openclaw_weixin_chub_models import WeixinChubModeSessionSlot

from tests.openclaw_weixin_chub_mode_helpers import (
    configured_manager,
    delivery_route,
    inject_default_delivery_route,
)


def _prepare_current_session(manager, codex_manager) -> None:
    session = CodexSession(
        session_mode="quick",
        id="session-1",
        workspace_id="chub",
        workspace_name="Chub",
        cwd="/project",
        title="需求实施",
        permission_mode="full-access",
        status="stopped",
        activity="idle",
    )
    manager._state.session_id = session.id
    manager._state.session_slots = [
        WeixinChubModeSessionSlot(slot=1, session_id=session.id)
    ]
    codex_manager.list_sessions.return_value = [session]
    codex_manager.get_session.return_value = session


def test_request_cat_and_chinese_alias_return_full_saved_request(settings) -> None:
    manager, _codex_manager, _quick_interactions = configured_manager(settings)
    item = manager.request_backlog.save(
        title="微信需求池",
        content="目标：保存小需求。\n\n验收：可以完整查看。",
    )

    result = manager.dispatch(
        message_id="request-cat",
        prompt="查看需求一",
        message_type="text",
        correlation_id=None,
        source_ip="100.64.0.21",
        delivery_route=delivery_route(),
    )

    assert result.message == (
        "Request\n\n"
        f"R{item.slot} · 微信需求池\n\n"
        "Status · Ready\n\n"
        "目标：保存小需求。\n\n验收：可以完整查看。"
    )


def test_chub_overview_lists_requests_below_sessions(settings) -> None:
    manager, _codex_manager, _quick_interactions = configured_manager(settings)
    manager.request_backlog.save(title="第一项需求", content="验收：展示。")
    manager._status_cache["sessions"] = ((), utc_now())

    message = manager._format_chub_overview("route", elapsed_ms=10)

    assert "No sessions\n\nRequests\n\nR1 · 第一项需求\n\nWeekly" in message


def test_chub_overview_does_not_report_unreadable_requests_as_empty(settings) -> None:
    manager, _codex_manager, _quick_interactions = configured_manager(settings)
    manager._status_cache["sessions"] = ((), utc_now())
    manager.request_backlog.path.parent.mkdir(parents=True, exist_ok=True)
    manager.request_backlog.path.write_text("invalid", encoding="utf-8")

    message = manager._format_chub_overview("route", elapsed_ms=10)

    assert "Requests\n\nUnavailable" in message
    assert "No requests" not in message


def test_request_run_submits_to_current_session_and_records_completion(settings) -> None:
    manager, codex_manager, quick_interactions = configured_manager(settings)
    _prepare_current_session(manager, codex_manager)
    item = manager.request_backlog.save(
        title="执行需求",
        content="目标：实现需求。\n\n验收：测试通过。",
    )

    result = manager.dispatch(
        message_id="request-run",
        prompt="run R1",
        message_type="text",
        correlation_id=None,
        source_ip="100.64.0.21",
        delivery_route=delivery_route(),
    )

    assert result.message is not None
    assert result.message.startswith("Submitted\n\nRequest · R1 · 执行需求\n\nSessions")
    kwargs = quick_interactions.submit.call_args.kwargs
    assert kwargs["weixin_request_slot"] == item.slot
    assert kwargs["weixin_request_generation"] == item.generation
    assert kwargs["weixin_request_title"] == item.title
    running = manager.request_backlog.get(item.slot)
    assert running.status == "running"

    task = QuickInteractionTask(
        id="task-1",
        session_id="session-1",
        prompt=item.content,
        summary=item.title,
        weixin_request_slot=item.slot,
        weixin_request_generation=item.generation,
        weixin_request_run_id=running.active_run_id,
        weixin_request_title=item.title,
        status="succeeded",
        created_at=utc_now(),
        updated_at=utc_now(),
    )
    manager.record_request_task_completion(task)

    assert manager.request_backlog.get(item.slot).status == "succeeded"


def test_request_run_busy_session_keeps_request_without_pending_retry(settings) -> None:
    manager, codex_manager, quick_interactions = configured_manager(settings)
    _prepare_current_session(manager, codex_manager)
    item = manager.request_backlog.save(
        title="稍后执行",
        content="验收：忙碌时不提交。",
    )
    quick_interactions.is_running.return_value = True

    result = manager.dispatch(
        message_id="busy-request-run",
        prompt="执行需求一",
        message_type="text",
        correlation_id=None,
        source_ip="100.64.0.21",
        delivery_route=delivery_route(),
    )

    assert result.message is not None
    assert result.message.startswith("Not submitted")
    assert "Request · R1 · 稍后执行" in result.message
    assert manager.request_backlog.get(item.slot).status == "failed"
    assert manager._state.pending_retry is None
    quick_interactions.submit.assert_not_called()


def test_request_run_keeps_running_when_worker_submission_is_uncertain(settings) -> None:
    manager, codex_manager, quick_interactions = configured_manager(settings)
    _prepare_current_session(manager, codex_manager)
    item = manager.request_backlog.save(
        title="等待确认",
        content="验收：按真实任务终态收敛。",
    )

    def uncertain_submission(*_args, **_kwargs):
        running = manager.request_backlog.get(item.slot)
        quick_interactions.find_request_task.return_value = QuickInteractionTask(
            id="uncertain-task",
            session_id="session-1",
            prompt=item.content,
            summary=item.title,
            weixin_request_slot=item.slot,
            weixin_request_generation=item.generation,
            weixin_request_run_id=running.active_run_id,
            weixin_request_title=item.title,
            status="requested",
            created_at=utc_now(),
            updated_at=utc_now(),
        )
        raise ApiError(
            503,
            "quick_worker_submission_uncertain",
            "Quick Worker submission is uncertain.",
        )

    quick_interactions.submit.side_effect = uncertain_submission

    result = manager.dispatch(
        message_id="uncertain-request-run",
        prompt="run R1",
        message_type="text",
        correlation_id=None,
        source_ip="100.64.0.21",
        delivery_route=delivery_route(),
    )

    assert result.message is not None
    assert result.message.startswith("Submission pending confirmation")
    assert "Not submitted" not in result.message
    assert "Request · R1 · 等待确认" in result.message
    running = manager.request_backlog.get(item.slot)
    assert running.status == "running"
    assert running.active_task_id == "uncertain-task"

    completed = quick_interactions.find_request_task.return_value.model_copy(
        update={"status": "succeeded", "updated_at": utc_now()}
    )
    manager.record_request_task_completion(completed)

    assert manager.request_backlog.get(item.slot).status == "succeeded"


def test_duplicate_request_run_does_not_submit_twice(settings) -> None:
    manager, codex_manager, quick_interactions = configured_manager(settings)
    _prepare_current_session(manager, codex_manager)
    manager.request_backlog.save(title="幂等执行", content="验收：只提交一次。")

    first = manager.dispatch(
        message_id="duplicate-request-run",
        prompt="run R1",
        message_type="text",
        correlation_id=None,
        source_ip="100.64.0.21",
        delivery_route=delivery_route(),
    )
    second = manager.dispatch(
        message_id="duplicate-request-run",
        prompt="run R1",
        message_type="text",
        correlation_id=None,
        source_ip="100.64.0.21",
        delivery_route=delivery_route(),
    )

    assert second.message == first.message
    quick_interactions.submit.assert_called_once()


def test_request_archive_is_distinct_from_session_archive(settings) -> None:
    manager, _codex_manager, _quick_interactions = configured_manager(settings)
    manager.request_backlog.save(title="待归档需求", content="验收：归档。")

    result = manager.dispatch(
        message_id="request-archive",
        prompt="archive R1",
        message_type="text",
        correlation_id=None,
        source_ip="100.64.0.21",
        delivery_route=delivery_route(),
    )

    assert result.message == (
        "Archive: Request R1 archived.\n\nRequest · R1 · 待归档需求"
    )
    assert manager.request_backlog.list_active() == ()
    manager.session_archiver.assert_not_called()


def test_request_archive_rejects_running_request(settings) -> None:
    manager, _codex_manager, _quick_interactions = configured_manager(settings)
    item = manager.request_backlog.save(title="运行中", content="验收：不能归档。")
    manager.request_backlog.claim_run(item.slot, "other-message")

    result = manager.dispatch(
        message_id="archive-running-request",
        prompt="归档需求一",
        message_type="text",
        correlation_id=None,
        source_ip="100.64.0.21",
        delivery_route=delivery_route(),
    )

    assert result.message == "Archive: Request R1 is running."


def test_request_recovery_rechecks_after_missing_task_grace(settings, monkeypatch) -> None:
    manager, _codex_manager, _quick_interactions = configured_manager(settings)
    item = manager.request_backlog.save(
        title="恢复需求",
        content="验收：宽限结束后自动收敛。",
    )
    manager.request_backlog.claim_run(item.slot, "interrupted-run")

    manager.reconcile_request_runs()

    timer = manager._request_reconcile_timer
    deadline = manager._request_reconcile_deadline
    assert timer is not None
    assert deadline is not None
    timer.cancel()
    monkeypatch.setattr(
        chub_mode_module,
        "utc_now",
        lambda: utc_now() + timedelta(seconds=301),
    )

    manager._run_scheduled_request_reconciliation(deadline)

    recovered = manager.request_backlog.get(item.slot)
    assert recovered.status == "failed"
    assert recovered.last_error == "Submission was interrupted before a task was recorded."
    manager.close()
