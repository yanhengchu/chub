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


def test_legacy_codex_usage_route_record_remains_loadable() -> None:
    record = WeixinChubModeSubmission.model_validate(
        {
            "message_id": "legacy-codex-usage",
            "operation_id": "operation-1",
            "status": "routed",
            "code": "codex_usage_checked",
            "message": "Codex Usage: Weekly 41% left",
            "http_status": 200,
            "dispatch_disposition": "reply",
            "created_at": "2026-08-12T00:00:00Z",
            "updated_at": "2026-08-12T00:00:00Z",
        }
    )

    assert record.code == "codex_usage_checked"


def test_legacy_codex_help_route_record_remains_loadable() -> None:
    record = WeixinChubModeSubmission.model_validate(
        {
            "message_id": "legacy-codex-help",
            "operation_id": "operation-1",
            "status": "routed",
            "code": "codex_help_checked",
            "message": "Codex 命令",
            "http_status": 200,
            "dispatch_disposition": "reply",
            "created_at": "2026-08-12T00:00:00Z",
            "updated_at": "2026-08-12T00:00:00Z",
        }
    )

    assert record.code == "codex_help_checked"


def test_duplicate_status_check_replays_without_rechecking(
    settings: Settings,
) -> None:
    manager, _codex_manager, quick_interactions = configured_manager(settings)
    first = manager.dispatch(
        message_id="status-duplicate",
        prompt="查询状态",
        message_type="text",
        correlation_id=None,
        source_ip="100.64.0.21",
        delivery_route=delivery_route(),
    )
    duplicate = manager.dispatch(
        message_id="status-duplicate",
        prompt="状态查询",
        message_type="text",
        correlation_id=None,
        source_ip="100.64.0.21",
        delivery_route=delivery_route(),
    )

    assert duplicate == first
    quick_interactions.weixin_task_status_snapshot.assert_called_once()
    assert manager._state.submissions == []


@pytest.mark.parametrize(
    "prompt",
    ["检查任务状态", "任务状态", "查询任务结果", "任务结果", "查询状态 123"],
)
def test_retired_or_non_exact_status_prompt_is_submitted_as_normal_task(
    settings: Settings,
    prompt: str,
) -> None:
    manager, _codex_manager, quick_interactions = configured_manager(settings)

    result = manager.dispatch(
        message_id=f"status-near-miss-{prompt}",
        prompt=prompt,
        message_type="text",
        correlation_id=None,
        source_ip="100.64.0.21",
        delivery_route=delivery_route(),
    )

    assert result.disposition == "reply"
    assert result.message == submitted_task_message(settings, prompt)
    quick_interactions.submit.assert_called_once()


def test_dispatch_persists_pass_decision_across_mode_change(
    settings: Settings,
) -> None:
    manager = WeixinChubModeManager(settings, MagicMock(), MagicMock())

    first = manager.dispatch(
        message_id="dispatch-pass-1",
        prompt="普通 OpenClaw 消息",
        message_type="text",
        correlation_id=None,
        source_ip="100.64.0.21",
        delivery_route=delivery_route(),
    )
    manager._state.configuration.enabled = True
    duplicate = manager.dispatch(
        message_id="dispatch-pass-1",
        prompt="重复投递",
        message_type="text",
        correlation_id=None,
        source_ip="100.64.0.21",
        delivery_route=delivery_route(),
    )

    assert first.disposition == duplicate.disposition == "pass"
    persisted = json.loads(
        settings.openclaw.weixin_chub_mode.state_file.read_text(encoding="utf-8")
    )
    assert persisted["submissions"][0]["status"] == "passed"
    assert persisted["submissions"][0]["http_status"] == 200
    assert "普通 OpenClaw 消息" not in json.dumps(persisted, ensure_ascii=False)


def test_dispatch_returns_bounded_failure_instead_of_api_error(
    settings: Settings,
) -> None:
    manager, _codex_manager, quick_interactions = configured_manager(settings)
    quick_interactions.is_running.return_value = True
    manager._state.session_id = "session-1"

    result = manager.dispatch(
        message_id="dispatch-busy-1",
        prompt="第二个任务",
        message_type="text",
        correlation_id=None,
        source_ip="100.64.0.21",
        delivery_route=delivery_route(),
    )

    assert result.disposition == "reply"
    assert result.message == (
        "Not submitted · The current Session is running.\n"
        "Task · 第二个任务\n\n"
        "Retry: Send session new retry to continue in a new Session."
    )
    quick_interactions.submit.assert_not_called()


