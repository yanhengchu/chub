from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from app.application import create_app
from app.ai_session.models import AiSession


AUTHORIZATION = {"Authorization": "Bearer test-token-that-is-long-enough-for-tests"}


def _worker_health(*, implementations: list[str] | None = None) -> dict[str, object]:
    registered = implementations or ["builtin-dev", "codex-010000"]
    return {
        "success": True,
        "data": {
            "status": "ready",
            "generation": "a" * 32,
            "implementation_ids": registered,
            "available_implementation_ids": registered,
        },
    }


@pytest.mark.anyio
async def test_invalid_runtime_plugin_id_is_rejected_before_worker_lookup(settings) -> None:
    app = create_app(settings)
    transport = httpx.ASGITransport(app=app)

    with patch("app.api.runtime_plugins.list_tasks", new=AsyncMock()) as list_tasks:
        async with httpx.AsyncClient(transport=transport, base_url="http://test", headers=AUTHORIZATION) as client:
            response = await client.delete("/api/runtime-modules/invalid_module")

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "runtime_plugin_id_invalid"
    list_tasks.assert_not_awaited()


@pytest.mark.anyio
async def test_install_records_terminal_failure_when_web_activation_raises(settings) -> None:
    app = create_app(settings)
    manager = app.state.ai_session_manager
    manager.install_runtime_plugin = MagicMock(side_effect=RuntimeError("broken plugin"))
    manager.runtime_plugin_service.inspect_archive = MagicMock(
        return_value=SimpleNamespace(implementation_id="codex-010001")
    )
    transport = httpx.ASGITransport(app=app)

    with (
        patch("app.api.runtime_plugins.list_tasks", new=AsyncMock(return_value={"success": True, "data": {"tasks": []}})),
        patch(
            "app.api.runtime_plugins.read_health",
            new=AsyncMock(
                return_value=_worker_health(
                    implementations=["builtin-dev", "codex-010000", "codex-010001"]
                )
            ),
        ),
        patch("app.api.runtime_plugins.log_operation", side_effect=["a" * 32, None, None]) as operation_log,
    ):
        async with httpx.AsyncClient(transport=transport, base_url="http://test", headers=AUTHORIZATION) as client:
            response = await client.post(
                "/api/runtime-modules/install",
                content=b"zip-content",
                headers={"X-Chub-Module-Filename": "module.zip"},
            )

    assert response.status_code == 500
    assert response.json()["error"]["code"] == "runtime_plugin_install_failed"
    assert operation_log.call_args_list[-1].kwargs["status"] == "failed"
    assert operation_log.call_args_list[-1].kwargs["reason"] == "runtime_plugin_install_failed"


@pytest.mark.anyio
async def test_install_rejects_a_busy_target_implementation(settings) -> None:
    app = create_app(settings)
    manager = app.state.ai_session_manager
    manager.runtime_plugin_service.inspect_archive = MagicMock(
        return_value=SimpleNamespace(implementation_id="codex-010001")
    )
    manager.install_runtime_plugin = MagicMock()
    transport = httpx.ASGITransport(app=app)

    with patch(
        "app.api.runtime_plugins.list_tasks",
        new=AsyncMock(
            return_value={
                "success": True,
                "data": {"tasks": [{"implementation_id": "codex-010001"}]},
            }
        ),
    ):
        async with httpx.AsyncClient(transport=transport, base_url="http://test", headers=AUTHORIZATION) as client:
            response = await client.post(
                "/api/runtime-modules/install",
                content=b"zip-content",
                headers={"X-Chub-Module-Filename": "module.zip"},
            )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "runtime_implementation_busy"
    manager.install_runtime_plugin.assert_not_called()


