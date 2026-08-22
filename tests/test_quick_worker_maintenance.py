import asyncio
import os
from pathlib import Path
import stat
import threading
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from app.application import create_app
from app.codex.models import utc_now
from app.core.config import Settings
from app.core.response import ApiError
from app.quick_worker import PROTOCOL_VERSION
from app.services.quick_worker_maintenance import (
    QuickWorkerReloadCoordinator,
    QuickWorkerReloadState,
    inspect_quick_worker,
    launch_quick_worker_reload_process,
)


AUTHORIZATION = {
    "Authorization": "Bearer test-token-that-is-long-enough-for-tests"
}


def worker_health(
    *,
    protocol_version: int = PROTOCOL_VERSION,
    status: str = "ready",
    generation: str = "a" * 32,
    active_tasks: int = 0,
    queued_tasks: int = 0,
) -> dict[str, object]:
    return {
        "success": True,
        "data": {
            "protocol_version": protocol_version,
            "status": status,
            "generation": generation,
            "active_tasks": active_tasks,
            "queued_tasks": queued_tasks,
            "uncertain_tasks": 0,
            "corrupt_tasks": 0,
            "available_runtime_ids": ["codex"],
        },
    }


class WaitingProcess:
    pid = os.getpid()

    def __init__(self, result: int = 0) -> None:
        self.result = result
        self.release = threading.Event()

    def wait(self) -> int:
        assert self.release.wait(2)
        return self.result


def test_reload_process_uses_only_fixed_command() -> None:
    command = Path("/fixed/scripts/chub")
    with patch("app.services.quick_worker_maintenance.subprocess.Popen") as popen:
        launch_quick_worker_reload_process(command)

    assert popen.call_args.args[0] == [str(command), "worker-reload"]
    assert popen.call_args.kwargs["start_new_session"] is True
    assert popen.call_args.kwargs["env"][
        "CHUB_WORKER_RELOAD_EXTERNAL_LOGGING"
    ] == "1"


def test_corrupt_reload_state_fails_closed(settings: Settings) -> None:
    state_path = settings.codex_pty.data_file.with_name(
        "quick-worker-maintenance.json"
    )
    state_path.write_text("{not-json", encoding="utf-8")
    coordinator = QuickWorkerReloadCoordinator(
        state_path,
        settings.codex_pty.data_file.parent / "chub",
    )

    assert coordinator.maintenance_available() is False
    with pytest.raises(ApiError) as raised:
        coordinator.begin("a" * 32, "127.0.0.1")
    assert raised.value.code == "quick_worker_reload_state_unavailable"


def test_reload_state_write_failure_does_not_launch(settings: Settings) -> None:
    coordinator = QuickWorkerReloadCoordinator(
        settings.codex_pty.data_file.with_name("quick-worker-maintenance.json"),
        settings.codex_pty.data_file.parent / "chub",
    )
    with (
        patch.object(coordinator, "_write", side_effect=OSError("read only")),
        patch(
            "app.services.quick_worker_maintenance.launch_quick_worker_reload_process"
        ) as launch,
        pytest.raises(ApiError) as raised,
    ):
        coordinator.begin("a" * 32, "127.0.0.1")

    assert raised.value.code == "quick_worker_reload_state_unavailable"
    assert coordinator.maintenance_available() is False
    launch.assert_not_called()


@pytest.mark.anyio
async def test_quick_worker_status_requires_trusted_network(
    settings: Settings,
) -> None:
    transport = httpx.ASGITransport(
        app=create_app(settings),
        client=("192.0.2.1", 12345),
    )
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/api/maintenance/quick-worker")

    assert response.status_code == 403


@pytest.mark.anyio
async def test_quick_worker_restart_is_independent_of_web_recovery(
    settings: Settings,
) -> None:
    app = create_app(settings)
    app.state.quick_interactions._recovery_ready = False
    transport = httpx.ASGITransport(app=app)

    with patch(
        "app.services.quick_worker_maintenance.read_health",
        new=AsyncMock(return_value=worker_health()),
    ):
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://test",
            headers=AUTHORIZATION,
        ) as client:
            recovering = await client.get("/api/maintenance/quick-worker")
            app.state.quick_interactions._recovery_ready = True
            ready = await client.get("/api/maintenance/quick-worker")

    assert recovering.status_code == 200
    assert recovering.json()["data"]["state"] == "recovering"
    assert recovering.json()["data"]["can_restart"] is True
    assert ready.json()["data"]["state"] == "ready"
    assert ready.json()["data"]["can_restart"] is True
    assert ready.json()["data"]["upgrade_required"] is False
    assert "generation" not in ready.json()["data"]
    assert "process_id" not in ready.json()["data"]


