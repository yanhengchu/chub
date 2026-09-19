from __future__ import annotations

import hashlib
import json
import os
import plistlib
import signal
import shutil
import socket
import stat
import subprocess
import threading
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from app.quick_worker import PROTOCOL_VERSION
from app.services.system_upgrade import SystemUpgradeOperation, runtime_recovery_plan


PROJECT_ROOT = Path(__file__).resolve().parents[1]
CHUB = PROJECT_ROOT / "scripts" / "chub"
WEB_RESTART = PROJECT_ROOT / "scripts" / "maintenance" / "chub-web-restart"
WORKER_RELOAD = PROJECT_ROOT / "scripts" / "maintenance" / "chub-worker-reload"
SYSTEM_RECOVERY = (
    PROJECT_ROOT / "scripts" / "maintenance" / "chub-system-recovery-reset"
)


@pytest.fixture
def service_env(tmp_path: Path) -> tuple[dict[str, str], Path]:
    class HealthHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            if self.path != "/api/health":
                self.send_error(404)
                return
            body = b'{"success":true}'
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format: str, *args: object) -> None:
            return

    health_server = ThreadingHTTPServer(("127.0.0.1", 0), HealthHandler)
    health_thread = threading.Thread(target=health_server.serve_forever, daemon=True)
    health_thread.start()
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
    supervisor_runtime = Path("/tmp") / (
        "chub-service-" + hashlib.sha256(str(tmp_path).encode()).hexdigest()[:12]
    )
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
                f"  port: {health_server.server_port}",
                "security: {}",
                "logs:",
                f"  file: {tmp_path / 'hub.log'}",
                f"  operations_file: {tmp_path / 'operations.log'}",
                f"  worker_operations_file: {tmp_path / 'worker-operations.log'}",
                "ai_runtime:",
                "  shared:",
                f"    workspace: {tmp_path / 'workspace'}",
                f"    state_dir: {tmp_path / 'state'}",
                f"    runtime_dir: {supervisor_runtime}",
                "  codex:",
                "    enabled: true",
                "automations:",
                f"  runtime_dir: {supervisor_runtime}",
                "",
            ]
        ),
        encoding="utf-8",
    )
    supervisor_socket = supervisor_runtime / "debug-chrome-supervisor.sock"
    supervisor_socket.parent.mkdir(mode=0o700)
    supervisor_listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    supervisor_listener.bind(str(supervisor_socket))
    supervisor_listener.listen(8)
    supervisor_listener.settimeout(0.1)
    supervisor_stopping = threading.Event()

    def serve_supervisor_status() -> None:
        while not supervisor_stopping.is_set():
            try:
                connection, _ = supervisor_listener.accept()
            except TimeoutError:
                continue
            with connection:
                connection.recv(4096)
                connection.sendall(
                    b'{"ok":true,"data":{"state":"stopped","mode":null,'
                    b'"endpoint":"http://127.0.0.1:9222","user_data_dir":"/tmp/debug",'
                    b'"profile_directory":null,"process_ids":[],"chrome_available":true}}\n'
                )

    supervisor_thread = threading.Thread(target=serve_supervisor_status, daemon=True)
    supervisor_thread.start()

    for command in ("journalctl", "launchctl", "systemctl"):
        executable = fake_bin / command
        executable.write_text(
            (
                f"#!/bin/sh\nprintf '%s %s\\n' '{command}' \"$*\""
                " >> \"$CHUB_TEST_CALLS\"\n"
                f"if [ '{command}' = launchctl ] && [ \"$1\" = print ]; then"
                " if [ \"${CHUB_TEST_LAUNCHCTL_PRINT:-}\" = running ]; then"
                " printf 'state = running\\n'; exit 0; fi;"
                " [ \"${CHUB_TEST_LAUNCHCTL_PRINT:-}\" = available ] && exit 0;"
                " exit 1; fi\n"
                f"if [ '{command}' = systemctl ] && [ \"$2\" = is-active ] &&"
                " [ \"${CHUB_TEST_SYSTEMCTL_INACTIVE:-}\" = 1 ]; then exit 1; fi\n"
            ),
            encoding="utf-8",
        )
        executable.chmod(0o755)

    env = os.environ.copy()
    for name in (
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
            "CHUB_TEST_SUPERVISOR_SOCKET": str(supervisor_socket),
        }
    )
    try:
        yield env, calls
    finally:
        supervisor_stopping.set()
        supervisor_thread.join(timeout=3)
        supervisor_listener.close()
        supervisor_socket.unlink(missing_ok=True)
        supervisor_runtime.rmdir()
        health_server.shutdown()
        health_server.server_close()
        health_thread.join(timeout=3)


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
    if platform == "Darwin":
        env["CHUB_TEST_LAUNCHCTL_PRINT"] = "available"

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


