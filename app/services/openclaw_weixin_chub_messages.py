from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Iterable, Mapping

from app.ai_usage.models import AiUsageData
from app.ai_usage.service import AiUsageService
from app.codex.models import CodexQuotaData, CodexTokenUsageData
from app.codex.quick_interactions import build_task_summary
from app.core.response import ApiError
from app.services.openclaw_weixin_chub_models import (
    MAX_WEIXIN_TASK_SUMMARY_CHARS,
    WeixinChubModeDispatchCode,
    WeixinChubModeDispatchResult,
    WeixinChubModeRuntimeConfig,
)


WEEKLY_WINDOW_MINUTES = 7 * 24 * 60
CHUB_HELP_MESSAGE = "\n\n".join(
    (
        "Commands",
        "Slots · N = SN = 一…九（中文数字可紧连中文指令）",
        "chub · 状态 / 查询状态",
        "help · 帮助",
        "restart · 重启 / 重新启动",
        "sync · 同步",
        "direct <task> · 直接执行 <正文>",
        "new <title> · 新建 <标题>",
        "rename <title> · 重命名 <标题>",
        "switch <1-9|S1-S9> [task] · 切换/会话 <槽位> [正文]",
        "stop <1-9|S1-S9> · 停止 <槽位>",
        "archive <1-9|S1-S9> · 归档 <槽位>",
        "cat <R1-R9> · 查看需求 <槽位>",
        "run <R1-R9> · 执行需求 <槽位>",
        "archive <R1-R9> · 归档需求 <槽位>",
        "retry · 重试 / 继续执行",
        "new retry · 新建 重试 / 新建 继续执行",
        "switch <1-9|S1-S9> retry · 切换/会话 <槽位> 重试",
    )
)


@dataclass(frozen=True)
class ChubOverviewSession:
    slot: int
    title: str
    state: str
    current: bool
    task_summary: str | None = None


@dataclass(frozen=True)
class ChubOverviewRequest:
    slot: int
    title: str


def format_elapsed_time(elapsed_ms: int) -> str:
    milliseconds = max(1, elapsed_ms)
    if milliseconds < 1000:
        return f"{milliseconds}ms"
    seconds = f"{milliseconds / 1000:.1f}".removesuffix(".0")
    return f"{seconds}s"


def format_chub_overview(
    *,
    elapsed_ms: int,
    readiness: object | None,
    memory_percent: float | None,
    disk_percent: float | None,
    failed_restart_notifications: int,
    failed_stop_notifications: int,
    sessions: tuple[ChubOverviewSession, ...] | None,
    usage_message: str,
    requests: tuple[ChubOverviewRequest, ...] | None = (),
) -> str:
    anomalies: list[str] = []
    lines = [f"Chub · {format_elapsed_time(elapsed_ms)}"]
    if readiness is None:
        anomalies.append("Chub status is not initialized.")
    elif not readiness.ready:
        code = getattr(readiness, "code", "unavailable")
        anomalies.append(f"Chub is not ready ({code}).")
    if memory_percent is not None and memory_percent >= 85:
        anomalies.append(f"Memory usage is high: {memory_percent:.0f}%")
    if disk_percent is not None and disk_percent >= 85:
        anomalies.append(f"Disk usage is high: {disk_percent:.0f}%")
    if failed_restart_notifications:
        anomalies.append(
            f"Restart result notifications failed: {failed_restart_notifications}"
        )
    if failed_stop_notifications:
        anomalies.append(
            f"Stop result notifications failed: {failed_stop_notifications}"
        )

    if anomalies:
        lines.extend(["", "Issues"])
        lines.extend(
            f"{index}. {message}"
            for index, message in enumerate(dict.fromkeys(anomalies), start=1)
        )

    session_lines = ["Sessions"]
    if sessions:
        for item in sessions:
            session_block = format_session_name_line(
                item.slot,
                item.title,
                item.state,
                item.current,
            )
            if item.state == "Busy":
                session_block = (
                    f"{session_block}\n\nTask · {item.task_summary or 'Running'}"
                )
            session_lines.append(session_block)
    elif sessions is None:
        session_lines.append("Unavailable")
    lines.extend(
        ["", "No sessions" if sessions == () else "\n\n".join(session_lines)]
    )
    request_lines = ["Requests"]
    if requests:
        request_lines.extend(f"R{item.slot} · {item.title}" for item in requests)
    elif requests is None:
        request_lines.append("Unavailable")
    lines.extend(
        ["", "No requests" if requests == () else "\n\n".join(request_lines)]
    )
    lines.extend(["", usage_message])
    return "\n".join(lines)


def codex_operation_message(operation_status: str, codex_message: str) -> str:
    return f"{operation_status}\n\n{codex_message}"


