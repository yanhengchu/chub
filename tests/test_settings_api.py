import httpx
import pytest
from tests.test_weixin_orchestration_plugins import module_archive
from unittest.mock import MagicMock

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
async def test_translation_settings_api_updates_model_and_level(settings) -> None:
    application = create_app(settings)
    application.state.ai_session_manager.validate_model = MagicMock()
    transport = httpx.ASGITransport(app=application)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.put(
            "/api/settings/weixin-translation",
            headers=authorization(settings),
            json={"model": "gpt-test", "reasoning_effort": "high"},
        )
        status = await client.get(
            "/api/settings/weixin-translation",
            headers=authorization(settings),
        )

    assert response.status_code == 200
    assert response.json()["data"]["model"] == "gpt-test"
    assert response.json()["data"]["reasoning_effort"] == "high"
    assert status.json()["data"]["model"] == "gpt-test"
    assert status.json()["data"]["reasoning_effort"] == "high"
    application.state.ai_session_manager.validate_model.assert_called_once_with(
        "gpt-test",
        "high",
    )


@pytest.mark.anyio
async def test_translation_settings_api_persists_internal_native_session_display(
    settings,
) -> None:
    transport = httpx.ASGITransport(app=create_app(settings))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        updated = await client.put(
            "/api/settings/weixin-translation",
            headers=authorization(settings),
            json={"show_internal_native_session": True},
        )

    assert updated.status_code == 200
    assert updated.json()["data"]["show_internal_native_session"] is True

    reloaded_transport = httpx.ASGITransport(app=create_app(settings))
    async with httpx.AsyncClient(
        transport=reloaded_transport,
        base_url="http://test",
    ) as client:
        reloaded = await client.get(
            "/api/settings/weixin-translation",
            headers=authorization(settings),
        )

    assert reloaded.json()["data"]["show_internal_native_session"] is True


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


@pytest.mark.anyio
async def test_task_orchestration_settings_persist_development_selection(settings) -> None:
    transport = httpx.ASGITransport(app=create_app(settings))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        initial = await client.get(
            "/api/settings/weixin-task-orchestration",
            headers=authorization(settings),
        )
        updated = await client.put(
            "/api/settings/weixin-task-orchestration",
            headers=authorization(settings),
            json={"implementation": "weixin-orchestration-dev"},
        )
        enabled = await client.put(
            "/api/settings/weixin-task-orchestration",
            headers=authorization(settings),
            json={"enabled": True},
        )

    assert initial.status_code == 200
    assert initial.json()["data"]["implementation"] == "disabled"
    assert initial.json()["data"]["enabled"] is False
    assert updated.status_code == 200
    assert updated.json()["data"]["implementation"] == "weixin-orchestration-dev"
    assert updated.json()["data"]["enabled"] is False
    assert updated.json()["data"]["development_available"] is True
    assert enabled.status_code == 200
    assert enabled.json()["data"]["enabled"] is True

    reloaded_transport = httpx.ASGITransport(app=create_app(settings))
    async with httpx.AsyncClient(
        transport=reloaded_transport,
        base_url="http://test",
    ) as client:
        reloaded = await client.get(
            "/api/settings/weixin-task-orchestration",
            headers=authorization(settings),
        )

    assert reloaded.json()["data"]["implementation"] == "weixin-orchestration-dev"
    assert reloaded.json()["data"]["enabled"] is True


@pytest.mark.anyio
async def test_clearing_orchestration_implementation_preserves_polish_mode(settings) -> None:
    transport = httpx.ASGITransport(app=create_app(settings))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        selected_mode = await client.put(
            "/api/settings/weixin-translation",
            headers=authorization(settings),
            json={"mode": "confirm"},
        )
        selected_implementation = await client.put(
            "/api/settings/weixin-task-orchestration",
            headers=authorization(settings),
            json={"implementation": "weixin-orchestration-dev"},
        )
        cleared = await client.put(
            "/api/settings/weixin-task-orchestration",
            headers=authorization(settings),
            json={"implementation": "disabled"},
        )
        reloaded_mode = await client.get(
            "/api/settings/weixin-translation",
            headers=authorization(settings),
        )

    assert selected_mode.json()["data"]["mode"] == "confirm"
    assert selected_implementation.json()["data"]["implementation"] == "weixin-orchestration-dev"
    assert cleared.json()["data"]["implementation"] == "disabled"
    assert cleared.json()["data"]["enabled"] is False
    assert reloaded_mode.json()["data"]["mode"] == "confirm"


@pytest.mark.anyio
async def test_task_orchestration_plugin_lifecycle_api(settings, tmp_path) -> None:
    settings.openclaw.weixin_chub_mode.orchestration_modules_dir = tmp_path / "modules"
    archive = module_archive(settings)
    transport = httpx.ASGITransport(app=create_app(settings))
    headers = {
        **authorization(settings),
        "X-Chub-Module-Filename": "weixin-refiner.zip",
    }
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        inspected = await client.post(
            "/api/settings/weixin-task-orchestration/modules/inspect",
            headers=headers,
            content=archive,
        )
        installed = await client.post(
            "/api/settings/weixin-task-orchestration/modules/install",
            headers=headers,
            content=archive,
        )
        module_ref = installed.json()["data"]["implementation_ref"]
        activated = await client.put(
            "/api/settings/weixin-task-orchestration",
            headers=authorization(settings),
            json={"implementation": "module", "module_ref": module_ref},
        )
        listed = await client.get(
            "/api/settings/weixin-task-orchestration/modules",
            headers=authorization(settings),
        )
        selected_mode = await client.put(
            "/api/settings/weixin-translation",
            headers=authorization(settings),
            json={"mode": "confirm"},
        )
        active_removal = await client.delete(
            f"/api/settings/weixin-task-orchestration/modules/{module_ref}",
            headers=authorization(settings),
        )
        reloaded = await client.get(
            "/api/settings/weixin-task-orchestration",
            headers=authorization(settings),
        )
        reloaded_mode = await client.get(
            "/api/settings/weixin-translation",
            headers=authorization(settings),
        )

    assert inspected.status_code == 200
    assert installed.status_code == 200
    assert activated.json()["data"]["implementation"] == "module"
    assert listed.json()["data"]["modules"][0]["active"] is True
    assert listed.json()["data"]["modules"][0]["removable"] is True
    assert selected_mode.json()["data"]["mode"] == "confirm"
    assert active_removal.status_code == 200
    assert reloaded.json()["data"]["implementation"] == "disabled"
    assert reloaded.json()["data"]["enabled"] is False
    assert reloaded_mode.json()["data"]["mode"] == "confirm"


@pytest.mark.anyio
async def test_task_orchestration_settings_rejects_unrelated_module_reference(settings) -> None:
    transport = httpx.ASGITransport(app=create_app(settings))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.put(
            "/api/settings/weixin-task-orchestration",
            headers=authorization(settings),
            json={"implementation": "weixin-orchestration-dev", "module_ref": "x" * 68},
        )

    assert response.status_code == 422
