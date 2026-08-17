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


def test_codex_retry_submits_latest_busy_task_to_current_session(
    settings: Settings,
) -> None:
    manager, _codex_manager, quick_interactions = configured_manager(settings)
    manager._state.session_id = "session-1"
    quick_interactions.is_running.return_value = True
    manager.dispatch(
        message_id="busy-original-1",
        prompt="继续检查设备",
        message_type="text",
        correlation_id=None,
        source_ip="100.64.0.21",
        delivery_route=delivery_route(),
    )
    quick_interactions.is_running.return_value = False

    result = manager.dispatch(
        message_id="retry-command-1",
        prompt="Retry",
        message_type="text",
        correlation_id=None,
        source_ip="100.64.0.21",
        delivery_route=delivery_route(),
    )

    assert result.message is not None
    assert result.message == (
        "Retry: The task was resubmitted.\n\n"
        "Task · 继续检查设备"
    )
    quick_interactions.submit.assert_called_once()
    assert quick_interactions.submit.call_args.args[1] == "继续检查设备"
    assert manager._state.pending_retry is None


@pytest.mark.parametrize(
    "command",
    [
        "New Retry。",
        "新建 重试",
        " 新建 继续执行。 ",
    ],
)
def test_codex_new_retry_creates_session_and_submits_latest_busy_task(
    settings: Settings,
    command: str,
) -> None:
    manager, codex_manager, quick_interactions = configured_manager(settings)
    manager._state.session_id = "session-1"
    quick_interactions.is_running.return_value = True
    manager.dispatch(
        message_id=f"busy-original-{command}",
        prompt="探索另一个问题",
        message_type="text",
        correlation_id=None,
        source_ip="100.64.0.21",
        delivery_route=delivery_route(),
    )

    codex_manager.create_session.return_value = SimpleNamespace(id="session-2")
    codex_manager.get_session.return_value = CodexSession(
        id="session-2",
        workspace_id="chub",
        workspace_name="Chub",
        cwd="/project",
        permission_mode="full-access",
        status="stopped",
        activity="idle",
    )
    quick_interactions.is_running.return_value = False
    result = manager.dispatch(
        message_id=f"combined-command-{command}",
        prompt=command,
        message_type="text",
        correlation_id=None,
        source_ip="100.64.0.21",
        delivery_route=delivery_route(),
    )

    assert result.message is not None
    assert result.message == (
        "Retry: A new Session was created and selected. The task was resubmitted.\n\n"
        "Task · 探索另一个问题"
    )
    assert manager.session_id() == "session-2"
    assert quick_interactions.submit.call_args.args[:2] == (
        "session-2",
        "探索另一个问题",
    )
    assert manager._state.pending_retry is None


def test_codex_switch_retry_selects_target_and_submits_pending_task(
    settings: Settings,
) -> None:
    manager, codex_manager, quick_interactions = configured_manager(settings)
    manager._state.session_id = "session-1"
    manager._state.session_slots = [
        WeixinChubModeSessionSlot(slot=1, session_id="session-1"),
        WeixinChubModeSessionSlot(slot=2, session_id="session-2"),
    ]
    sessions = [
        CodexSession(
            id=f"session-{slot}",
            workspace_id="chub",
            workspace_name="Chub",
            cwd="/project",
            title=f"Session {slot}",
            permission_mode="full-access",
            status="stopped",
            activity="idle",
        )
        for slot in (1, 2)
    ]
    by_id = {session.id: session for session in sessions}
    codex_manager.list_sessions.return_value = sessions
    codex_manager.get_session.side_effect = lambda session_id: by_id[session_id]
    quick_interactions.is_running.return_value = True
    manager.dispatch(
        message_id="busy-before-switch-retry",
        prompt="继续目标任务",
        message_type="text",
        correlation_id=None,
        source_ip="100.64.0.21",
        delivery_route=delivery_route(),
    )
    quick_interactions.is_running.return_value = False

    result = manager.dispatch(
        message_id="switch-retry",
        prompt="switch S2 retry",
        message_type="text",
        correlation_id=None,
        source_ip="100.64.0.21",
        delivery_route=delivery_route(),
    )
    submit_calls = quick_interactions.submit.call_count
    duplicate = manager.dispatch(
        message_id="switch-retry",
        prompt="切换1重试",
        message_type="text",
        correlation_id=None,
        source_ip="100.64.0.21",
        delivery_route=delivery_route(),
    )

    assert result.message is not None
    assert result.message.startswith(
        "Switch: Session 2 selected. Retry: The task was resubmitted."
    )
    assert result.message.count("\n\nSessions\n\n") == 1
    assert manager.session_id() == "session-2"
    assert manager._state.pending_retry is None
    assert quick_interactions.submit.call_args.args[:2] == (
        "session-2",
        "继续目标任务",
    )
    assert duplicate == result
    assert quick_interactions.submit.call_count == submit_calls


