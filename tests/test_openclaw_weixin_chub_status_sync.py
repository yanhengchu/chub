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
    "prompt",
    ["Chub。", "CHUB!", "chub？", "chub...", " chub ！ ", "《Chub》"],
)
def test_chub_status_ignores_trailing_punctuation(
    settings: Settings,
    prompt: str,
) -> None:
    manager, _codex_manager, quick_interactions = configured_manager(settings)
    result = manager.dispatch(
        message_id=f"chub-punctuation-{prompt}",
        prompt=prompt,
        message_type="voice",
        correlation_id=None,
        source_ip="100.64.0.21",
        delivery_route=delivery_route(),
    )

    assert result.disposition == "reply"
    assert result.message is not None
    assert re.match(r"Chub · [1-9][0-9]*ms(?:\n|$)", result.message)
    quick_interactions.weixin_task_status_snapshot.assert_called_once()
    quick_interactions.submit.assert_not_called()


@pytest.mark.parametrize(
    "prompt",
    ["CODEX status", "Codex status", "codex status"],
)
def test_codex_status_near_match_remains_a_normal_task_case_insensitively(
    settings: Settings,
    prompt: str,
) -> None:
    manager, _codex_manager, quick_interactions = configured_manager(settings)

    result = manager.dispatch(
        message_id=f"codex-usage-case-near-match-{prompt}",
        prompt=prompt,
        message_type="text",
        correlation_id=None,
        source_ip="100.64.0.21",
        delivery_route=delivery_route(),
    )

    assert result.disposition == "handled"
    assert result.message is None
    quick_interactions.submit.assert_called_once()


@pytest.mark.parametrize(
    "prompt",
    ["sync review the slot design", "chub -s review the slot design"],
)
def test_sync_command_with_business_text_remains_normal_task(
    settings: Settings,
    prompt: str,
) -> None:
    manager, _codex_manager, quick_interactions = configured_manager(settings)

    result = manager.dispatch(
        message_id="chub-short-sync-business-text",
        prompt=prompt,
        message_type="text",
        correlation_id=None,
        source_ip="100.64.0.21",
        delivery_route=delivery_route(),
    )

    assert result.disposition == "handled"
    assert result.message is None
    quick_interactions.submit.assert_called_once()


