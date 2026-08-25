from __future__ import annotations

import hashlib
import re
import unicodedata
from dataclasses import dataclass
from typing import Literal


CHUB_STATUS_PROMPT = "chub"
CHUB_CHECK_PROMPT = "check"
CHUB_HELP_PROMPT = "help"
CHUB_HELP_TOPICS = frozenset({"model", "text", "session", "request", "system"})
CHUB_USAGE_PROMPT = "usage"
TEXT_PROMPT = "text"
TEXT_CHECK_PROMPT = "text-check"
TEXT_MODE_VALUES = frozenset({"direct", "auto", "confirm"})
CHUB_MODEL_PROMPT = "model"
CHUB_MODEL_LIST_PROMPT = "model list"
CHUB_MODEL_LEVELS_PROMPT = "model level"
MODEL_LEVEL_TARGET_PATTERN = re.compile(r"model\s+level\s+(M[1-9][0-9]*)", re.I)
MODEL_USE_PATTERN = re.compile(
    r"model\s+use\s+(?:(M[1-9][0-9]*)(?:\s+(L[1-9][0-9]*))?|(L[1-9][0-9]*))",
    re.I,
)
CHUB_SYNC_PROMPT = "sync"
CHUB_RESTART_WEB_PROMPTS = frozenset({"restart", "restart web"})
CHUB_RESTART_WORKER_PROMPT = "restart worker"
CHUB_RESTART_CLAWBOT_PROMPT = "restart clawbot"
CHUB_UPGRADE_PROMPT = "upgrade"
SESSION_RENAME_PROMPT = "rename"
SESSION_NEW_PROMPT = "new"
SESSION_ARCHIVE_PROMPT = "archive"
SESSION_DELETE_PROMPT = "del"
SESSION_STOP_PROMPT = "stop"
SESSION_SLOT_PATTERN = re.compile(r"S([1-9])(?:\s+([\s\S]+))?", re.IGNORECASE)
SESSION_OPERATION_PATTERN = re.compile(
    r"(stop|archive|del)(?:\s+S([1-9]))?", re.IGNORECASE
)
REQUEST_COMMAND_PATTERN = re.compile(
    r"(cat|archive|del)\s+R([1-9])", re.IGNORECASE
)

WeixinChubCommandKind = Literal[
    "status",
    "check",
    "usage",
    "text_control",
    "text_check",
    "help",
    "model",
    "model_list",
    "model_levels",
    "model_use",
    "sync",
    "restart_web",
    "restart_worker",
    "restart_clawbot",
    "upgrade",
    "retry",
    "new",
    "rename",
    "stop",
    "archive",
    "delete",
    "session_slot",
    "request_cat",
    "request_archive",
    "request_delete",
    "normal",
]
FIXED_COMMAND_KINDS = frozenset(
    {
        "status",
        "check",
        "usage",
        "text_control",
        "text_check",
        "help",
        "model",
        "model_list",
        "model_levels",
        "model_use",
        "sync",
        "restart_web",
        "restart_worker",
        "restart_clawbot",
        "upgrade",
        "retry",
        "new",
        "rename",
        "stop",
        "archive",
        "delete",
        "session_slot",
        "request_cat",
        "request_archive",
        "request_delete",
    }
)


@dataclass(frozen=True)
class WeixinChubCommand:
    kind: WeixinChubCommandKind
    normalized_prompt: str
    task_prompt: str | None = None
    processing_mode: Literal["direct", "auto", "confirm"] | None = None
    text_action: Literal["mode", "list", "ok", "next", "cancel"] | None = None
    requested_index: int | None = None
    model_index: int | None = None
    level_index: int | None = None
    invalid_usage: bool = False


def normalize_fixed_prompt(prompt: str) -> str:
    normalized = " ".join(prompt.strip().split())
    while normalized and unicodedata.category(normalized[-1]).startswith("P"):
        normalized = normalized[:-1].rstrip()
    return normalized


def strip_command_whitespace(prompt: str) -> str:
    return prompt.strip()


def command_prompt(prompt: str) -> str | None:
    """Return the message body for one command at the beginning of the text."""
    value = prompt.strip()
    return value or None


def match_spaced_argument(
    prompt: str,
    commands: tuple[str, ...],
) -> str | None:
    value = strip_command_whitespace(prompt)
    folded = value.casefold()
    for command in sorted(commands, key=len, reverse=True):
        if not folded.startswith(command.casefold()):
            continue
        suffix = value[len(command) :]
        if suffix and suffix[0].isspace():
            return suffix.strip() or None
    return None


def match_session_slot_command(
    prompt: str,
    pattern: re.Pattern[str],
) -> tuple[int, str | None] | None:
    value = strip_command_whitespace(prompt)
    match = pattern.fullmatch(value)
    if match is not None:
        return int(match.group(1)), match.group(2).strip() if match.group(2) else None
    normalized = normalize_fixed_prompt(value)
    if pattern.fullmatch(normalized) is not None:
        return int(normalized.removesuffix("。")[-1]), None
    return None


