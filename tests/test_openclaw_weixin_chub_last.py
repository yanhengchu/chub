from types import SimpleNamespace
from unittest.mock import MagicMock

from app.codex.models import QuickInteractionTask, utc_now
from app.core.config import Settings

from tests.openclaw_weixin_chub_mode_helpers import (
    configured_manager,
    delivery_route,
)


def _completed_task() -> QuickInteractionTask:
    return QuickInteractionTask(
        id="web-task-1",
        session_id="session-1",
        prompt="电脑端检查服务",
        status="succeeded",
        result="服务运行正常。",
        created_at=utc_now(),
        updated_at=utc_now(),
    )


def test_last_resends_latest_standard_result_from_any_entrypoint(
    settings: Settings,
) -> None:
    manager, _codex_manager, quick_interactions = configured_manager(settings)
    manager._state.session_id = "session-1"
    task = _completed_task()
    quick_interactions.latest_completed_standard_task.return_value = task
    notifier = MagicMock(return_value=SimpleNamespace(status="sent", error=None))
    manager.last_result_notifier = notifier

    result = manager.dispatch(
        message_id="last-1",
        prompt="Last",
        message_type="text",
        correlation_id=None,
        source_ip="100.64.0.21",
        delivery_route=delivery_route(),
    )

    assert result.disposition == "handled"
    quick_interactions.latest_completed_standard_task.assert_called_once_with("session-1")
    notifier.assert_called_once_with(task, delivery_route())
    record = manager._find_submission("last-1")
    assert record is not None
    assert record.code == "codex_last_checked"
    assert record.dispatch_disposition == "handled"


def test_last_does_not_resend_on_duplicate_message(
    settings: Settings,
) -> None:
    manager, _codex_manager, quick_interactions = configured_manager(settings)
    manager._state.session_id = "session-1"
    quick_interactions.latest_completed_standard_task.return_value = _completed_task()
    notifier = MagicMock(return_value=SimpleNamespace(status="sent", error=None))
    manager.last_result_notifier = notifier
    request = dict(
        message_id="last-duplicate",
        prompt="last",
        message_type="text",
        correlation_id=None,
        source_ip="100.64.0.21",
        delivery_route=delivery_route(),
    )

    assert manager.dispatch(**request).disposition == "handled"
    assert manager.dispatch(**request).disposition == "handled"

    assert notifier.call_count == 1


def test_last_reports_when_current_session_has_no_completed_standard_result(
    settings: Settings,
) -> None:
    manager, _codex_manager, quick_interactions = configured_manager(settings)
    manager._state.session_id = "session-1"
    quick_interactions.latest_completed_standard_task.return_value = None

    result = manager.dispatch(
        message_id="last-empty",
        prompt="last",
        message_type="text",
        correlation_id=None,
        source_ip="100.64.0.21",
        delivery_route=delivery_route(),
    )

    assert result.disposition == "reply"
    assert result.message is not None
    assert result.message.startswith("Last: No completed task result")


def test_last_delivery_failure_can_be_retried_with_a_new_message_id(
    settings: Settings,
) -> None:
    manager, _codex_manager, quick_interactions = configured_manager(settings)
    manager._state.session_id = "session-1"
    task = _completed_task()
    quick_interactions.latest_completed_standard_task.return_value = task
    notifier = MagicMock(
        side_effect=(
            SimpleNamespace(status="failed", error="微信结果未送达。"),
            SimpleNamespace(status="sent", error=None),
        )
    )
    manager.last_result_notifier = notifier

    failed = manager.dispatch(
        message_id="last-failed",
        prompt="last",
        message_type="text",
        correlation_id=None,
        source_ip="100.64.0.21",
        delivery_route=delivery_route(),
    )
    retried = manager.dispatch(
        message_id="last-retry",
        prompt="last",
        message_type="text",
        correlation_id=None,
        source_ip="100.64.0.21",
        delivery_route=delivery_route(),
    )

    assert failed.disposition == "reply"
    assert failed.message is not None
    assert failed.message.startswith("Last: Result was not resent.")
    assert retried.disposition == "handled"
    assert notifier.call_count == 2
