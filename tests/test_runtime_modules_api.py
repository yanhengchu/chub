from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from app.application import create_app


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
async def test_invalid_runtime_module_id_is_rejected_before_worker_lookup(settings) -> None:
    app = create_app(settings)
    transport = httpx.ASGITransport(app=app)

    with patch("app.api.runtime_modules.list_tasks", new=AsyncMock()) as list_tasks:
        async with httpx.AsyncClient(transport=transport, base_url="http://test", headers=AUTHORIZATION) as client:
            response = await client.delete("/api/runtime-modules/invalid_module")

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "runtime_module_id_invalid"
    list_tasks.assert_not_awaited()


@pytest.mark.anyio
async def test_install_records_terminal_failure_when_web_activation_raises(settings) -> None:
    app = create_app(settings)
    manager = app.state.ai_session_manager
    manager.install_runtime_module = MagicMock(side_effect=RuntimeError("broken module"))
    manager.runtime_module_service.inspect_archive = MagicMock(
        return_value=SimpleNamespace(implementation_id="codex-010001")
    )
    transport = httpx.ASGITransport(app=app)

    with (
        patch("app.api.runtime_modules.list_tasks", new=AsyncMock(return_value={"success": True, "data": {"tasks": []}})),
        patch("app.api.runtime_modules.read_health", new=AsyncMock(return_value=_worker_health())),
        patch("app.api.runtime_modules.log_operation", side_effect=["a" * 32, None, None]) as operation_log,
    ):
        async with httpx.AsyncClient(transport=transport, base_url="http://test", headers=AUTHORIZATION) as client:
            response = await client.post(
                "/api/runtime-modules/install",
                content=b"zip-content",
                headers={"X-Chub-Module-Filename": "module.zip"},
            )

    assert response.status_code == 500
    assert response.json()["error"]["code"] == "runtime_module_install_failed"
    assert operation_log.call_args_list[-1].kwargs["status"] == "failed"
    assert operation_log.call_args_list[-1].kwargs["reason"] == "runtime_module_install_failed"


@pytest.mark.anyio
async def test_install_rejects_a_busy_target_implementation(settings) -> None:
    app = create_app(settings)
    manager = app.state.ai_session_manager
    manager.runtime_module_service.inspect_archive = MagicMock(
        return_value=SimpleNamespace(implementation_id="codex-010001")
    )
    manager.install_runtime_module = MagicMock()
    transport = httpx.ASGITransport(app=app)

    with patch(
        "app.api.runtime_modules.list_tasks",
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
    manager.install_runtime_module.assert_not_called()


@pytest.mark.anyio
async def test_remove_confirms_target_is_absent_from_worker_registry(settings) -> None:
    app = create_app(settings)
    manager = app.state.ai_session_manager
    removal = SimpleNamespace(module_id="codex-010001", operation_id="b" * 32)
    manager.remove_runtime_module = MagicMock(return_value=removal)
    manager.runtime_module_service.finalize_removal = MagicMock()
    transport = httpx.ASGITransport(app=app)

    with (
        patch("app.api.runtime_modules.list_tasks", new=AsyncMock(return_value={"success": True, "data": {"tasks": []}})),
        patch(
            "app.api.runtime_modules.read_health",
            new=AsyncMock(return_value=_worker_health(implementations=["builtin-dev"])),
        ),
        patch("app.api.runtime_modules.refresh_runtime_registry", new=AsyncMock(return_value={"success": True})),
        patch("app.api.runtime_modules.log_operation", side_effect=["b" * 32, None, None]),
    ):
        async with httpx.AsyncClient(transport=transport, base_url="http://test", headers=AUTHORIZATION) as client:
            response = await client.delete("/api/runtime-modules/codex-010001")

    assert response.status_code == 200
    assert response.json()["data"] == {
        "module_id": "codex-010001",
        "worker_generation": "a" * 32,
    }
    manager.runtime_module_service.finalize_removal.assert_called_once_with(removal)