def test_codex_switch_retry_busy_target_keeps_new_binding_and_pending_task(
    settings: Settings,
) -> None:
    manager, codex_manager, quick_interactions = configured_manager(settings)
    manager._state.session_id = "session-1"
    manager._state.session_slots = [
        WeixinChubModeSessionSlot(slot=1, session_id="session-1"),
        WeixinChubModeSessionSlot(slot=2, session_id="session-2"),
    ]
    sessions = [
        CodexSession(
            id="session-1",
            workspace_id="chub",
            workspace_name="Chub",
            cwd="/project",
            title="Current",
            permission_mode="full-access",
            status="stopped",
            activity="idle",
        ),
        CodexSession(
            id="session-2",
            workspace_id="chub",
            workspace_name="Chub",
            cwd="/project",
            title="Busy target",
            permission_mode="full-access",
            status="running",
            activity="working",
            activity_source="quick",
        ),
    ]
    codex_manager.list_sessions.return_value = sessions
    codex_manager.get_session.side_effect = lambda session_id: next(
        session for session in sessions if session.id == session_id
    )
    quick_interactions.is_running.return_value = True
    manager.dispatch(
        message_id="busy-before-target-check",
        prompt="必须保留的任务",
        message_type="text",
        correlation_id=None,
        source_ip="100.64.0.21",
        delivery_route=delivery_route(),
    )
    quick_interactions.submit.reset_mock()

    result = manager.dispatch(
        message_id="switch-retry-busy-target",
        prompt="会话 S2 重试",
        message_type="text",
        correlation_id=None,
        source_ip="100.64.0.21",
        delivery_route=delivery_route(),
    )

    assert result.message is not None
    assert result.message.startswith(
        "Switch: Session 2 selected. Retry: The task was not resubmitted."
    )
    assert manager.session_id() == "session-2"
    assert manager._state.pending_retry is not None
    assert manager._state.pending_retry.prompt == "必须保留的任务"
    quick_interactions.submit.assert_not_called()


def test_codex_switch_retry_continues_when_target_is_already_current(
    settings: Settings,
) -> None:
    manager, codex_manager, quick_interactions = configured_manager(settings)
    manager._state.session_id = "session-1"
    manager._state.session_slots = [
        WeixinChubModeSessionSlot(slot=1, session_id="session-1")
    ]
    session = CodexSession(
        id="session-1",
        workspace_id="chub",
        workspace_name="Chub",
        cwd="/project",
        title="Current",
        permission_mode="full-access",
        status="stopped",
        activity="idle",
    )
    codex_manager.list_sessions.return_value = [session]
    codex_manager.get_session.return_value = session
    quick_interactions.is_running.return_value = True
    manager.dispatch(
        message_id="busy-current-before-switch-retry",
        prompt="继续当前任务",
        message_type="text",
        correlation_id=None,
        source_ip="100.64.0.21",
        delivery_route=delivery_route(),
    )
    quick_interactions.is_running.return_value = False

    result = manager.dispatch(
        message_id="switch-retry-current",
        prompt="切换1重试",
        message_type="text",
        correlation_id=None,
        source_ip="100.64.0.21",
        delivery_route=delivery_route(),
    )

    assert result.message is not None
    assert result.message.startswith(
        "Switch: Session 1 selected. Retry: The task was resubmitted."
    )
    assert manager.session_id() == "session-1"
    assert manager._state.pending_retry is None
    assert quick_interactions.submit.call_args.args[:2] == (
        "session-1",
        "继续当前任务",
    )


