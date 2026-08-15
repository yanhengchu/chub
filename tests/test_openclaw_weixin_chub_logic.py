from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from app.services.openclaw_weixin_chub_commands import (
    command_task_message_id,
    parse_weixin_chub_command,
    retry_submission_message_id,
)
from app.services.openclaw_weixin_chub_messages import (
    ChubOverviewSession,
    codex_operation_message,
    dispatch_failure,
    format_chub_overview,
    format_session_blocks,
    session_matches_configuration,
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
        ("状态同步。", "sync", None, None, False),
        ("RESTART。", "restart", None, None, False),
        ("session retry", "retry", None, None, False),
        ("新建会话执行", "new_retry", None, None, False),
        ("新建会话执行 新正文", "new_retry", None, "新正文", False),
        ("session new: 检查设备", "new", None, "检查设备", False),
        ("切换会话三：继续处理", "switch", 3, "继续处理", False),
        ("session archive 2", "archive", 2, None, False),
        ("归档会话二 附带正文", "archive", 2, "附带正文", True),
        ("session switch 999", "switch", None, None, True),
        ("session new retrying", "normal", None, None, False),
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


def test_stable_command_message_ids_are_namespaced_and_deterministic() -> None:
    retry_id = retry_submission_message_id("command-1", "original-1")
    task_id = command_task_message_id("command-1")

    assert retry_id == retry_submission_message_id("command-1", "original-1")
    assert task_id == command_task_message_id("command-1")
    assert retry_id.startswith("retry-")
    assert task_id.startswith("command-task-")
    assert retry_id != task_id


def test_submission_result_uses_shared_task_summary_limit() -> None:
    summary = "任" * 27

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
            task_summary="任" * 28,
        )


def test_format_chub_overview_uses_only_supplied_snapshot() -> None:
    message = format_chub_overview(
        elapsed_ms=1250,
        readiness=SimpleNamespace(ready=False, message="配置不可用"),
        memory_percent=91,
        disk_percent=40,
        failed_task_notifications=1,
        failed_restart_notifications=2,
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
        "异常\n"
        "1. 配置不可用\n"
        "2. 内存使用率较高：91%\n"
        "3. 1 个任务结果通知失败\n"
        "4. 2 个重启结果通知失败\n\n"
        "Sessions\n\n"
        "S1 · 检查服务\n\n"
        "T1 · 检查设备状态\n\n"
        "Busy · Current\n\n"
        "Codex\n\n"
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
        "Sessions\n\nS1 · 任务\n\nAvailable · Current"
    )
    assert codex_operation_message("切换状态：成功。", "Sessions\n\nS1") == (
        "切换状态：成功。\n\nSessions\n\nS1"
    )
    session.reasoning_effort = "medium"
    assert not session_matches_configuration(session, configuration)


def test_dispatch_failure_message_contract_is_stable() -> None:
    result = dispatch_failure("in_progress")

    assert result.protocol_version == 3
    assert result.disposition == "reply"
    assert result.message == (
        "任务提交失败：当前 Session 正在执行，本任务未提交。\n\n"
        "如需新建 Session 并继续执行本任务，请回复："
        "session new retry 或“新建会话执行”。"
    )


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