def format_fixed_reply(message: str) -> str:
    replacements = {
        "任务提交失败：当前 Session 正在执行，本任务未提交。": (
            "Not submitted · The current Session is running."
        ),
        (
            "如需新建 Session 并继续执行本任务，请回复："
            "new retry、“新建 重试”或“新建 继续执行”。"
        ): "Retry: Send new retry to continue in a new Session.",
        (
            "任务提交失败：微信 Chub 模式配置无效，请检查工作区、权限、模型和"
            "微信通知配置。"
        ): "Not submitted · The WeChat Chub configuration is invalid.",
        "任务提交失败：Codex 当前不可用，请稍后重试。": (
            "Not submitted · Codex is unavailable. Try again later."
        ),
        "任务提交失败：无法确认本次消息的微信回送通道，请稍后重试。": (
            "Not submitted · The reply route is unavailable."
        ),
        "任务提交失败：该消息的回送通道与首次提交不一致。": (
            "Request: Rejected because the reply route does not match the original request."
        ),
        "上次提交被 Chub 重启中断，请重新发送任务。": (
            "Not submitted · The previous submission was interrupted by a Chub restart. Send it again."
        ),
        "Chub 重启中断了本次提交，请发送一条新消息重试。": (
            "Request: Interrupted by a Chub restart. Send a new message to try again."
        ),
        "任务提交失败：Chub 当前状态不可用，请稍后重试。": (
            "Not submitted · Chub state is unavailable. Try again later."
        ),
        "任务提交失败，请稍后重试。": "Not submitted · Submission failed. Try again later.",
    }
    paragraphs = message.split("\n\n")
    for index, paragraph in enumerate(paragraphs):
        lines = paragraph.splitlines()
        if not lines:
            continue
        if lines[0].startswith("任务摘要："):
            lines[0] = f"Task · {lines[0].removeprefix('任务摘要：')}"
        else:
            lines[0] = replacements.get(lines[0], lines[0])
        paragraphs[index] = "\n".join(lines)
    return "\n\n".join(paragraphs)


def switch_candidate_hint(remaining: int) -> str:
    if remaining <= 0:
        return ""
    return "Unregistered Sessions are available. Send sync before switching."


def format_session_blocks(
    entries: Iterable[tuple[int, str, str, bool]],
    task_summaries: Mapping[int, str] | None = None,
) -> str:
    paragraphs = ["Sessions"]
    for slot, title, state, current in entries:
        session_block = format_session_name_line(slot, title, state, current)
        if state == "Busy":
            summary = task_summaries.get(slot) if task_summaries is not None else None
            session_block = f"{session_block}\n\nTask · {summary or 'Running'}"
        paragraphs.append(session_block)
    if len(paragraphs) == 1:
        return "No sessions"
    return "\n\n".join(paragraphs)


def format_session_name_line(
    slot: int,
    title: str,
    state: str,
    current: bool,
) -> str:
    current_marker = "▶ " if current else ""
    state_marker = " !" if state == "Unavailable" else ""
    return f"{current_marker}S{slot}{state_marker} · {title}"


def build_session_title(title: str, max_width: int) -> str:
    return build_task_summary(
        title or "Unnamed Session",
        max_chars=MAX_WEIXIN_TASK_SUMMARY_CHARS,
        max_width=max_width,
    )


def build_task_name(summary: str, max_width: int) -> str:
    return build_task_summary(
        summary,
        max_chars=MAX_WEIXIN_TASK_SUMMARY_CHARS,
        max_width=max_width,
    )


def format_task_context(
    status: str,
    task_summary: str,
    *,
    session_slot: int | None = None,
    session_title: str | None = None,
    current: bool = False,
) -> str:
    paragraphs = [status]
    if session_slot is not None and session_title:
        paragraphs.append(
            format_session_name_line(
                session_slot,
                session_title,
                "Available",
                current,
            )
        )
    paragraphs.append(f"Task · {task_summary}")
    return "\n\n".join(paragraphs)


def with_task_summary(
    message: str,
    prompt: str,
    max_width: int = 64,
    *,
    session_slot: int | None = None,
    session_title: str | None = None,
    current: bool = False,
) -> str:
    paragraphs = message.split("\n\n")
    if any(paragraph.startswith("Task · ") for paragraph in paragraphs[1:3]):
        return message
    lines = message.splitlines()
    if any(line.startswith("Task · ") for line in lines[1:3]):
        return message
    first_line, separator, remainder = message.partition("\n")
    if first_line == "Request: Failed because Chub state is unavailable. Try again later.":
        first_line = "Not submitted · Chub state is unavailable. Try again later."
    context = format_task_context(
        first_line,
        build_task_name(prompt, max_width),
        session_slot=session_slot,
        session_title=session_title,
        current=current,
    )
    if not separator:
        return context
    return f"{context}\n{remainder}"


def format_codex_sessions(
    visible: list[tuple[int, object, str]],
    current_session_id: str | None,
    remaining: int,
    session_name_max_width: int,
) -> str:
    message = format_session_blocks(
        (
            (
                slot,
                build_session_title(
                    session.title or "Unnamed Session",
                    session_name_max_width,
                ),
                state,
                session.id == current_session_id,
            )
            for slot, session, state in visible
        )
    )
    if remaining:
        message = f"{message}\n\n{remaining} more Sessions"
    return message