@pytest.mark.parametrize("platform", ["Darwin", "Linux"])
def test_upgrade_logs_uses_the_platform_log_source(
    service_env: tuple[dict[str, str], Path],
    platform: str,
) -> None:
    env, calls = service_env
    env["CHUB_TEST_PLATFORM"] = platform
    if platform == "Darwin":
        log_dir = Path(env["CHUB_SERVICE_LOG_DIR"])
        log_dir.mkdir()
        (log_dir / "system-upgrade.out.log").write_text(
            "upgrade log entry\n", encoding="utf-8"
        )
        process = subprocess.Popen(
            ["bash", env["CHUB_TEST_SCRIPT"], "upgrade", "logs"],
            cwd=Path(env["CHUB_TEST_ROOT"]),
            env=env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=True,
        )
        try:
            assert process.stdout is not None
            assert process.stdout.readline().strip() == "upgrade log entry"
        finally:
            os.killpg(process.pid, signal.SIGTERM)
            process.wait(timeout=5)
        assert not calls.exists() or "journalctl" not in calls.read_text(encoding="utf-8")
        return

    result = run_chub("upgrade", env, "logs")
    assert result.returncode == 0, result.stderr
    assert "journalctl --user -u chub-system-upgrade.service -n 100 -f" in (
        calls.read_text(encoding="utf-8")
    )


def test_web_restart_is_deferred_inside_quick_interaction(
    service_env: tuple[dict[str, str], Path],
    tmp_path: Path,
) -> None:
    env, calls = service_env
    request_dir = tmp_path / "restart-requests"
    request_dir.mkdir()
    env.update(
        {
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
            "CHUB_QUICK_TASK_ID": "task-1",
            "CHUB_QUICK_RESTART_DIR": str(request_dir),
        }
    )

    result = run_chub("web", env, "restart")

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


def test_install_creates_and_preserves_the_sibling_local_modules_root(
    service_env: tuple[dict[str, str], Path],
) -> None:
    env, _ = service_env
    env["CHUB_TEST_PLATFORM"] = "Linux"
    workspace = Path(env["CHUB_TEST_ROOT"])
    local_root = workspace.parent / "chub-local-modules"

    result = run_chub("install", env)

    assert result.returncode == 0, result.stderr
    index = local_root / "chub-modules.json"
    assert json.loads(index.read_text("utf-8")) == {
        "schema_version": 1,
        "modules": [],
    }
    marker = local_root / "device-module.py"
    marker.write_text("keep", encoding="utf-8")

    result = run_chub("install", env, "--force")

    assert result.returncode == 0, result.stderr
    assert marker.read_text("utf-8") == "keep"


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


def test_chrome_supervisor_reconcile_writes_and_starts_linux_service(
    service_env: tuple[dict[str, str], Path],
) -> None:
    env, calls = service_env
    env["CHUB_TEST_PLATFORM"] = "Linux"

    result = run_chub("chrome", env, "supervisor", "reconcile")

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


def test_chrome_supervisor_reconcile_restarts_linux_service(
    service_env: tuple[dict[str, str], Path],
) -> None:
    env, calls = service_env
    env["CHUB_TEST_PLATFORM"] = "Linux"

    result = run_chub("chrome", env, "supervisor", "reconcile", "--restart")

    assert result.returncode == 0, result.stderr
    assert result.stdout == "Debug Chrome Supervisor is active\n"
    assert calls.read_text(encoding="utf-8").splitlines() == [
        "systemctl --user daemon-reload",
        "systemctl --user enable chub-debug-chrome.service",
        "systemctl --user restart chub-debug-chrome.service",
        "systemctl --user is-active --quiet chub-debug-chrome.service",
    ]


def test_chrome_supervisor_reconcile_fails_when_socket_is_unavailable(
    service_env: tuple[dict[str, str], Path],
) -> None:
    env, _ = service_env
    env["CHUB_TEST_PLATFORM"] = "Linux"
    Path(env["CHUB_TEST_SUPERVISOR_SOCKET"]).unlink()

    result = run_chub("chrome", env, "supervisor", "reconcile")

    assert result.returncode == 1
    assert result.stderr == "chub: Debug Chrome Supervisor socket did not become ready\n"
    assert "service-management" not in result.stderr


