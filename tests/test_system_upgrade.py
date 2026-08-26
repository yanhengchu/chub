import json
import os
import shutil
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import httpx
import pytest
import yaml

from app.ai_session import AiSessionManager
from app.application import create_app
from app.codex.models import QuickInteractionWeixinRoute
from app.core.build_info import SESSION_SCHEMA_VERSION, WEB_CODE_VERSION
from app.core.config import Settings
from app.quick_worker import PROTOCOL_VERSION
from app.services.openclaw import OpenClawManager
from app.services.system_upgrade import (
    SystemUpgradeCoordinator,
    SystemUpgradeSession,
    load_system_upgrade_plan,
    load_component_report,
    runtime_recovery_plan,
    system_upgrade_restart_readiness,
)
from app.system_upgrade_cli import prepare_restart
from app.quick_worker_tasks import (
    worker_leases_dir,
    worker_tasks_dir,
    worker_tombstones_dir,
)


TOKEN = "test-token-that-is-long-enough-for-tests"
PROJECT_ROOT = Path(__file__).resolve().parents[1]


def write_plan(path: Path) -> None:
    path.write_text(
        """{
  "version": 1,
  "contract_version": 1,
  "plan_id": "ai-session-manager-v1",
  "action": "runtime-data-reset",
  "title": "切换 AI Session 数据协议",
  "summary": "清理 Chub Session 关联并切换到新数据协议。",
  "source_code_version": "%s",
  "target_code_version": "%s",
  "source_session_schema": %d,
  "target_session_schema": %d,
  "source_worker_protocol": %d,
  "target_worker_protocol": %d,
  "effects": ["旧 Session 与运行记录将被清理"],
  "preserves": ["配置、日志、资料和 Codex 原生 Session 继续保留"]
}
"""
        % (
            WEB_CODE_VERSION,
            WEB_CODE_VERSION,
            SESSION_SCHEMA_VERSION,
            SESSION_SCHEMA_VERSION + 1,
            PROTOCOL_VERSION - 1,
            PROTOCOL_VERSION,
        ),
        encoding="utf-8",
    )
    path.chmod(0o600)


def test_plan_loader_accepts_only_current_fixed_source(tmp_path: Path) -> None:
    path = tmp_path / "system-upgrade.json"
    write_plan(path)

    loaded = load_system_upgrade_plan(path)

    assert loaded is not None
    assert loaded.plan.plan_id == "ai-session-manager-v1"
    assert len(loaded.fingerprint) == 64


def test_plan_loader_rejects_writable_plan(tmp_path: Path) -> None:
    path = tmp_path / "system-upgrade.json"
    write_plan(path)
    os.chmod(path, 0o666)

    with pytest.raises(OSError, match="权限"):
        load_system_upgrade_plan(path)


def test_plan_loader_allows_worker_only_target_change(tmp_path: Path) -> None:
    path = tmp_path / "system-upgrade.json"
    write_plan(path)
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["target_code_version"] = payload["source_code_version"]
    payload["target_session_schema"] = payload["source_session_schema"]
    path.write_text(json.dumps(payload), encoding="utf-8")

    loaded = load_system_upgrade_plan(path)

    assert loaded is not None
    assert loaded.plan.target_worker_protocol == PROTOCOL_VERSION


def test_plan_loader_allows_fixed_runtime_data_reset(tmp_path: Path) -> None:
    path = tmp_path / "system-upgrade.json"
    write_plan(path)
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["target_code_version"] = payload["source_code_version"]
    payload["target_session_schema"] = payload["source_session_schema"]
    payload["target_worker_protocol"] = payload["source_worker_protocol"]
    path.write_text(json.dumps(payload), encoding="utf-8")

    assert load_system_upgrade_plan(path) is not None


def test_app_uses_ai_session_manager_without_reading_legacy_store(
    settings: Settings,
) -> None:
    settings.codex_pty.data_file.write_text("[]", encoding="utf-8")
    settings.codex_pty.data_file.chmod(0o600)

    application = create_app(settings)
    try:
        assert isinstance(application.state.codex_pty_manager, AiSessionManager)
        assert application.state.ai_session_manager is application.state.codex_pty_manager
        assert not application.state.codex_pty_manager.store.list()
    finally:
        application.state.codex_pty_manager.close()


def test_restart_environment_repairs_missing_service_definitions(
    tmp_path: Path,
) -> None:
    project_root = tmp_path / "project"
    scripts = (
        project_root / "scripts" / "chub-system-upgrade-start",
        project_root / "scripts" / "chub-system-upgrade-restart",
    )
    python = project_root / ".venv" / "bin" / "python"
    systemd_root = tmp_path / "systemd"
    for path in (*scripts, python):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("#!/usr/bin/env bash\n", encoding="utf-8")
        path.chmod(0o700)
    systemd_root.mkdir()
    for name in ("chub-system-upgrade.service",):
        target = systemd_root / name
        target.write_text("[Service]\n", encoding="utf-8")
        target.chmod(0o600)
    environment = {"CHUB_SYSTEMD_USER_DIR": str(systemd_root)}

    with patch("app.services.system_upgrade.shutil.which", return_value="/bin/systemctl"):
        assert (
            system_upgrade_restart_readiness(
                project_root,
                "ubuntu",
                environment=environment,
            )
            is None
        )
        (systemd_root / "chub-system-upgrade.service").unlink()
        assert "独立服务" in system_upgrade_restart_readiness(
            project_root,
            "ubuntu",
            environment=environment,
        )


