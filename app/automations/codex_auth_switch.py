from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import pty
import re
import select
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import tomllib
from contextvars import ContextVar
from dataclasses import dataclass
from pathlib import Path
from threading import Event
from typing import Literal
from urllib.parse import urlsplit

from app.automations.browser import (
    _chrome_debug_module,
    _profile_modules,
    session_factory,
)
from app.automations.lock import LockBusy, file_lock
from app.core.config import Settings, load_settings


Mode = Literal["account", "api"]
TARGET_PROFILE = "Default"
DEVICE_URL = "https://auth.openai.com/codex/device"
MAX_CONFIG_BYTES = 1024 * 1024
AUTH_CALLBACK_TIMEOUT_SECONDS = 30
AUTH_PAGE_TIMEOUT_SECONDS = 45
DEVICE_CODE_CONFIRM_TIMEOUT_SECONDS = 10
DEVICE_AUTH_RESULT_TIMEOUT_SECONDS = 10
DEVICE_AUTH_SUCCESS_DWELL_MS = 1_000
DEVICE_CODE_LENGTH = 9
DEVICE_CODE_FILL_ATTEMPTS = 3
DEVICE_CODE_VERIFY_POLLS = 4
MAX_CLI_DIAGNOSTIC_BYTES = 8192
ANSI = re.compile(chr(27) + r"\[[0-?]*[ -/]*[@-~]")
DEVICE_CODE = re.compile(r"one-time code.*?([A-Z0-9]{4}-[A-Z0-9]{5})", re.S)
DEVICE_CODE_FORMAT = re.compile(r"^[A-Z0-9]{4}-[A-Z0-9]{5}$")
_AUTH_OPERATION_ID: ContextVar[str | None] = ContextVar(
    "codex_auth_operation_id",
    default=None,
)


class _AuthLoggerAdapter(logging.LoggerAdapter):
    def process(self, message, kwargs):
        operation_id = _AUTH_OPERATION_ID.get()
        if operation_id:
            message = f"{message} operation_id={operation_id}"
        return message, kwargs


AUTH_LOGGER = _AuthLoggerAdapter(
    logging.getLogger("hub.automations.codex_auth_switch"),
    {},
)
ACCOUNT_CONTROL_SELECTOR = (
    'button:visible, [role="button"]:visible, [role="option"]:visible'
)


class CodexAuthSwitchError(RuntimeError):
    pass


class CodexAuthSwitchCancelled(CodexAuthSwitchError):
    pass


@dataclass(frozen=True)
class SwitchResult:
    mode: Mode
    message: str


def _codex_home() -> Path:
    configured = os.environ.get("CODEX_HOME")
    if configured:
        candidate = Path(configured).expanduser()
        if candidate.is_absolute():
            return candidate
    return Path.home() / ".codex"


def _config_paths(home: Path) -> tuple[Path, Path, Path]:
    return home / "config.toml", home / "config_account.toml", home / "config_api.toml"


def _read_config(path: Path) -> bytes:
    try:
        metadata = path.stat()
        if not path.is_file() or metadata.st_size > MAX_CONFIG_BYTES:
            raise CodexAuthSwitchError("Codex 配置模板不可用")
        data = path.read_bytes()
        tomllib.loads(data.decode("utf-8"))
        return data
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
        raise CodexAuthSwitchError("Codex 配置模板不可用") from exc


def _replace_config(path: Path, data: bytes) -> None:
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as temporary:
            temporary.write(data)
            temporary.flush()
            os.fsync(temporary.fileno())
            temporary_path = Path(temporary.name)
        temporary_path.chmod(0o600)
        os.replace(temporary_path, path)
        path.chmod(0o600)
    except OSError as exc:
        try:
            temporary_path.unlink(missing_ok=True)  # type: ignore[has-type]
        except UnboundLocalError:
            pass
        raise CodexAuthSwitchError("Codex 配置同步失败") from exc


def _sync_configuration(mode: Mode, home: Path) -> None:
    AUTH_LOGGER.info("codex_auth_configuration phase=sync_started mode=%s", mode)
    current, account, api = _config_paths(home)
    current_data = _read_config(current)
    target = account if mode == "api" else api
    replacement = api if mode == "api" else account
    replacement_data = _read_config(replacement)
    _replace_config(target, current_data)
    _replace_config(current, replacement_data)
    AUTH_LOGGER.info("codex_auth_configuration phase=sync_completed mode=%s", mode)


