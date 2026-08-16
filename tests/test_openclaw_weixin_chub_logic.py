from datetime import datetime, timezone
from types import SimpleNamespace
from typing import get_args

import pytest
from pydantic import ValidationError

from app.services.openclaw_weixin_chub_commands import (
    FIXED_COMMAND_KINDS,
    WeixinChubCommandKind,
    command_task_message_id,
    parse_weixin_chub_command,
    retry_submission_message_id,
)
from app.services.openclaw_weixin_chub_messages import (
    ChubOverviewSession,
    build_session_title,
    codex_operation_message,
    dispatch_failure,
    format_chub_overview,
    format_fixed_reply,
    format_session_blocks,
    session_matches_configuration,
    with_task_summary,
)
from app.services.openclaw_weixin_chub_models import (
    WeixinChubModeRuntimeConfig,
    WeixinChubModeState,
    WeixinChubModeSubmissionResult,
)


@pytest.mark.parametrize(
    (
        "prompt",
        "kind",
        "requested_index",
        "task_prompt",
        "invalid_usage",
    ),
    [
        (" 《Chub》。 ", "status", None, None, False),
        ("help", "help", None, None, False),
        ("帮助。", "help", None, None, False),
        ("状态同步。", "sync", None, None, False),
        ("RESTART。", "restart", None, None, False),
        ("重新启动", "restart", None, None, False),
        ("session retry", "retry", None, None, False),
        ("新建会话执行", "new_retry", None, None, False),
        ("新建会话执行 新正文", "new_retry", None, "新正文", False),
        ("rename: 项目维护", "rename", None, "项目维护", False),
        ("重命名 新标题", "rename", None, "新标题", False),
        ("rename", "rename", None, None, False),
        ("session new: 检查设备", "new", None, "检查设备", False),
        ("切换会话三：继续处理", "switch", 3, "继续处理", False),
        ("切换会话 S3", "switch", 3, None, False),
        ("session archive 2", "archive", 2, None, False),
        ("session archive S2", "archive", 2, None, False),
        ("归档会话二 附带正文", "archive", 2, "附带正文", True),
        ("归档 S2", "archive", 2, None, False),
        ("session stop S2", "stop", 2, None, False),
        ("停止会话 S2", "stop", 2, None, False),
        ("停止服务后检查", "normal", None, None, False),
        ("session switch 999", "switch", None, None, True),
        ("session new retrying", "normal", None, None, False),
        ("renameable task", "normal", None, None, False),
        ("sync now", "normal", None, None, False),
        ("codex", "normal", None, None, False),
    ],
)
def test_parse_weixin_chub_command_contract(
    prompt: str,
    kind: str,
    requested_index: int | None,
    task_prompt: str | None,
    invalid_usage: bool,
) -> None:
    command = parse_weixin_chub_command(prompt)

    assert command.kind == kind
    assert command.requested_index == requested_index
    assert command.task_prompt == task_prompt
    assert command.invalid_usage is invalid_usage


def test_every_non_task_command_kind_uses_fixed_reply_contract() -> None:
    assert set(get_args(WeixinChubCommandKind)) == FIXED_COMMAND_KINDS | {"normal"}


def test_fixed_reply_uses_english_labels_and_preserves_task_title() -> None:
    message = format_fixed_reply(
        "任务提交失败，请稍后重试。\n\n"
        "任务摘要：任务提交失败，请稍后重试。\n\n"
        'Rename: Session 1 renamed to "任务摘要：检查服务".\n\n'
        "T1 · 用户原文：任务提交失败，请稍后重试。"
    )

    assert message == (
        "Not submitted · Submission failed. Try again later.\n\n"
        "Task · 任务提交失败，请稍后重试。\n\n"
        'Rename: Session 1 renamed to "任务摘要：检查服务".\n\n'
        "T1 · 用户原文：任务提交失败，请稍后重试。"
    )


def test_stable_command_message_ids_are_namespaced_and_deterministic() -> None:
    retry_id = retry_submission_message_id("command-1", "original-1")
    task_id = command_task_message_id("command-1")

    assert retry_id == retry_submission_message_id("command-1", "original-1")
    assert task_id == command_task_message_id("command-1")
    assert retry_id.startswith("retry-")
    assert task_id.startswith("command-task-")
    assert retry_id != task_id


def test_submission_result_uses_shared_task_summary_limit() -> None:
    summary = "任" * 48

    result = WeixinChubModeSubmissionResult(
        duplicate=False,
        new_session=False,
        message="任务已提交",
        task_summary=summary,
    )

    assert result.task_summary == summary
    with pytest.raises(ValidationError):
        WeixinChubModeSubmissionResult(
            duplicate=False,
            new_session=False,
            message="任务已提交",
            task_summary="任" * 49,
        )


