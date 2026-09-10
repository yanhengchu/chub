from __future__ import annotations

from tests.session_fixtures import CodexSession

import json
import re
import stat
import threading
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from app.codex.models import (
    CodexQuotaData,
    CodexQuotaWindow,
    CodexTokenUsageData,
    QuickInteractionWeixinRoute,
    WorkspaceInfo,
    utc_now,
)
from app.core.config import Settings
from app.core.response import ApiError
from app.services.openclaw_weixin_chub_mode import WeixinChubModeManager
from app.services.weixin_orchestration_dev import WeixinDevelopmentImplementation
from app.services.openclaw_weixin_chub_models import (
    MAX_STATE_BYTES,
    WeixinChubModePendingRetry,
    WeixinChubModeRuntimeConfig,
    WeixinChubModeSessionSlot,
    WeixinChubModeState,
    WeixinChubModeSubmission,
)
from app.services.weixin_translation import (
    TranslationConfirmationResult,
    TranslationEntry,
    TranslationExecutionOutcome,
)

from tests.openclaw_weixin_chub_mode_helpers import (
    configured_manager,
    delivery_route,
    enable_restart_command,
    inject_default_delivery_route,
    submitted_task_message,
)


def development_stage(source_hash: str = "a" * 64) -> MagicMock:
    stage = MagicMock()
    stage.snapshot.return_value = WeixinDevelopmentImplementation(
        implementation_id="weixin-orchestration-dev",
        source_hash=source_hash,
    )
    stage.execute_refinement.side_effect = (
        lambda *, enqueue_refinement, **_kwargs: enqueue_refinement()
    )
    return stage


def test_restart_network_routes_only_the_fixed_network_target(
    settings: Settings,
) -> None:
    manager, _codex_manager, _quick_interactions = configured_manager(settings)
    starter = MagicMock(
        return_value="Restart Network: Scheduled. The result will be sent when completed."
    )
    manager.maintenance_command_starter = starter

    result = manager.dispatch(
        message_id="restart-network",
        prompt="restart network",
        message_type="text",
        correlation_id=None,
        source_ip="127.0.0.1",
        delivery_route=delivery_route(),
    )

    assert result.message == "Restart Network: Scheduled. The result will be sent when completed."
    assert starter.call_args.args[0] == "network"
    assert starter.call_args.args[2] == delivery_route()


def test_duplicate_dispatch_is_logged_without_resubmitting(
    settings: Settings,
) -> None:
    manager, _codex_manager, quick_interactions = configured_manager(settings)
    manager.dispatch(
        message_id="dispatch-duplicate-1",
        prompt="检查设备状态",
        message_type="text",
        correlation_id=None,
        source_ip="100.64.0.21",
        delivery_route=delivery_route(),
    )

    with patch(
        "app.services.openclaw_weixin_chub_mode.write_operation"
    ) as write_operation:
        result = manager.dispatch(
            message_id="dispatch-duplicate-1",
            prompt="重复投递",
            message_type="text",
            correlation_id=None,
            source_ip="100.64.0.21",
            delivery_route=delivery_route(),
        )

    assert result.disposition == "reply"
    assert result.message == submitted_task_message(settings, "检查设备状态")
    assert quick_interactions.submit.call_count == 1
    dispatch_entries = [
        call.kwargs
        for call in write_operation.call_args_list
        if call.kwargs["action"] == "weixin_chub_mode_dispatch"
    ]
    assert [entry["status"] for entry in dispatch_entries] == [
        "requested",
        "started",
        "succeeded",
    ]


def test_dispatch_immediately_acknowledges_voice_task(
    settings: Settings,
) -> None:
    manager, _codex_manager, _quick_interactions = configured_manager(settings)

    result = manager.dispatch(
        message_id="dispatch-voice-1",
        prompt="检查语音任务",
        message_type="voice",
        correlation_id=None,
        source_ip="100.64.0.21",
        delivery_route=delivery_route(),
    )

    assert result.protocol_version == 3
    assert result.disposition == "reply"
    assert result.message == submitted_task_message(settings, "检查语音任务")


def test_submission_persists_one_direct_orchestration_request(
    settings: Settings,
) -> None:
    manager, _codex_manager, quick_interactions = configured_manager(settings)

    manager.dispatch(
        message_id="orchestration-record-1",
        prompt="整理今天的任务",
        message_type="text",
        correlation_id=None,
        source_ip="100.64.0.21",
        delivery_route=delivery_route(),
    )
    manager.dispatch(
        message_id="orchestration-record-1",
        prompt="重复投递不应创建新请求",
        message_type="text",
        correlation_id=None,
        source_ip="100.64.0.21",
        delivery_route=delivery_route(),
    )

    assert quick_interactions.submit.call_count == 1
    assert len(manager._state.orchestration_requests) == 1
    request = manager._state.orchestration_requests[0]
    assert request.message_id == "orchestration-record-1"
    assert request.implementation == "direct"
    assert request.status == "waiting"
    assert request.checkpoint == "task.submitted"
    assert request.session_id == "session-1"
    assert request.candidate_session_refs == [request.session_ref]
    assert request.creation_context == "default_slot"
    assert request.task_id == "task-1"
    assert request.original_prompt == "整理今天的任务"
    assert request.current_prompt == "整理今天的任务"
    assert request.stage_chain == []
    assert request.cursor == 0
    assert [(call.name, call.outcome) for call in request.capability_calls] == [
        ("create_session", "succeeded"),
        ("submit_task", "succeeded"),
    ]


def test_reconcile_records_terminal_task_without_resubmitting(
    settings: Settings,
) -> None:
    manager, _codex_manager, quick_interactions = configured_manager(settings)
    manager.dispatch(
        message_id="orchestration-reconcile-1",
        prompt="检查设备状态",
        message_type="text",
        correlation_id=None,
        source_ip="100.64.0.21",
        delivery_route=delivery_route(),
    )
    quick_interactions.get.return_value = SimpleNamespace(id="task-1", status="succeeded")

    manager.reconcile_orchestration_requests()

    request = manager._state.orchestration_requests[0]
    assert request.status == "completed"
    assert request.checkpoint == "task.finished"
    assert request.capability_calls[-1].name == "await_task"
    assert request.capability_calls[-1].outcome == "succeeded"
    assert quick_interactions.submit.call_count == 1


def test_reconcile_keeps_unconfirmed_task_out_of_success(
    settings: Settings,
) -> None:
    manager, _codex_manager, quick_interactions = configured_manager(settings)
    manager.dispatch(
        message_id="orchestration-unknown-1",
        prompt="检查设备状态",
        message_type="text",
        correlation_id=None,
        source_ip="100.64.0.21",
        delivery_route=delivery_route(),
    )
    quick_interactions.get.side_effect = ApiError(503, "quick_unavailable", "暂时不可用")

    manager.reconcile_orchestration_requests()

    request = manager._state.orchestration_requests[0]
    assert request.status == "unknown"
    assert request.checkpoint == "task.result_unknown"
    assert request.capability_calls[-1].outcome == "unknown"
    assert quick_interactions.submit.call_count == 1


