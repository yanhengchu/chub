from __future__ import annotations

import hashlib
import json
import os
import plistlib
import shutil
import socket
import stat
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from app.quick_worker import PROTOCOL_VERSION


PROJECT_ROOT = Path(__file__).resolve().parents[1]
CHUB = PROJECT_ROOT / "scripts" / "chub"
WEB_RESTART = PROJECT_ROOT / "scripts" / "chub-web-restart"


@pytest.fixture
def service_env(tmp_path: Path) -> tuple[dict[str, str], Path]:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    shutil.copytree(PROJECT_ROOT / "scripts", workspace / "scripts")
    shutil.copytree(PROJECT_ROOT / "app", workspace / "app")
    (workspace / ".venv").symlink_to(PROJECT_ROOT / ".venv", target_is_directory=True)
    (workspace / "main.py").symlink_to(PROJECT_ROOT / "main.py")
    (workspace / "config").mkdir()
    fake_bin = tmp_path / "fake-bin"
    fake_bin.mkdir()
    calls = tmp_path / "manager-calls.log"
    settings_file = workspace / "config" / "settings.local.yaml"
    settings_file.write_text(
        "\n".join(
            [
                "app:",
                "  name: Hub",
                "  version: 0.1.0",
                "node:",
                "  id: test-node",
                "  name: Test Node",
                "  type: unknown",
                "server:",
                "  tailnet_host: null",
                "  port: 8080",
                "security: {}",
                "logs:",
                f"  file: {tmp_path / 'hub.log'}",
                f"  operations_file: {tmp_path / 'operations.log'}",
                f"  worker_operations_file: {tmp_path / 'worker-operations.log'}",
                "codex_pty:",
                f"  workspace: {tmp_path / 'workspace'}",
                f"  data_file: {tmp_path / 'state' / 'sessions.json'}",
                f"  runtime_dir: {tmp_path / 'runtime'}",
                "",
            ]
        ),
        encoding="utf-8",
    )

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
    for name in (
        "CHUB_ACTIVITY_SOURCE",
        "CHUB_QUICK_TASK_ID",
        "CHUB_QUICK_RESTART_DIR",
    ):
        env.pop(name, None)
    env.update(
        {
            "HOME": str(tmp_path / "home"),
            "PATH": f"{fake_bin}:/usr/bin:/bin",
            "CHUB_COMMAND_DIR": str(tmp_path / "commands"),
            "CHUB_LAUNCH_AGENTS_DIR": str(tmp_path / "launch-agents"),
            "CHUB_SYSTEMD_USER_DIR": str(tmp_path / "systemd"),
            "CHUB_SERVICE_LOG_DIR": str(tmp_path / "logs"),
            "CHUB_TEST_CALLS": str(calls),
            "CHUB_TEST_ROOT": str(workspace),
            "CHUB_TEST_SCRIPT": str(workspace / "scripts" / "chub"),
        }
    )
    return env, calls


def run_chub(
    command: str,
    env: dict[str, str],
    *arguments: str,
    relative: bool = False,
    cwd: Path = PROJECT_ROOT,
) -> subprocess.CompletedProcess[str]:
    script = Path(env.get("CHUB_TEST_SCRIPT", str(CHUB)))
    workspace = Path(env.get("CHUB_TEST_ROOT", str(PROJECT_ROOT)))
    if cwd == PROJECT_ROOT and "CHUB_TEST_ROOT" in env:
        cwd = workspace
    executable = str(script.relative_to(workspace)) if relative else str(script)
    return subprocess.run(
        ["bash", executable, command, *arguments],
        cwd=cwd,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )


