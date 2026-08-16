from __future__ import annotations

import hashlib
import re
import unicodedata
from dataclasses import dataclass
from typing import Literal


TASK_STATUS_CHECK_PROMPTS = frozenset(
    {"查询状态", "状态查询", "检查状态", "状态检查"}
)
CHUB_STATUS_PROMPT = "chub"
CHUB_HELP_PROMPTS = frozenset({"help"})
CHUB_HELP_ALIASES = frozenset({"帮助"})
CHUB_SYNC_PROMPTS = frozenset({"sync"})
CHUB_SYNC_ALIASES = frozenset({"同步状态", "状态同步"})
CHUB_RESTART_PROMPTS = frozenset({"restart"})
CHUB_RESTART_ALIASES = frozenset({"重启", "重新启动"})
SESSION_RENAME_PROMPT = "rename"
SESSION_RENAME_ALIASES = frozenset({"重命名"})
SESSION_NEW_PROMPT = "session new"
SESSION_RETRY_PROMPT = "session retry"
SESSION_NEW_RETRY_PROMPT = "session new retry"
SESSION_SWITCH_PROMPT = "session switch"
SESSION_SWITCH_PATTERN = re.compile(r"session switch s?\s*([1-9])")
SESSION_SWITCH_TASK_PATTERN = re.compile(
    r"session\s+switch\s+s?\s*([1-9])", re.IGNORECASE
)
CHINESE_SWITCH_PATTERN = re.compile(
    r"(?:切换(?:会话)?|会话)\s*[sS]?\s*([1-9一二三四五六七八九])"
)
CHINESE_SWITCH_COMMAND_PATTERN = re.compile(
    r"(?:切换(?:会话)?|会话)"
    r"(?:\s*[sS])?"
    r"(?:\s*[+\-＋－]?[0-9０-９零〇一二三四五六七八九十百千万两]+)?"
)
SESSION_ARCHIVE_PROMPT = "session archive"
SESSION_ARCHIVE_PATTERN = re.compile(r"session archive s?\s*([1-9])")
SESSION_ARCHIVE_TASK_PATTERN = re.compile(
    r"session\s+archive\s+s?\s*([1-9])", re.IGNORECASE
)
CHINESE_ARCHIVE_PATTERN = re.compile(
    r"归档(?:会话)?\s*[sS]?\s*([1-9一二三四五六七八九])"
)
CHINESE_ARCHIVE_COMMAND_PATTERN = re.compile(
    r"归档(?:会话)?(?:\s*[sS])?"
    r"(?:\s*[+\-＋－]?[0-9０-９零〇一二三四五六七八九十百千万两]+)?"
)
SESSION_STOP_PROMPT = "session stop"
SESSION_STOP_PATTERN = re.compile(r"session stop s?\s*([1-9])")
SESSION_STOP_TASK_PATTERN = re.compile(
    r"session\s+stop\s+s?\s*([1-9])", re.IGNORECASE
)
CHINESE_STOP_PATTERN = re.compile(
    r"停止(?:会话)?\s*[sS]?\s*([1-9一二三四五六七八九])"
)
CHINESE_STOP_COMMAND_PATTERN = re.compile(
    r"停止(?:会话)?(?:\s*[sS])?"
    r"(?:\s*[+\-＋－]?[0-9０-９零〇一二三四五六七八九十百千万两]+)?"
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
CONTINUE_RETRY_PROMPTS = frozenset({"新建会话执行"})
COMMAND_TASK_SEPARATORS = frozenset(":：,，.。;；!?！？")

WeixinChubCommandKind = Literal[
    "status",
    "help",
    "sync",
    "restart",
    "retry",
    "new_retry",
    "new",
    "rename",
    "stop",
    "archive",
    "switch",
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
        "new",
        "rename",
        "stop",
        "archive",
        "switch",
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


def command_task_suffix(suffix: str) -> str | None:
    if not suffix or all(
        char.isspace() or char in COMMAND_TASK_SEPARATORS for char in suffix
    ):
        return None
    if suffix[0].isspace():
        return suffix.lstrip().rstrip() or None
    return suffix[1:].lstrip().rstrip() or None


def split_command_task(
    prompt: str,
    commands: tuple[str, ...],
) -> tuple[bool, str | None]:
    value = strip_command_leading_punctuation(prompt)
    folded = value.casefold()
    for command in sorted(commands, key=len, reverse=True):
        if not folded.startswith(command.casefold()):
            continue
        suffix = value[len(command) :]
        if not suffix:
            return True, None
        if not (suffix[0].isspace() or suffix[0] in COMMAND_TASK_SEPARATORS):
            continue
        return True, command_task_suffix(suffix)
    return False, None


def split_numbered_command_task(
    prompt: str,
    pattern: re.Pattern[str],
) -> tuple[int, str | None] | None:
    value = strip_command_leading_punctuation(prompt)
    match = pattern.match(value)
    if match is None:
        return None
    slot = match.group(1)
    suffix = value[match.end() :]
    if suffix and not (
        suffix[0].isspace() or suffix[0] in COMMAND_TASK_SEPARATORS
    ):
        return None
    requested_index = int(slot) if slot.isdigit() else CHINESE_SLOT_NUMBERS[slot]
    return requested_index, command_task_suffix(suffix)


def parse_weixin_chub_command(prompt: str) -> WeixinChubCommand:
    normalized = normalize_fixed_prompt(prompt)
    folded = normalized.casefold()
    if normalized in TASK_STATUS_CHECK_PROMPTS or folded == CHUB_STATUS_PROMPT:
        return WeixinChubCommand("status", normalized)
    if folded in CHUB_HELP_PROMPTS or normalized in CHUB_HELP_ALIASES:
        return WeixinChubCommand("help", normalized)
    if folded in CHUB_SYNC_PROMPTS or normalized in CHUB_SYNC_ALIASES:
        return WeixinChubCommand("sync", normalized)
    if folded in CHUB_RESTART_PROMPTS or normalized in CHUB_RESTART_ALIASES:
        return WeixinChubCommand("restart", normalized)
    if folded == SESSION_RETRY_PROMPT:
        return WeixinChubCommand("retry", normalized)

    matched, title = split_command_task(
        prompt,
        (SESSION_RENAME_PROMPT, *SESSION_RENAME_ALIASES),
    )
    if matched:
        return WeixinChubCommand("rename", normalized, task_prompt=title)

    matched, task = split_command_task(
        prompt,
        (SESSION_NEW_RETRY_PROMPT, *CONTINUE_RETRY_PROMPTS),
    )
    if matched:
        return WeixinChubCommand("new_retry", normalized, task_prompt=task)
    if folded.startswith(SESSION_NEW_RETRY_PROMPT):
        return WeixinChubCommand("normal", normalized)

    matched, task = split_command_task(prompt, (SESSION_NEW_PROMPT, "新建会话"))
    if matched:
        return WeixinChubCommand("new", normalized, task_prompt=task)

    numbered = split_numbered_command_task(
        prompt,
        SESSION_ARCHIVE_TASK_PATTERN,
    ) or split_numbered_command_task(prompt, CHINESE_ARCHIVE_PATTERN)
    if numbered is not None:
        requested_index, task = numbered
        return WeixinChubCommand(
            "archive",
            normalized,
            task_prompt=task,
            requested_index=requested_index,
            invalid_usage=task is not None,
        )
    if CHINESE_ARCHIVE_COMMAND_PATTERN.fullmatch(normalized):
        return WeixinChubCommand("archive", normalized, invalid_usage=True)
    if folded.startswith(SESSION_ARCHIVE_PROMPT):
        match = SESSION_ARCHIVE_PATTERN.fullmatch(folded)
        return WeixinChubCommand(
            "archive",
            normalized,
            requested_index=int(match.group(1)) if match is not None else None,
            invalid_usage=match is None,
        )

    numbered = split_numbered_command_task(
        prompt,
        SESSION_STOP_TASK_PATTERN,
    ) or split_numbered_command_task(prompt, CHINESE_STOP_PATTERN)
    if numbered is not None:
        requested_index, task = numbered
        return WeixinChubCommand(
            "stop",
            normalized,
            task_prompt=task,
            requested_index=requested_index,
            invalid_usage=task is not None,
        )
    if CHINESE_STOP_COMMAND_PATTERN.fullmatch(normalized):
        return WeixinChubCommand("stop", normalized, invalid_usage=True)
    if folded.startswith(SESSION_STOP_PROMPT):
        match = SESSION_STOP_PATTERN.fullmatch(folded)
        return WeixinChubCommand(
            "stop",
            normalized,
            requested_index=int(match.group(1)) if match is not None else None,
            invalid_usage=match is None,
        )

    numbered = split_numbered_command_task(
        prompt,
        SESSION_SWITCH_TASK_PATTERN,
    ) or split_numbered_command_task(prompt, CHINESE_SWITCH_PATTERN)
    if numbered is not None:
        requested_index, task = numbered
        return WeixinChubCommand(
            "switch",
            normalized,
            task_prompt=task,
            requested_index=requested_index,
        )
    if CHINESE_SWITCH_COMMAND_PATTERN.fullmatch(normalized):
        return WeixinChubCommand("switch", normalized, invalid_usage=True)
    if folded.startswith(f"{SESSION_SWITCH_PROMPT} "):
        match = SESSION_SWITCH_PATTERN.fullmatch(folded)
        return WeixinChubCommand(
            "switch",
            normalized,
            requested_index=int(match.group(1)) if match is not None else None,
            invalid_usage=match is None,
        )
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