@pytest.mark.parametrize("prompt", ["session new", "新建会话", " 新建会话。 "])
def test_codex_new_creates_and_switches_without_submitting(
    settings: Settings,
    prompt: str,
) -> None:
    manager, codex_manager, quick_interactions = configured_manager(settings)
    codex_manager.create_session.return_value = SimpleNamespace(id="session-new")
    manager.codex_account_reader = MagicMock()
    manager.codex_account_reader.read_account_status.return_value = (
        CodexQuotaData(status="unavailable"),
        CodexTokenUsageData(status="unavailable"),
    )
    codex_manager.list_sessions.return_value = [
        CodexSession(
            id="session-new",
            workspace_id="chub",
            workspace_name="Chub",
            cwd="/project",
            permission_mode="full-access",
            status="stopped",
            activity="idle",
        )
    ]

    result = manager.dispatch(
        message_id=f"codex-new-{prompt}",
        prompt=prompt,
        message_type="text",
        correlation_id=None,
        source_ip="100.64.0.21",
        delivery_route=delivery_route(),
    )

    assert result.message is not None
    assert result.message.startswith(
        "Create: Session 1 was created and selected.\n\n"
        "Sessions\n\n"
    )
    assert "▶ S1 · Unnamed Session" in result.message
    assert result.message.endswith("Weekly Unavailable")
    assert manager.session_id() == "session-new"
    codex_manager.set_initial_quick_interaction_title.assert_not_called()
    quick_interactions.submit.assert_not_called()


@pytest.mark.parametrize(
    ("prompt", "task_prompt"),
    [
        ("session new, check device status", "check device status"),
        ("新建会话，检查设备状态。", "检查设备状态。"),
        ("新建会话 检查另一项任务", "检查另一项任务"),
        ("新建会话：/api/devices 修复接口", "/api/devices 修复接口"),
        ("session new: .env 配置问题", ".env 配置问题"),
        ("新建会话，# 检查标题", "# 检查标题"),
    ],
)
def test_codex_new_with_task_creates_switches_and_submits_once(
    settings: Settings,
    prompt: str,
    task_prompt: str,
) -> None:
    manager, codex_manager, quick_interactions = configured_manager(settings)
    session = CodexSession(
        id="session-new",
        workspace_id="chub",
        workspace_name="Chub",
        cwd="/project",
        permission_mode="full-access",
        status="stopped",
        activity="idle",
    )
    codex_manager.create_session.return_value = SimpleNamespace(id=session.id)
    codex_manager.get_session.return_value = session
    codex_manager.list_sessions.return_value = [session]

    first = manager.dispatch(
        message_id=f"new-with-task-{prompt}",
        prompt=prompt,
        message_type="text",
        correlation_id=None,
        source_ip="100.64.0.21",
        delivery_route=delivery_route(),
    )
    duplicate = manager.dispatch(
        message_id=f"new-with-task-{prompt}",
        prompt=prompt,
        message_type="text",
        correlation_id=None,
        source_ip="100.64.0.21",
        delivery_route=delivery_route(),
    )

    assert first.disposition == "reply"
    assert first.message == (
        "Create: Session 1 was created and selected. Task submitted.\n"
        f"▶ S1 · {task_prompt}\n"
        f"Task · {task_prompt}"
    )
    assert duplicate.message == first.message
    codex_manager.create_session.assert_called_once()
    quick_interactions.submit.assert_called_once()
    assert quick_interactions.submit.call_args.args[:2] == (
        "session-new",
        task_prompt,
    )


