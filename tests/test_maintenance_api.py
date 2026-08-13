from unittest.mock import patch

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
        patch("app.api.maintenance.subprocess.Popen") as popen,
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
    assert popen.call_args.args[0] == [str(command)]
    assert popen.call_args.kwargs["start_new_session"] is True


@pytest.mark.anyio
async def test_restart_rejects_active_quick_interaction(settings: Settings) -> None:
    app = create_app(settings)
    app.state.quick_interactions._active_task_ids.add("task-1")
    transport = httpx.ASGITransport(app=app)

    with patch("app.api.maintenance.subprocess.Popen") as popen:
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://test",
            headers={"Authorization": "Bearer test-token-that-is-long-enough-for-tests"},
        ) as client:
            response = await client.post("/api/maintenance/restart")

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "quick_interaction_in_progress"
    popen.assert_not_called()


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
        patch("app.api.maintenance.subprocess.Popen") as popen,
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
    popen.assert_called_once()


@pytest.mark.anyio
async def test_restart_rejects_existing_deferred_restart(settings: Settings) -> None:
    app = create_app(settings)
    app.state.deferred_restart.pending = lambda: True
    transport = httpx.ASGITransport(app=app)

    with patch("app.api.maintenance.subprocess.Popen") as popen:
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://test",
            headers={"Authorization": "Bearer test-token-that-is-long-enough-for-tests"},
        ) as client:
            response = await client.post("/api/maintenance/restart")

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "restart_already_pending"
    popen.assert_not_called()