def test_worker_completion_advances_the_bound_orchestration_request(
    settings: Settings,
) -> None:
    manager, _codex_manager, quick_interactions = configured_manager(settings)
    manager.dispatch(
        message_id="orchestration-completion-1",
        prompt="检查设备状态",
        message_type="text",
        correlation_id=None,
        source_ip="100.64.0.21",
        delivery_route=delivery_route(),
    )

    manager.record_orchestration_task_finished(
        SimpleNamespace(id="task-1", status="succeeded")
    )

    request = manager._state.orchestration_requests[0]
    assert request.status == "completed"
    assert request.checkpoint == "task.finished"
    assert request.capability_calls[-1].name == "await_task"
    assert request.capability_calls[-1].outcome == "succeeded"
    assert quick_interactions.submit.call_count == 1


def test_successful_submission_lists_all_sessions_and_running_tasks(
    settings: Settings,
) -> None:
    manager, codex_manager, quick_interactions = configured_manager(settings)
    sessions = [
        CodexSession(
            id=f"session-{slot}",
            workspace_id="chub",
            workspace_name="Chub",
            cwd="/project",
            title=title,
            permission_mode="full-access",
            status="stopped",
            activity="idle",
        )
        for slot, title in ((1, "当前工作"), (2, "后台检查"))
    ]
    manager._state.session_id = "session-1"
    manager._state.session_slots = [
        WeixinChubModeSessionSlot(slot=slot, session_id=f"session-{slot}")
        for slot in (1, 2)
    ]
    codex_manager.list_sessions.return_value = sessions
    codex_manager.get_session.return_value = sessions[0]
    quick_interactions.is_running.side_effect = (
        lambda session_id: session_id == "session-2"
    )
    quick_interactions.weixin_task_status_snapshot.return_value = SimpleNamespace(
        running_tasks=(("session-2", "检查后台日志"),)
    )

    result = manager.dispatch(
        message_id="submission-full-session-list",
        prompt="继续当前任务",
        message_type="text",
        correlation_id=None,
        source_ip="100.64.0.21",
        delivery_route=delivery_route(),
    )

    assert result.message is not None
    assert result.message.startswith("Submitted\n\nSessions\n\n")
    assert "▶ S1 · 当前工作\n\nTask · 继续当前任务" in result.message
    assert "S2 · 后台检查\n\nTask · 检查后台日志" in result.message
    assert "Weekly" not in result.message


def test_successful_submission_keeps_task_context_when_session_snapshot_fails(
    settings: Settings,
) -> None:
    manager, codex_manager, quick_interactions = configured_manager(settings)
    session = CodexSession(
        id="session-1",
        workspace_id="chub",
        workspace_name="Chub",
        cwd="/project",
        title="当前工作",
        permission_mode="full-access",
        status="stopped",
        activity="idle",
    )
    manager._state.session_id = session.id
    manager._state.session_slots = [
        WeixinChubModeSessionSlot(slot=1, session_id=session.id)
    ]
    codex_manager.get_session.return_value = session
    codex_manager.list_sessions.side_effect = [[session], OSError("unavailable")]

    result = manager.dispatch(
        message_id="submission-session-list-unavailable",
        prompt="继续当前任务",
        message_type="text",
        correlation_id=None,
        source_ip="100.64.0.21",
        delivery_route=delivery_route(),
    )

    assert result.message == (
        "Submitted\n\nSessions\n\n"
        "▶ S1 · 当前工作\n\nTask · 继续当前任务"
    )
    quick_interactions.submit.assert_called_once()


def test_submit_creates_one_private_session_and_replays_duplicate(
    settings: Settings,
) -> None:
    manager, codex_manager, quick_interactions = configured_manager(settings)

    first = manager.submit(
        message_id="message-1",
        prompt="检查设备状态",
        correlation_id="correlation-1",
        source_ip="100.64.0.21",
    )
    duplicate = manager.submit(
        message_id="message-1",
        prompt="不会再次执行",
        correlation_id="correlation-2",
        source_ip="100.64.0.21",
    )

    assert first.accepted is True
    assert first.duplicate is False
    assert first.new_session is True
    assert first.task_summary == "检查设备状态"
    assert duplicate.duplicate is True
    assert duplicate.task_summary is None
    assert duplicate.message == first.message
    codex_manager.create_session.assert_called_once_with(
        "chub",
        "full-access",
        None,
        None,
    )
    codex_manager.set_initial_quick_interaction_title.assert_not_called()
    quick_interactions.submit.assert_called_once_with(
        "session-1",
        "检查设备状态",
        summary_max_chars=48,
        summary_max_width=64,
        operation_id=quick_interactions.submit.call_args.kwargs["operation_id"],
        source_ip="100.64.0.21",
        notification_route=delivery_route(),
    )
    state_file = settings.openclaw.weixin_chub_mode.state_file
    assert stat.S_IMODE(state_file.stat().st_mode) == 0o600
    state_text = state_file.read_text(encoding="utf-8")
    assert "不会再次执行" not in state_text
    assert "weixin-account" not in state_text
    assert "owner@im.wechat" not in state_text
    persisted = json.loads(state_text)
    assert persisted["session_id"] == "session-1"
    assert persisted["submissions"][0]["task_id"] == "task-1"


def test_submit_accepts_worker_verification_without_inviting_resend(
    settings: Settings,
) -> None:
    manager, _codex_manager, quick_interactions = configured_manager(settings)
    quick_interactions.submit.return_value = SimpleNamespace(
        id="task-verifying",
        summary="检查设备状态",
        submission_verifying=True,
    )

    first = manager.submit(
        message_id="message-verifying",
        prompt="检查设备状态",
        correlation_id=None,
        source_ip="100.64.0.21",
    )
    duplicate = manager.submit(
        message_id="message-verifying",
        prompt="不应重复提交",
        correlation_id=None,
        source_ip="100.64.0.21",
    )

    assert first.accepted is True
    assert first.code == "submitted"
    assert "being verified" in first.message
    assert "Do not resend" in first.message
    assert duplicate.duplicate is True
    assert duplicate.message == first.message
    assert quick_interactions.submit.call_count == 1
    stored = manager._find_submission("message-verifying")
    assert stored is not None
    assert stored.status == "submitted"
    assert stored.task_id == "task-verifying"


