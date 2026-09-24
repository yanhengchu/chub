from __future__ import annotations

import argparse
import asyncio
import json
from uuid import uuid4

from app.core.config import load_settings
from app.notifications.errors import NotificationError
from app.notifications.feishu.service import NotificationService
from app.notifications.models import NotificationRequest


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(prog="chub notification")
    commands = root.add_subparsers(dest="command", required=True)
    commands.add_parser("validate")
    commands.add_parser("list")

    users = commands.add_parser("users")
    users_commands = users.add_subparsers(dest="users_command", required=True)
    search = users_commands.add_parser("search")
    search.add_argument("--query", required=True)

    test = commands.add_parser("test")
    test.add_argument("--target", required=True)

    send = commands.add_parser("send")
    send.add_argument("--target", required=True)
    send.add_argument("--message", required=True)
    mention = send.add_mutually_exclusive_group()
    mention.add_argument("--mention-all", action="store_true")
    mention.add_argument("--mention-recipient", action="append", default=[])
    mention.add_argument("--mention-inline-recipient", action="append", default=[])
    return root


async def run(arguments: argparse.Namespace) -> dict[str, object]:
    settings = load_settings()
    service = NotificationService(settings.notifications)
    try:
        if arguments.command in {"validate", "list"}:
            targets = [item.model_dump() for item in service.targets()]
            return {"valid": True, "targets": targets}
        if arguments.command == "users":
            return service.search_users(arguments.query).model_dump()

        mention_mode = "none"
        recipients: list[str] = []
        message = "Chub 飞书通知配置测试。"
        if arguments.command == "send":
            message = arguments.message
            if arguments.mention_all:
                mention_mode = "all"
            elif arguments.mention_recipient:
                mention_mode = "recipients"
                recipients = arguments.mention_recipient
            elif arguments.mention_inline_recipient:
                mention_mode = "inline_recipients"
                recipients = arguments.mention_inline_recipient
        target = service.resolve_target_id(arguments.target)
        result = await service.send(
            NotificationRequest(
                request_id=uuid4().hex,
                target=target,
                message=message,
                mention_mode=mention_mode,
                recipients=recipients,
            )
        )
        return result.model_dump()
    finally:
        await service.close()


def main() -> int:
    arguments = parser().parse_args()
    try:
        result = asyncio.run(run(arguments))
    except NotificationError as exc:
        print(json.dumps(
            {
                "success": False,
                "error": {"code": exc.code, "message": exc.message},
            },
            ensure_ascii=False,
        ))
        return 1
    print(json.dumps({"success": True, "data": result}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