def test_coordinator_blocks_writes_and_releases_before_destructive_failure(
    tmp_path: Path,
) -> None:
    plan_path = tmp_path / "plan.json"
    write_plan(plan_path)
    loaded = load_system_upgrade_plan(plan_path)
    assert loaded is not None
    coordinator = SystemUpgradeCoordinator(
        tmp_path / "state" / "system-upgrade.json",
        plan_path,
        "old-instance",
    )
    calls = []

    operation = coordinator.begin(
        loaded,
        source_ip="127.0.0.1",
        old_worker_generation="a" * 32,
        runner=lambda operation_id: calls.append(operation_id),
    )

    assert coordinator.writes_blocked() is True
    coordinator.update(operation.operation_id, restart_process_id=os.getpid())
    coordinator.fail(operation.operation_id, "preflight failed")
    assert coordinator.writes_blocked() is False
    failed = coordinator.operation()
    assert failed is not None
    assert failed.restart_process_id is None
    assert calls == [operation.operation_id]


def test_component_results_are_persisted_and_degraded_state_is_visible(
    tmp_path: Path,
) -> None:
    plan_path = tmp_path / "plan.json"
    write_plan(plan_path)
    loaded = load_system_upgrade_plan(plan_path)
    assert loaded is not None
    state_path = tmp_path / "state" / "system-upgrade.json"
    coordinator = SystemUpgradeCoordinator(state_path, plan_path, "old-instance")
    operation = coordinator.begin(
        loaded,
        source_ip="127.0.0.1",
        old_worker_generation=None,
        runner=lambda _operation_id: None,
    )

    coordinator.record_component(
        operation.operation_id,
        "browser_supervisor",
        "degraded",
        "Supervisor unavailable",
    )
    coordinator.record_component(
        operation.operation_id,
        "quick_worker",
        "succeeded",
        "Worker ready",
    )
    coordinator.record_component(
        operation.operation_id,
        "debug_chrome_instance",
        "skipped",
        "Browser instance is outside the upgrade scope",
    )
    coordinator.update(
        operation.operation_id,
        restart_process_id=os.getpid(),
    )

    report = load_component_report(state_path, operation.operation_id)
    assert {item.component: item.status for item in report} == {
        "browser_supervisor": "degraded",
        "debug_chrome_instance": "skipped",
        "quick_worker": "succeeded",
    }
    status = coordinator.status_data(loaded, session_count=0)
    assert status.operation is not None
    assert status.operation.components[0].component == "browser_supervisor"

    coordinator.succeed(operation.operation_id)
    completed = coordinator.operation()
    assert completed is not None
    assert "browser_supervisor" in completed.message
    assert completed.restart_process_id is None


def test_coordinator_keeps_gate_closed_after_runtime_state_cleanup_failure(
    tmp_path: Path,
) -> None:
    plan_path = tmp_path / "plan.json"
    write_plan(plan_path)
    loaded = load_system_upgrade_plan(plan_path)
    assert loaded is not None
    state_path = tmp_path / "state" / "system-upgrade.json"
    coordinator = SystemUpgradeCoordinator(state_path, plan_path, "old-instance")
    operation = coordinator.begin(
        loaded,
        source_ip="127.0.0.1",
        old_worker_generation="b" * 32,
        runner=lambda _operation_id: None,
    )
    coordinator.update(
        operation.operation_id,
        stage="cleaning_state",
        destructive_started=True,
    )
    coordinator.fail(operation.operation_id, "cleanup failed")

    recovered = SystemUpgradeCoordinator(state_path, plan_path, "new-instance")

    assert recovered.writes_blocked() is True
    assert recovered.operation().status == "failed"
    with pytest.raises(Exception, match="最终验证"):
        recovered.begin(
            loaded,
            source_ip="127.0.0.1",
            old_worker_generation="c" * 32,
            runner=lambda _operation_id: None,
        )


@pytest.mark.parametrize(
    ("failed_stage", "expected_stage"),
    [
        ("cleaning_state", "draining_worker"),
        ("launching_services", "launching_services"),
        ("restarting_services", "launching_services"),
        ("verifying_new_instance", "verifying_new_instance"),
    ],
)
def test_coordinator_resumes_destructive_failure_from_durable_checkpoint(
    tmp_path: Path,
    failed_stage: str,
    expected_stage: str,
) -> None:
    plan_path = tmp_path / "plan.json"
    write_plan(plan_path)
    loaded = load_system_upgrade_plan(plan_path)
    assert loaded is not None
    coordinator = SystemUpgradeCoordinator(
        tmp_path / "state" / "system-upgrade.json",
        plan_path,
        "old-instance",
    )
    operation = coordinator.begin(
        loaded,
        source_ip="127.0.0.1",
        old_worker_generation="d" * 32,
        runner=lambda _operation_id: None,
    )
    coordinator.mark_started(operation.operation_id)
    coordinator.update(
        operation.operation_id,
        stage=failed_stage,
        destructive_started=True,
    )
    coordinator.fail(operation.operation_id, "interrupted")

    resumed = coordinator.resume_failed(lambda _operation_id: None)

    assert resumed is not None
    assert resumed.operation_id == operation.operation_id
    assert resumed.status == "started"
    assert resumed.stage == expected_stage
    assert coordinator.writes_blocked() is True


