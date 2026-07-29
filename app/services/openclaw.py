from __future__ import annotations

from datetime import datetime
import json
import os
import re
import shutil
import subprocess
import tempfile
import threading
import time
from typing import Literal

from pydantic import BaseModel

from app.codex.models import utc_now
from app.core.response import ApiError


OpenClawState = Literal[
    "unavailable",
    "unconfigured",
    "service_missing",
    "stopped",
    "running",
    "degraded",
    "unknown",
]
OpenClawChannelState = Literal[
    "unavailable",
    "not_configured",
    "stopped",
    "running",
    "degraded",
    "unknown",
]
OpenClawOwnerState = Literal[
    "unavailable",
    "not_configured",
    "configured",
    "unknown",
]

STATUS_TIMEOUT_SECONDS = 15
ACTION_TIMEOUT_SECONDS = 45
FINAL_STATE_TIMEOUT_SECONDS = 20
MAX_COMMAND_OUTPUT_BYTES = 256_000
TAILSCALE_STATUS_TIMEOUT_SECONDS = 5
TAILSCALE_HOST_PATTERN = re.compile(
    r"^(?=.{1,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+ts\.net$"
)


class OpenClawStatus(BaseModel):
    state: OpenClawState
    installed: bool
    configured: bool
    service_installed: bool
    service_loaded: bool
    ready: bool
    version: str | None = None
    service_manager: str | None = None
    bind_mode: str | None = None
    port: int | None = None
    access_url: str | None = None
    channel_state: OpenClawChannelState
    channel_count: int
    channel_running_count: int
    channel_message: str
    owner_state: OpenClawOwnerState
    owner_count: int
    owner_message: str
    message: str
    checked_at: datetime


