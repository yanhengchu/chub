from __future__ import annotations

from datetime import timedelta

from app.core.response import ApiError
from app.codex.models import CodexSession, QuickInteractionTask, utc_now
from app.services import openclaw_weixin_chub_mode as chub_mode_module
from app.services.openclaw_weixin_chub_models import WeixinChubModeSessionSlot

from tests.openclaw_weixin_chub_mode_helpers import (
    configured_manager,
    delivery_route,
    inject_default_delivery_route,
)


def _prepare_current_session(manager, codex_manager) -> None:
    session = CodexSession(
        session_mode="quick",
        id="session-1",
        workspace_id="chub",
        workspace_name="Chub",
        cwd="/project",
        title="需求实施",
        permission_mode="full-access",
        status="stopped",
        activity="idle",
    )
    manager._state.session_id = session.id
    manager._state.session_slots = [
        WeixinChubModeSessionSlot(slot=1, session_id=session.id)
    ]
    codex_manager.list_sessions.return_value = [session]
    codex_manager.get_session.return_value = session


def test_request_cat_and_chinese_alias_return_full_saved_request(settings) -> None:
    manager, _codex_manager, _quick_interactions = configured_manager(settings)
    item = manager.request_backlog.save(
        title="微信需求池",
        content="目标：保存小需求。\n\n验收：可以完整查看。",
    )

    result = manager.dispatch(
        message_id="request-cat",
        prompt="查看需求一",
        message_type="text",
        correlation_id=None,
        source_ip="100.64.0.21",
        delivery_route=delivery_route(),
    )

    assert result.message == (
        "Request\n\n"
        f"R{item.slot} · 微信需求池\n\n"
        "Status · Ready\n\n"
        "目标：保存小需求。\n\n验收：可以完整查看。"
    )


def test_chub_overview_lists_requests_below_sessions(settings) -> None:
    manager, _codex_manager, _quick_interactions = configured_manager(settings)
    manager.request_backlog.save(title="第一项需求", content="验收：展示。")
    manager._status_cache["sessions"] = ((), utc_now())

    message = manager._format_chub_overview("route", elapsed_ms=10)

    assert "No sessions\n\nRequests\n\nR1 · 第一项需求\n\nWeekly" in message


def test_chub_overview_does_not_report_unreadable_requests_as_empty(settings) -> None:
    manager, _codex_manager, _quick_interactions = configured_manager(settings)
    manager._status_cache["sessions"] = ((), utc_now())
    manager.request_backlog.path.parent.mkdir(parents=True, exist_ok=True)
    manager.request_backlog.path.write_text("invalid", encoding="utf-8")

    message = manager._format_chub_overview("route", elapsed_ms=10)

    assert "Requests\n\nUnavailable" in message
    assert "No requests" not in message


def test_request_archive_is_distinct_from_session_archive(settings) -> None:
    manager, _codex_manager, _quick_interactions = configured_manager(settings)
    manager.request_backlog.save(title="待归档需求", content="验收：归档。")

    result = manager.dispatch(
        message_id="request-archive",
        prompt="archive R1",
        message_type="text",
        correlation_id=None,
        source_ip="100.64.0.21",
        delivery_route=delivery_route(),
    )

    assert result.message == (
        "Archive: Request R1 archived.\n\nRequest · R1 · 待归档需求"
    )
    assert manager.request_backlog.list_active() == ()
    manager.session_archiver.assert_not_called()


def test_request_delete_removes_active_request_without_archiving(settings) -> None:
    manager, _codex_manager, _quick_interactions = configured_manager(settings)
    manager.request_backlog.save(title="待删除需求", content="验收：删除。")

    result = manager.dispatch(
        message_id="request-delete",
        prompt="del R1",
        message_type="text",
        correlation_id=None,
        source_ip="100.64.0.21",
        delivery_route=delivery_route(),
    )

    assert result.message == (
        "Delete: Request R1 deleted.\n\nRequest · R1 · 待删除需求"
    )
    assert manager.request_backlog.list_active() == ()
    manager.session_deleter.assert_not_called()