@pytest.mark.anyio
async def test_install_allows_covering_a_slot_bound_by_an_idle_session(settings) -> None:
    app = create_app(settings)
    manager = app.state.ai_session_manager
    manager.runtime_plugin_service.inspect_archive = MagicMock(
        return_value=SimpleNamespace(implementation_id="codex-010001")
    )
    manager.install_runtime_plugin = MagicMock(
        return_value=SimpleNamespace(
            installed=SimpleNamespace(),
            operation_id="a" * 32,
        )
    )
    manager.runtime_plugin_service.finalize = MagicMock()
    manager.store.save(
        AiSession(
            id="123e4567-e89b-12d3-a456-426614174001",
            runtime_id="codex",
            implementation_id="codex-010001",
            workspace_id="chub",
            workspace_name="Chub",
            cwd=settings.ai_runtime.codex.workspace,
        )
    )
    transport = httpx.ASGITransport(app=app)

    with (
        patch("app.api.runtime_plugins.list_tasks", new=AsyncMock(return_value={"success": True, "data": {"tasks": []}})),
        patch(
            "app.api.runtime_plugins.read_health",
            new=AsyncMock(
                return_value=_worker_health(
                    implementations=["builtin-dev", "codex-010000", "codex-010001"]
                )
            ),
        ),
        patch("app.api.runtime_plugins.refresh_runtime_registry", new=AsyncMock(return_value={"success": True})),
        patch("app.api.runtime_plugins.log_operation", side_effect=["a" * 32, None, None]),
    ):
        async with httpx.AsyncClient(transport=transport, base_url="http://test", headers=AUTHORIZATION) as client:
            response = await client.post(
                "/api/runtime-modules/install",
                content=b"zip-content",
                headers={"X-Chub-Module-Filename": "module.zip"},
            )

    assert response.status_code == 200
    manager.install_runtime_plugin.assert_called_once()


@pytest.mark.anyio
async def test_builtin_refresh_availability_is_disabled_for_a_running_task(settings) -> None:
    app = create_app(settings)
    transport = httpx.ASGITransport(app=app)

    with (
        patch("app.api.runtime_plugins.read_health", new=AsyncMock(return_value=_worker_health())),
        patch(
            "app.api.runtime_plugins.list_tasks",
            new=AsyncMock(
                return_value={
                    "success": True,
                    "data": {"tasks": [{"implementation_id": "builtin-dev", "status": "running"}]},
                }
            ),
        ),
    ):
        async with httpx.AsyncClient(transport=transport, base_url="http://test", headers=AUTHORIZATION) as client:
            response = await client.get("/api/runtime-modules/builtin-dev/refresh-availability")

    assert response.status_code == 200
    assert response.json()["data"] == {
        "available": False,
        "reason": "该 Runtime 版本仍有已受理任务，请等待其结束后再维护源码。",
    }


@pytest.mark.anyio
async def test_builtin_refresh_availability_is_disabled_for_a_queued_task(settings) -> None:
    app = create_app(settings)
    transport = httpx.ASGITransport(app=app)

    with (
        patch("app.api.runtime_plugins.read_health", new=AsyncMock(return_value=_worker_health())),
        patch(
            "app.api.runtime_plugins.list_tasks",
            new=AsyncMock(
                return_value={
                    "success": True,
                    "data": {"tasks": [{"implementation_id": "builtin-dev", "status": "queued"}]},
                }
            ),
        ),
    ):
        async with httpx.AsyncClient(transport=transport, base_url="http://test", headers=AUTHORIZATION) as client:
            response = await client.get("/api/runtime-modules/builtin-dev/refresh-availability")

    assert response.status_code == 200
    assert response.json()["data"] == {
        "available": False,
        "reason": "该 Runtime 版本仍有已受理任务，请等待其结束后再维护源码。",
    }