def test_coordinator_rebases_stale_final_verification_to_current_recovery_plan(
    tmp_path: Path,
) -> None:
    plan_path = tmp_path / "plan.json"
    write_plan(plan_path)
    loaded = load_system_upgrade_plan(plan_path)
    assert loaded is not None
    coordinator = SystemUpgradeCoordinator(
        tmp_path / "state" / "system-upgrade.json",
        plan_path,
        "old-instance",
    )
    operation = coordinator.begin(
        loaded,
        source_ip="127.0.0.1",
        old_worker_generation="e" * 32,
        runner=lambda _operation_id: None,
    )
    coordinator.mark_started(operation.operation_id)
    coordinator.update(
        operation.operation_id,
        stage="verifying_new_instance",
        destructive_started=True,
        restart_launch_state="launched",
    )
    coordinator.fail(operation.operation_id, "目标版本已过期")

    current = runtime_recovery_plan()
    assert coordinator.rebase_failed_verification(current) is True
    rebound = coordinator.operation()
    assert rebound is not None
    assert rebound.plan.plan_id == "runtime-recovery"
    assert rebound.fingerprint == current.fingerprint

    resumed = coordinator.resume_verification()
    assert resumed is not None
    assert resumed.status == "started"
    assert resumed.stage == "verifying_new_instance"
    assert coordinator.writes_blocked() is True


def test_coordinator_does_not_rebase_before_final_verification(
    tmp_path: Path,
) -> None:
    plan_path = tmp_path / "plan.json"
    write_plan(plan_path)
    loaded = load_system_upgrade_plan(plan_path)
    assert loaded is not None
    coordinator = SystemUpgradeCoordinator(
        tmp_path / "state" / "system-upgrade.json",
        plan_path,
        "old-instance",
    )
    operation = coordinator.begin(
        loaded,
        source_ip="127.0.0.1",
        old_worker_generation="f" * 32,
        runner=lambda _operation_id: None,
    )
    coordinator.update(
        operation.operation_id,
        stage="cleaning_state",
        destructive_started=True,
    )
    coordinator.fail(operation.operation_id, "cleanup interrupted")

    assert coordinator.rebase_failed_verification(runtime_recovery_plan()) is False


def test_coordinator_rebases_failed_cleanup_to_current_recovery_plan(
    tmp_path: Path,
) -> None:
    plan_path = tmp_path / "plan.json"
    write_plan(plan_path)
    loaded = load_system_upgrade_plan(plan_path)
    assert loaded is not None
    coordinator = SystemUpgradeCoordinator(
        tmp_path / "state" / "system-upgrade.json",
        plan_path,
        "old-instance",
    )
    operation = coordinator.begin(
        loaded,
        source_ip="127.0.0.1",
        old_worker_generation="g" * 32,
        runner=lambda _operation_id: None,
    )
    coordinator.mark_started(operation.operation_id)
    coordinator.update(
        operation.operation_id,
        stage="cleaning_state",
        destructive_started=True,
    )
    coordinator.fail(operation.operation_id, "旧恢复目标已过期")

    current = runtime_recovery_plan()
    assert coordinator.rebase_failed_recovery(current) is True
    rebound = coordinator.operation()
    assert rebound is not None
    assert rebound.plan.plan_id == "runtime-recovery"
    assert rebound.fingerprint == current.fingerprint
    assert coordinator.resume_failed(lambda _operation_id: None) is not None


def test_coordinator_persists_session_cleanup_journal(tmp_path: Path) -> None:
    plan_path = tmp_path / "plan.json"
    write_plan(plan_path)
    loaded = load_system_upgrade_plan(plan_path)
    assert loaded is not None
    coordinator = SystemUpgradeCoordinator(
        tmp_path / "state" / "system-upgrade.json",
        plan_path,
        "old-instance",
    )
    operation = coordinator.begin(
        loaded,
        source_ip="127.0.0.1",
        old_worker_generation="c" * 32,
        runner=lambda _operation_id: None,
    )
    sessions = [
        SystemUpgradeSession(session_id="session-1", native_session_id="native-1"),
        SystemUpgradeSession(session_id="session-2"),
    ]

    coordinator.update(
        operation.operation_id,
        stage="cleaning_state",
        destructive_started=True,
        sessions=sessions,
    )
    sessions[0].status = "discarded"
    coordinator.update(operation.operation_id, sessions=sessions)

    recovered = SystemUpgradeCoordinator(
        coordinator.path,
        plan_path,
        "new-instance",
    )
    recorded = recovered.operation()
    assert recorded is not None
    assert [item.status for item in recorded.sessions] == ["discarded", "pending"]
    assert recorded.discarded_sessions == 1