@pytest.mark.anyio
async def test_quick_worker_status_is_not_blocked_by_pending_system_upgrade(
    settings: Settings,
) -> None:
    app = create_app(settings)
    app.state.quick_interactions._recovery_ready = True
    app.state.system_upgrade.plan = lambda: SimpleNamespace(
        plan=SimpleNamespace(
            source_worker_protocol=PROTOCOL_VERSION - 1,
            target_worker_protocol=PROTOCOL_VERSION,
        )
    )
    transport = httpx.ASGITransport(app=app)

    with (
        patch(
            "app.services.quick_worker_maintenance.read_health",
            new=AsyncMock(return_value=worker_health()),
        ),
    ):
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://test",
            headers=AUTHORIZATION,
        ) as client:
            response = await client.get("/api/maintenance/quick-worker")

    assert response.status_code == 200
    assert response.json()["data"]["state"] == "ready"
    assert response.json()["data"]["can_restart"] is True
    assert response.json()["data"]["upgrade_required"] is False


@pytest.mark.anyio
async def test_quick_worker_restart_allows_idle_incompatible_protocol(
    settings: Settings,
) -> None:
    app = create_app(settings)
    app.state.quick_interactions._recovery_ready = False
    process = WaitingProcess()
    transport = httpx.ASGITransport(app=app)
    health = worker_health(protocol_version=PROTOCOL_VERSION - 1)

    with (
        patch(
            "app.services.quick_worker_maintenance.read_health",
            new=AsyncMock(return_value=health),
        ),
        patch(
            "app.services.quick_worker_maintenance.launch_quick_worker_reload_process",
            return_value=process,
        ) as launch,
    ):
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://test",
            headers=AUTHORIZATION,
        ) as client:
            response = await client.post("/api/maintenance/quick-worker/restart")
        process.release.set()

    assert response.status_code == 200
    assert response.json()["data"]["state"] == "restarting"
    launch.assert_called_once_with(app.state.quick_worker_maintenance.command)


@pytest.mark.anyio
async def test_quick_worker_restart_allows_busy_incompatible_worker(
    settings: Settings,
) -> None:
    coordinator = QuickWorkerReloadCoordinator(
        settings.codex_pty.data_file.with_name("quick-worker-maintenance.json"),
        settings.codex_pty.data_file.parent / "chub",
    )
    health = worker_health(
        protocol_version=PROTOCOL_VERSION - 1,
        active_tasks=1,
    )

    with patch(
        "app.services.quick_worker_maintenance.read_health",
        new=AsyncMock(return_value=health),
    ):
        inspection = await inspect_quick_worker(settings, False, coordinator)

    assert inspection.data.state == "incompatible"
    assert inspection.data.can_restart is True


@pytest.mark.anyio
async def test_quick_worker_restart_allows_busy_worker(
    settings: Settings,
) -> None:
    app = create_app(settings)
    app.state.quick_interactions._recovery_ready = True
    transport = httpx.ASGITransport(app=app)

    with (
        patch(
            "app.services.quick_worker_maintenance.read_health",
            new=AsyncMock(return_value=worker_health(active_tasks=1)),
        ),
        patch(
            "app.services.quick_worker_maintenance.launch_quick_worker_reload_process",
            return_value=WaitingProcess(),
        ) as launch,
    ):
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://test",
            headers=AUTHORIZATION,
        ) as client:
            response = await client.post("/api/maintenance/quick-worker/restart")
        launch.return_value.release.set()

    assert response.status_code == 200
    assert response.json()["data"]["state"] == "restarting"
    launch.assert_called_once_with(app.state.quick_worker_maintenance.command)


@pytest.mark.anyio
async def test_quick_worker_restart_uses_fixed_controlled_command(
    settings: Settings,
) -> None:
    app = create_app(settings)
    app.state.quick_interactions._recovery_ready = True
    process = WaitingProcess()
    transport = httpx.ASGITransport(app=app)

    with (
        patch(
            "app.services.quick_worker_maintenance.read_health",
            new=AsyncMock(return_value=worker_health()),
        ),
        patch(
            "app.services.quick_worker_maintenance.launch_quick_worker_reload_process",
            return_value=process,
        ) as launch,
        patch("app.services.quick_worker_maintenance.write_operation") as operation_log,
    ):
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://test",
            headers=AUTHORIZATION,
        ) as client:
            response = await client.post("/api/maintenance/quick-worker/restart")

        assert response.status_code == 200
        assert response.json()["data"]["state"] == "restarting"
        assert response.json()["data"]["can_restart"] is False
        assert response.json()["data"]["operation"]["operation_id"].startswith(
            "worker-reload:"
        )
        launch.assert_called_once_with(app.state.quick_worker_maintenance.command)
        assert [call.kwargs["status"] for call in operation_log.call_args_list[:2]] == [
            "requested",
            "started",
        ]
        process.release.set()
        for _attempt in range(20):
            if app.state.quick_worker_maintenance.operation().status == "succeeded":
                break
            threading.Event().wait(0.01)

    assert app.state.quick_worker_maintenance.operation().status == "succeeded"
    state_path = settings.codex_pty.data_file.with_name(
        "quick-worker-maintenance.json"
    )
    assert stat.S_IMODE(state_path.stat().st_mode) == 0o600


