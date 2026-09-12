import json
import zipfile

import httpx
import pytest

from app.application import create_app
import app.plugin_lifecycle.service as plugin_lifecycle_service
@pytest.mark.anyio
async def test_deliveryline_uses_shared_plugin_lifecycle(settings) -> None:
    app = create_app(settings)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        initial = await client.get("/api/plugins")
        imported = await client.post("/api/plugins/deliveryline/imports", json={"artifact_id": "development:deliveryline"})
        imported_navigation = await client.get("/settings/runtime")
        enabled = await client.put("/api/plugins/deliveryline/enabled", json={"artifact_id": "development:deliveryline", "enabled": True})
        removed = await client.delete("/api/plugins/deliveryline/imports/development%3Adeliveryline")
        removed_navigation = await client.get("/settings/runtime")
        page = await client.get("/settings/deliveryline")

    assert any(item["plugin_id"] == "deliveryline" for item in initial.json()["data"]["plugins"])
    deliveryline = next(item for item in initial.json()["data"]["plugins"] if item["plugin_id"] == "deliveryline")
    assert deliveryline["artifacts"][0]["description"] == "提供需求交付管理页面壳；当前不包含业务流程。"
    assert imported.json()["data"]["imported_artifact_ids"] == ["development:deliveryline"]
    assert 'href="/settings/deliveryline"' in imported_navigation.text
    assert enabled.json()["data"]["enabled_artifact_ids"] == ["development:deliveryline"]
    assert removed.json()["data"]["imported_artifact_ids"] == []
    assert 'href="/settings/deliveryline"' not in removed_navigation.text
    assert page.status_code == 200
    assert 'id="deliveryline-plugin-status"' in page.text
    assert 'id="deliveryline-plugin-version" data-settings-picker' in page.text


@pytest.mark.anyio
async def test_shared_lifecycle_updates_runtime_and_weixin_extensions(settings) -> None:
    transport = httpx.ASGITransport(app=create_app(settings))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        runtime_imported = await client.post(
            "/api/plugins/codex-runtime/imports",
            json={"artifact_id": "development:codex-runtime"},
        )
        runtime_enabled = await client.put(
            "/api/plugins/codex-runtime/enabled",
            json={"artifact_id": "development:codex-runtime", "enabled": True},
        )
        weixin_imported = await client.post(
            "/api/plugins/weixin-orchestration/imports",
            json={"artifact_id": "development:weixin-orchestration"},
        )
        weixin_enabled = await client.put(
            "/api/plugins/weixin-orchestration/enabled",
            json={"artifact_id": "development:weixin-orchestration", "enabled": True},
        )

    assert runtime_imported.status_code == 200
    assert "development:codex-runtime" in runtime_enabled.json()["data"]["enabled_artifact_ids"]
    assert weixin_imported.status_code == 200
    assert weixin_enabled.json()["data"]["enabled_artifact_ids"] == ["development:weixin-orchestration"]


@pytest.mark.anyio
async def test_deliveryline_zip_requires_manifest_and_keeps_import_state_when_artifact_is_missing(
    settings,
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(plugin_lifecycle_service, "PROJECT_ROOT", tmp_path)
    directory = tmp_path / "data/local/artifacts/plugins/deliveryline"
    directory.mkdir(parents=True)
    archive = directory / "deliveryline.zip"
    with zipfile.ZipFile(archive, "w") as package:
        package.writestr("chub-business-module.json", json.dumps({"module_id": "other"}))

    app = create_app(settings)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        rejected = await client.post(
            "/api/plugins/deliveryline/imports",
            json={"artifact_id": "zip:deliveryline.zip"},
        )
        with zipfile.ZipFile(archive, "w") as package:
            package.writestr(
                "chub-business-module.json",
                json.dumps({
                    "protocol_version": 1,
                    "module_id": "deliveryline",
                    "module_type": "business",
                    "display_name": "Deliveryline",
                    "description": "测试交付插件。",
                    "version": "1.0.0",
                    "chub_version": settings.app.version,
                }),
            )
        imported = await client.post(
            "/api/plugins/deliveryline/imports",
            json={"artifact_id": "zip:deliveryline.zip"},
        )
        archive.unlink()
        listed = await client.get("/api/plugins")
        unavailable_enabled = await client.put(
            "/api/plugins/deliveryline/enabled",
            json={"artifact_id": "zip:deliveryline.zip", "enabled": True},
        )
        navigation = await client.get("/settings/runtime")

    assert rejected.status_code == 422
    assert rejected.json()["error"]["code"] == "deliveryline_plugin_manifest_invalid"
    assert imported.status_code == 200
    assert imported.json()["data"]["artifacts"][1]["version"] == "1.0.0"
    deliveryline = next(item for item in listed.json()["data"]["plugins"] if item["plugin_id"] == "deliveryline")
    stale = next(item for item in deliveryline["artifacts"] if item["artifact_id"] == "zip:deliveryline.zip")
    assert deliveryline["imported_artifact_ids"] == ["zip:deliveryline.zip"]
    assert stale["available"] is False
    assert "当前不可用" in stale["reason"]
    assert unavailable_enabled.status_code == 409
    assert unavailable_enabled.json()["error"]["code"] == "plugin_artifact_unavailable"
    assert 'href="/settings/deliveryline"' in navigation.text
    saved_state = json.loads(app.state.plugin_lifecycle.path.read_text(encoding="utf-8"))
    assert saved_state["imports"]["deliveryline"] == ["zip:deliveryline.zip"]


@pytest.mark.anyio
async def test_extension_state_write_failure_is_reported_as_unconfirmed(settings, monkeypatch: pytest.MonkeyPatch) -> None:
    app = create_app(settings)

    def fail_write(_state) -> None:
        from app.core.response import ApiError

        raise ApiError(503, "plugin_lifecycle_state_unavailable", "插件生命周期状态不可写。")

    monkeypatch.setattr(app.state.plugin_lifecycle, "_write", fail_write)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/api/plugins/weixin-orchestration/imports",
            json={"artifact_id": "development:weixin-orchestration"},
        )

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "plugin_lifecycle_state_unconfirmed"
