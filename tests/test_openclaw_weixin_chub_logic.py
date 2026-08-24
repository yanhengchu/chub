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
        (" chub。 ", "status", None, None, False),
        ("check", "check", None, None, False),
        ("检查。", "check", None, None, False),
        ("USAGE。", "usage", None, None, False),
        ("text", "text_control", None, None, False),
        ("TEXT MODE AUTO。", "text_control", None, None, False),
        ("text list", "text_control", None, None, False),
        ("text ok", "text_control", None, None, False),
        ("text Please check the service status", "text_control", None, None, True),
        ("text-check Please check the service status.", "text_check", None, "Please check the service status", False),
        ("text mode automatic", "text_control", None, None, True),
        ("text-check", "text_check", None, None, True),
        ("help", "help", None, None, False),
        ("帮助。", "help", None, None, False),
        ("model", "model", None, None, False),
        ("MODEL。", "model", None, None, False),
        ("模型", "model", None, None, False),
        ("model list", "model_list", None, None, False),
        ("MODEL LIST。", "model_list", None, None, False),
        ("模型列表", "model_list", None, None, False),
        ("model level", "model_levels", None, None, False),
        ("MODEL LEVEL。", "model_levels", None, None, False),
        ("模型等级", "model_levels", None, None, False),
        ("model level M2", "model_levels", None, None, False),
        ("模型等级 M2", "model_levels", None, None, False),
        ("model use M2", "model_use", None, None, False),
        ("model use L2", "model_use", None, None, False),
        ("model use M2 L3", "model_use", None, None, False),
        ("模型切换 M2 L3", "model_use", None, None, False),
        ("同步。", "sync", None, None, False),
        ("restart", "restart_web", None, None, False),
        ("RESTART WEB。", "restart_web", None, None, False),
        ("重启 Web", "restart_web", None, None, False),
        ("RESTART WORKER", "restart_worker", None, None, False),
        ("重启 Worker", "restart_worker", None, None, False),
        ("RESTART CLAWBOT", "restart_clawbot", None, None, False),
        ("重启 ClawBot", "restart_clawbot", None, None, False),
        ("升级系统", "upgrade", None, None, False),
        ("upgrade", "upgrade", None, None, False),
        ("upgrade status", "normal", None, None, False),
        ("retry", "retry", None, None, False),
        ("重试", "retry", None, None, False),
        ("继续执行", "retry", None, None, False),
        ("stop", "stop", None, None, False),
        ("new retry", "normal", None, None, False),
        ("新建 重试", "normal", None, None, False),
        ("新建 继续执行", "normal", None, None, False),
        ("direct 检查设备", "normal", None, None, False),
        ("直接执行 检查设备", "normal", None, None, False),
        ("direct", "normal", None, None, False),
        ("直接执行", "normal", None, None, False),
        ("直接执行检查设备", "normal", None, None, False),
        ("rename 项目维护", "rename", None, "项目维护", False),
        ("重命名 新标题", "rename", None, "新标题", False),
        ("rename", "normal", None, None, False),
        ("new 检查设备", "new", None, "检查设备", False),
        ("新建 项目维护", "new", None, "项目维护", False),
        ("new", "new", None, None, False),
        ("新建", "new", None, None, False),
        ("switch 3 continue", "switch", 3, "continue", False),
        ("switch 一", "switch", 1, None, False),
        ("switch3 continue", "switch", 3, "continue", False),
        ("switchS3 continue", "switch", 3, "continue", False),
        ("switch s3 continue", "switch", 3, "continue", False),
        ("切换 3 继续处理", "switch", 3, "继续处理", False),
        ("S3", "session_slot", 3, None, False),
        ("s3", "session_slot", 3, None, False),
        ("SS3", "normal", None, None, False),
        ("S", "normal", None, None, False),
        ("会话 S3", "session_slot", 3, None, False),
        ("会话 3", "session_slot", 3, None, False),
        ("sn S3", "normal", None, None, False),
        ("switch 3 retry", "normal", None, None, False),
        ("switch S3 retry", "normal", None, None, False),
        ("切换 3 重试", "normal", None, None, False),
        ("切换3重试", "normal", None, None, False),
        ("切换S3，重试", "normal", None, None, False),
        ("切换S3重试服务", "switch", 3, "重试服务", False),
        ("切换 3 继续执行", "switch", 3, "继续执行", False),
        ("切换S3，这是正文", "switch", 3, "这是正文", False),
        ("切换3这是正文", "switch", 3, "这是正文", False),
        ("S3，这是正文", "session_slot", 3, "这是正文", False),
        ("会话三这是正文", "session_slot", 3, "这是正文", False),
        ("switch S3: continue", "switch", 3, "continue", False),
        ("archive 2", "archive", 2, None, False),
        ("archive 一", "archive", 1, None, False),
        ("archive2", "archive", 2, None, False),
        ("archiveS2", "archive", 2, None, False),
        ("archive S2", "archive", 2, None, False),
        ("归档 2", "archive", 2, None, False),
        ("归档2", "archive", 2, None, False),
        ("归档S2", "archive", 2, None, False),
        ("归档 S2", "archive", 2, None, False),
        ("stop 2", "stop", 2, None, False),
        ("stop 一", "stop", 1, None, False),
        ("stop2", "stop", 2, None, False),
        ("stopS2", "stop", 2, None, False),
        ("stop S2", "stop", 2, None, False),
        ("停止 2", "stop", 2, None, False),
        ("停止2", "stop", 2, None, False),
        ("停止S2", "stop", 2, None, False),
        ("停止 S2", "stop", 2, None, False),
        ("停止服务后检查", "normal", None, None, False),
        ("switch 999", "switch", None, None, True),
        ("S999", "session_slot", None, None, True),
        ("sn 999", "normal", None, None, False),
        ("session switch 2", "normal", None, None, False),
        ("session 2", "normal", None, None, False),
        ("切换2", "switch", 2, None, False),
        ("切换S10正文", "switch", None, None, True),
        ("切换10正文", "switch", None, None, True),
        ("切换二", "switch", 2, None, False),
        ("切换两", "switch", None, None, True),
        ("切换 二", "switch", 2, None, False),
        ("会话二", "session_slot", 2, None, False),
        ("停止二", "stop", 2, None, False),
        ("归档二", "archive", 2, None, False),
        ("cat R2", "request_cat", 2, None, False),
        ("查看 R2", "request_cat", 2, None, False),
        ("查看一", "request_cat", 1, None, False),
        ("查看需求 2", "request_cat", 2, None, False),
        ("查看需求二", "request_cat", 2, None, False),
        ("archive R2", "request_archive", 2, None, False),
        ("归档 R2", "request_archive", 2, None, False),
        ("归档r2", "request_archive", 2, None, False),
        ("归档需求 2", "request_archive", 2, None, False),
        ("归档需求二", "request_archive", 2, None, False),
        ("del 2", "delete", 2, None, False),
        ("delS2", "delete", 2, None, False),
        ("del 二", "delete", 2, None, False),
        ("del R2", "request_delete", 2, None, False),
        ("DEl r2", "request_delete", 2, None, False),
        ("cat R10", "request_cat", None, None, True),
        ("cat 2", "request_cat", None, None, True),
        ("cat R2 extra", "request_cat", None, None, True),
        ("cat RS2", "normal", None, None, False),
        ("cat R", "normal", None, None, False),
        ("归档需求十", "request_archive", None, None, True),
        ("archive R2 extra", "request_archive", None, None, True),
        ("del R10", "request_delete", None, None, True),
        ("del R2 extra", "request_delete", None, None, True),
        ("cat README", "normal", None, None, False),
        ("run tests", "normal", None, None, False),
        ("同步状态", "normal", None, None, False),
        ("session new retrying", "normal", None, None, False),
        ("renameable task", "normal", None, None, False),
        ("sync now", "normal", None, None, False),
        ("upgrade status now", "normal", None, None, False),
        ("system upgrade", "normal", None, None, False),
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
    expected_kind = "normal" if invalid_usage and kind not in {"text_control", "text_check"} else kind
    expected_index = None if invalid_usage else requested_index
    expected_task_prompt = None if invalid_usage else task_prompt

    assert command.kind == expected_kind
    assert command.requested_index == expected_index
    assert command.task_prompt == expected_task_prompt
    assert command.invalid_usage is (invalid_usage and kind in {"text_control", "text_check"})