@pytest.mark.parametrize(
    "prompt",
    [
        "chub sync",
        "CHUB SYNC。",
        "chub -s",
        "CHUB -S。",
        "补充槽位",
        " 补充槽位。 ",
    ],
)
def test_removed_sync_aliases_are_normal_tasks(
    settings: Settings,
    prompt: str,
) -> None:
    manager, _codex_manager, quick_interactions = configured_manager(settings)

    result = manager.dispatch(
        message_id=f"removed-chub-sync-alias-{prompt}",
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
    assert manager._state.submissions[-1].code == "submitted"


@pytest.mark.parametrize(
    "sync_prompt",
    ["sync", "SYNC。", "同步状态", " 状态同步。 "],
)
def test_chub_sync_lists_compatible_sessions_and_marks_current(
    settings: Settings,
    sync_prompt: str,
) -> None:
    manager, codex_manager, quick_interactions = configured_manager(settings)
    manager._state.session_id = "current-session"
    account_reader = MagicMock()
    account_reader.read_account_status.return_value = (
        CodexQuotaData(status="unavailable"),
        CodexTokenUsageData(status="unavailable"),
    )
    manager.codex_account_reader = account_reader
    codex_manager.list_sessions.return_value = [
        CodexSession(
            id="available-session",
            workspace_id="chub",
            workspace_name="Chub",
            cwd="/project",
            title="项目维护",
            permission_mode="full-access",
            status="stopped",
            activity="idle",
        ),
        CodexSession(
            id="current-session",
            workspace_id="chub",
            workspace_name="Chub",
            cwd="/project",
            title="微信 Chub",
            codex_session_id="native-current",
            permission_mode="full-access",
            status="stopped",
            activity="idle",
        ),
        CodexSession(
            id="busy-session",
            workspace_id="chub",
            workspace_name="Chub",
            cwd="/project",
            title="正在排障",
            permission_mode="full-access",
            status="stopped",
            activity="working",
            activity_source="quick",
        ),
        CodexSession(
            id="wrong-workspace",
            workspace_id="home",
            workspace_name="用户目录",
            cwd="/home/user",
            title="不应显示",
            permission_mode="full-access",
            status="stopped",
            activity="idle",
        ),
        CodexSession(
            id="wrong-permission",
            workspace_id="chub",
            workspace_name="Chub",
            cwd="/project",
            title="也不应显示",
            permission_mode="read-only",
            status="stopped",
            activity="idle",
        ),
    ]
    quick_interactions.is_running.side_effect = (
        lambda session_id: session_id == "busy-session"
    )

    result = manager.dispatch(
        message_id=f"codex-status-sessions-{sync_prompt}",
        prompt=sync_prompt,
        message_type="text",
        correlation_id=None,
        source_ip="100.64.0.21",
        delivery_route=delivery_route(),
    )

    assert result.message is not None
    assert result.message.startswith(
        "槽位同步完成：清理 0 · 补充 3 · 当前 3。\n\n"
        "Sessions\n\n"
    )
    assert "S1 · 微信 Chub\n\nAvailable · Current" in result.message
    assert "S2 · 项目维护\n\nAvailable" in result.message
    assert "S3 · 正在排障\n\nBusy" in result.message
    assert result.message.index("S3 · 正在排障") < result.message.index(
        "S1 · 微信 Chub"
    ) < result.message.index("S2 · 项目维护")
    assert result.message.endswith("Weekly 暂不可用")
    assert "不应显示" not in result.message
    persisted = json.loads(
        settings.openclaw.weixin_chub_mode.state_file.read_text(encoding="utf-8")
    )
    assert persisted["submissions"][0]["code"] == "chub_slots_synced"
    assert persisted["submissions"][0]["message"] == result.message
    account_reader.read_account_status.assert_called_once_with(force=True)
    quick_interactions.submit.assert_not_called()


def test_chub_sync_limits_id_sorted_sessions_without_reordering_current(
    settings: Settings,
) -> None:
    manager, codex_manager, _quick_interactions = configured_manager(settings)
    manager._state.session_id = "z-current-session"
    manager.codex_account_reader = MagicMock()
    manager.codex_account_reader.read_account_status.return_value = (
        CodexQuotaData(status="unavailable"),
        CodexTokenUsageData(status="unavailable"),
    )
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
            updated_at=f"2026-08-{index + 1:02d}T00:00:00Z",
        )
        for index in range(10)
    ]
    sessions.append(
        CodexSession(
            id="z-current-session",
            workspace_id="chub",
            workspace_name="Chub",
            cwd="/project",
            title="token=private-value 当前",
            permission_mode="full-access",
            status="stopped",
            activity="idle",
            updated_at="2026-07-01T00:00:00Z",
        )
    )
    codex_manager.list_sessions.return_value = sessions

    result = manager.dispatch(
        message_id="codex-status-limit",
        prompt="sync",
        message_type="text",
        correlation_id=None,
        source_ip="100.64.0.21",
        delivery_route=delivery_route(),
    )

    assert result.message is not None
    assert "private-value" not in result.message
    assert "Available · Current" in result.message
    assert "候选 7" in result.message
    assert "候选 8" not in result.message
    assert "候选 8" not in result.message


def test_chub_sync_hides_unavailable_sessions(settings: Settings) -> None:
    manager, codex_manager, _quick_interactions = configured_manager(settings)
    manager.codex_account_reader = MagicMock()
    manager.codex_account_reader.read_account_status.return_value = (
        CodexQuotaData(status="unavailable"),
        CodexTokenUsageData(status="unavailable"),
    )
    codex_manager.list_sessions.return_value = [
        CodexSession(
            id="broken",
            workspace_id="chub",
            workspace_name="Chub",
            cwd="/project",
            title="不可用会话",
            permission_mode="full-access",
            status="error",
            activity="unknown",
            error="private failure",
        )
    ]

    result = manager.dispatch(
        message_id="codex-status-unavailable",
        prompt="sync",
        message_type="text",
        correlation_id=None,
        source_ip="100.64.0.21",
        delivery_route=delivery_route(),
    )

    assert result.message is not None
    assert "\n\nSessions\n\n暂无已分配 Session\n\n" in result.message
    assert result.message.endswith("Weekly 暂不可用")
    assert "不可用会话" not in result.message