def test_duplicate_submission_refreshes_current_session_marker(
    settings: Settings,
) -> None:
    manager, _codex_manager, _quick_interactions = configured_manager(settings)
    first = manager.submit(
        message_id="message-current-marker",
        prompt="检查设备状态",
        correlation_id=None,
        source_ip="100.64.0.21",
    )
    manager._state.session_slots.append(
        WeixinChubModeSessionSlot(slot=2, session_id="session-2")
    )
    manager._state.session_id = "session-2"
    manager._state.submissions[0].message = first.message.replace("\n\n", "\n")

    duplicate = manager.submit(
        message_id="message-current-marker",
        prompt="不会再次执行",
        correlation_id=None,
        source_ip="100.64.0.21",
    )

    assert "\n▶ S1 ·" in first.message
    assert "\n\nS1 · 检查设备状态\n\n" in duplicate.message
    assert "▶ S1" not in duplicate.message

    manager._state.session_slots = [
        WeixinChubModeSessionSlot(slot=1, session_id="session-reused")
    ]
    manager._state.session_id = "session-reused"
    reused_slot = manager.submit(
        message_id="message-current-marker",
        prompt="仍然不会再次执行",
        correlation_id=None,
        source_ip="100.64.0.21",
    )

    assert "S1 ·" not in reused_slot.message
    assert reused_slot.message == "Submitted\n\nTask · 检查设备状态"


def test_enabled_translation_queues_optimization_before_main_submission(
    settings: Settings,
) -> None:
    manager, _codex_manager, quick_interactions = configured_manager(settings)
    manager.translation_manager = MagicMock()
    manager.translation_manager.has_active_target.return_value = False
    manager.translation_manager.enqueue.return_value = True

    result = manager.submit(
        message_id="translation-order",
        prompt="需要翻译的任务",
        correlation_id=None,
        source_ip="100.64.0.21",
        delivery_route=delivery_route(),
        preprocess=True,
    )

    manager.translation_manager.enqueue.assert_called_once()
    quick_interactions.submit.assert_not_called()
    assert result.code == "translation_queued"
    assert "Optimizing · Preparing to submit." in result.message


def test_enabled_translation_is_silently_accepted_and_replayed(
    settings: Settings,
) -> None:
    manager, _codex_manager, quick_interactions = configured_manager(settings)
    manager.translation_manager = MagicMock()
    manager.translation_manager.enabled.return_value = True
    manager.translation_manager.has_active_target.return_value = False
    manager.translation_manager.enqueue.return_value = True

    first = manager.dispatch(
        message_id="translation-silent",
        prompt="需要翻译的任务",
        message_type="text",
        correlation_id=None,
        source_ip="100.64.0.21",
        delivery_route=delivery_route(),
    )
    replay = manager.dispatch(
        message_id="translation-silent",
        prompt="重复消息不会再次执行",
        message_type="text",
        correlation_id=None,
        source_ip="100.64.0.21",
        delivery_route=delivery_route(),
    )

    assert first.disposition == "handled"
    assert first.message is None
    assert replay.disposition == "handled"
    assert replay.message is None
    manager.translation_manager.enqueue.assert_called_once()
    quick_interactions.submit.assert_not_called()


def test_long_body_bypasses_text_processing_and_submits_directly(
    settings: Settings,
) -> None:
    settings.openclaw.weixin_chub_mode.translation_preprocess_max_input_chars = 10
    manager, _codex_manager, quick_interactions = configured_manager(settings)
    manager.translation_manager = MagicMock()
    manager.translation_manager.processing_mode.return_value = "confirm"
    manager.translation_manager.has_active_target.return_value = False
    long_prompt = "这是超过处理阈值的正文"

    result = manager.dispatch(
        message_id="long-text-direct",
        prompt=long_prompt,
        message_type="text",
        correlation_id=None,
        source_ip="100.64.0.21",
        delivery_route=delivery_route(),
    )

    assert result.disposition == "reply"
    assert result.message is not None
    assert result.message.startswith("Submitted\n\n")
    quick_interactions.submit.assert_called_once()
    assert quick_interactions.submit.call_args.args[1] == long_prompt
    manager.translation_manager.enqueue.assert_not_called()


def test_confirmed_translation_uses_started_notification_without_reply(
    settings: Settings,
) -> None:
    manager, _codex_manager, _quick_interactions = configured_manager(settings)
    entry = TranslationEntry(
        id="confirmed-translation",
        message_id="translation-source",
        original="检查服务",
        route=delivery_route(),
        operation_id="translation-operation",
        source_ip="100.64.0.21",
        target_session_id="session-1",
        polished="请检查服务状态。",
        english="Please check the service status.",
        status="awaiting_confirmation",
        created_at=utc_now(),
        updated_at=utc_now(),
    )
    manager.translation_manager = MagicMock()
    manager.translation_manager.active_confirmation.return_value = entry
    manager.translation_manager.confirm.return_value = TranslationConfirmationResult(
        handled=True,
        action="submit",
        entry=entry,
        message="Translation confirmed · Preparing to submit.",
    )
    manager.translation_manager.schedule_confirmed_submission_retry.return_value = True

    result = manager.dispatch(
        message_id="translation-confirm",
        prompt="text ok",
        message_type="text",
        correlation_id=None,
        source_ip="100.64.0.21",
        delivery_route=delivery_route(),
    )

    assert result.disposition == "handled"
    assert result.message is None
    manager.translation_manager.schedule_confirmed_submission_retry.assert_called_once_with(
        delay_seconds=0
    )


def test_confirmed_translation_reports_waiting_until_target_is_writable(
    settings: Settings,
) -> None:
    manager, _codex_manager, _quick_interactions = configured_manager(settings)
    entry = TranslationEntry(
        id="confirmed-waiting-translation",
        message_id="translation-source",
        original="检查服务",
        route=delivery_route(),
        operation_id="translation-operation",
        source_ip="100.64.0.21",
        target_session_id="session-1",
        polished="请检查服务状态。",
        english="Please check the service status.",
        status="awaiting_confirmation",
        created_at=utc_now(),
        updated_at=utc_now(),
    )
    manager.translation_manager = MagicMock()
    manager.translation_manager.active_confirmation.return_value = entry
    manager.translation_manager.confirm.return_value = TranslationConfirmationResult(
        handled=True,
        action="submit",
        entry=entry,
    )
    manager.quick_interactions.is_running.return_value = True
    manager.translation_manager.schedule_confirmed_submission_retry.return_value = True

    result = manager.dispatch(
        message_id="translation-confirm-waiting",
        prompt="text ok",
        message_type="text",
        correlation_id=None,
        source_ip="100.64.0.21",
        delivery_route=delivery_route(),
    )

    assert result.disposition == "reply"
    assert result.message == "Translation confirmed · Waiting for the target session."