@pytest.mark.anyio
async def test_quick_worker_restart_remains_available_after_failed_upgrade(
    settings: Settings,
) -> None:
    app = create_app(settings)
    app.state.quick_interactions._recovery_ready = True
    app.state.system_upgrade._writes_blocked = True
    process = WaitingProcess()
    transport = httpx.ASGITransport(app=app)

    with (
        patch(
            "app.services.quick_worker_maintenance.read_health",
            new=AsyncMock(return_value=worker_health()),
        ),
        patch(
            "app.services.quick_worker_maintenance.launch_quick_worker_reload_process",
            return_value=process,
        ) as launch,
    ):
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://test",
            headers=AUTHORIZATION,
        ) as client:
            response = await client.post("/api/maintenance/quick-worker/restart")
        process.release.set()

    assert response.status_code == 200
    assert response.json()["data"]["state"] == "restarting"
    launch.assert_called_once_with(app.state.quick_worker_maintenance.command)


@pytest.mark.anyio
async def test_quick_worker_restart_allows_unavailable_worker(
    settings: Settings,
) -> None:
    app = create_app(settings)
    app.state.quick_interactions._recovery_ready = True
    process = WaitingProcess()
    transport = httpx.ASGITransport(app=app)

    with (
        patch(
            "app.services.quick_worker_maintenance.read_health",
            new=AsyncMock(side_effect=OSError("worker unavailable")),
        ),
        patch(
            "app.services.quick_worker_maintenance.launch_quick_worker_reload_process",
            return_value=process,
        ) as launch,
    ):
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://test",
            headers=AUTHORIZATION,
        ) as client:
            response = await client.post("/api/maintenance/quick-worker/restart")
        process.release.set()

    assert response.status_code == 200
    assert response.json()["data"]["state"] == "restarting"
    launch.assert_called_once_with(app.state.quick_worker_maintenance.command)


@pytest.mark.anyio
async def test_quick_worker_restart_allows_pending_chub_restart(
    settings: Settings,
) -> None:
    app = create_app(settings)
    app.state.quick_interactions._recovery_ready = True
    app.state.deferred_restart.request(
        operation_id="pending-restart",
        task_id="task-1",
        source_ip="127.0.0.1",
    )
    transport = httpx.ASGITransport(app=app)

    process = WaitingProcess()
    with (
        patch(
            "app.services.quick_worker_maintenance.read_health",
            new=AsyncMock(return_value=worker_health()),
        ),
        patch(
            "app.services.quick_worker_maintenance.launch_quick_worker_reload_process",
            return_value=process,
        ) as launch_worker,
    ):
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://test",
            headers=AUTHORIZATION,
        ) as client:
            response = await client.post("/api/maintenance/quick-worker/restart")
        process.release.set()

    assert response.status_code == 200
    launch_worker.assert_called_once()


@pytest.mark.anyio
async def test_concurrent_chub_and_worker_restarts_launch_independently(
    settings: Settings,
) -> None:
    app = create_app(settings)
    app.state.quick_interactions._recovery_ready = True
    chub_launched = asyncio.Event()
    event_loop = asyncio.get_running_loop()
    worker_process = WaitingProcess()
    transport = httpx.ASGITransport(app=app)

    async def wait_for_chub_launch(*_args, **_kwargs):
        await asyncio.wait_for(chub_launched.wait(), timeout=1)
        return worker_health()

    def launch_chub(_command):
        event_loop.call_soon_threadsafe(chub_launched.set)
        return object()

    with (
        patch(
            "app.services.quick_worker_maintenance.read_health",
            new=AsyncMock(side_effect=wait_for_chub_launch),
        ),
        patch(
            "app.services.quick_worker_maintenance.launch_quick_worker_reload_process",
            return_value=worker_process,
        ) as launch_worker,
        patch(
            "app.api.maintenance.launch_restart_process",
            side_effect=launch_chub,
        ) as launch_web,
        patch("app.api.maintenance.monitor_restart_process"),
    ):
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://test",
            headers=AUTHORIZATION,
        ) as client:
            worker_response, web_response = await asyncio.gather(
                client.post("/api/maintenance/quick-worker/restart"),
                client.post("/api/maintenance/restart"),
            )
        worker_process.release.set()

    assert web_response.status_code == 200
    assert worker_response.status_code == 200
    launch_web.assert_called_once()
    launch_worker.assert_called_once()


