from __future__ import annotations

import fcntl
import os
import platform
import subprocess
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

from app.core.config import Settings
from app.services.operation_log import write_operation


NMCLI = "/usr/bin/nmcli"
OPERATION_ACTION = "network_restart"
OPERATION_TARGET = "networkmanager:configured-wifi-vpn"


class NetworkRecoveryError(RuntimeError):
    pass


@dataclass(frozen=True)
class NetworkRecoveryResult:
    operation_id: str
    message: str


CommandRunner = Callable[[list[str], int], subprocess.CompletedProcess[str]]


def _run_command(command: list[str], timeout_seconds: int) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        check=False,
        capture_output=True,
        text=True,
        timeout=timeout_seconds,
    )


def _is_active(runner: CommandRunner, connection_uuid: str) -> bool:
    result = runner([NMCLI, "-t", "-f", "UUID", "connection", "show", "--active"], 10)
    if result.returncode != 0:
        raise NetworkRecoveryError("The final NetworkManager state could not be read.")
    return connection_uuid in {line.strip().lower() for line in result.stdout.splitlines()}


def _require_configured_connections(
    runner: CommandRunner,
    wifi_connection_uuid: str,
    vpn_connection_uuid: str,
) -> None:
    result = runner([NMCLI, "-t", "-f", "UUID", "connection", "show"], 10)
    if result.returncode != 0:
        raise NetworkRecoveryError("NetworkManager connection profiles could not be read.")
    configured = {line.strip().lower() for line in result.stdout.splitlines()}
    if wifi_connection_uuid not in configured or vpn_connection_uuid not in configured:
        raise NetworkRecoveryError("The configured Wi-Fi or VPN profile is unavailable.")


def _run_required(
    runner: CommandRunner,
    command: list[str],
    timeout_seconds: int,
    message: str,
) -> None:
    try:
        result = runner(command, timeout_seconds)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise NetworkRecoveryError(message) from exc
    if result.returncode != 0:
        raise NetworkRecoveryError(message)


def _wifi_radio_enabled(runner: CommandRunner) -> bool:
    result = runner([NMCLI, "radio", "wifi"], 10)
    if result.returncode != 0:
        raise NetworkRecoveryError("The Wi-Fi radio state could not be confirmed.")
    return result.stdout.strip().casefold() == "enabled"