def test_replayed_confirmed_translation_stays_silent(settings: Settings) -> None:
    manager, _codex_manager, _quick_interactions = configured_manager(settings)
    entry = TranslationEntry(
        id="replayed-confirmed-translation",
        message_id="translation-source",
        original="检查服务",
        route=delivery_route(),
        operation_id="translation-operation",
        source_ip="100.64.0.21",
        target_session_id="session-1",
        polished="请检查服务状态。",
        english="Please check the service status.",
        status="confirmed_waiting_target",
        created_at=utc_now(),
        updated_at=utc_now(),
    )
    manager.translation_manager = MagicMock()
    manager.translation_manager.active_confirmation.return_value = entry
    manager.translation_manager.confirm.return_value = TranslationConfirmationResult(
        handled=True,
        action="retry",
        entry=entry,
        message="Translation confirmed · Preparing to submit.",
    )

    result = manager.dispatch(
        message_id="translation-confirm-replay",
        prompt="text ok",
        message_type="text",
        correlation_id=None,
        source_ip="100.64.0.21",
        delivery_route=delivery_route(),
    )

    assert result.disposition == "handled"
    assert result.message is None
    manager.translation_manager.schedule_confirmed_submission_retry.assert_not_called()


def test_same_session_optimization_queues_while_target_is_busy(
    settings: Settings,
) -> None:
    manager, _codex_manager, quick_interactions = configured_manager(settings)
    manager.translation_manager = MagicMock()
    manager.translation_manager.has_active_target.return_value = True
    manager.translation_manager.enqueue.return_value = True
    quick_interactions.is_running.return_value = True

    result = manager.submit(
        message_id="concurrent-optimization",
        prompt="第二条任务",
        correlation_id=None,
        source_ip="100.64.0.21",
        delivery_route=delivery_route(),
        preprocess=True,
        confirmation_required=True,
    )

    assert result.code == "translation_queued"
    assert manager._state.pending_retry is None
    quick_interactions.submit.assert_not_called()
    quick_interactions.session_operation_guard.assert_not_called()
    manager.translation_manager.enqueue.assert_called_once()


def test_removed_direct_command_is_a_normal_task(settings: Settings) -> None:
    manager, _codex_manager, quick_interactions = configured_manager(settings)
    manager.translation_manager = MagicMock()
    manager.translation_manager.enabled.return_value = True
    manager.translation_manager.has_active_target.return_value = False

    result = manager.dispatch(
        message_id="direct-task",
        prompt="直接执行 检查设备状态",
        message_type="text",
        correlation_id=None,
        source_ip="100.64.0.21",
        delivery_route=delivery_route(),
    )

    assert result.disposition == "handled"
    quick_interactions.submit.assert_not_called()
    manager.translation_manager.enqueue.assert_called_once()


def test_optimized_task_selects_the_final_session_after_refinement(settings: Settings) -> None:
    manager, codex_manager, quick_interactions = configured_manager(settings)
    manager.translation_manager = MagicMock()
    manager.translation_manager.has_active_target.return_value = False
    manager.translation_manager.enqueue.return_value = True
    manager.translation_manager.entry_for_orchestration.return_value = SimpleNamespace(
        id="translation-entry"
    )
    manager.translation_result_notifier = MagicMock()
    manager.submit(
        message_id="optimized-source",
        prompt="检查下服务咋样",
        correlation_id=None,
        source_ip="100.64.0.21",
        delivery_route=delivery_route(),
        preprocess=True,
    )
    source = manager._find_submission("optimized-source")
    request = manager._state.orchestration_requests[0]
    assert codex_manager.create_session.call_count == 0
    assert quick_interactions.submit.call_count == 0
    assert request.original_prompt == "检查下服务咋样"
    assert request.current_prompt == "检查下服务咋样"
    assert [(stage.kind, stage.stage_id) for stage in request.stage_chain] == [
        ("development", "weixin_refinement")
    ]
    assert request.cursor == 0
    assert request.translation_entry_id == "translation-entry"
    assert request.checkpoint == "development.weixin_refinement.queued"
    codex_manager.list_sessions.return_value = [codex_manager.get_session.return_value]
    entry = TranslationEntry(
        id="translation-entry",
        message_id="optimized-source",
        original="检查下服务咋样",
        route=delivery_route(),
        operation_id="operation:translation",
        source_ip="100.64.0.21",
        target_session_id=source.session_id,
        created_at=utc_now(),
        updated_at=utc_now(),
    )

    outcome = manager.complete_optimized_task(
        entry,
        "请检查服务状态。",
        "Please check the service status.",
        None,
    )

    assert outcome.status == "submitted", outcome.error
    assert quick_interactions.submit.call_count == 1
    assert quick_interactions.submit.call_args.args == (
        "session-1",
        "请检查服务状态。",
    )
    assert len(manager._state.orchestration_requests) == 1
    request = manager._state.orchestration_requests[0]
    assert request.id == source.orchestration_id
    assert request.task_id == outcome.main_task_id
    assert request.checkpoint == "task.submitted"
    assert request.current_prompt == "请检查服务状态。"
    assert request.cursor == 1
    assert [(call.name, call.outcome) for call in request.capability_calls] == [
        ("create_session", "succeeded"),
        ("submit_task", "succeeded"),
    ]
    recovered_outcome = manager.complete_optimized_task(
        entry,
        "请检查服务状态。",
        "Please check the service status.",
        None,
    )
    assert recovered_outcome.status == "submitted"
    assert recovered_outcome.main_task_id == outcome.main_task_id
    assert quick_interactions.submit.call_count == 1
    manager.translation_result_notifier.assert_not_called()
    completed_entry = entry.model_copy(
        update={
            "status": outcome.status,
            "polished": "请检查服务状态。",
            "english": "Please check the service status.",
            "main_task_id": outcome.main_task_id,
        }
    )
    manager.notify_optimized_task_outcome(completed_entry)
    manager.translation_result_notifier.assert_called_once_with(
        completed_entry.route,
        outcome="started",
        target_session_id=completed_entry.target_session_id,
        task=completed_entry.polished,
        english=completed_entry.english,
        error=completed_entry.error,
    )

    replay = manager.dispatch(
        message_id="optimized-source",
        prompt="重复消息不会再次执行",
        message_type="text",
        correlation_id=None,
        source_ip="100.64.0.21",
        delivery_route=delivery_route(),
    )
    assert replay.disposition == "handled"
    assert replay.message is None
    assert quick_interactions.submit.call_count == 1


