import httpx
import pytest

from app.application import create_app


@pytest.mark.anyio
async def test_retired_weixin_text_settings_are_not_registered(settings) -> None:
    transport = httpx.ASGITransport(app=create_app(settings))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        current = await client.get("/api/settings/weixin-translation")
        update = await client.put(
            "/api/settings/weixin-translation",
            json={"mode": "confirm"},
        )
        orchestration = await client.get("/api/settings/weixin-task-orchestration")

    assert current.status_code == 404
    assert update.status_code == 404
    assert orchestration.status_code == 404
