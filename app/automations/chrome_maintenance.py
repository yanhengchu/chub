"""Public maintenance and lifecycle use cases for Chub Debug Chrome."""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from app.automations.chrome_supervisor import (
    ChromeStatusSnapshot,
    ChromeSupervisorError,
    request as request_supervisor,
    socket_path,
)
from app.automations.debug_chrome import (
    chrome_debug,
    chrome_profiles,
    copy_profile,
    profile_store,
)
from app.core.config import PROJECT_ROOT, Settings, load_settings
from app.core.platform import PlatformName, detect_platform


ChromeMode = Literal["headed", "headless"]
_SUPERVISOR_HEALTH_ATTEMPTS = 20
_SUPERVISOR_HEALTH_RETRY_SECONDS = 0.25


class ChromeLifecycleError(RuntimeError):
    pass


class ChromeSupervisorMaintenanceError(RuntimeError):
    pass


@dataclass(frozen=True)
class ChromeSupervisorMaintenanceResult:
    state: Literal["ready", "not_managed"]
    message: str


@dataclass(frozen=True)
class ChromeProfileSnapshot:
    profile_id: str
    name: str
    initialized: bool
    source_available: bool
    active: bool


def _platform_from_environment() -> PlatformName:
    test_platform = os.environ.get("CHUB_TEST_PLATFORM")
    if test_platform == "Darwin":
        return "macos"
    if test_platform == "Linux":
        return "ubuntu"
    return detect_platform()


def _snapshot(current: object) -> ChromeStatusSnapshot:
    try:
        chrome_debug.chrome_executable()
        chrome_available = True
    except RuntimeError as exc:
        if str(exc) != "Google Chrome executable was not found":
            raise
        chrome_available = False
    return ChromeStatusSnapshot(
        state=str(getattr(current, "state", "invalid")),
        mode=(
            getattr(current, "mode")
            if isinstance(getattr(current, "mode", None), str)
            else None
        ),
        endpoint=str(getattr(current, "endpoint", "")),
        user_data_dir=str(getattr(current, "user_data_dir", "")),
        profile_directory=(
            getattr(current, "profile_directory")
            if isinstance(getattr(current, "profile_directory", None), str)
            else None
        ),
        process_ids=[
            int(value)
            for value in getattr(current, "process_ids", [])
            if isinstance(value, int) and not isinstance(value, bool)
        ],
        chrome_available=chrome_available,
    )


