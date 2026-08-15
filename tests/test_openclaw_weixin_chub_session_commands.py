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


def test_codex_switch_uses_creation_order_and_allows_busy_target(
    settings: Settings,
) -> None:
    manager, codex_manager, quick_interactions = configured_manager(settings)
    manager._state.session_id = "a-current"
    sessions = [
        CodexSession(
            id="c-available",
            workspace_id="chub",
            workspace_name="Chub",
            cwd="/project",
            title="第三项",
            permission_mode="full-access",
            status="stopped",
            activity="idle",
        ),
        CodexSession(
            id="a-current",
            workspace_id="chub",
            workspace_name="Chub",
            cwd="/project",
            title="第一项",
            permission_mode="full-access",
            status="stopped",
            activity="idle",
        ),
        CodexSession(
            id="b-busy",
            workspace_id="chub",
            workspace_name="Chub",
            cwd="/project",
            title="第二项",
            permission_mode="full-access",
            status="stopped",
            activity="working",
            activity_source="quick",
        ),
    ]
    by_id = {session.id: session for session in sessions}
    manager._state.session_slots = [
        WeixinChubModeSessionSlot(slot=1, session_id="a-current"),
        WeixinChubModeSessionSlot(slot=2, session_id="b-busy"),
        WeixinChubModeSessionSlot(slot=3, session_id="c-available"),
    ]
    codex_manager.list_sessions.return_value = sessions
    codex_manager.get_session.side_effect = lambda session_id: by_id[session_id]
    quick_interactions.is_running.side_effect = (
        lambda session_id: session_id == "b-busy"
    )
    manager.codex_account_reader = MagicMock()
    manager.codex_account_reader.read_account_status.return_value = (
        CodexQuotaData(status="unavailable"),
        CodexTokenUsageData(status="unavailable"),
    )

    result = manager.dispatch(
        message_id="codex-switch-next",
        prompt="Session Switch 3",
        message_type="text",
        correlation_id=None,
        source_ip="100.64.0.21",
        delivery_route=delivery_route(),
    )

    assert result.message is not None
    assert result.message.startswith(
        "切换状态：已切换到 Session 3。\n\n"
        "Sessions\n\n"
    )
    assert "S3 · 第三项\n\nAvailable · Current" in result.message
    assert result.message.endswith("Weekly 暂不可用")
    assert manager.session_id() == "c-available"
    quick_interactions.submit.assert_not_called()


