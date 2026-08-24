from __future__ import annotations

from contextlib import nullcontext
from types import SimpleNamespace
from unittest.mock import MagicMock, call, patch

import pytest

from app.application import create_app
from app.codex.models import (
    CodexQuotaData,
    CodexSession,
    CodexTokenUsageData,
)
from app.core.config import Settings
from app.services.openclaw_weixin_chub_mode import WeixinChubModeManager
from app.services.openclaw_weixin_chub_models import WeixinChubModeSessionSlot

from tests.openclaw_weixin_chub_mode_helpers import (
    configured_manager,
    delivery_route,
)


def configured_stop_target(settings: Settings):
    manager, codex_manager, quick_interactions = configured_manager(settings)
    session = CodexSession(
        session_mode="quick",
        id="session-2",
        codex_session_id="native-2",
        workspace_id="chub",
        workspace_name="Chub",
        cwd="/project",
        title="运行任务",
        permission_mode="full-access",
        status="running",
        activity="working",
        activity_source="quick",
    )
    manager._state.session_id = session.id
    manager._state.session_slots = [
        WeixinChubModeSessionSlot(slot=2, session_id=session.id)
    ]
    codex_manager.list_sessions.return_value = [session]
    codex_manager.get_session.return_value = session
    manager.codex_account_reader = MagicMock()
    manager.codex_account_reader.read_account_status.return_value = (
        CodexQuotaData(status="unavailable"),
        CodexTokenUsageData(status="unavailable"),
    )

    def stop_session(session_id: str) -> CodexSession:
        assert session_id == session.id
        session.status = "stopped"
        session.activity = "idle"
        session.activity_source = "none"
        return session

    manager.session_stopper = MagicMock(side_effect=stop_session)
    manager.session_stop_notifier = MagicMock(
        return_value=SimpleNamespace(status="sent", error=None)
    )
    manager._start_stop_operation = lambda operation_id: manager._run_stop_operation(
        operation_id
    )
    return manager, session, quick_interactions


@pytest.mark.parametrize(
    "prompt",
    [
        "stop",
        "stop 2",
        "stop s2",
        "stop2",
        "stopS2",
        "stop S2",
        "STOP 2。",
        "停止 2",
        "停止",
        "停止2",
        "停止S2",
        "停止二",
        "停止 S2",
    ],
)
def test_session_stop_keeps_slot_and_current_binding(
    settings: Settings,
    prompt: str,
) -> None:
    manager, session, quick_interactions = configured_stop_target(settings)

    result = manager.dispatch(
        message_id=f"stop-{prompt}",
        prompt=prompt,
        message_type="text",
        correlation_id=None,
        source_ip="100.64.0.21",
        delivery_route=delivery_route(),
    )

    assert result.message is not None
    assert result.message == (
        "Stop: Scheduled. The result will be sent when completed."
    )
    assert manager.session_id() == session.id
    assert manager.session_slot_matches(2, session.id) is True
    manager.session_stopper.assert_called_once_with(session.id)
    final_message = manager.session_stop_notifier.call_args.args[1]()
    assert final_message.startswith(
        "Stop: Session 2 stopped.\n\nSessions\n\n▶ S2 · 运行任务"
    )
    assert "Task ·" not in final_message
    quick_interactions.submit.assert_not_called()


def test_session_stop_final_notification_restores_other_running_task_name(
    settings: Settings,
) -> None:
    manager, target, quick_interactions = configured_stop_target(settings)
    other = CodexSession(
        session_mode="quick",
        id="session-1",
        workspace_id="chub",
        workspace_name="Chub",
        cwd="/project",
        title="后台检查",
        permission_mode="full-access",
        status="running",
        activity="working",
        activity_source="quick",
    )
    manager._state.session_slots.insert(
        0,
        WeixinChubModeSessionSlot(slot=1, session_id=other.id),
    )
    manager.codex_manager.list_sessions.return_value = [target, other]
    quick_interactions.is_running.side_effect = lambda session_id: (
        session_id == other.id
    )
    quick_interactions.weixin_task_status_snapshot.return_value = SimpleNamespace(
        running_tasks=((other.id, "检查后台服务运行状态"),),
    )

    manager.dispatch(
        message_id="stop-with-other-running-task",
        prompt="stop 2",
        message_type="text",
        correlation_id=None,
        source_ip="100.64.0.21",
        delivery_route=delivery_route(),
    )

    final_message = manager.session_stop_notifier.call_args.args[1]()
    assert "Task · 检查后台服务运行状态" in final_message
    assert "Task · Running" not in final_message
    quick_interactions.weixin_task_status_snapshot.assert_called_with(delivery_route())