def test_format_chub_overview_uses_only_supplied_snapshot() -> None:
    message = format_chub_overview(
        elapsed_ms=1250,
        readiness=SimpleNamespace(ready=False, message="配置不可用"),
        memory_percent=91,
        disk_percent=40,
        failed_task_notifications=1,
        failed_restart_notifications=2,
        failed_stop_notifications=3,
        sessions=(
            ChubOverviewSession(
                slot=1,
                title="检查服务",
                state="Busy",
                current=True,
                task_summary="检查设备状态",
            ),
        ),
        usage_message="Weekly 75% · Today 2M",
    )

    assert message == (
        "Chub · 1.2s\n\n"
        "Issues\n"
        "1. Chub is not ready (unavailable).\n"
        "2. Memory usage is high: 91%\n"
        "3. Task result notifications failed: 1\n"
        "4. Restart result notifications failed: 2\n"
        "5. Stop result notifications failed: 3\n\n"
        "Sessions\n\n"
        "▶ S1 · 检查服务\n"
        "Task · 检查设备状态\n\n"
        "Weekly 75% · Today 2M"
    )


def test_format_chub_overview_omits_sessions_heading_when_empty() -> None:
    message = format_chub_overview(
        elapsed_ms=25,
        readiness=SimpleNamespace(ready=True),
        memory_percent=20,
        disk_percent=30,
        failed_task_notifications=0,
        failed_restart_notifications=0,
        failed_stop_notifications=0,
        sessions=(),
        usage_message="Weekly 75% · Today 2M",
    )

    assert message == (
        "Chub · 25ms\n\n"
        "No sessions\n\n"
        "Weekly 75% · Today 2M"
    )


def test_session_formatting_and_configuration_match_are_stateless() -> None:
    configuration = WeixinChubModeRuntimeConfig(
        workspace_id="chub",
        permission_mode="full-access",
        model="gpt-5",
        reasoning_effort="high",
    )
    session = SimpleNamespace(
        workspace_id="chub",
        permission_mode="full-access",
        model="gpt-5",
        reasoning_effort="high",
    )

    assert session_matches_configuration(session, configuration)
    assert format_session_blocks([(1, "任务", "Available", True)]) == (
        "Sessions\n\n▶ S1 · 任务"
    )
    assert codex_operation_message("切换状态：成功。", "Sessions\n\nS1") == (
        "切换状态：成功。\n\nSessions\n\nS1"
    )
    session.reasoning_effort = "medium"
    assert not session_matches_configuration(session, configuration)


def test_session_title_display_limit_is_configurable() -> None:
    title = "标" * 27

    display_title = build_session_title(title, 30)

    assert display_title == ("标" * 14) + "…"
    assert len(display_title) == 15
    assert build_session_title(title, 54) == title


def test_session_state_format_uses_task_line_for_busy_session() -> None:
    assert format_session_blocks(
        [
            (1, "当前任务", "Busy", True),
            (2, "空闲任务", "Available", False),
            (3, "异常任务", "Unavailable", False),
        ]
    ) == (
        "Sessions\n\n"
        "▶ S1 · 当前任务\n"
        "Task · Running\n\n"
        "S2 · 空闲任务\n\n"
        "S3 ! · 异常任务"
    )


def test_dispatch_failure_message_contract_is_stable() -> None:
    result = dispatch_failure("in_progress")

    assert result.protocol_version == 3
    assert result.disposition == "reply"
    assert result.message == (
        "Not submitted · The current Session is running.\n\n"
        "Retry: Send session new retry to continue in a new Session."
    )


def test_task_summary_is_inserted_before_status_suffix_once() -> None:
    message = (
        "任务提交失败，请稍后重试。\n\n"
        "Sessions\n\n▶ S1 · 项目维护\n\nWeekly Unavailable"
    )

    result = with_task_summary(message, "检查设备状态")

    assert result == (
        "任务提交失败，请稍后重试。\n"
        "Task · 检查设备状态\n\n"
        "Sessions\n\n▶ S1 · 项目维护\n\nWeekly Unavailable"
    )
    assert with_task_summary(result, "不会重复") == result


def test_legacy_state_round_trip_preserves_compatibility_fields() -> None:
    now = datetime(2026, 8, 15, tzinfo=timezone.utc)
    payload = {
        "version": 1,
        "configuration": {
            "enabled": True,
            "workspace_id": "chub",
            "permission_mode": "full-access",
            "model": None,
            "reasoning_effort": None,
        },
        "session_id": "session-1",
        "pending_retry": None,
        "session_slots": [{"slot": 1, "session_id": "session-1"}],
        "submissions": [
            {
                "message_id": "legacy-message",
                "correlation_id": None,
                "operation_id": "operation-1",
                "delivery_route_fingerprint": "a" * 64,
                "status": "routed",
                "code": "codex_usage_checked",
                "message": "Codex Usage: Weekly 41% left",
                "http_status": 200,
                "session_id": "session-1",
                "task_id": None,
                "new_session": False,
                "session_slot": 1,
                "session_title": "状态查询",
                "dispatch_disposition": "reply",
                "created_at": now.isoformat(),
                "updated_at": now.isoformat(),
            }
        ],
        "restart_operations": [],
    }

    state = WeixinChubModeState.model_validate(payload)
    dumped = state.model_dump(mode="json")

    assert dumped["submissions"][0]["code"] == "codex_usage_checked"
    assert dumped["submissions"][0]["session_slot"] == 1
    assert dumped["session_slots"] == [{"slot": 1, "session_id": "session-1"}]
    with pytest.raises(ValidationError):
        WeixinChubModeState.model_validate({**payload, "unexpected": True})
