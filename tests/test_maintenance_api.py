import threading
from unittest.mock import ANY, patch

import httpx
import pytest

from app.application import create_app
from app.codex.models import QuickInteractionTask, utc_now
from app.core.config import Settings


@pytest.mark.anyio
async def test_restart_requires_authentication(settings: Settings) -> None:
    transport = httpx.ASGITransport(app=create_app(settings))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post("/api/maintenance/restart")

    assert response.status_code == 401


@pytest.mark.anyio
async def test_restart_uses_chub_service_command(settings: Settings) -> None:
    transport = httpx.ASGITransport(app=create_app(settings))
    with (
        patch("app.api.maintenance.PROJECT_ROOT") as project_root,
        patch("app.api.maintenance.launch_restart_process") as launch_restart,
        patch("app.api.maintenance.monitor_restart_process") as monitor_restart,
    ):
        command = project_root / "scripts" / "chub-web-restart"
        command.is_file.return_value = True
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://test",
            headers={"Authorization": "Bearer test-token-that-is-long-enough-for-tests"},
        ) as client:
            response = await client.post("/api/maintenance/restart")

    assert response.status_code == 200
    assert response.json()["data"] == {"status": "restarting"}
    launch_restart.assert_called_once_with(command)
    monitor_restart.assert_called_once_with(launch_restart.return_value, ANY)


@pytest.mark.anyio
async def test_restart_allows_active_quick_interaction(settings: Settings) -> None:
    app = create_app(settings)
    app.state.quick_interactions._active_task_ids.add("task-1")
    transport = httpx.ASGITransport(app=app)

    with (
        patch("app.api.maintenance.launch_restart_process") as launch_restart,
        patch("app.api.maintenance.monitor_restart_process"),
    ):
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://test",
            headers={"Authorization": "Bearer test-token-that-is-long-enough-for-tests"},
        ) as client:
            response = await client.post("/api/maintenance/restart")

    assert response.status_code == 200
    assert response.json()["data"] == {"status": "restarting"}
    launch_restart.assert_called_once()


@pytest.mark.anyio
async def test_restart_allows_active_translation(settings: Settings) -> None:
    app = create_app(settings)
    task = QuickInteractionTask(
        id="translation-1",
        session_id="translation-session",
        prompt="translate",
        kind="translation",
        status="running",
        created_at=utc_now(),
        updated_at=utc_now(),
    )
    app.state.quick_interactions._tasks[task.id] = task
    app.state.quick_interactions._active_task_ids.add(task.id)
    transport = httpx.ASGITransport(app=app)

    with (
        patch("app.api.maintenance.PROJECT_ROOT") as project_root,
        patch("app.api.maintenance.launch_restart_process") as launch_restart,
        patch("app.api.maintenance.monitor_restart_process"),
    ):
        command = project_root / "scripts" / "chub-web-restart"
        command.is_file.return_value = True
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://test",
            headers={"Authorization": "Bearer test-token-that-is-long-enough-for-tests"},
        ) as client:
            response = await client.post("/api/maintenance/restart")

    assert response.status_code == 200
    assert response.json()["data"] == {"status": "restarting"}
    launch_restart.assert_called_once()


@pytest.mark.anyio
async def test_restart_immediately_claims_existing_deferred_restart(
    settings: Settings,
) -> None:
    app = create_app(settings)
    app.state.deferred_restart.request(
        operation_id="deferred-operation",
        task_id="task-1",
        source_ip="127.0.0.1",
    )
    transport = httpx.ASGITransport(app=app)

    with (
        patch("app.api.maintenance.launch_restart_process") as launch_restart,
        patch("app.api.maintenance.monitor_restart_process"),
    ):
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://test",
            headers={"Authorization": "Bearer test-token-that-is-long-enough-for-tests"},
        ) as client:
            response = await client.post("/api/maintenance/restart")

    assert response.status_code == 200
    assert response.json()["data"] == {"status": "restarting"}
    assert app.state.deferred_restart.state().status == "started"
    launch_restart.assert_called_once()