def test_coordinator_keeps_gate_closed_when_failure_cannot_be_persisted(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan_path = tmp_path / "plan.json"
    write_plan(plan_path)
    loaded = load_system_upgrade_plan(plan_path)
    assert loaded is not None
    coordinator = SystemUpgradeCoordinator(tmp_path / "state.json", plan_path, "old")
    operation = coordinator.begin(
        loaded,
        source_ip="127.0.0.1",
        old_worker_generation="a" * 32,
        runner=lambda _operation_id: None,
    )

    monkeypatch.setattr(
        coordinator,
        "_write",
        lambda _state: (_ for _ in ()).throw(OSError("state write failed")),
    )

    with pytest.raises(OSError, match="state write failed"):
        coordinator.fail(operation.operation_id, "cleanup failed")

    assert coordinator.writes_blocked() is True
    assert coordinator.operation().status == "requested"


def test_coordinator_reopens_gate_after_reversible_worker_drain_failure(
    tmp_path: Path,
) -> None:
    plan_path = tmp_path / "plan.json"
    write_plan(plan_path)
    loaded = load_system_upgrade_plan(plan_path)
    assert loaded is not None
    state_path = tmp_path / "state" / "system-upgrade.json"
    coordinator = SystemUpgradeCoordinator(state_path, plan_path, "old-instance")
    operation = coordinator.begin(
        loaded,
        source_ip="127.0.0.1",
        old_worker_generation="1" * 32,
        runner=lambda _operation_id: None,
    )
    coordinator.update(operation.operation_id, worker_drain_started=True)
    coordinator.fail(operation.operation_id, "freeze failed")

    recovered = SystemUpgradeCoordinator(state_path, plan_path, "new-instance")

    assert recovered.writes_blocked() is False
    restarted = recovered.begin(
        loaded,
        source_ip="127.0.0.1",
        old_worker_generation="2" * 32,
        runner=lambda _operation_id: None,
    )
    assert restarted.operation_id != operation.operation_id


def test_completed_runtime_recovery_can_be_started_again(tmp_path: Path) -> None:
    coordinator = SystemUpgradeCoordinator(
        tmp_path / "state" / "system-upgrade.json",
        tmp_path / "missing-plan.json",
        "old-instance",
    )
    plan = runtime_recovery_plan()
    completed = coordinator.begin(
        plan,
        source_ip="127.0.0.1",
        old_worker_generation="a" * 32,
        runner=lambda _operation_id: None,
    )
    coordinator.succeed(completed.operation_id)

    status = coordinator.status_data(None, session_count=0)
    repeated = coordinator.begin(
        plan,
        source_ip="127.0.0.1",
        old_worker_generation="b" * 32,
        runner=lambda _operation_id: None,
    )

    assert status.state == "available"
    assert status.can_start is True
    assert status.plan is not None
    assert status.plan.plan_id == "runtime-recovery"
    assert status.operation is not None
    assert status.operation.operation_id == completed.operation_id
    assert status.operation.status == "succeeded"
    assert repeated.operation_id != completed.operation_id


def test_prepare_restart_removes_actual_and_declared_worker_protocol_state(
    settings: Settings,
    tmp_path: Path,
) -> None:
    plan_path = tmp_path / "plan.json"
    write_plan(plan_path)
    loaded = load_system_upgrade_plan(plan_path)
    assert loaded is not None
    coordinator = SystemUpgradeCoordinator(
        settings.codex_pty.data_file.with_name("system-upgrade.json"),
        plan_path,
        "old",
    )
    operation = coordinator.begin(
        loaded,
        source_ip="127.0.0.1",
        old_worker_generation="d" * 32,
        old_worker_protocol=PROTOCOL_VERSION,
        runner=lambda _operation_id: None,
    )
    coordinator.mark_started(operation.operation_id)
    coordinator.update(
        operation.operation_id,
        stage="restarting_services",
        destructive_started=True,
        restart_launch_state="launched",
        restart_process_id=os.getpid(),
    )
    source_paths = (
        worker_tasks_dir(settings, PROTOCOL_VERSION - 1),
        worker_tombstones_dir(settings, PROTOCOL_VERSION - 1),
        worker_leases_dir(settings, PROTOCOL_VERSION - 1),
    )
    for path in source_paths:
        path.mkdir(parents=True)
        os.chmod(path, 0o700)
        (path / "record.json").write_text("{}", encoding="utf-8")
    actual_paths = (
        worker_tasks_dir(settings, PROTOCOL_VERSION),
        worker_tombstones_dir(settings, PROTOCOL_VERSION),
        worker_leases_dir(settings, PROTOCOL_VERSION),
    )
    unrelated_paths = (
        worker_tasks_dir(settings, PROTOCOL_VERSION + 1),
        worker_tombstones_dir(settings, PROTOCOL_VERSION + 1),
        worker_leases_dir(settings, PROTOCOL_VERSION + 1),
    )
    for path in (*actual_paths, *unrelated_paths):
        path.mkdir(parents=True)
        os.chmod(path, 0o700)
    for path in (
        settings.codex_pty.data_file,
        settings.codex_pty.data_file.with_name("ai-sessions.json"),
    ):
        path.write_text("[]", encoding="utf-8")
        path.chmod(0o600)
    for path in (
        settings.codex_pty.runtime_dir / "hooks",
        settings.codex_pty.runtime_dir / "restart-requests",
    ):
        path.mkdir(parents=True)
        path.chmod(0o700)
        (path / "record.json").write_text("{}", encoding="utf-8")

    with patch("app.system_upgrade_cli.load_settings", return_value=settings):
        prepare_restart(operation.operation_id)

    assert all(not path.exists() for path in source_paths)
    assert all(not path.exists() for path in actual_paths)
    assert all(path.is_dir() for path in unrelated_paths)
    assert not settings.codex_pty.data_file.exists()
    assert not settings.codex_pty.data_file.with_name("ai-sessions.json").exists()
    assert not (settings.codex_pty.runtime_dir / "hooks").exists()
    assert not (settings.codex_pty.runtime_dir / "restart-requests").exists()


def test_system_upgrade_restart_uses_fixed_linux_services(
    settings: Settings,
    tmp_path: Path,
) -> None:
    plan_path = tmp_path / "plan.json"
    write_plan(plan_path)
    loaded = load_system_upgrade_plan(plan_path)
    assert loaded is not None
    coordinator = SystemUpgradeCoordinator(
        settings.codex_pty.data_file.with_name("system-upgrade.json"),
        plan_path,
        "old",
    )
    operation = coordinator.begin(
        loaded,
        source_ip="127.0.0.1",
        old_worker_generation="f" * 32,
        runner=lambda _operation_id: None,
    )
    coordinator.mark_started(operation.operation_id)
    coordinator.update(
        operation.operation_id,
        stage="launching_services",
        destructive_started=True,
        restart_launch_state="launching",
    )
    for path in (
        worker_tasks_dir(settings, PROTOCOL_VERSION),
        worker_tombstones_dir(settings, PROTOCOL_VERSION),
        worker_leases_dir(settings, PROTOCOL_VERSION),
    ):
        path.mkdir(parents=True)
        os.chmod(path, 0o700)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    shutil.copytree(PROJECT_ROOT / "scripts", workspace / "scripts")
    shutil.copytree(PROJECT_ROOT / "app", workspace / "app")
    (workspace / ".venv").symlink_to(PROJECT_ROOT / ".venv", target_is_directory=True)
    (workspace / "main.py").symlink_to(PROJECT_ROOT / "main.py")
    (workspace / "config").mkdir()
    config_path = workspace / "config" / "settings.local.yaml"
    config_path.write_text(
        yaml.safe_dump(
            {
                "app": {"name": "Hub", "version": "0.1.0"},
                "node": {"id": "test", "name": "Test", "type": "ubuntu"},
                    "server": {"tailnet_host": None, "port": 8080},
                "security": {"allow_tailscale": False},
                "codex_pty": {
                    "workspace": str(settings.codex_pty.workspace),
                    "data_file": str(settings.codex_pty.data_file),
                    "runtime_dir": str(settings.codex_pty.runtime_dir),
                },
            }
        ),
        encoding="utf-8",
    )
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    calls = tmp_path / "systemctl-calls.txt"
    systemctl = fake_bin / "systemctl"
    systemctl.write_text(
        "#!/usr/bin/env bash\nprintf '%s\\n' \"$*\" >> \"$CHUB_TEST_CALLS\"\n",
        encoding="utf-8",
    )
    os.chmod(systemctl, 0o700)
    environment = os.environ.copy()
    environment.pop("CHUB_ACTIVITY_SOURCE", None)
    environment.update(
        {
            "HOME": str(tmp_path / "home"),
            "PATH": f"{fake_bin}:/usr/bin:/bin",
            "CHUB_TEST_PLATFORM": "Linux",
            "CHUB_TEST_CALLS": str(calls),
            "CHUB_SYSTEMD_USER_DIR": str(tmp_path / "systemd"),
            "CHUB_SERVICE_LOG_DIR": str(tmp_path / "logs"),
        }
    )

    launch = subprocess.run(
        [str(workspace / "scripts" / "chub-system-upgrade-start"), operation.operation_id],
        cwd=workspace,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )
    assert launch.returncode == 0, launch.stderr or launch.stdout

    process = subprocess.Popen(
        [
                str(workspace / "scripts" / "chub-system-upgrade-restart"),
                operation.operation_id,
            ],
            cwd=workspace,
        env=environment,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    coordinator.update(
        operation.operation_id,
        stage="restarting_services",
        restart_launch_state="launched",
        restart_process_id=process.pid,
    )
    stdout, stderr = process.communicate(timeout=10)

    assert process.returncode == 0, stderr or stdout
    assert calls.read_text(encoding="utf-8").splitlines() == [
        "--user --no-block start chub-system-upgrade.service",
        "--user stop chub.service",
        "--user stop chub-quick-worker.service",
        "--user stop chub-debug-chrome.service",
        "--user daemon-reload",
        "--user enable chub.service",
        "--user enable chub-quick-worker.service",
        "--user enable chub-debug-chrome.service",
        "--user daemon-reload",
        "--user enable chub-debug-chrome.service",
        "--user restart chub-debug-chrome.service",
        "--user is-active --quiet chub-debug-chrome.service",
        "--user restart chub-quick-worker.service",
        "--user --no-block restart chub.service",
    ]


@pytest.mark.anyio
async def test_system_upgrade_status_allows_runtime_recovery_without_plan(
    settings: Settings,
    tmp_path: Path,
) -> None:
    app = create_app(settings)
    app.state.system_upgrade.plan_path = tmp_path / "system-upgrade.json"
    transport = httpx.ASGITransport(app=app)

    app.state.quick_interactions._recovery_ready = True
    app.state.system_upgrade_restart_readiness = lambda: None
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://test",
        headers={"Authorization": f"Bearer {TOKEN}"},
    ) as client:
        response = await client.get("/api/maintenance/system-upgrade")

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["state"] == "available"
    assert data["can_start"] is True
    assert data["plan"]["plan_id"] == "runtime-recovery"
    assert data["plan"]["session_count"] == 0


@pytest.mark.anyio
async def test_invalid_upgrade_plan_falls_back_to_runtime_recovery(
    settings: Settings,
    tmp_path: Path,
) -> None:
    app = create_app(settings)
    plan_path = tmp_path / "system-upgrade.json"
    plan_path.write_text("not-json", encoding="utf-8")
    plan_path.chmod(0o600)
    app.state.system_upgrade.plan_path = plan_path
    app.state.quick_interactions._recovery_ready = True
    app.state.system_upgrade_restart_readiness = lambda: None
    app.state.run_system_upgrade = lambda _operation_id: None
    transport = httpx.ASGITransport(app=app)

    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://test",
        headers={"Authorization": f"Bearer {TOKEN}"},
    ) as client:
        preview = await client.get("/api/maintenance/system-upgrade")
        preview_data = preview.json()["data"]
        started = await client.post(
            "/api/maintenance/system-upgrade",
            json={"fingerprint": preview_data["plan"]["fingerprint"]},
        )

    assert preview.status_code == 200
    assert preview_data["state"] == "available"
    assert preview_data["can_start"] is True
    assert preview_data["plan"]["plan_id"] == "runtime-recovery"
    assert preview_data["message"] == "升级方案不可用，当前仅可执行运行态恢复。"
    assert started.status_code == 200
    assert started.json()["data"]["state"] == "preparing"