@pytest.mark.parametrize(
    ("prompt", "pending_prompt", "expected_status"),
    [
        ("切换 S2 继续检查日志", None, "Task submitted"),
        ("切换 S2 重试", "继续被阻塞的任务", "The task was resubmitted"),
    ],
)
def test_switch_continuation_resumes_after_final_state_write_is_interrupted(
    settings: Settings,
    prompt: str,
    pending_prompt: str | None,
    expected_status: str,
) -> None:
    manager, codex_manager, quick_interactions = configured_manager(settings)
    manager._state.session_id = "session-1"
    sessions = [
        CodexSession(
            id=f"session-{slot}",
            workspace_id="chub",
            workspace_name="Chub",
            cwd="/project",
            title=f"Session {slot}",
            permission_mode="full-access",
            status="stopped",
            activity="idle",
        )
        for slot in (1, 2)
    ]
    manager._state.session_slots = [
        WeixinChubModeSessionSlot(slot=slot, session_id=f"session-{slot}")
        for slot in (1, 2)
    ]
    if pending_prompt is not None:
        now = utc_now()
        manager._state.pending_retry = WeixinChubModePendingRetry(
            original_message_id="original-busy-message",
            prompt=pending_prompt,
            delivery_route_fingerprint=manager._route_fingerprint(delivery_route()),
            session_id="session-1",
            created_at=now,
            expires_at=now + timedelta(minutes=10),
        )
    by_id = {session.id: session for session in sessions}
    codex_manager.list_sessions.return_value = sessions
    codex_manager.get_session.side_effect = lambda session_id: by_id[session_id]
    manager._finish_codex_switch = MagicMock(side_effect=KeyboardInterrupt)

    with pytest.raises(KeyboardInterrupt):
        manager.dispatch(
            message_id="recoverable-switch-continuation",
            prompt=prompt,
            message_type="text",
            correlation_id=None,
            source_ip="100.64.0.21",
            delivery_route=delivery_route(),
        )

    parent = manager._find_submission("recoverable-switch-continuation")
    assert parent is not None
    assert parent.status == "reserved"
    assert parent.continuation_kind is not None
    quick_interactions.submit.assert_called_once()

    recovered, recovered_codex, recovered_quick = configured_manager(settings)
    recovered_codex.list_sessions.return_value = sessions
    recovered_codex.get_session.side_effect = lambda session_id: by_id[session_id]
    result = recovered.dispatch(
        message_id="recoverable-switch-continuation",
        prompt=prompt,
        message_type="text",
        correlation_id=None,
        source_ip="100.64.0.21",
        delivery_route=delivery_route(),
    )

    assert expected_status in (result.message or "")
    assert recovered.session_id() == "session-2"
    assert recovered._find_submission("recoverable-switch-continuation").status == "routed"
    assert recovered._state.pending_retry is None
    recovered_quick.submit.assert_not_called()


def test_busy_task_replaces_previous_pending_retry(
    settings: Settings,
) -> None:
    manager, _codex_manager, quick_interactions = configured_manager(settings)
    manager._state.session_id = "session-1"
    quick_interactions.is_running.return_value = True

    for message_id, prompt in (("busy-1", "第一个问题"), ("busy-2", "第二个问题")):
        manager.dispatch(
            message_id=message_id,
            prompt=prompt,
            message_type="text",
            correlation_id=None,
            source_ip="100.64.0.21",
            delivery_route=delivery_route(),
        )
    assert manager._state.pending_retry is not None
    assert manager._state.pending_retry.prompt == "第二个问题"
    assert manager._state.pending_retry.original_message_id == "busy-2"


def test_codex_retry_rejects_expired_or_different_route_without_side_effects(
    settings: Settings,
) -> None:
    manager, codex_manager, quick_interactions = configured_manager(settings)
    manager._state.session_id = "session-1"
    quick_interactions.is_running.return_value = True
    manager.dispatch(
        message_id="busy-expired",
        prompt="已经过期的任务",
        message_type="text",
        correlation_id=None,
        source_ip="100.64.0.21",
        delivery_route=delivery_route(),
    )
    assert manager._state.pending_retry is not None
    manager._state.pending_retry.expires_at = utc_now() - timedelta(seconds=1)

    result = manager.dispatch(
        message_id="retry-expired",
        prompt="新建 继续执行",
        message_type="text",
        correlation_id=None,
        source_ip="100.64.0.21",
        delivery_route=delivery_route(),
    )

    assert result.message is not None
    assert result.message.startswith(
        "Retry: No task is waiting to be continued. Send the task again."
        "\n\nNo sessions\n\n"
    )
    assert result.message.endswith("Weekly Unavailable")
    codex_manager.create_session.assert_not_called()
    quick_interactions.submit.assert_not_called()


