from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Iterable

from app.ai_usage.models import AiUsageData
from app.ai_usage.service import AiUsageService
from app.codex.models import CodexQuotaData, CodexTokenUsageData
from app.codex.quick_interactions import build_task_summary
from app.core.response import ApiError
from app.services.openclaw_weixin_chub_models import (
    WeixinChubModeDispatchCode,
    WeixinChubModeDispatchResult,
    WeixinChubModeRuntimeConfig,
)


WEEKLY_WINDOW_MINUTES = 7 * 24 * 60


@dataclass(frozen=True)
class ChubOverviewSession:
    slot: int
    title: str
    state: str
    current: bool
    task_summary: str | None = None


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
    failed_task_notifications: int,
    failed_restart_notifications: int,
    sessions: tuple[ChubOverviewSession, ...] | None,
    usage_message: str,
) -> str:
    anomalies: list[str] = []
    lines = [f"Chub · {format_elapsed_time(elapsed_ms)}"]
    if readiness is None:
        anomalies.append("Chub 状态尚未初始化")
    elif not readiness.ready:
        anomalies.append(readiness.message)
    if memory_percent is not None and memory_percent >= 85:
        anomalies.append(f"内存使用率较高：{memory_percent:.0f}%")
    if disk_percent is not None and disk_percent >= 85:
        anomalies.append(f"磁盘使用率较高：{disk_percent:.0f}%")
    if failed_task_notifications:
        anomalies.append(f"{failed_task_notifications} 个任务结果通知失败")
    if failed_restart_notifications:
        anomalies.append(f"{failed_restart_notifications} 个重启结果通知失败")

    if anomalies:
        lines.extend(["", "异常"])
        lines.extend(
            f"{index}. {message}"
            for index, message in enumerate(dict.fromkeys(anomalies), start=1)
        )

    session_lines = ["Sessions"]
    if sessions:
        for item in sessions:
            session_lines.append(f"S{item.slot} · {item.title}")
            if item.state == "Busy" and item.task_summary:
                session_lines.append(f"T{item.slot} · {item.task_summary}")
            status = f"{item.state} · Current" if item.current else item.state
            session_lines.append(status)
    else:
        session_lines.append("暂无已分配 Session" if sessions == () else "暂不可用")
    lines.extend(["", "\n\n".join(session_lines)])
    lines.extend(["", "Codex", "", usage_message])
    return "\n".join(lines)


def codex_operation_message(operation_status: str, codex_message: str) -> str:
    return f"{operation_status}\n\n{codex_message}"


def switch_candidate_hint(remaining: int) -> str:
    if remaining <= 0:
        return ""
    return "另有未登记的可用 Session，请先发送 sync、同步状态或状态同步后再切换。"


def format_session_blocks(
    entries: Iterable[tuple[int, str, str, bool]],
) -> str:
    paragraphs = ["Sessions"]
    for slot, title, state, current in entries:
        paragraphs.append(f"S{slot} · {title}")
        paragraphs.append(f"{state} · Current" if current else state)
    if len(paragraphs) == 1:
        paragraphs.append("暂无已分配 Session")
    return "\n\n".join(paragraphs)


def format_codex_sessions(
    visible: list[tuple[int, object, str]],
    current_session_id: str | None,
    remaining: int,
) -> str:
    message = format_session_blocks(
        (
            (
                slot,
                build_task_summary(session.title or "未命名 Session"),
                state,
                session.id == current_session_id,
            )
            for slot, session, state in visible
        )
    )
    if remaining:
        message = f"{message}\n\n另有 {remaining} 个"
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
        else "Weekly 暂不可用"
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
        return "Weekly 暂不可用"
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
    }
    return allowed.get(exc.code, "微信任务提交失败。")


def dispatch_failure(
    code: WeixinChubModeDispatchCode,
) -> WeixinChubModeDispatchResult:
    messages = {
        "in_progress": (
            "任务提交失败：当前 Session 正在执行，本任务未提交。\n\n"
            "如需新建 Session 并继续执行本任务，请回复："
            "session new retry 或“新建会话执行”。"
        ),
        "configuration_invalid": (
            "任务提交失败：微信 Chub 模式配置无效，请检查工作区、权限、模型和微信通知配置。"
        ),
        "codex_unavailable": "任务提交失败：Codex 当前不可用，请稍后重试。",
        "delivery_route_invalid": "任务提交失败：无法确认本次消息的微信回送通道，请稍后重试。",
        "message_conflict": "任务提交失败：该消息的回送通道与首次提交不一致。",
        "submission_interrupted": "上次提交被 Chub 重启中断，请重新发送任务。",
        "state_unavailable": "任务提交失败：Chub 当前状态不可用，请稍后重试。",
        "submission_failed": "任务提交失败，请稍后重试。",
    }
    return WeixinChubModeDispatchResult(
        disposition="reply",
        message=messages.get(code, "任务提交失败，请稍后重试。"),
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
