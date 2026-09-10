from __future__ import annotations

import json
import zipfile
from datetime import datetime, timezone
from pathlib import Path

import httpx
import pytest

from app.application import create_app
from app.services.deployment_package import (
    DeploymentPackageConfiguration,
    DeploymentPackageService,
)


class _DeferredThread:
    calls: list[tuple[object, tuple[object, ...], dict[str, object]]] = []

    def __init__(self, *, target, args, **kwargs) -> None:
        self.calls.append((target, args, kwargs))

    def start(self) -> None:
        return None


def test_release_build_contains_formal_modules_and_excludes_local_state(
    settings,
    tmp_path: Path,
) -> None:
    settings.deployment_package.artifacts_dir = tmp_path / "releases"
    settings.deployment_package.state_file = tmp_path / "state.json"
    service = DeploymentPackageService(settings)
    configuration = DeploymentPackageConfiguration(
        chub_release_version="1.2.3",
        runtime_implementation_id="codex-010001",
        runtime_release_version="2.0.0",
        runtime_description="正式 Runtime。",
        weixin_release_version="2.0.0",
        include_development_sources=False,
    )

    artifact, digest = service._build(configuration)

    assert artifact.parent == settings.deployment_package.artifacts_dir
    assert len(digest) == 64
    with zipfile.ZipFile(artifact) as archive:
        names = set(archive.namelist())
        assert "bundled-modules/codex-runtime.zip" in names
        assert "bundled-modules/weixin-refinement.zip" in names
        assert "DEPLOY_WITH_AI.md" in names
        assert "runtime-modules/codex-runtime/chub-module.json" not in names
        manifest = json.loads(archive.read("release-manifest.json"))
        assert manifest["chub_release_version"] == "1.2.3"
        assert manifest["include_development_sources"] is False


def test_release_configuration_persists_without_changing_app_version(
    settings,
    tmp_path: Path,
) -> None:
    settings.deployment_package.artifacts_dir = tmp_path / "releases"
    settings.deployment_package.state_file = tmp_path / "state.json"
    service = DeploymentPackageService(settings)
    configuration = DeploymentPackageConfiguration(
        chub_release_version="9.9.9",
        runtime_implementation_id="codex-010002",
        runtime_release_version="3.0.0",
        runtime_description="下一次发布。",
        weixin_release_version="3.0.0",
        include_development_sources=True,
    )

    saved = service.save_configuration(configuration)

    assert saved.app_version == settings.app.version
    assert saved.configuration == configuration
    assert json.loads(settings.deployment_package.state_file.read_text())["configuration"]["chub_release_version"] == "9.9.9"


def test_release_build_optionally_includes_development_sources(settings, tmp_path: Path) -> None:
    settings.deployment_package.artifacts_dir = tmp_path / "releases"
    settings.deployment_package.state_file = tmp_path / "state.json"
    service = DeploymentPackageService(settings)
    artifact, _ = service._build(
        DeploymentPackageConfiguration(
            chub_release_version="1.0.0",
            runtime_implementation_id="codex-010004",
            runtime_release_version="1.0.0",
            runtime_description="开发源码构建。",
            weixin_release_version="1.0.0",
            include_development_sources=True,
        )
    )

    with zipfile.ZipFile(artifact) as archive:
        assert "runtime-modules/codex-runtime/chub-module.json" in archive.namelist()
        assert "orchestration-modules/weixin-refinement/chub-capability-orchestration.json" in archive.namelist()


@pytest.mark.anyio
async def test_deployment_package_settings_api_reads_and_updates_configuration(
    settings,
    tmp_path: Path,
) -> None:
    settings.deployment_package.artifacts_dir = tmp_path / "releases"
    settings.deployment_package.state_file = tmp_path / "state.json"
    transport = httpx.ASGITransport(app=create_app(settings))
    payload = {
        "chub_release_version": "4.0.0",
        "runtime_release_version": "4.0.0",
        "weixin_release_version": "4.0.0",
        "include_development_sources": True,
    }

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        before = await client.get("/api/settings/deployment-package")
        updated = await client.put("/api/settings/deployment-package", json=payload)

    assert before.status_code == 200
    assert before.json()["data"]["app_version"] == settings.app.version
    assert updated.status_code == 200
    configuration = updated.json()["data"]["configuration"]
    assert {key: configuration[key] for key in payload} == payload
    assert configuration["runtime_implementation_id"] == "codex-010000"
    assert "AI Session" in configuration["runtime_description"]


def test_release_build_snapshots_configuration_at_start(
    settings,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings.deployment_package.artifacts_dir = tmp_path / "releases"
    settings.deployment_package.state_file = tmp_path / "state.json"
    service = DeploymentPackageService(settings)
    initial = service.status().configuration.model_copy(
        update={"chub_release_version": "1.0.0"}
    )
    service.save_configuration(initial)
    _DeferredThread.calls = []
    monkeypatch.setattr("app.services.deployment_package.threading.Thread", _DeferredThread)

    service.start(source_ip="127.0.0.1")
    service.save_configuration(initial.model_copy(update={"chub_release_version": "2.0.0"}))

    assert _DeferredThread.calls[0][1][2].chub_release_version == "1.0.0"


def test_interrupted_release_build_is_closed_and_can_be_retried(
    settings,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings.deployment_package.artifacts_dir = tmp_path / "releases"
    settings.deployment_package.state_file = tmp_path / "state.json"
    service = DeploymentPackageService(settings)
    configuration = service.status().configuration
    settings.deployment_package.state_file.write_text(
        json.dumps(
            {
                "configuration": configuration.model_dump(),
                "operation": {
                    "operation_id": "interrupted-build",
                    "status": "started",
                    "message": "正在构建。",
                    "started_at": datetime.now(timezone.utc).isoformat(),
                },
            }
        )
    )

    recovered = DeploymentPackageService(settings).status()

    assert recovered.operation is not None
    assert recovered.operation.status == "failed"
    assert "中断" in recovered.operation.message
    _DeferredThread.calls = []
    monkeypatch.setattr("app.services.deployment_package.threading.Thread", _DeferredThread)
    retried = DeploymentPackageService(settings).start(source_ip="127.0.0.1")
    assert retried.operation is not None
    assert retried.operation.status == "requested"
