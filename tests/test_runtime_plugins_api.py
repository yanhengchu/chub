import httpx
import pytest

from app.application import create_app


@pytest.mark.anyio
async def test_runtime_lifecycle_is_exposed_only_through_plugins_api(settings) -> None:
    transport = httpx.ASGITransport(app=create_app(settings))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        legacy = await client.get("/api/runtime-modules")
        plugins = await client.get("/api/plugins")

    runtime = next(item for item in plugins.json()["data"]["plugins"] if item["plugin_id"] == "runtime")
    assert legacy.status_code == 404
    assert any(item["artifact_id"] == "development:codex-runtime-dev" for item in runtime["artifacts"])


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