def test_chrome_supervisor_reconcile_preserves_fixed_systemd_failure_output(
    service_env: tuple[dict[str, str], Path],
) -> None:
    env, _ = service_env
    env["CHUB_TEST_PLATFORM"] = "Linux"
    env["CHUB_TEST_SYSTEMCTL_INACTIVE"] = "1"

    result = run_chub("chrome", env, "supervisor", "reconcile")

    assert result.returncode == 1
    assert result.stderr == "chub: Debug Chrome Supervisor did not become active\n"
    assert "service-management" not in result.stderr


def test_chrome_supervisor_reconcile_is_a_macos_noop(
    service_env: tuple[dict[str, str], Path],
) -> None:
    env, calls = service_env
    env["CHUB_TEST_PLATFORM"] = "Darwin"

    result = run_chub("chrome", env, "supervisor", "reconcile")

    assert result.returncode == 0
    assert result.stdout == ""
    assert not calls.exists()


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
    assert "Usage: chub <resource> <action>" in result.stdout


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
        ["bash", env["CHUB_TEST_SCRIPT"], "web", "logs"],
        cwd=tmp_path,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,
    )
    try:
        assert process.stdout is not None
        assert process.stdout.readline().strip() == "configured log entry"
    finally:
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
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
        result = run_chub("web", env, "restart", cwd=tmp_path)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)

    assert result.returncode == 0, result.stderr
    assert (
        f"Chub is healthy on http://127.0.0.1:{server.server_port}/api/health"
        in result.stdout
    )


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
    assert "chub web restart" not in help_result.stdout
    assert "web <start|stop|restart|logs>" in help_result.stdout
    assert "check" in help_result.stdout
    assert "worker <health|drain|reload|recover|start|stop|status|logs>" in help_result.stdout
    assert "reload cancels queued and running Worker tasks" in help_result.stdout
    assert "upgrade <service|logs>" in help_result.stdout
    assert "version, --version" in help_result.stdout
    assert "chrome supervisor reconcile [--restart]" in help_result.stdout
    assert "capability <page-read|page-interact> ..." in help_result.stdout
    assert invalid_result.returncode != 0
    assert "unknown command" in invalid_result.stderr


@pytest.mark.parametrize(
    "legacy_command",
    [
        "start",
        "stop",
        "restart",
        "logs",
        "worker-health",
        "worker-drain",
        "worker-reload",
        "worker-recover",
        "worker-start",
        "worker-stop",
        "worker-service-status",
        "network-restart",
        "service-definitions",
        "runtime-dependencies",
        "system-upgrade-service",
        "chrome-supervisor-ensure",
        "chrome-supervisor-reconcile",
    ],
)
def test_legacy_flat_commands_are_unknown(
    service_env: tuple[dict[str, str], Path],
    legacy_command: str,
) -> None:
    env, _ = service_env

    result = run_chub(legacy_command, env)

    assert result.returncode != 0
    assert f"unknown command: {legacy_command}" in result.stderr


def test_chrome_supervisor_ensure_is_not_a_supported_subcommand(
    service_env: tuple[dict[str, str], Path],
) -> None:
    env, _ = service_env

    result = run_chub("chrome", env, "supervisor", "ensure")

    assert result.returncode != 0
    assert "usage: chub chrome supervisor reconcile [--restart]" in result.stderr


@pytest.mark.parametrize(
    ("command", "arguments"),
    [
        ("page-read", ("--help",)),
        ("page-interact", ("--help",)),
    ],
)
def test_capability_commands_reach_the_direct_local_entrypoint(
    service_env: tuple[dict[str, str], Path],
    command: str,
    arguments: tuple[str, ...],
) -> None:
    env, _ = service_env

    result = run_chub("capability", env, command, *arguments)

    assert result.returncode == 0
    assert command in result.stdout


def test_version_reports_configured_version_and_platform(
    service_env: tuple[dict[str, str], Path],
) -> None:
    env, _ = service_env
    env["CHUB_TEST_PLATFORM"] = "Darwin"

    result = run_chub("--version", env)

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "Chub v0.1.0 · macos"


