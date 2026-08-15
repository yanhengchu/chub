from __future__ import annotations

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
    CodexSession,
    CodexTokenUsageData,
    QuickInteractionWeixinRoute,
    WorkspaceInfo,
    utc_now,
)
from app.core.config import Settings
from app.core.response import ApiError
from app.services.openclaw_weixin_chub_mode import WeixinChubModeManager
from app.services.openclaw_weixin_chub_models import (
    MAX_STATE_BYTES,
    WeixinChubModePendingRetry,
    WeixinChubModeRuntimeConfig,
    WeixinChubModeSessionSlot,
    WeixinChubModeState,
    WeixinChubModeSubmission,
)

from tests.openclaw_weixin_chub_mode_helpers import (
    configured_manager,
    delivery_route,
    enable_restart_command,
    inject_default_delivery_route,
)


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

    assert result.disposition == "handled"
    assert result.message is None
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


def test_dispatch_silently_accepts_voice_task(
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
    assert result.disposition == "handled"
    assert result.message is None


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
        operation_id=quick_interactions.submit.call_args.kwargs["operation_id"],
        source_ip="100.64.0.21",
        notification_route=delivery_route(),
        weixin_session_slot=1,
        weixin_session_title="检查设备状态",
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


def test_translation_is_enqueued_after_main_submission_is_persisted(
    settings: Settings,
) -> None:
    manager, _codex_manager, _quick_interactions = configured_manager(settings)
    manager.translation_manager = MagicMock()
    original_replace = manager._replace_submission
    persisted = False

    def replace_and_mark(submission) -> None:
        nonlocal persisted
        original_replace(submission)
        persisted = True

    manager._replace_submission = replace_and_mark
    manager.translation_manager.enqueue.side_effect = (
        lambda **_kwargs: persisted or pytest.fail("translation queued too early")
    )

    manager.submit(
        message_id="translation-order",
        prompt="需要翻译的任务",
        correlation_id=None,
        source_ip="100.64.0.21",
        delivery_route=delivery_route(),
    )

    manager.translation_manager.enqueue.assert_called_once()


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
        operation_id=quick_interactions.submit.call_args.kwargs["operation_id"],
        source_ip="100.64.0.21",
        notification_route=delivery_route(),
        weixin_session_slot=1,
        weixin_session_title="检查模型配置",
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
    manager.terminal_reclaimer = reclaimer

    with patch(
        "app.services.openclaw_weixin_chub_mode.write_operation"
    ) as write_operation:
        result = manager.submit(
            message_id="message-unknown",
            prompt="检查设备",
            correlation_id=None,
            source_ip="100.64.0.21",
        )

    assert result.message == "任务已提交，完成后将通过微信发送结果。"
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
    manager.terminal_reclaimer = MagicMock(
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
