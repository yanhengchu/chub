import httpx
import pytest

from app.application import create_app


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
    runtime = next(item for item in response.json()["data"]["plugins"] if item["plugin_id"] == "codex-runtime")
    assert response.status_code == 200
    assert any(item["artifact_id"] == "development:codex-runtime" for item in runtime["artifacts"])
    assert all(item["description"] for item in runtime["artifacts"])


@pytest.mark.anyio
async def test_disabling_codex_keeps_its_imported_lifecycle_artifact_visible(settings) -> None:
    transport = httpx.ASGITransport(app=create_app(settings))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        await client.post(
            "/api/plugins/codex-runtime/imports",
            json={"artifact_id": "development:codex-runtime"},
        )
        disabled = await client.put(
            "/api/plugins/codex-runtime/enabled",
            json={"artifact_id": "development:codex-runtime", "enabled": False},
        )

    assert disabled.status_code == 200
    assert "development:codex-runtime" in disabled.json()["data"]["imported_artifact_ids"]
    assert "development:codex-runtime" not in disabled.json()["data"]["enabled_artifact_ids"]


@pytest.mark.anyio
async def test_removing_codex_development_artifact_hides_runtime_navigation(settings) -> None:
    transport = httpx.ASGITransport(app=create_app(settings))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        await client.post(
            "/api/plugins/codex-runtime/imports",
            json={"artifact_id": "development:codex-runtime"},
        )
        imported_navigation = await client.get("/settings/runtime")
        removed = await client.delete(
            "/api/plugins/codex-runtime/imports/development%3Acodex-runtime",
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
            "/api/plugins/codex-runtime/imports",
            json={"artifact_id": "development:codex-runtime"},
        )
        deliveryline = await client.post(
            "/api/plugins/deliveryline/imports",
            json={"artifact_id": "development:deliveryline"},
        )

    assert codex.status_code == 409
    assert codex.json()["error"]["code"] == "system_upgrade_in_progress"
    assert deliveryline.status_code == 200