@pytest.mark.anyio
async def test_builtin_refresh_availability_allows_a_bound_session(settings) -> None:
    app = create_app(settings)
    manager = app.state.ai_session_manager
    session = AiSession(
        id="123e4567-e89b-12d3-a456-426614174000",
        runtime_id="codex",
        implementation_id="builtin-dev",
        workspace_id="chub",
        workspace_name="Chub",
        cwd=settings.ai_runtime.codex.workspace,
    )
    manager.store.save(session)
    transport = httpx.ASGITransport(app=app)

    with (
        patch("app.api.runtime_plugins.read_health", new=AsyncMock(return_value=_worker_health())),
        patch("app.api.runtime_plugins.list_tasks", new=AsyncMock(return_value={"success": True, "data": {"tasks": []}})),
    ):
        async with httpx.AsyncClient(transport=transport, base_url="http://test", headers=AUTHORIZATION) as client:
            response = await client.get("/api/runtime-modules/builtin-dev/refresh-availability")

    assert response.status_code == 200
    assert response.json()["data"] == {
        "available": True,
        "reason": None,
    }


@pytest.mark.anyio
async def test_refresh_builtin_dev_confirms_web_and_worker(settings) -> None:
    app = create_app(settings)
    manager = app.state.ai_session_manager
    manager.refresh_development_codex_plugin = MagicMock()
    transport = httpx.ASGITransport(app=app)

    with (
        patch("app.api.runtime_plugins.list_tasks", new=AsyncMock(return_value={"success": True, "data": {"tasks": []}})),
        patch("app.api.runtime_plugins.read_health", new=AsyncMock(return_value=_worker_health())),
        patch("app.api.runtime_plugins.refresh_runtime_registry", new=AsyncMock(return_value={"success": True})) as refresh_worker,
        patch("app.api.runtime_plugins.log_operation", side_effect=["c" * 32, None, None]) as operation_log,
    ):
        async with httpx.AsyncClient(transport=transport, base_url="http://test", headers=AUTHORIZATION) as client:
            response = await client.post("/api/runtime-modules/builtin-dev/refresh")

    assert response.status_code == 200
    assert response.json()["data"] == {"module_id": "builtin-dev", "worker_generation": "a" * 32}
    manager.refresh_development_codex_plugin.assert_called_once_with()
    refresh_worker.assert_awaited_once_with(
        settings,
        implementation_id="builtin-dev",
        expected_present=True,
        reload_builtin_source=True,
    )
    assert operation_log.call_args_list[-1].kwargs["status"] == "succeeded"


@pytest.mark.anyio
async def test_refresh_builtin_dev_is_rejected_when_codex_is_disabled(settings) -> None:
    app = create_app(settings)
    app.state.ai_session_manager.update_runtime_enabled("codex", False)
    transport = httpx.ASGITransport(app=app)

    async with httpx.AsyncClient(transport=transport, base_url="http://test", headers=AUTHORIZATION) as client:
        response = await client.post("/api/runtime-modules/builtin-dev/refresh")

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "ai_runtime_disabled"


@pytest.mark.anyio
async def test_refresh_builtin_dev_explains_target_task_conflict(settings) -> None:
    app = create_app(settings)
    manager = app.state.ai_session_manager
    previous_builtin = object()
    manager.refresh_development_codex_plugin = MagicMock(return_value=previous_builtin)
    manager.restore_development_codex_plugin = MagicMock()
    transport = httpx.ASGITransport(app=app)

    with (
        patch("app.api.runtime_plugins.list_tasks", new=AsyncMock(return_value={"success": True, "data": {"tasks": []}})),
        patch("app.api.runtime_plugins.read_health", new=AsyncMock(return_value=_worker_health())),
        patch(
            "app.api.runtime_plugins.refresh_runtime_registry",
            new=AsyncMock(return_value={"success": False, "error": {"code": "runtime_implementation_busy"}}),
        ),
        patch("app.api.runtime_plugins.log_operation", side_effect=["d" * 32, None, None]),
    ):
        async with httpx.AsyncClient(transport=transport, base_url="http://test", headers=AUTHORIZATION) as client:
            response = await client.post("/api/runtime-modules/builtin-dev/refresh")

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "runtime_implementation_busy"
    assert "运行中任务" in response.json()["error"]["message"]
    manager.restore_development_codex_plugin.assert_called_once_with(previous_builtin)