def test_development_implementation_snapshots_new_refinement_requests(
    settings: Settings,
) -> None:
    manager, _codex_manager, quick_interactions = configured_manager(settings)
    manager.development_stage = development_stage()
    manager.translation_manager = MagicMock()
    manager.translation_manager.enqueue.return_value = True
    manager.translation_manager.has_active_target.return_value = False
    manager.translation_manager.entry_for_orchestration.return_value = SimpleNamespace(
        id="development-translation"
    )

    manager.set_orchestration_implementation("weixin-orchestration-dev")
    manager.submit(
        message_id="development-refinement",
        prompt="检查服务",
        correlation_id=None,
        source_ip="100.64.0.21",
        delivery_route=delivery_route(),
        preprocess=True,
    )

    request = manager._state.orchestration_requests[0]
    stage = request.stage_chain[0]
    assert request.implementation == "weixin-orchestration-dev"
    assert stage.kind == "development"
    assert stage.development_ref == "weixin-orchestration-dev"
    assert stage.source_hash == "a" * 64
    assert request.checkpoint == "development.weixin_refinement.queued"
    manager.development_stage.execute_refinement.assert_called_once()
    quick_interactions.submit.assert_not_called()


def test_development_implementation_switch_only_affects_new_requests(
    settings: Settings,
) -> None:
    manager, _codex_manager, _quick_interactions = configured_manager(settings)
    manager.development_stage = development_stage()
    manager.translation_manager = MagicMock()
    manager.translation_manager.enqueue.return_value = True
    manager.translation_manager.entry_for_orchestration.side_effect = [
        SimpleNamespace(id="development-entry"),
        SimpleNamespace(id="development-next-entry"),
    ]

    manager.set_orchestration_implementation("weixin-orchestration-dev")
    manager.submit(
        message_id="development-existing",
        prompt="检查服务",
        correlation_id=None,
        source_ip="100.64.0.21",
        delivery_route=delivery_route(),
        preprocess=True,
    )
    manager.set_orchestration_implementation("weixin-orchestration-dev")
    manager.submit(
        message_id="development-new",
        prompt="检查服务",
        correlation_id=None,
        source_ip="100.64.0.21",
        delivery_route=delivery_route(),
        preprocess=True,
    )

    existing, new = manager._state.orchestration_requests
    assert existing.stage_chain[0].kind == "development"
    assert new.implementation == "weixin-orchestration-dev"
    assert new.stage_chain[0].kind == "development"


def test_development_preference_preserves_direct_and_confirmation_modes(
    settings: Settings,
) -> None:
    manager, _codex_manager, quick_interactions = configured_manager(settings)
    manager.development_stage = development_stage()
    manager.translation_manager = MagicMock()
    manager.translation_manager.enqueue.return_value = True
    manager.translation_manager.has_active_target.return_value = False
    manager.translation_manager.entry_for_orchestration.return_value = SimpleNamespace(
        id="development-confirmation-entry"
    )
    manager.set_orchestration_implementation("weixin-orchestration-dev")

    manager.submit(
        message_id="development-direct",
        prompt="直接执行",
        correlation_id=None,
        source_ip="100.64.0.21",
        delivery_route=delivery_route(),
    )
    direct = manager._state.orchestration_requests[0]
    assert direct.implementation == "direct"
    assert direct.stage_chain == []
    quick_interactions.submit.assert_called_once()

    manager.submit(
        message_id="development-confirmation",
        prompt="确认执行",
        correlation_id=None,
        source_ip="100.64.0.21",
        delivery_route=delivery_route(),
        preprocess=True,
        confirmation_required=True,
    )
    source = manager._find_submission("development-confirmation")
    outcome = manager.complete_optimized_task(
        TranslationEntry(
            id="development-confirmation-entry",
            message_id="development-confirmation",
            original="确认执行",
            route=delivery_route(),
            operation_id="development-confirmation-operation:translation",
            source_ip="100.64.0.21",
            target_session_id=source.session_id,
            confirmation_required=True,
            created_at=utc_now(),
            updated_at=utc_now(),
        ),
        "请确认执行。",
        "Please confirm execution.",
        None,
    )

    assert outcome.status == "ready_confirmation"
    request = manager._state.orchestration_requests[1]
    assert request.checkpoint == "development.weixin_refinement.awaiting_confirmation"
    assert quick_interactions.submit.call_count == 1


def test_development_source_change_blocks_new_or_existing_request(
    settings: Settings,
) -> None:
    manager, _codex_manager, quick_interactions = configured_manager(settings)
    manager.development_stage = development_stage()
    manager.translation_manager = MagicMock()
    manager.translation_manager.enqueue.return_value = True
    manager.translation_manager.entry_for_orchestration.return_value = SimpleNamespace(
        id="development-entry"
    )
    manager.set_orchestration_implementation("weixin-orchestration-dev")
    manager.submit(
        message_id="development-source-change",
        prompt="检查服务",
        correlation_id=None,
        source_ip="100.64.0.21",
        delivery_route=delivery_route(),
        preprocess=True,
    )
    source = manager._find_submission("development-source-change")
    manager.development_stage.snapshot.return_value = WeixinDevelopmentImplementation(
        implementation_id="weixin-orchestration-dev",
        source_hash="b" * 64,
    )

    with pytest.raises(ApiError) as error:
        manager.submit(
            message_id="development-new-after-change",
            prompt="检查服务",
            correlation_id=None,
            source_ip="100.64.0.21",
            delivery_route=delivery_route(),
            preprocess=True,
        )

    assert error.value.code == "weixin_orchestration_development_changed"
    manager.development_stage.require_snapshot.side_effect = ApiError(
        503,
        "weixin_orchestration_development_changed",
        "微信开发编排实现已变化，已受理任务未继续执行。",
    )
    outcome = manager.complete_optimized_task(
        TranslationEntry(
            id="development-entry",
            message_id="development-source-change",
            original="检查服务",
            route=delivery_route(),
            operation_id="development-operation:translation",
            source_ip="100.64.0.21",
            target_session_id=source.session_id,
            created_at=utc_now(),
            updated_at=utc_now(),
        ),
        "请检查服务。",
        "Please check the service.",
        None,
    )

    assert outcome.status == "failed"
    quick_interactions.submit.assert_not_called()
    request = manager._state.orchestration_requests[0]
    assert request.status == "rejected"
    assert request.checkpoint == "text_processing.failed"


def test_unavailable_development_implementation_does_not_accept_a_task(
    settings: Settings,
) -> None:
    manager, _codex_manager, quick_interactions = configured_manager(settings)
    manager.development_stage = development_stage()
    manager.development_stage.snapshot.side_effect = ApiError(
        503,
        "weixin_orchestration_development_unavailable",
        "微信开发编排实现当前不可用，本次任务未执行。",
    )
    manager._state.orchestration_implementation = "weixin-orchestration-dev"
    manager._state.orchestration_development_source_hash = "a" * 64
    manager.translation_manager = MagicMock()

    with pytest.raises(ApiError) as error:
        manager.submit(
            message_id="development-unavailable",
            prompt="检查服务",
            correlation_id=None,
            source_ip="100.64.0.21",
            delivery_route=delivery_route(),
            preprocess=True,
        )

    assert error.value.code == "weixin_orchestration_development_unavailable"
    manager.translation_manager.enqueue.assert_not_called()
    quick_interactions.submit.assert_not_called()


