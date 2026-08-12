from __future__ import annotations

import json
import stat
import threading
from datetime import datetime
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
from app.services.openclaw_weixin_chub_mode import (
    MAX_STATE_BYTES,
    WeixinChubModeManager,
    WeixinChubModeRuntimeConfig,
    WeixinChubModeState,
    WeixinChubModeSubmission,
)


def delivery_route(
    account_id: str = "weixin-account",
    recipient: str = "owner@im.wechat",
) -> QuickInteractionWeixinRoute:
    return QuickInteractionWeixinRoute(
        account_id=account_id,
        recipient=recipient,
    )


@pytest.fixture(autouse=True)
def inject_default_delivery_route(monkeypatch) -> None:
    original = WeixinChubModeManager.submit

    def submit_with_route(self, *args, delivery_route=None, **kwargs):
        return original(
            self,
            *args,
            delivery_route=delivery_route or globals()["delivery_route"](),
            **kwargs,
        )

    monkeypatch.setattr(WeixinChubModeManager, "submit", submit_with_route)


def configured_manager(
    settings: Settings,
) -> tuple[WeixinChubModeManager, MagicMock, MagicMock]:
    settings.openclaw.weixin_chub_mode.enabled = True
    settings.openclaw.quick_interaction_completion.enabled = True
    settings.openclaw.quick_interaction_completion.weixin_recipient = "recipient"
    codex_manager = MagicMock()
    codex_manager.workspaces.return_value = [
        WorkspaceInfo(id="chub", name="Chub", path="/project", available=True)
    ]
    codex_manager.available.return_value = True
    codex_manager.create_session.return_value = SimpleNamespace(id="session-1")
    codex_manager.get_session.return_value = CodexSession(
        id="session-1",
        workspace_id="chub",
        workspace_name="Chub",
        cwd="/project",
        permission_mode="full-access",
        status="stopped",
        activity="idle",
    )
    codex_manager.has_active_writer.return_value = False
    codex_manager.wait_for_writer_release.return_value = True
    quick_interactions = MagicMock()
    quick_interactions.deferred_restart = None
    quick_interactions.is_running.return_value = False
    quick_interactions.submit.return_value = SimpleNamespace(id="task-1")
    return (
        WeixinChubModeManager(
            settings,
            codex_manager,
            quick_interactions,
            MagicMock(return_value=None),
        ),
        codex_manager,
        quick_interactions,
    )


def test_dispatch_submits_with_chub_owned_reply(
    settings: Settings,
) -> None:
    manager, _codex_manager, quick_interactions = configured_manager(settings)

    with patch(
        "app.services.openclaw_weixin_chub_mode.write_operation"
    ) as write_operation:
        result = manager.dispatch(
            message_id="dispatch-message-1",
            prompt="检查设备状态",
            message_type="text",
            correlation_id="correlation-1",
            source_ip="100.64.0.21",
            delivery_route=delivery_route(),
        )

    assert result.protocol_version == 3
    assert result.disposition == "reply"
    assert result.message == (
        "任务已提交\n\n任务摘要：检查设备状态\n\n完成后将原路发送结果。"
    )
    quick_interactions.submit.assert_called_once()
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
    persisted = json.loads(
        settings.openclaw.weixin_chub_mode.state_file.read_text(encoding="utf-8")
    )
    assert persisted["submissions"][0]["http_status"] == 200


@pytest.mark.parametrize(
    "prompt",
    ["检查任务状态", "任务状态", "查询任务结果", "任务结果"],
)
def test_dispatch_routes_fixed_task_status_prompts_without_codex(
    settings: Settings,
    prompt: str,
) -> None:
    manager, _codex_manager, quick_interactions = configured_manager(settings)
    quick_interactions.check_weixin_task_status.return_value = SimpleNamespace(
        outcome="running",
        task=SimpleNamespace(summary="设备状态检查"),
    )

    result = manager.dispatch(
        message_id=f"status-{prompt}",
        prompt=f"  {prompt}  ",
        message_type="text",
        correlation_id=None,
        source_ip="100.64.0.21",
        delivery_route=delivery_route(),
    )

    assert result.disposition == "reply"
    assert result.message == "任务正在执行\n\n任务摘要：设备状态检查"
    quick_interactions.check_weixin_task_status.assert_called_once()
    quick_interactions.submit.assert_not_called()


def test_dispatch_retries_original_notification_and_suppresses_extra_reply(
    settings: Settings,
) -> None:
    manager, _codex_manager, quick_interactions = configured_manager(settings)
    quick_interactions.check_weixin_task_status.return_value = SimpleNamespace(
        outcome="notification_queued",
        task=SimpleNamespace(summary="设备状态检查"),
    )

    result = manager.dispatch(
        message_id="status-notification-sent",
        prompt="检查任务状态",
        message_type="text",
        correlation_id=None,
        source_ip="100.64.0.21",
        delivery_route=delivery_route(),
    )

    assert result.disposition == "handled"
    assert result.message is None
    quick_interactions.submit.assert_not_called()


