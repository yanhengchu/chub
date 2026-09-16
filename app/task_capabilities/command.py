"""Fixed local command surface for bounded Debug Chrome page operations."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys

from app.automations.browser import (
    DebugChromePageReadError,
    interact_debug_chrome_page,
    read_debug_chrome_page,
)
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
        help="Read one public network-reachable page",
    )
    page_read.add_argument("--url", required=True)
    page_interact = commands.add_parser(
        "page-interact",
        help="Follow one unique public page link",
    )
    page_interact.add_argument("--url", required=True)
    page_interact.add_argument("--follow-link", required=True)
    return root


async def run(arguments: argparse.Namespace) -> dict[str, object]:
    if arguments.command == "page-read":
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
    raise TaskCapabilityError("capability_unknown", "Unknown Chub capability command.")


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
