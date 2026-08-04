import json
import subprocess
from pathlib import Path

import pytest

from app.codex.models import QuickInteractionTask, utc_now
from app.core.config import OpenClawCompletionNotificationConfig
from app.services.openclaw_completion_notifications import (
    OpenClawCompletionNotifier,
)


def task(*, result: str = "执行完成") -> QuickInteractionTask:
    return QuickInteractionTask(
        id="task-1",
        session_id="session-1",
        prompt="执行任务",
        status="succeeded",
        result=result,
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
