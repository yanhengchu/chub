from __future__ import annotations

import os
import subprocess
import sys
import threading
from datetime import datetime, timedelta
from pathlib import Path
from uuid import uuid4

import psutil

from app.automations.browser import (
    debug_chrome_status,
    start_debug_chrome,
    stop_debug_chrome,
)
from app.automations.config import load_automations
from app.automations.models import (
    AutomationListData,
    AutomationRunAccepted,
    AutomationState,
    AutomationTaskPublic,
    BrowserControlResult,
)
from app.automations.operations import log_final_operation
from app.automations.lock import LockBusy, file_lock
from app.automations.store import AutomationStateStore
from app.core.config import PROJECT_ROOT, Settings
from app.core.response import ApiError


class AutomationManager:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._store = AutomationStateStore(settings.automations.data_dir)
        self._launch_lock = threading.Lock()
        self._state_lock = threading.Lock()

    def _load_config(self):
        try:
            return load_automations(self._settings.automations.config_file)
        except RuntimeError as exc:
            raise ApiError(
                503,
                "automation_config_invalid",
                "自动化任务配置无效",
            ) from exc

    @staticmethod
    def _rotate_log(path: Path) -> None:
        if not path.exists() or path.stat().st_size < 2 * 1024 * 1024:
            return
        rotated = path.with_suffix(path.suffix + ".1")
        rotated.unlink(missing_ok=True)
        os.replace(path, rotated)
        rotated.chmod(0o600)

    def _current_state(self, task_id: str) -> AutomationState:
        with self._state_lock:
            state = self._store.read(task_id)
            now = datetime.now().astimezone()
            stale = False
            if state.status == "queued" and state.started_at:
                stale = now - state.started_at > timedelta(minutes=1)
            elif state.status == "running" and state.process_id:
                stale = not psutil.pid_exists(state.process_id)
            if stale:
                state = state.model_copy(
                    update={
                        "status": "failed",
                        "message": "Runner 已异常退出",
                        "finished_at": now,
                    }
                )
                self._store.write(state)
            if (
                state.status in {"success", "failed"}
                and state.operation_id
                and not state.operation_logged
            ):
                state = log_final_operation(state)
                self._store.write(state)
            return state

    def list(self, *, home_only: bool = True) -> AutomationListData:
        if not self._settings.automations.enabled:
            return AutomationListData(
                enabled=False,
                browser_state="unavailable",
                browser_message="自动化任务未启用",
                browser_mode=None,
                enabled_count=0,
                tasks=[],
            )
        config = self._load_config()
        browser_state, browser_message, browser_mode = debug_chrome_status()
        tasks = [
            AutomationTaskPublic(
                id=task_id,
                name=task.name,
                description=task.description,
                enabled=task.enabled,
                state=self._current_state(task_id),
            )
            for task_id, task in config.tasks.items()
        ]
        tasks.sort(
            key=lambda item: (
                item.state.finished_at or item.state.started_at
            ).timestamp()
            if item.state.finished_at or item.state.started_at
            else float("-inf"),
            reverse=True,
        )
        if home_only:
            tasks = tasks[: self._settings.automations.max_home_tasks]
        return AutomationListData(
            enabled=True,
            browser_state=browser_state,
            browser_message=browser_message,
            browser_mode=browser_mode,
            enabled_count=sum(task.enabled for task in config.tasks.values()),
            tasks=tasks,
        )

    def start(
        self,
        task_id: str,
        *,
        operation_id: str,
        source_ip: str,
    ) -> AutomationRunAccepted:
        with self._launch_lock:
            config = self._load_config()
            task = config.tasks.get(task_id)
            if task is None:
                raise ApiError(404, "automation_not_found", "自动化任务不存在")
            if not task.enabled:
                raise ApiError(409, "automation_disabled", "自动化任务未启用")
            browser_state, _, _ = debug_chrome_status()
            if browser_state != "running":
                raise ApiError(409, "debug_chrome_not_running", "Debug Chrome 未运行")
            current = self._current_state(task_id)
            if current.status in {"queued", "running"}:
                raise ApiError(409, "automation_running", "自动化任务正在执行")

            run_id = uuid4().hex
            self._store.write(
                AutomationState(
                    task_id=task_id,
                    status="queued",
                    run_id=run_id,
                    trigger="web",
                    operation_id=operation_id,
                    source_ip=source_ip,
                    message="任务已受理",
                    started_at=datetime.now().astimezone(),
                )
            )
            log_dir = self._settings.automations.data_dir / "logs"
            log_dir.mkdir(parents=True, exist_ok=True)
            log_path = log_dir / f"{task_id}.log"
            try:
                self._rotate_log(log_path)
                output = log_path.open("ab")
                log_path.chmod(0o600)
                runner_environment = os.environ.copy()
                runner_environment["HUB_TOKEN"] = ""
                subprocess.Popen(
                    [
                        sys.executable,
                        "-m",
                        "app.automations.command",
                        "run",
                        task_id,
                        "--trigger",
                        "web",
                        "--run-id",
                        run_id,
                    ],
                    cwd=PROJECT_ROOT,
                    env=runner_environment,
                    stdin=subprocess.DEVNULL,
                    stdout=output,
                    stderr=subprocess.STDOUT,
                    start_new_session=True,
                )
            except OSError as exc:
                self._store.write(
                    AutomationState(
                        task_id=task_id,
                        status="failed",
                        run_id=run_id,
                        trigger="web",
                        operation_id=operation_id,
                        source_ip=source_ip,
                        message="无法启动自动化 Runner",
                        started_at=datetime.now().astimezone(),
                        finished_at=datetime.now().astimezone(),
                    )
                )
                raise ApiError(500, "automation_start_failed", "无法启动自动化任务") from exc
            finally:
                if "output" in locals():
                    output.close()
            return AutomationRunAccepted(task_id=task_id, run_id=run_id)

    def control_browser(self, action: str) -> BrowserControlResult:
        if action not in {"start", "stop"}:
            raise ApiError(404, "browser_action_not_found", "浏览器操作不存在")
        if action == "stop":
            config = self._load_config()
            if any(
                self._current_state(task_id).status in {"queued", "running"}
                for task_id in config.tasks
            ):
                raise ApiError(
                    409,
                    "automation_browser_busy",
                    "自动化任务正在使用 Debug Chrome",
                )
        lock_path = self._settings.automations.data_dir / "locks" / "debug-chrome.lock"
        try:
            with file_lock(lock_path, 0):
                try:
                    current = (
                        start_debug_chrome()
                        if action == "start"
                        else stop_debug_chrome()
                    )
                except RuntimeError as exc:
                    message = str(exc)
                    if "profile" in message.lower() or "managed" in message.lower():
                        public_message = "Debug Chrome profile 尚未初始化"
                    elif "port 9222" in message.lower():
                        public_message = "调试端口 9222 已被其他程序占用"
                    else:
                        public_message = "Debug Chrome 启停失败"
                    raise ApiError(
                        409,
                        "debug_chrome_control_failed",
                        public_message,
                    ) from exc
        except LockBusy as exc:
            raise ApiError(
                409,
                "automation_browser_busy",
                "自动化任务正在使用 Debug Chrome",
            ) from exc

        expected = "running" if action == "start" else "stopped"
        if current.state != expected:
            raise ApiError(
                500,
                "debug_chrome_state_mismatch",
                "Debug Chrome 最终状态不符合预期",
            )
        mode = None
        if current.mode == "headed":
            mode = "有界面模式"
        elif current.mode == "headless":
            mode = "无界面模式"
        return BrowserControlResult(
            state=expected,
            mode=mode,
            message="Debug Chrome 已启动" if action == "start" else "Debug Chrome 已停止",
        )