def test_refinement_confirmation_keeps_the_stage_pending_until_confirmed(
    settings: Settings,
) -> None:
    manager, _codex_manager, quick_interactions = configured_manager(settings)
    manager.translation_manager = MagicMock()
    manager.translation_manager.enqueue.return_value = True
    manager.translation_manager.entry_for_orchestration.return_value = SimpleNamespace(
        id="translation-confirmation-entry"
    )
    manager.submit(
        message_id="refinement-confirmation-source",
        prompt="检查服务",
        correlation_id=None,
        source_ip="100.64.0.21",
        delivery_route=delivery_route(),
        preprocess=True,
        confirmation_required=True,
    )
    entry = TranslationEntry(
        id="translation-confirmation-entry",
        message_id="refinement-confirmation-source",
        original="检查服务",
        route=delivery_route(),
        operation_id="operation-confirmation:translation",
        source_ip="100.64.0.21",
        confirmation_required=True,
        created_at=utc_now(),
        updated_at=utc_now(),
    )

    waiting = manager.complete_optimized_task(
        entry,
        "请检查服务。",
        "Please check the service.",
        None,
    )

    request = manager._state.orchestration_requests[0]
    assert waiting.status == "ready_confirmation"
    assert quick_interactions.submit.call_count == 0
    assert request.current_prompt == "请检查服务。"
    assert request.cursor == 0
    assert request.checkpoint == "development.weixin_refinement.awaiting_confirmation"

    submitted = manager.retry_confirmed_optimized_task(
        entry.model_copy(
            update={
                "confirmation_required": False,
                "polished": "请检查服务。",
                "english": "Please check the service.",
            }
        )
    )

    request = manager._state.orchestration_requests[0]
    assert submitted.status == "submitted"
    assert quick_interactions.submit.call_count == 1
    assert request.cursor == 1
    assert request.current_prompt == "请检查服务。"


def test_cancelled_refinement_confirmation_discards_its_orchestration_request(
    settings: Settings,
) -> None:
    manager, _codex_manager, quick_interactions = configured_manager(settings)
    manager.translation_manager = MagicMock()
    manager.translation_manager.enqueue.return_value = True
    manager.translation_manager.entry_for_orchestration.return_value = SimpleNamespace(
        id="cancelled-confirmation-entry"
    )
    manager.submit(
        message_id="cancelled-confirmation-source",
        prompt="检查服务",
        correlation_id=None,
        source_ip="100.64.0.21",
        delivery_route=delivery_route(),
        preprocess=True,
        confirmation_required=True,
    )
    entry = TranslationEntry(
        id="cancelled-confirmation-entry",
        message_id="cancelled-confirmation-source",
        original="检查服务",
        route=delivery_route(),
        operation_id="cancelled-operation:translation",
        source_ip="100.64.0.21",
        confirmation_required=True,
        status="discarded",
        error="Translation confirmation cancelled.",
        created_at=utc_now(),
        updated_at=utc_now(),
    )

    manager.discard_optimized_task(entry)

    request = manager._state.orchestration_requests[0]
    source = manager._find_submission("cancelled-confirmation-source")
    assert source is not None
    assert quick_interactions.submit.call_count == 0
    assert source.status == "rejected"
    assert source.code == "submission_failed"
    assert request.status == "rejected"
    assert request.checkpoint == "text_processing.discarded"


def test_interrupted_optimization_source_is_closed_and_replays_silently(
    settings: Settings,
) -> None:
    manager, _codex_manager, quick_interactions = configured_manager(settings)
    now = utc_now()
    route = delivery_route()
    manager._state.submissions.append(
        WeixinChubModeSubmission(
            message_id="interrupted-optimized-source",
            correlation_id=None,
            operation_id="interrupted-optimized-operation",
            delivery_route_fingerprint=manager._route_fingerprint(route),
            status="rejected",
            code="submission_interrupted",
            message="Chub 重启中断了本次提交。",
            http_status=409,
            created_at=now,
            updated_at=now,
        )
    )
    entry = TranslationEntry(
        id="interrupted-translation-entry",
        message_id="interrupted-optimized-source",
        original="检查服务",
        route=route,
        operation_id="interrupted-optimized-operation:translation",
        source_ip="100.64.0.21",
        target_session_id="session-1",
        created_at=now,
        updated_at=now,
    )

    outcome = manager.complete_optimized_task(
        entry,
        None,
        None,
        "服务重启前翻译任务未完成提交，未自动重试。",
    )
    replay = manager.dispatch(
        message_id="interrupted-optimized-source",
        prompt="重复消息不应重新执行",
        message_type="text",
        correlation_id=None,
        source_ip="100.64.0.21",
        delivery_route=route,
    )

    source = manager._find_submission("interrupted-optimized-source")
    assert outcome.status == "failed"
    assert source is not None
    assert source.status == "rejected"
    assert source.code == "submission_failed"
    assert source.dispatch_disposition == "handled"
    assert replay.disposition == "handled"
    assert replay.message is None
    quick_interactions.submit.assert_not_called()


def test_optimized_task_waits_if_target_becomes_busy(
    settings: Settings,
) -> None:
    manager, codex_manager, quick_interactions = configured_manager(settings)
    manager.translation_manager = MagicMock()
    manager.translation_manager.has_active_target.return_value = False
    manager.translation_manager.enqueue.return_value = True
    manager.translation_result_notifier = MagicMock()
    manager.submit(
        message_id="optimized-busy",
        prompt="检查服务",
        correlation_id=None,
        source_ip="100.64.0.21",
        delivery_route=delivery_route(),
        preprocess=True,
    )
    source = manager._find_submission("optimized-busy")
    codex_manager.list_sessions.return_value = [codex_manager.get_session.return_value]
    entry = TranslationEntry(
        id="translation-busy",
        message_id="optimized-busy",
        original="检查服务",
        route=delivery_route(),
        operation_id="operation-busy:translation",
        source_ip="100.64.0.21",
        target_session_id=source.session_id,
        created_at=utc_now(),
        updated_at=utc_now(),
    )
    quick_interactions.is_running.return_value = True

    outcome = manager.complete_optimized_task(
        entry,
        "请检查服务。",
        "Please check the service.",
        None,
    )

    assert outcome.status == "confirmed_waiting_target"
    quick_interactions.submit.assert_not_called()
    assert manager._state.pending_retry is None

    request = manager._state.orchestration_requests[0]
    assert request.status == "waiting"
    assert request.checkpoint == "dispatch.waiting_target"

    quick_interactions.is_running.return_value = False
    resumed = manager.retry_confirmed_optimized_task(
        entry.model_copy(update={
            "confirmation_required": False,
            "polished": "请检查服务。",
            "english": "Please check the service.",
        })
    )

    assert resumed.status == "submitted"
    quick_interactions.submit.assert_called_once()
    request = manager._state.orchestration_requests[0]
    assert request.status == "waiting"
    assert request.checkpoint == "task.submitted"
    assert request.task_id == "task-1"


