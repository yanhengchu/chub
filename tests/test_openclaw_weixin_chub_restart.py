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


@pytest.mark.parametrize(
    ("prompt", "message_type"),
    [
        ("restart", "text"),
        ("RESTART。", "text"),
        ("重启", "text"),
        (" 重启。 ", "voice"),
    ],
)
def test_chub_restart_registers_fixed_restart_and_replies(
    settings: Settings,
    prompt: str,
    message_type: str,
) -> None:
    manager, _codex_manager, quick_interactions = configured_manager(settings)
    coordinator, _notifier = enable_restart_command(manager)

    result = manager.dispatch(
        message_id=f"restart-{prompt}-{message_type}",
        prompt=prompt,
        message_type=message_type,
        correlation_id="correlation-1",
        source_ip="100.64.0.21",
        delivery_route=delivery_route(),
    )

    assert result.disposition == "reply"
    assert result.message == (
        "Restart: Scheduled. The result will be sent when completed."
    )
    request = coordinator.request.call_args.kwargs
    assert request["operation_id"].endswith(":restart")
    assert request["task_id"].startswith("weixin-restart-")
    assert request["source_ip"] == "100.64.0.21"
    coordinator.maybe_schedule.assert_called_once_with()
    assert len(manager._state.restart_operations) == 1
    operation = manager._state.restart_operations[0]
    assert operation.delivery_route == delivery_route()
    assert operation.status == "pending"
    quick_interactions.submit.assert_not_called()


def test_system_upgrade_status_is_fixed_read_only_command(settings: Settings) -> None:
    manager, _codex_manager, quick_interactions = configured_manager(settings)
    reader = MagicMock(
        return_value=SimpleNamespace(
            state="available",
            message="升级方案已就绪。",
        )
    )
    manager.system_upgrade_status_reader = reader

    result = manager.dispatch(
        message_id="system-upgrade-status",
        prompt="system upgrade status",
        message_type="text",
        correlation_id=None,
        source_ip="100.64.0.21",
        delivery_route=delivery_route(),
    )

    assert result.message == "System upgrade: Ready · 升级方案已就绪。"
    reader.assert_called_once_with()
    quick_interactions.submit.assert_not_called()


def test_system_upgrade_starts_once_without_creating_a_task(settings: Settings) -> None:
    manager, _codex_manager, quick_interactions = configured_manager(settings)
    starter = MagicMock(
        return_value=SimpleNamespace(
            state="preparing",
            message="正在关闭新的写入。",
        )
    )
    manager.system_upgrade_starter = starter

    first = manager.dispatch(
        message_id="system-upgrade-start",
        prompt="system upgrade",
        message_type="text",
        correlation_id="upgrade-1",
        source_ip="100.64.0.21",
        delivery_route=delivery_route(),
    )
    duplicate = manager.dispatch(
        message_id="system-upgrade-start",
        prompt="system upgrade",
        message_type="text",
        correlation_id="upgrade-1",
        source_ip="100.64.0.21",
        delivery_route=delivery_route(),
    )

    assert first.message == "System upgrade: Started. Check with system upgrade status."
    assert duplicate == first
    starter.assert_called_once_with("100.64.0.21")
    quick_interactions.submit.assert_not_called()


def test_system_upgrade_rejected_by_shared_preconditions(settings: Settings) -> None:
    manager, _codex_manager, quick_interactions = configured_manager(settings)
    manager.system_upgrade_starter = MagicMock(
        side_effect=ApiError(
            409,
            "system_upgrade_precondition_failed",
            "Quick Worker 尚未就绪。",
        )
    )

    result = manager.dispatch(
        message_id="system-upgrade-blocked",
        prompt="system upgrade",
        message_type="text",
        correlation_id=None,
        source_ip="100.64.0.21",
        delivery_route=delivery_route(),
    )

    assert result.message == "System upgrade: Not started · Quick Worker 尚未就绪。"
    quick_interactions.submit.assert_not_called()


