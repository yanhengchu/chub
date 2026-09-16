"""Fixed command surface for capabilities granted to a running AI task."""

from __future__ import annotations

import argparse
import asyncio
import hmac
import json
import os
import stat
import sys
from pathlib import Path

from pydantic import ValidationError

from app.automations.browser import (
    DebugChromePageReadError,
    interact_debug_chrome_page,
    read_debug_chrome_page,
)
from app.task_capabilities import TaskCapabilityContext

_CONTEXT_ENV = "CHUB_TASK_CAPABILITY_CONTEXT"
_TOKEN_ENV = "CHUB_TASK_CAPABILITY_TOKEN"
_MAX_CONTEXT_BYTES = 4 * 1024


class TaskCapabilityError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(prog="chub capability")
    commands = root.add_subparsers(dest="command", required=True)
    page_read = commands.add_parser(
        "page-read",
        help="Read one granted network-reachable page",
    )
    page_read.add_argument("--url", required=True)
    page_interact = commands.add_parser(
        "page-interact",
        help="Follow one granted network-reachable page link",
    )
    page_interact.add_argument("--url", required=True)
    page_interact.add_argument("--follow-link", required=True)
    return root


def _load_context() -> TaskCapabilityContext:
    raw_path = os.environ.get(_CONTEXT_ENV)
    token = os.environ.get(_TOKEN_ENV)
    if not raw_path or not token:
        raise TaskCapabilityError(
            "task_capability_context_missing",
            "This command is only available inside an authorized Chub task.",
        )
    path = Path(raw_path)
    try:
        metadata = path.lstat()
    except OSError as exc:
        raise TaskCapabilityError(
            "task_capability_context_unavailable",
            "The task capability context is unavailable.",
        ) from exc
    if (
        not stat.S_ISREG(metadata.st_mode)
        or metadata.st_uid != os.getuid()
        or stat.S_IMODE(metadata.st_mode) & 0o077
        or metadata.st_size > _MAX_CONTEXT_BYTES
    ):
        raise TaskCapabilityError(
            "task_capability_context_unsafe",
            "The task capability context is unsafe.",
        )
    try:
        payload = path.read_bytes()
        context = TaskCapabilityContext.model_validate_json(payload)
    except (OSError, UnicodeError, ValidationError) as exc:
        raise TaskCapabilityError(
            "task_capability_context_invalid",
            "The task capability context is invalid.",
        ) from exc
    if path.name != "capability-context.json":
        raise TaskCapabilityError(
            "task_capability_context_invalid",
            "The task capability context is invalid.",
        )
    if not hmac.compare_digest(context.token, token):
        raise TaskCapabilityError(
            "task_capability_token_rejected",
            "The task capability token was rejected.",
        )
    return context


def _require(context: TaskCapabilityContext, capability_id: str) -> None:
    if capability_id not in context.capability_ids:
        raise TaskCapabilityError(
            "task_capability_denied",
            f"This task was not granted {capability_id}.",
        )


async def run(arguments: argparse.Namespace) -> dict[str, object]:
    context = _load_context()
    if arguments.command == "page-read":
        _require(context, "chub.debug_chrome.page.read")
        try:
            snapshot = await read_debug_chrome_page(arguments.url)
        except DebugChromePageReadError as exc:
            raise TaskCapabilityError("debug_chrome_page_read_failed", str(exc)) from exc
        return {
            "source_url": snapshot.source_url,
            "final_url": snapshot.final_url,
            "title": snapshot.title,
            "content": snapshot.content,
            "truncated": snapshot.truncated,
        }
    if arguments.command == "page-interact":
        _require(context, "chub.debug_chrome.page.interact")
        try:
            snapshot = await interact_debug_chrome_page(
                arguments.url,
                follow_link_text=arguments.follow_link,
            )
        except DebugChromePageReadError as exc:
            raise TaskCapabilityError("debug_chrome_page_interact_failed", str(exc)) from exc
        return {
            "source_url": snapshot.source_url,
            "final_url": snapshot.final_url,
            "title": snapshot.title,
            "content": snapshot.content,
            "truncated": snapshot.truncated,
        }
    raise TaskCapabilityError("task_capability_unknown", "Unknown task capability command.")


def main() -> int:
    arguments = parser().parse_args()
    try:
        result = asyncio.run(run(arguments))
    except TaskCapabilityError as exc:
        print(
            json.dumps(
                {"success": False, "error": {"code": exc.code, "message": exc.message}},
                ensure_ascii=False,
            )
        )
        return 1
    print(json.dumps({"success": True, "data": result}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
