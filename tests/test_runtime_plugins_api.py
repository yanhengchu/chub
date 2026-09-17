from types import SimpleNamespace

import httpx
import pytest

from app.application import create_app
from app.api import runtime_plugins


@pytest.mark.anyio
async def test_legacy_runtime_lifecycle_routes_are_not_registered(settings) -> None:
    transport = httpx.ASGITransport(app=create_app(settings))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/api/runtime-modules")
    assert response.status_code == 404


@pytest.mark.anyio
async def test_runtime_lifecycle_is_exposed_only_through_plugins_api(settings) -> None:
    transport = httpx.ASGITransport(app=create_app(settings))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/api/plugins")
    runtime = next(item for item in response.json()["data"]["plugins"] if item["plugin_id"] == "runtime")
    assert response.status_code == 200
    assert any(item["artifact_id"] == "development:codex-runtime-dev" for item in runtime["artifacts"])
    assert all(item["description"] for item in runtime["artifacts"])


@pytest.mark.anyio
async def test_legacy_runtime_lifecycle_state_is_cleared(settings) -> None:
    app = create_app(settings)
    app.state.plugin_lifecycle.path.parent.mkdir(parents=True, exist_ok=True)
    app.state.plugin_lifecycle.path.write_text(
        '{"imports":{"codex-runtime":["development:codex-runtime"]},"enabled":{}}',
        encoding="utf-8",
    )
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/api/plugins")

    assert response.status_code == 200
    assert "codex-runtime" not in app.state.plugin_lifecycle.path.read_text("utf-8")


@pytest.mark.anyio
async def test_disabling_codex_keeps_its_imported_lifecycle_artifact_visible(settings) -> None:
    transport = httpx.ASGITransport(app=create_app(settings))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        await client.post(
            "/api/plugins/runtime/imports",
            json={"artifact_id": "development:codex-runtime-dev"},
        )
        disabled = await client.put(
            "/api/plugins/runtime/enabled",
            json={"artifact_id": "development:codex-runtime-dev", "enabled": False},
        )

    assert disabled.status_code == 200
    assert "development:codex-runtime-dev" in disabled.json()["data"]["imported_artifact_ids"]
    assert "development:codex-runtime-dev" not in disabled.json()["data"]["enabled_artifact_ids"]


@pytest.mark.anyio
async def test_codex_runtime_is_available_only_after_import_and_enablement(settings) -> None:
    app = create_app(settings)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        before = await client.get("/api/maintenance/quick-worker")
        imported = await client.post(
            "/api/plugins/runtime/imports",
            json={"artifact_id": "development:codex-runtime-dev"},
        )
        after_import = await client.get("/api/maintenance/quick-worker")
        enabled = await client.put(
            "/api/plugins/runtime/enabled",
            json={"artifact_id": "development:codex-runtime-dev", "enabled": True},
        )
        after_enablement = await client.get("/api/maintenance/quick-worker")

    assert before.json()["data"]["runtime_state"] == "unconfigured"
    assert imported.status_code == 200
    assert after_import.json()["data"]["runtime_state"] == "disabled"
    assert enabled.status_code == 200
    assert after_enablement.json()["data"]["runtime_state"] == "available"
    assert app.state.plugin_lifecycle.runtime_implementation_lifecycle_state(
        "codex-runtime-dev"
    ) == (True, True)


@pytest.mark.anyio
async def test_legacy_codex_runtime_writes_do_not_bypass_plugin_lifecycle(settings) -> None:
    app = create_app(settings)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        runtime = await client.put("/api/ai/runtimes/codex", json={"enabled": True})
        implementation = await client.put(
            "/api/ai/runtime-implementations/codex-runtime-dev/enabled",
            json={"enabled": True},
        )
        default = await client.put(
            "/api/ai/runtime-implementations/default",
            json={"implementation_id": "codex-runtime-dev"},
        )

    assert runtime.status_code == 409
    assert runtime.json()["error"]["code"] == "runtime_plugin_not_imported"
    assert implementation.status_code == 409
    assert implementation.json()["error"]["code"] == "plugin_not_imported"
    assert default.status_code == 409
    assert default.json()["error"]["code"] == "runtime_plugin_not_imported"


@pytest.mark.anyio
async def test_development_runtime_refresh_availability_uses_current_implementation_id(
    settings, monkeypatch
) -> None:
    app = create_app(settings)

    async def worker_ready(_request) -> str:
        return "worker-generation"

    async def implementation_idle(_request, implementation_id: str) -> None:
        assert implementation_id == "codex-runtime-dev"

    monkeypatch.setattr(runtime_plugins, "_worker_generation", worker_ready)
    monkeypatch.setattr(runtime_plugins, "_require_implementation_idle", implementation_idle)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        await client.post(
            "/api/plugins/runtime/imports",
            json={"artifact_id": "development:codex-runtime-dev"},
        )
        await client.put(
            "/api/plugins/runtime/enabled",
            json={"artifact_id": "development:codex-runtime-dev", "enabled": True},
        )
    response = await runtime_plugins.read_development_runtime_plugin_refresh_availability(
        "codex-runtime-dev",
        SimpleNamespace(app=app),
    )

    assert response.data.model_dump() == {"available": True, "reason": None}


@pytest.mark.anyio
async def test_removing_codex_development_artifact_hides_runtime_navigation(settings) -> None:
    transport = httpx.ASGITransport(app=create_app(settings))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        await client.post(
            "/api/plugins/runtime/imports",
            json={"artifact_id": "development:codex-runtime-dev"},
        )
        imported_navigation = await client.get("/settings/runtime")
        removed = await client.delete(
            "/api/plugins/runtime/imports/development%3Acodex-runtime-dev",
        )
        removed_navigation = await client.get("/settings/runtime")

    assert 'href="/settings/runtime/codex"' in imported_navigation.text
    assert removed.status_code == 200
    assert removed.json()["data"]["imported_artifact_ids"] == []
    assert 'href="/settings/runtime/codex"' not in removed_navigation.text


@pytest.mark.anyio
async def test_system_upgrade_blocks_only_codex_plugin_lifecycle_writes(settings) -> None:
    app = create_app(settings)
    app.state.system_upgrade._writes_blocked = True
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        codex = await client.post(
            "/api/plugins/runtime/imports",
            json={"artifact_id": "development:codex-runtime-dev"},
        )
        deliveryline = await client.post(
            "/api/plugins/deliveryline/imports",
            json={"artifact_id": "development:deliveryline"},
        )

    assert codex.status_code == 409
    assert codex.json()["error"]["code"] == "system_upgrade_in_progress"
    assert deliveryline.status_code == 200
