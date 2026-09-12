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
