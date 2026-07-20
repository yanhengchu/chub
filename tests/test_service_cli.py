from __future__ import annotations

import os
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
CHUB = PROJECT_ROOT / "scripts" / "chub"


@pytest.fixture
def service_env(tmp_path: Path) -> tuple[dict[str, str], Path]:
    fake_bin = tmp_path / "fake-bin"
    fake_bin.mkdir()
    calls = tmp_path / "manager-calls.log"

    for command in ("launchctl", "systemctl"):
        executable = fake_bin / command
        executable.write_text(
            (
                f"#!/bin/sh\nprintf '%s %s\\n' '{command}' \"$*\""
                " >> \"$CHUB_TEST_CALLS\"\n"
                f"if [ '{command}' = launchctl ] && [ \"$1\" = print ]; then"
                " exit 1; fi\n"
            ),
            encoding="utf-8",
        )
        executable.chmod(0o755)

    env = os.environ.copy()
    env.update(
        {
            "HOME": str(tmp_path / "home"),
            "PATH": f"{fake_bin}:{env['PATH']}",
            "CHUB_COMMAND_DIR": str(tmp_path / "commands"),
            "CHUB_LAUNCH_AGENTS_DIR": str(tmp_path / "launch-agents"),
            "CHUB_SYSTEMD_USER_DIR": str(tmp_path / "systemd"),
            "CHUB_TEST_CALLS": str(calls),
        }
    )
    return env, calls


def run_chub(
    command: str,
    env: dict[str, str],
    *,
    relative: bool = False,
    cwd: Path = PROJECT_ROOT,
) -> subprocess.CompletedProcess[str]:
    executable = str(CHUB.relative_to(PROJECT_ROOT)) if relative else str(CHUB)
    return subprocess.run(
        ["bash", executable, command],
        cwd=cwd,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )


@pytest.mark.parametrize(
    ("platform", "service_file", "manager_call"),
    [
        ("Darwin", "launch-agents/com.chub.node.plist", "launchctl bootstrap"),
        ("Linux", "systemd/chub.service", "systemctl --user enable --now"),
    ],
)
def test_install_writes_service_and_global_command(
    service_env: tuple[dict[str, str], Path],
    platform: str,
    service_file: str,
    manager_call: str,
) -> None:
    env, calls = service_env
    env["CHUB_TEST_PLATFORM"] = platform

    result = run_chub("install", env)

    assert result.returncode == 0, result.stderr
    generated = Path(env["HOME"]).parent / service_file
    content = generated.read_text(encoding="utf-8")
    assert str(PROJECT_ROOT) in content
    assert str(PROJECT_ROOT / ".venv" / "bin" / "python") in content
    assert (Path(env["CHUB_COMMAND_DIR"]) / "chub").resolve() == CHUB.resolve()
    assert manager_call in calls.read_text(encoding="utf-8")
    assert "HUB_TOKEN" not in content


@pytest.mark.parametrize("platform", ["Darwin", "Linux"])
def test_install_is_repeatable(
    service_env: tuple[dict[str, str], Path],
    platform: str,
) -> None:
    env, _ = service_env
    env["CHUB_TEST_PLATFORM"] = platform

    assert run_chub("install", env).returncode == 0
    result = run_chub("install", env)

    assert result.returncode == 0, result.stderr


def test_install_refuses_to_replace_unrelated_command(
    service_env: tuple[dict[str, str], Path],
) -> None:
    env, _ = service_env
    env["CHUB_TEST_PLATFORM"] = "Linux"
    command = Path(env["CHUB_COMMAND_DIR"]) / "chub"
    command.parent.mkdir(parents=True)
    command.write_text("unrelated", encoding="utf-8")

    result = run_chub("install", env)

    assert result.returncode != 0
    assert "refusing to replace existing command" in result.stderr
    assert command.read_text(encoding="utf-8") == "unrelated"


def test_install_refuses_command_from_another_path(
    service_env: tuple[dict[str, str], Path],
) -> None:
    env, _ = service_env
    env["CHUB_TEST_PLATFORM"] = "Linux"
    other_command = Path(env["PATH"].split(":", maxsplit=1)[0]) / "chub"
    other_command.write_text("#!/bin/sh\n", encoding="utf-8")
    other_command.chmod(0o755)

    result = run_chub("install", env)

    assert result.returncode != 0
    assert "another chub command is already on PATH" in result.stderr


def test_relative_bootstrap_creates_absolute_command_link(
    service_env: tuple[dict[str, str], Path],
) -> None:
    env, _ = service_env
    env["CHUB_TEST_PLATFORM"] = "Darwin"

    result = run_chub("install", env, relative=True)

    assert result.returncode == 0, result.stderr
    command = Path(env["CHUB_COMMAND_DIR"]) / "chub"
    assert command.readlink().is_absolute()
    assert command.resolve() == CHUB.resolve()


def test_help_works_outside_project_directory(
    service_env: tuple[dict[str, str], Path],
    tmp_path: Path,
) -> None:
    env, _ = service_env

    result = run_chub("help", env, cwd=tmp_path)

    assert result.returncode == 0
    assert "Usage: chub <command>" in result.stdout


