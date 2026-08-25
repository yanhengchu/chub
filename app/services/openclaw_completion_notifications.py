from __future__ import annotations

import json
import os
import queue
import shutil
import subprocess
import tempfile
import threading
import time
from dataclasses import dataclass
from typing import Callable, Literal

from app.codex.models import QuickInteractionTask, QuickInteractionWeixinRoute
from app.core.config import OpenClawCompletionNotificationConfig
from app.services.deferred_restart import DeferredRestartOutcome


MAX_COMMAND_OUTPUT_BYTES = 64 * 1024
MAX_COMPLETION_MESSAGE_PARTS = 5
ERROR_SOURCE_LABELS = {
    "chub": "Chub",
    "runtime": "Codex CLI (upstream Runtime)",
}
OVERFLOW_MESSAGE = "结果超过微信发送上限，剩余内容请在 Chub 快速交互页面查看。"
COMPLETION_OVERFLOW_MESSAGE = "More in Chub."
COMPLETION_USAGE_TIMEOUT_SECONDS = 2.0


@dataclass(frozen=True)
class CompletionNotificationResult:
    status: str
    error: str | None = None


class OpenClawCompletionNotifier:
    """Deliver bounded quick-interaction results through the local Weixin bot."""

    def __init__(self, config: OpenClawCompletionNotificationConfig) -> None:
        self.config = config
        self.session_slot_validator: Callable[[int, str], bool] | None = None
        self.session_current_validator: Callable[[int, str], bool] | None = None
        self.session_context_reader: Callable[
            [str], tuple[int | None, str | None]
        ] | None = None
        self.codex_status_reader: Callable[[QuickInteractionWeixinRoute], str] | None = (
            None
        )
        self.completion_usage_reader: Callable[[], str] | None = None

    def notify(
        self,
        task: QuickInteractionTask,
        route: QuickInteractionWeixinRoute | None = None,
    ) -> CompletionNotificationResult:
        if task.notification_route != "weixin-task":
            return CompletionNotificationResult(
                "skipped",
                "页面任务结果仅在 Chub 快速交互页面展示。",
            )
        return self._send(
            task,
            route,
            [],
            disabled_message="微信完成通知未启用。",
            message_factory=lambda: self._messages_for(
                task,
                footer=self._completion_usage_footer(task),
            ),
        )

    def notify_restart(
        self,
        task: QuickInteractionTask,
        route: QuickInteractionWeixinRoute | None,
        outcome: DeferredRestartOutcome,
    ) -> CompletionNotificationResult:
        messages = {
            "succeeded": "Restart: Completed. Chub is available.",
            "start_failed": "Restart: Failed. Check the Chub runtime logs.",
            "sensitive_task_failed": (
                "Restart: Canceled because the related task failed."
            ),
        }
        message = messages.get(outcome)
        if message is None:
            return CompletionNotificationResult("skipped", "本次无需发送微信重启通知。")
        if task.notification_route != "weixin-task":
            return CompletionNotificationResult("skipped", "页面任务不发送微信重启通知。")
        return self._send(
            task,
            route,
            [],
            disabled_message="微信重启通知未启用。",
            message_factory=lambda: [
                f"{message}\n\n{self._restart_codex_status(route)}"
            ],
        )

    def notify_weixin_restart_command(
        self,
        route: QuickInteractionWeixinRoute,
        outcome: DeferredRestartOutcome,
        error: str | None = None,
    ) -> CompletionNotificationResult:
        messages = {
            "succeeded": "Restart: Completed. Chub is available.",
            "cleared": "Restart: Completed. Chub is available.",
            "start_failed": "Restart: Failed. Check the Chub runtime logs.",
            "sensitive_task_failed": (
                "Restart: Canceled because the related task failed."
            ),
        }
        message = messages.get(outcome)
        if message is None:
            return CompletionNotificationResult("skipped", "本次无需发送微信重启结果。")
        if not self.config.enabled:
            return CompletionNotificationResult("skipped", "微信完成通知未启用。")
        return self._send_messages(
            required_account_id=route.account_id,
            recipient=route.recipient,
            messages=[],
            require_unique=True,
            unavailable_status="failed",
            message_factory=lambda: [
                f"{message}\n\n{self._restart_codex_status(route)}"
            ],
        )

    def _restart_codex_status(self, route: QuickInteractionWeixinRoute) -> str:
        if self.codex_status_reader is None:
            return "Sessions\n\nUnavailable\n\nWeekly Unavailable"
        try:
            message = self.codex_status_reader(route)
        except Exception:
            return "Sessions\n\nUnavailable\n\nWeekly Unavailable"
        return message or "Sessions\n\nUnavailable\n\nWeekly Unavailable"

    def notify_weixin_command_result(
        self,
        route: QuickInteractionWeixinRoute,
        message_factory: Callable[[], str],
    ) -> CompletionNotificationResult:
        if not self.config.enabled:
            return CompletionNotificationResult("skipped", "微信完成通知未启用。")
        return self._send_messages(
            required_account_id=route.account_id,
            recipient=route.recipient,
            messages=[],
            require_unique=True,
            unavailable_status="failed",
            message_factory=lambda: [message_factory()],
        )

    def notify_weixin_optimized_task(
        self,
        route: QuickInteractionWeixinRoute,
        *,
        outcome: Literal["started", "not_submitted", "failed"],
        target_session_id: str | None,
        task: str | None = None,
        english: str | None = None,
        error: str | None = None,
    ) -> CompletionNotificationResult:
        if not self.config.enabled:
            return CompletionNotificationResult("skipped", "微信完成通知未启用。")

        target = self._weixin_target_session_line(target_session_id)
        if outcome == "started":
            heading = "Started"
            paragraphs = [target]
            if task:
                paragraphs.append(f"Submitted:\n{task.strip()}")
            if english:
                paragraphs.append(f"English:\n{english.strip()}")
        elif outcome == "not_submitted":
            heading = "Not submitted"
            paragraphs = [target, error or "The task was not executed."]
            if task:
                paragraphs.append(f"Task:\n{task.strip()}")
        else:
            heading = "Optimization failed"
            paragraphs = [target, "The original task was not executed."]
            if error:
                paragraphs.append(f"Reason:\n{error.strip()}")
        return self._send_messages(
            required_account_id=route.account_id,
            recipient=route.recipient,
            messages=[],
            require_unique=True,
            unavailable_status="failed",
            message_factory=lambda: self._bounded_messages(
                heading,
                "\n\n".join(paragraphs),
            ),
        )

    def notify_weixin_translation_confirmation(
        self,
        route: QuickInteractionWeixinRoute,
        *,
        target_session_id: str | None,
        task: str,
        english: str,
    ) -> CompletionNotificationResult:
        if not self.config.enabled:
            return CompletionNotificationResult("skipped", "微信完成通知未启用。")
        body = "\n\n".join(
            (
                self._weixin_target_session_line(target_session_id),
                f"Polished:\n{task.strip()}",
                f"English:\n{english.strip()}",
                "Please confirm.",
            )
        )
        return self._send_messages(
            required_account_id=route.account_id,
            recipient=route.recipient,
            messages=[],
            require_unique=True,
            unavailable_status="failed",
            message_factory=lambda: self._bounded_messages("Translation ready", body),
        )

    def _weixin_target_session_line(
        self,
        session_id: str | None,
    ) -> str:
        slot, title = self._read_session_context(session_id)
        if session_id is None or slot is None or not title:
            return "Target Session unavailable"
        session_line = f"S{slot} · {title}"
        if (
            self.session_slot_validator is not None
            and not self.session_slot_validator(slot, session_id)
        ):
            return f"{session_line} (Unavailable)"
        if (
            self.session_current_validator is not None
            and self.session_current_validator(slot, session_id)
        ):
            return f"▶ {session_line}"
        return session_line

    def _send(
        self,
        task: QuickInteractionTask,
        route: QuickInteractionWeixinRoute | None,
        messages: list[str],
        *,
        disabled_message: str,
        message_factory: Callable[[], list[str]] | None = None,
    ) -> CompletionNotificationResult:
        if not self.config.enabled:
            return CompletionNotificationResult("skipped", disabled_message)
        if task.notification_route != "weixin-task" or route is None:
            return CompletionNotificationResult(
                "failed" if task.notification_route == "weixin-task" else "skipped",
                "微信原路回送信息不可用。"
                if task.notification_route == "weixin-task"
                else "页面任务结果仅在 Chub 快速交互页面展示。",
            )
        account_id = route.account_id
        recipient = route.recipient
        route_specific = True
        return self._send_messages(
            required_account_id=account_id,
            recipient=recipient,
            messages=messages,
            require_unique=route_specific,
            unavailable_status="failed" if route_specific else "skipped",
            message_factory=message_factory,
        )

    def _send_messages(
        self,
        *,
        required_account_id: str | None,
        recipient: str,
        messages: list[str],
        require_unique: bool,
        unavailable_status: str,
        message_factory: Callable[[], list[str]] | None = None,
    ) -> CompletionNotificationResult:
        executable = shutil.which("openclaw")
        if executable is None:
            return CompletionNotificationResult("failed", "OpenClaw 命令不可用。")
        deadline = time.monotonic() + self.config.timeout_seconds
        try:
            account_id = self._running_weixin_account(
                executable,
                required_account_id=required_account_id,
                require_unique=require_unique,
                timeout_seconds=self._remaining_timeout(deadline),
            )
        except (OSError, subprocess.TimeoutExpired, UnicodeError, ValueError) as exc:
            return CompletionNotificationResult(
                unavailable_status,
                str(exc) if isinstance(exc, ValueError) else "无法确认 ClawBot 状态。",
            )
        if message_factory is not None:
            messages = message_factory()
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

    def _messages_for(
        self,
        task: QuickInteractionTask,
        *,
        footer: str | None = None,
    ) -> list[str]:
        if task.kind == "translation":
            content = task.result if task.status == "succeeded" else task.error
            result = (content or "未返回结果。").strip()
            if task.status == "succeeded":
                original = (task.translation_original or "").strip()
                content = f"原文：\n{original}\n\n{result}"
                return self._bounded_messages("文本优化与翻译", content)
            source = (
                ERROR_SOURCE_LABELS.get(task.error_source or "")
                if task.status == "failed"
                else None
            )
            return self._bounded_messages(
                f"文本优化与翻译失败 · {source}" if source else "文本优化与翻译失败",
                result,
            )
        heading = {
            "succeeded": "Done",
            "failed": "Failed",
            "timed_out": "Timed out",
        }.get(task.status, "Finished")
        source = ERROR_SOURCE_LABELS.get(task.error_source or "")
        if task.status == "failed" and source:
            heading = f"{heading} · {source}"
        content = task.result if task.status == "succeeded" else task.error
        summary = (content or "No result.").strip()
        session_line = self._completion_session_line(task)
        single_prefix = self._completion_prefix(
            heading,
            task.summary,
            session_line,
        )
        footer_suffix = f"\n\n{footer}" if footer else ""
        if (
            len(single_prefix) + len(summary) + len(footer_suffix)
            <= self.config.max_message_chars
        ):
            return [f"{single_prefix}{summary}{footer_suffix}"]

        multipart_prefix = self._completion_prefix(
            f"{heading} · {MAX_COMPLETION_MESSAGE_PARTS}/{MAX_COMPLETION_MESSAGE_PARTS}",
            task.summary,
            session_line,
        )
        content_limit = self.config.max_message_chars - len(multipart_prefix)
        final_content_limit = content_limit - len(footer_suffix)
        parts = self._split_text(summary, final_content_limit)
        overflow = len(parts) > MAX_COMPLETION_MESSAGE_PARTS
        if overflow:
            visible = parts[: MAX_COMPLETION_MESSAGE_PARTS - 1]
            remaining = "\n".join(parts[MAX_COMPLETION_MESSAGE_PARTS - 1 :])
            final_limit = (
                content_limit
                - len(COMPLETION_OVERFLOW_MESSAGE)
                - len(footer_suffix)
                - 2
            )
            final, _remainder = self._take_part(remaining, final_limit)
            parts = [
                *visible,
                f"{final.rstrip()}\n\n{COMPLETION_OVERFLOW_MESSAGE}",
            ]

        total = len(parts)
        messages = [
            f"{self._completion_prefix(f'{heading} · {index}/{total}', task.summary, session_line)}{part}"
            for index, part in enumerate(parts, start=1)
        ]
        if footer_suffix:
            messages[-1] = f"{messages[-1]}{footer_suffix}"
        return messages

    def _completion_usage_footer(self, task: QuickInteractionTask) -> str | None:
        if task.kind == "translation" or task.status != "succeeded":
            return None
        if self.completion_usage_reader is None:
            return "Weekly Unavailable"

        result: queue.Queue[str] = queue.Queue(maxsize=1)

        def read_usage() -> None:
            try:
                message = self.completion_usage_reader()
            except Exception:
                message = "Weekly Unavailable"
            try:
                result.put_nowait(message or "Weekly Unavailable")
            except queue.Full:
                pass

        threading.Thread(target=read_usage, daemon=True).start()
        try:
            return result.get(timeout=COMPLETION_USAGE_TIMEOUT_SECONDS)
        except queue.Empty:
            return "Weekly Unavailable"

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
        paragraphs = [heading]
        if session_line:
            paragraphs.append(session_line)
        if task_summary:
            paragraphs.append(f"Task · {task_summary}")
        return "\n\n".join(paragraphs) + "\n\n"

    def _completion_session_line(self, task: QuickInteractionTask) -> str | None:
        slot, title = self._read_session_context(task.session_id)
        if slot is None or not title:
            return None
        suffix = ""
        if (
            self.session_slot_validator is not None
            and not self.session_slot_validator(slot, task.session_id)
        ):
            suffix = " (Unavailable)"
        session_line = f"S{slot} · {title}{suffix}"
        if suffix:
            return session_line
        if (
            self.session_current_validator is not None
            and self.session_current_validator(
                slot,
                task.session_id,
            )
        ):
            return f"▶ {session_line}"
        return session_line

    def _read_session_context(
        self,
        session_id: str | None,
    ) -> tuple[int | None, str | None]:
        if session_id is None or self.session_context_reader is None:
            return None, None
        try:
            return self.session_context_reader(session_id)
        except Exception:
            return None, None

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
