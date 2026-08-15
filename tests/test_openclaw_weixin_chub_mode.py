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
from app.services.openclaw_weixin_chub_mode import (
    MAX_STATE_BYTES,
    WeixinChubModeManager,
    WeixinChubModePendingRetry,
    WeixinChubModeRuntimeConfig,
    WeixinChubModeSessionSlot,
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
    quick_interactions.weixin_session_ids.return_value = set()
    quick_interactions.weixin_task_status_snapshot.return_value = SimpleNamespace(
        running_count=0,
        pending_notification_count=0,
        failed_notification_count=0,
    )
    quick_interactions.submit.return_value = SimpleNamespace(id="task-1")
    manager = WeixinChubModeManager(
            settings,
            codex_manager,
            quick_interactions,
            MagicMock(return_value=None),
        )
    manager.session_archiver = MagicMock()
    manager._status_cache["readiness"] = (manager.status(), utc_now())
    return (
        manager,
        codex_manager,
        quick_interactions,
    )


def enable_restart_command(
    manager: WeixinChubModeManager,
) -> tuple[MagicMock, MagicMock]:
    coordinator = MagicMock()
    coordinator.request.side_effect = lambda **kwargs: SimpleNamespace(
        operation_id=kwargs["operation_id"],
        created=True,
    )
    notifier = MagicMock(
        return_value=SimpleNamespace(status="sent", error=None)
    )
    manager.restart_coordinator = coordinator
    manager.restart_notifier = notifier
    return coordinator, notifier


def test_dispatch_silently_submits_text_task(
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
    assert result.disposition == "handled"
    assert result.message is None
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
    ["chub", "查询状态", "状态查询", "检查状态", "状态检查"],
)
def test_dispatch_routes_chub_status_aliases_to_live_overview(
    settings: Settings,
    prompt: str,
) -> None:
    manager, codex_manager, quick_interactions = configured_manager(settings)
    quick_interactions.weixin_task_status_snapshot.return_value = SimpleNamespace(
        running_count=2,
        pending_notification_count=1,
        failed_notification_count=1,
    )

    result = manager.dispatch(
        message_id=f"status-{prompt}",
        prompt=f"  《{prompt}》。  ",
        message_type="text",
        correlation_id=None,
        source_ip="100.64.0.21",
        delivery_route=delivery_route(),
    )

    assert result.disposition == "reply"
    assert result.message is not None
    assert re.match(r"Chub · [1-9][0-9]*ms(?:\n|$)", result.message)
    assert "1 个任务结果通知失败" in result.message
    assert "Codex\n\nWeekly 暂不可用" in result.message
    assert "执行中 2" not in result.message
    codex_manager.list_sessions.assert_called_once()
    quick_interactions.submit.assert_not_called()


def test_chub_status_message_id_cannot_later_submit_a_normal_task(
    settings: Settings,
) -> None:
    manager, _codex_manager, quick_interactions = configured_manager(settings)

    first = manager.dispatch(
        message_id="shared-message-id",
        prompt="chub",
        message_type="text",
        correlation_id=None,
        source_ip="100.64.0.21",
        delivery_route=delivery_route(),
    )
    duplicate = manager.dispatch(
        message_id="shared-message-id",
        prompt="执行普通任务",
        message_type="text",
        correlation_id=None,
        source_ip="100.64.0.21",
        delivery_route=delivery_route(),
    )

    assert duplicate == first
    quick_interactions.submit.assert_not_called()


def test_chub_status_refreshes_after_state_lock_is_released(settings: Settings) -> None:
    manager, _codex_manager, _quick_interactions = configured_manager(settings)
    result_holder: list[object] = []
    completed = threading.Event()

    def read_status() -> None:
        result_holder.append(
            manager.dispatch(
                message_id="status-while-state-locked",
                prompt="chub",
                message_type="text",
                correlation_id=None,
                source_ip="100.64.0.21",
                delivery_route=delivery_route(),
            )
        )
        completed.set()

    with manager._lock:
        thread = threading.Thread(target=read_status)
        thread.start()
        assert not completed.wait(timeout=0.05)
    assert completed.wait(timeout=1)
    thread.join(timeout=1)

    assert re.match(r"Chub · [1-9][0-9]*ms(?:\n|$)", result_holder[0].message)


def test_chub_overview_failure_completes_ephemeral_dedup(
    settings: Settings,
) -> None:
    manager, _codex_manager, _quick_interactions = configured_manager(settings)
    manager._format_chub_overview = MagicMock(side_effect=RuntimeError("format"))

    first = manager.dispatch(
        message_id="overview-format-failure",
        prompt="chub",
        message_type="text",
        correlation_id=None,
        source_ip="100.64.0.21",
        delivery_route=delivery_route(),
    )
    duplicate = manager.dispatch(
        message_id="overview-format-failure",
        prompt="chub",
        message_type="text",
        correlation_id=None,
        source_ip="100.64.0.21",
        delivery_route=delivery_route(),
    )

    assert first.message == "Chub 状态总览生成失败，请稍后重试。"
    assert duplicate == first


def test_bare_codex_is_submitted_as_normal_task(settings: Settings) -> None:
    manager, _codex_manager, quick_interactions = configured_manager(settings)

    result = manager.dispatch(
        message_id="bare-codex",
        prompt="codex",
        message_type="text",
        correlation_id=None,
        source_ip="100.64.0.21",
        delivery_route=delivery_route(),
    )

    assert result.disposition == "handled"
    assert result.message is None
    quick_interactions.submit.assert_called_once()


def test_chub_refreshes_status_on_every_query(
    settings: Settings,
) -> None:
    manager, codex_manager, quick_interactions = configured_manager(settings)
    manager.system_status_reader = MagicMock(
        return_value=SimpleNamespace(
            system=SimpleNamespace(memory_percent=42, disk_percent=86)
        )
    )
    manager.codex_account_reader = MagicMock()
    manager.codex_account_reader.read_account_status.return_value = (
        CodexQuotaData(status="unavailable"),
        CodexTokenUsageData(status="unavailable"),
    )
    codex_manager.list_sessions.return_value = []

    first = manager.dispatch(
        message_id="chub-query-1",
        prompt="chub",
        message_type="text",
        correlation_id=None,
        source_ip="100.64.0.21",
        delivery_route=delivery_route(),
    )
    second = manager.dispatch(
        message_id="chub-query-2",
        prompt="chub",
        message_type="text",
        correlation_id=None,
        source_ip="100.64.0.21",
        delivery_route=delivery_route(),
    )

    assert "磁盘使用率较高：86%" in (first.message or "")
    assert "Weekly 暂不可用" in (first.message or "")
    assert "Weekly 暂不可用" in (second.message or "")
    assert manager.system_status_reader.call_count == 2
    assert manager.codex_account_reader.read_account_status.call_count == 2
    manager.codex_account_reader.read_account_status.assert_called_with(force=True)
    assert codex_manager.list_sessions.call_count == 2


def test_concurrent_chub_queries_share_one_live_collection(
    settings: Settings,
) -> None:
    manager, codex_manager, _quick_interactions = configured_manager(settings)
    collection_started = threading.Event()
    release_collection = threading.Event()

    def read_system_status() -> object:
        collection_started.set()
        assert release_collection.wait(timeout=1)
        return SimpleNamespace(
            system=SimpleNamespace(memory_percent=42, disk_percent=27)
        )

    manager.system_status_reader = MagicMock(side_effect=read_system_status)
    manager.codex_account_reader = MagicMock()
    manager.codex_account_reader.read_account_status.return_value = (
        CodexQuotaData(status="unavailable"),
        CodexTokenUsageData(status="unavailable"),
    )
    codex_manager.list_sessions.return_value = []
    results: list[object] = []

    def query(message_id: str) -> None:
        results.append(
            manager.dispatch(
                message_id=message_id,
                prompt="chub",
                message_type="text",
                correlation_id=None,
                source_ip="100.64.0.21",
                delivery_route=delivery_route(),
            )
        )

    first = threading.Thread(target=query, args=("concurrent-chub-1",))
    second = threading.Thread(target=query, args=("concurrent-chub-2",))
    first.start()
    assert collection_started.wait(timeout=1)
    second.start()
    release_collection.set()
    first.join(timeout=1)
    second.join(timeout=1)

    assert len(results) == 2
    assert all(
        re.match(r"Chub · [1-9][0-9]*ms(?:\n|$)", result.message)
        for result in results
    )
    manager.system_status_reader.assert_called_once()
    manager.codex_account_reader.read_account_status.assert_called_once()
    codex_manager.list_sessions.assert_called_once()


@pytest.mark.parametrize("prompt", ["chub refresh", "chub -f", "刷新状态"])
def test_removed_chub_refresh_commands_are_normal_tasks(
    settings: Settings,
    prompt: str,
) -> None:
    manager, codex_manager, quick_interactions = configured_manager(settings)

    result = manager.dispatch(
        message_id=f"removed-refresh-{prompt}",
        prompt=prompt,
        message_type="text",
        correlation_id=None,
        source_ip="100.64.0.21",
        delivery_route=delivery_route(),
    )

    assert result.disposition == "handled"
    assert result.message is None
    quick_interactions.submit.assert_called_once()


def test_failed_account_refresh_preserves_last_success_timestamp(
    settings: Settings,
) -> None:
    manager, codex_manager, _quick_interactions = configured_manager(settings)
    checked_at = utc_now() - timedelta(minutes=10)
    manager._status_cache["account"] = (
        (
            CodexQuotaData(
                status="available",
                checked_at=checked_at,
                windows=[
                    CodexQuotaWindow(
                        remaining_percent=50,
                        window_duration_minutes=10_080,
                        resets_at=checked_at + timedelta(days=1),
                    )
                ],
            ),
            CodexTokenUsageData(status="available", checked_at=checked_at),
        ),
        checked_at,
    )
    manager.codex_account_reader = MagicMock()
    manager.codex_account_reader.read_account_status.return_value = (
        CodexQuotaData(
            status="available",
            checked_at=checked_at,
            message="refresh failed",
        ),
        CodexTokenUsageData(
            status="available",
            checked_at=checked_at,
            message="refresh failed",
        ),
    )
    codex_manager.list_sessions.return_value = []

    result = manager.dispatch(
        message_id="failed-account-refresh",
        prompt="chub",
        message_type="text",
        correlation_id=None,
        source_ip="100.64.0.21",
        delivery_route=delivery_route(),
    )

    assert manager._status_cache["account"][1] == checked_at
    assert "Weekly 50%" in (result.message or "")
    assert "异常" not in (result.message or "")
    assert "600 秒前" not in (result.message or "")


def test_chub_overview_shows_running_task_on_refreshed_session(
    settings: Settings,
) -> None:
    manager, codex_manager, quick_interactions = configured_manager(settings)
    manager._state.session_id = "session-1"
    manager._state.session_slots = [
        WeixinChubModeSessionSlot(slot=1, session_id="session-1")
    ]
    codex_manager.list_sessions.return_value = [
        CodexSession(
            id="session-1",
            workspace_id="chub",
            workspace_name="Chub",
            cwd="/project",
            title="运行任务",
            permission_mode="full-access",
            status="stopped",
            activity="idle",
        )
    ]
    quick_interactions.is_running.side_effect = lambda session_id: session_id == "session-1"
    quick_interactions.weixin_task_status_snapshot.return_value = SimpleNamespace(
        running_count=1,
        pending_notification_count=0,
        failed_notification_count=0,
        running_tasks=(("session-1", "优化状态展示"),),
    )

    result = manager.dispatch(
        message_id="busy-session-overview",
        prompt="chub",
        message_type="text",
        correlation_id=None,
        source_ip="100.64.0.21",
        delivery_route=delivery_route(),
    )

    assert (
        "S1 · 运行任务\n\nT1 · 优化状态展示\n\nBusy · Current"
        in (result.message or "")
    )


def test_chub_overview_separates_each_busy_session_block(
    settings: Settings,
) -> None:
    manager, _codex_manager, quick_interactions = configured_manager(settings)
    manager._status_cache["sessions"] = (
        (
            SimpleNamespace(
                slot=1,
                session_id="session-1",
                title="Chub 快速交互独立…",
                state="Busy",
                current=False,
            ),
            SimpleNamespace(
                slot=2,
                session_id="session-2",
                title="项目文档优化",
                state="Busy",
                current=True,
            ),
        ),
        utc_now(),
    )
    manager._task_status_cache["route"] = (
        SimpleNamespace(
            failed_notification_count=0,
            running_tasks=(
                ("session-1", "开始执行第四个阶段"),
                ("session-2", "项目文档优化"),
            ),
        ),
        utc_now(),
    )
    quick_interactions.is_running.return_value = True

    message = manager._format_chub_overview("route", elapsed_ms=129)

    assert (
        "Sessions\n\n"
        "S1 · Chub 快速交互独立…\n\n"
        "T1 · 开始执行第四个阶段\n\n"
        "Busy\n\n"
        "S2 · 项目文档优化\n\n"
        "T2 · 项目文档优化\n\n"
        "Busy · Current\n\n"
        "Codex"
    ) in message


def test_chub_overview_does_not_guess_task_for_non_weixin_busy_session(
    settings: Settings,
) -> None:
    manager, codex_manager, quick_interactions = configured_manager(settings)
    manager._state.session_id = "session-1"
    manager._state.session_slots = [
        WeixinChubModeSessionSlot(slot=1, session_id="session-1")
    ]
    codex_manager.list_sessions.return_value = [
        CodexSession(
            id="session-1",
            workspace_id="chub",
            workspace_name="Chub",
            cwd="/project",
            title="终端任务",
            permission_mode="full-access",
            status="running",
            activity="working",
        )
    ]
    quick_interactions.weixin_task_status_snapshot.return_value = SimpleNamespace(
        running_count=0,
        pending_notification_count=0,
        failed_notification_count=0,
        running_tasks=(),
    )

    result = manager.dispatch(
        message_id="non-weixin-busy-session-overview",
        prompt="chub",
        message_type="text",
        correlation_id=None,
        source_ip="100.64.0.21",
        delivery_route=delivery_route(),
    )

    assert "S1 · 终端任务\n\nBusy · Current" in (result.message or "")
    assert "T1 ·" not in (result.message or "")


def test_chub_overview_does_not_repeat_unavailable_session_as_anomaly(
    settings: Settings,
) -> None:
    manager, _codex_manager, _quick_interactions = configured_manager(settings)
    manager._status_cache["sessions"] = (
        (
            SimpleNamespace(
                slot=1,
                session_id="session-1",
                title="语音通知处理",
                state="Unavailable",
                current=True,
            ),
        ),
        utc_now(),
    )

    message = manager._format_chub_overview("route", elapsed_ms=10)

    assert "S1 · 语音通知处理\n\nUnavailable · Current" in message
    assert "Session 1 不可用" not in message


@pytest.mark.parametrize(
    ("elapsed_ms", "expected"),
    [
        (0, "1ms"),
        (10, "10ms"),
        (999, "999ms"),
        (1000, "1s"),
        (1324, "1.3s"),
        (10_000, "10s"),
    ],
)
def test_chub_overview_formats_elapsed_time_compactly(
    elapsed_ms: int,
    expected: str,
) -> None:
    assert WeixinChubModeManager._format_elapsed_time(elapsed_ms) == expected


def test_chub_sync_failure_does_not_commit_partial_slots(
    settings: Settings,
) -> None:
    manager, codex_manager, _quick_interactions = configured_manager(settings)
    manager._state.session_slots = [
        WeixinChubModeSessionSlot(slot=1, session_id="missing")
    ]
    codex_manager.list_sessions.return_value = []
    original_write = manager._write_state
    manager._write_state = MagicMock(side_effect=OSError("write failed"))

    result = manager.dispatch(
        message_id="chub-sync-write-failure",
        prompt="同步状态",
        message_type="text",
        correlation_id=None,
        source_ip="100.64.0.21",
        delivery_route=delivery_route(),
    )

    assert result.message == "任务提交失败：Chub 当前状态不可用，请稍后重试。"
    assert manager.session_slot_matches(1, "missing")
    manager._write_state = original_write


def test_session_slots_snapshot_is_consistent_copy(settings: Settings) -> None:
    manager, _codex_manager, _quick_interactions = configured_manager(settings)
    manager._state.session_slots = [
        WeixinChubModeSessionSlot(slot=1, session_id="session-1"),
        WeixinChubModeSessionSlot(slot=3, session_id="session-3"),
    ]

    snapshot = manager.session_slots_snapshot()
    snapshot["session-1"] = 9

    assert snapshot == {"session-1": 9, "session-3": 3}
    assert manager.session_slots_snapshot() == {"session-1": 1, "session-3": 3}


def test_codex_status_does_not_report_missing_daily_bucket_as_zero() -> None:
    message = WeixinChubModeManager._codex_usage_message(
        CodexQuotaData(status="unavailable"),
        CodexTokenUsageData(status="available", daily_usage=[]),
    )

    assert message == "Weekly 暂不可用"


def test_codex_status_compacts_large_token_count() -> None:
    assert WeixinChubModeManager._compact_token_count(81_805_470) == "81.8M"


def test_codex_status_uses_shared_compact_format_without_date() -> None:
    checked_at = utc_now()
    message = WeixinChubModeManager._codex_usage_message(
        CodexQuotaData(
            status="available",
            checked_at=checked_at,
            windows=[
                CodexQuotaWindow(
                    remaining_percent=54,
                    window_duration_minutes=10_080,
                    resets_at=checked_at + timedelta(days=1),
                )
            ],
        ),
        CodexTokenUsageData(
            status="available",
            checked_at=checked_at,
            daily_usage=[
                {
                    "start_date": datetime.now().astimezone().date(),
                    "tokens": 67_400_000,
                }
            ],
        ),
    )

    assert message == "Weekly 54% · Today 67.4M"


def test_chub_overview_separates_codex_heading_and_shortens_token_label(
    settings: Settings,
) -> None:
    manager, _codex_manager, _quick_interactions = configured_manager(settings)
    checked_at = utc_now()
    manager._status_cache["account"] = (
        (
            CodexQuotaData(
                status="available",
                checked_at=checked_at,
                windows=[
                    CodexQuotaWindow(
                        remaining_percent=71,
                        window_duration_minutes=10_080,
                        resets_at=checked_at + timedelta(days=1),
                    )
                ],
            ),
            CodexTokenUsageData(
                status="available",
                checked_at=checked_at,
                daily_usage=[
                    {
                        "start_date": datetime.now().astimezone().date(),
                        "tokens": 67_400_000,
                    }
                ],
            ),
        ),
        checked_at,
    )

    message = manager._format_chub_overview("route", elapsed_ms=10)

    assert "\n\nCodex\n\nWeekly 71% · Today 67.4M" in message
    assert "Today 67.4M (" not in message
    assert "Daily tokens" not in message


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

    assert result.disposition == "handled"
    assert result.message is None
    quick_interactions.submit.assert_called_once()


@pytest.mark.parametrize(
    "prompt",
    ["codex help", "Codex Help。", "《CODEX HELP》", "codex help me review this"],
)
def test_removed_codex_help_is_submitted_as_normal_task(
    settings: Settings,
    prompt: str,
) -> None:
    manager, _codex_manager, quick_interactions = configured_manager(settings)

    result = manager.dispatch(
        message_id=f"removed-codex-help-{prompt}",
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
        "session switch",
        "Session Switch。",
        "codex switch",
        "codex switch 2",
        "codex archive 2",
        "codex new",
        "codex retry",
        "codex new retry",
    ],
)
def test_removed_or_unparameterized_session_commands_are_normal_tasks(
    settings: Settings,
    prompt: str,
) -> None:
    manager, _codex_manager, quick_interactions = configured_manager(settings)

    result = manager.dispatch(
        message_id=f"removed-session-command-{prompt}",
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


def test_codex_switch_uses_creation_order_and_allows_busy_target(
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
    manager._state.session_slots = [
        WeixinChubModeSessionSlot(slot=1, session_id="a-current"),
        WeixinChubModeSessionSlot(slot=2, session_id="b-busy"),
        WeixinChubModeSessionSlot(slot=3, session_id="c-available"),
    ]
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
        prompt="Session Switch 3",
        message_type="text",
        correlation_id=None,
        source_ip="100.64.0.21",
        delivery_route=delivery_route(),
    )

    assert result.message is not None
    assert result.message.startswith(
        "切换状态：已切换到 Session 3。\n\n"
        "Sessions\n\n"
    )
    assert "S3 · 第三项\n\nAvailable · Current" in result.message
    assert result.message.endswith("Weekly 暂不可用")
    assert manager.session_id() == "c-available"
    quick_interactions.submit.assert_not_called()


@pytest.mark.parametrize(
    "prompt",
    [
        "切换2",
        "切换二",
        " 切换 二。 ",
        "切换会话2",
        "切换会话 二",
        "会话2",
        " 会话 二。 ",
    ],
)
def test_chinese_switch_routes_to_numbered_session(
    settings: Settings,
    prompt: str,
) -> None:
    manager, codex_manager, quick_interactions = configured_manager(settings)
    manager._state.session_id = "session-1"
    sessions = [
        CodexSession(
            id=f"session-{slot}",
            workspace_id="chub",
            workspace_name="Chub",
            cwd="/project",
            title=f"第 {slot} 项",
            permission_mode="full-access",
            status="stopped",
            activity="idle",
        )
        for slot in (1, 2)
    ]
    manager._state.session_slots = [
        WeixinChubModeSessionSlot(slot=slot, session_id=f"session-{slot}")
        for slot in (1, 2)
    ]
    codex_manager.list_sessions.return_value = sessions
    codex_manager.get_session.return_value = sessions[1]
    manager.codex_account_reader = MagicMock()
    manager.codex_account_reader.read_account_status.return_value = (
        CodexQuotaData(status="unavailable"),
        CodexTokenUsageData(status="unavailable"),
    )

    result = manager.dispatch(
        message_id=f"chinese-switch-{prompt}",
        prompt=prompt,
        message_type="voice",
        correlation_id=None,
        source_ip="100.64.0.21",
        delivery_route=delivery_route(),
    )

    assert result.message is not None
    assert "S2 · 第 2 项\n\nAvailable · Current" in result.message
    assert "Task status:" not in result.message
    assert manager.session_id() == "session-2"
    quick_interactions.submit.assert_not_called()


@pytest.mark.parametrize(
    ("prompt", "task_prompt"),
    [
        ("session switch 2, continue checking logs", "continue checking logs"),
        ("切换2，继续检查日志。", "继续检查日志。"),
        ("切换 2，继续检查正文", "继续检查正文"),
        ("切换会话二 继续处理部署问题", "继续处理部署问题"),
        ("切换会话2：/api/devices", "/api/devices"),
        ("切换2，# 检查标题", "# 检查标题"),
        ("会话二，继续检查告警", "继续检查告警"),
        ("会话 2 继续检查正文", "继续检查正文"),
    ],
)
def test_codex_switch_with_task_switches_and_submits_once(
    settings: Settings,
    prompt: str,
    task_prompt: str,
) -> None:
    manager, codex_manager, quick_interactions = configured_manager(settings)
    manager._state.session_id = "session-1"
    sessions = [
        CodexSession(
            id=f"session-{slot}",
            workspace_id="chub",
            workspace_name="Chub",
            cwd="/project",
            title=f"第 {slot} 项",
            permission_mode="full-access",
            status="stopped",
            activity="idle",
        )
        for slot in (1, 2)
    ]
    manager._state.session_slots = [
        WeixinChubModeSessionSlot(slot=slot, session_id=f"session-{slot}")
        for slot in (1, 2)
    ]
    codex_manager.list_sessions.return_value = sessions
    codex_manager.get_session.return_value = sessions[1]
    manager.codex_account_reader = MagicMock()
    manager.codex_account_reader.read_account_status.return_value = (
        CodexQuotaData(status="unavailable"),
        CodexTokenUsageData(status="unavailable"),
    )

    first = manager.dispatch(
        message_id=f"switch-with-task-{prompt}",
        prompt=prompt,
        message_type="text",
        correlation_id=None,
        source_ip="100.64.0.21",
        delivery_route=delivery_route(),
    )
    duplicate = manager.dispatch(
        message_id=f"switch-with-task-{prompt}",
        prompt=prompt,
        message_type="text",
        correlation_id=None,
        source_ip="100.64.0.21",
        delivery_route=delivery_route(),
    )

    assert first.disposition == "reply"
    assert first.message is not None
    assert first.message.startswith("切换状态：已切换到 Session 2。")
    assert "任务状态：已提交。" in first.message
    assert duplicate.message == first.message
    assert manager.session_id() == "session-2"
    quick_interactions.submit.assert_called_once()
    assert quick_interactions.submit.call_args.args[:2] == (
        "session-2",
        task_prompt,
    )


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
    manager._state.session_slots = [
        WeixinChubModeSessionSlot(slot=1, session_id="b-available")
    ]
    codex_manager.get_session.return_value = available
    manager.codex_account_reader = MagicMock()
    manager.codex_account_reader.read_account_status.return_value = (
        CodexQuotaData(status="unavailable"),
        CodexTokenUsageData(status="unavailable"),
    )

    result = manager.dispatch(
        message_id="codex-switch-first",
        prompt="session switch 1",
        message_type="text",
        correlation_id=None,
        source_ip="100.64.0.21",
        delivery_route=delivery_route(),
    )

    assert result.message is not None
    assert "S1 · 可用会话\n\nAvailable · Current" in result.message
    assert manager.session_id() == "b-available"


@pytest.mark.parametrize(
    "prompt",
    [
        "Session Archive 2。",
        "归档2",
        "归档二",
        "归档 二",
        "归档会话2",
        "归档会话 二",
    ],
)
def test_codex_archive_removes_target_and_clears_current_binding(
    settings: Settings,
    prompt: str,
) -> None:
    manager, codex_manager, quick_interactions = configured_manager(settings)
    sessions = [
        CodexSession(
            id=f"session-{index}",
            codex_session_id=f"native-{index}",
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
    manager._state.session_id = "session-2"
    manager._state.session_slots = [
        WeixinChubModeSessionSlot(slot=1, session_id="session-1"),
        WeixinChubModeSessionSlot(slot=2, session_id="session-2"),
    ]
    remaining_sessions = list(sessions)
    codex_manager.list_sessions.side_effect = lambda: list(remaining_sessions)
    codex_manager.get_session.side_effect = lambda session_id: by_id[session_id]
    manager.session_archiver.side_effect = lambda session_id: remaining_sessions.__setitem__(
        slice(None),
        [session for session in remaining_sessions if session.id != session_id],
    )
    manager.codex_account_reader = MagicMock()
    manager.codex_account_reader.read_account_status.return_value = (
        CodexQuotaData(status="unavailable"),
        CodexTokenUsageData(status="unavailable"),
    )

    with patch(
        "app.services.openclaw_weixin_chub_mode.write_operation"
    ) as write_operation:
        result = manager.dispatch(
            message_id=f"archive-{prompt}",
            prompt=prompt,
            message_type="voice",
            correlation_id=None,
            source_ip="100.64.0.21",
            delivery_route=delivery_route(),
        )

    assert result.message is not None
    assert result.message.startswith(
        "归档状态：Session 2 已归档，当前绑定已清除。\n\n"
        "Sessions\n\n"
    )
    assert result.message.endswith("Weekly 暂不可用")
    assert "S1 · 候选 1\n\nAvailable" in result.message
    assert "候选 2" not in result.message
    assert " · Current" not in result.message
    manager.session_archiver.assert_called_once_with("session-2")
    assert manager.session_id() is None
    assert manager.session_slot_matches(2, "session-2") is False
    quick_interactions.submit.assert_not_called()
    archive_entries = [
        call.kwargs
        for call in write_operation.call_args_list
        if call.kwargs["action"] == "archive_codex_session"
    ]
    assert [entry["status"] for entry in archive_entries] == [
        "requested",
        "started",
        "succeeded",
    ]


def test_duplicate_codex_archive_does_not_archive_twice(settings: Settings) -> None:
    manager, codex_manager, _quick_interactions = configured_manager(settings)
    session = CodexSession(
        id="session-1",
        codex_session_id="native-1",
        workspace_id="chub",
        workspace_name="Chub",
        cwd="/project",
        title="待归档",
        permission_mode="full-access",
        status="stopped",
        activity="idle",
    )
    codex_manager.list_sessions.return_value = [session]
    codex_manager.get_session.return_value = session
    manager._state.session_slots = [
        WeixinChubModeSessionSlot(slot=1, session_id="session-1")
    ]

    first = manager.dispatch(
        message_id="duplicate-archive",
        prompt="归档一",
        message_type="text",
        correlation_id=None,
        source_ip="100.64.0.21",
        delivery_route=delivery_route(),
    )
    duplicate = manager.dispatch(
        message_id="duplicate-archive",
        prompt="归档一",
        message_type="text",
        correlation_id=None,
        source_ip="100.64.0.21",
        delivery_route=delivery_route(),
    )

    assert duplicate == first
    manager.session_archiver.assert_called_once_with("session-1")


def test_codex_archive_status_preserves_freed_slot_for_codex_new(
    settings: Settings,
) -> None:
    manager, codex_manager, _quick_interactions = configured_manager(settings)
    sessions = [
        CodexSession(
            id=f"session-{index}",
            codex_session_id=f"native-{index}",
            workspace_id="chub",
            workspace_name="Chub",
            cwd="/project",
            title=f"候选 {index}",
            permission_mode="full-access",
            status="stopped",
            activity="idle",
        )
        for index in range(1, 11)
    ]
    by_id = {session.id: session for session in sessions}
    manager._state.session_slots = [
        WeixinChubModeSessionSlot(slot=index, session_id=f"session-{index}")
        for index in range(1, 10)
    ]
    remaining_sessions = list(sessions)
    codex_manager.list_sessions.side_effect = lambda: list(remaining_sessions)
    codex_manager.get_session.side_effect = lambda session_id: by_id[session_id]
    manager.session_archiver.side_effect = lambda session_id: remaining_sessions.__setitem__(
        slice(None),
        [session for session in remaining_sessions if session.id != session_id],
    )
    manager.codex_account_reader = MagicMock()
    manager.codex_account_reader.read_account_status.return_value = (
        CodexQuotaData(status="unavailable"),
        CodexTokenUsageData(status="unavailable"),
    )

    archived = manager.dispatch(
        message_id="archive-preserves-slot",
        prompt="归档2",
        message_type="text",
        correlation_id=None,
        source_ip="100.64.0.21",
        delivery_route=delivery_route(),
    )

    assert archived.message is not None
    assert "2. 候选 10" not in archived.message
    assert "另有 1 个" in archived.message
    assert manager.session_slot_matches(2, "session-10") is False

    refreshed = manager.dispatch(
        message_id="status-fills-freed-slot",
        prompt="sync",
        message_type="text",
        correlation_id=None,
        source_ip="100.64.0.21",
        delivery_route=delivery_route(),
    )

    assert refreshed.message is not None
    assert "S2 · 候选 10\n\nAvailable" in refreshed.message
    assert manager.session_slot_matches(2, "session-10") is True


def test_codex_archive_does_not_fill_unassigned_candidate(
    settings: Settings,
) -> None:
    manager, codex_manager, _quick_interactions = configured_manager(settings)
    sessions = [
        CodexSession(
            id=f"session-{index}",
            codex_session_id=f"native-{index}",
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
    manager._state.session_slots = [
        WeixinChubModeSessionSlot(slot=1, session_id="session-1")
    ]
    codex_manager.list_sessions.return_value = sessions
    manager.codex_account_reader = MagicMock()
    manager.codex_account_reader.read_account_status.return_value = (
        CodexQuotaData(status="unavailable"),
        CodexTokenUsageData(status="unavailable"),
    )

    archived = manager.dispatch(
        message_id="archive-unassigned-candidate",
        prompt="归档2",
        message_type="text",
        correlation_id=None,
        source_ip="100.64.0.21",
        delivery_route=delivery_route(),
    )

    assert archived.message is not None
    assert archived.message.startswith("归档状态：未归档，编号无效。\n\n")
    assert "另有 1 个" in archived.message
    manager.session_archiver.assert_not_called()
    assert manager.session_slot_matches(2, "session-2") is False

    manager.dispatch(
        message_id="status-fills-archive-candidate",
        prompt="sync",
        message_type="text",
        correlation_id=None,
        source_ip="100.64.0.21",
        delivery_route=delivery_route(),
    )

    assert manager.session_slot_matches(2, "session-2") is True


@pytest.mark.parametrize(
    ("activity", "quick_running", "writer_active"),
    [("working", False, False), ("idle", True, False), ("idle", False, True), ("unknown", False, False)],
)
def test_codex_archive_rejects_session_that_is_not_safely_idle(
    settings: Settings,
    activity: str,
    quick_running: bool,
    writer_active: bool,
) -> None:
    manager, codex_manager, quick_interactions = configured_manager(settings)
    session = CodexSession(
        id="session-1",
        codex_session_id="native-1",
        workspace_id="chub",
        workspace_name="Chub",
        cwd="/project",
        title="使用中",
        permission_mode="full-access",
        status="running" if activity != "unknown" else "stopped",
        activity=activity,
    )
    codex_manager.list_sessions.return_value = [session]
    codex_manager.get_session.return_value = session
    manager._state.session_slots = [
        WeixinChubModeSessionSlot(slot=1, session_id="session-1")
    ]
    quick_interactions.is_running.return_value = quick_running
    codex_manager.has_active_writer.return_value = writer_active

    result = manager.dispatch(
        message_id=f"unsafe-archive-{activity}-{quick_running}-{writer_active}",
        prompt="session archive 1",
        message_type="text",
        correlation_id=None,
        source_ip="100.64.0.21",
        delivery_route=delivery_route(),
    )

    assert result.message is not None
    assert "未归档" in result.message
    manager.session_archiver.assert_not_called()


@pytest.mark.parametrize(
    "prompt",
    [
        "session archive",
        "session archive 0",
        "session archive -1",
        "session archive abc",
        "session archive 1 extra",
        "归档",
        "归档10",
        "归档零",
        "归档会话",
        "归档会话2，继续处理",
    ],
)
def test_codex_archive_invalid_usage_is_not_submitted(
    settings: Settings,
    prompt: str,
) -> None:
    manager, codex_manager, quick_interactions = configured_manager(settings)

    result = manager.dispatch(
        message_id=f"invalid-archive-{prompt}",
        prompt=prompt,
        message_type="text",
        correlation_id=None,
        source_ip="100.64.0.21",
        delivery_route=delivery_route(),
    )

    assert result.message is not None
    assert result.message.startswith("用法：发送 session archive n")
    codex_manager.list_sessions.assert_not_called()
    quick_interactions.submit.assert_not_called()


def test_chinese_archive_business_text_is_submitted_as_normal_task(
    settings: Settings,
) -> None:
    manager, _codex_manager, quick_interactions = configured_manager(settings)

    result = manager.dispatch(
        message_id="archive-business-text",
        prompt="归档日志后再检查",
        message_type="text",
        correlation_id=None,
        source_ip="100.64.0.21",
        delivery_route=delivery_route(),
    )

    assert result.disposition == "handled"
    assert result.message is None
    quick_interactions.submit.assert_called_once()


def test_codex_archive_failure_keeps_slot_and_explains_possible_stop(
    settings: Settings,
) -> None:
    manager, codex_manager, _quick_interactions = configured_manager(settings)
    session = CodexSession(
        id="session-1",
        codex_session_id="native-1",
        workspace_id="chub",
        workspace_name="Chub",
        cwd="/project",
        title="待归档",
        permission_mode="full-access",
        status="stopped",
        activity="idle",
    )
    codex_manager.list_sessions.return_value = [session]
    codex_manager.get_session.return_value = session
    manager._state.session_slots = [
        WeixinChubModeSessionSlot(slot=1, session_id="session-1")
    ]
    manager.session_archiver.side_effect = ApiError(
        503,
        "codex_session_archive_failed",
        "private failure",
    )

    result = manager.dispatch(
        message_id="archive-command-failure",
        prompt="归档1",
        message_type="text",
        correlation_id=None,
        source_ip="100.64.0.21",
        delivery_route=delivery_route(),
    )

    assert result.message == (
        "归档失败；Session 可能已停止，但未从列表移除。"
        "请发送 chub 查看状态后再重试。"
    )
    assert manager.session_slot_matches(1, "session-1") is True


def test_codex_archive_rejects_session_with_pending_retry(
    settings: Settings,
) -> None:
    manager, codex_manager, _quick_interactions = configured_manager(settings)
    session = CodexSession(
        id="session-1",
        codex_session_id="native-1",
        workspace_id="chub",
        workspace_name="Chub",
        cwd="/project",
        title="待续提",
        permission_mode="full-access",
        status="stopped",
        activity="idle",
    )
    codex_manager.list_sessions.return_value = [session]
    codex_manager.get_session.return_value = session
    manager._state.session_slots = [
        WeixinChubModeSessionSlot(slot=1, session_id="session-1")
    ]
    now = utc_now()
    manager._state.pending_retry = WeixinChubModePendingRetry(
        original_message_id="original-message",
        prompt="继续处理任务",
        delivery_route_fingerprint="a" * 64,
        created_at=now,
        expires_at=now + timedelta(minutes=10),
        session_id="session-1",
    )

    result = manager.dispatch(
        message_id="archive-with-pending-retry",
        prompt="归档1",
        message_type="text",
        correlation_id=None,
        source_ip="100.64.0.21",
        delivery_route=delivery_route(),
    )

    assert result.message.startswith(
        "归档状态：未归档，该 Session 关联一条待继续执行的任务。\n\n"
    )
    assert "Sessions\n\nS1 · 待续提\n\nAvailable" in result.message
    manager.session_archiver.assert_not_called()


def test_codex_archive_state_sync_failure_reports_partial_success(
    settings: Settings,
) -> None:
    manager, codex_manager, _quick_interactions = configured_manager(settings)
    session = CodexSession(
        id="session-1",
        codex_session_id="native-1",
        workspace_id="chub",
        workspace_name="Chub",
        cwd="/project",
        title="待归档",
        permission_mode="full-access",
        status="stopped",
        activity="idle",
    )
    codex_manager.list_sessions.return_value = [session]
    codex_manager.get_session.return_value = session
    manager._state.session_slots = [
        WeixinChubModeSessionSlot(slot=1, session_id="session-1")
    ]
    original_write = manager._write_state
    write_count = 0

    def fail_cleanup_write(state) -> None:
        nonlocal write_count
        write_count += 1
        if write_count == 3:
            raise OSError("write failed")
        original_write(state)

    manager._write_state = fail_cleanup_write

    result = manager.dispatch(
        message_id="archive-state-sync-failure",
        prompt="归档1",
        message_type="text",
        correlation_id=None,
        source_ip="100.64.0.21",
        delivery_route=delivery_route(),
    )

    assert result.message == (
        "Session 已归档，但 Chub 未能同步列表状态。"
        "请稍后发送 chub 查看状态。"
    )
    manager.session_archiver.assert_called_once_with("session-1")


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
    manager._state.session_slots = [
        WeixinChubModeSessionSlot(slot=index, session_id=f"session-{index}")
        for index in range(1, 4)
    ]
    codex_manager.list_sessions.return_value = list(reversed(sessions))
    codex_manager.get_session.side_effect = lambda session_id: by_id[session_id]
    manager.codex_account_reader = MagicMock()
    manager.codex_account_reader.read_account_status.return_value = (
        CodexQuotaData(status="unavailable"),
        CodexTokenUsageData(status="unavailable"),
    )

    result = manager.dispatch(
        message_id="codex-switch-number",
        prompt=" session   switch   2 ",
        message_type="text",
        correlation_id=None,
        source_ip="100.64.0.21",
        delivery_route=delivery_route(),
    )

    assert result.message is not None
    assert "S2 · 候选 2\n\nAvailable · Current" in result.message
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
    manager._state.session_slots = [
        WeixinChubModeSessionSlot(slot=index, session_id=f"session-{index}")
        for index in (1, 2)
    ]
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
                prompt="session switch 2",
                message_type="text",
                correlation_id=None,
                source_ip="100.64.0.21",
                delivery_route=delivery_route(),
            )
    finally:
        account_release.set()

    assert result.message is not None
    assert result.message.startswith(
        "切换状态：已切换到 Session 2。\n\n"
        "Sessions\n\n"
    )
    assert result.message.endswith("Codex 用量查询失败，请稍后重试。")
    assert "S2 · 候选 2\n\nAvailable · Current" in result.message
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
        prompt="session switch 2",
        message_type="text",
        correlation_id=None,
        source_ip="100.64.0.21",
        delivery_route=delivery_route(),
    )

    assert result.message is not None
    assert result.message.startswith("切换状态：未切换，编号无效。\n\n")
    assert "Sessions" in result.message
    assert "S1 · 当前会话\n\nAvailable · Current" in result.message
    assert manager.session_id() == "session-1"
    codex_manager.get_session.assert_not_called()


def test_codex_switch_does_not_fill_unassigned_candidate(
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
    manager._state.session_slots = [
        WeixinChubModeSessionSlot(slot=1, session_id="session-1")
    ]
    codex_manager.list_sessions.return_value = sessions
    manager.codex_account_reader = MagicMock()
    manager.codex_account_reader.read_account_status.return_value = (
        CodexQuotaData(status="unavailable"),
        CodexTokenUsageData(status="unavailable"),
    )

    switched = manager.dispatch(
        message_id="switch-unassigned-candidate",
        prompt="切换2",
        message_type="text",
        correlation_id=None,
        source_ip="100.64.0.21",
        delivery_route=delivery_route(),
    )

    assert switched.message is not None
    assert switched.message.startswith(
        "切换状态：未切换，编号无效。"
        "另有未登记的可用 Session，请先发送 sync、同步状态或状态同步后再切换。\n\n"
    )
    assert "另有 1 个" in switched.message
    assert manager.session_id() == "session-1"
    assert manager.session_slot_matches(2, "session-2") is False

    manager.dispatch(
        message_id="status-fills-switch-candidate",
        prompt="sync",
        message_type="text",
        correlation_id=None,
        source_ip="100.64.0.21",
        delivery_route=delivery_route(),
    )

    assert manager.session_slot_matches(2, "session-2") is True


@pytest.mark.parametrize(
    "prompt",
    [
        "session switch 0",
        "session switch -1",
        "session switch abc",
        "切换10",
        "切换-1",
        "切换＋2",
        "切换０",
        "切换零",
        "切换十",
        "切换",
        "会话10",
        "会话零",
        "会话",
    ],
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
    assert result.message.startswith("用法：发送 session switch n")
    codex_manager.list_sessions.assert_not_called()
    quick_interactions.submit.assert_not_called()


@pytest.mark.parametrize(
    "prompt",
    [
        "切换网络后再检查",
        "切换二再执行",
        "切换到备用节点",
        "会话设置需要检查",
        "会话二再执行",
    ],
)
def test_chinese_switch_business_text_is_submitted_as_normal_task(
    settings: Settings,
    prompt: str,
) -> None:
    manager, codex_manager, quick_interactions = configured_manager(settings)

    result = manager.dispatch(
        message_id=f"switch-business-text-{prompt}",
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
    assert manager.codex_account_reader is None


def test_codex_switch_rejects_oversized_numeric_index(settings: Settings) -> None:
    manager, codex_manager, quick_interactions = configured_manager(settings)

    result = manager.dispatch(
        message_id="invalid-large-switch-index",
        prompt="session switch " + ("9" * 5_000),
        message_type="text",
        correlation_id=None,
        source_ip="100.64.0.21",
        delivery_route=delivery_route(),
    )

    assert result.message is not None
    assert result.message.startswith("用法：发送 session switch n")
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
    manager._state.session_slots = [
        WeixinChubModeSessionSlot(slot=index, session_id=f"session-{index}")
        for index in range(1, 4)
    ]
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
        prompt="session switch 2",
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
    manager._state.session_slots = [
        WeixinChubModeSessionSlot(slot=index, session_id=f"session-{index}")
        for index in range(1, 4)
    ]
    codex_manager.list_sessions.return_value = sessions
    codex_manager.get_session.side_effect = lambda session_id: by_id[session_id]
    manager.codex_account_reader = MagicMock()
    manager.codex_account_reader.read_account_status.return_value = (
        CodexQuotaData(status="unavailable"),
        CodexTokenUsageData(status="unavailable"),
    )

    first = manager.dispatch(
        message_id="duplicate-switch",
        prompt="session switch 3",
        message_type="text",
        correlation_id=None,
        source_ip="100.64.0.21",
        delivery_route=delivery_route(),
    )
    duplicate = manager.dispatch(
        message_id="duplicate-switch",
        prompt="session switch 3",
        message_type="text",
        correlation_id=None,
        source_ip="100.64.0.21",
        delivery_route=delivery_route(),
    )

    assert duplicate == first
    assert manager.session_id() == "session-3"
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

    assert result.disposition == "handled"
    assert result.message is None
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
        "任务提交失败：当前 Session 正在执行，本任务未提交。\n\n"
        "如需新建 Session 并继续执行本任务，请回复："
        "session new retry 或“新建会话执行”。"
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
        "创建状态：Session 1 已创建并切换。\n\n"
        "Sessions\n\n"
    )
    assert "S1 · 未命名 Session\n\nAvailable · Current" in result.message
    assert result.message.endswith("Weekly 暂不可用")
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
    assert "创建状态：Session 1 已创建并切换。" in (first.message or "")
    assert "任务状态：已提交。" in (first.message or "")
    assert duplicate.message == first.message
    codex_manager.create_session.assert_called_once()
    quick_interactions.submit.assert_called_once()
    assert quick_interactions.submit.call_args.args[:2] == (
        "session-new",
        task_prompt,
    )


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
    assert "S2 · 新建会话\n\nAvailable · Current" in result.message
    assert "等待候选" not in result.message
    assert "另有 1 个" in result.message
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

    assert "S1 · 候选 1\n\nAvailable · Current" in internal_status
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
    assert "Codex 用量查询失败" in created.message
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

    assert "S1 · 未命名 Session\n\nAvailable" in (result.message or "")
    quick_interactions.submit.assert_not_called()


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

    assert result.disposition == "handled"
    assert result.message is None
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


def test_dispatch_silently_accepts_voice_task(
    settings: Settings,
) -> None:
    manager, _codex_manager, _quick_interactions = configured_manager(settings)

    result = manager.dispatch(
        message_id="dispatch-voice-1",
        prompt="检查语音任务",
        message_type="voice",
        correlation_id=None,
        source_ip="100.64.0.21",
        delivery_route=delivery_route(),
    )

    assert result.protocol_version == 3
    assert result.disposition == "handled"
    assert result.message is None


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
    codex_manager.set_initial_quick_interaction_title.assert_not_called()
    quick_interactions.submit.assert_called_once_with(
        "session-1",
        "检查设备状态",
        operation_id=quick_interactions.submit.call_args.kwargs["operation_id"],
        source_ip="100.64.0.21",
        notification_route=delivery_route(),
        weixin_session_slot=1,
        weixin_session_title="检查设备状态",
    )
    state_file = settings.openclaw.weixin_chub_mode.state_file
    assert stat.S_IMODE(state_file.stat().st_mode) == 0o600
    state_text = state_file.read_text(encoding="utf-8")
    assert "不会再次执行" not in state_text
    assert "weixin-account" not in state_text
    assert "owner@im.wechat" not in state_text
    persisted = json.loads(state_text)
    assert persisted["session_id"] == "session-1"
    assert persisted["submissions"][0]["task_id"] == "task-1"


def test_translation_is_enqueued_after_main_submission_is_persisted(
    settings: Settings,
) -> None:
    manager, _codex_manager, _quick_interactions = configured_manager(settings)
    manager.translation_manager = MagicMock()
    original_replace = manager._replace_submission
    persisted = False

    def replace_and_mark(submission) -> None:
        nonlocal persisted
        original_replace(submission)
        persisted = True

    manager._replace_submission = replace_and_mark
    manager.translation_manager.enqueue.side_effect = (
        lambda **_kwargs: persisted or pytest.fail("translation queued too early")
    )

    manager.submit(
        message_id="translation-order",
        prompt="需要翻译的任务",
        correlation_id=None,
        source_ip="100.64.0.21",
        delivery_route=delivery_route(),
    )

    manager.translation_manager.enqueue.assert_called_once()


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
        weixin_session_slot=1,
        weixin_session_title="检查模型配置",
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
        503,
        "quick_worker_unavailable",
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

    assert [error.status_code for error in errors] == [503, 503]
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