class ChromeLifecycleUseCase:
    """Owns fixed Debug Chrome lifecycle operations for one Chub process."""

    def __init__(
        self,
        settings: Settings | None = None,
        *,
        detected_platform: PlatformName | None = None,
        supervisor_socket: Path | None = None,
    ) -> None:
        self._platform = detected_platform or _platform_from_environment()
        if supervisor_socket is not None:
            self._supervisor_socket = supervisor_socket
        elif self._platform == "ubuntu":
            resolved_settings = settings or load_settings()
            self._supervisor_socket = socket_path(resolved_settings.automations.runtime_dir)
        else:
            self._supervisor_socket = None

    @property
    def uses_supervisor(self) -> bool:
        return self._supervisor_socket is not None

    def status_snapshot(self) -> ChromeStatusSnapshot:
        try:
            if self._supervisor_socket is not None:
                return request_supervisor(self._supervisor_socket, "status")
            return _snapshot(chrome_debug.status())
        except ChromeSupervisorError as exc:
            raise ChromeLifecycleError("Debug Chrome Supervisor 当前不可用") from exc
        except RuntimeError as exc:
            raise ChromeLifecycleError(str(exc)) from exc
        except Exception as exc:
            raise ChromeLifecycleError("无法检查 Debug Chrome 状态") from exc

    def public_status(self) -> tuple[str, str, str | None]:
        try:
            current = self.status_snapshot()
        except ChromeLifecycleError as exc:
            if str(exc) == "Google Chrome executable was not found":
                return (
                    "unavailable",
                    "未检测到 Google Chrome；安装后可使用受管浏览器自动化能力。",
                    None,
                )
            return "unavailable", str(exc), None
        if current.state == "running":
            return (
                "running",
                "已运行",
                {"headed": "有界面", "headless": "无界面"}.get(current.mode),
            )
        if current.state == "stopped":
            if not current.chrome_available:
                return (
                    "unavailable",
                    "未检测到 Google Chrome；安装后可使用受管浏览器自动化能力。",
                    None,
                )
            return "stopped", "未启动，启动后可提供受管浏览器自动化能力。", None
        return "invalid", "状态异常", None

    def require_running(self) -> ChromeStatusSnapshot:
        current = self.status_snapshot()
        if current.state != "running":
            _, message, _ = self.public_status()
            raise ChromeLifecycleError(message)
        return current

    def current_profile(self) -> str | None:
        current = self.status_snapshot()
        return current.profile_directory if current.state == "running" else None

    def active_profile(self) -> str | None:
        try:
            return profile_store.active_profile(chrome_debug.DEFAULT_USER_DATA_DIR)
        except (OSError, RuntimeError) as exc:
            raise ChromeLifecycleError("无法确认 Debug Chrome 当前浏览器账户") from exc

    def start(self, mode: ChromeMode = "headless") -> ChromeStatusSnapshot:
        if mode not in {"headed", "headless"}:
            raise ChromeLifecycleError("Debug Chrome 启动模式无效")
        try:
            if self._supervisor_socket is not None:
                return request_supervisor(self._supervisor_socket, "start", mode=mode)
            return _snapshot(chrome_debug.start(headless=mode == "headless"))
        except ChromeSupervisorError as exc:
            raise ChromeLifecycleError("Debug Chrome Supervisor 当前不可用") from exc
        except RuntimeError as exc:
            raise ChromeLifecycleError(str(exc)) from exc

    def stop(self) -> ChromeStatusSnapshot:
        try:
            if self._supervisor_socket is not None:
                return request_supervisor(self._supervisor_socket, "stop")
            return _snapshot(chrome_debug.stop())
        except ChromeSupervisorError as exc:
            raise ChromeLifecycleError("Debug Chrome Supervisor 当前不可用") from exc
        except RuntimeError as exc:
            raise ChromeLifecycleError(str(exc)) from exc

    def browser_profiles(self) -> tuple[list[ChromeProfileSnapshot], str | None]:
        target = chrome_debug.DEFAULT_USER_DATA_DIR
        source_error = None
        try:
            source = {
                profile.directory: profile for profile in chrome_profiles.list_profiles()
            }
        except (OSError, RuntimeError):
            source = {}
            source_error = "无法读取默认 Chrome 用户"
        try:
            initialized = set(profile_store.copied_profiles(target))
            active = profile_store.active_profile(target)
        except (OSError, RuntimeError):
            initialized = set()
            active = None
        profiles = []
        for profile_id in sorted(set(source) | initialized, key=chrome_profiles.profile_sort_key):
            source_profile = source.get(profile_id)
            if source_profile is not None:
                name = source_profile.name
            else:
                try:
                    name = profile_store.profile_display_name(target, profile_id)
                except (OSError, RuntimeError):
                    name = profile_id
            profiles.append(
                ChromeProfileSnapshot(
                    profile_id=profile_id,
                    name=name,
                    initialized=profile_id in initialized,
                    source_available=source_profile is not None,
                    active=profile_id == active,
                )
            )
        return profiles, source_error

    def initialize_and_start(self, profile_id: str, mode: ChromeMode = "headless") -> ChromeStatusSnapshot:
        profiles, _ = self.browser_profiles()
        selected = next((item for item in profiles if item.profile_id == profile_id), None)
        if selected is None:
            raise ChromeLifecycleError("Chrome profile is not available")
        if not selected.initialized:
            if not selected.source_available:
                raise ChromeLifecycleError("Chrome profile source is not available")
            copy_profile.copy_profile(
                profile_id,
                target=chrome_debug.DEFAULT_USER_DATA_DIR,
                close_running=False,
            )
        profile_store.select_profile(chrome_debug.DEFAULT_USER_DATA_DIR, profile_id)
        current = self.start(mode)
        if current.state != "running" or current.profile_directory != profile_id:
            raise ChromeLifecycleError("Debug Chrome did not start with the selected profile")
        return current

    def select_and_start(self, profile_id: str, mode: ChromeMode = "headless") -> ChromeStatusSnapshot:
        profiles, _ = self.browser_profiles()
        selected = next((item for item in profiles if item.profile_id == profile_id), None)
        if selected is None or not selected.initialized:
            raise ChromeLifecycleError("Debug Chrome profile is not initialized")
        profile_store.select_profile(chrome_debug.DEFAULT_USER_DATA_DIR, profile_id)
        current = self.start(mode)
        if current.state != "running" or current.profile_directory != profile_id:
            raise ChromeLifecycleError("Debug Chrome did not start with the selected profile")
        return current

    def select_profile_and_start(self, profile_id: str, mode: ChromeMode) -> ChromeStatusSnapshot:
        profile_store.select_profile(chrome_debug.DEFAULT_USER_DATA_DIR, profile_id)
        current = self.start(mode)
        if current.state != "running" or current.profile_directory != profile_id:
            raise ChromeLifecycleError("Debug Chrome did not start with the selected profile")
        return current

    def cleanup_interrupted_profile_copy(self) -> None:
        target = chrome_debug.DEFAULT_USER_DATA_DIR
        with profile_store.profile_store_lock(target):
            copy_profile.cleanup_stale_staging(target)


