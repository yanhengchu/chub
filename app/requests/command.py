from __future__ import annotations

import argparse
import sys
from uuid import uuid4

from app.core.config import load_settings
from app.services.request_backlog import RequestBacklogError, RequestBacklogStore
from app.services.operation_log import write_operation


def _slot(value: str) -> int:
    normalized = value.strip().upper()
    if len(normalized) == 2 and normalized[0] == "R" and normalized[1] in "123456789":
        return int(normalized[1])
    raise argparse.ArgumentTypeError("request slot must be R1-R9")


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(prog="chub request")
    commands = root.add_subparsers(dest="command", required=True)

    save = commands.add_parser("save", help="Save a new request from stdin")
    save.add_argument("--title", required=True)

    update = commands.add_parser("update", help="Replace one request from stdin")
    update.add_argument("slot", type=_slot)
    update.add_argument("--title")

    show = commands.add_parser("show", help="Show one active request")
    show.add_argument("slot", type=_slot)

    commands.add_parser("list", help="List active requests")
    return root


def _store() -> RequestBacklogStore:
    settings = load_settings()
    return RequestBacklogStore(
        settings.openclaw.weixin_chub_mode.request_state_file
    )


def _write_log(operation_id: str, action: str, status: str, target: str) -> None:
    write_operation(
        operation_id=operation_id,
        action=action,
        status=status,
        target=target,
        source_ip="local-agent",
    )


def main() -> int:
    arguments = parser().parse_args()
    store = _store()
    operation_id = uuid4().hex
    action = f"request_backlog_{arguments.command}"
    target = f"R{arguments.slot}" if hasattr(arguments, "slot") else "request-backlog"
    _write_log(operation_id, action, "requested", target)
    _write_log(operation_id, action, "started", target)
    try:
        if arguments.command == "save":
            content = sys.stdin.read()
            item = store.save(title=arguments.title, content=content)
            _write_log(operation_id, action, "succeeded", f"R{item.slot}")
            print(f"Saved R{item.slot} · {item.title}")
            return 0
        if arguments.command == "update":
            content = sys.stdin.read()
            item = store.update(
                arguments.slot,
                title=arguments.title,
                content=content,
            )
            _write_log(operation_id, action, "succeeded", f"R{item.slot}")
            print(f"Updated R{item.slot} · {item.title}")
            return 0
        if arguments.command == "show":
            item = store.get(arguments.slot)
            _write_log(operation_id, action, "succeeded", f"R{item.slot}")
            print(f"R{item.slot} · {item.title}\n\n{item.content}")
            return 0
        for item in store.list_active():
            print(f"R{item.slot} · {item.title}")
        _write_log(operation_id, action, "succeeded", target)
        return 0
    except (RequestBacklogError, ValueError) as exc:
        _write_log(operation_id, action, "failed", target)
        print(f"chub request: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