class OpenClawManager:
    def __init__(self) -> None:
        self._operation_lock = threading.Lock()

    @staticmethod
    def _resolve_executable() -> str | None:
        return shutil.which("openclaw")

    def status(self) -> OpenClawStatus:
        status, executable = self._gateway_status()
        if executable is None:
            return status
        channel_update, channel_ids = self._channel_status(executable, status)
        owner_update = self._owner_status(executable, status, channel_ids)
        return status.model_copy(
            update={
                "access_url": self._tailscale_access_url(status.port),
                **channel_update,
                **owner_update,
            }
        )

    def _gateway_status(self) -> tuple[OpenClawStatus, str | None]:
        executable = self._resolve_executable()
        if executable is None:
            return OpenClawStatus(
                state="unavailable",
                installed=False,
                configured=False,
                service_installed=False,
                service_loaded=False,
                ready=False,
                channel_state="unavailable",
                channel_count=0,
                channel_running_count=0,
                channel_message="OpenClaw 未安装，无法检查消息通道。",
                owner_state="unavailable",
                owner_count=0,
                owner_message="OpenClaw 未安装，无法检查 Owner 权限。",
                message="当前节点未安装 OpenClaw。",
                checked_at=utc_now(),
            ), None
        try:
            payload = self._run_json(
                executable,
                ["gateway", "status", "--json"],
                timeout=STATUS_TIMEOUT_SECONDS,
            )
        except ApiError as exc:
            return OpenClawStatus(
                state="unknown",
                installed=True,
                configured=False,
                service_installed=False,
                service_loaded=False,
                ready=False,
                channel_state="unknown",
                channel_count=0,
                channel_running_count=0,
                channel_message="暂时无法检查消息通道。",
                owner_state="unknown",
                owner_count=0,
                owner_message="暂时无法检查 Owner 权限。",
                message=exc.message,
                checked_at=utc_now(),
            ), None
        return self._parse_status(payload), executable

    def _channel_status(
        self,
        executable: str,
        gateway_status: OpenClawStatus,
    ) -> tuple[dict[str, object], set[str]]:
        if not gateway_status.ready:
            return {
                "channel_state": "unavailable",
                "channel_count": 0,
                "channel_running_count": 0,
                "channel_message": "Gateway 未就绪，无法检查消息通道。",
            }, set()
        try:
            payload = self._run_json(
                executable,
                ["channels", "status", "--json"],
                timeout=STATUS_TIMEOUT_SECONDS,
            )
        except ApiError:
            return {
                "channel_state": "unknown",
                "channel_count": 0,
                "channel_running_count": 0,
                "channel_message": "消息通道状态检查失败，请刷新后重试。",
            }, set()
        return self._parse_channel_status(payload)

    def _owner_status(
        self,
        executable: str,
        gateway_status: OpenClawStatus,
        channel_ids: set[str],
    ) -> dict[str, object]:
        if not gateway_status.configured:
            return {
                "owner_state": "unavailable",
                "owner_count": 0,
                "owner_message": "OpenClaw 未完成初始化，无法检查 Owner 权限。",
            }
        try:
            payload = self._run_json(
                executable,
                ["config", "get", "commands", "--json"],
                timeout=STATUS_TIMEOUT_SECONDS,
            )
        except ApiError:
            return {
                "owner_state": "unknown",
                "owner_count": 0,
                "owner_message": "Owner 权限状态检查失败，请刷新后重试。",
            }
        return self._parse_owner_status(payload, channel_ids)

    @staticmethod
    def _parse_owner_status(
        payload: dict,
        channel_ids: set[str] | None = None,
    ) -> dict[str, object]:
        owner_entries = payload.get("ownerAllowFrom")
        if owner_entries is None:
            owners: list[object] = []
        elif isinstance(owner_entries, list):
            owners = [str(entry).strip() for entry in owner_entries if str(entry).strip()]
        else:
            return {
                "owner_state": "unknown",
                "owner_count": 0,
                "owner_message": "OpenClaw 返回了无法识别的 Owner 权限状态。",
            }

        normalized_channel_ids = {channel_id.lower() for channel_id in channel_ids or set()}
        applicable_owners = [
            owner
            for owner in owners
            if ":" not in owner
            or owner.split(":", 1)[0].lower() in normalized_channel_ids
        ]
        count = len(applicable_owners) if normalized_channel_ids else len(owners)
        if count == 0:
            return {
                "owner_state": "not_configured",
                "owner_count": 0,
                "owner_message": "当前消息通道未配置 Owner，无法使用受限管理工具。",
            }
        return {
            "owner_state": "configured",
            "owner_count": count,
            "owner_message": f"当前消息通道已配置 {count} 个 Owner 身份。",
        }

    @staticmethod
    def _parse_channel_status(
        payload: dict,
    ) -> tuple[dict[str, object], set[str]]:
        channel_accounts = payload.get("channelAccounts")
        if not isinstance(channel_accounts, dict):
            return {
                "channel_state": "unknown",
                "channel_count": 0,
                "channel_running_count": 0,
                "channel_message": "OpenClaw 返回了无法识别的消息通道状态。",
            }, set()

        accounts: list[dict] = []
        configured_channel_ids: set[str] = set()
        for channel_id, channel_entries in channel_accounts.items():
            if not isinstance(channel_entries, list):
                continue
            if any(
                isinstance(entry, dict)
                and entry.get("enabled") is True
                and entry.get("configured") is True
                for entry in channel_entries
            ):
                configured_channel_ids.add(str(channel_id))
            accounts.extend(
                entry for entry in channel_entries if isinstance(entry, dict)
            )

        configured = [
            account
            for account in accounts
            if account.get("enabled") is True and account.get("configured") is True
        ]
        running = [
            account
            for account in configured
            if account.get("running") is True
            and account.get("restartPending") is not True
            and not account.get("lastError")
        ]
        total = len(configured)
        running_count = len(running)

        if not accounts or not configured:
            state: OpenClawChannelState = "not_configured"
            message = "尚未配置已启用的消息通道。"
        elif running_count == total:
            state = "running"
            message = f"{running_count} 个消息通道运行正常。"
        elif running_count == 0:
            state = "stopped"
            message = f"{total} 个已配置消息通道均未正常运行。"
        else:
            state = "degraded"
            message = f"{running_count}/{total} 个消息通道运行正常。"

        return {
            "channel_state": state,
            "channel_count": total,
            "channel_running_count": running_count,
            "channel_message": message,
        }, configured_channel_ids

    def control(self, action: Literal["start", "stop", "restart"]) -> OpenClawStatus:
        if not self._operation_lock.acquire(blocking=False):
            raise ApiError(
                409,
                "openclaw_operation_in_progress",
                "OpenClaw 正在执行其他维护操作。",
            )
        try:
            before = self.status()
            self._validate_action(action, before)
            executable = self._resolve_executable()
            if executable is None:
                raise ApiError(409, "openclaw_not_installed", "当前节点未安装 OpenClaw。")
            self._run_json(
                executable,
                ["gateway", action, "--json"],
                timeout=ACTION_TIMEOUT_SECONDS,
            )
            return self._wait_for_final_state(action)
        finally:
            self._operation_lock.release()

    @staticmethod
    def _validate_action(action: str, status: OpenClawStatus) -> None:
        if not status.installed:
            raise ApiError(409, "openclaw_not_installed", "当前节点未安装 OpenClaw。")
        if status.state == "unknown":
            raise ApiError(
                503,
                "openclaw_status_unavailable",
                "暂时无法确认 OpenClaw Gateway 状态，请刷新后重试。",
            )
        if not status.configured:
            raise ApiError(
                409,
                "openclaw_not_configured",
                "OpenClaw 尚未完成初始化配置。",
            )
        if not status.service_installed:
            raise ApiError(
                409,
                "openclaw_service_not_installed",
                "OpenClaw Gateway 后台服务尚未安装。",
            )
        if action in {"stop", "restart"} and status.state not in {
            "running",
            "degraded",
        }:
            raise ApiError(
                409,
                "openclaw_not_running",
                "OpenClaw Gateway 当前未运行。",
            )

    def _wait_for_final_state(self, action: str) -> OpenClawStatus:
        deadline = time.monotonic() + FINAL_STATE_TIMEOUT_SECONDS
        latest, _ = self._gateway_status()
        while time.monotonic() < deadline:
            if action == "stop":
                if latest.state == "stopped" and not latest.ready:
                    return self.status()
            elif latest.state == "running" and latest.ready:
                return self.status()
            time.sleep(0.25)
            latest, _ = self._gateway_status()
        expected = "停止" if action == "stop" else "恢复就绪"
        raise ApiError(
            504,
            "openclaw_final_state_timeout",
            f"OpenClaw 命令已执行，但 Gateway 未在限定时间内{expected}。",
        )

    @staticmethod
    def _parse_status(payload: dict) -> OpenClawStatus:
        cli = payload.get("cli") if isinstance(payload.get("cli"), dict) else {}
        config = (
            payload.get("config")
            if isinstance(payload.get("config"), dict)
            else {}
        )
        config_cli = (
            config.get("cli") if isinstance(config.get("cli"), dict) else {}
        )
        service = (
            payload.get("service")
            if isinstance(payload.get("service"), dict)
            else {}
        )
        command = (
            service.get("command")
            if isinstance(service.get("command"), dict)
            else {}
        )
        runtime = (
            service.get("runtime")
            if isinstance(service.get("runtime"), dict)
            else {}
        )
        gateway = (
            payload.get("gateway")
            if isinstance(payload.get("gateway"), dict)
            else {}
        )
        rpc = payload.get("rpc") if isinstance(payload.get("rpc"), dict) else {}
        port_data = (
            payload.get("port")
            if isinstance(payload.get("port"), dict)
            else {}
        )

        configured = bool(config_cli.get("exists") and config_cli.get("valid"))
        service_loaded = bool(service.get("loaded"))
        service_installed = bool(
            service_loaded
            or command.get("sourcePath")
            or command.get("programArguments")
        )
        ready = bool(rpc.get("ok"))
        port_status = port_data.get("status")

        if not configured:
            state: OpenClawState = "unconfigured"
            message = "OpenClaw 已安装，但尚未完成初始化配置。"
        elif not service_installed:
            state = "service_missing"
            message = "Gateway 后台服务尚未安装。"
        elif ready:
            state = "running"
            message = "Gateway 运行正常并已通过连接探测。"
        elif not service_loaded and port_status in {None, "free"}:
            state = "stopped"
            message = "Gateway 后台服务已停止。"
        elif service_loaded or runtime.get("status") == "running":
            state = "degraded"
            message = "Gateway 服务已启动，但尚未通过连接探测。"
        else:
            state = "unknown"
            message = "Gateway 状态无法确认，请刷新后重试。"

        port = gateway.get("port")
        return OpenClawStatus(
            state=state,
            installed=True,
            configured=configured,
            service_installed=service_installed,
            service_loaded=service_loaded,
            ready=ready,
            version=cli.get("version") if isinstance(cli.get("version"), str) else None,
            service_manager=(
                service.get("label")
                if isinstance(service.get("label"), str)
                else None
            ),
            bind_mode=(
                gateway.get("bindMode")
                if isinstance(gateway.get("bindMode"), str)
                else None
            ),
            port=port if isinstance(port, int) else None,
            channel_state="unavailable",
            channel_count=0,
            channel_running_count=0,
            channel_message="尚未检查消息通道。",
            owner_state="unavailable",
            owner_count=0,
            owner_message="尚未检查 Owner 权限。",
            message=message,
            checked_at=utc_now(),
        )

    def _tailscale_access_url(self, gateway_port: int | None) -> str | None:
        if gateway_port is None:
            return None
        executable = shutil.which("tailscale")
        if executable is None:
            return None
        try:
            payload = self._run_json(
                executable,
                ["serve", "status", "--json"],
                timeout=TAILSCALE_STATUS_TIMEOUT_SECONDS,
                environment_overrides={"TAILSCALE_BE_CLI": "1"},
            )
        except ApiError:
            return None
        return self._parse_tailscale_access_url(payload, gateway_port)

    @staticmethod
    def _parse_tailscale_access_url(
        payload: dict,
        gateway_port: int,
    ) -> str | None:
        web = payload.get("Web")
        if not isinstance(web, dict):
            return None
        expected_proxy = f"http://127.0.0.1:{gateway_port}"
        for endpoint, details in sorted(web.items()):
            if not isinstance(endpoint, str) or not isinstance(details, dict):
                continue
            hostname, separator, port = endpoint.rpartition(":")
            if (
                separator != ":"
                or port != "443"
                or not TAILSCALE_HOST_PATTERN.fullmatch(hostname.lower())
            ):
                continue
            handlers = details.get("Handlers")
            root = handlers.get("/") if isinstance(handlers, dict) else None
            if not isinstance(root, dict) or root.get("Proxy") != expected_proxy:
                continue
            return f"https://{hostname.lower()}/"
        return None

    @staticmethod
    def _run_json(
        executable: str,
        arguments: list[str],
        *,
        timeout: int,
        environment_overrides: dict[str, str] | None = None,
    ) -> dict:
        command_environment = None
        if environment_overrides:
            command_environment = os.environ.copy()
            command_environment.update(environment_overrides)
        try:
            with tempfile.TemporaryFile() as output_file:
                process = subprocess.run(
                    [executable, *arguments],
                    stdin=subprocess.DEVNULL,
                    stdout=output_file,
                    stderr=subprocess.DEVNULL,
                    env=command_environment,
                    timeout=timeout,
                    check=False,
                )
                output_file.seek(0)
                output = output_file.read(MAX_COMMAND_OUTPUT_BYTES + 1)
        except subprocess.TimeoutExpired as exc:
            raise ApiError(
                504,
                "openclaw_command_timeout",
                "OpenClaw 命令执行超时。",
            ) from exc
        except OSError as exc:
            raise ApiError(
                503,
                "openclaw_command_unavailable",
                "无法执行 OpenClaw 命令。",
            ) from exc
        if process.returncode != 0:
            raise ApiError(
                502,
                "openclaw_command_failed",
                "OpenClaw 命令执行失败。",
            )
        if len(output) > MAX_COMMAND_OUTPUT_BYTES:
            raise ApiError(
                502,
                "openclaw_response_too_large",
                "OpenClaw 返回的状态内容超过限制。",
            )
        try:
            payload = json.loads(output.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError, TypeError) as exc:
            raise ApiError(
                502,
                "openclaw_invalid_response",
                "OpenClaw 返回了无法识别的状态。",
            ) from exc
        if not isinstance(payload, dict):
            raise ApiError(
                502,
                "openclaw_invalid_response",
                "OpenClaw 返回了无法识别的状态。",
            )
        return payload