def test_chub_sync_keeps_success_when_usage_lookup_fails(
    settings: Settings,
) -> None:
    manager, codex_manager, _quick_interactions = configured_manager(settings)
    manager.codex_account_reader = MagicMock()
    manager.codex_account_reader.read_account_status.side_effect = RuntimeError(
        "unavailable"
    )
    codex_manager.list_sessions.return_value = [
        CodexSession(
            id="available-session",
            workspace_id="chub",
            workspace_name="Chub",
            cwd="/project",
            title="项目维护",
            permission_mode="full-access",
            status="stopped",
            activity="idle",
        )
    ]

    result = manager.dispatch(
        message_id="chub-sync-usage-failure",
        prompt="sync",
        message_type="text",
        correlation_id=None,
        source_ip="100.64.0.21",
        delivery_route=delivery_route(),
    )

    assert result.message is not None
    assert result.message.startswith(
        "槽位同步完成：清理 0 · 补充 1 · 当前 1。\n\n"
        "Sessions\n\n"
        "S1 · 项目维护\n\nAvailable"
    )
    assert result.message.endswith("Codex 用量查询失败，请稍后重试。")
    assert manager.session_slot_matches(1, "available-session")


def test_chub_sync_retains_configured_unavailable_slot(
    settings: Settings,
) -> None:
    manager, codex_manager, _quick_interactions = configured_manager(settings)
    manager._state.session_slots = [
        WeixinChubModeSessionSlot(slot=3, session_id="broken")
    ]
    manager.codex_account_reader = MagicMock()
    manager.codex_account_reader.read_account_status.return_value = (
        CodexQuotaData(status="unavailable"),
        CodexTokenUsageData(status="unavailable"),
    )
    broken = CodexSession(
        id="broken",
        workspace_id="chub",
        workspace_name="Chub",
        cwd="/project",
        title="故障上下文",
        permission_mode="full-access",
        status="error",
        activity="unknown",
    )
    candidate = broken.model_copy(
        update={"id": "candidate", "title": "等待候选", "status": "stopped", "activity": "idle"}
    )
    codex_manager.list_sessions.return_value = [broken, candidate]

    result = manager.dispatch(
        message_id="codex-status-retain-unavailable",
        prompt="sync",
        message_type="text",
        correlation_id=None,
        source_ip="100.64.0.21",
        delivery_route=delivery_route(),
    )

    assert "S3 · 故障上下文\n\nUnavailable" in (result.message or "")
    assert manager.session_slot_matches(3, "broken")


def test_codex_new_rejects_before_creation_when_nine_slots_are_full(
    settings: Settings,
) -> None:
    manager, codex_manager, _quick_interactions = configured_manager(settings)
    manager._state.session_slots = [
        WeixinChubModeSessionSlot(slot=slot, session_id=f"session-{slot}")
        for slot in range(1, 10)
    ]
    codex_manager.list_sessions.return_value = [
        CodexSession(
            id=f"session-{slot}",
            workspace_id="chub",
            workspace_name="Chub",
            cwd="/project",
            permission_mode="full-access",
            status="stopped",
            activity="idle",
        )
        for slot in range(1, 10)
    ]

    result = manager.dispatch(
        message_id="codex-new-full",
        prompt="Session New",
        message_type="text",
        correlation_id=None,
        source_ip="100.64.0.21",
        delivery_route=delivery_route(),
    )

    assert result.message is not None
    assert "9 个微信 Session 槽位已满" in result.message
    codex_manager.create_session.assert_not_called()