def session_matches_configuration(
    session: object,
    configuration: WeixinChubModeRuntimeConfig,
) -> bool:
    return bool(
        getattr(session, "workspace_id", None) == configuration.workspace_id
        and getattr(session, "permission_mode", None) == configuration.permission_mode
        and configuration.permission_mode != "ask"
        and (
            configuration.model is None
            or getattr(session, "model", None) == configuration.model
        )
        and (
            configuration.reasoning_effort is None
            or getattr(session, "reasoning_effort", None)
            == configuration.reasoning_effort
        )
    )


def codex_usage_message(
    quota: CodexQuotaData,
    usage: CodexTokenUsageData,
) -> str:
    weekly = next(
        (
            window
            for window in quota.windows
            if window.window_duration_minutes == WEEKLY_WINDOW_MINUTES
        ),
        None,
    )
    weekly_text = (
        f"Weekly {weekly.remaining_percent}%"
        if weekly is not None
        else "Weekly Unavailable"
    )
    today = datetime.now().astimezone().date()
    today_bucket = next(
        (bucket for bucket in usage.daily_usage if bucket.start_date == today),
        None,
    )
    if usage.status == "available" and today_bucket is not None:
        return (
            f"{weekly_text} · Today "
            f"{AiUsageService.compact_tokens(today_bucket.tokens)}"
        )
    return weekly_text


def usage_message(value: object) -> str:
    if isinstance(value, AiUsageData):
        if value.status == "available" and value.display.short:
            return value.display.short
        return "Weekly Unavailable"
    quota, usage = value
    return codex_usage_message(quota, usage)


def compact_token_count(tokens: int) -> str:
    for divisor, suffix in (
        (1_000_000_000, "B"),
        (1_000_000, "M"),
        (1_000, "K"),
    ):
        if tokens >= divisor:
            return f"{tokens / divisor:.1f}{suffix}"
    return str(tokens)


def safe_submission_error(exc: ApiError) -> str:
    allowed = {
        "quick_interaction_in_progress": "微信通道当前绑定 Session 正在执行任务，请等待完成。",
        "quick_interaction_terminal_working": "微信通道当前绑定 Session 正在由终端使用。",
        "quick_interaction_terminal_active": "微信通道当前绑定 Session 不能执行快速交互。",
        "quick_interaction_writer_active": (
            "微信通道当前绑定 Session 仍由实时终端占用，请先停止终端。"
        ),
        "codex_writer_status_unavailable": (
            "暂时无法确认微信通道当前绑定 Session 是否可写，请稍后重试。"
        ),
        "weixin_chub_mode_session_reclaim_failed": (
            "微信通道当前绑定 Session 状态未知且未能安全停止，请稍后重试。"
        ),
        "quick_interaction_requires_terminal": "当前权限不支持微信快速交互。",
        "codex_model_unavailable": "所选 Codex 模型当前不可用。",
        "codex_reasoning_effort_unsupported": "所选推理等级当前不可用。",
        "weixin_chub_mode_session_slots_full": (
            "9 个微信 Session 槽位已满，请先归档或删除一个 Session。"
        ),
        "weixin_chub_mode_target_unavailable": (
            "原目标 Session 已不可用，本次任务未执行。"
        ),
        "weixin_chub_mode_in_progress": (
            "目标 Session 正在执行其他任务，本次任务已丢弃。"
        ),
        "weixin_translation_unavailable": (
            "文本优化服务当前不可用，本次任务未执行。"
        ),
    }
    return allowed.get(exc.code, "微信任务提交失败。")


def dispatch_failure(
    code: WeixinChubModeDispatchCode,
) -> WeixinChubModeDispatchResult:
    messages = {
        "in_progress": (
            "Not submitted · The current Session is running.\n\n"
            "Retry: Send new retry to continue in a new Session."
        ),
        "configuration_invalid": (
            "Not submitted · The WeChat Chub configuration is invalid."
        ),
        "codex_unavailable": "Not submitted · Codex is unavailable. Try again later.",
        "delivery_route_invalid": "Not submitted · The reply route is unavailable.",
        "message_conflict": "Not submitted · The reply route does not match the original request.",
        "submission_interrupted": "Not submitted · A Chub restart interrupted the previous submission. Send it again.",
        "state_unavailable": "Request: Failed because Chub state is unavailable. Try again later.",
        "submission_failed": "Not submitted · Submission failed. Try again later.",
    }
    return WeixinChubModeDispatchResult(
        disposition="reply",
        message=messages.get(
            code,
            "Not submitted · Submission failed. Try again later.",
        ),
    )


def dispatch_failure_from_error(exc: ApiError) -> WeixinChubModeDispatchResult:
    code_map: dict[str, WeixinChubModeDispatchCode] = {
        "weixin_chub_mode_in_progress": "in_progress",
        "weixin_chub_mode_configuration_invalid": "configuration_invalid",
        "weixin_chub_mode_codex_unavailable": "codex_unavailable",
        "weixin_chub_mode_delivery_route_invalid": "delivery_route_invalid",
        "weixin_chub_mode_message_conflict": "message_conflict",
        "weixin_chub_mode_submission_interrupted": "submission_interrupted",
        "weixin_chub_mode_state_unavailable": "state_unavailable",
    }
    return dispatch_failure(code_map.get(exc.code, "submission_failed"))
