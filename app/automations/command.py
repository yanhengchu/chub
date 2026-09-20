from __future__ import annotations

import argparse
import json
from datetime import datetime

from app.automations.models import AutomationState
from app.automations.operations import log_final_operation
from app.automations.runner import (
    AutomationFailed,
    revalidate_weekly_inputs,
    run_automation,
)
from app.automations.store import AutomationStateStore
from app.core.config import load_settings, log_local_config_fallback
from app.core.logger import configure_logging


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="chub-automation")
    subparsers = parser.add_subparsers(dest="command", required=True)
    run = subparsers.add_parser("run", help="Run one configured automation")
    run.add_argument("task_id")
    run.add_argument("--trigger", choices=["web", "cli", "schedule"], default="cli")
    run.add_argument("--run-id")
    run.add_argument("--replace-pending", action="store_true")
    revalidate = subparsers.add_parser(
        "revalidate", help="Revalidate pending weekly-report inputs"
    )
    revalidate.add_argument("task_id")
    revalidate.add_argument("--trigger", choices=["web", "cli"], default="cli")
    revalidate.add_argument("--run-id")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    settings = load_settings()
    configure_logging(settings.logs)
    log_local_config_fallback(settings)
    try:
        if args.command == "revalidate":
            result = revalidate_weekly_inputs(
                settings,
                args.task_id,
                trigger=args.trigger,
                run_id=args.run_id,
            )
        else:
            result = run_automation(
                settings,
                args.task_id,
                trigger=args.trigger,
                run_id=args.run_id,
                replace_pending=args.replace_pending,
            )
    except Exception as exc:
        if args.run_id:
            store = AutomationStateStore(settings.automations.state_dir)
            queued = store.read(args.task_id)
            failed = AutomationState(
                task_id=args.task_id,
                status="failed",
                run_id=args.run_id,
                trigger=args.trigger,
                operation_id=(
                    queued.operation_id if queued.run_id == args.run_id else None
                ),
                operation_action=(
                    queued.operation_action if queued.run_id == args.run_id else "run_automation"
                ),
                source_ip=queued.source_ip if queued.run_id == args.run_id else None,
                message=(
                    str(exc)
                    if isinstance(exc, AutomationFailed)
                    else "Runner 启动失败"
                ),
                finished_at=datetime.now().astimezone(),
            )
            store.write(log_final_operation(failed))
        print(json.dumps({"success": False, "error": str(exc)}, ensure_ascii=False))
        return 2
    print(json.dumps({"success": True, "data": result.model_dump(mode="json")}, ensure_ascii=False))
    return 0 if result.status == "success" else 1


if __name__ == "__main__":
    raise SystemExit(main())