@pytest.mark.anyio
async def test_system_upgrade_does_not_gate_on_web_or_worker_status(
    settings: Settings,
    tmp_path: Path,
) -> None:
    app = create_app(settings)
    plan_path = tmp_path / "system-upgrade.json"
    write_plan(plan_path)
    loaded = load_system_upgrade_plan(plan_path)
    assert loaded is not None
    app.state.system_upgrade.plan_path = plan_path
    app.state.system_upgrade_restart_readiness = lambda: None
    app.state.run_system_upgrade = lambda _operation_id: None
    app.state.quick_interactions._recovery_ready = False
    transport = httpx.ASGITransport(app=app)

    with (
        patch.object(
            app.state.codex_pty_manager,
            "system_upgrade_sessions",
            side_effect=OSError("unavailable"),
        ),
        patch.object(
            app.state.codex_pty_manager,
            "available",
            return_value=False,
        ) as available,
        patch(
            "app.api.maintenance.read_health",
            create=True,
            side_effect=AssertionError("upgrade must not inspect Worker health"),
        ) as read_worker_health,
    ):
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://test",
            headers={"Authorization": f"Bearer {TOKEN}"},
        ) as client:
            preview = await client.get("/api/maintenance/system-upgrade")
            started = await client.post(
                "/api/maintenance/system-upgrade",
                json={"fingerprint": loaded.fingerprint},
            )

    assert preview.status_code == 200
    assert preview.json()["data"]["state"] == "available"
    assert preview.json()["data"]["can_start"] is True
    assert started.status_code == 200
    assert started.json()["data"]["state"] == "preparing"
    available.assert_not_called()
    read_worker_health.assert_not_called()
    operation = app.state.system_upgrade.operation()
    assert operation is not None
    assert operation.old_worker_generation is None


