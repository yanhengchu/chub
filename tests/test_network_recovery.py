from __future__ import annotations

import subprocess

import pytest

from app.core.config import Settings
from app.services.network_recovery import (
    NMCLI,
    NetworkRecoveryError,
    restart_network,
)


WIFI_UUID = "61243ed4-ca59-4f3f-87bb-8e9d3ebe381c"
VPN_UUID = "c583eb7c-9e3a-4686-8980-f3978fd6a6f6"
WIFI_DEVICE = "wlp3s0"


class NetworkManagerRunner:
    def __init__(self) -> None:
        self.calls: list[list[str]] = []
        self.radio_enabled = True
        self.active = {WIFI_UUID, VPN_UUID}
        self.wifi_device_states = ["disconnected"]

    def __call__(self, command: list[str], _timeout: int) -> subprocess.CompletedProcess[str]:
        self.calls.append(command)
        if command == [NMCLI, "-t", "-f", "UUID", "connection", "show"]:
            return subprocess.CompletedProcess(command, 0, f"{WIFI_UUID}\n{VPN_UUID}\n", "")
        if command == [
            NMCLI,
            "-t",
            "-f",
            "UUID",
            "connection",
            "show",
            "--active",
        ]:
            return subprocess.CompletedProcess(
                command,
                0,
                "\n".join(sorted(self.active)) + "\n",
                "",
            )
        if command == [NMCLI, "connection", "down", "uuid", VPN_UUID]:
            self.active.discard(VPN_UUID)
            return subprocess.CompletedProcess(command, 0, "", "")
        if command == [NMCLI, "radio", "wifi", "off"]:
            self.radio_enabled = False
            self.active.discard(WIFI_UUID)
            return subprocess.CompletedProcess(command, 0, "", "")
        if command == [NMCLI, "radio", "wifi", "on"]:
            self.radio_enabled = True
            return subprocess.CompletedProcess(command, 0, "", "")
        if command == [NMCLI, "radio", "wifi"]:
            return subprocess.CompletedProcess(
                command,
                0,
                "enabled\n" if self.radio_enabled else "disabled\n",
                "",
            )
        if command == [NMCLI, "-t", "-f", "DEVICE,TYPE,STATE", "device", "status"]:
            state = self.wifi_device_states.pop(0) if self.wifi_device_states else "disconnected"
            return subprocess.CompletedProcess(
                command,
                0,
                f"{WIFI_DEVICE}:wifi:{state}\n",
                "",
            )
        if command[-6:] == [
            "connection",
            "up",
            "uuid",
            WIFI_UUID,
            "ifname",
            WIFI_DEVICE,
        ]:
            self.active.add(WIFI_UUID)
            return subprocess.CompletedProcess(command, 0, "", "")
        if command[-4:] == ["connection", "up", "uuid", VPN_UUID]:
            self.active.add(VPN_UUID)
            return subprocess.CompletedProcess(command, 0, "", "")
        raise AssertionError(f"unexpected command: {command}")


def configure_network_recovery(settings: Settings) -> None:
    settings.network_recovery.enabled = True
    settings.network_recovery.wifi_device = WIFI_DEVICE
    settings.network_recovery.wifi_connection_uuid = WIFI_UUID
    settings.network_recovery.vpn_connection_uuid = VPN_UUID
    settings.network_recovery.lock_file = settings.codex_pty.runtime_dir / "network.lock"


def test_network_recovery_runs_only_fixed_networkmanager_sequence(
    settings: Settings,
) -> None:
    configure_network_recovery(settings)
    runner = NetworkManagerRunner()

    result = restart_network(
        settings,
        operation_id="a" * 32,
        source_ip="local-cli",
        runner=runner,
    )

    assert result.operation_id == "a" * 32
    assert "Wi-Fi and VPN are connected" in result.message
    assert runner.active == {WIFI_UUID, VPN_UUID}
    assert all(command[0] == NMCLI for command in runner.calls)
    assert [NMCLI, "connection", "down", "uuid", VPN_UUID] in runner.calls
    assert [NMCLI, "radio", "wifi", "off"] in runner.calls
    assert [NMCLI, "radio", "wifi", "on"] in runner.calls
    assert [
        NMCLI,
        "--wait",
        "45",
        "connection",
        "up",
        "uuid",
        WIFI_UUID,
        "ifname",
        WIFI_DEVICE,
    ] in runner.calls


def test_network_recovery_waits_for_the_configured_wifi_device(
    settings: Settings,
) -> None:
    configure_network_recovery(settings)
    runner = NetworkManagerRunner()
    runner.wifi_device_states = ["unavailable", "disconnected"]

    restart_network(settings, source_ip="local-cli", runner=runner)

    state_reads = [
        command
        for command in runner.calls
        if command == [NMCLI, "-t", "-f", "DEVICE,TYPE,STATE", "device", "status"]
    ]
    assert len(state_reads) == 2


def test_network_recovery_fails_closed_when_disabled(settings: Settings) -> None:
    with pytest.raises(NetworkRecoveryError, match="not enabled"):
        restart_network(settings, source_ip="local-cli", runner=NetworkManagerRunner())


def test_network_recovery_fails_closed_on_macos_without_running_nmcli(
    settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    configure_network_recovery(settings)
    runner = NetworkManagerRunner()
    monkeypatch.setattr(
        "app.services.network_recovery.platform.system",
        lambda: "Darwin",
    )

    with pytest.raises(NetworkRecoveryError, match="only available on Ubuntu"):
        restart_network(settings, source_ip="local-cli", runner=runner)

    assert runner.calls == []