@pytest.mark.parametrize(
    "prompt",
    [
        "切换2",
        "切换二",
        " 切换 二。 ",
        "切换会话2",
        "切换会话 二",
        "会话2",
        " 会话 二。 ",
    ],
)
def test_chinese_switch_routes_to_numbered_session(
    settings: Settings,
    prompt: str,
) -> None:
    manager, codex_manager, quick_interactions = configured_manager(settings)
    manager._state.session_id = "session-1"
    sessions = [
        CodexSession(
            id=f"session-{slot}",
            workspace_id="chub",
            workspace_name="Chub",
            cwd="/project",
            title=f"第 {slot} 项",
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
    codex_manager.list_sessions.return_value = sessions
    codex_manager.get_session.return_value = sessions[1]
    manager.codex_account_reader = MagicMock()
    manager.codex_account_reader.read_account_status.return_value = (
        CodexQuotaData(status="unavailable"),
        CodexTokenUsageData(status="unavailable"),
    )

    result = manager.dispatch(
        message_id=f"chinese-switch-{prompt}",
        prompt=prompt,
        message_type="voice",
        correlation_id=None,
        source_ip="100.64.0.21",
        delivery_route=delivery_route(),
    )

    assert result.message is not None
    assert "S2 · 第 2 项\n\nAvailable · Current" in result.message
    assert "Task status:" not in result.message
    assert manager.session_id() == "session-2"
    quick_interactions.submit.assert_not_called()


@pytest.mark.parametrize(
    ("prompt", "task_prompt"),
    [
        ("session switch 2, continue checking logs", "continue checking logs"),
        ("切换2，继续检查日志。", "继续检查日志。"),
        ("切换 2，继续检查正文", "继续检查正文"),
        ("切换会话二 继续处理部署问题", "继续处理部署问题"),
        ("切换会话2：/api/devices", "/api/devices"),
        ("切换2，# 检查标题", "# 检查标题"),
        ("会话二，继续检查告警", "继续检查告警"),
        ("会话 2 继续检查正文", "继续检查正文"),
    ],
)
def test_codex_switch_with_task_switches_and_submits_once(
    settings: Settings,
    prompt: str,
    task_prompt: str,
) -> None:
    manager, codex_manager, quick_interactions = configured_manager(settings)
    manager._state.session_id = "session-1"
    sessions = [
        CodexSession(
            id=f"session-{slot}",
            workspace_id="chub",
            workspace_name="Chub",
            cwd="/project",
            title=f"第 {slot} 项",
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
    codex_manager.list_sessions.return_value = sessions
    codex_manager.get_session.return_value = sessions[1]
    manager.codex_account_reader = MagicMock()
    manager.codex_account_reader.read_account_status.return_value = (
        CodexQuotaData(status="unavailable"),
        CodexTokenUsageData(status="unavailable"),
    )

    first = manager.dispatch(
        message_id=f"switch-with-task-{prompt}",
        prompt=prompt,
        message_type="text",
        correlation_id=None,
        source_ip="100.64.0.21",
        delivery_route=delivery_route(),
    )
    duplicate = manager.dispatch(
        message_id=f"switch-with-task-{prompt}",
        prompt=prompt,
        message_type="text",
        correlation_id=None,
        source_ip="100.64.0.21",
        delivery_route=delivery_route(),
    )

    assert first.disposition == "reply"
    assert first.message is not None
    assert first.message.startswith("切换状态：已切换到 Session 2。")
    assert "任务状态：已提交。" in first.message
    assert duplicate.message == first.message
    assert manager.session_id() == "session-2"
    quick_interactions.submit.assert_called_once()
    assert quick_interactions.submit.call_args.args[:2] == (
        "session-2",
        task_prompt,
    )


def test_codex_switch_without_current_uses_first_visible_session(
    settings: Settings,
) -> None:
    manager, codex_manager, _quick_interactions = configured_manager(settings)
    unavailable = CodexSession(
        id="a-unavailable",
        workspace_id="chub",
        workspace_name="Chub",
        cwd="/project",
        title="不应选择",
        permission_mode="full-access",
        status="error",
        activity="unknown",
        error="private failure",
    )
    available = CodexSession(
        id="b-available",
        workspace_id="chub",
        workspace_name="Chub",
        cwd="/project",
        title="可用会话",
        permission_mode="full-access",
        status="stopped",
        activity="idle",
    )
    codex_manager.list_sessions.return_value = [available, unavailable]
    manager._state.session_slots = [
        WeixinChubModeSessionSlot(slot=1, session_id="b-available")
    ]
    codex_manager.get_session.return_value = available
    manager.codex_account_reader = MagicMock()
    manager.codex_account_reader.read_account_status.return_value = (
        CodexQuotaData(status="unavailable"),
        CodexTokenUsageData(status="unavailable"),
    )

    result = manager.dispatch(
        message_id="codex-switch-first",
        prompt="session switch 1",
        message_type="text",
        correlation_id=None,
        source_ip="100.64.0.21",
        delivery_route=delivery_route(),
    )

    assert result.message is not None
    assert "S1 · 可用会话\n\nAvailable · Current" in result.message
    assert manager.session_id() == "b-available"


@pytest.mark.parametrize(
    "prompt",
    [
        "Session Archive 2。",
        "归档2",
        "归档二",
        "归档 二",
        "归档会话2",
        "归档会话 二",
    ],
)
def test_codex_archive_removes_target_and_clears_current_binding(
    settings: Settings,
    prompt: str,
) -> None:
    manager, codex_manager, quick_interactions = configured_manager(settings)
    sessions = [
        CodexSession(
            id=f"session-{index}",
            codex_session_id=f"native-{index}",
            workspace_id="chub",
            workspace_name="Chub",
            cwd="/project",
            title=f"候选 {index}",
            permission_mode="full-access",
            status="stopped",
            activity="idle",
        )
        for index in (1, 2)
    ]
    by_id = {session.id: session for session in sessions}
    manager._state.session_id = "session-2"
    manager._state.session_slots = [
        WeixinChubModeSessionSlot(slot=1, session_id="session-1"),
        WeixinChubModeSessionSlot(slot=2, session_id="session-2"),
    ]
    remaining_sessions = list(sessions)
    codex_manager.list_sessions.side_effect = lambda: list(remaining_sessions)
    codex_manager.get_session.side_effect = lambda session_id: by_id[session_id]
    manager.session_archiver.side_effect = lambda session_id: remaining_sessions.__setitem__(
        slice(None),
        [session for session in remaining_sessions if session.id != session_id],
    )
    manager.codex_account_reader = MagicMock()
    manager.codex_account_reader.read_account_status.return_value = (
        CodexQuotaData(status="unavailable"),
        CodexTokenUsageData(status="unavailable"),
    )

    with patch(
        "app.services.openclaw_weixin_chub_mode.write_operation"
    ) as write_operation:
        result = manager.dispatch(
            message_id=f"archive-{prompt}",
            prompt=prompt,
            message_type="voice",
            correlation_id=None,
            source_ip="100.64.0.21",
            delivery_route=delivery_route(),
        )

    assert result.message is not None
    assert result.message.startswith(
        "归档状态：Session 2 已归档，当前绑定已清除。\n\n"
        "Sessions\n\n"
    )
    assert result.message.endswith("Weekly 暂不可用")
    assert "S1 · 候选 1\n\nAvailable" in result.message
    assert "候选 2" not in result.message
    assert " · Current" not in result.message
    manager.session_archiver.assert_called_once_with("session-2")
    assert manager.session_id() is None
    assert manager.session_slot_matches(2, "session-2") is False
    quick_interactions.submit.assert_not_called()
    archive_entries = [
        call.kwargs
        for call in write_operation.call_args_list
        if call.kwargs["action"] == "archive_codex_session"
    ]
    assert [entry["status"] for entry in archive_entries] == [
        "requested",
        "started",
        "succeeded",
    ]


def test_duplicate_codex_archive_does_not_archive_twice(settings: Settings) -> None:
    manager, codex_manager, _quick_interactions = configured_manager(settings)
    session = CodexSession(
        id="session-1",
        codex_session_id="native-1",
        workspace_id="chub",
        workspace_name="Chub",
        cwd="/project",
        title="待归档",
        permission_mode="full-access",
        status="stopped",
        activity="idle",
    )
    codex_manager.list_sessions.return_value = [session]
    codex_manager.get_session.return_value = session
    manager._state.session_slots = [
        WeixinChubModeSessionSlot(slot=1, session_id="session-1")
    ]

    first = manager.dispatch(
        message_id="duplicate-archive",
        prompt="归档一",
        message_type="text",
        correlation_id=None,
        source_ip="100.64.0.21",
        delivery_route=delivery_route(),
    )
    duplicate = manager.dispatch(
        message_id="duplicate-archive",
        prompt="归档一",
        message_type="text",
        correlation_id=None,
        source_ip="100.64.0.21",
        delivery_route=delivery_route(),
    )

    assert duplicate == first
    manager.session_archiver.assert_called_once_with("session-1")


def test_codex_archive_status_preserves_freed_slot_for_codex_new(
    settings: Settings,
) -> None:
    manager, codex_manager, _quick_interactions = configured_manager(settings)
    sessions = [
        CodexSession(
            id=f"session-{index}",
            codex_session_id=f"native-{index}",
            workspace_id="chub",
            workspace_name="Chub",
            cwd="/project",
            title=f"候选 {index}",
            permission_mode="full-access",
            status="stopped",
            activity="idle",
        )
        for index in range(1, 11)
    ]
    by_id = {session.id: session for session in sessions}
    manager._state.session_slots = [
        WeixinChubModeSessionSlot(slot=index, session_id=f"session-{index}")
        for index in range(1, 10)
    ]
    remaining_sessions = list(sessions)
    codex_manager.list_sessions.side_effect = lambda: list(remaining_sessions)
    codex_manager.get_session.side_effect = lambda session_id: by_id[session_id]
    manager.session_archiver.side_effect = lambda session_id: remaining_sessions.__setitem__(
        slice(None),
        [session for session in remaining_sessions if session.id != session_id],
    )
    manager.codex_account_reader = MagicMock()
    manager.codex_account_reader.read_account_status.return_value = (
        CodexQuotaData(status="unavailable"),
        CodexTokenUsageData(status="unavailable"),
    )

    archived = manager.dispatch(
        message_id="archive-preserves-slot",
        prompt="归档2",
        message_type="text",
        correlation_id=None,
        source_ip="100.64.0.21",
        delivery_route=delivery_route(),
    )

    assert archived.message is not None
    assert "2. 候选 10" not in archived.message
    assert "另有 1 个" in archived.message
    assert manager.session_slot_matches(2, "session-10") is False

    refreshed = manager.dispatch(
        message_id="status-fills-freed-slot",
        prompt="sync",
        message_type="text",
        correlation_id=None,
        source_ip="100.64.0.21",
        delivery_route=delivery_route(),
    )

    assert refreshed.message is not None
    assert "S2 · 候选 10\n\nAvailable" in refreshed.message
    assert manager.session_slot_matches(2, "session-10") is True


def test_codex_archive_does_not_fill_unassigned_candidate(
    settings: Settings,
) -> None:
    manager, codex_manager, _quick_interactions = configured_manager(settings)
    sessions = [
        CodexSession(
            id=f"session-{index}",
            codex_session_id=f"native-{index}",
            workspace_id="chub",
            workspace_name="Chub",
            cwd="/project",
            title=f"候选 {index}",
            permission_mode="full-access",
            status="stopped",
            activity="idle",
        )
        for index in (1, 2)
    ]
    manager._state.session_slots = [
        WeixinChubModeSessionSlot(slot=1, session_id="session-1")
    ]
    codex_manager.list_sessions.return_value = sessions
    manager.codex_account_reader = MagicMock()
    manager.codex_account_reader.read_account_status.return_value = (
        CodexQuotaData(status="unavailable"),
        CodexTokenUsageData(status="unavailable"),
    )

    archived = manager.dispatch(
        message_id="archive-unassigned-candidate",
        prompt="归档2",
        message_type="text",
        correlation_id=None,
        source_ip="100.64.0.21",
        delivery_route=delivery_route(),
    )

    assert archived.message is not None
    assert archived.message.startswith("归档状态：未归档，编号无效。\n\n")
    assert "另有 1 个" in archived.message
    manager.session_archiver.assert_not_called()
    assert manager.session_slot_matches(2, "session-2") is False

    manager.dispatch(
        message_id="status-fills-archive-candidate",
        prompt="sync",
        message_type="text",
        correlation_id=None,
        source_ip="100.64.0.21",
        delivery_route=delivery_route(),
    )

    assert manager.session_slot_matches(2, "session-2") is True


@pytest.mark.parametrize(
    ("activity", "quick_running", "writer_active"),
    [("working", False, False), ("idle", True, False), ("idle", False, True), ("unknown", False, False)],
)
def test_codex_archive_rejects_session_that_is_not_safely_idle(
    settings: Settings,
    activity: str,
    quick_running: bool,
    writer_active: bool,
) -> None:
    manager, codex_manager, quick_interactions = configured_manager(settings)
    session = CodexSession(
        id="session-1",
        codex_session_id="native-1",
        workspace_id="chub",
        workspace_name="Chub",
        cwd="/project",
        title="使用中",
        permission_mode="full-access",
        status="running" if activity != "unknown" else "stopped",
        activity=activity,
    )
    codex_manager.list_sessions.return_value = [session]
    codex_manager.get_session.return_value = session
    manager._state.session_slots = [
        WeixinChubModeSessionSlot(slot=1, session_id="session-1")
    ]
    quick_interactions.is_running.return_value = quick_running
    codex_manager.has_active_writer.return_value = writer_active

    result = manager.dispatch(
        message_id=f"unsafe-archive-{activity}-{quick_running}-{writer_active}",
        prompt="session archive 1",
        message_type="text",
        correlation_id=None,
        source_ip="100.64.0.21",
        delivery_route=delivery_route(),
    )

    assert result.message is not None
    assert "未归档" in result.message
    manager.session_archiver.assert_not_called()


@pytest.mark.parametrize(
    "prompt",
    [
        "session archive",
        "session archive 0",
        "session archive -1",
        "session archive abc",
        "session archive 1 extra",
        "归档",
        "归档10",
        "归档零",
        "归档会话",
        "归档会话2，继续处理",
    ],
)
def test_codex_archive_invalid_usage_is_not_submitted(
    settings: Settings,
    prompt: str,
) -> None:
    manager, codex_manager, quick_interactions = configured_manager(settings)

    result = manager.dispatch(
        message_id=f"invalid-archive-{prompt}",
        prompt=prompt,
        message_type="text",
        correlation_id=None,
        source_ip="100.64.0.21",
        delivery_route=delivery_route(),
    )

    assert result.message is not None
    assert result.message.startswith("用法：发送 session archive n")
    codex_manager.list_sessions.assert_not_called()
    quick_interactions.submit.assert_not_called()


def test_chinese_archive_business_text_is_submitted_as_normal_task(
    settings: Settings,
) -> None:
    manager, _codex_manager, quick_interactions = configured_manager(settings)

    result = manager.dispatch(
        message_id="archive-business-text",
        prompt="归档日志后再检查",
        message_type="text",
        correlation_id=None,
        source_ip="100.64.0.21",
        delivery_route=delivery_route(),
    )

    assert result.disposition == "handled"
    assert result.message is None
    quick_interactions.submit.assert_called_once()


def test_codex_archive_failure_keeps_slot_and_explains_possible_stop(
    settings: Settings,
) -> None:
    manager, codex_manager, _quick_interactions = configured_manager(settings)
    session = CodexSession(
        id="session-1",
        codex_session_id="native-1",
        workspace_id="chub",
        workspace_name="Chub",
        cwd="/project",
        title="待归档",
        permission_mode="full-access",
        status="stopped",
        activity="idle",
    )
    codex_manager.list_sessions.return_value = [session]
    codex_manager.get_session.return_value = session
    manager._state.session_slots = [
        WeixinChubModeSessionSlot(slot=1, session_id="session-1")
    ]
    manager.session_archiver.side_effect = ApiError(
        503,
        "codex_session_archive_failed",
        "private failure",
    )

    result = manager.dispatch(
        message_id="archive-command-failure",
        prompt="归档1",
        message_type="text",
        correlation_id=None,
        source_ip="100.64.0.21",
        delivery_route=delivery_route(),
    )

    assert result.message == (
        "归档失败；Session 可能已停止，但未从列表移除。"
        "请发送 chub 查看状态后再重试。"
    )
    assert manager.session_slot_matches(1, "session-1") is True


def test_codex_archive_rejects_session_with_pending_retry(
    settings: Settings,
) -> None:
    manager, codex_manager, _quick_interactions = configured_manager(settings)
    session = CodexSession(
        id="session-1",
        codex_session_id="native-1",
        workspace_id="chub",
        workspace_name="Chub",
        cwd="/project",
        title="待续提",
        permission_mode="full-access",
        status="stopped",
        activity="idle",
    )
    codex_manager.list_sessions.return_value = [session]
    codex_manager.get_session.return_value = session
    manager._state.session_slots = [
        WeixinChubModeSessionSlot(slot=1, session_id="session-1")
    ]
    now = utc_now()
    manager._state.pending_retry = WeixinChubModePendingRetry(
        original_message_id="original-message",
        prompt="继续处理任务",
        delivery_route_fingerprint="a" * 64,
        created_at=now,
        expires_at=now + timedelta(minutes=10),
        session_id="session-1",
    )

    result = manager.dispatch(
        message_id="archive-with-pending-retry",
        prompt="归档1",
        message_type="text",
        correlation_id=None,
        source_ip="100.64.0.21",
        delivery_route=delivery_route(),
    )

    assert result.message.startswith(
        "归档状态：未归档，该 Session 关联一条待继续执行的任务。\n\n"
    )
    assert "Sessions\n\nS1 · 待续提\n\nAvailable" in result.message
    manager.session_archiver.assert_not_called()


def test_codex_archive_state_sync_failure_reports_partial_success(
    settings: Settings,
) -> None:
    manager, codex_manager, _quick_interactions = configured_manager(settings)
    session = CodexSession(
        id="session-1",
        codex_session_id="native-1",
        workspace_id="chub",
        workspace_name="Chub",
        cwd="/project",
        title="待归档",
        permission_mode="full-access",
        status="stopped",
        activity="idle",
    )
    codex_manager.list_sessions.return_value = [session]
    codex_manager.get_session.return_value = session
    manager._state.session_slots = [
        WeixinChubModeSessionSlot(slot=1, session_id="session-1")
    ]
    original_write = manager._write_state
    write_count = 0

    def fail_cleanup_write(state) -> None:
        nonlocal write_count
        write_count += 1
        if write_count == 3:
            raise OSError("write failed")
        original_write(state)

    manager._write_state = fail_cleanup_write

    result = manager.dispatch(
        message_id="archive-state-sync-failure",
        prompt="归档1",
        message_type="text",
        correlation_id=None,
        source_ip="100.64.0.21",
        delivery_route=delivery_route(),
    )

    assert result.message == (
        "Session 已归档，但 Chub 未能同步列表状态。"
        "请稍后发送 chub 查看状态。"
    )
    manager.session_archiver.assert_called_once_with("session-1")


def test_codex_switch_number_uses_fresh_visible_list(settings: Settings) -> None:
    manager, codex_manager, _quick_interactions = configured_manager(settings)
    sessions = [
        CodexSession(
            id=f"session-{index}",
            workspace_id="chub",
            workspace_name="Chub",
            cwd="/project",
            title=f"候选 {index}",
            permission_mode="full-access",
            status="stopped",
            activity="idle",
        )
        for index in range(1, 4)
    ]
    by_id = {session.id: session for session in sessions}
    manager._state.session_slots = [
        WeixinChubModeSessionSlot(slot=index, session_id=f"session-{index}")
        for index in range(1, 4)
    ]
    codex_manager.list_sessions.return_value = list(reversed(sessions))
    codex_manager.get_session.side_effect = lambda session_id: by_id[session_id]
    manager.codex_account_reader = MagicMock()
    manager.codex_account_reader.read_account_status.return_value = (
        CodexQuotaData(status="unavailable"),
        CodexTokenUsageData(status="unavailable"),
    )

    result = manager.dispatch(
        message_id="codex-switch-number",
        prompt=" session   switch   2 ",
        message_type="text",
        correlation_id=None,
        source_ip="100.64.0.21",
        delivery_route=delivery_route(),
    )

    assert result.message is not None
    assert "S2 · 候选 2\n\nAvailable · Current" in result.message
    assert manager.session_id() == "session-2"


def test_codex_switch_uses_one_deadline_and_reuses_session_scan(
    settings: Settings,
) -> None:
    manager, codex_manager, _quick_interactions = configured_manager(settings)
    manager._state.session_id = "session-1"
    sessions = [
        CodexSession(
            id=f"session-{index}",
            workspace_id="chub",
            workspace_name="Chub",
            cwd="/project",
            title=f"候选 {index}",
            permission_mode="full-access",
            status="stopped",
            activity="idle",
        )
        for index in (1, 2)
    ]
    by_id = {session.id: session for session in sessions}
    manager._state.session_slots = [
        WeixinChubModeSessionSlot(slot=index, session_id=f"session-{index}")
        for index in (1, 2)
    ]
    codex_manager.list_sessions.return_value = sessions
    codex_manager.get_session.side_effect = lambda session_id: by_id[session_id]
    account_release = threading.Event()
    manager.codex_account_reader = MagicMock()

    def slow_account(*, force: bool):
        assert force is True
        account_release.wait(1)
        return CodexQuotaData(status="unavailable"), CodexTokenUsageData(
            status="unavailable"
        )

    manager.codex_account_reader.read_account_status.side_effect = slow_account

    try:
        with patch(
            "app.services.openclaw_weixin_chub_mode.CODEX_STATUS_TIMEOUT_SECONDS",
            0.01,
        ):
            result = manager.dispatch(
                message_id="codex-switch-bounded",
                prompt="session switch 2",
                message_type="text",
                correlation_id=None,
                source_ip="100.64.0.21",
                delivery_route=delivery_route(),
            )
    finally:
        account_release.set()

    assert result.message is not None
    assert result.message.startswith(
        "切换状态：已切换到 Session 2。\n\n"
        "Sessions\n\n"
    )
    assert result.message.endswith("Codex 用量查询失败，请稍后重试。")
    assert "S2 · 候选 2\n\nAvailable · Current" in result.message
    assert manager.session_id() == "session-2"
    codex_manager.list_sessions.assert_called_once()


def test_codex_switch_out_of_range_returns_fresh_list_without_changing_binding(
    settings: Settings,
) -> None:
    manager, codex_manager, _quick_interactions = configured_manager(settings)
    manager._state.session_id = "session-1"
    session = CodexSession(
        id="session-1",
        workspace_id="chub",
        workspace_name="Chub",
        cwd="/project",
        title="当前会话",
        permission_mode="full-access",
        status="stopped",
        activity="idle",
    )
    codex_manager.list_sessions.return_value = [session]

    result = manager.dispatch(
        message_id="codex-switch-out-of-range",
        prompt="session switch 2",
        message_type="text",
        correlation_id=None,
        source_ip="100.64.0.21",
        delivery_route=delivery_route(),
    )

    assert result.message is not None
    assert result.message.startswith("切换状态：未切换，编号无效。\n\n")
    assert "Sessions" in result.message
    assert "S1 · 当前会话\n\nAvailable · Current" in result.message
    assert manager.session_id() == "session-1"
    codex_manager.get_session.assert_not_called()


def test_codex_switch_does_not_fill_unassigned_candidate(
    settings: Settings,
) -> None:
    manager, codex_manager, _quick_interactions = configured_manager(settings)
    manager._state.session_id = "session-1"
    sessions = [
        CodexSession(
            id=f"session-{index}",
            workspace_id="chub",
            workspace_name="Chub",
            cwd="/project",
            title=f"候选 {index}",
            permission_mode="full-access",
            status="stopped",
            activity="idle",
        )
        for index in (1, 2)
    ]
    manager._state.session_slots = [
        WeixinChubModeSessionSlot(slot=1, session_id="session-1")
    ]
    codex_manager.list_sessions.return_value = sessions
    manager.codex_account_reader = MagicMock()
    manager.codex_account_reader.read_account_status.return_value = (
        CodexQuotaData(status="unavailable"),
        CodexTokenUsageData(status="unavailable"),
    )

    switched = manager.dispatch(
        message_id="switch-unassigned-candidate",
        prompt="切换2",
        message_type="text",
        correlation_id=None,
        source_ip="100.64.0.21",
        delivery_route=delivery_route(),
    )

    assert switched.message is not None
    assert switched.message.startswith(
        "切换状态：未切换，编号无效。"
        "另有未登记的可用 Session，请先发送 sync、同步状态或状态同步后再切换。\n\n"
    )
    assert "另有 1 个" in switched.message
    assert manager.session_id() == "session-1"
    assert manager.session_slot_matches(2, "session-2") is False

    manager.dispatch(
        message_id="status-fills-switch-candidate",
        prompt="sync",
        message_type="text",
        correlation_id=None,
        source_ip="100.64.0.21",
        delivery_route=delivery_route(),
    )

    assert manager.session_slot_matches(2, "session-2") is True


@pytest.mark.parametrize(
    "prompt",
    [
        "session switch 0",
        "session switch -1",
        "session switch abc",
        "切换10",
        "切换-1",
        "切换＋2",
        "切换０",
        "切换零",
        "切换十",
        "切换",
        "会话10",
        "会话零",
        "会话",
    ],
)
def test_codex_switch_invalid_usage_is_not_submitted(
    settings: Settings,
    prompt: str,
) -> None:
    manager, codex_manager, quick_interactions = configured_manager(settings)

    result = manager.dispatch(
        message_id=f"invalid-{prompt}",
        prompt=prompt,
        message_type="text",
        correlation_id=None,
        source_ip="100.64.0.21",
        delivery_route=delivery_route(),
    )

    assert result.message is not None
    assert result.message.startswith("用法：发送 session switch n")
    codex_manager.list_sessions.assert_not_called()
    quick_interactions.submit.assert_not_called()


@pytest.mark.parametrize(
    "prompt",
    [
        "切换网络后再检查",
        "切换二再执行",
        "切换到备用节点",
        "会话设置需要检查",
        "会话二再执行",
    ],
)
def test_chinese_switch_business_text_is_submitted_as_normal_task(
    settings: Settings,
    prompt: str,
) -> None:
    manager, codex_manager, quick_interactions = configured_manager(settings)

    result = manager.dispatch(
        message_id=f"switch-business-text-{prompt}",
        prompt=prompt,
        message_type="text",
        correlation_id=None,
        source_ip="100.64.0.21",
        delivery_route=delivery_route(),
    )

    assert result.disposition == "handled"
    assert result.message is None
    quick_interactions.submit.assert_called_once()
    assert quick_interactions.submit.call_args.args[1] == prompt
    assert manager.codex_account_reader is None


def test_codex_switch_rejects_oversized_numeric_index(settings: Settings) -> None:
    manager, codex_manager, quick_interactions = configured_manager(settings)

    result = manager.dispatch(
        message_id="invalid-large-switch-index",
        prompt="session switch " + ("9" * 5_000),
        message_type="text",
        correlation_id=None,
        source_ip="100.64.0.21",
        delivery_route=delivery_route(),
    )

    assert result.message is not None
    assert result.message.startswith("用法：发送 session switch n")
    codex_manager.list_sessions.assert_not_called()
    quick_interactions.submit.assert_not_called()


def test_codex_switch_write_failure_keeps_previous_binding(
    settings: Settings,
) -> None:
    manager, codex_manager, _quick_interactions = configured_manager(settings)
    manager._state.session_id = "session-1"
    sessions = [
        CodexSession(
            id=f"session-{index}",
            workspace_id="chub",
            workspace_name="Chub",
            cwd="/project",
            title=f"候选 {index}",
            permission_mode="full-access",
            status="stopped",
            activity="idle",
        )
        for index in (1, 2)
    ]
    by_id = {session.id: session for session in sessions}
    manager._state.session_slots = [
        WeixinChubModeSessionSlot(slot=index, session_id=f"session-{index}")
        for index in range(1, 4)
    ]
    codex_manager.list_sessions.return_value = sessions
    codex_manager.get_session.side_effect = lambda session_id: by_id[session_id]
    original_write = manager._write_state
    write_count = 0

    def fail_switch_write(state) -> None:
        nonlocal write_count
        write_count += 1
        if write_count == 2:
            raise OSError("write failed")
        original_write(state)

    manager._write_state = fail_switch_write

    result = manager.dispatch(
        message_id="codex-switch-write-failure",
        prompt="session switch 2",
        message_type="text",
        correlation_id=None,
        source_ip="100.64.0.21",
        delivery_route=delivery_route(),
    )

    assert result.message == "任务提交失败：Chub 当前状态不可用，请稍后重试。"
    assert manager.session_id() == "session-1"
    persisted = json.loads(
        settings.openclaw.weixin_chub_mode.state_file.read_text(encoding="utf-8")
    )
    assert persisted["session_id"] == "session-1"


def test_duplicate_codex_switch_does_not_switch_twice(settings: Settings) -> None:
    manager, codex_manager, _quick_interactions = configured_manager(settings)
    manager._state.session_id = "session-1"
    sessions = [
        CodexSession(
            id=f"session-{index}",
            workspace_id="chub",
            workspace_name="Chub",
            cwd="/project",
            title=f"候选 {index}",
            permission_mode="full-access",
            status="stopped",
            activity="idle",
        )
        for index in range(1, 4)
    ]
    by_id = {session.id: session for session in sessions}
    manager._state.session_slots = [
        WeixinChubModeSessionSlot(slot=index, session_id=f"session-{index}")
        for index in range(1, 4)
    ]
    codex_manager.list_sessions.return_value = sessions
    codex_manager.get_session.side_effect = lambda session_id: by_id[session_id]
    manager.codex_account_reader = MagicMock()
    manager.codex_account_reader.read_account_status.return_value = (
        CodexQuotaData(status="unavailable"),
        CodexTokenUsageData(status="unavailable"),
    )

    first = manager.dispatch(
        message_id="duplicate-switch",
        prompt="session switch 3",
        message_type="text",
        correlation_id=None,
        source_ip="100.64.0.21",
        delivery_route=delivery_route(),
    )
    duplicate = manager.dispatch(
        message_id="duplicate-switch",
        prompt="session switch 3",
        message_type="text",
        correlation_id=None,
        source_ip="100.64.0.21",
        delivery_route=delivery_route(),
    )

    assert duplicate == first
    assert manager.session_id() == "session-3"
    codex_manager.list_sessions.assert_called_once()
    manager.codex_account_reader.read_account_status.assert_called_once_with(force=True)