@pytest.mark.anyio
async def test_failed_upgrade_does_not_block_maintenance_restarts(
    settings: Settings,
) -> None:
    app = create_app(settings)
    app.state.system_upgrade._writes_blocked = True
    openclaw = MagicMock()
    openclaw.control.return_value = OpenClawManager._parse_status(
        {
            "service": {
                "loaded": True,
                "command": {"sourcePath": "/tmp/openclaw.plist"},
                "runtime": {"status": "running"},
            },
            "config": {"cli": {"exists": True, "valid": True}},
            "gateway": {"bindMode": "loopback", "port": 18789},
            "port": {"status": "listening"},
            "rpc": {"ok": True},
        }
    )
    app.state.openclaw_manager = openclaw
    transport = httpx.ASGITransport(app=app)

    with (
        patch("app.api.maintenance.launch_restart_process") as launch_restart,
        patch("app.api.maintenance.monitor_restart_process"),
    ):
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://test",
            headers={"Authorization": f"Bearer {TOKEN}"},
        ) as client:
            restart = await client.post("/api/maintenance/restart")
            clawbot = await client.post("/api/openclaw/restart")
            session_read = await client.get("/api/codex/sessions")
            health = await client.get("/api/health")

    assert restart.status_code == 200
    launch_restart.assert_called_once()
    assert clawbot.status_code == 200
    openclaw.control.assert_called_once_with("restart")
    assert session_read.status_code == 200
    assert health.status_code == 200


@pytest.mark.anyio
async def test_active_upgrade_still_blocks_maintenance_restarts(
    settings: Settings,
) -> None:
    app = create_app(settings)
    app.state.system_upgrade.in_progress = lambda: True
    transport = httpx.ASGITransport(app=app)

    with patch("app.api.maintenance.launch_restart_process") as launch_restart:
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://test",
            headers={"Authorization": f"Bearer {TOKEN}"},
        ) as client:
            response = await client.post("/api/maintenance/restart")

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "system_upgrade_in_progress"
    launch_restart.assert_not_called()