def test_status_accepts_only_verbose_option(
    service_env: tuple[dict[str, str], Path],
) -> None:
    env, _ = service_env

    result = run_chub("status", env, "--unexpected")

    assert result.returncode != 0
    assert "usage: chub status [--verbose]" in result.stderr


def test_status_reports_concise_component_summary(
    service_env: tuple[dict[str, str], Path],
) -> None:
    env, _ = service_env
    env["CHUB_TEST_PLATFORM"] = "Darwin"

    result = run_chub("status", env)

    assert result.returncode != 0
    assert "Chub Web · service unknown · health " in result.stdout
    assert "Quick Worker · service unknown · unavailable" in result.stdout
    assert "Debug Chrome Supervisor · not-managed" in result.stdout
    assert "System upgrade executor · missing" in result.stdout


def test_check_is_read_only_and_returns_failure_when_system_is_unhealthy(
    service_env: tuple[dict[str, str], Path],
) -> None:
    env, calls = service_env
    env["CHUB_TEST_PLATFORM"] = "Darwin"

    result = run_chub("check", env)

    assert result.returncode != 0
    assert "Chub check failed" in result.stderr
    manager_calls = calls.read_text(encoding="utf-8") if calls.exists() else ""
    # Without an installed service definition, the concise status path reports
    # the service as unknown without querying the service manager.
    assert "bootstrap" not in manager_calls
    assert "kickstart" not in manager_calls
    assert "bootout" not in manager_calls

def test_worker_reload_command_drains_tasks_and_checks_worker_final_state() -> None:
    cli_content = CHUB.read_text(encoding="utf-8")
    content = WORKER_RELOAD.read_text(encoding="utf-8")
    reload_body = content[content.index("reload_worker() {") :]

    assert 'scripts/maintenance/chub-worker-reload" reload' in cli_content
    assert "quick_worker_drain" in reload_body
    reload_service_body = content[
        content.index("reload_worker_service() {") :
        content.index("reload_worker() {")
    ]
    assert "clear_retired_worker_state" in reload_service_body
    assert "run_platform_action worker-definition-exists" in reload_service_body
    assert "run_platform_action worker-stop" in reload_service_body
    assert "run_platform_action worker-start" in reload_service_body
    assert reload_service_body.index("worker-stop") < reload_service_body.index(
        "clear_retired_worker_state"
    ) < reload_service_body.index("worker-start")
    assert "launchctl" not in content
    assert "systemctl" not in content
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
        content.index("quick_worker_drain() {") :
        content.index("clear_retired_worker_state() {")
    ]
    assert "quick_worker_drain" in maintenance_body
    assert "worker_protocol_version" in content
    assert 'data.get("protocol_version") != 7' not in content
    record_body = content[
        content.index("record_worker_reload_operation() {") :
        content.index("reload_worker() {")
    ]
    assert 'CHUB_WORKER_RELOAD_EXTERNAL_LOGGING:-' in record_body
    assert record_body.index("CHUB_WORKER_RELOAD_EXTERNAL_LOGGING") < record_body.index(
        "write_operation"
    )
    assert "verified_idle=true" in reload_body
    assert "reason=reason or None" in record_body


def test_maintenance_service_adapters_delegate_platform_manager() -> None:
    maintenance_scripts = (
        WEB_RESTART,
        WORKER_RELOAD,
        PROJECT_ROOT / "scripts" / "maintenance" / "chub-system-upgrade-start",
        PROJECT_ROOT / "scripts" / "maintenance" / "chub-system-upgrade-restart",
        SYSTEM_RECOVERY,
    )

    for script in maintenance_scripts:
        content = script.read_text(encoding="utf-8")
        assert "scripts/platform/service-management.sh" in content
        assert "launchctl" not in content
        assert "systemctl" not in content


def test_recovery_reset_requires_explicit_force_without_touching_services(
    service_env: tuple[dict[str, str], Path],
) -> None:
    env, calls = service_env

    result = run_chub("recovery", env, "reset")

    assert result.returncode == 1
    assert "usage: chub recovery reset --force" in result.stderr
    assert not calls.exists()