def test_codex_new_retry_cannot_claim_task_from_different_route(
    settings: Settings,
) -> None:
    manager, codex_manager, quick_interactions = configured_manager(settings)
    manager._state.session_id = "session-1"
    quick_interactions.is_running.return_value = True
    manager.dispatch(
        message_id="busy-other-route",
        prompt="私有待继续任务",
        message_type="text",
        correlation_id=None,
        source_ip="100.64.0.21",
        delivery_route=delivery_route(),
    )

    result = manager.dispatch(
        message_id="retry-other-route",
        prompt="新建 重试",
        message_type="text",
        correlation_id=None,
        source_ip="100.64.0.21",
        delivery_route=delivery_route(recipient="another-owner@im.wechat"),
    )

    assert result.message is not None
    assert result.message.startswith(
        "Retry: No task is waiting to be continued. Send the task again."
        "\n\nNo sessions\n\n"
    )
    assert result.message.endswith("Weekly Unavailable")
    codex_manager.create_session.assert_not_called()
    quick_interactions.submit.assert_not_called()
    assert manager._state.pending_retry is not None
    assert manager._state.pending_retry.prompt == "私有待继续任务"


def test_duplicate_codex_new_retry_does_not_create_or_submit_twice(
    settings: Settings,
) -> None:
    manager, codex_manager, quick_interactions = configured_manager(settings)
    manager._state.session_id = "session-1"
    quick_interactions.is_running.return_value = True
    manager.dispatch(
        message_id="busy-for-duplicate",
        prompt="只执行一次",
        message_type="text",
        correlation_id=None,
        source_ip="100.64.0.21",
        delivery_route=delivery_route(),
    )
    codex_manager.create_session.return_value = SimpleNamespace(id="session-2")
    codex_manager.get_session.return_value = CodexSession(
        id="session-2",
        workspace_id="chub",
        workspace_name="Chub",
        cwd="/project",
        permission_mode="full-access",
        status="stopped",
        activity="idle",
    )
    quick_interactions.is_running.return_value = False

    first = manager.dispatch(
        message_id="combined-duplicate",
        prompt="new retry",
        message_type="text",
        correlation_id=None,
        source_ip="100.64.0.21",
        delivery_route=delivery_route(),
    )
    duplicate = manager.dispatch(
        message_id="combined-duplicate",
        prompt="new retry",
        message_type="text",
        correlation_id=None,
        source_ip="100.64.0.21",
        delivery_route=delivery_route(),
    )

    assert "The task was resubmitted." in (first.message or "")
    assert duplicate.message == first.message
    codex_manager.create_session.assert_called_once()
    quick_interactions.submit.assert_called_once()


def test_retry_keeps_pending_task_when_current_session_is_still_busy(
    settings: Settings,
) -> None:
    manager, _codex_manager, quick_interactions = configured_manager(settings)
    manager._state.session_id = "session-1"
    quick_interactions.is_running.return_value = True
    manager.dispatch(
        message_id="busy-still-running",
        prompt="稍后继续的任务",
        message_type="text",
        correlation_id=None,
        source_ip="100.64.0.21",
        delivery_route=delivery_route(),
    )

    result = manager.dispatch(
        message_id="retry-still-running",
        prompt="retry",
        message_type="text",
        correlation_id=None,
        source_ip="100.64.0.21",
        delivery_route=delivery_route(),
    )

    assert result.message is not None
    assert result.message.startswith("Retry: The task was not resubmitted.")
    assert result.message.splitlines()[2] == "Task · 稍后继续的任务"
    assert "Not submitted · The current Session is running." in result.message
    assert result.message.endswith(
        "Retry: Send new retry to continue in a new Session."
    )
    assert manager._state.pending_retry is not None
    assert manager._state.pending_retry.prompt == "稍后继续的任务"
    assert manager._state.pending_retry.claimed_by_message_id is None
    quick_interactions.submit.assert_not_called()