@pytest.mark.parametrize(
    ("platform", "manager_call"),
    [
        ("Darwin", "launchctl kickstart -k gui/"),
        ("Linux", "systemctl --user --no-block restart chub.service"),
    ],
)
def test_web_restart_uses_atomic_service_manager_restart(
    service_env: tuple[dict[str, str], Path],
    platform: str,
    manager_call: str,
) -> None:
    env, calls = service_env
    env["CHUB_TEST_PLATFORM"] = platform

    result = subprocess.run(
        ["bash", str(WEB_RESTART)],
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    manager_calls = calls.read_text(encoding="utf-8")
    assert manager_call in manager_calls
    assert "quick-worker" not in manager_calls


def test_web_restart_is_deferred_inside_quick_interaction(
    service_env: tuple[dict[str, str], Path],
    tmp_path: Path,
) -> None:
    env, calls = service_env
    request_dir = tmp_path / "restart-requests"
    request_dir.mkdir()
    env.update(
        {
            "CHUB_ACTIVITY_SOURCE": "quick",
            "CHUB_QUICK_TASK_ID": "task-1",
            "CHUB_QUICK_RESTART_DIR": str(request_dir),
            "CHUB_TEST_PLATFORM": "Unsupported",
        }
    )

    result = subprocess.run(
        ["bash", str(WEB_RESTART)],
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )

    request_file = request_dir / "task-1.request"
    assert result.returncode == 0, result.stderr
    assert request_file.is_file()
    assert stat.S_IMODE(request_file.stat().st_mode) == 0o600
    assert "restart registered" in result.stdout
    assert not calls.exists()


@pytest.mark.parametrize(
    "command",
    ["worker-cutover-preflight", "worker-cutover"],
)
def test_removed_worker_cutover_commands_are_unknown(
    service_env: tuple[dict[str, str], Path],
    command: str,
) -> None:
    env, calls = service_env

    result = run_chub(command, env)

    assert result.returncode == 1
    assert "unknown command" in result.stderr
    assert not calls.exists()


def test_web_restart_does_not_fall_back_when_quick_context_is_invalid(
    service_env: tuple[dict[str, str], Path],
) -> None:
    env, calls = service_env
    env.update(
        {
            "CHUB_ACTIVITY_SOURCE": "quick",
            "CHUB_QUICK_TASK_ID": "task-1",
            "CHUB_TEST_PLATFORM": "Darwin",
        }
    )

    result = subprocess.run(
        ["bash", str(WEB_RESTART)],
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode != 0
    assert "deferred restart directory is unavailable" in result.stderr
    assert not calls.exists()


def test_web_restart_rejects_symlink_request_file(
    service_env: tuple[dict[str, str], Path],
    tmp_path: Path,
) -> None:
    env, calls = service_env
    request_dir = tmp_path / "restart-requests"
    request_dir.mkdir()
    target = tmp_path / "unrelated"
    target.write_text("keep", encoding="utf-8")
    (request_dir / "task-1.request").symlink_to(target)
    env.update(
        {
            "CHUB_ACTIVITY_SOURCE": "quick",
            "CHUB_QUICK_TASK_ID": "task-1",
            "CHUB_QUICK_RESTART_DIR": str(request_dir),
            "CHUB_TEST_PLATFORM": "Darwin",
        }
    )

    result = subprocess.run(
        ["bash", str(WEB_RESTART)],
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode != 0
    assert "deferred restart request is invalid" in result.stderr
    assert target.read_text(encoding="utf-8") == "keep"
    assert not calls.exists()


def test_chub_restart_uses_same_quick_interaction_deferral(
    service_env: tuple[dict[str, str], Path],
    tmp_path: Path,
) -> None:
    env, calls = service_env
    request_dir = tmp_path / "restart-requests"
    request_dir.mkdir()
    env.update(
        {
            "CHUB_ACTIVITY_SOURCE": "quick",
            "CHUB_QUICK_TASK_ID": "task-1",
            "CHUB_QUICK_RESTART_DIR": str(request_dir),
        }
    )

    result = run_chub("restart", env)

    assert result.returncode == 0, result.stderr
    assert (request_dir / "task-1.request").is_file()
    assert "restart registered" in result.stdout
    assert not calls.exists()


@pytest.mark.parametrize(
    ("platform", "service_file", "manager_call"),
    [
        ("Darwin", "launch-agents/com.chub.node.plist", "launchctl bootstrap"),
        ("Linux", "systemd/chub.service", "systemctl --user enable"),
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
    if platform == "Darwin":
        plistlib.loads(generated.read_bytes())
    workspace = Path(env["CHUB_TEST_ROOT"])
    assert str(workspace) in content
    assert str(workspace / ".venv" / "bin" / "python") in content
    assert (Path(env["CHUB_COMMAND_DIR"]) / "chub").resolve() == Path(env["CHUB_TEST_SCRIPT"])
    assert manager_call in calls.read_text(encoding="utf-8")
    assert "TOKEN" not in content


@pytest.mark.parametrize(
    ("platform", "worker_file", "worker_identity"),
    [
        (
            "Darwin",
            "launch-agents/com.chub.quick-worker.plist",
            "com.chub.quick-worker",
        ),
        (
            "Linux",
            "systemd/chub-quick-worker.service",
            "app.quick_worker serve",
        ),
    ],
)
def test_install_writes_independent_quick_worker_service(
    service_env: tuple[dict[str, str], Path],
    platform: str,
    worker_file: str,
    worker_identity: str,
) -> None:
    env, calls = service_env
    env["CHUB_TEST_PLATFORM"] = platform

    result = run_chub("install", env)

    assert result.returncode == 0, result.stderr
    generated = Path(env["HOME"]).parent / worker_file
    content = generated.read_text(encoding="utf-8")
    assert worker_identity in content
    assert str(Path(env["CHUB_TEST_ROOT"])) in content
    assert "PartOf=" not in content
    assert "com.chub.node" not in content
    assert "chub.service" not in content
    if platform == "Darwin":
        assert "<key>Umask</key>" in content
        assert "<integer>63</integer>" in content
        assert stat.S_IMODE(
            Path(env["CHUB_SERVICE_LOG_DIR"]).stat().st_mode
        ) == 0o700
        for name in ("quick-worker.out.log", "quick-worker.err.log"):
            log_file = Path(env["CHUB_SERVICE_LOG_DIR"]) / name
            assert stat.S_IMODE(log_file.stat().st_mode) == 0o600
    else:
        assert "UMask=0077" in content
    manager_calls = calls.read_text(encoding="utf-8")
    assert "quick-worker" in manager_calls


@pytest.mark.parametrize(
    ("platform", "service_file", "runner_identity"),
    [
        (
            "Darwin",
            "launch-agents/com.chub.system-upgrade.plist",
            "chub-system-upgrade-restart",
        ),
        (
            "Linux",
            "systemd/chub-system-upgrade.service",
            "chub-system-upgrade-restart",
        ),
    ],
)
def test_install_writes_independent_system_upgrade_service(
    service_env: tuple[dict[str, str], Path],
    platform: str,
    service_file: str,
    runner_identity: str,
) -> None:
    env, calls = service_env
    env["CHUB_TEST_PLATFORM"] = platform

    result = run_chub("install", env)

    assert result.returncode == 0, result.stderr
    generated = Path(env["HOME"]).parent / service_file
    content = generated.read_text(encoding="utf-8")
    assert runner_identity in content
    assert "chub.service" not in content
    assert "chub-quick-worker.service" not in content
    if platform == "Darwin":
        plistlib.loads(generated.read_bytes())
        assert "<key>RunAtLoad</key>" in content
        assert "<false/>" in content
    else:
        assert "Type=oneshot" in content
        assert "TimeoutStartSec=infinity" in content
        assert "chub-system-upgrade.service" not in calls.read_text(encoding="utf-8")


def test_linux_install_writes_independent_debug_chrome_supervisor_service(
    service_env: tuple[dict[str, str], Path],
) -> None:
    env, calls = service_env
    env["CHUB_TEST_PLATFORM"] = "Linux"

    result = run_chub("install", env)

    assert result.returncode == 0, result.stderr
    service = Path(env["CHUB_SYSTEMD_USER_DIR"]) / "chub-debug-chrome.service"
    content = service.read_text(encoding="utf-8")
    assert "app.automations.chrome_supervisor serve" in content
    assert "KillMode=control-group" in content
    assert "chub.service" not in content
    assert "chub-quick-worker.service" not in content
    manager_calls = calls.read_text(encoding="utf-8")
    assert "enable chub-debug-chrome.service" in manager_calls
    assert "restart chub-debug-chrome.service" in manager_calls


def test_macos_install_does_not_write_debug_chrome_supervisor_service(
    service_env: tuple[dict[str, str], Path],
) -> None:
    env, _ = service_env
    env["CHUB_TEST_PLATFORM"] = "Darwin"

    result = run_chub("install", env)

    assert result.returncode == 0, result.stderr
    assert not (
        Path(env["CHUB_LAUNCH_AGENTS_DIR"]) / "chub-debug-chrome.service"
    ).exists()


def test_install_clears_only_retired_worker_state(
    service_env: tuple[dict[str, str], Path],
    tmp_path: Path,
) -> None:
    env, _ = service_env
    env["CHUB_TEST_PLATFORM"] = "Linux"
    root = tmp_path / "state" / "quick-worker"
    current = root / "tasks-v7"
    current.mkdir(parents=True, mode=0o700)
    os.chmod(root, 0o700)
    os.chmod(current, 0o700)
    (current / "keep").write_text("current", encoding="utf-8")
    for name in ("tasks", "tombstones", "session-leases", "legacy-deliveries-v7"):
        path = root / name
        path.mkdir(mode=0o700)
        os.chmod(path, 0o700)
        (path / "retired").write_text("old", encoding="utf-8")

    result = run_chub("install", env)

    assert result.returncode == 0, result.stderr
    assert (current / "keep").read_text(encoding="utf-8") == "current"
    assert all(not (root / name).exists() for name in (
        "tasks",
        "tombstones",
        "session-leases",
        "legacy-deliveries-v7",
    ))


@pytest.mark.parametrize("platform", ["Darwin", "Linux"])
def test_install_is_repeatable(
    service_env: tuple[dict[str, str], Path],
    platform: str,
) -> None:
    env, _ = service_env
    env["CHUB_TEST_PLATFORM"] = platform

    assert run_chub("install", env).returncode == 0
    result = run_chub("install", env, "--force")

    assert result.returncode == 0, result.stderr


def test_install_adds_discovered_nvm_codex_directory_to_service_path(
    service_env: tuple[dict[str, str], Path],
    tmp_path: Path,
) -> None:
    env, _ = service_env
    env["CHUB_TEST_PLATFORM"] = "Linux"
    nvm_bin = tmp_path / "home" / ".nvm" / "versions" / "node" / "v24" / "bin"
    nvm_bin.mkdir(parents=True)
    codex = nvm_bin / "codex"
    codex.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    codex.chmod(0o755)
    env["PATH"] = f"{nvm_bin}:{env['PATH']}"

    result = run_chub("install", env)

    assert result.returncode == 0, result.stderr
    service = Path(env["CHUB_SYSTEMD_USER_DIR"]) / "chub.service"
    assert f"Environment=PATH={nvm_bin}:" in service.read_text(encoding="utf-8")


def test_linux_install_restarts_service_to_apply_updated_environment(
    service_env: tuple[dict[str, str], Path],
) -> None:
    env, calls = service_env
    env["CHUB_TEST_PLATFORM"] = "Linux"

    result = run_chub("install", env)

    assert result.returncode == 0, result.stderr
    manager_calls = calls.read_text(encoding="utf-8")
    assert "systemctl --user daemon-reload" in manager_calls
    assert "systemctl --user enable chub.service" in manager_calls
    assert "systemctl --user enable chub-quick-worker.service" in manager_calls
    assert "systemctl --user restart chub-quick-worker.service" in manager_calls
    assert "systemctl --user restart chub.service" in manager_calls


def test_chrome_supervisor_ensure_writes_and_starts_linux_service(
    service_env: tuple[dict[str, str], Path],
) -> None:
    env, calls = service_env
    env["CHUB_TEST_PLATFORM"] = "Linux"

    result = run_chub("chrome-supervisor-ensure", env)

    assert result.returncode == 0, result.stderr
    unit = Path(env["CHUB_SYSTEMD_USER_DIR"]) / "chub-debug-chrome.service"
    assert unit.is_file()
    assert "app.automations.chrome_supervisor serve" in unit.read_text(
        encoding="utf-8"
    )
    assert calls.read_text(encoding="utf-8").splitlines() == [
        "systemctl --user daemon-reload",
        "systemctl --user enable chub-debug-chrome.service",
        "systemctl --user start chub-debug-chrome.service",
        "systemctl --user is-active --quiet chub-debug-chrome.service",
    ]


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


def test_macos_install_refuses_symlink_worker_log(
    service_env: tuple[dict[str, str], Path],
    tmp_path: Path,
) -> None:
    env, _ = service_env
    env["CHUB_TEST_PLATFORM"] = "Darwin"
    log_dir = Path(env["CHUB_SERVICE_LOG_DIR"])
    log_dir.mkdir()
    target = tmp_path / "unrelated.log"
    target.write_text("keep", encoding="utf-8")
    (log_dir / "quick-worker.out.log").symlink_to(target)

    result = run_chub("install", env)

    assert result.returncode != 0
    assert "service log is not a regular file" in result.stderr
    assert target.read_text(encoding="utf-8") == "keep"


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
    assert command.resolve() == Path(env["CHUB_TEST_SCRIPT"])


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
    env, _ = service_env
    configured_log = tmp_path / "custom" / "configured.log"
    configured_log.parent.mkdir()
    configured_log.write_text("configured log entry\n", encoding="utf-8")
    config_file = Path(env["CHUB_TEST_ROOT"]) / "config" / "settings.local.yaml"
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
                "  tailnet_host: null",
                "  port: 8080",
                "security: {}",
                "logs:",
                f"  file: {configured_log}",
                "",
            ]
        ),
        encoding="utf-8",
    )
    process = subprocess.Popen(
            ["bash", env["CHUB_TEST_SCRIPT"], "logs"],
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

    env, _ = service_env
    server = ThreadingHTTPServer(("127.0.0.1", 0), HealthHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    config_file = Path(env["CHUB_TEST_ROOT"]) / "config" / "settings.local.yaml"
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
                "  tailnet_host: null",
                f"  port: {server.server_port}",
                "security: {}",
                "",
            ]
        ),
        encoding="utf-8",
    )
    env, _ = service_env
    env["CHUB_TEST_PLATFORM"] = "Linux"
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


def test_start_rejects_non_private_listener(
    service_env: tuple[dict[str, str], Path],
    tmp_path: Path,
) -> None:
    env, _ = service_env
    config_file = Path(env["CHUB_TEST_ROOT"]) / "config" / "settings.local.yaml"
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
                "  tailnet_host: 0.0.0.0",
                "  port: 8080",
                "security: {}",
                "",
            ]
        ),
        encoding="utf-8",
    )
    env, _ = service_env
    env["CHUB_TEST_PLATFORM"] = "Linux"

    result = run_chub("start", env, cwd=tmp_path)

    assert result.returncode == 1
    assert "configuration could not be read" in result.stderr


