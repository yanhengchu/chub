import json
import shutil
import zipfile

import httpx
import pytest

from app.ai_runtime.runtime_plugin_packages import RuntimePluginService
from app.application import create_app
from app.quick_worker import QuickWorkerServer, read_health
import app.plugin_lifecycle.service as plugin_lifecycle_service
from scripts.build.codex_runtime_zip import build as build_codex_runtime_zip
from scripts.build.deliveryline_plugin_zip import build as build_deliveryline_plugin_zip
from scripts.build.weixin_orchestration_plugin_zip import (
    build as build_weixin_orchestration_plugin_zip,
)


def test_deliveryline_formal_zip_build_uses_the_current_chub_version(tmp_path) -> None:
    archive_path = tmp_path / "deliveryline-release-1.2.3.zip"

    built = build_deliveryline_plugin_zip(archive_path, version="1.2.3")

    assert built == archive_path
    assert (built.stat().st_mode & 0o777) == 0o600
    with zipfile.ZipFile(built) as archive:
        manifest = json.loads(
            archive.read("chub-business-module.json").decode("utf-8")
        )
        assert manifest["version"] == "1.2.3"
        assert manifest["chub_version"]
        assert archive.namelist() == ["chub-business-module.json"]


@pytest.mark.anyio
async def test_plugin_lifecycle_discovers_bundled_release_archives_only_when_present(
    settings,
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(plugin_lifecycle_service, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr("app.core.module_sources.PROJECT_ROOT", tmp_path)
    bundled = tmp_path / "bundled-modules"
    bundled.mkdir()
    for name in (
        "codex-runtime-release-1.0.0.zip",
        "weixin-refinement-release-1.0.0.zip",
    ):
        with zipfile.ZipFile(bundled / name, "w"):
            pass

    app = create_app(settings)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/api/plugins")

    assert response.status_code == 200
    plugins = {item["plugin_id"]: item for item in response.json()["data"]["plugins"]}
    assert "bundled:codex-runtime-release-1.0.0.zip" in {
        item["artifact_id"] for item in plugins["runtime"]["artifacts"]
    }
    assert "bundled:weixin-refinement-release-1.0.0.zip" in {
        item["artifact_id"] for item in plugins["weixin-orchestration"]["artifacts"]
    }
    assert plugins["deliveryline"]["artifacts"] == []


@pytest.mark.anyio
async def test_plugin_lifecycle_keeps_bundled_archive_selectable_when_local_name_matches(
    settings,
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(plugin_lifecycle_service, "PROJECT_ROOT", tmp_path)
    filename = "codex-runtime-release-1.0.0.zip"
    bundled = tmp_path / "bundled-modules"
    local = tmp_path / "data/local/artifacts/plugins/runtime"
    bundled.mkdir()
    local.mkdir(parents=True)
    with zipfile.ZipFile(bundled / filename, "w") as package:
        package.writestr("chub-module.json", "{}")
    (local / filename).write_text("not-a-zip", encoding="utf-8")

    app = create_app(settings)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/api/plugins")

    artifacts = next(
        item for item in response.json()["data"]["plugins"]
        if item["plugin_id"] == "runtime"
    )["artifacts"]
    assert {item["artifact_id"] for item in artifacts} >= {
        f"zip:{filename}",
        f"bundled:{filename}",
    }


@pytest.mark.anyio
async def test_plugin_lifecycle_imports_valid_bundled_runtime_archive(
    settings,
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(plugin_lifecycle_service, "PROJECT_ROOT", tmp_path)
    # The shared settings fixture installs one formal Runtime for general tests.
    # This case models a fresh release deployment before the bundled ZIP is imported.
    existing = RuntimePluginService(settings).remove(
        "codex-010000",
        operation_id="0" * 32,
    )
    RuntimePluginService(settings).finalize_removal(existing)
    bundled = tmp_path / "bundled-modules"
    bundled.mkdir()
    archive_path = bundled / "codex-runtime-release-1.0.1.zip"
    build_codex_runtime_zip(
        archive_path,
        implementation_id="codex-010001",
        version="1.0.1",
        description="测试正式随包 Codex Runtime。",
        chub_version=settings.app.version,
    )
    workspace = tmp_path / "worker-workspace"
    workspace.mkdir()
    executable = tmp_path / "codex"
    executable.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    executable.chmod(0o700)
    worker = QuickWorkerServer(
        settings,
        codex_workspaces={"home": workspace},
        codex_executable=executable,
        codex_home=tmp_path / "codex-home",
    )
    await worker.start()
    app = create_app(settings)
    transport = httpx.ASGITransport(app=app)
    try:
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            imported = await client.post(
                "/api/plugins/runtime/imports",
                json={"artifact_id": "bundled:codex-runtime-release-1.0.1.zip"},
            )
        health = await read_health(settings)
    finally:
        await worker.close()

    assert imported.status_code == 200
    assert imported.json()["data"]["imported_artifact_ids"] == ["runtime:codex-010001"]
    assert "codex-010001" in app.state.ai_session_manager.runtime_plugins.implementation_ids()
    assert "codex-010001" in health["data"]["implementation_ids"]
    assert "codex-010001" in health["data"]["available_implementation_ids"]
    assert app.state.plugin_lifecycle.runtime_implementation_lifecycle_state(
        "codex-010001"
    ) == (True, False)


@pytest.mark.anyio
async def test_plugin_lifecycle_imports_and_enables_valid_bundled_weixin_archive(
    settings,
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(plugin_lifecycle_service, "PROJECT_ROOT", tmp_path)
    bundled = tmp_path / "bundled-modules"
    bundled.mkdir()
    filename = "weixin-refinement-release-1.0.1.zip"
    build_weixin_orchestration_plugin_zip(
        bundled / filename,
        version="1.0.1",
        chub_version=settings.app.version,
    )
    app = create_app(settings)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        imported = await client.post(
            "/api/plugins/weixin-orchestration/imports",
            json={"artifact_id": f"bundled:{filename}"},
        )
        artifact_id = imported.json()["data"]["imported_artifact_ids"][0]
        enabled = await client.put(
            f"/api/plugins/weixin-orchestration/enabled",
            json={"artifact_id": artifact_id, "enabled": True},
        )
        listed = await client.get("/api/plugins")

    assert imported.status_code == 200
    assert artifact_id.startswith("orchestration:weixin-refinement@1.0.1+")
    assert enabled.status_code == 200
    assert enabled.json()["data"]["enabled_artifact_ids"] == [artifact_id]
    orchestration = next(
        item
        for item in listed.json()["data"]["plugins"]
        if item["plugin_id"] == "weixin-orchestration"
    )
    assert f"bundled:{filename}" not in {
        item["artifact_id"] for item in orchestration["artifacts"]
    }
    assert artifact_id in {item["artifact_id"] for item in orchestration["artifacts"]}


@pytest.mark.anyio
async def test_plugin_lifecycle_keeps_new_bundled_weixin_candidate_with_same_module_id(
    settings,
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(plugin_lifecycle_service, "PROJECT_ROOT", tmp_path)
    settings.openclaw.weixin_chub_mode.orchestration_modules_dir = (
        tmp_path / "installed-orchestration-modules"
    )
    bundled = tmp_path / "bundled-modules"
    bundled.mkdir()
    imported_filename = "weixin-refinement-release-2.0.0.zip"
    update_filename = "weixin-refinement-release-2.0.1.zip"
    for filename, version in ((imported_filename, "2.0.0"), (update_filename, "2.0.1")):
        build_weixin_orchestration_plugin_zip(
            bundled / filename,
            version=version,
            chub_version=settings.app.version,
        )

    app = create_app(settings)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        imported = await client.post(
            "/api/plugins/weixin-orchestration/imports",
            json={"artifact_id": f"bundled:{imported_filename}"},
        )
        listed = await client.get("/api/plugins")

    assert imported.status_code == 200
    orchestration = next(
        item
        for item in listed.json()["data"]["plugins"]
        if item["plugin_id"] == "weixin-orchestration"
    )
    artifact_ids = {item["artifact_id"] for item in orchestration["artifacts"]}
    assert f"bundled:{imported_filename}" not in artifact_ids
    assert f"bundled:{update_filename}" in artifact_ids


@pytest.mark.anyio
async def test_plugin_lifecycle_keeps_same_version_bundled_weixin_candidate_when_content_differs(
    settings,
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(plugin_lifecycle_service, "PROJECT_ROOT", tmp_path)
    settings.openclaw.weixin_chub_mode.orchestration_modules_dir = (
        tmp_path / "installed-orchestration-modules"
    )
    bundled = tmp_path / "bundled-modules"
    bundled.mkdir()
    imported_filename = "weixin-refinement-release-3.0.0.zip"
    changed_filename = "weixin-refinement-release-3.0.0-rebuilt.zip"
    for filename in (imported_filename, changed_filename):
        build_weixin_orchestration_plugin_zip(
            bundled / filename,
            version="3.0.0",
            chub_version=settings.app.version,
        )
    with zipfile.ZipFile(bundled / changed_filename, "a") as package:
        package.writestr("release-notes.txt", "rebuilt package")

    app = create_app(settings)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        imported = await client.post(
            "/api/plugins/weixin-orchestration/imports",
            json={"artifact_id": f"bundled:{imported_filename}"},
        )
        listed = await client.get("/api/plugins")

    assert imported.status_code == 200
    orchestration = next(
        item
        for item in listed.json()["data"]["plugins"]
        if item["plugin_id"] == "weixin-orchestration"
    )
    assert f"bundled:{changed_filename}" in {
        item["artifact_id"] for item in orchestration["artifacts"]
    }


@pytest.mark.anyio
async def test_plugin_lifecycle_keeps_bundled_weixin_candidate_when_imported_artifact_is_unavailable(
    settings,
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(plugin_lifecycle_service, "PROJECT_ROOT", tmp_path)
    settings.openclaw.weixin_chub_mode.orchestration_modules_dir = (
        tmp_path / "installed-orchestration-modules"
    )
    bundled = tmp_path / "bundled-modules"
    bundled.mkdir()
    filename = "weixin-refinement-release-4.0.0.zip"
    build_weixin_orchestration_plugin_zip(
        bundled / filename,
        version="4.0.0",
        chub_version=settings.app.version,
    )

    app = create_app(settings)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        imported = await client.post(
            "/api/plugins/weixin-orchestration/imports",
            json={"artifact_id": f"bundled:{filename}"},
        )
        implementation_ref = imported.json()["data"]["imported_artifact_ids"][0].removeprefix(
            "orchestration:"
        )
        content_hash = implementation_ref.rsplit("+", 1)[1]
        shutil.rmtree(
            app.state.weixin_chub_mode.orchestration_plugin_service.artifacts_dir / content_hash
        )
        listed = await client.get("/api/plugins")

    assert imported.status_code == 200
    orchestration = next(
        item
        for item in listed.json()["data"]["plugins"]
        if item["plugin_id"] == "weixin-orchestration"
    )
    assert f"bundled:{filename}" in {
        item["artifact_id"] for item in orchestration["artifacts"]
    }


@pytest.mark.anyio
async def test_plugin_lifecycle_marks_invalid_bundled_weixin_archive_unavailable(
    settings,
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(plugin_lifecycle_service, "PROJECT_ROOT", tmp_path)
    settings.openclaw.weixin_chub_mode.orchestration_modules_dir = (
        tmp_path / "installed-orchestration-modules"
    )
    bundled = tmp_path / "bundled-modules"
    bundled.mkdir()
    filename = "weixin-refinement-release-invalid.zip"
    with zipfile.ZipFile(bundled / filename, "w") as package:
        package.writestr("unexpected.txt", "not a plugin")

    app = create_app(settings)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        listed = await client.get("/api/plugins")

    assert listed.status_code == 200
    orchestration = next(
        item
        for item in listed.json()["data"]["plugins"]
        if item["plugin_id"] == "weixin-orchestration"
    )
    candidate = next(
        item
        for item in orchestration["artifacts"]
        if item["artifact_id"] == f"bundled:{filename}"
    )
    assert candidate["available"] is False
    assert candidate["reason"].startswith("随包 ZIP 不可导入：")


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
    assert deliveryline["artifacts"][0]["description"] == "提供需求提出档案、评审前校验与归档查看；后续交付阶段尚未接入。"
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
            "/api/plugins/runtime/imports",
            json={"artifact_id": "development:codex-runtime-dev"},
        )
        runtime_enabled = await client.put(
            "/api/plugins/runtime/enabled",
            json={"artifact_id": "development:codex-runtime-dev", "enabled": True},
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
    assert "development:codex-runtime-dev" in runtime_enabled.json()["data"]["enabled_artifact_ids"]
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
    assert next(
        item
        for item in imported.json()["data"]["artifacts"]
        if item["artifact_id"] == "zip:deliveryline.zip"
    )["version"] == "1.0.0"
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
