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
        prompt="Session Retry",
        message_type="text",
        correlation_id=None,
        source_ip="100.64.0.21",
        delivery_route=delivery_route(),
    )

    assert result.message == (
        "刚才的任务已重新提交。\n\n"
        "任务摘要：继续检查设备\n\n完成后将原路发送结果。"
    )
    quick_interactions.submit.assert_called_once()
    assert quick_interactions.submit.call_args.args[1] == "继续检查设备"
    assert manager._state.pending_retry is None


@pytest.mark.parametrize(
    "command",
    [
        "Session New Retry。",
        "新建会话执行",
        " 新建会话执行。 ",
        "新建会话执行？！",
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

    assert result.message == (
        "已创建并切换到新的 Session，刚才的任务已重新提交。\n\n"
        "任务摘要：探索另一个问题\n\n完成后将原路发送结果。"
    )
    assert manager.session_id() == "session-2"
    assert quick_interactions.submit.call_args.args[:2] == (
        "session-2",
        "探索另一个问题",
    )
    assert manager._state.pending_retry is None


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
        prompt="新建会话执行",
        message_type="text",
        correlation_id=None,
        source_ip="100.64.0.21",
        delivery_route=delivery_route(),
    )

    assert result.message == "没有可继续执行的任务，请重新发送任务内容。"
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
        prompt="新建会话执行",
        message_type="text",
        correlation_id=None,
        source_ip="100.64.0.21",
        delivery_route=delivery_route(recipient="another-owner@im.wechat"),
    )

    assert result.message == "没有可继续执行的任务，请重新发送任务内容。"
    codex_manager.create_session.assert_not_called()
    quick_interactions.submit.assert_not_called()
    assert manager._state.pending_retry is not None
    assert manager._state.pending_retry.prompt == "私有待继续任务"


@pytest.mark.parametrize(
    "prompt",
    [
        "新建会话执行，检查日志",
        "session new retry: check logs",
    ],
)
def test_codex_new_retry_rejects_attached_task_without_replacing_pending(
    settings: Settings,
    prompt: str,
) -> None:
    manager, codex_manager, quick_interactions = configured_manager(settings)
    manager._state.session_id = "session-1"
    quick_interactions.is_running.return_value = True
    manager.dispatch(
        message_id="busy-before-invalid-retry",
        prompt="需要保留的任务",
        message_type="text",
        correlation_id=None,
        source_ip="100.64.0.21",
        delivery_route=delivery_route(),
    )

    first = manager.dispatch(
        message_id=f"invalid-new-retry-{prompt}",
        prompt=prompt,
        message_type="text",
        correlation_id=None,
        source_ip="100.64.0.21",
        delivery_route=delivery_route(),
    )
    duplicate = manager.dispatch(
        message_id=f"invalid-new-retry-{prompt}",
        prompt=prompt,
        message_type="text",
        correlation_id=None,
        source_ip="100.64.0.21",
        delivery_route=delivery_route(),
    )

    assert first.message == duplicate.message == (
        "“新建会话执行”只用于继续最近一条未提交任务，"
        "请不要附带新正文。"
    )
    assert manager._state.pending_retry is not None
    assert manager._state.pending_retry.prompt == "需要保留的任务"
    codex_manager.create_session.assert_not_called()
    quick_interactions.submit.assert_not_called()


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
        prompt="session new retry",
        message_type="text",
        correlation_id=None,
        source_ip="100.64.0.21",
        delivery_route=delivery_route(),
    )
    duplicate = manager.dispatch(
        message_id="combined-duplicate",
        prompt="session new retry",
        message_type="text",
        correlation_id=None,
        source_ip="100.64.0.21",
        delivery_route=delivery_route(),
    )

    assert "已重新提交" in (first.message or "")
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
        prompt="session retry",
        message_type="text",
        correlation_id=None,
        source_ip="100.64.0.21",
        delivery_route=delivery_route(),
    )

    assert result.message is not None
    assert result.message.startswith("任务提交失败：当前 Session 正在执行")
    assert manager._state.pending_retry is not None
    assert manager._state.pending_retry.prompt == "稍后继续的任务"
    assert manager._state.pending_retry.claimed_by_message_id is None
    quick_interactions.submit.assert_not_called()