def test_submit_rejects_invalid_delivery_route_before_codex(
    settings: Settings,
) -> None:
    manager, codex_manager, quick_interactions = configured_manager(settings)
    manager.route_validator = MagicMock(return_value="原消息的 ClawBot 当前不可用。")

    with pytest.raises(ApiError) as error:
        manager.submit(
            message_id="message-route-invalid",
            prompt="检查设备",
            correlation_id=None,
            source_ip="100.64.0.21",
        )

    assert error.value.code == "weixin_chub_mode_delivery_route_invalid"
    codex_manager.create_session.assert_not_called()
    quick_interactions.submit.assert_not_called()


def test_duplicate_message_with_different_route_is_rejected(
    settings: Settings,
) -> None:
    manager, _codex_manager, quick_interactions = configured_manager(settings)
    manager.submit(
        message_id="message-route-conflict",
        prompt="检查设备",
        correlation_id=None,
        source_ip="100.64.0.21",
    )

    with pytest.raises(ApiError) as error:
        manager.submit(
            message_id="message-route-conflict",
            prompt="不能重复执行",
            correlation_id=None,
            source_ip="100.64.0.21",
            delivery_route=delivery_route(recipient="other@im.wechat"),
        )

    assert error.value.code == "weixin_chub_mode_message_conflict"
    quick_interactions.submit.assert_called_once()


def test_mode_readiness_no_longer_requires_global_recipient(
    settings: Settings,
) -> None:
    manager, _codex_manager, _quick_interactions = configured_manager(settings)
    settings.openclaw.quick_interaction_completion.weixin_recipient = None

    status = manager.status()

    assert status.ready is True


def test_disabled_runtime_has_specific_weixin_reply_and_chub_status(
    settings: Settings,
) -> None:
    manager, codex_manager, quick_interactions = configured_manager(settings)
    codex_manager.require_runtime_submission.side_effect = ApiError(
        409,
        "ai_runtime_disabled",
        "当前 AI Runtime 已停用，无法提交新的 AI 任务。",
    )

    status = manager.status()
    result = manager.dispatch(
        message_id="runtime-disabled",
        prompt="检查设备状态",
        message_type="text",
        correlation_id=None,
        source_ip="100.64.0.21",
        delivery_route=delivery_route(),
    )

    assert status.ready is False
    assert status.code == "ai_runtime_disabled"
    assert result.message == (
        "Not submitted · Codex Runtime is disabled. Chub is in base mode. "
        "Enable it in Settings to submit AI tasks.\n\n"
        "Task · 检查设备状态"
    )
    quick_interactions.submit.assert_not_called()


def test_unavailable_quick_worker_has_specific_weixin_reply(
    settings: Settings,
) -> None:
    manager, _codex_manager, quick_interactions = configured_manager(settings)
    quick_interactions.require_quick_session_creation.side_effect = ApiError(
        503,
        "quick_worker_unavailable",
        "Quick Worker 当前不可用，无法创建快速交互 Session。",
    )

    status = manager.status()
    result = manager.dispatch(
        message_id="worker-unavailable",
        prompt="检查设备状态",
        message_type="text",
        correlation_id=None,
        source_ip="100.64.0.21",
        delivery_route=delivery_route(),
    )

    assert status.ready is False
    assert status.code == "quick_worker_unavailable"
    assert result.message == (
        "Not submitted · Quick Worker is unavailable. Try again later.\n\n"
        "Task · 检查设备状态"
    )
    quick_interactions.submit.assert_not_called()


def test_unavailable_quick_worker_keeps_weixin_recovery_command_available(
    settings: Settings,
) -> None:
    manager, _codex_manager, quick_interactions = configured_manager(settings)
    quick_interactions.require_quick_session_creation.side_effect = ApiError(
        503,
        "quick_worker_unavailable",
        "Quick Worker 当前不可用，无法创建快速交互 Session。",
    )
    starter = MagicMock(
        return_value="Restart Worker: Scheduled. The result will be sent when completed."
    )
    manager.maintenance_command_starter = starter

    result = manager.dispatch(
        message_id="worker-recovery",
        prompt="restart worker",
        message_type="text",
        correlation_id=None,
        source_ip="100.64.0.21",
        delivery_route=delivery_route(),
    )

    assert result.message == (
        "Restart Worker: Scheduled. The result will be sent when completed."
    )
    assert starter.call_args.args[0] == "worker"


def test_submit_reuses_session_when_defaults_resolve_to_effective_model(
    settings: Settings,
) -> None:
    manager, codex_manager, quick_interactions = configured_manager(settings)
    codex_manager.get_session.return_value = CodexSession(
        id="session-1",
        workspace_id="chub",
        workspace_name="Chub",
        cwd="/project",
        permission_mode="full-access",
        model="gpt-effective-default",
        reasoning_effort="medium",
        status="stopped",
        activity="idle",
    )

    first = manager.submit(
        message_id="message-1",
        prompt="第一条任务",
        correlation_id=None,
        source_ip="100.64.0.21",
    )
    second = manager.submit(
        message_id="message-2",
        prompt="第二条任务",
        correlation_id=None,
        source_ip="100.64.0.21",
    )

    assert first.new_session is True
    assert second.new_session is False
    codex_manager.create_session.assert_called_once()
    assert quick_interactions.submit.call_count == 2
    assert manager.session_id() == "session-1"


def test_submit_replaces_session_when_explicit_model_no_longer_matches(
    settings: Settings,
) -> None:
    settings.openclaw.weixin_chub_mode.model = "configured-model"
    manager, codex_manager, quick_interactions = configured_manager(settings)
    manager._state.session_id = "old-session"
    codex_manager.get_session.return_value = CodexSession(
        id="old-session",
        workspace_id="chub",
        workspace_name="Chub",
        cwd="/project",
        permission_mode="full-access",
        model="different-model",
        reasoning_effort="medium",
    )
    codex_manager.create_session.return_value = SimpleNamespace(id="new-session")
    quick_interactions.submit.return_value = SimpleNamespace(id="task-1")

    result = manager.submit(
        message_id="message-1",
        prompt="检查模型配置",
        correlation_id=None,
        source_ip="100.64.0.21",
    )

    assert result.new_session is True
    codex_manager.create_session.assert_called_once_with(
        "chub",
        "full-access",
        "configured-model",
        None,
    )
    quick_interactions.submit.assert_called_once_with(
        "new-session",
        "检查模型配置",
        summary_max_chars=48,
        summary_max_width=64,
        operation_id=quick_interactions.submit.call_args.kwargs["operation_id"],
        source_ip="100.64.0.21",
        notification_route=delivery_route(),
    )