def test_dispatch_summarizes_parallel_weixin_tasks_and_notification_retry(
    settings: Settings,
) -> None:
    manager, _codex_manager, quick_interactions = configured_manager(settings)
    quick_interactions.check_weixin_task_status.return_value = SimpleNamespace(
        outcome="running",
        task=SimpleNamespace(summary="任务一"),
        tasks=(
            SimpleNamespace(summary="任务一"),
            SimpleNamespace(summary="任务二"),
        ),
        notification_outcome="notification_queued",
    )

    result = manager.dispatch(
        message_id="status-parallel",
        prompt="检查任务状态",
        message_type="text",
        correlation_id=None,
        source_ip="100.64.0.21",
        delivery_route=delivery_route(),
    )

    assert result.disposition == "reply"
    assert result.message == (
        "当前有 2 个任务正在执行：\n\n"
        "1. 任务一\n"
        "2. 任务二\n\n"
        "另有已结束任务，结果将原路补发。"
    )


def test_dispatch_routes_codex_status_without_submitting_task(
    settings: Settings,
) -> None:
    manager, _codex_manager, quick_interactions = configured_manager(settings)
    account_reader = MagicMock()
    quota = CodexQuotaData(
        status="available",
        windows=[
            CodexQuotaWindow(
                remaining_percent=41,
                window_duration_minutes=7 * 24 * 60,
                resets_at="2026-08-18T00:00:00Z",
            )
        ],
    )
    usage = CodexTokenUsageData(
        status="available",
        daily_usage=[
            {
                "start_date": datetime.now().astimezone().date(),
                "tokens": 123_456,
            }
        ],
    )
    account_reader.read_account_status.return_value = (quota, usage)
    manager.codex_account_reader = account_reader

    result = manager.dispatch(
        message_id="codex-usage-1",
        prompt="  codex  ",
        message_type="text",
        correlation_id=None,
        source_ip="100.64.0.21",
        delivery_route=delivery_route(),
    )

    assert result.disposition == "reply"
    expected_date = datetime.now().astimezone().strftime("%m-%d")
    assert result.message == (
        f"Codex Usage: Weekly 41% left · Daily tokens 123.5K ({expected_date})\n"
        "Active sessions: None"
    )
    account_reader.read_account_status.assert_called_once_with(force=True)
    quick_interactions.submit.assert_not_called()


def test_codex_status_route_returns_partial_result_when_daily_usage_is_unavailable(
    settings: Settings,
) -> None:
    manager, _codex_manager, quick_interactions = configured_manager(settings)
    account_reader = MagicMock()
    quota = CodexQuotaData(
        status="available",
        windows=[
            CodexQuotaWindow(
                remaining_percent=41,
                window_duration_minutes=7 * 24 * 60,
                resets_at="2026-08-18T00:00:00Z",
            )
        ],
    )
    usage = CodexTokenUsageData(
        status="unavailable",
    )
    account_reader.read_account_status.return_value = (quota, usage)
    manager.codex_account_reader = account_reader

    result = manager.dispatch(
        message_id="codex-usage-partial",
        prompt="codex",
        message_type="text",
        correlation_id=None,
        source_ip="100.64.0.21",
        delivery_route=delivery_route(),
    )

    assert result.message == (
        "Codex Usage: Weekly 41% left · Daily tokens 暂不可用\n"
        "Active sessions: None"
    )
    quick_interactions.submit.assert_not_called()


def test_codex_status_does_not_report_missing_daily_bucket_as_zero() -> None:
    message = WeixinChubModeManager._codex_usage_message(
        CodexQuotaData(status="unavailable"),
        CodexTokenUsageData(status="available", daily_usage=[]),
    )

    assert message == (
        "Codex Usage: Weekly 暂不可用 · Daily tokens 暂不可用"
    )


def test_codex_status_compacts_large_token_count() -> None:
    assert WeixinChubModeManager._compact_token_count(81_805_470) == "81.8M"


def test_codex_status_route_requires_exact_prompt(settings: Settings) -> None:
    manager, _codex_manager, quick_interactions = configured_manager(settings)

    result = manager.dispatch(
        message_id="codex-usage-near-match",
        prompt="codex status",
        message_type="text",
        correlation_id=None,
        source_ip="100.64.0.21",
        delivery_route=delivery_route(),
    )

    assert result.message.startswith("任务已提交")
    quick_interactions.submit.assert_called_once()


def test_codex_status_lists_compatible_sessions_and_marks_current(
    settings: Settings,
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
        message_id="codex-status-sessions",
        prompt="codex",
        message_type="text",
        correlation_id=None,
        source_ip="100.64.0.21",
        delivery_route=delivery_route(),
    )

    assert result.message is not None
    assert "Active sessions:\n1. 项目维护 · Available" in result.message
    assert "2. 正在排障 · Busy" in result.message
    assert "3. 微信 Chub [Current] · Available" in result.message
    assert "不应显示" not in result.message
    persisted = json.loads(
        settings.openclaw.weixin_chub_mode.state_file.read_text(encoding="utf-8")
    )
    assert persisted["submissions"][0]["code"] == "codex_status_checked"
    quick_interactions.submit.assert_not_called()