def parse_weixin_chub_command(prompt: str) -> WeixinChubCommand:
    command = command_prompt(prompt)
    if command is None:
        return WeixinChubCommand("normal", prompt)

    normalized = normalize_fixed_prompt(command)
    folded = normalized.casefold()
    if folded == CHUB_STATUS_PROMPT:
        return WeixinChubCommand("status", normalized)
    if folded == CHUB_CHECK_PROMPT:
        return WeixinChubCommand("check", normalized)
    if folded == CHUB_USAGE_PROMPT:
        return WeixinChubCommand("usage", normalized)
    text_parts = normalized.split()
    text_check_parts = normalized.split(maxsplit=1)
    if text_check_parts and text_check_parts[0].casefold() == TEXT_CHECK_PROMPT:
        if len(text_check_parts) == 2:
            return WeixinChubCommand("text_check", normalized, task_prompt=text_check_parts[1])
        return WeixinChubCommand("text_check", normalized, invalid_usage=True)
    if text_parts and text_parts[0].casefold() == TEXT_PROMPT:
        if len(text_parts) == 1:
            return WeixinChubCommand("text_control", normalized, text_action="mode")
        action = text_parts[1].casefold()
        if action in {"ok", "next", "cancel", "list"} and len(text_parts) == 2:
            return WeixinChubCommand("text_control", normalized, text_action=action)
        if action == "mode":
            if len(text_parts) == 2:
                return WeixinChubCommand("text_control", normalized, text_action="mode")
            if len(text_parts) == 3 and text_parts[2].casefold() in TEXT_MODE_VALUES:
                return WeixinChubCommand(
                    "text_control",
                    normalized,
                    processing_mode=text_parts[2].casefold(),
                    text_action="mode",
                )
        return WeixinChubCommand("text_control", normalized, invalid_usage=True)
    help_parts = folded.split()
    if help_parts == [CHUB_HELP_PROMPT]:
        return WeixinChubCommand("help", normalized)
    if len(help_parts) == 2 and help_parts[0] == CHUB_HELP_PROMPT and help_parts[1] in CHUB_HELP_TOPICS:
        return WeixinChubCommand("help", normalized, task_prompt=help_parts[1])
    if folded == CHUB_MODEL_PROMPT:
        return WeixinChubCommand("model", normalized)
    if folded == CHUB_MODEL_LIST_PROMPT:
        return WeixinChubCommand("model_list", normalized)
    if folded == CHUB_MODEL_LEVELS_PROMPT:
        return WeixinChubCommand("model_levels", normalized)
    level_target = MODEL_LEVEL_TARGET_PATTERN.fullmatch(normalized)
    if level_target is not None:
        return WeixinChubCommand(
            "model_levels",
            normalized,
            model_index=int(level_target.group(1)[1:]),
        )
    model_use = MODEL_USE_PATTERN.fullmatch(normalized)
    if model_use is not None:
        model_reference, paired_level, level_reference = model_use.groups()
        selected_level = paired_level or level_reference
        return WeixinChubCommand(
            "model_use",
            normalized,
            model_index=(int(model_reference[1:]) if model_reference else None),
            level_index=(int(selected_level[1:]) if selected_level else None),
        )
    if folded == CHUB_SYNC_PROMPT:
        return WeixinChubCommand("sync", normalized)
    if folded in CHUB_RESTART_WEB_PROMPTS:
        return WeixinChubCommand("restart_web", normalized)
    if folded == CHUB_RESTART_WORKER_PROMPT:
        return WeixinChubCommand("restart_worker", normalized)
    if folded == CHUB_RESTART_CLAWBOT_PROMPT:
        return WeixinChubCommand("restart_clawbot", normalized)
    if folded == CHUB_UPGRADE_PROMPT:
        return WeixinChubCommand("upgrade", normalized)
    if folded == "retry":
        return WeixinChubCommand("retry", normalized)

    request_match = REQUEST_COMMAND_PATTERN.fullmatch(normalized)
    if request_match is not None:
        kind = {
            "cat": "request_cat",
            "archive": "request_archive",
            "del": "request_delete",
        }[request_match.group(1).casefold()]
        return WeixinChubCommand(
            kind,
            normalized,
            requested_index=int(request_match.group(2)),
        )

    title = match_spaced_argument(command, (SESSION_RENAME_PROMPT,))
    if title is not None:
        return WeixinChubCommand("rename", normalized, task_prompt=title)
    if folded == SESSION_RENAME_PROMPT:
        return WeixinChubCommand("normal", prompt)

    session_slot_command = match_session_slot_command(command, SESSION_SLOT_PATTERN)
    if session_slot_command is not None:
        requested_index, task = session_slot_command
        return WeixinChubCommand(
            "session_slot",
            normalized,
            task_prompt=task,
            requested_index=requested_index,
        )
    operation_match = SESSION_OPERATION_PATTERN.fullmatch(normalized)
    if operation_match is not None:
        operation = operation_match.group(1).casefold()
        slot = operation_match.group(2)
        if operation == SESSION_STOP_PROMPT:
            return WeixinChubCommand(
                "stop",
                normalized,
                requested_index=int(slot) if slot else None,
            )
        if slot:
            return WeixinChubCommand(
                "archive" if operation == SESSION_ARCHIVE_PROMPT else "delete",
                normalized,
                requested_index=int(slot),
            )

    title = match_spaced_argument(command, (SESSION_NEW_PROMPT,))
    if title is not None:
        if normalize_fixed_prompt(title).casefold() == "retry":
            return WeixinChubCommand("normal", prompt)
        return WeixinChubCommand("new", normalized, task_prompt=title)
    if folded == SESSION_NEW_PROMPT:
        return WeixinChubCommand("new", normalized)

    return WeixinChubCommand("normal", prompt)


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