def _run_status(codex: str) -> str:
    try:
        result = subprocess.run(
            [codex, "login", "status"],
            check=False,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            timeout=15,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise CodexAuthSwitchError("Codex 认证状态无法读取") from exc
    return result.stdout.strip()[:4096]


def _assert_status(codex: str, mode: Mode) -> None:
    status = _run_status(codex)
    if mode == "account" and "Logged in using ChatGPT" in status:
        AUTH_LOGGER.info("codex_auth_cli phase=login_status_confirmed mode=account")
        return
    if mode == "api" and "Not logged in" in status:
        AUTH_LOGGER.info("codex_auth_cli phase=login_status_confirmed mode=api")
        return
    AUTH_LOGGER.info("codex_auth_cli phase=login_status_unconfirmed mode=%s", mode)
    raise CodexAuthSwitchError("Codex 认证最终状态无法确认")


def _raise_if_cancelled(cancel_event: Event | None) -> None:
    if cancel_event is not None and cancel_event.is_set():
        raise CodexAuthSwitchCancelled("Codex 认证切换已停止")


def _device_code_characters(code: str) -> str:
    if not DEVICE_CODE_FORMAT.fullmatch(code):
        raise CodexAuthSwitchError("Codex 设备授权码格式无效")
    characters = code.replace("-", "")
    if len(characters) != DEVICE_CODE_LENGTH:
        raise CodexAuthSwitchError("Codex 设备授权码长度无效")
    return characters


def _start_device_auth(
    codex: str,
    cancel_event: Event | None = None,
) -> tuple[int, int, str]:
    pid, descriptor = pty.fork()
    if pid == 0:
        os.execvp(codex, [codex, "login", "--device-auth"])
    output = bytearray()
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        if cancel_event is not None and cancel_event.is_set():
            _cancel_process(pid)
            try:
                os.close(descriptor)
            except OSError:
                pass
            raise CodexAuthSwitchCancelled("Codex 认证切换已停止")
        readable, _, _ = select.select([descriptor], [], [], 0.5)
        if not readable:
            continue
        try:
            chunk = os.read(descriptor, 4096)
        except OSError:
            break
        if not chunk:
            break
        output.extend(chunk)
        parsed = ANSI.sub("", output.decode("utf-8", "replace"))
        matched = DEVICE_CODE.search(parsed)
        if matched:
            code = matched.group(1)
            _device_code_characters(code)
            AUTH_LOGGER.info(
                "codex_auth_cli phase=device_code_parsed character_count=%d",
                DEVICE_CODE_LENGTH,
            )
            return pid, descriptor, code
    _cancel_process(pid)
    try:
        os.close(descriptor)
    except OSError:
        pass
    raise CodexAuthSwitchError("无法读取 Codex 设备授权码")


def _cancel_process(pid: int) -> None:
    try:
        os.kill(pid, signal.SIGINT)
    except ProcessLookupError:
        return
    try:
        os.waitpid(pid, 0)
    except ChildProcessError:
        pass


def _cli_terminal_phase(output: bytes) -> str:
    """Classify CLI progress without retaining terminal text or authentication data."""
    text = ANSI.sub("", output.decode("utf-8", "replace")).lower()
    if any(marker in text for marker in ("waiting for authorization", "waiting for login")):
        return "waiting_for_callback"
    if any(marker in text for marker in ("denied", "declined", "rejected", "not authorized")):
        return "authorization_rejected"
    if any(marker in text for marker in ("expired", "expiration")):
        return "authorization_expired"
    if any(marker in text for marker in ("network", "connection", "dns", "offline")):
        return "network_error"
    if any(marker in text for marker in ("timed out", "timeout")):
        return "cli_timeout"
    if any(marker in text for marker in ("error", "failed", "failure")):
        return "cli_reported_failure"
    if output:
        return "cli_output_received"
    return "waiting_for_callback"


def _wait_for_success(
    pid: int,
    descriptor: int,
    cancel_event: Event | None = None,
) -> None:
    deadline = time.monotonic() + AUTH_CALLBACK_TIMEOUT_SECONDS
    started_at = time.monotonic()
    output = bytearray()
    phase = "waiting_for_callback"
    terminal_read_unavailable_logged = False
    AUTH_LOGGER.info(
        "codex_auth_cli phase=waiting_for_callback timeout_seconds=%d",
        AUTH_CALLBACK_TIMEOUT_SECONDS,
    )
    try:
        while time.monotonic() < deadline:
            try:
                _raise_if_cancelled(cancel_event)
            except CodexAuthSwitchCancelled:
                AUTH_LOGGER.info(
                    "codex_auth_cli phase=cancelled elapsed_seconds=%d",
                    round(time.monotonic() - started_at),
                )
                raise
            readable, _, _ = select.select([descriptor], [], [], 0.2)
            if readable:
                try:
                    chunk = os.read(descriptor, 4096)
                except OSError:
                    if not terminal_read_unavailable_logged:
                        AUTH_LOGGER.info("codex_auth_cli phase=terminal_read_unavailable")
                        terminal_read_unavailable_logged = True
                else:
                    output.extend(chunk)
                    if len(output) > MAX_CLI_DIAGNOSTIC_BYTES:
                        del output[:-MAX_CLI_DIAGNOSTIC_BYTES]
                    next_phase = _cli_terminal_phase(bytes(output))
                    if next_phase != phase:
                        phase = next_phase
                        AUTH_LOGGER.info("codex_auth_cli phase=%s", phase)
            done, status = os.waitpid(pid, os.WNOHANG)
            if done:
                exit_code = os.waitstatus_to_exitcode(status)
                AUTH_LOGGER.info(
                    "codex_auth_cli phase=exited exit_code=%d elapsed_seconds=%d last_phase=%s",
                    exit_code,
                    round(time.monotonic() - started_at),
                    phase,
                )
                if exit_code == 0:
                    return
                raise CodexAuthSwitchError(
                    f"Codex 账户登录失败（exit_code={exit_code};phase={phase}）"
                )
        AUTH_LOGGER.info(
            "codex_auth_cli phase=timeout elapsed_seconds=%d last_phase=%s",
            round(time.monotonic() - started_at),
            phase,
        )
        raise CodexAuthSwitchError(f"Codex 设备授权超时（phase={phase}）")
    finally:
        try:
            os.close(descriptor)
        except OSError:
            pass


def _device_auth_page_state(
    url: str,
    input_count: int,
    code_length: int,
    control_count: int = 0,
) -> Literal["account", "consent", "code", "unknown"]:
    parsed = urlsplit(url)
    host = (parsed.hostname or "").lower().rstrip(".")
    if host != "auth.openai.com":
        raise CodexAuthSwitchError("Codex 授权页已跳转到需要人工处理的页面")
    if input_count == code_length:
        return "code"
    if input_count > 0:
        raise CodexAuthSwitchError("Codex 授权码页面格式已变化")
    path = parsed.path.rstrip("/")
    if path.endswith("/consent"):
        return "consent"
    if path == "/codex/device" or control_count > 0:
        return "account"
    return "unknown"


def _safe_auth_path(url: str) -> str:
    parsed = urlsplit(url)
    return parsed.path or "/"


async def _click_first_visible(page, selector: str, failure: str) -> None:
    controls = page.locator(selector)
    if await controls.count() == 0:
        raise CodexAuthSwitchError(failure)
    await controls.first.click(timeout=5_000, no_wait_after=True)


def _device_code_values_match(values: list[str], characters: str) -> bool:
    return len(values) == len(characters) and all(
        value == character for value, character in zip(values, characters, strict=True)
    )


async def _wait_for_device_code_values(
    page,
    inputs,
    characters: str,
    cancel_event: Event | None = None,
) -> bool:
    for _ in range(DEVICE_CODE_VERIFY_POLLS):
        _raise_if_cancelled(cancel_event)
        values = [await inputs.nth(index).input_value() for index in range(len(characters))]
        if _device_code_values_match(values, characters):
            AUTH_LOGGER.info("codex_auth_code inputs=confirmed count=%d", len(values))
            return True
        await inputs.nth(len(characters) - 1).blur()
        await inputs.nth(len(characters) - 1).press("Tab")
        await page.wait_for_timeout(250)
    return False


async def _fill_device_code_with_retries(
    page,
    inputs,
    characters: str,
    cancel_event: Event | None = None,
) -> None:
    for attempt in range(1, DEVICE_CODE_FILL_ATTEMPTS + 1):
        AUTH_LOGGER.info(
            "codex_auth_code action=fill_device_code attempt=%d input_count=%d",
            attempt,
            len(characters),
        )
        for index, character in enumerate(characters):
            await inputs.nth(index).fill(character)
        if await _wait_for_device_code_values(
            page,
            inputs,
            characters,
            cancel_event,
        ):
            return
        AUTH_LOGGER.info(
            "codex_auth_code verification=failed attempt=%d",
            attempt,
        )
    raise CodexAuthSwitchError(
        f"Codex 设备码连续 {DEVICE_CODE_FILL_ATTEMPTS} 次填充校验失败"
    )


async def _device_code_confirmation_control(page):
    grant = page.locator('button[value="grant"]:visible')
    if await grant.count() == 1:
        return grant.first
    submit = page.locator("button[type=submit]:visible")
    if await submit.count() == 1:
        return submit.first
    return None


async def _wait_for_device_code_confirmation(
    page,
    deadline: float,
    cancel_event: Event | None = None,
):
    last_observation: tuple[int, bool] | None = None
    while time.monotonic() < deadline:
        _raise_if_cancelled(cancel_event)
        control = await _device_code_confirmation_control(page)
        count = 0 if control is None else 1
        enabled = control is not None and await control.is_enabled()
        observation = (count, enabled)
        if observation != last_observation:
            AUTH_LOGGER.info(
                "codex_auth_code confirmation_controls=%d enabled=%s",
                count,
                str(enabled).lower(),
            )
            last_observation = observation
        if control is not None and enabled:
            return control
        await page.wait_for_timeout(250)
    raise CodexAuthSwitchError("Codex 设备码确认按钮未就绪")


async def _wait_for_device_auth_result(
    page,
    code_input_count: int,
    cancel_event: Event | None = None,
) -> None:
    deadline = time.monotonic() + DEVICE_AUTH_RESULT_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        _raise_if_cancelled(cancel_event)
        input_count = await page.locator("input:visible").count()
        if input_count != code_input_count:
            AUTH_LOGGER.info(
                "codex_auth_page stage=post_submit_result path=%s inputs=%d",
                _safe_auth_path(page.url),
                input_count,
            )
            await page.wait_for_timeout(DEVICE_AUTH_SUCCESS_DWELL_MS)
            AUTH_LOGGER.info("codex_auth_page stage=post_submit_result_observed")
            return
        await page.wait_for_timeout(250)
    raise CodexAuthSwitchError("Codex 设备码已提交，但未进入授权结果页")


async def _complete_device_auth(
    code: str,
    cancel_event: Event | None = None,
) -> None:
    async with session_factory()() as chrome:
        page = await chrome.context.new_page()
        try:
            await page.goto(DEVICE_URL, wait_until="domcontentloaded", timeout=30_000)
            characters = _device_code_characters(code)
            deadline = time.monotonic() + AUTH_PAGE_TIMEOUT_SECONDS
            selected_account_at: float | None = None
            submitted_consent_at: float | None = None
            last_observation: tuple[str, str, int, int] | None = None
            while time.monotonic() < deadline:
                _raise_if_cancelled(cancel_event)
                inputs = page.locator("input:visible")
                controls = page.locator(ACCOUNT_CONTROL_SELECTOR)
                input_count = await inputs.count()
                control_count = await controls.count()
                state = _device_auth_page_state(
                    page.url,
                    input_count,
                    len(characters),
                    control_count,
                )
                observation = (state, _safe_auth_path(page.url), control_count, input_count)
                if observation != last_observation:
                    AUTH_LOGGER.info(
                        "codex_auth_page stage=%s path=%s controls=%d inputs=%d",
                        state,
                        observation[1],
                        control_count,
                        input_count,
                    )
                    last_observation = observation
                if state == "account":
                    if selected_account_at is None:
                        AUTH_LOGGER.info("codex_auth_action action=select_default_account")
                        await _click_first_visible(
                            page,
                            ACCOUNT_CONTROL_SELECTOR,
                            "未找到 Codex 账户选择项",
                        )
                        selected_account_at = time.monotonic()
                    elif time.monotonic() - selected_account_at >= 15:
                        raise CodexAuthSwitchError("Codex 账户选择未进入授权确认页")
                elif state == "consent":
                    if submitted_consent_at is None:
                        AUTH_LOGGER.info("codex_auth_action action=confirm_consent")
                        grant = page.locator('button[value="grant"]:visible')
                        if await grant.count() > 0:
                            await grant.first.click(timeout=5_000, no_wait_after=True)
                        else:
                            await _click_first_visible(
                                page,
                                "button[type=submit]:visible",
                                "未找到 Codex 授权确认操作",
                            )
                        submitted_consent_at = time.monotonic()
                    elif time.monotonic() - submitted_consent_at >= 15:
                        raise CodexAuthSwitchError("Codex 授权确认未进入设备码页面")
                elif state == "code":
                    await _fill_device_code_with_retries(
                        page,
                        inputs,
                        characters,
                        cancel_event,
                    )
                    confirm_deadline = min(
                        deadline,
                        time.monotonic() + DEVICE_CODE_CONFIRM_TIMEOUT_SECONDS,
                    )
                    confirmation = await _wait_for_device_code_confirmation(
                        page,
                        confirm_deadline,
                        cancel_event,
                    )
                    AUTH_LOGGER.info("codex_auth_action action=confirm_device_code")
                    await confirmation.click(timeout=5_000, no_wait_after=True)
                    await _wait_for_device_auth_result(
                        page,
                        len(characters),
                        cancel_event,
                    )
                    return
                await page.wait_for_timeout(250)
            raise CodexAuthSwitchError("未识别 Codex 授权页面状态")
        except CodexAuthSwitchError:
            raise
        except Exception as exc:
            raise CodexAuthSwitchError("Codex 授权页面操作失败") from exc
        finally:
            if not page.is_closed():
                await page.close()


@dataclass(frozen=True)
class BrowserSnapshot:
    profile: str | None
    mode: str | None


def _capture_browser_snapshot() -> BrowserSnapshot:
    chrome_debug = _chrome_debug_module()
    _, _, profile_store = _profile_modules()
    current = chrome_debug.status()
    if current.state not in {"running", "stopped"}:
        raise CodexAuthSwitchError("Debug Chrome 当前不可用")
    return BrowserSnapshot(
        profile=(
            current.profile_directory
            if current.state == "running"
            else profile_store.active_profile(chrome_debug.DEFAULT_USER_DATA_DIR)
        ),
        mode=current.mode if current.state == "running" else None,
    )


def _switch_browser_for_account(snapshot: BrowserSnapshot) -> None:
    chrome_debug = _chrome_debug_module()
    _, _, profile_store = _profile_modules()
    current = chrome_debug.status()
    if current.state == "running":
        chrome_debug.stop()
    profile_store.select_profile(chrome_debug.DEFAULT_USER_DATA_DIR, TARGET_PROFILE)
    started = chrome_debug.start(headless=False)
    if started.state != "running" or started.profile_directory != TARGET_PROFILE:
        raise CodexAuthSwitchError("无法启动目标 Debug Chrome 账户")


def _restore_browser(snapshot: BrowserSnapshot) -> None:
    chrome_debug = _chrome_debug_module()
    _, _, profile_store = _profile_modules()
    current = chrome_debug.status()
    if current.state == "running":
        chrome_debug.stop()
    if snapshot.profile is None:
        return
    profile_store.select_profile(chrome_debug.DEFAULT_USER_DATA_DIR, snapshot.profile)
    if snapshot.mode is None:
        return
    restored = chrome_debug.start(headless=snapshot.mode != "headed")
    if restored.state != "running" or restored.profile_directory != snapshot.profile:
        raise CodexAuthSwitchError("Codex 已切换，但 Debug Chrome 未能恢复到原状态")


def _logout(codex: str) -> None:
    AUTH_LOGGER.info("codex_auth_cli phase=logout_started")
    try:
        result = subprocess.run(
            [codex, "logout"],
            check=False,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise CodexAuthSwitchError("Codex 账户退出失败") from exc
    if result.returncode != 0:
        raise CodexAuthSwitchError("Codex 账户退出失败")
    AUTH_LOGGER.info("codex_auth_cli phase=logout_completed")


def switch(
    mode: Mode,
    *,
    codex_home: Path | None = None,
    settings: Settings | None = None,
    cancel_event: Event | None = None,
    operation_id: str | None = None,
) -> SwitchResult:
    operation_token = _AUTH_OPERATION_ID.set(operation_id)
    try:
        AUTH_LOGGER.info("codex_auth_switch phase=requested mode=%s", mode)
        codex = shutil.which("codex")
        if not codex:
            raise CodexAuthSwitchError("未找到 Codex CLI")
        home = codex_home or _codex_home()
        resolved_settings = settings or load_settings()
        lock_path = (
            resolved_settings.automations.runtime_dir / "locks" / "codex-auth-switch.lock"
        )
        try:
            with file_lock(lock_path, 0):
                AUTH_LOGGER.info("codex_auth_switch phase=lock_acquired mode=%s", mode)
                _raise_if_cancelled(cancel_event)
                if mode == "api":
                    _logout(codex)
                    _assert_status(codex, "api")
                    _sync_configuration("api", home)
                    AUTH_LOGGER.info("codex_auth_switch phase=completed mode=api")
                    return SwitchResult(mode="api", message="已切换到 API Key 模式")

                pid: int | None = None
                descriptor: int | None = None
                browser_lock = (
                    resolved_settings.automations.runtime_dir
                    / "locks"
                    / "debug-chrome.lock"
                )
                with file_lock(browser_lock, 0):
                    snapshot = _capture_browser_snapshot()
                    try:
                        AUTH_LOGGER.info("codex_auth_browser phase=account_profile_starting")
                        _switch_browser_for_account(snapshot)
                        AUTH_LOGGER.info("codex_auth_browser phase=account_profile_ready")
                        _raise_if_cancelled(cancel_event)
                        pid, descriptor, code = _start_device_auth(codex, cancel_event)
                        AUTH_LOGGER.info("codex_auth_cli phase=device_code_received")
                        asyncio.run(_complete_device_auth(code, cancel_event))
                        AUTH_LOGGER.info("codex_auth_cli phase=device_code_submitted")
                        _wait_for_success(pid, descriptor, cancel_event)
                        descriptor = None
                        pid = None
                        _assert_status(codex, "account")
                        _sync_configuration("account", home)
                        AUTH_LOGGER.info("codex_auth_switch phase=completed mode=account")
                        return SwitchResult(mode="account", message="已切换到账户登录模式")
                    finally:
                        if pid is not None:
                            _cancel_process(pid)
                        if descriptor is not None:
                            try:
                                os.close(descriptor)
                            except OSError:
                                pass
                        AUTH_LOGGER.info("codex_auth_browser phase=restore_started")
                        _restore_browser(snapshot)
                        AUTH_LOGGER.info("codex_auth_browser phase=restore_completed")
        except LockBusy as exc:
            AUTH_LOGGER.info("codex_auth_switch phase=blocked reason=auth_switch_busy")
            raise CodexAuthSwitchError("认证切换脚本正在运行") from exc
        except CodexAuthSwitchCancelled:
            AUTH_LOGGER.info("codex_auth_switch phase=cancelled")
            raise
        except CodexAuthSwitchError:
            AUTH_LOGGER.info("codex_auth_switch phase=failed reason=known_switch_error")
            raise
    finally:
        _AUTH_OPERATION_ID.reset(operation_token)


def main() -> None:
    parser = argparse.ArgumentParser(description="Switch Codex authentication mode")
    parser.add_argument("mode", choices=("account", "api"))
    arguments = parser.parse_args()
    try:
        result = switch(arguments.mode)
    except CodexAuthSwitchError as exc:
        print(json.dumps({"success": False, "message": str(exc)}, ensure_ascii=False))
        raise SystemExit(1)
    print(json.dumps({"success": True, "mode": result.mode, "message": result.message}, ensure_ascii=False))


if __name__ == "__main__":
    main()