@pytest.mark.parametrize(
    ("prompt", "kind"),
    [
        ("new 新会话", "new"),
        ("状态", "status"),
        ("help", "help"),
        ("switch 2", "switch"),
        ("S2", "session_slot"),
    ],
)
def test_command_at_text_start_is_a_fixed_command(
    prompt: str,
    kind: str,
) -> None:
    command = parse_weixin_chub_command(prompt)

    assert command.kind == kind


@pytest.mark.parametrize(
    ("prompt", "kind", "task_prompt"),
    [
        (" help ", "help", None),
        ("usage。", "usage", None),
        ("\tnew 新会话", "new", "新会话"),
        (" /direct 检查设备", "normal", None),
    ],
)
def test_leading_whitespace_and_trailing_punctuation_are_normalized(
    prompt: str,
    kind: str,
    task_prompt: str | None,
) -> None:
    command = parse_weixin_chub_command(prompt)

    assert command.kind == kind
    assert command.task_prompt == task_prompt


@pytest.mark.parametrize(
    "prompt",
    [
        "/help",
        "/new",
        "/switch 10",
        "/direct 检查设备",
        "。usage",
        "new retry",
        "switch 2 retry",
        "unknown command",
        "usage extra",
        "model list extra",
        "model level extra",
        "restart later",
        "unknown",
    ],
)
def test_non_matching_command_text_is_a_normal_task(
    prompt: str,
) -> None:
    command = parse_weixin_chub_command(prompt)

    assert command.kind == "normal"
    assert command.normalized_prompt == prompt


def test_every_non_task_command_kind_uses_fixed_reply_contract() -> None:
    assert set(get_args(WeixinChubCommandKind)) == FIXED_COMMAND_KINDS | {"normal"}


@pytest.mark.parametrize(
    ("prompt", "model_index", "level_index"),
    [
        ("model level M2", 2, None),
        ("model use M2", 2, None),
        ("model use L3", None, 3),
        ("model use M2 L3", 2, 3),
    ],
)
def test_model_command_indices_are_parsed(
    prompt: str,
    model_index: int | None,
    level_index: int | None,
) -> None:
    command = parse_weixin_chub_command(prompt)

    assert command.model_index == model_index
    assert command.level_index == level_index


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
        session_mode="quick",
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
        "Retry: Send retry to continue in the current Session."
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