def test_codex_new_with_task_failure_shows_task_summary(settings: Settings) -> None:
    manager, codex_manager, quick_interactions = configured_manager(settings)
    session = CodexSession(
        id="session-new",
        workspace_id="chub",
        workspace_name="Chub",
        cwd="/project",
        permission_mode="full-access",
        status="stopped",
        activity="idle",
    )
    codex_manager.create_session.return_value = SimpleNamespace(id=session.id)
    codex_manager.get_session.return_value = session
    codex_manager.list_sessions.return_value = [session]
    quick_interactions.submit.side_effect = ApiError(
        503,
        "quick_worker_unavailable",
        "private detail",
    )

    result = manager.dispatch(
        message_id="new-with-failed-task",
        prompt="新建会话 检查设备状态",
        message_type="text",
        correlation_id=None,
        source_ip="100.64.0.21",
        delivery_route=delivery_route(),
    )
    duplicate = manager.dispatch(
        message_id="new-with-failed-task",
        prompt="新建会话 检查设备状态",
        message_type="text",
        correlation_id=None,
        source_ip="100.64.0.21",
        delivery_route=delivery_route(),
    )

    assert result.message is not None
    assert "the task was not submitted" in result.message
    assert result.message.splitlines()[1] == "▶ S1 · Unnamed Session"
    assert result.message.splitlines()[2] == "Task · 检查设备状态"
    assert "Sessions" not in result.message
    assert "Weekly" not in result.message
    assert duplicate == result
    quick_interactions.submit.assert_called_once()


def test_codex_new_with_task_creation_failure_does_not_link_current_session(
    settings: Settings,
) -> None:
    manager, codex_manager, quick_interactions = configured_manager(settings)
    manager._state.session_id = "session-1"
    manager._state.session_slots = [
        WeixinChubModeSessionSlot(slot=1, session_id="session-1")
    ]
    codex_manager.create_session.side_effect = ApiError(
        503,
        "codex_unavailable",
        "private detail",
    )

    result = manager.dispatch(
        message_id="new-with-task-create-failure",
        prompt="新建会话 检查设备状态",
        message_type="text",
        correlation_id=None,
        source_ip="100.64.0.21",
        delivery_route=delivery_route(),
    )

    assert result.message == (
        "Create: Failed. Codex could not create a Session.\n"
        "Task · 检查设备状态"
    )
    quick_interactions.submit.assert_not_called()


def test_codex_new_status_does_not_fill_unassigned_candidate(
    settings: Settings,
) -> None:
    manager, codex_manager, _quick_interactions = configured_manager(settings)
    sessions = [
        CodexSession(
            id=session_id,
            workspace_id="chub",
            workspace_name="Chub",
            cwd="/project",
            title=title,
            permission_mode="full-access",
            status="stopped",
            activity="idle",
        )
        for session_id, title in (
            ("session-1", "已有会话"),
            ("candidate", "等待候选"),
            ("session-new", "新建会话"),
        )
    ]
    manager._state.session_slots = [
        WeixinChubModeSessionSlot(slot=1, session_id="session-1")
    ]
    codex_manager.create_session.return_value = SimpleNamespace(id="session-new")
    codex_manager.list_sessions.return_value = sessions
    manager.codex_account_reader = MagicMock()
    manager.codex_account_reader.read_account_status.return_value = (
        CodexQuotaData(status="unavailable"),
        CodexTokenUsageData(status="unavailable"),
    )

    result = manager.dispatch(
        message_id="new-does-not-fill-candidate",
        prompt="session new",
        message_type="text",
        correlation_id=None,
        source_ip="100.64.0.21",
        delivery_route=delivery_route(),
    )

    assert result.message is not None
    assert "▶ S2 · 新建会话" in result.message
    assert "等待候选" not in result.message
    assert "1 more Sessions" in result.message
    assert manager.session_slot_matches(2, "session-new") is True
    assert manager.session_slot_matches(3, "candidate") is False


def test_internal_codex_status_does_not_fill_unassigned_candidate(
    settings: Settings,
) -> None:
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
        for index in (1, 2)
    ]
    manager._state.session_id = "session-1"
    manager._state.session_slots = [
        WeixinChubModeSessionSlot(slot=1, session_id="session-1")
    ]
    codex_manager.list_sessions.return_value = sessions
    manager.codex_account_reader = MagicMock()
    manager.codex_account_reader.read_account_status.return_value = (
        CodexQuotaData(status="unavailable"),
        CodexTokenUsageData(status="unavailable"),
    )
    manager._refresh_chub_cache()
    codex_manager.list_sessions.reset_mock()

    internal_status = manager.codex_status_message()

    assert "▶ S1 · 候选 1" in internal_status
    assert "候选 2" not in internal_status
    codex_manager.list_sessions.assert_not_called()
    assert manager.session_slot_matches(2, "session-2") is False

    manager.dispatch(
        message_id="active-status-fills-internal-candidate",
        prompt="sync",
        message_type="text",
        correlation_id=None,
        source_ip="100.64.0.21",
        delivery_route=delivery_route(),
    )

    assert manager.session_slot_matches(2, "session-2") is True


