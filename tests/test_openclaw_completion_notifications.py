import json
import subprocess
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from app.codex.models import (
    QuickInteractionTask,
    QuickInteractionWeixinRoute,
    utc_now,
)
from app.core.config import OpenClawCompletionNotificationConfig
from app.services.openclaw_completion_notifications import (
    CompletionNotificationResult,
    OpenClawCompletionNotifier,
)


def task(
    *,
    result: str = "执行完成",
    notification_route: str = "default",
    summary: str | None = None,
    weixin_request_slot: int | None = None,
    weixin_request_generation: str | None = None,
    weixin_request_run_id: str | None = None,
    weixin_request_title: str | None = None,
    kind: str = "standard",
    translation_original: str | None = None,
) -> QuickInteractionTask:
    return QuickInteractionTask(
        id="task-1",
        session_id="session-1",
        prompt="执行任务",
        summary=summary,
        weixin_request_slot=weixin_request_slot,
        weixin_request_generation=weixin_request_generation,
        weixin_request_run_id=weixin_request_run_id,
        weixin_request_title=weixin_request_title,
        kind=kind,
        translation_original=translation_original,
        status="succeeded",
        result=result,
        notification_route=notification_route,
        created_at=utc_now(),
        updated_at=utc_now(),
    )


def test_translation_notification_contains_original_polish_and_english() -> None:
    notifier = OpenClawCompletionNotifier(OpenClawCompletionNotificationConfig())

    messages = notifier._messages_for(
        task(
            result="润色：\n请检查服务状态。\n\nEnglish：\nPlease check the service status.",
            kind="translation",
            translation_original="检查下服务咋样",
        )
    )

    assert messages == [
        "文本优化与翻译\n\n原文：\n检查下服务咋样\n\n"
        "润色：\n请检查服务状态。\n\n"
        "English：\nPlease check the service status."
    ]


def test_translation_notification_is_bounded_to_five_parts() -> None:
    notifier = OpenClawCompletionNotifier(
        OpenClawCompletionNotificationConfig(max_message_chars=256)
    )

    messages = notifier._messages_for(
        task(
            result="润色：\n" + "内容" * 2000 + "\n\nEnglish：\n" + "text " * 2000,
            kind="translation",
            translation_original="原文" * 1000,
        )
    )

    assert len(messages) == 5
    assert all(len(message) <= 256 for message in messages)
    assert "结果超过微信发送上限" in messages[-1]


def test_optimized_task_notification_reports_real_submission_state() -> None:
    notifier = OpenClawCompletionNotifier(
        OpenClawCompletionNotificationConfig(enabled=True)
    )
    notifier._send_messages = MagicMock(
        return_value=CompletionNotificationResult("sent")
    )
    notifier.session_slot_validator = MagicMock(return_value=True)
    notifier.session_current_validator = MagicMock(return_value=True)
    notifier.session_context_reader = lambda _session_id: (2, "服务检查")
    delivery_route = QuickInteractionWeixinRoute(
        account_id="weixin-account",
        recipient="owner@im.wechat",
    )

    result = notifier.notify_weixin_optimized_task(
        delivery_route,
        outcome="started",
        target_session_id="session-2",
        task="请检查服务状态。",
        english="Please check the service status.",
    )

    assert result.status == "sent"
    kwargs = notifier._send_messages.call_args.kwargs
    assert kwargs["required_account_id"] == "weixin-account"
    message = kwargs["message_factory"]()[0]
    assert message == (
        "Started\n\n▶ S2 · 服务检查\n\n"
        "Submitted:\n请检查服务状态。\n\n"
        "English:\nPlease check the service status."
    )
    notifier.session_slot_validator.assert_called_once_with(2, "session-2")
    notifier.session_current_validator.assert_called_once_with(2, "session-2")