def test_recovery_reset_stops_the_upgrade_executor_before_core_services(
    service_env: tuple[dict[str, str], Path],
) -> None:
    env, calls = service_env
    env["CHUB_TEST_PLATFORM"] = "Linux"
    platform_script = (
        Path(env["CHUB_TEST_ROOT"]) / "scripts" / "platform" / "service-management.sh"
    )

    result = subprocess.run(
        [str(platform_script), "recovery-core-stop"],
        cwd=Path(env["CHUB_TEST_ROOT"]),
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert calls.read_text(encoding="utf-8").splitlines() == [
        "systemctl --user stop chub-system-upgrade.service",
        "systemctl --user stop chub.service",
        "systemctl --user stop chub-quick-worker.service",
    ]


def test_macos_recovery_reset_stops_fixed_jobs_without_definitions(
    service_env: tuple[dict[str, str], Path],
) -> None:
    env, calls = service_env
    env["CHUB_TEST_PLATFORM"] = "Darwin"
    platform_script = (
        Path(env["CHUB_TEST_ROOT"]) / "scripts" / "platform" / "service-management.sh"
    )

    result = subprocess.run(
        [str(platform_script), "recovery-core-stop"],
        cwd=Path(env["CHUB_TEST_ROOT"]),
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    manager_calls = calls.read_text(encoding="utf-8")
    assert "launchctl bootout gui/" in manager_calls
    assert "com.chub.system-upgrade" in manager_calls
    assert "com.chub.node" in manager_calls
    assert "com.chub.quick-worker" in manager_calls


def test_recovery_reset_bypasses_upgrade_state_but_keeps_fixed_boundaries() -> None:
    cli = CHUB.read_text(encoding="utf-8")
    start = cli.index("force_runtime_recovery() {")
    end = cli.index("install_command()", start)
    recovery_body = cli[start:end]
    script = SYSTEM_RECOVERY.read_text(encoding="utf-8")

    assert "require_system_upgrade_idle" not in recovery_body
    assert "require_worker_idle_for_maintenance" not in recovery_body
    assert "recovery reset must be run from a local terminal" in recovery_body
    assert "recovery-core-stop" in script
    assert "app.system_recovery_cli force-reset" in script
    assert "chub-web-restart" in script
    assert "launchctl" not in script
    assert "systemctl" not in script


def test_system_upgrade_start_reconciles_the_current_oneshot_definition() -> None:
    content = (
        PROJECT_ROOT / "scripts" / "platform" / "service-management.sh"
    ).read_text(encoding="utf-8")
    start_body = content[
        content.index("load_system_upgrade_service() {") : content.index(
            "start_system_upgrade_service() {"
        )
    ]

    assert "write_macos_system_upgrade_service" in start_body
    assert "write_systemd_system_upgrade_service" in start_body
    assert "launchctl bootout" in start_body
    assert "launchctl bootstrap" in start_body
    assert "systemctl --user daemon-reload" in start_body


def test_macos_upgrade_recovery_does_not_reload_an_active_executor(
    service_env: tuple[dict[str, str], Path],
) -> None:
    env, calls = service_env
    env["CHUB_TEST_PLATFORM"] = "Darwin"
    env["CHUB_TEST_LAUNCHCTL_PRINT"] = "running"
    state_dir = Path(env["CHUB_TEST_ROOT"]).parent / "state"
    state_dir.mkdir(mode=0o700)
    loaded = runtime_recovery_plan()
    now = datetime.now(UTC)
    operation = SystemUpgradeOperation(
        operation_id="a" * 32,
        plan=loaded.plan,
        fingerprint=loaded.fingerprint,
        status="started",
        stage="restarting_services",
        source_ip="127.0.0.1",
        old_instance_id="old-instance",
        destructive_started=True,
        restart_launch_state="launched",
        message="正在重启 Chub Web 和 Quick Worker。",
        requested_at=now,
        updated_at=now,
    )
    state_path = state_dir / "system-upgrade.json"
    state_path.write_text(operation.model_dump_json(), encoding="utf-8")
    state_path.chmod(0o600)
    launch_agents = Path(env["CHUB_LAUNCH_AGENTS_DIR"])
    launch_agents.mkdir(mode=0o700)
    (launch_agents / "com.chub.system-upgrade.plist").write_text(
        "placeholder", encoding="utf-8"
    )

    result = run_chub("upgrade", env, "service")

    assert result.returncode == 0, result.stderr
    assert "already running" in result.stdout
    manager_calls = calls.read_text(encoding="utf-8")
    assert "launchctl print gui/" in manager_calls
    assert "launchctl bootout" not in manager_calls
    assert "launchctl bootstrap" not in manager_calls
    assert "launchctl kickstart" not in manager_calls


def test_macos_upgrade_start_reloads_an_inactive_oneshot_definition(
    service_env: tuple[dict[str, str], Path],
) -> None:
    env, calls = service_env
    env["CHUB_TEST_PLATFORM"] = "Darwin"
    platform_script = Path(env["CHUB_TEST_ROOT"]) / "scripts" / "platform" / "service-management.sh"

    result = subprocess.run(
        [str(platform_script), "system-upgrade-start"],
        cwd=Path(env["CHUB_TEST_ROOT"]),
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    manager_calls = calls.read_text(encoding="utf-8")
    assert manager_calls.index("launchctl bootout gui/") < manager_calls.index(
        "launchctl bootstrap gui/"
    ) < manager_calls.index("launchctl kickstart gui/")


def test_stop_controls_only_chub_web_without_worker_precondition(
    service_env: tuple[dict[str, str], Path],
) -> None:
    env, calls = service_env
    env["CHUB_TEST_PLATFORM"] = "Linux"
    assert run_chub("install", env).returncode == 0
    calls.write_text("", encoding="utf-8")
    env["CHUB_TEST_SYSTEMCTL_INACTIVE"] = "1"

    stopped = run_chub("web", env, "stop")

    assert stopped.returncode == 0, stopped.stderr
    manager_calls = calls.read_text(encoding="utf-8")
    assert "systemctl --user stop chub.service" in manager_calls
    assert "chub-quick-worker.service" not in manager_calls


@pytest.mark.parametrize("command", ["drain", "reload", "recover"])
def test_worker_maintenance_refuses_to_wait_on_its_own_quick_task(
    service_env: tuple[dict[str, str], Path],
    command: str,
) -> None:
    env, calls = service_env
    env["CHUB_QUICK_TASK_ID"] = "task-1"

    result = run_chub("worker", env, command)

    assert result.returncode != 0
    assert "local terminal" in result.stderr
    assert not calls.exists()


@pytest.mark.parametrize(
    ("platform", "action", "manager_call"),
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
    action: str,
    manager_call: str,
) -> None:
    env, calls = service_env
    env["CHUB_TEST_PLATFORM"] = platform
    assert run_chub("install", env).returncode == 0
    calls.write_text("", encoding="utf-8")

    result = run_chub("web", env, action, *(("--force",) if action == "stop" else ()))

    assert result.returncode == 0, result.stderr
    assert manager_call in calls.read_text(encoding="utf-8")


@pytest.mark.parametrize(
    ("action", "expected_usage"),
    [
        ("web-start", "usage: service-management.sh web-start"),
        ("core-stop", "usage: service-management.sh core-stop"),
        (
            "all-services-uninstall",
            "usage: service-management.sh all-services-uninstall",
        ),
    ],
)
def test_platform_service_adapter_rejects_extra_arguments(
    service_env: tuple[dict[str, str], Path],
    action: str,
    expected_usage: str,
) -> None:
    env, calls = service_env
    env["CHUB_TEST_PLATFORM"] = "Linux"
    workspace = Path(env["CHUB_TEST_ROOT"])

    result = subprocess.run(
        [
            "bash",
            str(workspace / "scripts" / "platform" / "service-management.sh"),
            action,
            "unexpected",
        ],
        cwd=workspace,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode != 0
    assert expected_usage in result.stderr
    assert not calls.exists()


@pytest.mark.parametrize(
    ("platform", "action", "web_call"),
    [
        (
            "Darwin",
            "start",
            "com.chub.node",
        ),
        (
            "Darwin",
            "stop",
            "com.chub.node",
        ),
        (
            "Linux",
            "start",
            "start chub.service",
        ),
        (
            "Linux",
            "stop",
            "stop chub.service",
        ),
    ],
)
def test_chub_commands_manage_only_the_web_control_plane(
    service_env: tuple[dict[str, str], Path],
    platform: str,
    action: str,
    web_call: str,
) -> None:
    env, calls = service_env
    env["CHUB_TEST_PLATFORM"] = platform
    assert run_chub("install", env).returncode == 0
    calls.write_text("", encoding="utf-8")

    result = run_chub("web", env, action, *(("--force",) if action == "stop" else ()))

    assert result.returncode == 0, result.stderr
    manager_calls = calls.read_text(encoding="utf-8")
    assert web_call in manager_calls
    assert "chub-quick-worker" not in manager_calls
