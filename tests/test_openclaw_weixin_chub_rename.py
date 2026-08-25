from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

from app.codex.models import CodexQuotaData, CodexSession, CodexTokenUsageData
from app.core.config import Settings
from app.core.response import ApiError
from app.services.openclaw_weixin_chub_models import WeixinChubModeSessionSlot
from tests.openclaw_weixin_chub_mode_helpers import configured_manager, delivery_route


def _current_session(*, title: str = "旧标题", activity: str = "idle") -> CodexSession:
    return CodexSession(
        session_mode="quick",
        id="session-1",
        workspace_id="chub",
        workspace_name="Chub",
        cwd="/project",
        title=title,
        permission_mode="full-access",
        status="stopped",
        activity=activity,
        activity_source="quick" if activity == "working" else "none",
    )


def _enable_usage_snapshot(manager) -> None:
    manager.codex_account_reader = MagicMock()
    manager.codex_account_reader.read_account_status.return_value = (
        CodexQuotaData(status="unavailable"),
        CodexTokenUsageData(status="unavailable"),
    )


def test_rename_current_session_normalizes_title_and_is_idempotent(
    settings: Settings,
) -> None:
    manager, codex_manager, quick_interactions = configured_manager(settings)
    manager._state.session_id = "session-1"
    manager._state.session_slots = [
        WeixinChubModeSessionSlot(slot=1, session_id="session-1")
    ]
    current = _current_session(activity="working")
    renamed = current.model_copy(update={"title": "新 标题"})
    codex_manager.get_session.return_value = current
    codex_manager.rename_session.return_value = renamed
    codex_manager.list_sessions.return_value = [renamed]
    quick_interactions.weixin_task_status_snapshot.return_value = SimpleNamespace(
        running_tasks=(("session-1", "优化微信指令交互流程"),),
    )
    _enable_usage_snapshot(manager)

    first = manager.dispatch(
        message_id="rename-current-session",
        prompt="rename  新   标题",
        message_type="text",
        correlation_id=None,
        source_ip="100.64.0.21",
        delivery_route=delivery_route(),
    )
    duplicate = manager.dispatch(
        message_id="rename-current-session",
        prompt="rename  新   标题",
        message_type="text",
        correlation_id=None,
        source_ip="100.64.0.21",
        delivery_route=delivery_route(),
    )

    assert first.message is not None
    assert first.message.startswith(
        'Rename: Session 1 renamed to "新 标题".\n\n'
        "Sessions\n\n▶ S1 · 新 标题\n\nTask · 优化微信指令交互流程\n\n"
    )
    assert first.message.endswith("Weekly Unavailable")
    assert duplicate == first
    codex_manager.rename_session.assert_called_once_with("session-1", "新 标题")
    quick_interactions.submit.assert_not_called()


def test_rename_supports_chinese_alias(settings: Settings) -> None:
    manager, codex_manager, _quick_interactions = configured_manager(settings)
    manager._state.session_id = "session-1"
    manager._state.session_slots = [
        WeixinChubModeSessionSlot(slot=3, session_id="session-1")
    ]
    current = _current_session()
    renamed = current.model_copy(update={"title": "项目维护"})
    codex_manager.get_session.return_value = current
    codex_manager.rename_session.return_value = renamed
    codex_manager.list_sessions.return_value = [renamed]
    _enable_usage_snapshot(manager)

    result = manager.dispatch(
        message_id="rename-chinese-alias",
        prompt="rename 项目维护",
        message_type="text",
        correlation_id=None,
        source_ip="100.64.0.21",
        delivery_route=delivery_route(),
    )

    assert result.message is not None
    assert result.message.startswith(
        'Rename: Session 3 renamed to "项目维护".\n\n'
        "Sessions\n\n▶ S3 · 项目维护\n\n"
    )
    assert result.message.endswith("Weekly Unavailable")
    codex_manager.rename_session.assert_called_once_with("session-1", "项目维护")