@pytest.mark.anyio
async def test_failed_upgrade_does_not_preempt_maintenance_request(
    settings: Settings,
) -> None:
    app = create_app(settings)
    app.state.system_upgrade._writes_blocked = True
    transport = httpx.ASGITransport(app=app)

    with (
        patch("app.api.maintenance.launch_restart_process") as launch_restart,
        patch("app.api.maintenance.monitor_restart_process"),
    ):
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://test",
        ) as client:
            response = await client.post("/api/maintenance/restart")

    assert response.status_code == 200
    launch_restart.assert_called_once()


@pytest.mark.anyio
async def test_upgrade_rejects_changed_plan_fingerprint(
    settings: Settings,
    tmp_path: Path,
) -> None:
    app = create_app(settings)
    plan_path = tmp_path / "system-upgrade.json"
    write_plan(plan_path)
    app.state.system_upgrade.plan_path = plan_path
    transport = httpx.ASGITransport(app=app)

    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://test",
        headers={"Authorization": f"Bearer {TOKEN}"},
    ) as client:
        response = await client.post(
            "/api/maintenance/system-upgrade",
            json={"fingerprint": "0" * 64},
        )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "system_upgrade_plan_changed"


@pytest.mark.anyio
async def test_upgrade_preview_allows_recovery_from_corrupt_session_store(
    settings: Settings,
    tmp_path: Path,
) -> None:
    app = create_app(settings)
    plan_path = tmp_path / "system-upgrade.json"
    write_plan(plan_path)
    app.state.system_upgrade.plan_path = plan_path
    app.state.system_upgrade_restart_readiness = lambda: None
    app.state.quick_interactions._recovery_ready = True
    ai_session_path = settings.codex_pty.data_file.with_name("ai-sessions.json")
    ai_session_path.write_text("not-json", encoding="utf-8")
    ai_session_path.chmod(0o600)
    transport = httpx.ASGITransport(app=app)

    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://test",
        headers={"Authorization": f"Bearer {TOKEN}"},
    ) as client:
        response = await client.get("/api/maintenance/system-upgrade")

    assert response.status_code == 200
    assert response.json()["data"]["state"] == "available"
    assert response.json()["data"]["can_start"] is True


@pytest.mark.anyio
async def test_upgrade_preview_rejects_unsafe_session_runtime_path(
    settings: Settings,
    tmp_path: Path,
) -> None:
    app = create_app(settings)
    app.state.system_upgrade.plan_path = tmp_path / "system-upgrade.json"
    app.state.system_upgrade_restart_readiness = lambda: None
    app.state.quick_interactions._recovery_ready = True
    ai_session_path = settings.codex_pty.data_file.with_name("ai-sessions.json")
    ai_session_path.symlink_to(tmp_path / "unexpected-session-state")
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://test",
        headers={"Authorization": f"Bearer {TOKEN}"},
    ) as client:
        response = await client.get("/api/maintenance/system-upgrade")

    assert response.status_code == 200
    assert response.json()["data"]["state"] == "blocked"
    assert response.json()["data"]["can_start"] is False
    assert "类型、所有者或权限不安全" in response.json()["data"]["message"]


@pytest.mark.anyio
async def test_valid_plan_is_previewed_and_started_by_fingerprint(
    settings: Settings,
    tmp_path: Path,
) -> None:
    app = create_app(settings)
    plan_path = tmp_path / "system-upgrade.json"
    write_plan(plan_path)
    loaded = load_system_upgrade_plan(plan_path)
    assert loaded is not None
    app.state.system_upgrade.plan_path = plan_path
    app.state.quick_interactions._recovery_ready = True
    app.state.run_system_upgrade = lambda _operation_id: None
    app.state.system_upgrade_restart_readiness = lambda: None
    transport = httpx.ASGITransport(app=app)

    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://test",
        headers={"Authorization": f"Bearer {TOKEN}"},
    ) as client:
        preview = await client.get("/api/maintenance/system-upgrade")
        started = await client.post(
            "/api/maintenance/system-upgrade",
            json={"fingerprint": loaded.fingerprint},
        )

    assert preview.status_code == 200
    assert preview.json()["data"]["state"] == "available"
    assert preview.json()["data"]["can_start"] is True
    assert preview.json()["data"]["resume"] is True
    assert preview.json()["data"]["plan"]["source_worker_protocol"] == PROTOCOL_VERSION - 1
    assert preview.json()["data"]["plan"]["session_labels"] == []
    assert started.status_code == 200
    assert started.json()["data"]["state"] == "preparing"
    assert app.state.system_upgrade.writes_blocked() is True
    operation = app.state.system_upgrade.operation()
    assert operation is not None
    assert operation.old_worker_protocol is None


