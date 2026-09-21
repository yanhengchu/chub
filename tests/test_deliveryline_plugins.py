import json
import zipfile

import httpx
import pytest

from app.application import create_app
from scripts.build.deliveryline_plugin_zip import build as build_deliveryline_plugin_zip


def test_deliveryline_formal_zip_build_uses_the_current_chub_version(tmp_path) -> None:
    archive_path = tmp_path / "deliveryline-release-1.2.3.zip"

    built = build_deliveryline_plugin_zip(archive_path, version="1.2.3")

    assert built == archive_path
    with zipfile.ZipFile(built) as archive:
        manifest = json.loads(archive.read("chub-business-module.json"))
    assert manifest["version"] == "1.2.3"
    assert manifest["chub_version"]


@pytest.mark.anyio
async def test_deliveryline_uses_shared_plugin_lifecycle(settings) -> None:
    transport = httpx.ASGITransport(app=create_app(settings))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        imported = await client.post(
            "/api/plugins/deliveryline/imports",
            json={"artifact_id": "development:deliveryline"},
        )
        enabled = await client.put(
            "/api/plugins/deliveryline/enabled",
            json={"artifact_id": "development:deliveryline", "enabled": True},
        )
        removed = await client.delete(
            "/api/plugins/deliveryline/imports/development%3Adeliveryline"
        )

    assert imported.json()["data"]["imported_artifact_ids"] == ["development:deliveryline"]
    assert enabled.json()["data"]["enabled_artifact_ids"] == ["development:deliveryline"]
    assert removed.json()["data"]["imported_artifact_ids"] == []


@pytest.mark.anyio
async def test_deliveryline_zip_rejects_an_invalid_manifest(settings, tmp_path, monkeypatch) -> None:
    monkeypatch.setattr("app.plugin_lifecycle.service.PROJECT_ROOT", tmp_path)
    directory = tmp_path / "data/local/artifacts/plugins/deliveryline"
    directory.mkdir(parents=True)
    with zipfile.ZipFile(directory / "deliveryline.zip", "w") as package:
        package.writestr("chub-business-module.json", json.dumps({"module_id": "other"}))

    transport = httpx.ASGITransport(app=create_app(settings))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/api/plugins/deliveryline/imports",
            json={"artifact_id": "zip:deliveryline.zip"},
        )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "deliveryline_plugin_manifest_invalid"