def test_optimized_task_notification_marks_reused_slot_unavailable() -> None:
    notifier = OpenClawCompletionNotifier(
        OpenClawCompletionNotificationConfig(enabled=True)
    )
    notifier._send_messages = MagicMock(
        return_value=CompletionNotificationResult("sent")
    )
    notifier.session_slot_validator = MagicMock(return_value=False)
    notifier.session_current_validator = MagicMock(return_value=True)
    notifier.session_context_reader = lambda _session_id: (2, "服务检查")

    notifier.notify_weixin_optimized_task(
        QuickInteractionWeixinRoute(
            account_id="weixin-account",
            recipient="owner@im.wechat",
        ),
        outcome="started",
        target_session_id="old-session",
        task="请检查服务状态。",
        english="Please check the service status.",
    )

    message = notifier._send_messages.call_args.kwargs["message_factory"]()[0]
    assert "S2 · 服务检查 (Unavailable)" in message
    assert "▶ S2" not in message
    notifier.session_current_validator.assert_not_called()


def executable(tmp_path: Path) -> Path:
    path = tmp_path / "openclaw"
    path.write_text("#!/bin/sh\n", encoding="utf-8")
    path.chmod(0o700)
    return path


def test_notification_skips_when_recipient_is_not_configured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "shutil.which",
        lambda _name: pytest.fail("OpenClaw should not be inspected without a recipient"),
    )
    notifier = OpenClawCompletionNotifier(OpenClawCompletionNotificationConfig())

    result = notifier.notify(task())

    assert result.status == "skipped"
    assert result.error == "尚未配置微信通知收件人。"


