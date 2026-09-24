import httpx
import pytest

from app.application import create_app


PLUGIN_ID = "chub-task-prompt-optimizer"


def _client(app):
    transport = httpx.ASGITransport(app=app)
    client = httpx.AsyncClient(transport=transport, base_url="http://test")
    return client


@pytest.mark.anyio
async def test_orchestration_enablement_waits_for_web_assembly_then_reports_loaded(settings) -> None:
    app = create_app(settings)
    async with _client(app) as client:
        listed = (await client.get("/api/plugins")).json()["data"]["plugins"]
        plugin = next(item for item in listed if item["plugin_id"] == PLUGIN_ID)
        assert plugin["name"] == "任务编排插件"
        artifact_id = next(item["artifact_id"] for item in plugin["artifacts"] if item["source"] == "development")
        artifact = next(item for item in plugin["artifacts"] if item["artifact_id"] == artifact_id)
        assert artifact["name"] == "任务提示词优化"
        assert artifact["load_state"] == "not_loaded"
        assert PLUGIN_ID not in app.state.orchestration_plugins

        rejected = await client.put(
            f"/api/plugins/{PLUGIN_ID}/enabled",
            json={"artifact_id": artifact_id, "enabled": True},
        )
        assert rejected.status_code == 409
        assert rejected.json()["error"]["code"] == "plugin_not_imported"
        zip_rejected = await client.put(
            f"/api/plugins/{PLUGIN_ID}/enabled",
            json={"artifact_id": "zip:deferred.zip", "enabled": True},
        )
        assert zip_rejected.status_code == 409
        assert zip_rejected.json()["error"]["code"] == "plugin_artifact_lifecycle_unavailable"

        imported = await client.post(
            f"/api/plugins/{PLUGIN_ID}/imports",
            json={"artifact_id": artifact_id},
        )
        assert imported.status_code == 200
        assert imported.json()["data"]["enabled_artifact_ids"] == []

        enabled = await client.put(
            f"/api/plugins/{PLUGIN_ID}/enabled",
            json={"artifact_id": artifact_id, "enabled": True},
        )
        enabled_data = enabled.json()["data"]
        enabled_artifact = next(item for item in enabled_data["artifacts"] if item["artifact_id"] == artifact_id)
        assert enabled_data["enabled_artifact_ids"] == [artifact_id]
        assert enabled_artifact["load_state"] == "pending_reload"
        assert enabled_artifact["reload_required"] is True
        assert PLUGIN_ID not in app.state.orchestration_plugins

    reloaded_app = create_app(settings)
    assert PLUGIN_ID in reloaded_app.state.orchestration_plugins
    assert reloaded_app.state.orchestration_plugins[PLUGIN_ID].stage_kinds == ()
    async with _client(reloaded_app) as client:
        listed = (await client.get("/api/plugins")).json()["data"]["plugins"]
        plugin = next(item for item in listed if item["plugin_id"] == PLUGIN_ID)
        artifact = next(item for item in plugin["artifacts"] if item["artifact_id"] == artifact_id)
        assert plugin["loaded_artifact_ids"] == [artifact_id]
        assert artifact["load_state"] == "loaded"
        assert artifact["reload_required"] is False

        disabled = await client.put(
            f"/api/plugins/{PLUGIN_ID}/enabled",
            json={"artifact_id": artifact_id, "enabled": False},
        )
        disabled_data = disabled.json()["data"]
        disabled_artifact = next(item for item in disabled_data["artifacts"] if item["artifact_id"] == artifact_id)
        assert disabled_data["enabled_artifact_ids"] == []
        assert disabled_data["loaded_artifact_ids"] == [artifact_id]
        assert disabled_artifact["load_state"] == "loaded"
        assert disabled_artifact["reload_required"] is True


@pytest.mark.anyio
async def test_prompt_optimizer_settings_navigation_follows_import_lifecycle(settings) -> None:
    app = create_app(settings)
    async with _client(app) as client:
        before = await client.get("/settings/runtime")
        assert 'class="settings-navigation-subgroup">任务编排插件</span>' not in before.text
        unavailable = await client.get(f"/settings/{PLUGIN_ID}")
        assert unavailable.status_code == 404

        plugin = next(
            item for item in (await client.get("/api/plugins")).json()["data"]["plugins"]
            if item["plugin_id"] == PLUGIN_ID
        )
        artifact_id = next(
            item["artifact_id"] for item in plugin["artifacts"]
            if item["source"] == "development"
        )
        imported = await client.post(
            f"/api/plugins/{PLUGIN_ID}/imports",
            json={"artifact_id": artifact_id},
        )
        assert imported.status_code == 200

        manager = await client.get("/settings/runtime")
        settings_page = await client.get(f"/settings/{PLUGIN_ID}")
        assert 'class="settings-navigation-subgroup">任务编排插件</span>' in manager.text
        assert f'href="/settings/{PLUGIN_ID}"' in manager.text
        assert settings_page.status_code == 200
        assert "提示词处理模式" in settings_page.text
        assert "auto" in settings_page.text
        assert "当前阶段链尚未接入" not in settings_page.text
        assert 'id="prompt-optimizer-mode-availability"' not in settings_page.text

        removed = await client.delete(f"/api/plugins/{PLUGIN_ID}/imports/{artifact_id}")
        assert removed.status_code == 200
        after = await client.get("/settings/runtime")
        assert 'class="settings-navigation-subgroup">任务编排插件</span>' not in after.text
        assert (await client.get(f"/settings/{PLUGIN_ID}")).status_code == 404


