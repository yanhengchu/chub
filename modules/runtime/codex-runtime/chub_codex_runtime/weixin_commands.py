from __future__ import annotations

from app.services.openclaw_weixin_chub_commands import (
    WeixinChubCommand,
    normalize_fixed_prompt,
)


def parse_weixin_command(prompt: str) -> WeixinChubCommand | None:
    """Return only Codex Runtime's bounded Weixin command contract."""
    normalized = normalize_fixed_prompt(prompt)
    if not normalized:
        return None
    folded = normalized.casefold()
    if folded == "codex auth":
        return WeixinChubCommand("codex_auth", normalized, command_group="codex")
    if folded == "codex auth switch":
        return WeixinChubCommand(
            "codex_auth_switch",
            normalized,
            command_group="codex",
        )
    if folded == "codex" or folded.startswith("codex "):
        return WeixinChubCommand(
            "codex_auth",
            normalized,
            invalid_usage=True,
            command_group="codex",
        )
    return None