@pytest.mark.parametrize("platform", ["Darwin", "Linux"])
def test_uninstall_removes_only_service_and_owned_command(
    service_env: tuple[dict[str, str], Path],
    platform: str,
) -> None:
    env, _ = service_env
    env["CHUB_TEST_PLATFORM"] = platform
    assert run_chub("install", env).returncode == 0

    result = run_chub("uninstall", env, "--force")

    assert result.returncode == 0, result.stderr
    assert not (Path(env["CHUB_COMMAND_DIR"]) / "chub").exists()
    if platform == "Darwin":
        assert not (
            Path(env["CHUB_LAUNCH_AGENTS_DIR"])
            / "com.chub.quick-worker.plist"
        ).exists()
    else:
        assert not (
            Path(env["CHUB_SYSTEMD_USER_DIR"])
            / "chub-quick-worker.service"
        ).exists()
    workspace = Path(env["CHUB_TEST_ROOT"])
    assert workspace.exists()
    assert not (workspace / ".env").exists()


def test_help_and_unknown_command(service_env: tuple[dict[str, str], Path]) -> None:
    env, _ = service_env
    help_result = run_chub("help", env)
    invalid_result = run_chub("invalid", env)

    assert help_result.returncode == 0
    assert "chub restart" not in help_result.stdout
    assert "restart" in help_result.stdout
    assert "check" in help_result.stdout
    assert "worker-drain" in help_result.stdout
    assert "worker-reload" in help_result.stdout
    assert "worker-recover" in help_result.stdout
    assert invalid_result.returncode != 0
    assert "unknown command" in invalid_result.stderr