class ChromeSupervisorMaintenanceUseCase:
    """Reconciles only the fixed Ubuntu Debug Chrome Supervisor service."""

    def __init__(
        self,
        *,
        project_root: Path = PROJECT_ROOT,
        detected_platform: PlatformName | None = None,
    ) -> None:
        self._project_root = project_root
        self._platform = detected_platform or _platform_from_environment()

    def reconcile(self, *, restart: bool) -> ChromeSupervisorMaintenanceResult:
        if self._platform == "macos":
            return ChromeSupervisorMaintenanceResult("not_managed", "")
        if self._platform != "ubuntu":
            raise ChromeSupervisorMaintenanceError("unsupported platform")
        command = self._project_root / "scripts" / "platform" / "service-management.sh"
        try:
            result = subprocess.run(
                [str(command), "chrome-supervisor-reconcile", *( ["--restart"] if restart else [] )],
                cwd=self._project_root,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=20,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise ChromeSupervisorMaintenanceError(
                "Debug Chrome Supervisor did not become active"
            ) from exc
        if result.returncode != 0:
            detail = (result.stderr or result.stdout).strip()
            if "socket did not become ready" in detail:
                raise ChromeSupervisorMaintenanceError(
                    "Debug Chrome Supervisor socket did not become ready"
                )
            raise ChromeSupervisorMaintenanceError(
                "Debug Chrome Supervisor did not become active"
            )
        return ChromeSupervisorMaintenanceResult("ready", "Debug Chrome Supervisor is active")


def _supervisor_health(*, wait: bool) -> int:
    lifecycle = ChromeLifecycleUseCase()
    if not lifecycle.uses_supervisor:
        return 0
    attempts = _SUPERVISOR_HEALTH_ATTEMPTS if wait else 1
    for attempt in range(attempts):
        try:
            lifecycle.status_snapshot()
            return 0
        except ChromeLifecycleError:
            if attempt + 1 < attempts:
                time.sleep(_SUPERVISOR_HEALTH_RETRY_SECONDS)
    return 1


def main() -> int:
    parser = argparse.ArgumentParser(prog="chub chrome supervisor")
    parser.add_argument("command", choices=("reconcile", "health"))
    parser.add_argument("--restart", action="store_true")
    parser.add_argument("--wait", action="store_true")
    arguments = parser.parse_args()
    if arguments.command == "health":
        return _supervisor_health(wait=arguments.wait)
    try:
        result = ChromeSupervisorMaintenanceUseCase().reconcile(restart=arguments.restart)
    except ChromeSupervisorMaintenanceError as exc:
        print(f"chub: {exc}", file=sys.stderr)
        return 1
    if result.state == "ready":
        print(result.message)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
