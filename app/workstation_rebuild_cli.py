from __future__ import annotations

import argparse

from app.core.config import load_settings
from app.core.logger import configure_logging
from app.services.default_runtime_bootstrap import initialize_default_runtime
from app.services.workstation_rebuild import WorkstationRebuildCoordinator


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m app.workstation_rebuild_cli")
    parser.add_argument(
        "command",
        choices=("start", "stage", "bootstrap-default-runtime", "succeed", "fail", "status"),
    )
    parser.add_argument("operation_id", nargs="?")
    parser.add_argument("--message", default="")
    args = parser.parse_args()
    if args.command != "status":
        configure_logging(load_settings().logs)
    coordinator = WorkstationRebuildCoordinator()
    if args.command == "status":
        print(coordinator.status_data().model_dump_json())
        return
    if not args.operation_id:
        parser.error("operation_id is required")
    if args.command == "start":
        coordinator.mark_executor_started(args.operation_id)
    elif args.command == "stage":
        stages = {
            "stopping_services",
            "cleaning_state",
            "starting_services",
            "initializing_runtime",
            "verifying",
        }
        if args.message not in stages:
            parser.error("--message must be a rebuild stage")
        messages = {
            "stopping_services": "正在停止 Chub Web、Quick Worker 与相关服务。",
            "cleaning_state": "正在清理 Chub 可重建运行态。",
            "starting_services": "正在按当前代码和配置重新建立工作站服务。",
            "initializing_runtime": "正在导入并启用当前默认 Runtime。",
            "verifying": "正在确认新 Web、Quick Worker 与默认 Runtime 的可用状态。",
        }
        coordinator.update(args.operation_id, stage=args.message, message=messages[args.message])
    elif args.command == "bootstrap-default-runtime":
        initialize_default_runtime(load_settings())
    elif args.command == "succeed":
        coordinator.succeed(args.operation_id)
    else:
        coordinator.fail(args.operation_id, args.message)


if __name__ == "__main__":
    main()