@pytest.mark.anyio
async def test_refresh_builtin_dev_rejects_queued_target_tasks(settings) -> None:
    app = create_app(settings)
    manager = app.state.ai_session_manager
    manager.refresh_development_codex_plugin = MagicMock()
    transport = httpx.ASGITransport(app=app)

    with (
        patch(
            "app.api.runtime_plugins.list_tasks",
            new=AsyncMock(
                return_value={
                    "success": True,
                    "data": {"tasks": [{"implementation_id": "builtin-dev", "status": "queued"}]},
                }
            ),
        ),
        patch("app.api.runtime_plugins.read_health", new=AsyncMock(return_value=_worker_health())),
        patch("app.api.runtime_plugins.refresh_runtime_registry", new=AsyncMock(return_value={"success": True})) as refresh_worker,
        patch("app.api.runtime_plugins.log_operation", side_effect=["f" * 32, None, None]),
    ):
        async with httpx.AsyncClient(transport=transport, base_url="http://test", headers=AUTHORIZATION) as client:
            response = await client.post("/api/runtime-modules/builtin-dev/refresh")

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "runtime_implementation_busy"
    refresh_worker.assert_not_awaited()


@pytest.mark.anyio
async def test_refresh_builtin_dev_explains_worker_upgrade_requirement(settings) -> None:
    app = create_app(settings)
    manager = app.state.ai_session_manager
    manager.refresh_development_codex_plugin = MagicMock()
    transport = httpx.ASGITransport(app=app)

    with (
        patch("app.api.runtime_plugins.list_tasks", new=AsyncMock(return_value={"success": True, "data": {"tasks": []}})),
        patch("app.api.runtime_plugins.read_health", new=AsyncMock(return_value=_worker_health())),
        patch(
            "app.api.runtime_plugins.refresh_runtime_registry",
            new=AsyncMock(return_value={"success": False, "error": {"code": "worker_request_invalid"}}),
        ),
        patch("app.api.runtime_plugins.log_operation", side_effect=["e" * 32, None, None]),
    ):
        async with httpx.AsyncClient(transport=transport, base_url="http://test", headers=AUTHORIZATION) as client:
            response = await client.post("/api/runtime-modules/builtin-dev/refresh")

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "quick_worker_refresh_upgrade_required"
    assert "重载 Quick Worker" in response.json()["error"]["message"]


@pytest.mark.anyio
async def test_remove_confirms_target_is_absent_from_worker_registry(settings) -> None:
    app = create_app(settings)
    manager = app.state.ai_session_manager
    removal = SimpleNamespace(module_id="codex-010001", operation_id="b" * 32)
    manager.remove_runtime_plugin = MagicMock(return_value=removal)
    manager.runtime_plugin_service.finalize_removal = MagicMock()
    transport = httpx.ASGITransport(app=app)

    with (
        patch("app.api.runtime_plugins.list_tasks", new=AsyncMock(return_value={"success": True, "data": {"tasks": []}})),
        patch(
            "app.api.runtime_plugins.read_health",
            new=AsyncMock(return_value=_worker_health(implementations=["builtin-dev"])),
        ),
        patch("app.api.runtime_plugins.refresh_runtime_registry", new=AsyncMock(return_value={"success": True})) as refresh_worker,
        patch("app.api.runtime_plugins.log_operation", side_effect=["b" * 32, None, None]),
    ):
        async with httpx.AsyncClient(transport=transport, base_url="http://test", headers=AUTHORIZATION) as client:
            response = await client.delete("/api/runtime-modules/codex-010001")

    assert response.status_code == 200
    assert response.json()["data"] == {
        "module_id": "codex-010001",
        "worker_generation": "a" * 32,
    }
    manager.runtime_plugin_service.finalize_removal.assert_called_once_with(removal)
    refresh_worker.assert_awaited_once_with(
        settings,
        implementation_id="codex-010001",
        expected_present=False,
    )
