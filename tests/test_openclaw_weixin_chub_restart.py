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
    assert result.message == "Chub 重启已登记，完成后将原路发送结果。"
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

    assert result.disposition == "handled"
    assert result.message is None
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

    assert second.message == "Chub 重启已在处理中，完成后将原路发送结果。"
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

    assert result.message == "Chub 重启未登记：原消息的 ClawBot 当前不可用。"
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
    assert "1 个重启结果通知失败" in overview
    assert recovered.record_deferred_restart_completion(
        current.coordinator_operation_id,
        coordinator.request.call_args.kwargs["task_id"],
        "succeeded",
        utc_now(),
    )
    notifier.assert_not_called()