def test_check_is_read_only_and_returns_failure_when_system_is_unhealthy(
    service_env: tuple[dict[str, str], Path],
) -> None:
    env, calls = service_env
    env["CHUB_TEST_PLATFORM"] = "Darwin"

    result = run_chub("check", env)

    assert result.returncode != 0
    assert "Chub check failed" in result.stderr
    manager_calls = calls.read_text(encoding="utf-8")
    assert "launchctl print" in manager_calls
    assert "bootstrap" not in manager_calls
    assert "kickstart" not in manager_calls
    assert "bootout" not in manager_calls

def test_worker_reload_command_drains_tasks_and_checks_worker_final_state() -> None:
    content = CHUB.read_text(encoding="utf-8")
    reload_body = content[content.index("quick_worker_reload() {") :]

    assert "quick_worker_drain" in reload_body
    reload_service_body = content[
        content.index("reload_worker_service() {") :
        content.index("quick_worker_reload() {")
    ]
    assert "clear_retired_worker_state" in reload_service_body
    assert "worker_health_generation" in reload_body
    assert "worker_health_protocol" in reload_body
    assert "worker_health_is_idle" in reload_body
    assert "old_generation" in reload_body
    assert "old_protocol" in reload_body
    assert "new_generation" in reload_body
    assert "health_check true" not in reload_body
    assert reload_body.index("reload_worker_service") < reload_body.index(
        'succeeded "$new_generation"'
    )

    maintenance_body = content[
        content.index("require_worker_idle_for_maintenance() {") :
        content.index("install_service() {")
    ]
    assert "quick_worker_drain" in maintenance_body
    assert "worker_protocol_version" in content
    assert 'data.get("protocol_version") != 7' not in content
    record_body = content[
        content.index("record_worker_reload_operation() {") :
        content.index("worker_health_generation() {")
    ]
    assert 'CHUB_WORKER_RELOAD_EXTERNAL_LOGGING:-' in record_body
    assert record_body.index("CHUB_WORKER_RELOAD_EXTERNAL_LOGGING") < record_body.index(
        "write_operation"
    )