@pytest.mark.anyio
async def test_reload_reconciles_new_generation_after_web_restart(
    settings: Settings,
) -> None:
    state_path = settings.codex_pty.data_file.with_name(
        "quick-worker-maintenance.json"
    )
    command = settings.codex_pty.data_file.parent / "chub"
    command.touch(mode=0o700)
    process = WaitingProcess()
    with (
        patch(
            "app.services.quick_worker_maintenance.launch_quick_worker_reload_process",
            return_value=process,
        ),
        patch("app.services.quick_worker_maintenance.write_operation"),
    ):
        coordinator = QuickWorkerReloadCoordinator(state_path, command)
        assert coordinator.begin("a" * 32, "127.0.0.1") is True
        reloaded = QuickWorkerReloadCoordinator(state_path, command)
        reloaded.reconcile("b" * 32, True)

    process.release.set()
    assert reloaded.operation().status == "succeeded"


def test_reload_waits_for_pid_handoff_after_web_crash(settings: Settings) -> None:
    state_path = settings.codex_pty.data_file.with_name(
        "quick-worker-maintenance.json"
    )
    command = settings.codex_pty.data_file.parent / "chub"
    now = utc_now()
    coordinator = QuickWorkerReloadCoordinator(
        state_path,
        command,
        handoff_grace_seconds=60,
    )
    coordinator._write(
        QuickWorkerReloadState(
            operation_id="worker-reload:handoff",
            status="requested",
            old_generation="a" * 32,
            source_ip="127.0.0.1",
            requested_at=now,
            updated_at=now,
        )
    )
    reloaded = QuickWorkerReloadCoordinator(
        state_path,
        command,
        handoff_grace_seconds=60,
    )

    with patch("app.services.quick_worker_maintenance.write_operation"):
        reloaded.reconcile("a" * 32, True)
        assert reloaded.operation().status == "restarting"
        reloaded.reconcile("b" * 32, True)

    assert reloaded.operation().status == "succeeded"


def test_reload_monitor_start_failure_falls_back_to_reconciliation(
    settings: Settings,
) -> None:
    state_path = settings.codex_pty.data_file.with_name(
        "quick-worker-maintenance.json"
    )
    command = settings.codex_pty.data_file.parent / "chub"
    command.touch(mode=0o700)
    process = WaitingProcess()
    coordinator = QuickWorkerReloadCoordinator(state_path, command)

    with (
        patch(
            "app.services.quick_worker_maintenance.launch_quick_worker_reload_process",
            return_value=process,
        ),
        patch(
            "app.services.quick_worker_maintenance.threading.Thread.start",
            side_effect=RuntimeError,
        ),
        patch.object(
            coordinator,
            "_reload_process_is_running",
            return_value=True,
        ),
        patch("app.services.quick_worker_maintenance.write_operation"),
    ):
        assert coordinator.begin("a" * 32, "127.0.0.1") is True
        coordinator.reconcile("a" * 32, True)
        assert coordinator.operation().status == "restarting"
        coordinator.reconcile("b" * 32, True)

    assert coordinator.operation().status == "succeeded"


def test_reload_pid_handoff_expires_to_failed(settings: Settings) -> None:
    state_path = settings.codex_pty.data_file.with_name(
        "quick-worker-maintenance.json"
    )
    command = settings.codex_pty.data_file.parent / "chub"
    now = utc_now()
    writer = QuickWorkerReloadCoordinator(
        state_path,
        command,
        handoff_grace_seconds=0,
    )
    writer._write(
        QuickWorkerReloadState(
            operation_id="worker-reload:expired-handoff",
            status="requested",
            old_generation="a" * 32,
            source_ip="127.0.0.1",
            requested_at=now,
            updated_at=now,
        )
    )
    coordinator = QuickWorkerReloadCoordinator(
        state_path,
        command,
        handoff_grace_seconds=0,
    )

    with patch("app.services.quick_worker_maintenance.write_operation"):
        coordinator.reconcile("a" * 32, True)

    assert coordinator.operation().status == "failed"


@pytest.mark.anyio
async def test_inspection_allows_idle_incompatible_protocol(
    settings: Settings,
) -> None:
    coordinator = QuickWorkerReloadCoordinator(
        settings.codex_pty.data_file.with_name("quick-worker-maintenance.json"),
        settings.codex_pty.data_file.parent / "chub",
    )
    health = worker_health()
    health["data"]["protocol_version"] = PROTOCOL_VERSION - 1

    with patch(
        "app.services.quick_worker_maintenance.read_health",
        new=AsyncMock(return_value=health),
    ):
        inspection = await inspect_quick_worker(settings, True, coordinator)

    assert inspection.data.state == "incompatible"
    assert inspection.data.can_restart is True
