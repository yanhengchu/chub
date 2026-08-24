import httpx
import pytest

from app.application import create_app


def authorization(settings) -> dict[str, str]:
    return {}


@pytest.mark.anyio
async def test_translation_settings_api_rejects_untrusted_network(settings) -> None:
    transport = httpx.ASGITransport(app=create_app(settings))
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://test",
        trust_env=False,
    ) as client:
        response = await client.get("/api/settings/weixin-translation")

    assert response.status_code == 200


@pytest.mark.anyio
async def test_translation_settings_api_updates_and_persists_node_state(
    settings,
) -> None:
    settings.openclaw.weixin_chub_mode.translation_enabled = False
    transport = httpx.ASGITransport(app=create_app(settings))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        initial = await client.get(
            "/api/settings/weixin-translation",
            headers=authorization(settings),
        )
        updated = await client.put(
            "/api/settings/weixin-translation",
            headers=authorization(settings),
            json={"enabled": True},
        )

    assert initial.status_code == 200
    assert initial.json()["data"]["enabled"] is False
    assert updated.status_code == 200
    assert updated.json()["data"]["enabled"] is True

    reloaded_transport = httpx.ASGITransport(app=create_app(settings))
    async with httpx.AsyncClient(
        transport=reloaded_transport,
        base_url="http://test",
    ) as client:
        reloaded = await client.get(
            "/api/settings/weixin-translation",
            headers=authorization(settings),
        )

    assert reloaded.json()["data"]["enabled"] is True


@pytest.mark.anyio
async def test_translation_settings_api_supports_confirmation_mode(settings) -> None:
    transport = httpx.ASGITransport(app=create_app(settings))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.put(
            "/api/settings/weixin-translation",
            headers=authorization(settings),
            json={"mode": "confirm"},
        )

    assert response.status_code == 200
    assert response.json()["data"]["mode"] == "confirm"
    assert response.json()["data"]["enabled"] is True


@pytest.mark.anyio
async def test_translation_settings_api_fails_closed_for_invalid_state(
    settings,
) -> None:
    state_file = settings.openclaw.weixin_chub_mode.state_file.with_name(
        "weixin-translation.json"
    )
    state_file.parent.mkdir(parents=True, exist_ok=True)
    state_file.write_bytes(b"\xff\xfe")
    transport = httpx.ASGITransport(app=create_app(settings))

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(
            "/api/settings/weixin-translation",
            headers=authorization(settings),
        )

    assert response.status_code == 503
    assert response.json()["error"]["code"] == (
        "weixin_translation_settings_unavailable"
    )