def test_chub_restart_initial_reply_does_not_list_sessions_or_usage(
    settings: Settings,
) -> None:
    manager, codex_manager, quick_interactions = configured_manager(settings)
    enable_restart_command(manager)
    sessions = [
        CodexSession(
            session_mode="quick",
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
    quick_interactions.is_running.side_effect = (
        lambda session_id: session_id == "session-2"
    )
    quick_interactions.weixin_task_status_snapshot.return_value = SimpleNamespace(
        running_tasks=(("session-2", "检查后台日志"),)
    )

    result = manager.dispatch(
        message_id="restart-with-session-list",
        prompt="restart",
        message_type="text",
        correlation_id=None,
        source_ip="100.64.0.21",
        delivery_route=delivery_route(),
    )

    assert result.message == (
        "Restart: Scheduled. The result will be sent when completed."
    )
    assert "Sessions" not in result.message
    assert "Weekly" not in result.message


@pytest.mark.parametrize(
    "prompt",
    [
        "chub restart",
        "CHUB RESTART。",
        "restart now",
        "重启 Chub",
        "重启后检查状态",
    ],
)
def test_removed_or_near_restart_matches_remain_normal_tasks(
    settings: Settings,
    prompt: str,
) -> None:
    manager, _codex_manager, quick_interactions = configured_manager(settings)
    coordinator, _notifier = enable_restart_command(manager)

    result = manager.dispatch(
        message_id=f"restart-near-match-{prompt}",
        prompt=prompt,
        message_type="text",
        correlation_id=None,
        source_ip="100.64.0.21",
        delivery_route=delivery_route(),
    )

    assert result.disposition == "reply"
    assert result.message == submitted_task_message(settings, prompt)
    coordinator.request.assert_not_called()
    quick_interactions.submit.assert_called_once()


def test_duplicate_chub_restart_does_not_register_twice(settings: Settings) -> None:
    manager, _codex_manager, quick_interactions = configured_manager(settings)
    coordinator, _notifier = enable_restart_command(manager)

    first = manager.dispatch(
        message_id="duplicate-chub-restart",
        prompt="restart",
        message_type="text",
        correlation_id=None,
        source_ip="100.64.0.21",
        delivery_route=delivery_route(),
    )
    duplicate = manager.dispatch(
        message_id="duplicate-chub-restart",
        prompt="restart",
        message_type="text",
        correlation_id=None,
        source_ip="100.64.0.21",
        delivery_route=delivery_route(),
    )

    assert duplicate == first
    coordinator.request.assert_called_once()
    coordinator.maybe_schedule.assert_called_once()
    assert len(manager._state.restart_operations) == 1
    quick_interactions.submit.assert_not_called()


def test_second_chub_restart_reuses_active_route_operation(
    settings: Settings,
) -> None:
    manager, _codex_manager, _quick_interactions = configured_manager(settings)
    coordinator, _notifier = enable_restart_command(manager)

    manager.dispatch(
        message_id="first-chub-restart",
        prompt="restart",
        message_type="text",
        correlation_id=None,
        source_ip="100.64.0.21",
        delivery_route=delivery_route(),
    )
    second = manager.dispatch(
        message_id="second-chub-restart",
        prompt="重启",
        message_type="text",
        correlation_id=None,
        source_ip="100.64.0.21",
        delivery_route=delivery_route(),
    )

    assert second.message == (
        "Restart: Already in progress. The result will be sent when completed.\n\n"
        "No sessions\n\nWeekly Unavailable"
    )
    coordinator.request.assert_called_once()
    assert len(manager._state.restart_operations) == 1


def test_chub_restart_rejects_unavailable_delivery_route(
    settings: Settings,
) -> None:
    manager, _codex_manager, quick_interactions = configured_manager(settings)
    coordinator, _notifier = enable_restart_command(manager)
    manager.route_validator = MagicMock(return_value="原消息的 ClawBot 当前不可用。")

    result = manager.dispatch(
        message_id="invalid-route-chub-restart",
        prompt="restart",
        message_type="text",
        correlation_id=None,
        source_ip="100.64.0.21",
        delivery_route=delivery_route(),
    )

    assert result.message == (
        "Restart: Not scheduled because the reply route is unavailable.\n\n"
        "No sessions\n\nWeekly Unavailable"
    )
    coordinator.request.assert_not_called()
    quick_interactions.submit.assert_not_called()


def test_chub_restart_completion_sends_persisted_route_once(
    settings: Settings,
) -> None:
    manager, _codex_manager, _quick_interactions = configured_manager(settings)
    coordinator, notifier = enable_restart_command(manager)
    manager.dispatch(
        message_id="completed-chub-restart",
        prompt="restart",
        message_type="text",
        correlation_id=None,
        source_ip="100.64.0.21",
        delivery_route=delivery_route(),
    )
    operation = manager._state.restart_operations[0].model_copy(deep=True)
    request = SimpleNamespace(
        operation_id=operation.coordinator_operation_id,
        requested_task_id=coordinator.request.call_args.kwargs["task_id"],
    )

    assert manager.deferred_restart_readiness(request) == "ready"
    assert manager.record_deferred_restart_started(
        operation.coordinator_operation_id,
        request.requested_task_id,
        utc_now(),
    )
    assert manager.record_deferred_restart_completion(
        operation.coordinator_operation_id,
        request.requested_task_id,
        "succeeded",
        utc_now(),
    )
    assert manager.record_deferred_restart_completion(
        operation.coordinator_operation_id,
        request.requested_task_id,
        "succeeded",
        utc_now(),
    )

    completed = manager._state.restart_operations[0]
    assert completed.status == "succeeded"
    assert completed.notification_status == "sent"
    notifier.assert_called_once_with(delivery_route(), "succeeded", None)


def test_chub_restart_interrupted_notification_is_not_retried(
    settings: Settings,
) -> None:
    manager, _codex_manager, _quick_interactions = configured_manager(settings)
    coordinator, notifier = enable_restart_command(manager)
    manager.dispatch(
        message_id="interrupted-chub-restart-notification",
        prompt="重启",
        message_type="text",
        correlation_id=None,
        source_ip="100.64.0.21",
        delivery_route=delivery_route(),
    )
    operation = manager._state.restart_operations[0]
    operation.status = "succeeded"
    operation.notification_status = "sending"
    manager._write_state(manager._state)

    recovered = WeixinChubModeManager(
        settings,
        manager.codex_manager,
        manager.quick_interactions,
        manager.route_validator,
        restart_coordinator=coordinator,
        restart_notifier=notifier,
    )
    current = recovered._state.restart_operations[0]

    assert current.notification_status == "failed"
    assert "未自动重试" in (current.notification_error or "")
    overview = recovered._format_chub_overview(
        recovered._route_fingerprint(delivery_route()),
        elapsed_ms=10,
    )
    assert "Restart result notifications failed: 1" in overview
    assert recovered.record_deferred_restart_completion(
        current.coordinator_operation_id,
        coordinator.request.call_args.kwargs["task_id"],
        "succeeded",
        utc_now(),
    )
    notifier.assert_not_called()
