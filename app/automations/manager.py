from __future__ import annotations

import asyncio
import logging
import os
import subprocess
import sys
import threading
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import urlparse
from uuid import uuid4

import psutil

from app.automations.browser import (
    debug_chrome_status,
    session_factory,
    start_debug_chrome,
    stop_debug_chrome,
)
from app.automations.config import DuplicateAutomationTaskError, load_automations
from app.automations.models import (
    AutomationListData,
    AutomationRunAccepted,
    AutomationState,
    AutomationTaskPublic,
    BrowserControlResult,
    FeishuEnvironmentState,
)
from app.automations.operations import log_final_operation
from app.automations.lock import LockBusy, file_lock
from app.automations.store import AutomationStateStore
from app.core.config import PROJECT_ROOT, Settings
from app.core.response import ApiError


FEISHU_ENVIRONMENT_URL = "https://qw6xxurweq.feishu.cn/drive/home/"
FEISHU_TENANT_HOST = "qw6xxurweq.feishu.cn"
FEISHU_LOGIN_HOST = "accounts.feishu.cn"
FEISHU_CHECK_TIMEOUT_MS = 30_000
FEISHU_LOGIN_PAGE_NAME = "chub-feishu-environment"
FEISHU_QR_CANVAS = ".new-scan-qrcode-container .newLogin_scan-QR-code canvas"
FEISHU_ACCOUNT_LOGIN = '.enter-credential, [data-test="login-phone-input"]'
FEISHU_MODE_SWITCH = ".switch-login-mode-container"
FEISHU_QR_REFRESH = ".qr-scan-mask-cover"
FEISHU_QR_OVERLAY = ".newLogin_scan-shadow"
LOGGER = logging.getLogger("hub.automations")


def _feishu_environment_for_url(
    url: str,
    *,
    checked_at: datetime,
) -> FeishuEnvironmentState:
    host = (urlparse(url).hostname or "").lower().rstrip(".")
    if host == FEISHU_TENANT_HOST:
        return FeishuEnvironmentState(
            state="available",
            message="登录有效",
            checked_at=checked_at,
        )
    if host == FEISHU_LOGIN_HOST:
        return FeishuEnvironmentState(
            state="login_required",
            message="需要登录",
            checked_at=checked_at,
        )
    return FeishuEnvironmentState(
        state="failed",
        message="检查失败",
        checked_at=checked_at,
    )