def test_chub_refresh_keeps_cached_overview_when_session_lookup_fails(
    settings: Settings,
) -> None:
    manager, codex_manager, _quick_interactions = configured_manager(settings)
    manager.codex_account_reader = MagicMock()
    manager.codex_account_reader.read_account_status.return_value = (
        CodexQuotaData(status="unavailable"),
        CodexTokenUsageData(status="unavailable"),
    )
    codex_manager.list_sessions.side_effect = RuntimeError("unavailable")

    result = manager.dispatch(
        message_id="codex-status-session-failure",
        prompt="chub",
        message_type="text",
        correlation_id=None,
        source_ip="100.64.0.21",
        delivery_route=delivery_route(),
    )

    assert result.message is not None
    assert "Sessions\n\n暂不可用" in result.message
    assert "异常" not in result.message

def test_codex_status_session_matching_respects_explicit_model_and_effort() -> None:
    configuration = WeixinChubModeRuntimeConfig(
        enabled=True,
        workspace_id="chub",
        permission_mode="full-access",
        model="gpt-test",
        reasoning_effort="high",
    )
    matching = CodexSession(
        id="matching",
        workspace_id="chub",
        workspace_name="Chub",
        cwd="/project",
        permission_mode="full-access",
        model="gpt-test",
        reasoning_effort="high",
    )

    assert WeixinChubModeManager._session_matches_configuration(
        matching,
        configuration,
    )
    assert not WeixinChubModeManager._session_matches_configuration(
        matching.model_copy(update={"model": "different"}),
        configuration,
    )
    assert not WeixinChubModeManager._session_matches_configuration(
        matching.model_copy(update={"reasoning_effort": "medium"}),
        configuration,
    )


def test_codex_status_distinguishes_writer_error_and_unknown_running_session(
    settings: Settings,
) -> None:
    manager, codex_manager, quick_interactions = configured_manager(settings)
    writer_session = CodexSession(
        id="writer",
        workspace_id="chub",
        workspace_name="Chub",
        cwd="/project",
        codex_session_id="native-writer",
        permission_mode="full-access",
        status="stopped",
        activity="idle",
    )
    codex_manager.has_active_writer.return_value = True

    assert manager._codex_session_dispatch_state(writer_session) == "Busy"
    assert manager._codex_session_dispatch_state(
        writer_session.model_copy(
            update={
                "status": "error",
                "error": "private failure",
            }
        )
    ) == "Unavailable"
    codex_manager.has_active_writer.return_value = False
    assert manager._codex_session_dispatch_state(
        writer_session.model_copy(
            update={
                "status": "running",
                "activity": "unknown",
            }
        )
    ) == "Unavailable"
    quick_interactions.submit.assert_not_called()


def test_codex_status_keeps_sessions_available_while_restart_is_pending(
    settings: Settings,
) -> None:
    manager, _codex_manager, quick_interactions = configured_manager(settings)
    quick_interactions.deferred_restart = MagicMock()
    quick_interactions.deferred_restart.pending.return_value = True
    session = CodexSession(
        id="available",
        workspace_id="chub",
        workspace_name="Chub",
        cwd="/project",
        permission_mode="full-access",
        status="stopped",
        activity="idle",
    )

    assert manager._codex_session_dispatch_state(session) == "Available"


def test_chub_refresh_bounds_slow_session_lookup(
    settings: Settings,
) -> None:
    manager, codex_manager, _quick_interactions = configured_manager(settings)
    manager.codex_account_reader = MagicMock()
    manager.codex_account_reader.read_account_status.return_value = (
        CodexQuotaData(status="unavailable"),
        CodexTokenUsageData(status="unavailable"),
    )
    blocker = threading.Event()

    def slow_sessions() -> list[CodexSession]:
        blocker.wait(1)
        return []

    codex_manager.list_sessions.side_effect = slow_sessions

    try:
        with patch(
            "app.services.openclaw_weixin_chub_mode.CODEX_STATUS_TIMEOUT_SECONDS",
            0.01,
        ):
            result = manager.dispatch(
                message_id="codex-status-timeout",
                prompt="chub",
                message_type="text",
                correlation_id=None,
                source_ip="100.64.0.21",
                delivery_route=delivery_route(),
            )
    finally:
        blocker.set()

    assert result.message is not None
    assert "Sessions\n\n暂不可用" in result.message
    assert "异常" not in result.message
