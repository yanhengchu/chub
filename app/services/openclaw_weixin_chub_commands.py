from __future__ import annotations

import hashlib
import re
import unicodedata
from dataclasses import dataclass
from typing import Literal


TASK_STATUS_CHECK_PROMPTS = frozenset({"状态", "查询状态"})
CHUB_STATUS_PROMPT = "chub"
CHUB_HELP_PROMPTS = frozenset({"help"})
CHUB_HELP_ALIASES = frozenset({"帮助"})
CHUB_SYNC_PROMPTS = frozenset({"sync"})
CHUB_SYNC_ALIASES = frozenset({"同步"})
CHUB_RESTART_PROMPTS = frozenset({"restart"})
CHUB_RESTART_ALIASES = frozenset({"重启", "重新启动"})
SESSION_RENAME_PROMPT = "rename"
SESSION_RENAME_ALIASES = frozenset({"重命名"})
SESSION_NEW_PROMPT = "new"
SESSION_NEW_ALIASES = frozenset({"新建"})
SESSION_RETRY_PROMPTS = frozenset({"retry"})
SESSION_RETRY_ALIASES = frozenset({"重试", "继续执行"})
SESSION_SWITCH_RETRY_PROMPTS = frozenset({"retry"})
SESSION_SWITCH_RETRY_ALIASES = frozenset({"重试"})
SESSION_NEW_RETRY_PROMPTS = frozenset({"new retry"})
SESSION_NEW_RETRY_ALIASES = frozenset({"新建 重试", "新建 继续执行"})
DIRECT_PROMPT = "direct"
DIRECT_ALIASES = frozenset({"直接执行"})
SESSION_SWITCH_PROMPT = "switch"
ENGLISH_SWITCH_PREFIX_PATTERN = re.compile(
    r"switch\s+S?([1-9])", re.IGNORECASE
)
CHINESE_SWITCH_PREFIX_PATTERN = re.compile(
    r"(?:切换|会话)\s*S?([1-9])", re.IGNORECASE
)
CHINESE_SWITCH_NUMBER_PREFIX_PATTERN = re.compile(
    r"(?:切换|会话)\s*([一二三四五六七八九])"
)
INVALID_SWITCH_SLOT_PATTERN = re.compile(
    r"(?:switch\s+|切换\s*|会话\s*)"
    r"(?:S?\d+|[零〇一二两三四五六七八九十百千万]+)",
    re.IGNORECASE,
)
SESSION_ARCHIVE_PROMPT = "archive"
SESSION_ARCHIVE_PATTERN = re.compile(r"archive\s+S?([1-9])", re.IGNORECASE)
CHINESE_ARCHIVE_PATTERN = re.compile(r"归档\s+S?([1-9])", re.IGNORECASE)
INVALID_ARCHIVE_SLOT_PATTERN = re.compile(
    r"(?:archive|归档)\s*(?:S?\d+|[零〇一二两三四五六七八九十百千万]+)",
    re.IGNORECASE,
)
SESSION_STOP_PROMPT = "stop"
SESSION_STOP_PATTERN = re.compile(r"stop\s+S?([1-9])", re.IGNORECASE)
CHINESE_STOP_PATTERN = re.compile(r"停止\s+S?([1-9])", re.IGNORECASE)
INVALID_STOP_SLOT_PATTERN = re.compile(
    r"(?:stop|停止)\s*(?:S?\d+|[零〇一二两三四五六七八九十百千万]+)",
    re.IGNORECASE,
)
CHINESE_SLOT_NUMBERS = {
    "一": 1,
    "二": 2,
    "三": 3,
    "四": 4,
    "五": 5,
    "六": 6,
    "七": 7,
    "八": 8,
    "九": 9,
}
REQUEST_COMMAND_PATTERNS = (
    (
        "request_cat",
        re.compile(r"cat\s+R([1-9])", re.IGNORECASE),
        re.compile(r"查看需求\s*R?([1-9])", re.IGNORECASE),
        re.compile(r"查看需求\s*([一二三四五六七八九])"),
        re.compile(
            r"(?:cat\s+R|查看需求\s*R?)"
            r"(?:\d+|[零〇一二两三四五六七八九十百千万]+)",
            re.IGNORECASE,
        ),
    ),
    (
        "request_run",
        re.compile(r"run\s+R([1-9])", re.IGNORECASE),
        re.compile(r"执行需求\s*R?([1-9])", re.IGNORECASE),
        re.compile(r"执行需求\s*([一二三四五六七八九])"),
        re.compile(
            r"(?:run\s+R|执行需求\s*R?)"
            r"(?:\d+|[零〇一二两三四五六七八九十百千万]+)",
            re.IGNORECASE,
        ),
    ),
    (
        "request_archive",
        re.compile(r"archive\s+R([1-9])", re.IGNORECASE),
        re.compile(r"归档需求\s*R?([1-9])", re.IGNORECASE),
        re.compile(r"归档需求\s*([一二三四五六七八九])"),
        re.compile(
            r"(?:archive\s+R|归档需求\s*R?)"
            r"(?:\d+|[零〇一二两三四五六七八九十百千万]+)",
            re.IGNORECASE,
        ),
    ),
)
CHINESE_NUMBERED_ALIAS_PATTERN = re.compile(
    r"(切换|会话|停止|归档)\s*([一二三四五六七八九])(?:\s+([\s\S]+))?"
)

