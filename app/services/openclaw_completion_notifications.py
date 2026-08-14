from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import time
from dataclasses import dataclass
from typing import Callable

from app.codex.models import QuickInteractionTask, QuickInteractionWeixinRoute
from app.core.config import OpenClawCompletionNotificationConfig
from app.services.deferred_restart import DeferredRestartOutcome


MAX_COMMAND_OUTPUT_BYTES = 64 * 1024
MAX_COMPLETION_MESSAGE_PARTS = 5
OVERFLOW_MESSAGE = "结果超过微信发送上限，剩余内容请在 Chub 快速交互页面查看。"


@dataclass(frozen=True)
class CompletionNotificationResult:
    status: str
    error: str | None = None


class OpenClawCompletionNotifier:
    """Deliver bounded quick-interaction results through the local Weixin bot."""

    def __init__(self, config: OpenClawCompletionNotificationConfig) -> None:
        self.config = config
        self.session_slot_validator: Callable[[int, str], bool] | None = None
        self.codex_status_reader: Callable[[], str] | None = None

    def notify(
        self,
        task: QuickInteractionTask,
        route: QuickInteractionWeixinRoute | None = None,
    ) -> CompletionNotificationResult:
        messages = self._messages_for(task)
        return self._send(
            task,
            route,
            messages,
            disabled_message="微信完成通知未启用。",
        )

    def notify_restart(
        self,
        task: QuickInteractionTask,
        route: QuickInteractionWeixinRoute | None,
        outcome: DeferredRestartOutcome,
    ) -> CompletionNotificationResult:
        messages = {
            "succeeded": "Chub 已完成自动重启，服务已恢复。",
            "start_failed": (
                "Chub 自动重启未完成："
                + (
                    task.deferred_restart_error
                    or "旧记录没有保存具体原因，请查看 Chub 运行日志。"
                )
            ),
            "sensitive_task_failed": (
                "Chub 已取消自动重启：等待期间有运行资源修改任务异常结束，"
                "请检查任务结果后再决定是否重启。"
            ),
        }
        message = messages.get(outcome)
        if message is None:
            return CompletionNotificationResult("skipped", "本次无需发送微信重启通知。")
        if task.notification_route != "weixin-task":
            return CompletionNotificationResult("skipped", "页面任务不发送微信重启通知。")
        session_line = self._session_line(task)
        message = f"{message}\n\n关联 {session_line or 'Session：Unavailable'}"
        message = f"{message}\n\n关联任务：{task.summary or 'Unavailable'}"
        message = f"{message}\n\n{self._restart_codex_status()}"
        return self._send(
            task,
            route,
            [message],
            disabled_message="微信重启通知未启用。",
        )

    def _restart_codex_status(self) -> str:
        if self.codex_status_reader is None:
            return "Sessions\n\n暂不可用\n\nWeekly 暂不可用 · Tokens 暂不可用"
        try:
            message = self.codex_status_reader()
        except Exception:
            return "Sessions\n\n暂不可用\n\nWeekly 暂不可用 · Tokens 暂不可用"
        return message or "Sessions\n\n暂不可用\n\nWeekly 暂不可用 · Tokens 暂不可用"

    def _send(
        self,
        task: QuickInteractionTask,
        route: QuickInteractionWeixinRoute | None,
        messages: list[str],
        *,
        disabled_message: str,
    ) -> CompletionNotificationResult:
        if not self.config.enabled:
            return CompletionNotificationResult("skipped", disabled_message)
        if task.notification_route == "weixin-task":
            if route is None:
                return CompletionNotificationResult(
                    "failed",
                    "微信原路回送信息不可用。",
                )
            account_id = route.account_id
            recipient = route.recipient
        else:
            recipient = self.config.weixin_recipient
            if not recipient:
                return CompletionNotificationResult(
                    "skipped",
                    "尚未配置微信通知收件人。",
                )
            account_id = None
        executable = shutil.which("openclaw")
        if executable is None:
            return CompletionNotificationResult("failed", "OpenClaw 命令不可用。")
        deadline = time.monotonic() + self.config.timeout_seconds
        try:
            account_id = self._running_weixin_account(
                executable,
                required_account_id=account_id,
                require_unique=task.notification_route == "weixin-task",
                timeout_seconds=self._remaining_timeout(deadline),
            )
        except (OSError, subprocess.TimeoutExpired, UnicodeError, ValueError) as exc:
            return CompletionNotificationResult(
                "failed" if task.notification_route == "weixin-task" else "skipped",
                str(exc) if isinstance(exc, ValueError) else "无法确认 ClawBot 状态。",
            )
        sent = 0
        for message in messages:
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
                    timeout_seconds=self._remaining_timeout(deadline),
                )
            except (OSError, subprocess.TimeoutExpired, UnicodeError, ValueError):
                error = (
                    f"微信通知部分送达（{sent}/{len(messages)}）。"
                    if sent
                    else "微信通知未送达。"
                )
                return CompletionNotificationResult("failed", error)
            sent += 1
        return CompletionNotificationResult("sent")

    def validate_weixin_route(
        self,
        route: QuickInteractionWeixinRoute,
    ) -> str | None:
        if not self.config.enabled:
            return "微信完成通知未启用。"
        executable = shutil.which("openclaw")
        if executable is None:
            return "OpenClaw 命令不可用。"
        try:
            self._running_weixin_account(
                executable,
                required_account_id=route.account_id,
                require_unique=True,
                timeout_seconds=min(self.config.timeout_seconds, 5),
            )
        except (OSError, subprocess.TimeoutExpired, UnicodeError, ValueError) as exc:
            return str(exc) if isinstance(exc, ValueError) else "无法确认 ClawBot 状态。"
        return None

    def _running_weixin_account(
        self,
        executable: str,
        *,
        required_account_id: str | None = None,
        require_unique: bool = False,
        timeout_seconds: float | None = None,
    ) -> str:
        payload = self._run_json(
            executable,
            ["channels", "status", "--json"],
            timeout_seconds=timeout_seconds,
        )
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
        if require_unique and len(running) != 1:
            raise ValueError("未检测到唯一运行中的 ClawBot。")
        if required_account_id is not None:
            if required_account_id not in running:
                raise ValueError("原消息的 ClawBot 当前不可用。")
            return required_account_id
        configured = self.config.weixin_account_id
        if configured:
            if configured not in running:
                raise ValueError("配置的 ClawBot 当前未运行。")
            return configured
        if len(running) != 1:
            raise ValueError("未检测到唯一运行中的 ClawBot。")
        return running[0]

    def _messages_for(self, task: QuickInteractionTask) -> list[str]:
        if task.kind == "translation":
            content = task.result if task.status == "succeeded" else task.error
            result = (content or "未返回结果。").strip()
            if task.status == "succeeded":
                original = (task.translation_original or "").strip()
                content = f"原文：\n{original}\n\n{result}"
                return self._bounded_messages("文本优化与翻译", content)
            return self._bounded_messages(
                "文本优化与翻译失败",
                result,
            )
        heading = {
            "succeeded": "任务执行成功",
            "failed": "任务执行失败",
            "timed_out": "任务执行超时",
        }.get(task.status, "任务执行结束")
        content = task.result if task.status == "succeeded" else task.error
        summary = (content or "未返回结果。").strip()
        single_prefix = self._completion_prefix(
            heading,
            task.summary,
            self._session_line(task),
        )
        if len(single_prefix) + len(summary) <= self.config.max_message_chars:
            return [f"{single_prefix}{summary}"]

        multipart_prefix = self._completion_prefix(
            f"{heading}（{MAX_COMPLETION_MESSAGE_PARTS}/{MAX_COMPLETION_MESSAGE_PARTS}）",
            task.summary,
            self._session_line(task),
        )
        content_limit = self.config.max_message_chars - len(multipart_prefix)
        parts = self._split_text(summary, content_limit)
        overflow = len(parts) > MAX_COMPLETION_MESSAGE_PARTS
        if overflow:
            visible = parts[: MAX_COMPLETION_MESSAGE_PARTS - 1]
            remaining = "\n".join(parts[MAX_COMPLETION_MESSAGE_PARTS - 1 :])
            final_limit = content_limit - len(OVERFLOW_MESSAGE) - 2
            final, _remainder = self._take_part(remaining, final_limit)
            parts = [*visible, f"{final.rstrip()}\n\n{OVERFLOW_MESSAGE}"]

        total = len(parts)
        return [
            f"{self._completion_prefix(f'{heading}（{index}/{total}）', task.summary, self._session_line(task))}{part}"
            for index, part in enumerate(parts, start=1)
        ]

    def _bounded_messages(self, heading: str, content: str) -> list[str]:
        prefix = f"{heading}\n\n"
        if len(prefix) + len(content) <= self.config.max_message_chars:
            return [f"{prefix}{content}"]
        multipart_prefix = (
            f"{heading}（{MAX_COMPLETION_MESSAGE_PARTS}/{MAX_COMPLETION_MESSAGE_PARTS}）\n\n"
        )
        content_limit = self.config.max_message_chars - len(multipart_prefix)
        parts = self._split_text(content, content_limit)
        if len(parts) > MAX_COMPLETION_MESSAGE_PARTS:
            visible = parts[: MAX_COMPLETION_MESSAGE_PARTS - 1]
            remaining = "\n".join(parts[MAX_COMPLETION_MESSAGE_PARTS - 1 :])
            final_limit = content_limit - len(OVERFLOW_MESSAGE) - 2
            final, _remainder = self._take_part(remaining, final_limit)
            parts = [*visible, f"{final.rstrip()}\n\n{OVERFLOW_MESSAGE}"]
        total = len(parts)
        return [
            f"{heading}（{index}/{total}）\n\n{part}"
            for index, part in enumerate(parts, start=1)
        ]

    @staticmethod
    def _completion_prefix(
        heading: str,
        task_summary: str | None,
        session_line: str | None = None,
    ) -> str:
        lines = [heading]
        if session_line:
            lines.append(session_line)
        if task_summary:
            lines.append(f"任务摘要：{task_summary}")
        return "\n\n".join(lines) + "\n\n"

    def _session_line(self, task: QuickInteractionTask) -> str | None:
        slot = task.weixin_session_slot
        title = task.weixin_session_title
        if slot is None or not title:
            return None
        suffix = ""
        if (
            self.session_slot_validator is not None
            and not self.session_slot_validator(slot, task.session_id)
        ):
            suffix = "（已不可切换）"
        return f"Session：{slot} · {title}{suffix}"

    @classmethod
    def _split_text(cls, text: str, limit: int) -> list[str]:
        parts: list[str] = []
        remaining = text
        while remaining:
            part, remaining = cls._take_part(remaining, limit)
            parts.append(part)
        return parts

    @staticmethod
    def _take_part(text: str, limit: int) -> tuple[str, str]:
        if len(text) <= limit:
            return text, ""
        minimum_break = max(1, limit // 2)
        boundary = -1
        for separator in (
            "\n\n",
            "\n",
            "。",
            "！",
            "？",
            ". ",
            "! ",
            "? ",
            "；",
            "; ",
        ):
            candidate = text.rfind(separator, minimum_break, limit)
            if candidate >= 0:
                candidate += len(separator)
                boundary = max(boundary, candidate)
        if boundary < minimum_break:
            boundary = limit
        return text[:boundary].rstrip(), text[boundary:].lstrip()

    @staticmethod
    def _remaining_timeout(deadline: float) -> float:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise subprocess.TimeoutExpired("openclaw", 0)
        return remaining

    def _run_json(
        self,
        executable: str,
        arguments: list[str],
        *,
        timeout_seconds: float | None = None,
    ) -> dict:
        with tempfile.TemporaryFile() as output:
            process = subprocess.run(
                [executable, *arguments],
                stdin=subprocess.DEVNULL,
                stdout=output,
                stderr=subprocess.DEVNULL,
                timeout=timeout_seconds or self.config.timeout_seconds,
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
