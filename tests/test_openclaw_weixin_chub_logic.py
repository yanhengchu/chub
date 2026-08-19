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
        ("同步。", "sync", None, None, False),
        ("RESTART。", "restart", None, None, False),
        ("重新启动", "restart", None, None, False),
        ("SYSTEM UPGRADE STATUS。", "system_upgrade_status", None, None, False),
        ("system upgrade", "system_upgrade", None, None, False),
        ("retry", "retry", None, None, False),
        ("重试", "retry", None, None, False),
        ("继续执行", "retry", None, None, False),
        ("new retry", "new_retry", None, None, False),
        ("新建 重试", "new_retry", None, None, False),
        ("新建 继续执行", "new_retry", None, None, False),
        ("direct 检查设备", "direct", None, "检查设备", False),
        ("直接执行 检查设备", "direct", None, "检查设备", False),
        ("direct", "direct", None, None, True),
        ("直接执行", "direct", None, None, True),
        ("直接执行检查设备", "normal", None, None, False),
        ("rename 项目维护", "rename", None, "项目维护", False),
        ("重命名 新标题", "rename", None, "新标题", False),
        ("rename", "rename", None, None, False),
        ("new 检查设备", "new", None, "检查设备", False),
        ("新建 项目维护", "new", None, "项目维护", False),
        ("switch 3 continue", "switch", 3, "continue", False),
        ("switch s3 continue", "switch", 3, "continue", False),
        ("切换 3 继续处理", "switch", 3, "继续处理", False),
        ("会话 S3", "switch", 3, None, False),
        ("会话 3", "switch", 3, None, False),
        ("switch 3 retry", "switch_retry", 3, None, False),
        ("switch S3 retry", "switch_retry", 3, None, False),
        ("切换 3 重试", "switch_retry", 3, None, False),
        ("切换3重试", "switch_retry", 3, None, False),
        ("切换S3，重试", "switch_retry", 3, None, False),
        ("切换S3重试服务", "switch", 3, "重试服务", False),
        ("切换 3 继续执行", "switch", 3, "继续执行", False),
        ("切换S3，这是正文", "switch", 3, "这是正文", False),
        ("切换3这是正文", "switch", 3, "这是正文", False),
        ("会话三这是正文", "switch", 3, "这是正文", False),
        ("switch S3: continue", "switch", 3, "continue", False),
        ("archive 2", "archive", 2, None, False),
        ("archive S2", "archive", 2, None, False),
        ("归档 2", "archive", 2, None, False),
        ("归档 S2", "archive", 2, None, False),
        ("stop 2", "stop", 2, None, False),
        ("stop S2", "stop", 2, None, False),
        ("停止 2", "stop", 2, None, False),
        ("停止 S2", "stop", 2, None, False),
        ("停止服务后检查", "normal", None, None, False),
        ("switch 999", "switch", None, None, True),
        ("session switch 2", "normal", None, None, False),
        ("切换2", "switch", 2, None, False),
        ("切换S10正文", "switch", None, None, True),
        ("切换10正文", "switch", None, None, True),
        ("切换二", "switch", 2, None, False),
        ("切换两", "switch", None, None, True),
        ("切换 二", "switch", 2, None, False),
        ("会话二", "switch", 2, None, False),
        ("停止二", "stop", 2, None, False),
        ("归档二", "archive", 2, None, False),
        ("cat R2", "request_cat", 2, None, False),
        ("查看需求 2", "request_cat", 2, None, False),
        ("查看需求二", "request_cat", 2, None, False),
        ("run R2", "request_run", 2, None, False),
        ("执行需求 R2", "request_run", 2, None, False),
        ("执行需求二", "request_run", 2, None, False),
        ("archive R2", "request_archive", 2, None, False),
        ("归档需求 2", "request_archive", 2, None, False),
        ("归档需求二", "request_archive", 2, None, False),
        ("cat R10", "request_cat", None, None, True),
        ("cat 2", "request_cat", None, None, True),
        ("cat R2 extra", "request_cat", None, None, True),
        ("run R10", "request_run", None, None, True),
        ("run S2", "request_run", None, None, True),
        ("执行需求二 再执行", "request_run", None, None, True),
        ("归档需求十", "request_archive", None, None, True),
        ("archive R2 extra", "request_archive", None, None, True),
        ("cat README", "normal", None, None, False),
        ("run tests", "normal", None, None, False),
        ("同步状态", "normal", None, None, False),
        ("session new retrying", "normal", None, None, False),
        ("renameable task", "normal", None, None, False),
        ("sync now", "normal", None, None, False),
        ("system upgrade status now", "normal", None, None, False),
        ("system upgrade now", "normal", None, None, False),
        ("systemupgrade", "normal", None, None, False),
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
        "3. Restart result notifications failed: 2\n"
        "4. Stop result notifications failed: 3\n\n"
        "Sessions\n\n"
        "▶ S1 · 检查服务\n\n"
        "Task · 检查设备状态\n\n"
        "No requests\n\n"
        "Weekly 75% · Today 2M"
    )


def test_format_chub_overview_omits_sessions_heading_when_empty() -> None:
    message = format_chub_overview(
        elapsed_ms=25,
        readiness=SimpleNamespace(ready=True),
        memory_percent=20,
        disk_percent=30,
        failed_restart_notifications=0,
        failed_stop_notifications=0,
        sessions=(),
        usage_message="Weekly 75% · Today 2M",
    )

    assert message == (
        "Chub · 25ms\n\n"
        "No sessions\n\n"
        "No requests\n\n"
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
        "▶ S1 · 当前任务\n\n"
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
        "Retry: Send new retry to continue in a new Session."
    )


def test_task_summary_is_inserted_before_status_suffix_once() -> None:
    message = (
        "任务提交失败，请稍后重试。\n\n"
        "Sessions\n\n▶ S1 · 项目维护\n\nWeekly Unavailable"
    )

    result = with_task_summary(message, "检查设备状态")

    assert result == (
        "任务提交失败，请稍后重试。\n\n"
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