def _wait_for_wifi_device(
    runner: CommandRunner,
    wifi_device: str,
    timeout_seconds: int,
) -> None:
    deadline = time.monotonic() + timeout_seconds
    while True:
        try:
            result = runner(
                [NMCLI, "-t", "-f", "DEVICE,TYPE,STATE", "device", "status"],
                10,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise NetworkRecoveryError("The Wi-Fi device state could not be read.") from exc
        if result.returncode != 0:
            raise NetworkRecoveryError("The Wi-Fi device state could not be read.")
        for line in result.stdout.splitlines():
            parts = line.split(":", 2)
            if len(parts) != 3:
                continue
            device, connection_type, state = parts
            if (
                device == wifi_device
                and connection_type == "wifi"
                and state.casefold() not in {"unavailable", "unmanaged", "unknown"}
            ):
                return
        if time.monotonic() >= deadline:
            raise NetworkRecoveryError("The configured Wi-Fi device did not become ready.")
        time.sleep(0.5)


def _restore_wifi_radio(runner: CommandRunner) -> None:
    try:
        runner([NMCLI, "radio", "wifi", "on"], 15)
    except (OSError, subprocess.TimeoutExpired):
        pass


def restart_network(
    settings: Settings,
    *,
    source_ip: str,
    operation_id: str | None = None,
    runner: CommandRunner = _run_command,
) -> NetworkRecoveryResult:
    """Run the single configured NetworkManager Wi-Fi/VPN recovery sequence."""
    config = settings.network_recovery
    resolved_operation_id = operation_id or uuid4().hex
    write_operation(
        operation_id=resolved_operation_id,
        action=OPERATION_ACTION,
        status="requested",
        target=OPERATION_TARGET,
        source_ip=source_ip,
    )
    if not config.enabled:
        write_operation(
            operation_id=resolved_operation_id,
            action=OPERATION_ACTION,
            status="failed",
            target=OPERATION_TARGET,
            source_ip=source_ip,
            reason="network recovery is disabled",
        )
        raise NetworkRecoveryError("Network restart is not enabled on this node.")
    if platform.system() != "Linux":
        write_operation(
            operation_id=resolved_operation_id,
            action=OPERATION_ACTION,
            status="failed",
            target=OPERATION_TARGET,
            source_ip=source_ip,
            reason="NetworkManager recovery is only supported on Ubuntu",
        )
        raise NetworkRecoveryError("Network restart is only available on Ubuntu.")

    assert config.wifi_connection_uuid is not None
    assert config.vpn_connection_uuid is not None
    assert config.wifi_device is not None
    lock_file = config.lock_file
    try:
        lock_file.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        descriptor = os.open(lock_file, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    except OSError as exc:
        message = "The network recovery lock could not be created."
        write_operation(
            operation_id=resolved_operation_id,
            action=OPERATION_ACTION,
            status="failed",
            target=OPERATION_TARGET,
            source_ip=source_ip,
            reason=message,
        )
        raise NetworkRecoveryError(message) from exc
    wifi_was_disabled = False
    try:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise NetworkRecoveryError("Another network restart is already in progress.") from exc
        write_operation(
            operation_id=resolved_operation_id,
            action=OPERATION_ACTION,
            status="started",
            target=OPERATION_TARGET,
            source_ip=source_ip,
        )
        _require_configured_connections(
            runner,
            config.wifi_connection_uuid,
            config.vpn_connection_uuid,
        )
        # A disconnected VPN is already at the desired stage; any other failure stops.
        vpn_down = runner(
            [NMCLI, "connection", "down", "uuid", config.vpn_connection_uuid],
            15,
        )
        if vpn_down.returncode != 0 and _is_active(runner, config.vpn_connection_uuid):
            raise NetworkRecoveryError("The configured VPN could not be disconnected.")
        _run_required(
            runner,
            [NMCLI, "radio", "wifi", "off"],
            15,
            "The Wi-Fi radio could not be turned off.",
        )
        wifi_was_disabled = True
        if _wifi_radio_enabled(runner):
            raise NetworkRecoveryError("The Wi-Fi radio did not turn off.")
        _run_required(
            runner,
            [NMCLI, "radio", "wifi", "on"],
            15,
            "The Wi-Fi radio could not be turned on.",
        )
        wifi_was_disabled = False
        if not _wifi_radio_enabled(runner):
            raise NetworkRecoveryError("The Wi-Fi radio did not turn on.")
        _wait_for_wifi_device(
            runner,
            config.wifi_device,
            config.wifi_timeout_seconds,
        )
        _run_required(
            runner,
            [
                NMCLI,
                "--wait",
                str(config.wifi_timeout_seconds),
                "connection",
                "up",
                "uuid",
                config.wifi_connection_uuid,
                "ifname",
                config.wifi_device,
            ],
            config.wifi_timeout_seconds + 5,
            "The configured Wi-Fi did not reconnect.",
        )
        if not _is_active(runner, config.wifi_connection_uuid):
            raise NetworkRecoveryError("The configured Wi-Fi final state could not be confirmed.")
        _run_required(
            runner,
            [
                NMCLI,
                "--wait",
                str(config.vpn_timeout_seconds),
                "connection",
                "up",
                "uuid",
                config.vpn_connection_uuid,
            ],
            config.vpn_timeout_seconds + 5,
            "The configured VPN did not reconnect.",
        )
        if not _is_active(runner, config.vpn_connection_uuid):
            raise NetworkRecoveryError("The configured VPN final state could not be confirmed.")
    except NetworkRecoveryError as exc:
        if wifi_was_disabled:
            _restore_wifi_radio(runner)
        write_operation(
            operation_id=resolved_operation_id,
            action=OPERATION_ACTION,
            status="failed",
            target=OPERATION_TARGET,
            source_ip=source_ip,
            reason=str(exc),
        )
        raise
    except (OSError, subprocess.TimeoutExpired) as exc:
        if wifi_was_disabled:
            _restore_wifi_radio(runner)
        message = "NetworkManager could not complete the network restart."
        write_operation(
            operation_id=resolved_operation_id,
            action=OPERATION_ACTION,
            status="failed",
            target=OPERATION_TARGET,
            source_ip=source_ip,
            reason=message,
        )
        raise NetworkRecoveryError(message) from exc
    finally:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
        finally:
            os.close(descriptor)
    write_operation(
        operation_id=resolved_operation_id,
        action=OPERATION_ACTION,
        status="succeeded",
        target=OPERATION_TARGET,
        source_ip=source_ip,
    )
    return NetworkRecoveryResult(
        operation_id=resolved_operation_id,
        message="Network restart completed. Wi-Fi and VPN are connected.",
    )
