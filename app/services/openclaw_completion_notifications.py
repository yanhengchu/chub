from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass

from app.codex.models import QuickInteractionTask
from app.core.config import OpenClawCompletionNotificationConfig


MAX_COMMAND_OUTPUT_BYTES = 64 * 1024


@dataclass(frozen=True)
class CompletionNotificationResult:
    status: str
    error: str | None = None


class OpenClawCompletionNotifier:
    """Deliver one bounded quick-interaction summary through the local Weixin bot."""

    def __init__(self, config: OpenClawCompletionNotificationConfig) -> None:
        self.config = config

    def notify(self, task: QuickInteractionTask) -> CompletionNotificationResult:
        if not self.config.enabled:
            return CompletionNotificationResult("skipped", "微信完成通知未启用。")
        recipient = self.config.weixin_recipient
        if not recipient:
            return CompletionNotificationResult("skipped", "尚未配置微信通知收件人。")
        executable = shutil.which("openclaw")
        if executable is None:
            return CompletionNotificationResult("failed", "OpenClaw 命令不可用。")
        try:
            account_id = self._running_weixin_account(executable)
        except ValueError as exc:
            return CompletionNotificationResult("skipped", str(exc))
        message = self._message_for(task)
        try:
            self._run_json(
                executable,
                [
                    "message",
                    "send",
                    "--channel",
                    "openclaw-weixin",
                    "--account",
                    account_id,
                    "--target",
                    recipient,
                    "--message",
                    message,
                    "--json",
                ],
            )
        except (OSError, subprocess.TimeoutExpired, ValueError):
            return CompletionNotificationResult("failed", "微信通知未送达。")
        return CompletionNotificationResult("sent")

    def _running_weixin_account(self, executable: str) -> str:
        payload = self._run_json(executable, ["channels", "status", "--json"])
        accounts = payload.get("channelAccounts")
        if not isinstance(accounts, dict):
            raise ValueError("无法确认 ClawBot 状态。")
        entries = accounts.get("openclaw-weixin")
        if not isinstance(entries, list):
            raise ValueError("当前未配置 ClawBot。")
        running = [
            item.get("accountId")
            for item in entries
            if isinstance(item, dict)
            and item.get("enabled") is True
            and item.get("configured") is True
            and item.get("running") is True
            and item.get("restartPending") is not True
            and not item.get("lastError")
            and isinstance(item.get("accountId"), str)
        ]
        configured = self.config.weixin_account_id
        if configured:
            if configured not in running:
                raise ValueError("配置的 ClawBot 当前未运行。")
            return configured
        if len(running) != 1:
            raise ValueError("未检测到唯一运行中的 ClawBot。")
        return running[0]

    def _message_for(self, task: QuickInteractionTask) -> str:
        status = {
            "succeeded": "成功",
            "failed": "失败",
            "timed_out": "超时",
        }.get(task.status, "结束")
        content = task.result if task.status == "succeeded" else task.error
        prefix = f"快速交互已{status}\n"
        suffix = "\n\n完整结果请在 Chub 快速交互页面查看。"
        limit = self.config.max_message_chars - len(prefix) - len(suffix)
        summary = (content or "未返回结果。").strip()
        if len(summary) > limit:
            summary = f"{summary[: max(1, limit - 1)]}…"
        return f"{prefix}{summary}{suffix}"

    def _run_json(self, executable: str, arguments: list[str]) -> dict:
        with tempfile.TemporaryFile() as output:
            process = subprocess.run(
                [executable, *arguments],
                stdin=subprocess.DEVNULL,
                stdout=output,
                stderr=subprocess.DEVNULL,
                timeout=self.config.timeout_seconds,
                check=False,
                env=os.environ.copy(),
            )
            output.seek(0)
            content = output.read(MAX_COMMAND_OUTPUT_BYTES + 1)
        if process.returncode != 0 or len(content) > MAX_COMMAND_OUTPUT_BYTES:
            raise ValueError("OpenClaw 消息发送失败。")
        payload = json.loads(content.decode("utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("OpenClaw 返回无效响应。")
        return payload