@pytest.mark.anyio
async def test_destructive_upgrade_failure_can_continue_with_same_operation(
    settings: Settings,
    tmp_path: Path,
) -> None:
    app = create_app(settings)
    plan_path = tmp_path / "plan" / "system-upgrade.json"
    plan_path.parent.mkdir()
    write_plan(plan_path)
    loaded = load_system_upgrade_plan(plan_path)
    assert loaded is not None
    app.state.system_upgrade.plan_path = plan_path
    operation = app.state.system_upgrade.begin(
        loaded,
        source_ip="127.0.0.1",
        old_worker_generation="a" * 32,
        old_worker_protocol=PROTOCOL_VERSION,
        runner=lambda _operation_id: None,
    )
    app.state.system_upgrade.mark_started(operation.operation_id)
    app.state.system_upgrade.update(
        operation.operation_id,
        stage="cleaning_state",
        destructive_started=True,
    )
    app.state.system_upgrade.fail(operation.operation_id, "cleanup interrupted")
    failed_operation = app.state.system_upgrade.operation()
    assert failed_operation is not None
    assert failed_operation.failed_stage == "cleaning_state"
    assert failed_operation.fingerprint == loaded.fingerprint
    assert app.state.system_upgrade.status_data(
        loaded,
        session_count=0,
    ).can_start is True
    app.state.run_system_upgrade = lambda _operation_id: None
    transport = httpx.ASGITransport(app=app)

    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://test",
        headers={"Authorization": f"Bearer {TOKEN}"},
    ) as client:
        preview = await client.get("/api/maintenance/system-upgrade")
        resumed = await client.post(
            "/api/maintenance/system-upgrade",
            json={"fingerprint": loaded.fingerprint},
        )

    assert preview.status_code == 200
    assert preview.json()["data"]["state"] == "failed"
    assert preview.json()["data"]["can_start"] is True
    assert preview.json()["data"]["resume"] is True
    assert preview.json()["data"]["plan"]["fingerprint"] == loaded.fingerprint
    assert resumed.status_code == 200
    assert resumed.json()["data"]["state"] == "draining"
    continued = app.state.system_upgrade.operation()
    assert continued is not None
    assert continued.operation_id == operation.operation_id
    assert continued.status == "started"
    assert continued.stage == "draining_worker"
    assert app.state.system_upgrade.writes_blocked() is True


@pytest.mark.anyio
async def test_changed_recovery_plan_can_continue_failed_cleanup(
    settings: Settings,
    tmp_path: Path,
) -> None:
    app = create_app(settings)
    plan_path = tmp_path / "plan" / "system-upgrade.json"
    plan_path.parent.mkdir()
    old_plan = runtime_recovery_plan().plan.model_dump(mode="json")
    old_plan["summary"] = "旧恢复目标"
    plan_path.write_text(json.dumps(old_plan), encoding="utf-8")
    plan_path.chmod(0o600)
    loaded = load_system_upgrade_plan(plan_path)
    assert loaded is not None
    app.state.system_upgrade.plan_path = plan_path
    operation = app.state.system_upgrade.begin(
        loaded,
        source_ip="127.0.0.1",
        old_worker_generation="a" * 32,
        runner=lambda _operation_id: None,
    )
    app.state.system_upgrade.mark_started(operation.operation_id)
    app.state.system_upgrade.update(
        operation.operation_id,
        stage="cleaning_state",
        destructive_started=True,
    )
    app.state.system_upgrade.fail(operation.operation_id, "旧恢复目标已过期")
    current = runtime_recovery_plan()
    plan_path.write_text(current.plan.model_dump_json(), encoding="utf-8")
    plan_path.chmod(0o600)
    app.state.quick_interactions._recovery_ready = True
    app.state.system_upgrade_restart_readiness = lambda: None
    app.state.run_system_upgrade = lambda _operation_id: None
    transport = httpx.ASGITransport(app=app)

    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://test",
        headers={"Authorization": f"Bearer {TOKEN}"},
    ) as client:
        preview = await client.get("/api/maintenance/system-upgrade")
        resumed = await client.post(
            "/api/maintenance/system-upgrade",
            json={"fingerprint": current.fingerprint},
        )

    assert preview.status_code == 200
    assert preview.json()["data"]["can_start"] is True
    assert preview.json()["data"]["plan"]["fingerprint"] == current.fingerprint
    assert resumed.status_code == 200
    assert resumed.json()["data"]["state"] == "draining"


def test_weixin_system_upgrade_uses_application_upgrade_coordinator(
    settings: Settings,
    tmp_path: Path,
) -> None:
    settings.openclaw.weixin_chub_mode.enabled = True
    app = create_app(settings)
    plan_path = tmp_path / "system-upgrade.json"
    write_plan(plan_path)
    app.state.system_upgrade.plan_path = plan_path
    app.state.quick_interactions._recovery_ready = True
    app.state.run_system_upgrade = lambda _operation_id: None
    app.state.system_upgrade_restart_readiness = lambda: None
    result = app.state.weixin_chub_mode.dispatch(
        message_id="weixin-system-upgrade",
        prompt="upgrade",
        message_type="text",
        correlation_id="upgrade-request",
        source_ip="100.64.0.21",
        delivery_route=QuickInteractionWeixinRoute(
            account_id="weixin-account",
            recipient="owner@im.wechat",
        ),
    )

    assert result.message == (
        "Upgrade: Started. The final result will be sent when completed."
    )
    operation = app.state.system_upgrade.operation()
    assert operation is not None
    assert operation.source_ip == "100.64.0.21"