WeixinChubCommandKind = Literal[
    "status",
    "help",
    "sync",
    "restart",
    "retry",
    "new_retry",
    "switch_retry",
    "direct",
    "new",
    "rename",
    "stop",
    "archive",
    "switch",
    "request_cat",
    "request_run",
    "request_archive",
    "normal",
]
FIXED_COMMAND_KINDS = frozenset(
    {
        "status",
        "help",
        "sync",
        "restart",
        "retry",
        "new_retry",
        "switch_retry",
        "direct",
        "new",
        "rename",
        "stop",
        "archive",
        "switch",
        "request_cat",
        "request_run",
        "request_archive",
    }
)


@dataclass(frozen=True)
class WeixinChubCommand:
    kind: WeixinChubCommandKind
    normalized_prompt: str
    task_prompt: str | None = None
    requested_index: int | None = None
    invalid_usage: bool = False


def normalize_fixed_prompt(prompt: str) -> str:
    normalized = " ".join(prompt.strip().split())
    while normalized and unicodedata.category(normalized[0]).startswith("P"):
        normalized = normalized[1:].lstrip()
    while normalized and unicodedata.category(normalized[-1]).startswith("P"):
        normalized = normalized[:-1].rstrip()
    return normalized


def strip_command_leading_punctuation(prompt: str) -> str:
    value = prompt.strip()
    while value and unicodedata.category(value[0]).startswith("P"):
        value = value[1:].lstrip()
    return value


def normalize_chinese_numbered_alias(prompt: str) -> str:
    match = CHINESE_NUMBERED_ALIAS_PATTERN.fullmatch(prompt)
    if match is None:
        return prompt
    command, chinese_slot, task = match.groups()
    suffix = f" {task.strip()}" if task else ""
    return f"{command} {CHINESE_SLOT_NUMBERS[chinese_slot]}{suffix}"


def match_spaced_argument(
    prompt: str,
    commands: tuple[str, ...],
) -> str | None:
    value = strip_command_leading_punctuation(prompt)
    folded = value.casefold()
    for command in sorted(commands, key=len, reverse=True):
        if not folded.startswith(command.casefold()):
            continue
        suffix = value[len(command) :]
        if suffix and suffix[0].isspace():
            return suffix.strip() or None
    return None


def match_numbered_command(
    prompt: str,
    patterns: tuple[re.Pattern[str], ...],
) -> tuple[int, str | None] | None:
    value = strip_command_leading_punctuation(prompt)
    for pattern in patterns:
        match = pattern.fullmatch(value)
        if match is None:
            continue
        task = (
            match.group(2).strip()
            if match.lastindex == 2 and match.group(2)
            else None
        )
        return int(match.group(1)), task
    return None


def strip_switch_task_separator(value: str) -> str | None:
    remainder = value.lstrip()
    while remainder and unicodedata.category(remainder[0])[0] in {"P", "S"}:
        remainder = remainder[1:].lstrip()
    return remainder.strip() or None