def test_rename_result_keeps_full_title_and_shortens_session_list(
    settings: Settings,
) -> None:
    settings.openclaw.weixin_chub_mode.session_name_max_width = 30
    manager, codex_manager, _quick_interactions = configured_manager(settings)
    manager._state.session_id = "session-1"
    manager._state.session_slots = [
        WeixinChubModeSessionSlot(slot=1, session_id="session-1")
    ]
    current = _current_session()
    title = "标" * 27
    renamed = current.model_copy(update={"title": title})
    codex_manager.get_session.return_value = current
    codex_manager.rename_session.return_value = renamed
    codex_manager.list_sessions.return_value = [renamed]
    _enable_usage_snapshot(manager)

    result = manager.dispatch(
        message_id="rename-long-display-title",
        prompt=f"rename {title}",
        message_type="text",
        correlation_id=None,
        source_ip="100.64.0.21",
        delivery_route=delivery_route(),
    )

    assert result.message is not None
    assert result.message.startswith(f'Rename: Session 1 renamed to "{title}".')
    assert f"▶ S1 · {'标' * 14}…" in result.message
    assert result.message.endswith("Weekly Unavailable")


def test_rename_rejects_missing_or_invalid_title(settings: Settings) -> None:
    manager, codex_manager, quick_interactions = configured_manager(settings)

    missing = manager.dispatch(
        message_id="rename-missing-title",
        prompt="rename",
        message_type="text",
        correlation_id=None,
        source_ip="100.64.0.21",
        delivery_route=delivery_route(),
    )
    assert missing.message is not None
    assert "Task · rename" in missing.message
    quick_interactions.submit.assert_called_once()

    long_title = manager.dispatch(
        message_id="rename-long-title",
        prompt=f"rename {'标' * 49}",
        message_type="text",
        correlation_id=None,
        source_ip="100.64.0.21",
        delivery_route=delivery_route(),
    )
    assert long_title.message is not None
    assert long_title.message == "Usage: rename <title> (maximum 48 characters)."

    codex_manager.rename_session.assert_not_called()


def test_rename_requires_current_session(settings: Settings) -> None:
    manager, codex_manager, quick_interactions = configured_manager(settings)

    result = manager.dispatch(
        message_id="rename-without-current-session",
        prompt="rename 新标题",
        message_type="text",
        correlation_id=None,
        source_ip="100.64.0.21",
        delivery_route=delivery_route(),
    )

    assert result.message is not None
    assert result.message.startswith(
        "Rename: Not completed because no Session is selected.\n\nNo sessions\n\n"
    )
    assert result.message.endswith("Weekly Unavailable")
    codex_manager.rename_session.assert_not_called()
    quick_interactions.submit.assert_not_called()


def test_rename_failure_keeps_command_out_of_normal_submission(
    settings: Settings,
) -> None:
    manager, codex_manager, quick_interactions = configured_manager(settings)
    manager._state.session_id = "session-1"
    manager._state.session_slots = [
        WeixinChubModeSessionSlot(slot=1, session_id="session-1")
    ]
    codex_manager.get_session.return_value = _current_session()
    codex_manager.rename_session.side_effect = ApiError(
        503,
        "codex_session_rename_failed",
        "rename failed",
    )

    result = manager.dispatch(
        message_id="rename-failed",
        prompt="rename 新标题",
        message_type="text",
        correlation_id=None,
        source_ip="100.64.0.21",
        delivery_route=delivery_route(),
    )

    assert result.message is not None
    assert result.message.startswith(
        "Rename: Failed. The current title was not changed. Try again later."
        "\n\nNo sessions\n\n"
    )
    assert result.message.endswith("Weekly Unavailable")
    quick_interactions.submit.assert_not_called()


def test_rename_external_writer_explains_how_to_recover(
    settings: Settings,
) -> None:
    manager, codex_manager, quick_interactions = configured_manager(settings)
    manager._state.session_id = "session-1"
    manager._state.session_slots = [
        WeixinChubModeSessionSlot(slot=1, session_id="session-1")
    ]
    codex_manager.get_session.return_value = _current_session()
    codex_manager.rename_session.side_effect = ApiError(
        409,
        "codex_session_writer_active",
        "This is open in another app, close it there to continue here.",
    )

    result = manager.dispatch(
        message_id="rename-external-writer",
        prompt="rename 新标题",
        message_type="text",
        correlation_id=None,
        source_ip="100.64.0.21",
        delivery_route=delivery_route(),
    )

    assert result.message is not None
    assert result.message.startswith(
        "Rename: Not completed. This is open in another app, "
        "close it there to continue here.\n\nNo sessions\n\n"
    )
    assert result.message.endswith("Weekly Unavailable")
    quick_interactions.submit.assert_not_called()
