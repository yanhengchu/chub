"""First external Weixin refinement implementation.

The module deliberately has one input: Chub's bounded refinement callback.
It does not receive a route, Session, Worker, path, command or network handle.
"""

import re
from collections.abc import Callable

from app.services.openclaw_weixin_chub_commands import (
    TEXT_CHECK_PROMPT,
    TEXT_MODE_VALUES,
    TEXT_PROMPT,
    WeixinChubCommand,
    normalize_fixed_prompt,
)


def execute_refinement(*, enqueue_refinement: Callable[[], object]) -> object:
    return enqueue_refinement()


def parse_command(prompt: str) -> WeixinChubCommand | None:
    """Return only this module's bounded ``text`` command contract."""
    normalized = normalize_fixed_prompt(prompt)
    if not normalized:
        return None
    if normalized.casefold() == "text help":
        return WeixinChubCommand("help", normalized, task_prompt="text")
    check = normalized.split(maxsplit=1)
    if check and check[0].casefold() == TEXT_CHECK_PROMPT:
        return WeixinChubCommand(
            "text_check", normalized,
            task_prompt=check[1] if len(check) == 2 else None,
            invalid_usage=len(check) != 2,
        )
    parts = normalized.split()
    if not parts or parts[0].casefold() != TEXT_PROMPT:
        return None
    if len(parts) == 1:
        return WeixinChubCommand("text_control", normalized, text_action="mode")
    action = parts[1].casefold()
    if action in {"ok", "next", "cancel", "list"} and len(parts) == 2:
        return WeixinChubCommand("text_control", normalized, text_action=action)
    if action == "mode" and (len(parts) == 2 or (len(parts) == 3 and parts[2].casefold() in TEXT_MODE_VALUES)):
        return WeixinChubCommand("text_control", normalized, text_action="mode", processing_mode=parts[2].casefold() if len(parts) == 3 else None)
    if action == "model" and len(parts) >= 3:
        model_action = parts[2].casefold()
        if model_action == "list" and len(parts) == 3:
            return WeixinChubCommand("text_control", normalized, text_action="model_list")
        if model_action == "level":
            if len(parts) == 3:
                return WeixinChubCommand("text_control", normalized, text_action="model_levels")
            if len(parts) == 4 and re.fullmatch(r"M[1-9][0-9]*", parts[3], re.I):
                return WeixinChubCommand("text_control", normalized, text_action="model_levels", model_index=int(parts[3][1:]))
        if model_action == "use":
            matched = re.fullmatch(r"(?:(M[1-9][0-9]*)(?:\s+(L[1-9][0-9]*))?|(L[1-9][0-9]*))", " ".join(parts[3:]), re.I)
            if matched is not None:
                model, paired_level, level = matched.groups()
                chosen_level = paired_level or level
                return WeixinChubCommand("text_control", normalized, text_action="model_use", model_index=int(model[1:]) if model else None, level_index=int(chosen_level[1:]) if chosen_level else None)
    return WeixinChubCommand("text_control", normalized, invalid_usage=True)