def match_switch_command(prompt: str) -> tuple[int, str | None] | None:
    value = strip_command_leading_punctuation(prompt).strip()
    for pattern in (
        ENGLISH_SWITCH_PREFIX_PATTERN,
        CHINESE_SWITCH_PREFIX_PATTERN,
        CHINESE_SWITCH_NUMBER_PREFIX_PATTERN,
    ):
        match = pattern.match(value)
        if match is None:
            continue
        remainder = value[match.end() :]
        if remainder and (
            remainder[0].isdigit()
            or remainder[0] in "零〇一二两三四五六七八九十百千万"
        ):
            return None
        slot = CHINESE_SLOT_NUMBERS.get(match.group(1), match.group(1))
        return int(slot), strip_switch_task_separator(remainder)
    return None


def parse_weixin_chub_command(prompt: str) -> WeixinChubCommand:
    normalized = normalize_fixed_prompt(prompt)
    normalized_numbered_alias = normalize_chinese_numbered_alias(normalized)
    folded = normalized.casefold()
    if normalized in TASK_STATUS_CHECK_PROMPTS or folded == CHUB_STATUS_PROMPT:
        return WeixinChubCommand("status", normalized)
    if folded in CHUB_HELP_PROMPTS or normalized in CHUB_HELP_ALIASES:
        return WeixinChubCommand("help", normalized)
    if folded in CHUB_SYNC_PROMPTS or normalized in CHUB_SYNC_ALIASES:
        return WeixinChubCommand("sync", normalized)
    if folded in CHUB_RESTART_PROMPTS or normalized in CHUB_RESTART_ALIASES:
        return WeixinChubCommand("restart", normalized)
    if folded in SESSION_RETRY_PROMPTS or normalized in SESSION_RETRY_ALIASES:
        return WeixinChubCommand("retry", normalized)

    for (
        kind,
        english_pattern,
        chinese_pattern,
        chinese_number_pattern,
        invalid_pattern,
    ) in REQUEST_COMMAND_PATTERNS:
        match = (
            english_pattern.fullmatch(normalized)
            or chinese_pattern.fullmatch(normalized)
            or chinese_number_pattern.fullmatch(normalized)
        )
        if match is not None:
            value = match.group(1)
            slot = CHINESE_SLOT_NUMBERS.get(value, value)
            return WeixinChubCommand(
                kind,
                normalized,
                requested_index=int(slot),
            )
        bare_english = {
            "request_cat": "cat",
            "request_run": "run",
            "request_archive": "archive r",
        }[kind]
        bare_chinese = {
            "request_cat": "查看需求",
            "request_run": "执行需求",
            "request_archive": "归档需求",
        }[kind]
        if (
            normalized.casefold() == bare_english
            or normalized == bare_chinese
            or invalid_pattern.fullmatch(normalized) is not None
            or (
                kind in {"request_cat", "request_run"}
                and re.fullmatch(
                    rf"{'cat' if kind == 'request_cat' else 'run'}\s+S?\d+",
                    normalized,
                    re.IGNORECASE,
                )
                is not None
            )
            or re.match(
                {
                    "request_cat": r"^cat\s+R(?:$|\d|\s)",
                    "request_run": r"^run\s+R(?:$|\d|\s)",
                    "request_archive": r"^archive\s+R(?:$|\d|\s)",
                }[kind],
                normalized,
                re.IGNORECASE,
            )
            is not None
            or re.match(
                {
                    "request_cat": r"^查看需求\s*(?:$|R?\d|R?[零〇一二两三四五六七八九十百千万])",
                    "request_run": r"^执行需求\s*(?:$|R?\d|R?[零〇一二两三四五六七八九十百千万])",
                    "request_archive": r"^归档需求\s*(?:$|R?\d|R?[零〇一二两三四五六七八九十百千万])",
                }[kind],
                normalized,
                re.IGNORECASE,
            )
            is not None
        ):
            return WeixinChubCommand(kind, normalized, invalid_usage=True)

    if (
        folded in SESSION_NEW_RETRY_PROMPTS
        or normalized in SESSION_NEW_RETRY_ALIASES
    ):
        return WeixinChubCommand("new_retry", normalized)

    title = match_spaced_argument(
        prompt, (SESSION_RENAME_PROMPT, *SESSION_RENAME_ALIASES)
    )
    if title is not None:
        return WeixinChubCommand("rename", normalized, task_prompt=title)
    if folded == SESSION_RENAME_PROMPT or normalized in SESSION_RENAME_ALIASES:
        return WeixinChubCommand("rename", normalized)

    direct_task = match_spaced_argument(prompt, (DIRECT_PROMPT, *DIRECT_ALIASES))
    if direct_task is not None:
        return WeixinChubCommand("direct", normalized, task_prompt=direct_task)
    if folded == DIRECT_PROMPT or normalized in DIRECT_ALIASES:
        return WeixinChubCommand("direct", normalized, invalid_usage=True)

    switch_command = match_switch_command(prompt)
    if switch_command is not None:
        requested_index, task = switch_command
        if task is not None and (
            task.casefold() in SESSION_SWITCH_RETRY_PROMPTS
            or task in SESSION_SWITCH_RETRY_ALIASES
        ):
            return WeixinChubCommand(
                "switch_retry",
                normalized,
                requested_index=requested_index,
            )
        return WeixinChubCommand(
            "switch",
            normalized,
            task_prompt=task,
            requested_index=requested_index,
        )
    if (
        folded == SESSION_SWITCH_PROMPT
        or folded.startswith(f"{SESSION_SWITCH_PROMPT} ")
        or normalized in {"切换", "会话"}
        or normalized.startswith(("切换 ", "会话 ", "切换S", "会话S"))
        or INVALID_SWITCH_SLOT_PATTERN.match(normalized) is not None
    ):
        return WeixinChubCommand(
            "switch",
            normalized,
            invalid_usage=True,
        )

    for kind, english, english_pattern, chinese, chinese_pattern, invalid_pattern in (
        (
            "stop",
            SESSION_STOP_PROMPT,
            SESSION_STOP_PATTERN,
            "停止",
            CHINESE_STOP_PATTERN,
            INVALID_STOP_SLOT_PATTERN,
        ),
        (
            "archive",
            SESSION_ARCHIVE_PROMPT,
            SESSION_ARCHIVE_PATTERN,
            "归档",
            CHINESE_ARCHIVE_PATTERN,
            INVALID_ARCHIVE_SLOT_PATTERN,
        ),
    ):
        match = english_pattern.fullmatch(
            normalized
        ) or chinese_pattern.fullmatch(normalized_numbered_alias)
        if match is not None:
            return WeixinChubCommand(
                kind,
                normalized,
                requested_index=int(match.group(1)),
            )
        if (
            folded == english
            or folded.startswith(f"{english} ")
            or normalized == chinese
            or normalized.startswith(f"{chinese} ")
            or invalid_pattern.fullmatch(normalized) is not None
        ):
            return WeixinChubCommand(kind, normalized, invalid_usage=True)

    title = match_spaced_argument(
        prompt, (SESSION_NEW_PROMPT, *SESSION_NEW_ALIASES)
    )
    if title is not None:
        return WeixinChubCommand("new", normalized, task_prompt=title)
    if folded == SESSION_NEW_PROMPT or normalized in SESSION_NEW_ALIASES:
        return WeixinChubCommand("new", normalized, invalid_usage=True)

    return WeixinChubCommand("normal", normalized)


def retry_submission_message_id(
    command_message_id: str,
    original_message_id: str,
) -> str:
    digest = hashlib.sha256(
        f"{command_message_id}\0{original_message_id}".encode("utf-8")
    ).hexdigest()
    return f"retry-{digest}"


def command_task_message_id(command_message_id: str) -> str:
    digest = hashlib.sha256(
        f"{command_message_id}\0command-task".encode("utf-8")
    ).hexdigest()
    return f"command-task-{digest}"


def switch_retry_message_id(command_message_id: str) -> str:
    digest = hashlib.sha256(
        f"{command_message_id}\0switch-retry".encode("utf-8")
    ).hexdigest()
    return f"switch-retry-{digest}"
