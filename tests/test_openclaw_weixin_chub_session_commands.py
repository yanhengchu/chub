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
    submitted_task_message,
)


def test_codex_switch_uses_creation_order_and_allows_busy_target(
    settings: Settings,
) -> None:
    manager, codex_manager, quick_interactions = configured_manager(settings)
    manager._state.session_id = "a-current"
    sessions = [
        CodexSession(
            session_mode="quick",
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
            session_mode="quick",
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
            session_mode="quick",
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
        prompt="Switch 3",
        message_type="text",
        correlation_id=None,
        source_ip="100.64.0.21",
        delivery_route=delivery_route(),
    )

    assert result.message is not None
    assert result.message.startswith(
        "Switch: Session 3 selected.\n\n"
        "Sessions\n\n"
    )
    assert "▶ S3 · 第三项" in result.message
    assert result.message.endswith("Weekly Unavailable")
    assert manager.session_id() == "c-available"
    quick_interactions.submit.assert_not_called()


@pytest.mark.parametrize(
    "prompt",
    [
        "切换 2",
        " 切换 2。 ",
        "切换二",
        "切换 二",
        "S2",
        "s2",
        "会话 2",
        "会话二",
        "会话 S2",
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
            session_mode="quick",
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
    assert "▶ S2 · 第 2 项" in result.message
    assert "Task status:" not in result.message
    assert manager.session_id() == "session-2"
    quick_interactions.submit.assert_not_called()


@pytest.mark.parametrize(
    ("prompt", "task_prompt"),
    [
        ("switch 2 continue checking logs", "continue checking logs"),
        ("S2 continue checking logs", "continue checking logs"),
        ("切换 2 继续检查正文", "继续检查正文"),
        ("切换S2，继续检查正文", "继续检查正文"),
        ("切换2继续检查正文", "继续检查正文"),
        ("切换二继续检查正文", "继续检查正文"),
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
            session_mode="quick",
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
    assert first.message.startswith("Switch: Session 2 selected.")
    assert "\n\nSessions\n\n" in first.message
    assert "S1 · 第 1 项" in first.message
    assert f"▶ S2 · 第 2 项\n\nTask · {task_prompt}" in first.message
    assert "Weekly" not in first.message
    assert duplicate.message == first.message
    assert manager.session_id() == "session-2"
    quick_interactions.submit.assert_called_once()
    assert quick_interactions.submit.call_args.args[:2] == (
        "session-2",
        task_prompt,
    )


def test_codex_switch_task_uses_enabled_text_optimization(
    settings: Settings,
) -> None:
    manager, codex_manager, quick_interactions = configured_manager(settings)
    manager.translation_manager = MagicMock()
    manager.translation_manager.enabled.return_value = True
    manager.translation_manager.has_active_target.return_value = False
    manager.translation_manager.enqueue.return_value = True
    manager._state.session_id = "session-1"
    sessions = [
        CodexSession(
            session_mode="quick",
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

    first = manager.dispatch(
        message_id="switch-optimized-task",
        prompt="切换 2 检查服务状态",
        message_type="text",
        correlation_id=None,
        source_ip="100.64.0.21",
        delivery_route=delivery_route(),
    )
    duplicate = manager.dispatch(
        message_id="switch-optimized-task",
        prompt="切换 2 重复正文不得执行",
        message_type="text",
        correlation_id=None,
        source_ip="100.64.0.21",
        delivery_route=delivery_route(),
    )

    assert first.message is not None
    assert first.message.startswith(
        "Switch: Session 2 selected. Optimizing · Preparing to submit."
    )
    assert "▶ S2 · 第 2 项\n\nTask · 检查服务状态" in first.message
    assert duplicate == first
    assert manager.session_id() == "session-2"
    quick_interactions.submit.assert_not_called()
    manager.translation_manager.enqueue.assert_called_once_with(
        message_id=manager._command_task_message_id("switch-optimized-task"),
        original="检查服务状态",
        route=delivery_route(),
        operation_id=manager.translation_manager.enqueue.call_args.kwargs["operation_id"],
        source_ip="100.64.0.21",
        target_session_id="session-2",
    )


def test_codex_switch_long_body_submits_directly(
    settings: Settings,
) -> None:
    settings.openclaw.weixin_chub_mode.translation_preprocess_max_input_chars = 10
    manager, codex_manager, quick_interactions = configured_manager(settings)
    manager.translation_manager = MagicMock()
    manager.translation_manager.processing_mode.return_value = "confirm"
    manager.translation_manager.has_active_target.return_value = False
    manager._state.session_id = "session-1"
    sessions = [
        CodexSession(
            session_mode="quick",
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
    long_prompt = "这是超过处理阈值的切换正文"

    result = manager.dispatch(
        message_id="switch-long-direct",
        prompt=f"切换 2 {long_prompt}",
        message_type="text",
        correlation_id=None,
        source_ip="100.64.0.21",
        delivery_route=delivery_route(),
    )

    assert result.message is not None
    assert result.message.startswith("Switch: Session 2 selected. Task submitted.")
    quick_interactions.submit.assert_called_once()
    assert quick_interactions.submit.call_args.args[1] == long_prompt
    manager.translation_manager.enqueue.assert_not_called()


def test_codex_switch_task_keeps_selection_when_optimization_cannot_queue(
    settings: Settings,
) -> None:
    manager, codex_manager, quick_interactions = configured_manager(settings)
    manager.translation_manager = MagicMock()
    manager.translation_manager.enabled.return_value = True
    manager.translation_manager.has_active_target.return_value = False
    manager.translation_manager.enqueue.return_value = False
    manager._state.session_id = "session-1"
    sessions = [
        CodexSession(
            session_mode="quick",
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

    result = manager.dispatch(
        message_id="switch-optimization-failed",
        prompt="S2 检查服务状态",
        message_type="text",
        correlation_id=None,
        source_ip="100.64.0.21",
        delivery_route=delivery_route(),
    )

    assert result.message is not None
    assert result.message.startswith(
        "Switch: Session 2 selected, but the task was not submitted."
    )
    assert "Task · 检查服务状态" in result.message
    assert manager.session_id() == "session-2"
    quick_interactions.submit.assert_not_called()
    manager.translation_manager.enqueue.assert_called_once()


def test_codex_switch_with_task_failure_shows_task_summary(
    settings: Settings,
) -> None:
    manager, codex_manager, quick_interactions = configured_manager(settings)
    manager._state.session_id = "session-1"
    sessions = [
        CodexSession(
            session_mode="quick",
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
    quick_interactions.submit.side_effect = ApiError(
        503,
        "quick_worker_unavailable",
        "private detail",
    )

    result = manager.dispatch(
        message_id="switch-with-failed-task",
        prompt="切换 2 继续检查日志",
        message_type="text",
        correlation_id=None,
        source_ip="100.64.0.21",
        delivery_route=delivery_route(),
    )
    duplicate = manager.dispatch(
        message_id="switch-with-failed-task",
        prompt="切换 2 继续检查日志",
        message_type="text",
        correlation_id=None,
        source_ip="100.64.0.21",
        delivery_route=delivery_route(),
    )

    assert result.message is not None
    assert "the task was not submitted" in result.message
    assert result.message.splitlines()[2] == "▶ S2 · 第 2 项"
    assert result.message.splitlines()[4] == "Task · 继续检查日志"
    assert "Sessions" not in result.message
    assert duplicate == result
    quick_interactions.submit.assert_called_once()


def test_codex_switch_with_task_busy_target_uses_target_without_current_marker(
    settings: Settings,
) -> None:
    manager, codex_manager, quick_interactions = configured_manager(settings)
    manager._state.session_id = "session-1"
    sessions = [
        CodexSession(
            session_mode="quick",
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
    quick_interactions.is_running.side_effect = (
        lambda session_id: session_id == "session-2"
    )

    result = manager.dispatch(
        message_id="switch-with-busy-target-task",
        prompt="切换 2 继续检查日志",
        message_type="text",
        correlation_id=None,
        source_ip="100.64.0.21",
        delivery_route=delivery_route(),
    )

    assert result.message == (
        "Switch: Not completed because the target Session is running. "
        "The task was not submitted.\n\n"
        "S2 · 第 2 项\n\n"
        "Task · 继续检查日志"
    )
    assert manager.session_id() == "session-1"
    quick_interactions.submit.assert_not_called()


def test_codex_switch_without_current_uses_first_visible_session(
    settings: Settings,
) -> None:
    manager, codex_manager, _quick_interactions = configured_manager(settings)
    unavailable = CodexSession(
        session_mode="quick",
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
        session_mode="quick",
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
        prompt="switch 1",
        message_type="text",
        correlation_id=None,
        source_ip="100.64.0.21",
        delivery_route=delivery_route(),
    )

    assert result.message is not None
    assert "▶ S1 · 可用会话" in result.message
    assert manager.session_id() == "b-available"


@pytest.mark.parametrize(
    "prompt",
    [
        "Archive 2。",
        "Archive2。",
        "ArchiveS2。",
        "Archive S2。",
        "归档 2",
        "归档2",
        "归档S2",
        "归档二",
        "归档 S2",
    ],
)
def test_codex_archive_removes_target_and_clears_current_binding(
    settings: Settings,
    prompt: str,
) -> None:
    manager, codex_manager, quick_interactions = configured_manager(settings)
    sessions = [
        CodexSession(
            session_mode="quick",
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
        "Archive: Session 2 archived. The current selection was cleared.\n\n"
        "Sessions\n\n"
    )
    assert result.message.endswith("Weekly Unavailable")
    assert "S1 · 候选 1" in result.message
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


def test_codex_archive_allows_chub_only_session_without_native_binding(
    settings: Settings,
) -> None:
    manager, codex_manager, _quick_interactions = configured_manager(settings)
    session = CodexSession(
        session_mode="quick",
        id="session-1",
        workspace_id="chub",
        workspace_name="Chub",
        cwd="/project",
        title="尚未启动",
        permission_mode="full-access",
        status="stopped",
        activity="idle",
    )
    remaining_sessions = [session]
    codex_manager.list_sessions.side_effect = lambda: list(remaining_sessions)
    codex_manager.get_session.return_value = session
    manager._state.session_slots = [
        WeixinChubModeSessionSlot(slot=1, session_id="session-1")
    ]
    manager.session_archiver.side_effect = lambda session_id: remaining_sessions.clear()

    result = manager.dispatch(
        message_id="archive-chub-only",
        prompt="归档 1",
        message_type="text",
        correlation_id=None,
        source_ip="100.64.0.21",
        delivery_route=delivery_route(),
    )

    assert result.message is not None
    assert result.message.startswith("Archive: Session 1 archived.")
    manager.session_archiver.assert_called_once_with("session-1")
    assert manager.session_slot_matches(1, "session-1") is False


def test_duplicate_codex_archive_does_not_archive_twice(settings: Settings) -> None:
    manager, codex_manager, _quick_interactions = configured_manager(settings)
    session = CodexSession(
        session_mode="quick",
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
        prompt="归档 1",
        message_type="text",
        correlation_id=None,
        source_ip="100.64.0.21",
        delivery_route=delivery_route(),
    )
    duplicate = manager.dispatch(
        message_id="duplicate-archive",
        prompt="归档 1",
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
            session_mode="quick",
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
        prompt="归档 2",
        message_type="text",
        correlation_id=None,
        source_ip="100.64.0.21",
        delivery_route=delivery_route(),
    )

    assert archived.message is not None
    assert "2. 候选 10" not in archived.message
    assert "1 more Sessions" in archived.message
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
    assert "S2 · 候选 10" in refreshed.message
    assert manager.session_slot_matches(2, "session-10") is True


def test_codex_archive_does_not_fill_unassigned_candidate(
    settings: Settings,
) -> None:
    manager, codex_manager, _quick_interactions = configured_manager(settings)
    sessions = [
        CodexSession(
            session_mode="quick",
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
        prompt="归档 2",
        message_type="text",
        correlation_id=None,
        source_ip="100.64.0.21",
        delivery_route=delivery_route(),
    )

    assert archived.message is not None
    assert archived.message.startswith(
        "Archive: Not completed because the Session number is invalid.\n\n"
    )
    assert "1 more Sessions" in archived.message
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
    [("working", False, False), ("idle", True, False), ("idle", False, True)],
)
def test_codex_archive_rejects_session_that_is_not_safely_idle(
    settings: Settings,
    activity: str,
    quick_running: bool,
    writer_active: bool,
) -> None:
    manager, codex_manager, quick_interactions = configured_manager(settings)
    session = CodexSession(
        session_mode="quick",
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
    if activity == "working" or quick_running:
        manager.session_archiver.side_effect = ApiError(
            409,
            "codex_session_in_progress",
            "Session 当前正在执行，请等待任务结束后再归档。",
        )
    elif writer_active:
        manager.session_archiver.side_effect = ApiError(
            409,
            "codex_session_writer_active",
            "This is open in another app, close it there to continue here.",
        )
    else:
        raise AssertionError("unexpected archive gate test case")

    result = manager.dispatch(
        message_id=f"unsafe-archive-{activity}-{quick_running}-{writer_active}",
        prompt="archive 1",
        message_type="text",
        correlation_id=None,
        source_ip="100.64.0.21",
        delivery_route=delivery_route(),
    )

    assert result.message is not None
    if writer_active:
        assert (
            "This is open in another app, close it there to continue here."
            in result.message
        )
    else:
        assert "Archive: Not completed" in result.message
    manager.session_archiver.assert_called_once_with("session-1")


@pytest.mark.parametrize(
    "prompt",
    [
        "archive",
        "archive 0",
        "archive -1",
        "archive abc",
        "archive 1 extra",
        "归档",
        "归档 10",
        "归档 2 继续处理",
    ],
)
def test_codex_archive_invalid_usage_is_submitted_as_normal_task(
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
    assert result.message == submitted_task_message(settings, prompt)
    quick_interactions.submit.assert_called_once()


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

    assert result.disposition == "reply"
    assert result.message == submitted_task_message(settings, "归档日志后再检查")
    quick_interactions.submit.assert_called_once()


def test_codex_archive_failure_keeps_slot_and_explains_possible_stop(
    settings: Settings,
) -> None:
    manager, codex_manager, _quick_interactions = configured_manager(settings)
    session = CodexSession(
        session_mode="quick",
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
        prompt="归档 1",
        message_type="text",
        correlation_id=None,
        source_ip="100.64.0.21",
        delivery_route=delivery_route(),
    )

    assert result.message is not None
    assert result.message.startswith(
        "Archive: Failed. The Session may have stopped but remains listed."
        " Send chub before trying again.\n\nSessions\n\n"
    )
    assert result.message.endswith("Weekly Unavailable")
    assert manager.session_slot_matches(1, "session-1") is True


def test_codex_archive_rejects_session_with_pending_retry(
    settings: Settings,
) -> None:
    manager, codex_manager, _quick_interactions = configured_manager(settings)
    session = CodexSession(
        session_mode="quick",
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
        prompt="归档 1",
        message_type="text",
        correlation_id=None,
        source_ip="100.64.0.21",
        delivery_route=delivery_route(),
    )

    assert result.message.startswith(
        "Archive: Not completed because the Session has a pending retry task.\n\n"
    )
    assert "Sessions\n\nS1 · 待续提" in result.message
    manager.session_archiver.assert_not_called()


def test_codex_archive_state_sync_failure_reports_partial_success(
    settings: Settings,
) -> None:
    manager, codex_manager, _quick_interactions = configured_manager(settings)
    session = CodexSession(
        session_mode="quick",
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
        prompt="归档 1",
        message_type="text",
        correlation_id=None,
        source_ip="100.64.0.21",
        delivery_route=delivery_route(),
    )

    assert result.message is not None
    assert result.message.startswith(
        "Archive: Completed, but Chub could not synchronize the Session list."
        " Send chub later.\n\nSessions\n\n"
    )
    assert result.message.endswith("Weekly Unavailable")
    manager.session_archiver.assert_called_once_with("session-1")


def test_codex_switch_number_uses_fresh_visible_list(settings: Settings) -> None:
    manager, codex_manager, _quick_interactions = configured_manager(settings)
    sessions = [
        CodexSession(
            session_mode="quick",
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
        prompt=" switch 2 ",
        message_type="text",
        correlation_id=None,
        source_ip="100.64.0.21",
        delivery_route=delivery_route(),
    )

    assert result.message is not None
    assert "▶ S2 · 候选 2" in result.message
    assert manager.session_id() == "session-2"


def test_codex_switch_uses_one_deadline_and_reuses_session_scan(
    settings: Settings,
) -> None:
    manager, codex_manager, _quick_interactions = configured_manager(settings)
    manager._state.session_id = "session-1"
    sessions = [
        CodexSession(
            session_mode="quick",
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
                prompt="switch 2",
                message_type="text",
                correlation_id=None,
                source_ip="100.64.0.21",
                delivery_route=delivery_route(),
            )
    finally:
        account_release.set()

    assert result.message is not None
    assert result.message.startswith(
        "Switch: Session 2 selected.\n\n"
        "Sessions\n\n"
    )
    assert result.message.endswith("Weekly Unavailable")
    assert "▶ S2 · 候选 2" in result.message
    assert manager.session_id() == "session-2"
    codex_manager.list_sessions.assert_called_once()


def test_codex_switch_out_of_range_returns_fresh_list_without_changing_binding(
    settings: Settings,
) -> None:
    manager, codex_manager, _quick_interactions = configured_manager(settings)
    manager._state.session_id = "session-1"
    session = CodexSession(
        session_mode="quick",
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
        prompt="switch 2",
        message_type="text",
        correlation_id=None,
        source_ip="100.64.0.21",
        delivery_route=delivery_route(),
    )

    assert result.message is not None
    assert result.message.startswith(
        "Switch: Not completed because the Session number is invalid.\n\n"
    )
    assert "Sessions" in result.message
    assert "▶ S1 · 当前会话" in result.message
    assert manager.session_id() == "session-1"
    codex_manager.get_session.assert_not_called()


def test_codex_switch_does_not_fill_unassigned_candidate(
    settings: Settings,
) -> None:
    manager, codex_manager, _quick_interactions = configured_manager(settings)
    manager._state.session_id = "session-1"
    sessions = [
        CodexSession(
            session_mode="quick",
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
        prompt="切换 2",
        message_type="text",
        correlation_id=None,
        source_ip="100.64.0.21",
        delivery_route=delivery_route(),
    )

    assert switched.message is not None
    assert switched.message.startswith(
        "Switch: Not completed because the Session number is invalid. "
        "Unregistered Sessions are available. Send sync before switching.\n\n"
    )
    assert "1 more Sessions" in switched.message
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
        "switch 0",
        "switch -1",
        "switch abc",
        "S0",
        "S10",
        "SABC",
        "S",
        "切换 10",
        "切换S10正文",
        "切换 -1",
        "切换",
        "会话 10",
        "会话",
    ],
)
def test_codex_switch_invalid_usage_is_submitted_as_normal_task(
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
    assert result.message == submitted_task_message(settings, prompt)
    quick_interactions.submit.assert_called_once()


@pytest.mark.parametrize(
    "prompt",
    [
        "切换网络后再检查",
        "切换到备用节点",
        "会话设置需要检查",
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

    assert result.disposition == "reply"
    assert result.message == submitted_task_message(settings, prompt)
    quick_interactions.submit.assert_called_once()
    assert quick_interactions.submit.call_args.args[1] == prompt
    assert manager.codex_account_reader is None


def test_codex_switch_rejects_oversized_numeric_index(settings: Settings) -> None:
    manager, codex_manager, quick_interactions = configured_manager(settings)

    result = manager.dispatch(
        message_id="invalid-large-switch-index",
        prompt="switch " + ("9" * 5_000),
        message_type="text",
        correlation_id=None,
        source_ip="100.64.0.21",
        delivery_route=delivery_route(),
    )

    assert result.message is not None
    assert result.message == submitted_task_message(
        settings,
        "switch " + ("9" * 5_000),
    )
    quick_interactions.submit.assert_called_once()


def test_codex_switch_write_failure_keeps_previous_binding(
    settings: Settings,
) -> None:
    manager, codex_manager, _quick_interactions = configured_manager(settings)
    manager._state.session_id = "session-1"
    sessions = [
        CodexSession(
            session_mode="quick",
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
        prompt="switch 2",
        message_type="text",
        correlation_id=None,
        source_ip="100.64.0.21",
        delivery_route=delivery_route(),
    )

    assert result.message == (
        "Request: Failed because Chub state is unavailable. Try again later.\n\n"
        "Sessions\n\nUnavailable\n\nWeekly Unavailable"
    )
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
            session_mode="quick",
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
        prompt="switch 3",
        message_type="text",
        correlation_id=None,
        source_ip="100.64.0.21",
        delivery_route=delivery_route(),
    )
    duplicate = manager.dispatch(
        message_id="duplicate-switch",
        prompt="switch 3",
        message_type="text",
        correlation_id=None,
        source_ip="100.64.0.21",
        delivery_route=delivery_route(),
    )

    assert duplicate == first
    assert manager.session_id() == "session-3"
    codex_manager.list_sessions.assert_called_once()
    manager.codex_account_reader.read_account_status.assert_called_once_with(force=True)


def test_codex_delete_removes_target_and_clears_current_binding(
    settings: Settings,
) -> None:
    manager, codex_manager, _quick_interactions = configured_manager(settings)
    session = CodexSession(
        session_mode="quick",
        id="session-1",
        codex_session_id="native-1",
        workspace_id="chub",
        workspace_name="Chub",
        cwd="/project",
        title="待删除",
        permission_mode="full-access",
        status="stopped",
        activity="idle",
    )
    remaining_sessions = [session]
    manager._state.session_id = "session-1"
    manager._state.session_slots = [
        WeixinChubModeSessionSlot(slot=1, session_id="session-1")
    ]
    codex_manager.list_sessions.side_effect = lambda: list(remaining_sessions)
    codex_manager.get_session.return_value = session
    manager.session_deleter.side_effect = lambda _session_id: remaining_sessions.clear()

    result = manager.dispatch(
        message_id="delete-session",
        prompt="del S1",
        message_type="text",
        correlation_id=None,
        source_ip="100.64.0.21",
        delivery_route=delivery_route(),
    )

    assert result.message is not None
    assert result.message.startswith(
        "Delete: Session 1 deleted. The current selection was cleared."
    )
    manager.session_deleter.assert_called_once_with("session-1")
    assert manager.session_id() is None
    assert manager.session_slot_matches(1, "session-1") is False