def test_stop_refuses_active_worker_without_explicit_force(
    service_env: tuple[dict[str, str], Path],
    tmp_path: Path,
) -> None:
    env, calls = service_env
    env["CHUB_TEST_PLATFORM"] = "Linux"
    worker_runtime = tmp_path / "worker-runtime"
    config_file = Path(env["CHUB_TEST_ROOT"]) / "config" / "settings.local.yaml"
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
                "  tailnet_host: null",
                "  port: 8080",
                "security: {}",
                "codex_pty:",
                f"  runtime_dir: {worker_runtime}",
                f"  data_file: {tmp_path / 'sessions.json'}",
                "",
            ]
        ),
        encoding="utf-8",
    )
    assert run_chub("install", env).returncode == 0
    calls.write_text("", encoding="utf-8")

    identity = hashlib.sha256(str(worker_runtime).encode("utf-8")).hexdigest()[:12]
    socket_dir = Path("/tmp") / f"chub-qw-{os.getuid()}-{identity}"
    socket_dir.mkdir(mode=0o700)
    socket_path = socket_dir / "worker.sock"
    listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    listener.bind(str(socket_path))
    listener.listen(1)

    def serve_health() -> None:
        try:
            connection, _ = listener.accept()
        except OSError:
            return
        with connection:
            request = json.loads(connection.makefile("rb").readline())
            response = {
                "success": True,
                "request_id": request["request_id"],
                "data": {
                    "protocol_version": PROTOCOL_VERSION,
                    "status": "ready",
                    "generation": "a" * 32,
                    "code_version": "test-worker",
                    "pid": os.getpid(),
                    "active_tasks": 1,
                },
            }
            connection.sendall((json.dumps(response) + "\n").encode("utf-8"))

    thread = threading.Thread(target=serve_health, daemon=True)
    thread.start()
    try:
        blocked = run_chub("stop", env)
    finally:
        thread.join(timeout=3)
        listener.close()
        socket_path.unlink(missing_ok=True)
        socket_dir.rmdir()

    assert blocked.returncode != 0
    assert "active or queued tasks" in blocked.stderr
    assert calls.read_text(encoding="utf-8") == ""

    forced = run_chub("stop", env, "--force")
    assert forced.returncode == 0, forced.stderr
    assert "systemctl --user stop" in calls.read_text(encoding="utf-8")