def test_logs_uses_configured_log_path(
    service_env: tuple[dict[str, str], Path],
    tmp_path: Path,
) -> None:
    env, _ = service_env
    configured_log = tmp_path / "custom" / "configured.log"
    configured_log.parent.mkdir()
    configured_log.write_text("configured log entry\n", encoding="utf-8")
    config_file = tmp_path / "settings.yaml"
    config_file.write_text(
        "\n".join(
            [
                "app:",
                "  name: Hub",
                "  version: 0.1.0",
                "node:",
                "  id: test",
                "  name: Test",
                "  type: unknown",
                "server:",
                "  host: 127.0.0.1",
                "  port: 8080",
                "security: {}",
                "logs:",
                f"  file: {configured_log}",
                "",
            ]
        ),
        encoding="utf-8",
    )
    env["HUB_CONFIG_FILE"] = str(config_file)
    process = subprocess.Popen(
        ["bash", str(CHUB), "logs"],
        cwd=tmp_path,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        assert process.stdout is not None
        assert process.stdout.readline().strip() == "configured log entry"
    finally:
        process.terminate()
        process.wait(timeout=3)


def test_restart_checks_configured_listen_address(
    service_env: tuple[dict[str, str], Path],
    tmp_path: Path,
) -> None:
    class HealthHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            assert self.path == "/api/health"
            body = b'{"success":true}'
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format: str, *args: object) -> None:
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), HealthHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    config_file = tmp_path / "settings.yaml"
    config_file.write_text(
        "\n".join(
            [
                "app:",
                "  name: Hub",
                "  version: 0.1.0",
                "node:",
                "  id: test",
                "  name: Test",
                "  type: unknown",
                "server:",
                "  host: 127.0.0.1",
                f"  port: {server.server_port}",
                "security: {}",
                "",
            ]
        ),
        encoding="utf-8",
    )
    env, _ = service_env
    env["CHUB_TEST_PLATFORM"] = "Linux"
    env["HUB_CONFIG_FILE"] = str(config_file)
    try:
        result = run_chub("restart", env, cwd=tmp_path)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)

    assert result.returncode == 0, result.stderr
    assert (
        f"Chub is healthy on http://127.0.0.1:{server.server_port}/api/health"
        in result.stdout
    )


def test_start_warns_when_listener_is_not_tailscale(
    service_env: tuple[dict[str, str], Path],
    tmp_path: Path,
) -> None:
    config_file = tmp_path / "settings.yaml"
    config_file.write_text(
        "\n".join(
            [
                "app:",
                "  name: Hub",
                "  version: 0.1.0",
                "node:",
                "  id: test",
                "  name: Test",
                "  type: unknown",
                "server:",
                "  host: 0.0.0.0",
                "  port: 8080",
                "security: {}",
                "",
            ]
        ),
        encoding="utf-8",
    )
    env, _ = service_env
    env["CHUB_TEST_PLATFORM"] = "Linux"
    env["HUB_CONFIG_FILE"] = str(config_file)

    result = run_chub("start", env, cwd=tmp_path)

    assert result.returncode == 0, result.stderr
    assert "is not a Tailscale IP" in result.stderr
    assert "Codex PTY will be disabled" in result.stderr


@pytest.mark.parametrize("platform", ["Darwin", "Linux"])
def test_uninstall_removes_only_service_and_owned_command(
    service_env: tuple[dict[str, str], Path],
    platform: str,
) -> None:
    env, _ = service_env
    env["CHUB_TEST_PLATFORM"] = platform
    assert run_chub("install", env).returncode == 0

    result = run_chub("uninstall", env)

    assert result.returncode == 0, result.stderr
    assert not (Path(env["CHUB_COMMAND_DIR"]) / "chub").exists()
    assert PROJECT_ROOT.exists()
    assert (PROJECT_ROOT / ".env").exists()


def test_help_and_unknown_command(service_env: tuple[dict[str, str], Path]) -> None:
    env, _ = service_env
    help_result = run_chub("help", env)
    invalid_result = run_chub("invalid", env)

    assert help_result.returncode == 0
    assert "chub restart" not in help_result.stdout
    assert "restart" in help_result.stdout
    assert invalid_result.returncode != 0
    assert "unknown command" in invalid_result.stderr


@pytest.mark.parametrize(
    ("platform", "command", "manager_call"),
    [
        ("Darwin", "start", "launchctl bootstrap"),
        ("Darwin", "stop", "launchctl bootout"),
        ("Linux", "start", "systemctl --user start"),
        ("Linux", "stop", "systemctl --user stop"),
        ("Linux", "status", "systemctl --user status"),
    ],
)
def test_service_commands_use_platform_manager(
    service_env: tuple[dict[str, str], Path],
    platform: str,
    command: str,
    manager_call: str,
) -> None:
    env, calls = service_env
    env["CHUB_TEST_PLATFORM"] = platform
    assert run_chub("install", env).returncode == 0
    calls.write_text("", encoding="utf-8")

    result = run_chub(command, env)

    assert result.returncode == 0, result.stderr
    assert manager_call in calls.read_text(encoding="utf-8")