def test_codex_status_limits_id_sorted_sessions_without_reordering_current(
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
        prompt="codex",
        message_type="text",
        correlation_id=None,
        source_ip="100.64.0.21",
        delivery_route=delivery_route(),
    )

    assert result.message is not None
    assert "private-value" not in result.message
    assert "[Current]" not in result.message
    assert "另有 1 个" in result.message


def test_codex_status_hides_unavailable_sessions(settings: Settings) -> None:
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
        prompt="codex",
        message_type="text",
        correlation_id=None,
        source_ip="100.64.0.21",
        delivery_route=delivery_route(),
    )

    assert result.message is not None
    assert result.message.endswith("Active sessions: None")
    assert "不可用会话" not in result.message


def test_codex_status_keeps_usage_when_session_lookup_fails(
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
        prompt="codex",
        message_type="text",
        correlation_id=None,
        source_ip="100.64.0.21",
        delivery_route=delivery_route(),
    )

    assert result.message == (
        "Codex Usage: Weekly 暂不可用 · Daily tokens 暂不可用\n"
        "Active sessions: 暂不可用"
    )


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


def test_codex_status_marks_sessions_unavailable_while_restart_is_pending(
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

    assert manager._codex_session_dispatch_state(session) == "Unavailable"


def test_codex_status_bounds_slow_session_lookup(
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
                prompt="codex",
                message_type="text",
                correlation_id=None,
                source_ip="100.64.0.21",
                delivery_route=delivery_route(),
            )
    finally:
        blocker.set()

    assert result.message is not None
    assert result.message.endswith("Active sessions: 暂不可用")


def test_codex_switch_uses_id_order_and_allows_busy_target(
    settings: Settings,
) -> None:
    manager, codex_manager, quick_interactions = configured_manager(settings)
    manager._state.session_id = "a-current"
    sessions = [
        CodexSession(
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
        prompt="codex switch",
        message_type="text",
        correlation_id=None,
        source_ip="100.64.0.21",
        delivery_route=delivery_route(),
    )

    assert result.message is not None
    assert result.message.startswith(
        "Codex Usage: Weekly 暂不可用 · Daily tokens 暂不可用\n"
    )
    assert "2. 第二项 [Current] · Busy" in result.message
    assert "已切换" not in result.message
    assert manager.session_id() == "b-busy"
    quick_interactions.submit.assert_not_called()


def test_codex_switch_without_current_uses_first_visible_session(
    settings: Settings,
) -> None:
    manager, codex_manager, _quick_interactions = configured_manager(settings)
    unavailable = CodexSession(
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
    codex_manager.get_session.return_value = available
    manager.codex_account_reader = MagicMock()
    manager.codex_account_reader.read_account_status.return_value = (
        CodexQuotaData(status="unavailable"),
        CodexTokenUsageData(status="unavailable"),
    )

    result = manager.dispatch(
        message_id="codex-switch-first",
        prompt="codex switch",
        message_type="text",
        correlation_id=None,
        source_ip="100.64.0.21",
        delivery_route=delivery_route(),
    )

    assert result.message is not None
    assert "1. 可用会话 [Current] · Available" in result.message
    assert manager.session_id() == "b-available"


def test_codex_switch_number_uses_fresh_visible_list(settings: Settings) -> None:
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
        for index in range(1, 4)
    ]
    by_id = {session.id: session for session in sessions}
    codex_manager.list_sessions.return_value = list(reversed(sessions))
    codex_manager.get_session.side_effect = lambda session_id: by_id[session_id]
    manager.codex_account_reader = MagicMock()
    manager.codex_account_reader.read_account_status.return_value = (
        CodexQuotaData(status="unavailable"),
        CodexTokenUsageData(status="unavailable"),
    )

    result = manager.dispatch(
        message_id="codex-switch-number",
        prompt=" codex   switch   2 ",
        message_type="text",
        correlation_id=None,
        source_ip="100.64.0.21",
        delivery_route=delivery_route(),
    )

    assert result.message is not None
    assert "2. 候选 2 [Current] · Available" in result.message
    assert manager.session_id() == "session-2"


def test_codex_switch_uses_one_deadline_and_reuses_session_scan(
    settings: Settings,
) -> None:
    manager, codex_manager, _quick_interactions = configured_manager(settings)
    manager._state.session_id = "session-1"
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
    by_id = {session.id: session for session in sessions}
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
                prompt="codex switch",
                message_type="text",
                correlation_id=None,
                source_ip="100.64.0.21",
                delivery_route=delivery_route(),
            )
    finally:
        account_release.set()

    assert result.message is not None
    assert result.message.startswith("Codex 用量查询失败，请稍后重试。\n")
    assert "2. 候选 2 [Current] · Available" in result.message
    assert manager.session_id() == "session-2"
    codex_manager.list_sessions.assert_called_once()


def test_codex_switch_out_of_range_returns_fresh_list_without_changing_binding(
    settings: Settings,
) -> None:
    manager, codex_manager, _quick_interactions = configured_manager(settings)
    manager._state.session_id = "session-1"
    session = CodexSession(
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
        prompt="codex switch 2",
        message_type="text",
        correlation_id=None,
        source_ip="100.64.0.21",
        delivery_route=delivery_route(),
    )

    assert result.message is not None
    assert result.message.startswith("编号无效。\nActive sessions:")
    assert "1. 当前会话 [Current] · Available" in result.message
    assert manager.session_id() == "session-1"
    codex_manager.get_session.assert_not_called()


@pytest.mark.parametrize(
    "prompt",
    ["codex switch 0", "codex switch -1", "codex switch abc", "codex switch 1 extra"],
)
def test_codex_switch_invalid_usage_is_not_submitted(
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
    assert result.message.startswith("用法：发送 codex switch")
    codex_manager.list_sessions.assert_not_called()
    quick_interactions.submit.assert_not_called()


def test_codex_switch_rejects_oversized_numeric_index(settings: Settings) -> None:
    manager, codex_manager, quick_interactions = configured_manager(settings)

    result = manager.dispatch(
        message_id="invalid-large-switch-index",
        prompt="codex switch " + ("9" * 5_000),
        message_type="text",
        correlation_id=None,
        source_ip="100.64.0.21",
        delivery_route=delivery_route(),
    )

    assert result.message is not None
    assert result.message.startswith("用法：发送 codex switch")
    codex_manager.list_sessions.assert_not_called()
    quick_interactions.submit.assert_not_called()


def test_codex_switch_write_failure_keeps_previous_binding(
    settings: Settings,
) -> None:
    manager, codex_manager, _quick_interactions = configured_manager(settings)
    manager._state.session_id = "session-1"
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
    by_id = {session.id: session for session in sessions}
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
        prompt="codex switch",
        message_type="text",
        correlation_id=None,
        source_ip="100.64.0.21",
        delivery_route=delivery_route(),
    )

    assert result.message == "任务提交失败：Chub 当前状态不可用，请稍后重试。"
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
    codex_manager.list_sessions.return_value = sessions
    codex_manager.get_session.side_effect = lambda session_id: by_id[session_id]
    manager.codex_account_reader = MagicMock()
    manager.codex_account_reader.read_account_status.return_value = (
        CodexQuotaData(status="unavailable"),
        CodexTokenUsageData(status="unavailable"),
    )

    first = manager.dispatch(
        message_id="duplicate-switch",
        prompt="codex switch",
        message_type="text",
        correlation_id=None,
        source_ip="100.64.0.21",
        delivery_route=delivery_route(),
    )
    duplicate = manager.dispatch(
        message_id="duplicate-switch",
        prompt="codex switch",
        message_type="text",
        correlation_id=None,
        source_ip="100.64.0.21",
        delivery_route=delivery_route(),
    )

    assert duplicate == first
    assert manager.session_id() == "session-2"
    codex_manager.list_sessions.assert_called_once()
    manager.codex_account_reader.read_account_status.assert_called_once_with(force=True)


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


def test_status_check_persists_interrupted_reservation_before_side_effect(
    settings: Settings,
) -> None:
    manager, _codex_manager, quick_interactions = configured_manager(settings)

    def check_status(*_args, **_kwargs):
        persisted = json.loads(
            settings.openclaw.weixin_chub_mode.state_file.read_text(encoding="utf-8")
        )
        record = persisted["submissions"][0]
        assert record["status"] == "reserved"
        assert record["code"] == "submission_interrupted"
        return SimpleNamespace(outcome="empty", task=None)

    quick_interactions.check_weixin_task_status.side_effect = check_status

    result = manager.dispatch(
        message_id="status-reserved-first",
        prompt="检查任务状态",
        message_type="text",
        correlation_id=None,
        source_ip="100.64.0.21",
        delivery_route=delivery_route(),
    )

    assert result.message == "当前没有执行中或待通知的任务。"
    persisted = json.loads(
        settings.openclaw.weixin_chub_mode.state_file.read_text(encoding="utf-8")
    )
    assert persisted["submissions"][0]["status"] == "routed"


@pytest.mark.parametrize(
    ("outcome", "message"),
    [
        ("notification_sending", "任务已结束，结果正在原路发送。"),
        ("notification_failed", "任务已结束，但结果通知失败，请稍后再次检查。"),
        ("empty", "当前没有执行中或待通知的任务。"),
    ],
)
def test_dispatch_returns_one_status_check_result(
    settings: Settings,
    outcome: str,
    message: str,
) -> None:
    manager, _codex_manager, quick_interactions = configured_manager(settings)
    quick_interactions.check_weixin_task_status.return_value = SimpleNamespace(
        outcome=outcome,
        task=None,
    )

    result = manager.dispatch(
        message_id=f"status-{outcome}",
        prompt="任务状态",
        message_type="text",
        correlation_id=None,
        source_ip="100.64.0.21",
        delivery_route=delivery_route(),
    )

    assert result.disposition == "reply"
    assert result.message == message


def test_duplicate_status_check_replays_without_rechecking(
    settings: Settings,
) -> None:
    manager, _codex_manager, quick_interactions = configured_manager(settings)
    quick_interactions.check_weixin_task_status.return_value = SimpleNamespace(
        outcome="empty",
        task=None,
    )

    first = manager.dispatch(
        message_id="status-duplicate",
        prompt="检查任务状态",
        message_type="text",
        correlation_id=None,
        source_ip="100.64.0.21",
        delivery_route=delivery_route(),
    )
    duplicate = manager.dispatch(
        message_id="status-duplicate",
        prompt="任务结果",
        message_type="text",
        correlation_id=None,
        source_ip="100.64.0.21",
        delivery_route=delivery_route(),
    )

    assert duplicate == first
    quick_interactions.check_weixin_task_status.assert_called_once()


def test_non_exact_status_prompt_is_submitted_as_normal_task(
    settings: Settings,
) -> None:
    manager, _codex_manager, quick_interactions = configured_manager(settings)

    result = manager.dispatch(
        message_id="status-near-miss",
        prompt="检查任务状态 123",
        message_type="text",
        correlation_id=None,
        source_ip="100.64.0.21",
        delivery_route=delivery_route(),
    )

    assert result.disposition == "reply"
    quick_interactions.check_weixin_task_status.assert_not_called()
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
    assert result.message == "任务提交失败：已有微信任务正在执行，请等待完成后重试。"
    quick_interactions.submit.assert_not_called()


def test_duplicate_dispatch_is_logged_without_resubmitting(
    settings: Settings,
) -> None:
    manager, _codex_manager, quick_interactions = configured_manager(settings)
    manager.dispatch(
        message_id="dispatch-duplicate-1",
        prompt="检查设备状态",
        message_type="text",
        correlation_id=None,
        source_ip="100.64.0.21",
        delivery_route=delivery_route(),
    )

    with patch(
        "app.services.openclaw_weixin_chub_mode.write_operation"
    ) as write_operation:
        result = manager.dispatch(
            message_id="dispatch-duplicate-1",
            prompt="重复投递",
            message_type="text",
            correlation_id=None,
            source_ip="100.64.0.21",
            delivery_route=delivery_route(),
        )

    assert result.message == "该消息已处理，任务不会重复执行。"
    assert quick_interactions.submit.call_count == 1
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


def test_dispatch_builds_bounded_voice_reply_in_chub(
    settings: Settings,
) -> None:
    manager, _codex_manager, _quick_interactions = configured_manager(settings)
    transcript = "语音内容" * 1_000

    result = manager.dispatch(
        message_id="dispatch-voice-1",
        prompt=transcript,
        message_type="voice",
        correlation_id=None,
        source_ip="100.64.0.21",
        delivery_route=delivery_route(),
    )

    assert result.protocol_version == 3
    assert result.disposition == "reply"
    assert len(result.message or "") == 3_000
    assert result.message is not None
    assert "语音识别内容：\n语音内容" in result.message
    assert result.message.endswith("（语音识别内容过长，已截断）")


def test_submit_creates_one_private_session_and_replays_duplicate(
    settings: Settings,
) -> None:
    manager, codex_manager, quick_interactions = configured_manager(settings)

    first = manager.submit(
        message_id="message-1",
        prompt="检查设备状态",
        correlation_id="correlation-1",
        source_ip="100.64.0.21",
    )
    duplicate = manager.submit(
        message_id="message-1",
        prompt="不会再次执行",
        correlation_id="correlation-2",
        source_ip="100.64.0.21",
    )

    assert first.accepted is True
    assert first.duplicate is False
    assert first.new_session is True
    assert first.task_summary == "检查设备状态"
    assert duplicate.duplicate is True
    assert duplicate.task_summary is None
    assert duplicate.message == first.message
    codex_manager.create_session.assert_called_once_with(
        "chub",
        "full-access",
        None,
        None,
    )
    codex_manager.set_initial_quick_interaction_title.assert_called_once_with(
        "session-1",
        "微信 Chub",
    )
    quick_interactions.submit.assert_called_once_with(
        "session-1",
        "检查设备状态",
        operation_id=quick_interactions.submit.call_args.kwargs["operation_id"],
        source_ip="100.64.0.21",
        notification_route=delivery_route(),
    )
    state_file = settings.openclaw.weixin_chub_mode.state_file
    assert stat.S_IMODE(state_file.stat().st_mode) == 0o600
    state_text = state_file.read_text(encoding="utf-8")
    assert "检查设备状态" not in state_text
    assert "weixin-account" not in state_text
    assert "owner@im.wechat" not in state_text
    persisted = json.loads(state_text)
    assert persisted["session_id"] == "session-1"
    assert persisted["submissions"][0]["task_id"] == "task-1"


def test_submit_rejects_invalid_delivery_route_before_codex(
    settings: Settings,
) -> None:
    manager, codex_manager, quick_interactions = configured_manager(settings)
    manager.route_validator = MagicMock(return_value="原消息的 ClawBot 当前不可用。")

    with pytest.raises(ApiError) as error:
        manager.submit(
            message_id="message-route-invalid",
            prompt="检查设备",
            correlation_id=None,
            source_ip="100.64.0.21",
        )

    assert error.value.code == "weixin_chub_mode_delivery_route_invalid"
    codex_manager.create_session.assert_not_called()
    quick_interactions.submit.assert_not_called()


def test_duplicate_message_with_different_route_is_rejected(
    settings: Settings,
) -> None:
    manager, _codex_manager, quick_interactions = configured_manager(settings)
    manager.submit(
        message_id="message-route-conflict",
        prompt="检查设备",
        correlation_id=None,
        source_ip="100.64.0.21",
    )

    with pytest.raises(ApiError) as error:
        manager.submit(
            message_id="message-route-conflict",
            prompt="不能重复执行",
            correlation_id=None,
            source_ip="100.64.0.21",
            delivery_route=delivery_route(recipient="other@im.wechat"),
        )

    assert error.value.code == "weixin_chub_mode_message_conflict"
    quick_interactions.submit.assert_called_once()


def test_mode_readiness_no_longer_requires_global_recipient(
    settings: Settings,
) -> None:
    manager, _codex_manager, _quick_interactions = configured_manager(settings)
    settings.openclaw.quick_interaction_completion.weixin_recipient = None

    status = manager.status()

    assert status.ready is True


def test_submit_reuses_session_when_defaults_resolve_to_effective_model(
    settings: Settings,
) -> None:
    manager, codex_manager, quick_interactions = configured_manager(settings)
    codex_manager.get_session.return_value = CodexSession(
        id="session-1",
        workspace_id="chub",
        workspace_name="Chub",
        cwd="/project",
        permission_mode="full-access",
        model="gpt-effective-default",
        reasoning_effort="medium",
        status="stopped",
        activity="idle",
    )

    first = manager.submit(
        message_id="message-1",
        prompt="第一条任务",
        correlation_id=None,
        source_ip="100.64.0.21",
    )
    second = manager.submit(
        message_id="message-2",
        prompt="第二条任务",
        correlation_id=None,
        source_ip="100.64.0.21",
    )

    assert first.new_session is True
    assert second.new_session is False
    codex_manager.create_session.assert_called_once()
    assert quick_interactions.submit.call_count == 2
    assert manager.session_id() == "session-1"


def test_submit_replaces_session_when_explicit_model_no_longer_matches(
    settings: Settings,
) -> None:
    settings.openclaw.weixin_chub_mode.model = "configured-model"
    manager, codex_manager, quick_interactions = configured_manager(settings)
    manager._state.session_id = "old-session"
    codex_manager.get_session.return_value = CodexSession(
        id="old-session",
        workspace_id="chub",
        workspace_name="Chub",
        cwd="/project",
        permission_mode="full-access",
        model="different-model",
        reasoning_effort="medium",
    )
    codex_manager.create_session.return_value = SimpleNamespace(id="new-session")
    quick_interactions.submit.return_value = SimpleNamespace(id="task-1")

    result = manager.submit(
        message_id="message-1",
        prompt="检查模型配置",
        correlation_id=None,
        source_ip="100.64.0.21",
    )

    assert result.new_session is True
    codex_manager.create_session.assert_called_once_with(
        "chub",
        "full-access",
        "configured-model",
        None,
    )
    quick_interactions.submit.assert_called_once_with(
        "new-session",
        "检查模型配置",
        operation_id=quick_interactions.submit.call_args.kwargs["operation_id"],
        source_ip="100.64.0.21",
        notification_route=delivery_route(),
    )


def test_submit_logs_dispatch_lifecycle_without_exposing_prompt(
    settings: Settings,
) -> None:
    manager, _codex_manager, _quick_interactions = configured_manager(settings)

    with patch(
        "app.services.openclaw_weixin_chub_mode.write_operation"
    ) as write_operation:
        manager.submit(
            message_id="message-1",
            prompt="不应出现在操作日志",
            correlation_id=None,
            source_ip="100.64.0.21",
        )

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
    assert {entry["target"] for entry in dispatch_entries} == {settings.node.id}
    assert all(
        "不应出现在操作日志" not in str(entry) for entry in dispatch_entries
    )


def test_submit_reclaims_unknown_session_before_quick_interaction(
    settings: Settings,
) -> None:
    manager, codex_manager, quick_interactions = configured_manager(settings)
    manager._state.session_id = "session-1"
    codex_manager.get_session.return_value = CodexSession(
        id="session-1",
        workspace_id="chub",
        workspace_name="Chub",
        cwd="/project",
        codex_session_id="native-session-1",
        permission_mode="full-access",
        status="running",
        activity="unknown",
    )
    reclaimer = MagicMock(
        return_value=SimpleNamespace(status="stopped", activity="idle")
    )
    manager.terminal_reclaimer = reclaimer

    with patch(
        "app.services.openclaw_weixin_chub_mode.write_operation"
    ) as write_operation:
        result = manager.submit(
            message_id="message-unknown",
            prompt="检查设备",
            correlation_id=None,
            source_ip="100.64.0.21",
        )

    assert result.message == "任务已提交，完成后将通过微信发送结果。"
    assert result.task_summary == "检查设备"
    reclaimer.assert_called_once_with("session-1")
    codex_manager.wait_for_writer_release.assert_called_once_with(
        "native-session-1",
        timeout=3.0,
    )
    quick_interactions.submit.assert_called_once()
    reclaim_entries = [
        call.kwargs
        for call in write_operation.call_args_list
        if call.kwargs["action"] == "weixin_chub_mode_session_reclaim"
    ]
    assert [entry["status"] for entry in reclaim_entries] == [
        "requested",
        "started",
        "succeeded",
    ]


def test_submit_rejects_unknown_session_when_writer_does_not_release(
    settings: Settings,
) -> None:
    manager, codex_manager, quick_interactions = configured_manager(settings)
    manager._state.session_id = "session-1"
    codex_manager.get_session.return_value = CodexSession(
        id="session-1",
        workspace_id="chub",
        workspace_name="Chub",
        cwd="/project",
        codex_session_id="native-session-1",
        permission_mode="full-access",
        status="running",
        activity="unknown",
    )
    codex_manager.wait_for_writer_release.return_value = False
    manager.terminal_reclaimer = MagicMock(
        return_value=SimpleNamespace(status="stopped", activity="idle")
    )

    with pytest.raises(ApiError) as error:
        manager.submit(
            message_id="message-writer-active",
            prompt="检查设备",
            correlation_id=None,
            source_ip="100.64.0.21",
        )

    assert error.value.code == "weixin_chub_mode_submission_failed"
    assert "未能安全停止" in error.value.message
    quick_interactions.submit.assert_not_called()


def test_submit_rejects_busy_session_and_replays_same_failure(
    settings: Settings,
) -> None:
    manager, _codex_manager, quick_interactions = configured_manager(settings)
    manager.submit(
        message_id="message-1",
        prompt="首个任务",
        correlation_id=None,
        source_ip="100.64.0.21",
    )
    quick_interactions.is_running.return_value = True

    with pytest.raises(ApiError) as first_error:
        manager.submit(
            message_id="message-2",
            prompt="第二个任务",
            correlation_id=None,
            source_ip="100.64.0.21",
        )
    with pytest.raises(ApiError) as duplicate_error:
        manager.submit(
            message_id="message-2",
            prompt="重复消息",
            correlation_id=None,
            source_ip="100.64.0.21",
        )

    assert first_error.value.code == "weixin_chub_mode_in_progress"
    assert duplicate_error.value.code == "weixin_chub_mode_in_progress"
    assert quick_interactions.submit.call_count == 1


def test_disabled_submission_failure_is_persisted_for_idempotency(
    settings: Settings,
) -> None:
    codex_manager = MagicMock()
    quick_interactions = MagicMock()
    manager = WeixinChubModeManager(
        settings,
        codex_manager,
        quick_interactions,
    )

    for prompt in ("首次消息", "重复消息"):
        with pytest.raises(ApiError) as error:
            manager.submit(
                message_id="message-1",
                prompt=prompt,
                correlation_id=None,
                source_ip="100.64.0.21",
            )
        assert error.value.code == "weixin_chub_mode_mode_disabled"

    quick_interactions.submit.assert_not_called()
    payload = json.loads(
        settings.openclaw.weixin_chub_mode.state_file.read_text(encoding="utf-8")
    )
    assert payload["submissions"][0]["status"] == "rejected"
    assert payload["submissions"][0]["code"] == "mode_disabled"


def test_quick_interaction_failure_replays_same_bounded_error(
    settings: Settings,
) -> None:
    manager, _codex_manager, quick_interactions = configured_manager(settings)
    quick_interactions.submit.side_effect = ApiError(
        409,
        "chub_restart_pending",
        "底层实现细节不应透传。",
    )

    errors = []
    for prompt in ("首次消息", "重复消息"):
        with pytest.raises(ApiError) as error:
            manager.submit(
                message_id="message-1",
                prompt=prompt,
                correlation_id=None,
                source_ip="100.64.0.21",
            )
        errors.append(error.value)

    assert [error.status_code for error in errors] == [409, 409]
    assert [error.code for error in errors] == [
        "weixin_chub_mode_submission_failed",
        "weixin_chub_mode_submission_failed",
    ]
    assert errors[0].message == errors[1].message == "微信任务提交失败。"
    quick_interactions.submit.assert_called_once()


def test_restart_recovers_reserved_submission_as_fixed_failure(
    settings: Settings,
) -> None:
    settings.openclaw.weixin_chub_mode.enabled = True
    state_file = settings.openclaw.weixin_chub_mode.state_file
    state_file.write_text(
        WeixinChubModeState(
            configuration=WeixinChubModeRuntimeConfig(enabled=True),
            submissions=[
                WeixinChubModeSubmission(
                    message_id="message-1",
                    correlation_id=None,
                    operation_id="operation-1",
                    delivery_route_fingerprint=(
                        WeixinChubModeManager._route_fingerprint(delivery_route())
                    ),
                    status="reserved",
                    code="submission_interrupted",
                    message="等待提交。",
                    created_at=utc_now(),
                    updated_at=utc_now(),
                )
            ],
        ).model_dump_json(),
        encoding="utf-8",
    )
    manager = WeixinChubModeManager(settings, MagicMock(), MagicMock())

    with pytest.raises(ApiError) as error:
        manager.submit(
            message_id="message-1",
            prompt="重复消息",
            correlation_id=None,
            source_ip="100.64.0.21",
        )

    assert error.value.code == "weixin_chub_mode_submission_interrupted"
    assert "发送一条新消息重试" in error.value.message


def test_startup_repairs_legacy_success_http_status(
    settings: Settings,
) -> None:
    settings.openclaw.weixin_chub_mode.enabled = True
    state_file = settings.openclaw.weixin_chub_mode.state_file
    state_file.write_text(
        WeixinChubModeState(
            configuration=WeixinChubModeRuntimeConfig(enabled=True),
            submissions=[
                WeixinChubModeSubmission(
                    message_id="legacy-success",
                    correlation_id=None,
                    operation_id="operation-1",
                    delivery_route_fingerprint=(
                        WeixinChubModeManager._route_fingerprint(delivery_route())
                    ),
                    status="submitted",
                    code="submitted",
                    message="任务已提交。",
                    http_status=409,
                    session_id="session-1",
                    task_id="task-1",
                    created_at=utc_now(),
                    updated_at=utc_now(),
                )
            ],
        ).model_dump_json(),
        encoding="utf-8",
    )

    WeixinChubModeManager(settings, MagicMock(), MagicMock())

    payload = json.loads(state_file.read_text(encoding="utf-8"))
    assert payload["submissions"][0]["http_status"] == 200


def test_startup_configuration_change_resets_bound_session(
    settings: Settings,
) -> None:
    settings.openclaw.weixin_chub_mode.enabled = True
    state_file = settings.openclaw.weixin_chub_mode.state_file
    state_file.write_text(
        WeixinChubModeState(
            configuration=WeixinChubModeRuntimeConfig(
                enabled=True,
                workspace_id="home",
            ),
            session_id="old-session",
        ).model_dump_json(),
        encoding="utf-8",
    )

    manager = WeixinChubModeManager(settings, MagicMock(), MagicMock())

    assert manager.configuration().workspace_id == "chub"
    assert manager.session_id() is None


def test_invalid_state_blocks_submission_without_overwriting_file(
    settings: Settings,
) -> None:
    state_file = settings.openclaw.weixin_chub_mode.state_file
    state_file.write_text("not-json", encoding="utf-8")
    manager = WeixinChubModeManager(settings, MagicMock(), MagicMock())

    with pytest.raises(ApiError) as error:
        manager.submit(
            message_id="message-1",
            prompt="检查状态",
            correlation_id=None,
            source_ip="100.64.0.21",
        )

    assert error.value.code == "weixin_chub_mode_state_unavailable"
    assert state_file.read_text(encoding="utf-8") == "not-json"


def test_symlink_state_is_rejected_without_touching_target(
    settings: Settings,
    tmp_path,
) -> None:
    state_file = settings.openclaw.weixin_chub_mode.state_file
    target = tmp_path / "unrelated.json"
    target.write_text("keep", encoding="utf-8")
    state_file.symlink_to(target)

    manager = WeixinChubModeManager(settings, MagicMock(), MagicMock())

    assert manager.status().code == "disabled"
    with pytest.raises(ApiError) as error:
        manager.submit(
            message_id="message-1",
            prompt="检查状态",
            correlation_id=None,
            source_ip="100.64.0.21",
        )
    assert error.value.code == "weixin_chub_mode_state_unavailable"
    assert target.read_text(encoding="utf-8") == "keep"


def test_state_writer_prunes_oldest_records_to_byte_limit(
    settings: Settings,
) -> None:
    manager, _codex_manager, _quick_interactions = configured_manager(settings)
    large = "消" * 450
    state = WeixinChubModeState(
        configuration=manager.configuration(),
        submissions=[
            WeixinChubModeSubmission(
                message_id=f"{index:04d}-{large}",
                correlation_id=large,
                operation_id=f"operation-{index}",
                status="rejected",
                code="submission_failed",
                message=large,
                created_at=utc_now(),
                updated_at=utc_now(),
            )
            for index in range(2_000)
        ],
    )

    manager._write_state(state)

    state_file = settings.openclaw.weixin_chub_mode.state_file
    assert state_file.stat().st_size <= MAX_STATE_BYTES
    persisted = json.loads(state_file.read_text(encoding="utf-8"))
    assert len(persisted["submissions"]) < 2_000
    assert persisted["submissions"][-1]["message_id"].startswith("1999-")


def test_state_failure_after_task_start_fails_closed(
    settings: Settings,
) -> None:
    manager, _codex_manager, quick_interactions = configured_manager(settings)
    original_write = manager._write_state
    write_count = 0

    def fail_final_write(state: WeixinChubModeState) -> None:
        nonlocal write_count
        write_count += 1
        if write_count == 3:
            raise OSError("disk unavailable")
        original_write(state)

    manager._write_state = fail_final_write

    with pytest.raises(ApiError) as error:
        manager.submit(
            message_id="message-1",
            prompt="检查设备状态",
            correlation_id=None,
            source_ip="100.64.0.21",
        )

    assert error.value.code == "weixin_chub_mode_state_unavailable"
    assert "任务已启动" in error.value.message
    quick_interactions.submit.assert_called_once()
    assert manager.status().ready is False
    with pytest.raises(ApiError) as retry_error:
        manager.submit(
            message_id="message-1",
            prompt="不能重复执行",
            correlation_id=None,
            source_ip="100.64.0.21",
        )
    assert retry_error.value.code == "weixin_chub_mode_state_unavailable"
    assert quick_interactions.submit.call_count == 1
