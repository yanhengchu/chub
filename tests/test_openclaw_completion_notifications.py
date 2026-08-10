import json
import subprocess
from pathlib import Path

import pytest

from app.codex.models import (
    QuickInteractionTask,
    QuickInteractionWeixinRoute,
    utc_now,
)
from app.core.config import OpenClawCompletionNotificationConfig
from app.services.openclaw_completion_notifications import (
    OpenClawCompletionNotifier,
)


def task(
    *,
    result: str = "执行完成",
    notification_route: str = "default",
) -> QuickInteractionTask:
    return QuickInteractionTask(
        id="task-1",
        session_id="session-1",
        prompt="执行任务",
        status="succeeded",
        result=result,
        notification_route=notification_route,
        created_at=utc_now(),
        updated_at=utc_now(),
    )


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
    responses = [
        {
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
        },
        {"result": {"messageId": "message-1"}},
    ]

    def run(arguments, **kwargs):
        calls.append(arguments)
        kwargs["stdout"].write(json.dumps(responses.pop(0)).encode())
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
    message = calls[1][calls[1].index("--message") + 1]
    assert len(message) <= 256
    assert message.endswith("完整结果请在 Chub 快速交互页面查看。")


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

    result = notifier.notify(
        task(notification_route="weixin-task"),
        route,
    )

    assert result.status == "sent"
    assert calls[1][calls[1].index("--account") + 1] == "route-account"
    assert calls[1][calls[1].index("--target") + 1] == "origin@im.wechat"


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