def test_codex_operation_log_uses_operation_result_not_status_refresh(
    settings: Settings,
) -> None:
    manager, codex_manager, _quick_interactions = configured_manager(settings)
    codex_manager.create_session.return_value = SimpleNamespace(id="session-new")
    codex_manager.list_sessions.return_value = [
        CodexSession(
            id="session-new",
            workspace_id="chub",
            workspace_name="Chub",
            cwd="/project",
            permission_mode="full-access",
            status="stopped",
            activity="idle",
        )
    ]

    with patch(
        "app.services.openclaw_weixin_chub_mode.write_operation"
    ) as write_operation:
        created = manager.dispatch(
            message_id="new-with-status-failure",
            prompt="session new",
            message_type="text",
            correlation_id=None,
            source_ip="100.64.0.21",
            delivery_route=delivery_route(),
        )

    assert created.message is not None
    assert "Weekly Unavailable" in created.message
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

    with patch(
        "app.services.openclaw_weixin_chub_mode.write_operation"
    ) as write_operation:
        manager.dispatch(
            message_id="invalid-switch-log",
            prompt="session switch 0",
            message_type="text",
            correlation_id=None,
            source_ip="100.64.0.21",
            delivery_route=delivery_route(),
        )

    dispatch_entries = [
        call.kwargs
        for call in write_operation.call_args_list
        if call.kwargs["action"] == "weixin_chub_mode_dispatch"
    ]
    assert [entry["status"] for entry in dispatch_entries] == [
        "requested",
        "started",
        "failed",
    ]


def test_duplicate_codex_new_replays_status_without_creating_twice(
    settings: Settings,
) -> None:
    manager, codex_manager, _quick_interactions = configured_manager(settings)
    codex_manager.create_session.return_value = SimpleNamespace(id="session-new")
    codex_manager.list_sessions.return_value = [
        CodexSession(
            id="session-new",
            workspace_id="chub",
            workspace_name="Chub",
            cwd="/project",
            permission_mode="full-access",
            status="stopped",
            activity="idle",
        )
    ]

    first = manager.dispatch(
        message_id="codex-new-duplicate",
        prompt="session new",
        message_type="text",
        correlation_id=None,
        source_ip="100.64.0.21",
        delivery_route=delivery_route(),
    )
    duplicate = manager.dispatch(
        message_id="codex-new-duplicate",
        prompt="session new",
        message_type="text",
        correlation_id=None,
        source_ip="100.64.0.21",
        delivery_route=delivery_route(),
    )

    assert duplicate.message == first.message
    codex_manager.create_session.assert_called_once()


def test_codex_new_discards_unstarted_session_when_slot_state_write_fails(
    settings: Settings,
) -> None:
    manager, codex_manager, _quick_interactions = configured_manager(settings)
    codex_manager.create_session.return_value = SimpleNamespace(id="session-new")
    manager._write_state = MagicMock(side_effect=OSError("disk unavailable"))

    with pytest.raises(OSError):
        manager._create_session(manager.configuration())

    codex_manager.discard_unstarted_session.assert_called_once_with("session-new")
    assert manager.session_id() is None
    assert manager._state.session_slots == []


def test_chub_sync_uses_placeholder_for_untitled_session(
    settings: Settings,
) -> None:
    manager, codex_manager, quick_interactions = configured_manager(settings)
    manager.codex_account_reader = MagicMock()
    manager.codex_account_reader.read_account_status.return_value = (
        CodexQuotaData(status="unavailable"),
        CodexTokenUsageData(status="unavailable"),
    )
    codex_manager.list_sessions.return_value = [
        CodexSession(
            id="untitled-session",
            workspace_id="chub",
            workspace_name="Chub",
            cwd="/project",
            title=None,
            permission_mode="full-access",
            status="stopped",
            activity="idle",
        )
    ]

    result = manager.dispatch(
        message_id="codex-status-untitled",
        prompt="sync",
        message_type="text",
        correlation_id=None,
        source_ip="100.64.0.21",
        delivery_route=delivery_route(),
    )

    assert "S1 · Unnamed Session" in (result.message or "")
    quick_interactions.submit.assert_not_called()
