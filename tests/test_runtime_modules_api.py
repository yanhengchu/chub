from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from app.application import create_app


AUTHORIZATION = {"Authorization": "Bearer test-token-that-is-long-enough-for-tests"}


@pytest.mark.anyio
async def test_runtime_module_install_records_terminal_failure_for_unexpected_error(
    settings,
) -> None:
    app = create_app(settings)
    manager = app.state.ai_session_manager
    manager.install_runtime_module = MagicMock(side_effect=RuntimeError("broken module"))
    transport = httpx.ASGITransport(app=app)
    health = {
        "success": True,
        "data": {
            "status": "ready",
            "generation": "a" * 32,
            "active_tasks": 0,
            "queued_tasks": 0,
            "uncertain_tasks": 0,
            "corrupt_tasks": 0,
            "runtime_ids": ["codex"],
            "available_runtime_ids": ["codex"],
        },
    }
    drain = {"success": True, "data": {"status": "draining"}}
    resume = {"success": True, "data": {"status": "ready"}}

    with (
        patch.object(
            manager.runtime_module_service,
            "inspect_archive",
            return_value=SimpleNamespace(module_id="local-test"),
        ),
        patch("app.api.runtime_modules.read_health", new=AsyncMock(return_value=health)),
        patch("app.api.runtime_modules.request_drain", new=AsyncMock(return_value=drain)),
        patch("app.api.runtime_modules.resume_after_drain", new=AsyncMock(return_value=resume)) as resume_after,
        patch("app.api.runtime_modules.log_operation", side_effect=["operation-1", None, None]) as operation_log,
    ):
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://test",
            headers=AUTHORIZATION,
        ) as client:
            response = await client.post(
                "/api/runtime-modules/install",
                content=b"zip-content",
                headers={"X-Chub-Module-Filename": "module.zip"},
            )

    assert response.status_code == 500
    assert response.json()["error"]["code"] == "runtime_module_install_failed"
    resume_after.assert_awaited_once()
    assert operation_log.call_args_list[-1].kwargs["status"] == "failed"
    assert operation_log.call_args_list[-1].kwargs["reason"] == "runtime_module_install_failed"


@pytest.mark.anyio
async def test_invalid_runtime_module_does_not_drain_worker(settings) -> None:
    app = create_app(settings)
    transport = httpx.ASGITransport(app=app)

    with patch("app.api.runtime_modules.request_drain", new=AsyncMock()) as drain:
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://test",
            headers=AUTHORIZATION,
        ) as client:
            response = await client.post(
                "/api/runtime-modules/install",
                content=b"not-a-zip",
                headers={"X-Chub-Module-Filename": "invalid.zip"},
            )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "runtime_module_install_invalid"
    drain.assert_not_awaited()


@pytest.mark.anyio
async def test_runtime_module_install_does_not_clear_state_before_worker_reload(
    settings,
) -> None:
    app = create_app(settings)
    manager = app.state.ai_session_manager
    activation = SimpleNamespace(
        installed=SimpleNamespace(
            manifest=SimpleNamespace(module_id="local-test"),
        ),
        operation_id="operation-1",
    )
    manager.install_runtime_module = MagicMock(return_value=activation)
    manager.runtime_module_service.inspect_archive = MagicMock(
        return_value=SimpleNamespace(module_id="local-test"),
    )
    manager.runtime_module_service.mark_worker_reload_requested = MagicMock()
    manager.runtime_module_service.rollback = MagicMock()
    manager.refresh_external_runtime_modules = MagicMock()
    app.state.quick_worker_maintenance.begin = MagicMock(return_value=False)
    transport = httpx.ASGITransport(app=app)
    health = {
        "success": True,
        "data": {
            "status": "ready",
            "generation": "a" * 32,
            "active_tasks": 0,
            "queued_tasks": 0,
            "uncertain_tasks": 0,
            "corrupt_tasks": 0,
            "runtime_ids": ["codex"],
            "available_runtime_ids": ["codex"],
        },
    }

    with (
        patch("app.api.runtime_modules.read_health", new=AsyncMock(return_value=health)),
        patch("app.api.runtime_modules.request_drain", new=AsyncMock(return_value={"success": True})),
        patch("app.api.runtime_modules.resume_after_drain", new=AsyncMock(return_value={"success": True})),
        patch("app.api.runtime_modules.clear_runtime_state", new=AsyncMock()) as clear_state,
    ):
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://test",
            headers=AUTHORIZATION,
        ) as client:
            response = await client.post(
                "/api/runtime-modules/install",
                content=b"zip-content",
                headers={"X-Chub-Module-Filename": "module.zip"},
            )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "quick_worker_reload_in_progress"
    clear_state.assert_not_awaited()


