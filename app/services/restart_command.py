from __future__ import annotations

import logging
import os
import signal
import subprocess
import threading
import time
from pathlib import Path
from typing import Callable, Protocol


LOGGER = logging.getLogger("hub.restart_command")
INTERRUPTED_RESTART_GRACE_SECONDS = 5.0


class RestartProcess(Protocol):
    def wait(self) -> int: ...


def launch_restart_process(
    command: Path,
    *,
    environment: dict[str, str] | None = None,
) -> RestartProcess:
    env = None
    if environment is not None:
        env = os.environ.copy()
        env.update(environment)
    return subprocess.Popen(
        [str(command)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
        env=env,
    )


def describe_restart_launch_error(error: BaseException) -> str:
    if isinstance(error, FileNotFoundError):
        return "重启脚本不存在或服务进程无法访问，旧 Chub 实例继续运行。"
    if isinstance(error, PermissionError):
        return "重启脚本没有执行权限，旧 Chub 实例继续运行。"
    if isinstance(error, OSError) and error.errno is not None:
        return f"系统无法启动重启脚本（错误码 {error.errno}），旧 Chub 实例继续运行。"
    return "系统未能创建重启进程，旧 Chub 实例继续运行。"


def describe_restart_process_failure(return_code: int, grace_seconds: float) -> str:
    if return_code < 0:
        signal_number = abs(return_code)
        try:
            signal_name = signal.Signals(signal_number).name
        except ValueError:
            signal_name = f"信号 {signal_number}"
        return (
            f"重启脚本被 {signal_name}（信号 {signal_number}）中断，等待 "
            f"{grace_seconds:g} 秒后旧 Chub 实例仍在运行。"
        )
    return (
        f"重启脚本返回退出码 {return_code}，服务管理器未接受重启请求，"
        "旧 Chub 实例继续运行。"
    )


def _survived_interrupted_restart_grace(grace_seconds: float) -> bool:
    time.sleep(grace_seconds)
    return True


def monitor_restart_process(
    process: RestartProcess,
    on_failure: Callable[[str], None],
    *,
    interrupted_grace_seconds: float = INTERRUPTED_RESTART_GRACE_SECONDS,
    interrupted_survival_check: Callable[[float], bool] = (
        _survived_interrupted_restart_grace
    ),
) -> None:
    def wait_for_exit() -> None:
        try:
            return_code = process.wait()
        except Exception:
            LOGGER.warning("Unable to observe Chub restart command", exc_info=True)
            return
        else:
            if return_code == 0:
                return
            if return_code < 0:
                LOGGER.info(
                    "Chub restart command was interrupted while the old instance was stopping: "
                    "return_code=%s",
                    return_code,
                )
                if not interrupted_survival_check(interrupted_grace_seconds):
                    return
            LOGGER.warning(
                "Chub restart command exited unsuccessfully: return_code=%s",
                return_code,
            )
        failure_reason = describe_restart_process_failure(
            return_code,
            interrupted_grace_seconds,
        )
        try:
            on_failure(failure_reason)
        except Exception:
            LOGGER.warning("Unable to record Chub restart command failure", exc_info=True)

    try:
        threading.Thread(
            target=wait_for_exit,
            daemon=True,
            name="chub-restart-command-monitor",
        ).start()
    except RuntimeError:
        LOGGER.warning("Unable to start Chub restart command monitor", exc_info=True)