def test_notification_uses_only_running_account_and_fixed_recipient(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    command = executable(tmp_path)
    calls: list[list[str]] = []
    account_status = {
        "channelAccounts": {
            "openclaw-weixin": [
                {
                    "accountId": "account-1",
                    "enabled": True,
                    "configured": True,
                    "running": True,
                    "restartPending": False,
                }
            ]
        }
    }

    def run(arguments, **kwargs):
        calls.append(arguments)
        payload = account_status if len(calls) == 1 else {
            "result": {"messageId": f"message-{len(calls) - 1}"}
        }
        kwargs["stdout"].write(json.dumps(payload).encode())
        return subprocess.CompletedProcess(arguments, 0)

    monkeypatch.setattr("shutil.which", lambda _name: str(command))
    monkeypatch.setattr("subprocess.run", run)
    notifier = OpenClawCompletionNotifier(
        OpenClawCompletionNotificationConfig(
            weixin_recipient="recipient-1@im.wechat",
            max_message_chars=256,
        )
    )

    result = notifier.notify(task(result="结果" * 300))

    assert result.status == "sent"
    assert calls[0][1:] == ["channels", "status", "--json"]
    assert calls[1][1:7] == [
        "message",
        "send",
        "--channel",
        "openclaw-weixin",
        "--account",
        "account-1",
    ]
    assert calls[1][calls[1].index("--target") + 1] == "recipient-1@im.wechat"
    messages = [call[call.index("--message") + 1] for call in calls[1:]]
    assert len(messages) == 3
    assert all(len(message) <= 256 for message in messages)
    assert messages[0].startswith("Done · 1/3\n\n")
    assert messages[-1].startswith("Done · 3/3\n\n")
    assert "".join(message.split("\n\n", 1)[1] for message in messages) == (
        "结果" * 300
    )
    assert all("完整结果请" not in message for message in messages)


def test_short_notification_contains_complete_result_without_page_hint() -> None:
    notifier = OpenClawCompletionNotifier(
        OpenClawCompletionNotificationConfig(max_message_chars=256)
    )

    messages = notifier._messages_for(task(result="完整结果"))

    assert messages == ["Done\n\n完整结果"]


def test_notification_reuses_persisted_task_summary() -> None:
    notifier = OpenClawCompletionNotifier(
        OpenClawCompletionNotificationConfig(max_message_chars=256)
    )

    messages = notifier._messages_for(
        task(result="完整结果", summary="检查 Ubuntu 服务状态")
    )

    assert messages == ["Done\n\nTask · 检查 Ubuntu 服务状态\n\n完整结果"]


def test_notification_includes_stable_session_and_marks_reused_slot() -> None:
    notifier = OpenClawCompletionNotifier(
        OpenClawCompletionNotificationConfig(max_message_chars=256)
    )
    completed = task(
        result="完整结果",
        summary="检查服务",
    )
    notifier.session_context_reader = lambda _session_id: (3, "服务检查")

    notifier.session_slot_validator = lambda slot, session_id: (
        slot == 3 and session_id == "session-1"
    )
    notifier.session_current_validator = lambda slot, session_id: (
        slot == 3 and session_id == "session-1"
    )
    assert notifier._messages_for(completed) == [
        "Done\n\n▶ S3 · 服务检查\n\nTask · 检查服务\n\n完整结果"
    ]

    notifier.session_current_validator = lambda _slot, _session_id: False
    assert "\nS3 · 服务检查\n" in notifier._messages_for(completed)[0]
    assert "▶ S3" not in notifier._messages_for(completed)[0]

    notifier.session_current_validator = lambda _slot, _session_id: True
    notifier.session_slot_validator = lambda _slot, _session_id: False
    assert "S3 · 服务检查 (Unavailable)" in notifier._messages_for(
        completed
    )[0]
    assert "▶ S3" not in notifier._messages_for(completed)[0]


def test_notification_reads_latest_session_title_by_session_id() -> None:
    notifier = OpenClawCompletionNotifier(
        OpenClawCompletionNotificationConfig(max_message_chars=256)
    )
    current_title = {"value": "旧标题"}
    notifier.session_context_reader = lambda _session_id: (3, current_title["value"])
    notifier.session_slot_validator = lambda _slot, _session_id: True

    completed = task(result="完整结果", summary="检查服务")
    assert "S3 · 旧标题" in notifier._messages_for(completed)[0]

    current_title["value"] = "新标题"
    assert "S3 · 新标题" in notifier._messages_for(completed)[0]
    assert "旧标题" not in notifier._messages_for(completed)[0]


def test_notification_includes_request_and_marks_reused_slot() -> None:
    notifier = OpenClawCompletionNotifier(
        OpenClawCompletionNotificationConfig(max_message_chars=256)
    )
    completed = task(
        result="完整结果",
        summary="实现需求",
        weixin_request_slot=2,
        weixin_request_generation="a" * 32,
        weixin_request_run_id="b" * 32,
        weixin_request_title="需求储备",
    )

    notifier.request_slot_validator = lambda slot, generation: (
        slot == 2 and generation == "a" * 32
    )
    assert notifier._messages_for(completed) == [
        "Done\n\nRequest · R2 · 需求储备\n\nTask · 实现需求\n\n完整结果"
    ]

    notifier.request_slot_validator = lambda _slot, _generation: False
    assert "Request · R2 · 需求储备 (Unavailable)" in notifier._messages_for(
        completed
    )[0]

@pytest.mark.parametrize(
    ("status", "error", "expected"),
    [
        (
            "failed",
            "执行失败原因",
            "Failed\n\nTask · 检查设备状态\n\n执行失败原因",
        ),
        (
            "timed_out",
            "执行超时说明",
            "Timed out\n\nTask · 检查设备状态\n\n执行超时说明",
        ),
    ],
)
def test_notification_uses_task_status_heading(
    status: str,
    error: str,
    expected: str,
) -> None:
    notifier = OpenClawCompletionNotifier(
        OpenClawCompletionNotificationConfig(max_message_chars=256)
    )
    failed_task = task(summary="检查设备状态").model_copy(
        update={"status": status, "result": None, "error": error}
    )

    assert notifier._messages_for(failed_task) == [expected]


def test_notification_labels_runtime_error_source() -> None:
    notifier = OpenClawCompletionNotifier(
        OpenClawCompletionNotificationConfig(max_message_chars=256)
    )
    failed_task = task(summary="检查设备状态").model_copy(
        update={
            "status": "failed",
            "result": None,
            "error": "upstream unavailable",
            "error_source": "runtime",
        }
    )

    assert notifier._messages_for(failed_task) == [
        "Failed · Codex CLI (upstream Runtime)\n\nTask · 检查设备状态\n\nupstream unavailable"
    ]


def test_notification_does_not_label_timeout_as_an_error_source() -> None:
    notifier = OpenClawCompletionNotifier(
        OpenClawCompletionNotificationConfig(max_message_chars=256)
    )
    timed_out_translation = task(kind="translation").model_copy(
        update={
            "status": "timed_out",
            "result": None,
            "error": "翻译任务超时",
            "error_source": "chub",
        }
    )

    assert notifier._messages_for(timed_out_translation) == [
        "文本优化与翻译失败\n\n翻译任务超时"
    ]


def test_completion_usage_footer_only_applies_to_successful_main_task() -> None:
    notifier = OpenClawCompletionNotifier(OpenClawCompletionNotificationConfig())
    notifier.completion_usage_reader = MagicMock(
        return_value="Weekly 64% · Today 1.2M"
    )

    assert (
        notifier._completion_usage_footer(task())
        == "Weekly 64% · Today 1.2M"
    )
    assert notifier._completion_usage_footer(
        task().model_copy(update={"status": "failed", "result": None})
    ) is None
    assert notifier._completion_usage_footer(task(kind="translation")) is None

    assert notifier.completion_usage_reader.call_count == 1


def test_completion_usage_footer_degrades_without_affecting_result() -> None:
    notifier = OpenClawCompletionNotifier(OpenClawCompletionNotificationConfig())
    notifier.completion_usage_reader = MagicMock(side_effect=OSError("unavailable"))

    assert notifier._completion_usage_footer(task()) == "Weekly Unavailable"


def test_notification_caps_long_result_at_five_messages() -> None:
    notifier = OpenClawCompletionNotifier(
        OpenClawCompletionNotificationConfig(max_message_chars=256)
    )

    messages = notifier._messages_for(task(result="很长的结果" * 1000))

    assert len(messages) == 5
    assert all(len(message) <= 256 for message in messages)
    assert messages[0].startswith("Done · 1/5\n\n")
    assert messages[-1].startswith("Done · 5/5\n\n")
    assert messages[-1].endswith("More in Chub.")


def test_multipart_success_appends_usage_only_to_final_part() -> None:
    notifier = OpenClawCompletionNotifier(
        OpenClawCompletionNotificationConfig(max_message_chars=256)
    )

    messages = notifier._messages_for(
        task(result="很长的结果" * 1000),
        footer="Weekly 64% · Today 1.2M",
    )

    assert len(messages) == 5
    assert all(len(message) <= 256 for message in messages)
    assert all("Weekly" not in message for message in messages[:-1])
    assert messages[-1].endswith("Weekly 64% · Today 1.2M")
    assert "More in Chub." in messages[-1]


def test_multipart_notification_repeats_summary_within_each_limit() -> None:
    notifier = OpenClawCompletionNotifier(
        OpenClawCompletionNotificationConfig(max_message_chars=256)
    )

    messages = notifier._messages_for(
        task(result="结果" * 300, summary="检查 Ubuntu 服务状态")
    )

    assert len(messages) > 1
    assert all(len(message) <= 256 for message in messages)
    assert all("Task · 检查 Ubuntu 服务状态" in message for message in messages)


def test_multipart_notification_uses_one_current_session_snapshot() -> None:
    notifier = OpenClawCompletionNotifier(
        OpenClawCompletionNotificationConfig(max_message_chars=256)
    )
    current_checks = 0

    def is_current(_slot: int, _session_id: str) -> bool:
        nonlocal current_checks
        current_checks += 1
        return True

    notifier.session_slot_validator = lambda _slot, _session_id: True
    notifier.session_current_validator = is_current
    notifier.session_context_reader = lambda _session_id: (3, "服务检查")
    completed = task(
        result="结果" * 300,
        summary="检查服务",
    )

    messages = notifier._messages_for(completed)

    assert len(messages) > 1
    assert current_checks == 1
    assert all("\n▶ S3 · 服务检查\n" in message for message in messages)


def test_notification_never_exceeds_configured_limit_at_separator_boundary() -> None:
    notifier = OpenClawCompletionNotifier(
        OpenClawCompletionNotificationConfig(max_message_chars=4000)
    )

    messages = notifier._messages_for(task(result=("段落内容。\n\n" * 600)[:4000]))

    assert len(messages) > 1
    assert all(len(message) <= 4000 for message in messages)


def test_notification_reports_partial_delivery_and_stops(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    command = executable(tmp_path)
    calls: list[list[str]] = []

    def run(arguments, **kwargs):
        calls.append(arguments)
        if len(calls) == 1:
            payload = {
                "channelAccounts": {
                    "openclaw-weixin": [
                        {
                            "accountId": "account-1",
                            "enabled": True,
                            "configured": True,
                            "running": True,
                        }
                    ]
                }
            }
            returncode = 0
        else:
            payload = {"result": {"messageId": f"message-{len(calls) - 1}"}}
            returncode = 1 if len(calls) == 3 else 0
        kwargs["stdout"].write(json.dumps(payload).encode())
        return subprocess.CompletedProcess(arguments, returncode)

    monkeypatch.setattr("shutil.which", lambda _name: str(command))
    monkeypatch.setattr("subprocess.run", run)
    notifier = OpenClawCompletionNotifier(
        OpenClawCompletionNotificationConfig(
            weixin_recipient="recipient-1@im.wechat",
            max_message_chars=256,
        )
    )

    result = notifier.notify(task(result="结果" * 300))

    assert result.status == "failed"
    assert result.error == "微信通知部分送达（1/3）。"
    assert len(calls) == 3


def test_notification_parts_share_one_timeout(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    command = executable(tmp_path)
    calls: list[list[str]] = []
    times = iter((0.0, 0.0, 0.0, 21.0))

    def run(arguments, **kwargs):
        calls.append(arguments)
        payload = (
            {
                "channelAccounts": {
                    "openclaw-weixin": [
                        {
                            "accountId": "account-1",
                            "enabled": True,
                            "configured": True,
                            "running": True,
                        }
                    ]
                }
            }
            if len(calls) == 1
            else {"result": {"messageId": "message-1"}}
        )
        kwargs["stdout"].write(json.dumps(payload).encode())
        return subprocess.CompletedProcess(arguments, 0)

    monkeypatch.setattr("shutil.which", lambda _name: str(command))
    monkeypatch.setattr("subprocess.run", run)
    monkeypatch.setattr("time.monotonic", lambda: next(times))
    notifier = OpenClawCompletionNotifier(
        OpenClawCompletionNotificationConfig(
            weixin_recipient="recipient-1@im.wechat",
            timeout_seconds=20,
            max_message_chars=256,
        )
    )

    result = notifier.notify(task(result="结果" * 300))

    assert result.status == "failed"
    assert result.error == "微信通知部分送达（1/3）。"
    assert len(calls) == 2


def test_notification_does_not_fall_back_from_configured_stopped_account(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    command = executable(tmp_path)

    def run(arguments, **kwargs):
        payload = {
            "channelAccounts": {
                "openclaw-weixin": [
                    {
                        "accountId": "other-account",
                        "enabled": True,
                        "configured": True,
                        "running": True,
                    }
                ]
            }
        }
        kwargs["stdout"].write(json.dumps(payload).encode())
        return subprocess.CompletedProcess(arguments, 0)

    monkeypatch.setattr("shutil.which", lambda _name: str(command))
    monkeypatch.setattr("subprocess.run", run)
    notifier = OpenClawCompletionNotifier(
        OpenClawCompletionNotificationConfig(
            weixin_account_id="account-1",
            weixin_recipient="recipient-1@im.wechat",
        )
    )

    result = notifier.notify(task())

    assert result.status == "skipped"
    assert result.error == "配置的 ClawBot 当前未运行。"


def test_notification_reports_command_failure_without_details(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    command = executable(tmp_path)
    call_count = 0

    def run(arguments, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            payload = {
                "channelAccounts": {
                    "openclaw-weixin": [
                        {
                            "accountId": "account-1",
                            "enabled": True,
                            "configured": True,
                            "running": True,
                        }
                    ]
                }
            }
            kwargs["stdout"].write(json.dumps(payload).encode())
            return subprocess.CompletedProcess(arguments, 0)
        return subprocess.CompletedProcess(arguments, 1)

    monkeypatch.setattr("shutil.which", lambda _name: str(command))
    monkeypatch.setattr("subprocess.run", run)
    notifier = OpenClawCompletionNotifier(
        OpenClawCompletionNotificationConfig(
            weixin_recipient="recipient-1@im.wechat",
        )
    )

    result = notifier.notify(task())

    assert result.status == "failed"
    assert result.error == "微信通知未送达。"


def test_recipient_configuration_rejects_non_weixin_identifier() -> None:
    with pytest.raises(ValueError):
        OpenClawCompletionNotificationConfig(weixin_recipient="recipient-1")


def test_weixin_task_uses_its_immutable_route_instead_of_global_target(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    command = executable(tmp_path)
    calls: list[list[str]] = []
    current = {"value": False}

    def run(arguments, **kwargs):
        calls.append(arguments)
        if len(calls) == 1:
            current["value"] = True
        payload = (
            {
                "channelAccounts": {
                    "openclaw-weixin": [
                        {
                            "accountId": "route-account",
                            "enabled": True,
                            "configured": True,
                            "running": True,
                        }
                    ]
                }
            }
            if len(calls) == 1
            else {"result": {"messageId": "message-1"}}
        )
        kwargs["stdout"].write(json.dumps(payload).encode())
        return subprocess.CompletedProcess(arguments, 0)

    monkeypatch.setattr("shutil.which", lambda _name: str(command))
    monkeypatch.setattr("subprocess.run", run)
    notifier = OpenClawCompletionNotifier(
        OpenClawCompletionNotificationConfig(
            weixin_account_id="global-account",
            weixin_recipient="global@im.wechat",
        )
    )
    route = QuickInteractionWeixinRoute(
        account_id="route-account",
        recipient="origin@im.wechat",
    )
    notifier.session_slot_validator = lambda slot, session_id: (
        slot == 3 and session_id == "session-1"
    )
    notifier.session_current_validator = lambda _slot, _session_id: current[
        "value"
    ]
    notifier.session_context_reader = lambda _session_id: (3, "绘画二")
    notifier.completion_usage_reader = lambda: "Weekly 64% · Today 1.2M"

    result = notifier.notify(
        task(
            notification_route="weixin-task",
        ),
        route,
    )

    assert result.status == "sent"
    assert calls[1][calls[1].index("--account") + 1] == "route-account"
    assert calls[1][calls[1].index("--target") + 1] == "origin@im.wechat"
    assert "\n▶ S3 · 绘画二\n" in calls[1][
        calls[1].index("--message") + 1
    ]
    assert calls[1][calls[1].index("--message") + 1].endswith(
        "Weekly 64% · Today 1.2M"
    )


def test_weixin_task_fails_without_route_or_global_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "shutil.which",
        lambda _name: pytest.fail("OpenClaw should not be inspected without the task route"),
    )
    notifier = OpenClawCompletionNotifier(
        OpenClawCompletionNotificationConfig(
            weixin_recipient="global@im.wechat",
        )
    )

    result = notifier.notify(task(notification_route="weixin-task"))

    assert result.status == "failed"
    assert result.error == "微信原路回送信息不可用。"


def test_restart_notification_uses_weixin_task_route(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    command = executable(tmp_path)
    calls: list[list[str]] = []

    def run(arguments, **kwargs):
        calls.append(arguments)
        payload = (
            {
                "channelAccounts": {
                    "openclaw-weixin": [
                        {
                            "accountId": "route-account",
                            "enabled": True,
                            "configured": True,
                            "running": True,
                        }
                    ]
                }
            }
            if len(calls) == 1
            else {"result": {"messageId": "message-1"}}
        )
        kwargs["stdout"].write(json.dumps(payload).encode())
        return subprocess.CompletedProcess(arguments, 0)

    monkeypatch.setattr("shutil.which", lambda _name: str(command))
    monkeypatch.setattr("subprocess.run", run)
    notifier = OpenClawCompletionNotifier(OpenClawCompletionNotificationConfig())
    status_read_call_counts: list[int] = []

    def read_status(_route: QuickInteractionWeixinRoute) -> str:
        status_read_call_counts.append(len(calls))
        return (
            "Sessions\n\n"
            "S1 · session new，我…\n\n"
            "▶ S3 · 服务检查\n\n"
            "Weekly Unavailable"
        )

    status_reader = MagicMock(side_effect=read_status)
    notifier.codex_status_reader = status_reader
    route = QuickInteractionWeixinRoute(
        account_id="route-account",
        recipient="origin@im.wechat",
    )

    result = notifier.notify_restart(
        task(
            notification_route="weixin-task",
            summary="检查 Ubuntu 服务状态",
        ),
        route,
        "succeeded",
    )

    assert result.status == "sent"
    send = calls[1]
    assert send[send.index("--account") + 1] == "route-account"
    assert send[send.index("--target") + 1] == "origin@im.wechat"
    status_reader.assert_called_once_with(route)
    assert status_read_call_counts == [1]
    assert send[send.index("--message") + 1] == (
        "Restart: Completed. Chub is available.\n\n"
        "Sessions\n\n"
        "S1 · session new，我…\n\n"
        "▶ S3 · 服务检查\n\n"
        "Weekly Unavailable"
    )


def test_weixin_restart_command_notification_uses_saved_route() -> None:
    notifier = OpenClawCompletionNotifier(OpenClawCompletionNotificationConfig())
    notifier._send_messages = MagicMock(
        return_value=CompletionNotificationResult("sent")
    )
    route = QuickInteractionWeixinRoute(
        account_id="route-account",
        recipient="origin@im.wechat",
    )

    result = notifier.notify_weixin_restart_command(route, "succeeded")

    assert result.status == "sent"
    kwargs = notifier._send_messages.call_args.kwargs
    assert kwargs["required_account_id"] == "route-account"
    assert kwargs["recipient"] == "origin@im.wechat"
    assert kwargs["messages"] == []
    assert kwargs["require_unique"] is True
    assert kwargs["unavailable_status"] == "failed"
    assert kwargs["message_factory"]() == [
        "Restart: Completed. Chub is available.\n\n"
        "Sessions\n\nUnavailable\n\nWeekly Unavailable"
    ]


def test_weixin_command_result_notification_uses_saved_route() -> None:
    notifier = OpenClawCompletionNotifier(OpenClawCompletionNotificationConfig())
    notifier._send_messages = MagicMock(
        return_value=CompletionNotificationResult("sent")
    )
    route = QuickInteractionWeixinRoute(
        account_id="route-account",
        recipient="origin@im.wechat",
    )

    result = notifier.notify_weixin_command_result(
        route,
        lambda: (
            "Stop: Session 2 stopped.\n\nSessions\n\n"
            "S2 · Title\n\nWeekly Unavailable"
        ),
    )

    assert result.status == "sent"
    kwargs = notifier._send_messages.call_args.kwargs
    assert kwargs["required_account_id"] == "route-account"
    assert kwargs["recipient"] == "origin@im.wechat"
    assert kwargs["require_unique"] is True
    assert kwargs["unavailable_status"] == "failed"
    assert kwargs["messages"] == []
    assert kwargs["message_factory"]() == [
        "Stop: Session 2 stopped.\n\nSessions\n\nS2 · Title\n\nWeekly Unavailable"
    ]


def test_weixin_restart_command_failure_uses_recorded_reason() -> None:
    notifier = OpenClawCompletionNotifier(OpenClawCompletionNotificationConfig())
    notifier._send_messages = MagicMock(
        return_value=CompletionNotificationResult("sent")
    )
    route = QuickInteractionWeixinRoute(
        account_id="route-account",
        recipient="origin@im.wechat",
    )

    notifier.notify_weixin_restart_command(
        route,
        "start_failed",
        "重启脚本返回退出码 1，旧 Chub 实例继续运行。",
    )

    assert notifier._send_messages.call_args.kwargs["message_factory"]() == [
        "Restart: Failed. Check the Chub runtime logs.\n\n"
        "Sessions\n\nUnavailable\n\nWeekly Unavailable"
    ]


def test_restart_notification_skips_page_task_without_inspecting_openclaw(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "shutil.which",
        lambda _name: pytest.fail("Page restart must not inspect OpenClaw"),
    )
    notifier = OpenClawCompletionNotifier(OpenClawCompletionNotificationConfig())

    result = notifier.notify_restart(task(), None, "succeeded")

    assert result.status == "skipped"
    assert result.error == "页面任务不发送微信重启通知。"


def test_restart_failure_notification_uses_recorded_reason() -> None:
    notifier = OpenClawCompletionNotifier(OpenClawCompletionNotificationConfig())
    notifier._send = MagicMock(return_value=CompletionNotificationResult("sent"))
    failed_task = task(notification_route="weixin-task").model_copy(
        update={
            "deferred_restart_status": "start_failed",
            "deferred_restart_error": "重启脚本返回退出码 1，旧 Chub 实例继续运行。",
        }
    )

    notifier.notify_restart(
        failed_task,
        QuickInteractionWeixinRoute(
            account_id="route-account",
            recipient="origin@im.wechat",
        ),
        "start_failed",
    )

    messages = notifier._send.call_args.kwargs["message_factory"]()
    assert messages[0].startswith(
        "Restart: Failed. Check the Chub runtime logs."
    )


def test_restart_notification_omits_legacy_related_context(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    command = executable(tmp_path)
    calls: list[list[str]] = []

    def run(arguments, **kwargs):
        calls.append(arguments)
        payload = (
            {
                "channelAccounts": {
                    "openclaw-weixin": [
                        {
                            "accountId": "route-account",
                            "enabled": True,
                            "configured": True,
                            "running": True,
                        }
                    ]
                }
            }
            if len(calls) == 1
            else {"result": {"messageId": "message-1"}}
        )
        kwargs["stdout"].write(json.dumps(payload).encode())
        return subprocess.CompletedProcess(arguments, 0)

    monkeypatch.setattr("shutil.which", lambda _name: str(command))
    monkeypatch.setattr("subprocess.run", run)
    notifier = OpenClawCompletionNotifier(OpenClawCompletionNotificationConfig())
    route = QuickInteractionWeixinRoute(
        account_id="route-account",
        recipient="origin@im.wechat",
    )

    result = notifier.notify_restart(
        task(notification_route="weixin-task"),
        route,
        "succeeded",
    )

    assert result.status == "sent"
    message = calls[1][calls[1].index("--message") + 1]
    assert "Related Session:" not in message
    assert "Task:" not in message
    assert "Sessions\n\nUnavailable\n\nWeekly Unavailable" in message


def test_weixin_route_validation_requires_one_healthy_clawbot(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    command = executable(tmp_path)

    def run(arguments, **kwargs):
        kwargs["stdout"].write(json.dumps({
            "channelAccounts": {
                "openclaw-weixin": [
                    {
                        "accountId": account,
                        "enabled": True,
                        "configured": True,
                        "running": True,
                    }
                    for account in ("account-1", "account-2")
                ]
            }
        }).encode())
        return subprocess.CompletedProcess(arguments, 0)

    monkeypatch.setattr("shutil.which", lambda _name: str(command))
    monkeypatch.setattr("subprocess.run", run)
    notifier = OpenClawCompletionNotifier(OpenClawCompletionNotificationConfig())

    error = notifier.validate_weixin_route(
        QuickInteractionWeixinRoute(
            account_id="account-1",
            recipient="origin@im.wechat",
        )
    )

    assert error == "未检测到唯一运行中的 ClawBot。"
