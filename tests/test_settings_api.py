import httpx
import pytest

from app.application import create_app


@pytest.mark.anyio
async def test_translation_settings_remain_available_without_legacy_plugin_routes(settings) -> None:
    transport = httpx.ASGITransport(app=create_app(settings))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        updated = await client.put("/api/settings/weixin-translation", json={"mode": "confirm"})
        legacy = await client.get("/api/settings/weixin-task-orchestration")
    assert updated.status_code == 200
    assert updated.json()["data"]["mode"] == "confirm"
    assert legacy.status_code == 404


@pytest.mark.anyio
async def test_translation_settings_reject_removed_enabled_switch(settings) -> None:
    transport = httpx.ASGITransport(app=create_app(settings))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.put(
            "/api/settings/weixin-translation",
            json={"enabled": True},
        )

    assert response.status_code == 422


@pytest.mark.anyio
async def test_translation_settings_reject_empty_or_mixed_updates(settings) -> None:
    transport = httpx.ASGITransport(app=create_app(settings))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        empty = await client.put("/api/settings/weixin-translation", json={})
        mixed = await client.put(
            "/api/settings/weixin-translation",
            json={"mode": "auto", "model": "gpt-test"},
        )

    assert empty.status_code == 422
    assert mixed.status_code == 422


@pytest.mark.anyio
async def test_translation_runtime_is_read_only_from_session_defaults(settings) -> None:
    transport = httpx.ASGITransport(app=create_app(settings))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        current = await client.get("/api/settings/weixin-translation")
        updated = await client.put(
            "/api/settings/weixin-translation",
            json={"runtime_id": "codex"},
        )
    assert current.status_code == 200
    assert current.json()["data"]["runtime_id"] == "codex"
    assert updated.status_code == 422