@pytest.mark.anyio
async def test_restart_reuses_restart_that_is_already_starting(
    settings: Settings,
) -> None:
    app = create_app(settings)
    app.state.deferred_restart.request(
        operation_id="deferred-operation",
        task_id="task-1",
        source_ip="127.0.0.1",
    )
    assert app.state.deferred_restart.begin_immediate_restart() == "claimed"
    transport = httpx.ASGITransport(app=app)

    with (
        patch("app.api.maintenance.launch_restart_process") as launch_restart,
        patch("app.api.maintenance.monitor_restart_process"),
    ):
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://test",
            headers={"Authorization": "Bearer test-token-that-is-long-enough-for-tests"},
        ) as client:
            response = await client.post("/api/maintenance/restart")

    assert response.status_code == 200
    assert response.json()["data"] == {"status": "restarting"}
    launch_restart.assert_not_called()


@pytest.mark.anyio
async def test_restart_reuses_manual_restart_without_deferred_request(
    settings: Settings,
) -> None:
    app = create_app(settings)
    transport = httpx.ASGITransport(app=app)

    with (
        patch("app.api.maintenance.launch_restart_process") as launch_restart,
        patch("app.api.maintenance.monitor_restart_process"),
    ):
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://test",
            headers={"Authorization": "Bearer test-token-that-is-long-enough-for-tests"},
        ) as client:
            first = await client.post("/api/maintenance/restart")
            second = await client.post("/api/maintenance/restart")

    assert first.status_code == 200
    assert second.status_code == 200
    assert launch_restart.call_count == 1


@pytest.mark.anyio
async def test_restart_launch_failure_ends_claim_without_background_retry(
    settings: Settings,
) -> None:
    app = create_app(settings)
    app.state.deferred_restart.grace_seconds = 0
    app.state.deferred_restart.set_ready_check(lambda _request: "ready")
    app.state.deferred_restart.request(
        operation_id="deferred-operation",
        task_id="task-1",
        source_ip="127.0.0.1",
    )
    transport = httpx.ASGITransport(app=app)

    with (
        patch(
            "app.api.maintenance.launch_restart_process",
            side_effect=OSError("restart unavailable"),
        ),
        patch("app.application.launch_restart_process") as automatic_restart,
    ):
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://test",
            headers={"Authorization": "Bearer test-token-that-is-long-enough-for-tests"},
        ) as client:
            response = await client.post("/api/maintenance/restart")

    assert response.status_code == 500
    assert response.json()["error"]["code"] == "restart_failed"
    threading.Event().wait(0.05)

    assert app.state.deferred_restart.pending() is False
    automatic_restart.assert_not_called()


@pytest.mark.anyio
async def test_restart_async_failure_ends_claimed_deferred_restart(
    settings: Settings,
) -> None:
    app = create_app(settings)
    app.state.deferred_restart.request(
        operation_id="deferred-operation",
        task_id="task-1",
        source_ip="127.0.0.1",
    )
    transport = httpx.ASGITransport(app=app)

    with (
        patch("app.api.maintenance.launch_restart_process") as launch_restart,
        patch("app.api.maintenance.monitor_restart_process") as monitor_restart,
    ):
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://test",
            headers={"Authorization": "Bearer test-token-that-is-long-enough-for-tests"},
        ) as client:
            response = await client.post("/api/maintenance/restart")
        monitor_restart.call_args.args[1]("重启脚本返回退出码 1，旧服务仍在运行。")

    assert response.status_code == 200
    assert app.state.deferred_restart.pending() is False
    launch_restart.assert_called_once()


@pytest.mark.anyio
async def test_restart_async_failure_records_manual_operation_failure(
    settings: Settings,
) -> None:
    app = create_app(settings)
    transport = httpx.ASGITransport(app=app)

    with (
        patch("app.api.maintenance.launch_restart_process"),
        patch("app.api.maintenance.monitor_restart_process") as monitor_restart,
        patch("app.api.maintenance.write_operation") as write_operation,
    ):
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://test",
            headers={"Authorization": "Bearer test-token-that-is-long-enough-for-tests"},
        ) as client:
            response = await client.post("/api/maintenance/restart")
        monitor_restart.call_args.args[1]("重启脚本返回退出码 1，旧服务仍在运行。")

    assert response.status_code == 200
    assert write_operation.call_args.kwargs["status"] == "failed"