def test_submit_logs_dispatch_lifecycle_without_exposing_prompt(
    settings: Settings,
) -> None:
    manager, _codex_manager, _quick_interactions = configured_manager(settings)

    with patch(
        "app.services.openclaw_weixin_chub_mode.write_operation"
    ) as write_operation:
        manager.submit(
            message_id="message-1",
            prompt="不应出现在操作日志",
            correlation_id=None,
            source_ip="100.64.0.21",
        )

    dispatch_entries = [
        call.kwargs
        for call in write_operation.call_args_list
        if call.kwargs["action"] == "weixin_chub_mode_dispatch"
    ]
    assert [entry["status"] for entry in dispatch_entries] == [
        "requested",
        "started",
        "succeeded",
    ]
    assert {entry["target"] for entry in dispatch_entries} == {settings.node.id}
    assert all(
        "不应出现在操作日志" not in str(entry) for entry in dispatch_entries
    )


def test_submit_reclaims_unknown_session_before_quick_interaction(
    settings: Settings,
) -> None:
    manager, codex_manager, quick_interactions = configured_manager(settings)
    manager._state.session_id = "session-1"
    codex_manager.get_session.return_value = CodexSession(
        id="session-1",
        workspace_id="chub",
        workspace_name="Chub",
        cwd="/project",
        codex_session_id="native-session-1",
        permission_mode="full-access",
        status="running",
        activity="unknown",
    )
    reclaimer = MagicMock(
        return_value=SimpleNamespace(status="stopped", activity="idle")
    )
    manager.session_reclaimer = reclaimer

    with patch(
        "app.services.openclaw_weixin_chub_mode.write_operation"
    ) as write_operation:
        result = manager.submit(
            message_id="message-unknown",
            prompt="检查设备",
            correlation_id=None,
            source_ip="100.64.0.21",
        )

    assert result.message == "Submitted\n\nTask · 检查设备"
    assert result.task_summary == "检查设备"
    reclaimer.assert_called_once_with("session-1")
    codex_manager.wait_for_writer_release.assert_called_once_with(
        "native-session-1",
        timeout=3.0,
    )
    quick_interactions.submit.assert_called_once()
    reclaim_entries = [
        call.kwargs
        for call in write_operation.call_args_list
        if call.kwargs["action"] == "weixin_chub_mode_session_reclaim"
    ]
    assert [entry["status"] for entry in reclaim_entries] == [
        "requested",
        "started",
        "succeeded",
    ]


def test_submit_rejects_unknown_session_when_writer_does_not_release(
    settings: Settings,
) -> None:
    manager, codex_manager, quick_interactions = configured_manager(settings)
    manager._state.session_id = "session-1"
    codex_manager.get_session.return_value = CodexSession(
        id="session-1",
        workspace_id="chub",
        workspace_name="Chub",
        cwd="/project",
        codex_session_id="native-session-1",
        permission_mode="full-access",
        status="running",
        activity="unknown",
    )
    codex_manager.wait_for_writer_release.return_value = False
    manager.session_reclaimer = MagicMock(
        return_value=SimpleNamespace(status="stopped", activity="idle")
    )

    with pytest.raises(ApiError) as error:
        manager.submit(
            message_id="message-writer-active",
            prompt="检查设备",
            correlation_id=None,
            source_ip="100.64.0.21",
        )

    assert error.value.code == "weixin_chub_mode_submission_failed"
    assert "未能安全停止" in error.value.message
    quick_interactions.submit.assert_not_called()


def test_submit_rejects_busy_session_and_replays_same_failure(
    settings: Settings,
) -> None:
    manager, _codex_manager, quick_interactions = configured_manager(settings)
    manager.submit(
        message_id="message-1",
        prompt="首个任务",
        correlation_id=None,
        source_ip="100.64.0.21",
    )
    quick_interactions.is_running.return_value = True

    with pytest.raises(ApiError) as first_error:
        manager.submit(
            message_id="message-2",
            prompt="第二个任务",
            correlation_id=None,
            source_ip="100.64.0.21",
        )
    with pytest.raises(ApiError) as duplicate_error:
        manager.submit(
            message_id="message-2",
            prompt="重复消息",
            correlation_id=None,
            source_ip="100.64.0.21",
        )

    assert first_error.value.code == "weixin_chub_mode_in_progress"
    assert duplicate_error.value.code == "weixin_chub_mode_in_progress"
    assert quick_interactions.submit.call_count == 1


def test_disabled_submission_failure_is_persisted_for_idempotency(
    settings: Settings,
) -> None:
    codex_manager = MagicMock()
    quick_interactions = MagicMock()
    manager = WeixinChubModeManager(
        settings,
        codex_manager,
        quick_interactions,
    )

    for prompt in ("首次消息", "重复消息"):
        with pytest.raises(ApiError) as error:
            manager.submit(
                message_id="message-1",
                prompt=prompt,
                correlation_id=None,
                source_ip="100.64.0.21",
            )
        assert error.value.code == "weixin_chub_mode_mode_disabled"

    quick_interactions.submit.assert_not_called()
    payload = json.loads(
        settings.openclaw.weixin_chub_mode.state_file.read_text(encoding="utf-8")
    )
    assert payload["submissions"][0]["status"] == "rejected"
    assert payload["submissions"][0]["code"] == "mode_disabled"


def test_quick_interaction_failure_replays_same_bounded_error(
    settings: Settings,
) -> None:
    manager, _codex_manager, quick_interactions = configured_manager(settings)
    quick_interactions.submit.side_effect = ApiError(
        503,
        "quick_worker_unavailable",
        "底层实现细节不应透传。",
    )

    errors = []
    for prompt in ("首次消息", "重复消息"):
        with pytest.raises(ApiError) as error:
            manager.submit(
                message_id="message-1",
                prompt=prompt,
                correlation_id=None,
                source_ip="100.64.0.21",
            )
        errors.append(error.value)

    assert [error.status_code for error in errors] == [503, 503]
    assert [error.code for error in errors] == [
        "weixin_chub_mode_submission_failed",
        "weixin_chub_mode_submission_failed",
    ]
    assert errors[0].message == errors[1].message == "微信任务提交失败。"
    quick_interactions.submit.assert_called_once()
