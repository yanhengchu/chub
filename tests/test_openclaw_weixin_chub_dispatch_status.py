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


def test_dispatch_immediately_acknowledges_text_task(
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
    assert result.message == submitted_task_message(settings, "检查设备状态")
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


def test_submission_and_session_list_use_the_same_task_summary(
    settings: Settings,
) -> None:
    settings.openclaw.weixin_chub_mode.task_name_max_width = 16
    manager, _codex_manager, quick_interactions = configured_manager(settings)
    prompt = "任" * 20

    submission = manager.dispatch(
        message_id="shared-task-summary",
        prompt=prompt,
        message_type="text",
        correlation_id=None,
        source_ip="100.64.0.21",
        delivery_route=delivery_route(),
    )
    manager._status_cache["sessions"] = (
        (
            SimpleNamespace(
                slot=1,
                session_id="session-1",
                title="项目维护",
                state="Busy",
                current=True,
            ),
        ),
        utc_now(),
    )
    manager._task_status_cache["route"] = (
        SimpleNamespace(
            failed_notification_count=0,
            running_tasks=(("session-1", prompt),),
        ),
        utc_now(),
    )
    quick_interactions.is_running.return_value = True

    overview = manager._format_chub_overview("route", elapsed_ms=10)

    assert submission.message == submitted_task_message(settings, prompt)
    assert "Task · 任任任任任任任…" in overview


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
    assert "Task result notifications failed: 1" in result.message
    assert result.message.endswith("No sessions\n\nWeekly Unavailable")
    assert "Sessions\n\nNo sessions" not in result.message
    assert "执行中 2" not in result.message
    codex_manager.list_sessions.assert_called_once()
    quick_interactions.submit.assert_not_called()


@pytest.mark.parametrize("prompt", ["help", "HELP。", "帮助", " 帮助。 "])
def test_dispatch_returns_concise_chub_help(
    settings: Settings,
    prompt: str,
) -> None:
    manager, codex_manager, quick_interactions = configured_manager(settings)

    result = manager.dispatch(
        message_id=f"help-{prompt}",
        prompt=prompt,
        message_type="text",
        correlation_id=None,
        source_ip="100.64.0.21",
        delivery_route=delivery_route(),
    )

    assert result.message == (
        "Commands\n\n"
        "chub\n"
        "sync\n"
        "session new [task]\n"
        "rename <title>\n"
        "session switch <S1-S9|1-9> [task]\n"
        "session stop <S1-S9|1-9>\n"
        "session archive <S1-S9|1-9>\n"
        "session retry\n"
        "session new retry\n"
        "restart"
    )
    codex_manager.list_sessions.assert_not_called()
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

    assert first.message is not None
    assert first.message.startswith(
        "Status: Failed to build the Chub overview. Try again later."
    )
    assert first.message.endswith("Weekly Unavailable")
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

    assert result.disposition == "reply"
    assert result.message == submitted_task_message(settings, "codex")
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

    assert "Disk usage is high: 86%" in (first.message or "")
    assert "Weekly Unavailable" in (first.message or "")
    assert "Weekly Unavailable" in (second.message or "")
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

    assert result.disposition == "reply"
    assert result.message == submitted_task_message(settings, prompt)
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
        "▶ S1 · 运行任务\nTask · 优化状态展示"
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
        "S1 · Chub 快速交互独立…\n"
        "Task · 开始执行第四个阶段\n\n"
        "▶ S2 · 项目文档优化\n"
        "Task · 项目文档优化\n\n"
        "Weekly"
    ) in message


def test_chub_overview_uses_configured_task_name_limit(settings: Settings) -> None:
    settings.openclaw.weixin_chub_mode.task_name_max_width = 16
    manager, _codex_manager, quick_interactions = configured_manager(settings)
    manager._status_cache["sessions"] = (
        (
            SimpleNamespace(
                slot=1,
                session_id="session-1",
                title="项目维护",
                state="Busy",
                current=True,
            ),
        ),
        utc_now(),
    )
    manager._task_status_cache["route"] = (
        SimpleNamespace(
            failed_notification_count=0,
            running_tasks=(("session-1", "任" * 20),),
        ),
        utc_now(),
    )
    quick_interactions.is_running.return_value = True

    message = manager._format_chub_overview("route", elapsed_ms=10)

    assert "Task · 任任任任任任任…" in message
    assert "任" * 8 not in message


def test_chub_overview_uses_generic_task_line_without_trusted_summary(
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

    assert "▶ S1 · 终端任务" in (result.message or "")
    assert "Task · Running" in (result.message or "")


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

    assert "▶ S1 ! · 语音通知处理" in message
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

    assert result.message == (
        "Request: Failed because Chub state is unavailable. Try again later.\n\n"
        "Sessions\n\nUnavailable\n\nWeekly Unavailable"
    )
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

    assert message == "Weekly Unavailable"


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

    assert "\n\nWeekly 71% · Today 67.4M" in message
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

    assert result.disposition == "reply"
    assert result.message == submitted_task_message(settings, "codex status")
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

    assert result.disposition == "reply"
    assert result.message == submitted_task_message(settings, prompt)
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

    assert result.disposition == "reply"
    assert result.message == submitted_task_message(settings, prompt)
    quick_interactions.submit.assert_called_once()