@pytest.mark.anyio
async def test_runtime_module_removal_resumes_worker_after_web_rejection(settings) -> None:
    app = create_app(settings)
    app.state.ai_session_manager.remove_runtime_module = MagicMock(
        side_effect=RuntimeError("broken removal")
    )
    transport = httpx.ASGITransport(app=app)
    health = {"success": True, "data": {"status": "ready", "generation": "a" * 32, "active_tasks": 0, "queued_tasks": 0, "uncertain_tasks": 0, "corrupt_tasks": 0, "runtime_ids": ["codex"], "available_runtime_ids": ["codex"]}}

    with (
        patch("app.api.runtime_modules.read_health", new=AsyncMock(return_value=health)),
        patch("app.api.runtime_modules.request_drain", new=AsyncMock(return_value={"success": True})),
        patch("app.api.runtime_modules.resume_after_drain", new=AsyncMock(return_value={"success": True})) as resume_after,
    ):
        async with httpx.AsyncClient(transport=transport, base_url="http://test", headers=AUTHORIZATION) as client:
            response = await client.delete("/api/runtime-modules/local-test")

    assert response.status_code == 500
    assert response.json()["error"]["code"] == "runtime_module_remove_failed"
    resume_after.assert_awaited_once()


@pytest.mark.anyio
async def test_runtime_module_install_keeps_confirmed_module_when_state_cleanup_fails(
    settings,
) -> None:
    app = create_app(settings)
    manager = app.state.ai_session_manager
    activation = SimpleNamespace(
        installed=SimpleNamespace(manifest=SimpleNamespace(module_id="local-test")),
        operation_id="operation-1",
    )
    manager.install_runtime_module = MagicMock(return_value=activation)
    manager.runtime_module_service.inspect_archive = MagicMock(
        return_value=SimpleNamespace(module_id="local-test"),
    )
    manager.runtime_module_service.mark_worker_reload_requested = MagicMock()
    manager.runtime_module_service.rollback = MagicMock()
    manager.runtime_module_service.finalize = MagicMock()
    app.state.quick_worker_maintenance.begin = MagicMock(return_value=True)
    health = {
        "success": True,
        "data": {
            "status": "ready",
            "generation": "a" * 32,
            "active_tasks": 0,
            "queued_tasks": 0,
            "uncertain_tasks": 0,
            "corrupt_tasks": 0,
            "runtime_ids": ["codex", "local-test"],
            "available_runtime_ids": ["codex", "local-test"],
        },
    }
    transport = httpx.ASGITransport(app=app)

    with (
        patch("app.api.runtime_modules.read_health", new=AsyncMock(return_value=health)),
        patch("app.api.runtime_modules.request_drain", new=AsyncMock(return_value={"success": True})),
        patch("app.api.runtime_modules._wait_for_reload", new=AsyncMock(return_value=True)),
        patch("app.api.runtime_modules.clear_runtime_state", new=AsyncMock(return_value={"success": False})),
        patch("app.api.runtime_modules.log_operation", side_effect=["operation-1", None, None]),
    ):
        async with httpx.AsyncClient(transport=transport, base_url="http://test", headers=AUTHORIZATION) as client:
            response = await client.post(
                "/api/runtime-modules/install",
                content=b"zip-content",
                headers={"X-Chub-Module-Filename": "module.zip"},
            )

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "runtime_module_state_cleanup_unconfirmed"
    manager.runtime_module_service.rollback.assert_not_called()
    manager.runtime_module_service.finalize.assert_called_once_with(activation)


@pytest.mark.anyio
async def test_runtime_module_removal_keeps_confirmed_removal_when_state_cleanup_fails(
    settings,
) -> None:
    app = create_app(settings)
    manager = app.state.ai_session_manager
    removal = SimpleNamespace(module_id="local-test", operation_id="operation-1")
    manager.remove_runtime_module = MagicMock(return_value=removal)
    manager.runtime_module_service.rollback_removal = MagicMock()
    manager.runtime_module_service.finalize_removal = MagicMock()
    app.state.quick_worker_maintenance.begin = MagicMock(return_value=True)
    health = {
        "success": True,
        "data": {
            "status": "ready",
            "generation": "a" * 32,
            "active_tasks": 0,
            "queued_tasks": 0,
            "uncertain_tasks": 0,
            "corrupt_tasks": 0,
            "runtime_ids": ["codex"],
            "available_runtime_ids": ["codex"],
        },
    }
    transport = httpx.ASGITransport(app=app)

    with (
        patch("app.api.runtime_modules.read_health", new=AsyncMock(return_value=health)),
        patch("app.api.runtime_modules.request_drain", new=AsyncMock(return_value={"success": True})),
        patch("app.api.runtime_modules._wait_for_reload", new=AsyncMock(return_value=True)),
        patch("app.api.runtime_modules.clear_runtime_state", new=AsyncMock(return_value={"success": False})),
        patch("app.api.runtime_modules.log_operation", side_effect=["operation-1", None, None]),
    ):
        async with httpx.AsyncClient(transport=transport, base_url="http://test", headers=AUTHORIZATION) as client:
            response = await client.delete("/api/runtime-modules/local-test")

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "runtime_module_state_cleanup_unconfirmed"
    manager.runtime_module_service.rollback_removal.assert_not_called()
    manager.runtime_module_service.finalize_removal.assert_called_once_with(removal)


@pytest.mark.anyio
async def test_runtime_module_removal_rejects_invalid_id_before_worker_drain(settings) -> None:
    app = create_app(settings)
    transport = httpx.ASGITransport(app=app)

    with patch("app.api.runtime_modules.request_drain", new=AsyncMock()) as drain:
        async with httpx.AsyncClient(transport=transport, base_url="http://test", headers=AUTHORIZATION) as client:
            response = await client.delete("/api/runtime-modules/invalid_module")

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "runtime_module_id_invalid"
    drain.assert_not_awaited()