@pytest.mark.anyio
async def test_prompt_optimizer_settings_are_persisted_and_auto_remains_unavailable(settings) -> None:
    app = create_app(settings)
    async with _client(app) as client:
        unavailable = await client.get(f"/api/plugins/{PLUGIN_ID}/settings")
        assert unavailable.status_code == 404

        plugin = next(
            item for item in (await client.get("/api/plugins")).json()["data"]["plugins"]
            if item["plugin_id"] == PLUGIN_ID
        )
        artifact_id = next(
            item["artifact_id"] for item in plugin["artifacts"]
            if item["source"] == "development"
        )
        imported = await client.post(
            f"/api/plugins/{PLUGIN_ID}/imports",
            json={"artifact_id": artifact_id},
        )
        assert imported.status_code == 200

        initial = await client.get(f"/api/plugins/{PLUGIN_ID}/settings")
        assert initial.status_code == 200
        initial_data = initial.json()["data"]
        assert initial_data["mode"] == "direct"
        assert initial_data["is_default"] is True
        assert initial_data["auto_available"] is False
        assert initial_data["effective"] is False

        saved = await client.put(
            f"/api/plugins/{PLUGIN_ID}/settings",
            json={"mode": "direct"},
        )
        assert saved.status_code == 200
        assert saved.json()["data"]["is_default"] is False

        rejected = await client.put(
            f"/api/plugins/{PLUGIN_ID}/settings",
            json={"mode": "auto"},
        )
        assert rejected.status_code == 409
        assert rejected.json()["error"]["code"] == "prompt_optimizer_auto_unavailable"
        unchanged = await client.get(f"/api/plugins/{PLUGIN_ID}/settings")
        assert unchanged.json()["data"]["mode"] == "direct"

    reloaded_app = create_app(settings)
    async with _client(reloaded_app) as client:
        persisted = await client.get(f"/api/plugins/{PLUGIN_ID}/settings")
        assert persisted.status_code == 200
        assert persisted.json()["data"]["mode"] == "direct"
        assert persisted.json()["data"]["is_default"] is False


@pytest.mark.anyio
async def test_enabled_orchestration_plugin_can_be_removed_and_reports_pending_unload(settings) -> None:
    app = create_app(settings)
    async with _client(app) as client:
        plugins = (await client.get("/api/plugins")).json()["data"]["plugins"]
        plugin = next(item for item in plugins if item["plugin_id"] == PLUGIN_ID)
        artifact_id = next(item["artifact_id"] for item in plugin["artifacts"] if item["source"] == "development")
        imported = await client.post(
            f"/api/plugins/{PLUGIN_ID}/imports",
            json={"artifact_id": artifact_id},
        )
        assert imported.status_code == 200
        enabled = await client.put(
            f"/api/plugins/{PLUGIN_ID}/enabled",
            json={"artifact_id": artifact_id, "enabled": True},
        )
        enabled_artifact = next(
            item for item in enabled.json()["data"]["artifacts"]
            if item["artifact_id"] == artifact_id
        )
        assert enabled_artifact["removable"] is True

    loaded_app = create_app(settings)
    assert PLUGIN_ID in loaded_app.state.orchestration_plugins
    async with _client(loaded_app) as client:
        removed = await client.delete(f"/api/plugins/{PLUGIN_ID}/imports/{artifact_id}")
        assert removed.status_code == 200
        data = removed.json()["data"]
        assert data["imported_artifact_ids"] == []
        assert data["enabled_artifact_ids"] == []
        artifact = next(item for item in data["artifacts"] if item["artifact_id"] == artifact_id)
        assert artifact["imported"] is False
        assert artifact["enabled"] is False
        assert artifact["loaded"] is True
        assert artifact["reload_required"] is True
        assert artifact["load_state"] == "loaded"


@pytest.mark.anyio
async def test_orchestration_load_failure_is_reported_without_blocking_plugin_api(settings, monkeypatch) -> None:
    from app.plugin_lifecycle.orchestration_loader import OrchestrationPluginLoadResult

    app = create_app(settings)
    async with _client(app) as client:
        plugin = next(
            item for item in (await client.get("/api/plugins")).json()["data"]["plugins"]
            if item["plugin_id"] == PLUGIN_ID
        )
        artifact_id = next(item["artifact_id"] for item in plugin["artifacts"] if item["source"] == "development")
        await client.post(f"/api/plugins/{PLUGIN_ID}/imports", json={"artifact_id": artifact_id})
        await client.put(f"/api/plugins/{PLUGIN_ID}/enabled", json={"artifact_id": artifact_id, "enabled": True})

    monkeypatch.setattr(
        "app.plugin_lifecycle.service.load_development_orchestration_plugin",
        lambda source, artifact, version: OrchestrationPluginLoadResult(
            artifact, "failed", reason="测试装载失败。"
        ),
    )
    failed_app = create_app(settings)
    async with _client(failed_app) as client:
        plugins = (await client.get("/api/plugins")).json()["data"]["plugins"]
        plugin = next(item for item in plugins if item["plugin_id"] == PLUGIN_ID)
        artifact = next(item for item in plugin["artifacts"] if item["artifact_id"] == artifact_id)
        assert artifact["load_state"] == "failed"
        assert artifact["load_reason"] == "测试装载失败。"
        health = await client.get("/api/health")
        assert health.status_code == 200