def test_session_stop_duplicate_does_not_stop_twice(settings: Settings) -> None:
    manager, session, _quick_interactions = configured_stop_target(settings)

    with patch(
        "app.services.openclaw_weixin_chub_mode.write_operation"
    ) as write_operation:
        first = manager.dispatch(
            message_id="duplicate-stop",
            prompt="停止 2",
            message_type="text",
            correlation_id=None,
            source_ip="100.64.0.21",
            delivery_route=delivery_route(),
        )
        duplicate = manager.dispatch(
            message_id="duplicate-stop",
            prompt="停止 2",
            message_type="text",
            correlation_id=None,
            source_ip="100.64.0.21",
            delivery_route=delivery_route(),
        )

    assert duplicate == first
    manager.session_stopper.assert_called_once_with(session.id)
    stop_entries = [
        call.kwargs
        for call in write_operation.call_args_list
        if call.kwargs["action"] == "stop_codex_session"
    ]
    assert [entry["status"] for entry in stop_entries] == [
        "requested",
        "started",
        "succeeded",
    ]


def test_session_stop_returns_before_background_operation_runs(
    settings: Settings,
) -> None:
    manager, session, _quick_interactions = configured_stop_target(settings)
    manager._start_stop_operation = MagicMock()

    result = manager.dispatch(
        message_id="scheduled-stop",
        prompt="停止 2",
        message_type="text",
        correlation_id=None,
        source_ip="100.64.0.21",
        delivery_route=delivery_route(),
    )

    assert result.message is not None
    assert result.message.startswith(
        "Stop: Scheduled. The result will be sent when completed."
    )
    manager.session_stopper.assert_not_called()
    manager.session_stop_notifier.assert_not_called()
    manager._start_stop_operation.assert_called_once()
    operation = manager._state.stop_operations[0]
    assert operation.session_id == session.id
    assert operation.status == "pending"
    assert operation.notification_status is None


def test_session_stop_notification_failure_is_persisted_and_reported(
    settings: Settings,
) -> None:
    manager, _session, _quick_interactions = configured_stop_target(settings)
    manager.session_stop_notifier.return_value = SimpleNamespace(
        status="failed",
        error="route unavailable",
    )

    manager.dispatch(
        message_id="stop-notification-failed",
        prompt="停止 2",
        message_type="text",
        correlation_id=None,
        source_ip="100.64.0.21",
        delivery_route=delivery_route(),
    )

    operation = manager._state.stop_operations[0]
    assert operation.status == "succeeded"
    assert operation.notification_status == "failed"
    assert operation.notification_error == "route unavailable"
    overview = manager._format_chub_overview(
        manager._route_fingerprint(delivery_route()),
        elapsed_ms=10,
    )
    assert "Stop result notifications failed: 1" in overview


def test_session_stop_result_write_failure_still_sends_final_notification(
    settings: Settings,
) -> None:
    manager, session, _quick_interactions = configured_stop_target(settings)
    manager._start_stop_operation = MagicMock()
    manager.dispatch(
        message_id="stop-result-write-failed",
        prompt="stop 2",
        message_type="text",
        correlation_id=None,
        source_ip="100.64.0.21",
        delivery_route=delivery_route(),
    )
    operation_id = manager._state.stop_operations[0].operation_id
    manager._write_state = MagicMock(side_effect=OSError("disk unavailable"))

    manager._complete_stop_operation(
        operation_id,
        status="succeeded",
        message="Stop: Session 2 stopped.",
    )

    assert manager._state_error is True
    manager.session_stop_notifier.assert_called_once()
    message_factory = manager.session_stop_notifier.call_args.args[1]
    assert message_factory().startswith("Stop: Session 2 stopped.")
    assert manager._state.stop_operations[0].status == "pending"