@pytest.mark.parametrize("command", ["worker-drain", "worker-reload", "worker-recover"])
def test_worker_maintenance_refuses_to_wait_on_its_own_quick_task(
    service_env: tuple[dict[str, str], Path],
    command: str,
) -> None:
    env, calls = service_env
    env["CHUB_ACTIVITY_SOURCE"] = "quick"

    result = run_chub(command, env)

    assert result.returncode != 0
    assert "local terminal" in result.stderr
    assert not calls.exists()


@pytest.mark.parametrize(
    ("platform", "command", "manager_call"),
    [
        ("Darwin", "start", "launchctl bootstrap"),
        ("Darwin", "stop", "launchctl bootout"),
        ("Linux", "start", "systemctl --user start"),
        ("Linux", "stop", "systemctl --user stop"),
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

    result = run_chub(command, env, *(("--force",) if command == "stop" else ()))

    assert result.returncode == 0, result.stderr
    assert manager_call in calls.read_text(encoding="utf-8")


@pytest.mark.parametrize(
    ("platform", "command", "web_call", "worker_call"),
    [
        (
            "Darwin",
            "start",
            "com.chub.node",
            "com.chub.quick-worker",
        ),
        (
            "Darwin",
            "stop",
            "com.chub.node",
            "com.chub.quick-worker",
        ),
        (
            "Linux",
            "start",
            "start chub.service",
            "start chub-quick-worker.service",
        ),
        (
            "Linux",
            "stop",
            "stop chub.service",
            "stop chub-quick-worker.service",
        ),
    ],
)
def test_node_commands_manage_web_and_worker_as_separate_services(
    service_env: tuple[dict[str, str], Path],
    platform: str,
    command: str,
    web_call: str,
    worker_call: str,
) -> None:
    env, calls = service_env
    env["CHUB_TEST_PLATFORM"] = platform
    assert run_chub("install", env).returncode == 0
    calls.write_text("", encoding="utf-8")

    result = run_chub(command, env, *(("--force",) if command == "stop" else ()))

    assert result.returncode == 0, result.stderr
    manager_calls = calls.read_text(encoding="utf-8")
    assert web_call in manager_calls
    assert worker_call in manager_calls