class AutomationManager:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._store = AutomationStateStore(settings.automations.data_dir)
        self._launch_lock = threading.Lock()
        self._state_lock = threading.Lock()
        self._qr_lock = threading.Lock()
        self._feishu_environment = FeishuEnvironmentState()
        self._feishu_checking = False
        self._feishu_qr_path = (
            settings.automations.data_dir / "runtime" / "feishu-login-qr.png"
        )
        self._clear_feishu_qr()

    def _clear_feishu_qr(self) -> None:
        with self._qr_lock:
            self._feishu_qr_path.unlink(missing_ok=True)

    def _save_feishu_qr(self, content: bytes) -> None:
        with self._qr_lock:
            directory = self._feishu_qr_path.parent
            directory.mkdir(parents=True, exist_ok=True)
            directory.chmod(0o700)
            temporary = self._feishu_qr_path.with_suffix(".tmp")
            try:
                temporary.write_bytes(content)
                temporary.chmod(0o600)
                os.replace(temporary, self._feishu_qr_path)
                self._feishu_qr_path.chmod(0o600)
            finally:
                temporary.unlink(missing_ok=True)

    def feishu_qr_content(self) -> bytes:
        with self._state_lock:
            available = self._feishu_environment.qr_available
        if not available:
            raise ApiError(404, "feishu_qr_not_found", "飞书登录二维码不可用")
        with self._qr_lock:
            try:
                return self._feishu_qr_path.read_bytes()
            except FileNotFoundError as exc:
                raise ApiError(
                    404,
                    "feishu_qr_not_found",
                    "飞书登录二维码不可用",
                ) from exc

    def _public_feishu_environment(self, browser_state: str) -> FeishuEnvironmentState:
        with self._state_lock:
            if browser_state != "running":
                self._feishu_environment = FeishuEnvironmentState()
                self._clear_feishu_qr()
                return FeishuEnvironmentState(
                    state="browser_stopped",
                    message="浏览器未启动",
                )
            return self._feishu_environment.model_copy()

    def _set_feishu_environment(self, state: FeishuEnvironmentState) -> None:
        with self._state_lock:
            self._feishu_environment = state

    async def _check_feishu_page(self) -> FeishuEnvironmentState:
        session = session_factory()
        async with session(ensure_page=True) as chrome:
            page = None
            for existing_page in chrome.context.pages:
                if existing_page.is_closed():
                    continue
                try:
                    if await existing_page.evaluate("window.name") == FEISHU_LOGIN_PAGE_NAME:
                        page = existing_page
                        break
                except Exception:
                    continue
            if page is None:
                page = await chrome.context.new_page()
                await page.add_init_script(
                    f"window.name = {FEISHU_LOGIN_PAGE_NAME!r};"
                )
            keep_open = False
            try:
                await page.goto(
                    FEISHU_ENVIRONMENT_URL,
                    timeout=FEISHU_CHECK_TIMEOUT_MS,
                    wait_until="domcontentloaded",
                )
                result = _feishu_environment_for_url(
                    page.url,
                    checked_at=datetime.now().astimezone(),
                )
                if result.state == "login_required":
                    keep_open = True
                    await page.bring_to_front()
                    try:
                        await page.wait_for_function(
                            """selectors => selectors.some(selector =>
                                Array.from(document.querySelectorAll(selector)).some(element => {
                                    const style = window.getComputedStyle(element);
                                    return style.visibility !== 'hidden'
                                        && style.display !== 'none'
                                        && element.getBoundingClientRect().width > 0
                                        && element.getBoundingClientRect().height > 0;
                                })
                            )""",
                            arg=[FEISHU_QR_CANVAS, FEISHU_ACCOUNT_LOGIN],
                            timeout=10_000,
                        )
                    except Exception:
                        LOGGER.warning(
                            "Feishu login page structure was not recognized",
                            exc_info=True,
                        )
                        self._clear_feishu_qr()
                        return FeishuEnvironmentState(
                            state="failed",
                            message="无法识别飞书登录页面",
                            checked_at=result.checked_at,
                        )
                    canvas = page.locator(FEISHU_QR_CANVAS).first
                    if not await canvas.is_visible():
                        account_login = page.locator(FEISHU_ACCOUNT_LOGIN).first
                        if await account_login.is_visible():
                            await page.locator(FEISHU_MODE_SWITCH).first.click(
                                timeout=10_000
                            )
                            await canvas.wait_for(state="visible", timeout=10_000)
                    if not await canvas.is_visible():
                        self._clear_feishu_qr()
                        return FeishuEnvironmentState(
                            state="failed",
                            message="无法识别飞书登录二维码",
                            checked_at=result.checked_at,
                        )
                    refresh = page.locator(FEISHU_QR_REFRESH).first
                    if await refresh.is_visible():
                        await refresh.click(timeout=10_000)
                        await canvas.wait_for(state="visible", timeout=10_000)
                    await page.locator(FEISHU_QR_OVERLAY).first.wait_for(
                        state="hidden",
                        timeout=10_000,
                    )
                    screenshot = await canvas.screenshot(type="png")
                    if not screenshot:
                        self._clear_feishu_qr()
                        return FeishuEnvironmentState(
                            state="failed",
                            message="飞书登录二维码截图失败",
                            checked_at=result.checked_at,
                        )
                    self._save_feishu_qr(screenshot)
                    return result.model_copy(
                        update={
                            "message": "需要登录",
                            "qr_available": True,
                        }
                    )
                self._clear_feishu_qr()
                return result
            finally:
                if not keep_open and not page.is_closed():
                    await page.close()

    def _load_config(self):
        try:
            return load_automations(*self._settings.automations.config_files)
        except DuplicateAutomationTaskError as exc:
            raise ApiError(
                503,
                "automation_config_conflict",
                f"自动化任务配置冲突：{exc.task_id}",
            ) from exc
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
                feishu_environment=FeishuEnvironmentState(
                    state="browser_stopped",
                    message="浏览器未启动",
                ),
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
            feishu_environment=self._public_feishu_environment(browser_state),
            enabled_count=sum(task.enabled for task in config.tasks.values()),
            tasks=tasks,
        )

    def check_feishu_environment(self) -> FeishuEnvironmentState:
        browser_state, _, _ = debug_chrome_status()
        if browser_state != "running":
            raise ApiError(409, "debug_chrome_not_running", "Debug Chrome 未运行")

        with self._launch_lock:
            config = self._load_config()
            if self._feishu_checking or any(
                self._current_state(task_id).status in {"queued", "running"}
                for task_id in config.tasks
            ):
                raise ApiError(
                    409,
                    "automation_browser_busy",
                    "自动化任务正在使用 Debug Chrome",
                )
            self._feishu_checking = True
            self._set_feishu_environment(
                FeishuEnvironmentState(state="checking", message="检查中")
            )

        lock_path = self._settings.automations.data_dir / "locks" / "debug-chrome.lock"
        try:
            with file_lock(lock_path, 0):
                try:
                    result = asyncio.run(self._check_feishu_page())
                except Exception:
                    LOGGER.exception("Feishu environment check failed")
                    self._clear_feishu_qr()
                    result = FeishuEnvironmentState(
                        state="failed",
                        message="检查失败",
                        checked_at=datetime.now().astimezone(),
                    )
        except LockBusy as exc:
            result = FeishuEnvironmentState(state="unchecked", message="未检查")
            self._set_feishu_environment(result)
            raise ApiError(
                409,
                "automation_browser_busy",
                "自动化任务正在使用 Debug Chrome",
            ) from exc
        finally:
            with self._launch_lock:
                self._feishu_checking = False

        self._set_feishu_environment(result)
        return result

    def start(
        self,
        task_id: str,
        *,
        operation_id: str,
        source_ip: str,
    ) -> AutomationRunAccepted:
        with self._launch_lock:
            if self._feishu_checking:
                raise ApiError(
                    409,
                    "automation_browser_busy",
                    "飞书环境正在检查",
                )
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

    def control_browser(
        self,
        action: str,
        mode: str = "headed",
    ) -> BrowserControlResult:
        if action not in {"start", "stop"}:
            raise ApiError(404, "browser_action_not_found", "浏览器操作不存在")
        if action == "start" and mode not in {"headed", "headless"}:
            raise ApiError(422, "browser_mode_invalid", "Debug Chrome 启动模式无效")
        with self._launch_lock:
            if self._feishu_checking:
                raise ApiError(
                    409,
                    "automation_browser_busy",
                    "飞书环境正在检查",
                )
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
                        start_debug_chrome(mode)
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
        self._set_feishu_environment(FeishuEnvironmentState())
        self._clear_feishu_qr()
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