def test_interrupted_session_stop_is_not_replayed_after_restart(
    settings: Settings,
) -> None:
    manager, session, quick_interactions = configured_stop_target(settings)
    manager._start_stop_operation = MagicMock()
    manager.dispatch(
        message_id="interrupted-stop",
        prompt="停止 2",
        message_type="text",
        correlation_id=None,
        source_ip="100.64.0.21",
        delivery_route=delivery_route(),
    )

    stopper = MagicMock()
    recovered = WeixinChubModeManager(
        settings,
        manager.codex_manager,
        quick_interactions,
        manager.route_validator,
        session_stopper=stopper,
        session_stop_notifier=manager.session_stop_notifier,
    )

    operation = recovered._state.stop_operations[0]
    assert operation.session_id == session.id
    assert operation.status == "failed"
    assert operation.notification_status == "failed"
    stopper.assert_not_called()


@pytest.mark.parametrize(
    "prompt",
    [
        "stop 0",
        "stop 10",
        "stop 2 extra",
        "停止 0",
        "停止 2 继续处理",
    ],
)
def test_invalid_stop_falls_back_to_normal_task(
    settings: Settings,
    prompt: str,
) -> None:
    manager, _session, quick_interactions = configured_stop_target(settings)

    result = manager.dispatch(
        message_id=f"invalid-stop-{prompt}",
        prompt=prompt,
        message_type="text",
        correlation_id=None,
        source_ip="100.64.0.21",
        delivery_route=delivery_route(),
    )

    assert result.message is not None
    assert result.message.startswith("Submitted")
    manager.session_stopper.assert_not_called()
    quick_interactions.submit.assert_called_once()
    assert quick_interactions.submit.call_args.args[1] == prompt


def test_session_stop_requires_confirmed_final_state_and_logs_failure(
    settings: Settings,
) -> None:
    manager, session, _quick_interactions = configured_stop_target(settings)
    manager.session_stopper.return_value = session
    manager.session_stopper.side_effect = None

    with patch(
        "app.services.openclaw_weixin_chub_mode.write_operation"
    ) as write_operation:
        result = manager.dispatch(
            message_id="unconfirmed-stop",
            prompt="stop 2",
            message_type="text",
            correlation_id=None,
            source_ip="100.64.0.21",
            delivery_route=delivery_route(),
        )

    assert result.message is not None
    assert result.message.startswith(
        "Stop: Scheduled. The result will be sent when completed."
    )
    assert manager.session_stop_notifier.call_args.args[1]().startswith(
        "Stop: Failed. The Session may have partially stopped."
    )
    stop_entries = [
        call.kwargs
        for call in write_operation.call_args_list
        if call.kwargs["action"] == "stop_codex_session"
    ]
    assert [entry["status"] for entry in stop_entries] == [
        "requested",
        "started",
        "failed",
    ]


def test_application_stop_callback_cleans_resources_in_order(
    settings: Settings,
) -> None:
    application = create_app(settings)
    parent = MagicMock()
    quick_interactions = application.state.quick_interactions
    terminal_tickets = application.state.terminal_tickets
    terminal_connections = application.state.terminal_connections
    codex_manager = application.state.codex_pty_manager
    quick_interactions.stop_operation_guard = MagicMock(
        return_value=nullcontext()
    )
    quick_interactions.cancel_codex_session = MagicMock()
    terminal_tickets.revoke_session = MagicMock()
    terminal_connections.close_session = MagicMock()
    codex_manager.ensure_stop_allowed = MagicMock()
    codex_manager.stop_session = MagicMock()
    parent.attach_mock(codex_manager.ensure_stop_allowed, "gate")
    parent.attach_mock(quick_interactions.cancel_codex_session, "cancel")
    parent.attach_mock(terminal_tickets.revoke_session, "revoke")
    parent.attach_mock(terminal_connections.close_session, "close")
    parent.attach_mock(codex_manager.stop_session, "stop")
    codex_manager.stop_session.return_value = SimpleNamespace(
        status="stopped",
        activity="idle",
    )

    result = application.state.weixin_chub_mode.session_stopper("session-2")

    assert result.status == "stopped"
    assert parent.mock_calls == [
        call.gate("session-2"),
        call.cancel("session-2"),
        call.revoke("session-2"),
        call.close("session-2"),
        call.stop("session-2"),
    ]
