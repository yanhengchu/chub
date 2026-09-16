from __future__ import annotations

import json
import os
import subprocess
import sys
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from app.application import create_app
from app.core.response import ApiError
from app.services.deployment_package import (
    DeploymentPackageConfiguration,
    DeploymentPackageService,
)
from scripts import build_chub_release_zip
from scripts.build_codex_runtime_zip import default_output as codex_runtime_default_output
from scripts.build_weixin_orchestration_plugin_zip import (
    default_output as weixin_refinement_default_output,
)


class _DeferredThread:
    calls: list[tuple[object, tuple[object, ...], dict[str, object]]] = []

    def __init__(self, *, target, args, **kwargs) -> None:
        self.calls.append((target, args, kwargs))

    def start(self) -> None:
        return None


def test_formal_module_default_names_include_release_version_and_timestamp() -> None:
    built_at = datetime(2026, 9, 11, 8, 30, 45, tzinfo=timezone.utc)

    assert codex_runtime_default_output("2.0.0", built_at=built_at).name == (
        "codex-runtime-release-2.0.0-20260911083045.zip"
    )
    assert weixin_refinement_default_output("2.0.0", built_at=built_at).name == (
        "weixin-refinement-release-2.0.0-20260911083045.zip"
    )


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

    built = service._build(configuration)
    artifact = built.artifact
    digest = built.sha256

    assert artifact.parent == settings.deployment_package.artifacts_dir
    assert len(digest) == 64
    assert built.build_id == artifact.stem.rsplit("-", 1)[1]
    with zipfile.ZipFile(artifact) as archive:
        names = set(archive.namelist())
        bundled_modules = {
            name for name in names if name.startswith("bundled-modules/")
        }
        assert len(bundled_modules) == 2
        assert any(name.startswith("bundled-modules/codex-runtime-release-2.0.0-") for name in bundled_modules)
        assert any(name.startswith("bundled-modules/weixin-refinement-release-2.0.0-") for name in bundled_modules)
        assert "DEPLOY_WITH_AI.md" in names
        assert "app/automations/debug_chrome/chrome_debug.py" in names
        assert "app/automations/debug_chrome/playwright_session.py" in names
        assert not any(name.startswith(".agents/") for name in names)
        assert "config/automations.yaml" not in names
        assert "config/settings.example.yaml" in names
        assert "runtime-modules/codex-runtime/chub-module.json" not in names
        assert archive.read("pyproject.toml").decode("utf-8").count('version = "1.2.3"') == 1
        assert 'version: "1.2.3"' in archive.read("config/settings.example.yaml").decode("utf-8")
        manifest = json.loads(archive.read("release-manifest.json"))
        assert manifest["chub_release_version"] == "1.2.3"
        assert manifest["build_id"] == built.build_id
        assert manifest["include_development_sources"] is False
        assert manifest["bundled_modules"] == [
            {
                "kind": "runtime",
                "artifact_name": built.bundled_modules[0].artifact_name,
                "module_id": "codex-010001",
                "implementation_id": "codex-010001",
                "version": "2.0.0",
                "sha256": built.bundled_modules[0].sha256,
            },
            {
                "kind": "weixin-orchestration",
                "artifact_name": built.bundled_modules[1].artifact_name,
                "module_id": "weixin-refinement",
                "implementation_id": None,
                "version": "2.0.0",
                "sha256": built.bundled_modules[1].sha256,
            },
        ]
        timestamp = artifact.stem.rsplit("-", 1)[1]
        assert all(name.endswith(f"-{timestamp}.zip") for name in bundled_modules)
        runtime_archive = next(name for name in bundled_modules if "codex-runtime" in name)
        weixin_archive = next(name for name in bundled_modules if "weixin-refinement" in name)
        with zipfile.ZipFile(archive.open(runtime_archive)) as module:
            runtime_manifest = json.loads(module.read("chub-module.json"))
        with zipfile.ZipFile(archive.open(weixin_archive)) as module:
            weixin_manifest = json.loads(module.read("chub-capability-orchestration.json"))
        assert runtime_manifest["version"] == "2.0.0"
        assert runtime_manifest["chub_version"] == "1.2.3"
        assert weixin_manifest["version"] == "2.0.0"
        assert weixin_manifest["chub_version"] == "1.2.3"

    extracted = tmp_path / "extracted-release"
    with zipfile.ZipFile(artifact) as archive:
        archive.extractall(extracted)
    environment = os.environ.copy()
    environment.pop("PYTHONPATH", None)
    imported = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "from app.automations.browser import _chrome_debug_module, session_factory; "
                "assert _chrome_debug_module().__name__ == "
                "'app.automations.debug_chrome.chrome_debug'; "
                "assert callable(session_factory())"
            ),
        ],
        cwd=extracted,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
    )
    assert imported.returncode == 0, imported.stderr


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


def test_release_version_sources_are_synchronized_after_a_successful_publish(
    settings,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = tmp_path / "project"
    (project / "config").mkdir(parents=True)
    (project / "runtime-modules" / "codex-runtime").mkdir(parents=True)
    (project / "orchestration-modules" / "weixin-refinement").mkdir(parents=True)
    (project / "pyproject.toml").write_text('[project]\nname = "chub"\nversion = "0.1.0"\n')
    (project / "config" / "settings.example.yaml").write_text('app:\n  name: "Hub"\n  version: "0.1.0"\n')
    (project / "config" / "settings.local.yaml").write_text('app:\n  name: "Hub"\n  version: "0.1.0"\n')
    (project / "runtime-modules" / "codex-runtime" / "chub-module.json").write_text(
        '{"version":"dev","chub_version":"0.1.0"}\n'
    )
    (project / "orchestration-modules" / "weixin-refinement" / "chub-capability-orchestration.json").write_text(
        '{"version":"1.0.0","chub_version":"0.1.0"}\n'
    )
    monkeypatch.setattr("app.services.deployment_package.PROJECT_ROOT", project)
    service = DeploymentPackageService(settings)
    configuration = DeploymentPackageConfiguration(
        chub_release_version="1.2.3",
        runtime_implementation_id="codex-010001",
        runtime_release_version="2.0.0",
        runtime_description="正式 Runtime。",
        weixin_release_version="3.0.0",
    )

    updates = service._version_updates(configuration)
    service._synchronize_project_versions(updates)

    assert 'version = "1.2.3"' in (project / "pyproject.toml").read_text()
    assert 'version: "1.2.3"' in (project / "config" / "settings.example.yaml").read_text()
    assert 'version: "1.2.3"' in (project / "config" / "settings.local.yaml").read_text()
    assert json.loads((project / "runtime-modules" / "codex-runtime" / "chub-module.json").read_text()) == {
        "version": "2.0.0",
        "chub_version": "1.2.3",
    }
    assert json.loads((project / "orchestration-modules" / "weixin-refinement" / "chub-capability-orchestration.json").read_text()) == {
        "version": "3.0.0",
        "chub_version": "1.2.3",
    }


def test_release_build_optionally_includes_development_sources(settings, tmp_path: Path) -> None:
    settings.deployment_package.artifacts_dir = tmp_path / "releases"
    settings.deployment_package.state_file = tmp_path / "state.json"
    service = DeploymentPackageService(settings)
    built = service._build(
        DeploymentPackageConfiguration(
            chub_release_version="1.0.0",
            runtime_implementation_id="codex-010004",
            runtime_release_version="1.0.0",
            runtime_description="开发源码构建。",
            weixin_release_version="1.0.0",
            include_development_sources=True,
        )
    )

    with zipfile.ZipFile(built.artifact) as archive:
        assert "runtime-modules/codex-runtime/chub-module.json" in archive.namelist()
        assert "orchestration-modules/weixin-refinement/chub-capability-orchestration.json" in archive.namelist()


def test_release_build_does_not_publish_when_module_preflight_fails(
    settings,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings.deployment_package.artifacts_dir = tmp_path / "releases"
    settings.deployment_package.state_file = tmp_path / "state.json"
    service = DeploymentPackageService(settings)

    def fail_preflight(*args, **kwargs):
        raise OSError("module preflight failed")

    monkeypatch.setattr(
        "app.services.deployment_package.RuntimePluginService.install", fail_preflight
    )

    with pytest.raises(OSError, match="module preflight failed"):
        service._build(service.status().configuration)

    assert not settings.deployment_package.artifacts_dir.exists() or not list(
        settings.deployment_package.artifacts_dir.iterdir()
    )


@pytest.mark.anyio
async def test_deployment_package_settings_api_reads_and_updates_configuration(
    settings,
    tmp_path: Path,
) -> None:
    settings.deployment_package.artifacts_dir = tmp_path / "releases"
    settings.deployment_package.state_file = tmp_path / "state.json"
    transport = httpx.ASGITransport(app=create_app(settings))
    payload = {
        "release_version": "4.0.0",
        "include_development_sources": True,
    }

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        before = await client.get("/api/settings/deployment-package")
        updated = await client.put("/api/settings/deployment-package", json=payload)

    assert before.status_code == 200
    assert before.json()["data"]["app_version"] == settings.app.version
    assert set(before.json()["data"]["source_versions"]) == {"chub", "runtime", "weixin"}
    assert updated.status_code == 200
    configuration = updated.json()["data"]["configuration"]
    assert configuration["chub_release_version"] == payload["release_version"]
    assert configuration["runtime_release_version"] == payload["release_version"]
    assert configuration["weixin_release_version"] == payload["release_version"]
    assert configuration["include_development_sources"] is payload["include_development_sources"]
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
    monkeypatch.setattr(service, "_require_idle_worker", lambda: None)
    initial = service.status().configuration.model_copy(
        update={"chub_release_version": "1.0.0"}
    )
    service.save_configuration(initial)
    _DeferredThread.calls = []
    monkeypatch.setattr("app.services.deployment_package.threading.Thread", _DeferredThread)

    service.start(source_ip="127.0.0.1")
    service.save_configuration(initial.model_copy(update={"chub_release_version": "2.0.0"}))

    assert _DeferredThread.calls[0][1][2].chub_release_version == "1.0.0"


def test_release_rejects_a_busy_worker_before_registering_the_operation(
    settings,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings.deployment_package.artifacts_dir = tmp_path / "releases"
    settings.deployment_package.state_file = tmp_path / "state.json"
    service = DeploymentPackageService(settings)
    monkeypatch.setattr(
        "app.services.deployment_package.subprocess.run",
        lambda *args, **kwargs: SimpleNamespace(
            returncode=0,
            stdout=b'{"success":true,"data":{"status":"ready","active_tasks":1,"queued_tasks":0}}',
        ),
    )

    for publish in (service.start, service.publish):
        with pytest.raises(ApiError) as error:
            publish(source_ip="127.0.0.1")

        assert error.value.status_code == 409
        assert error.value.code == "release_worker_busy"
    assert not settings.deployment_package.state_file.exists()


def test_release_rolls_back_versions_when_success_state_cannot_be_written(
    settings,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = tmp_path / "project"
    (project / "config").mkdir(parents=True)
    (project / "runtime-modules" / "codex-runtime").mkdir(parents=True)
    (project / "orchestration-modules" / "weixin-refinement").mkdir(parents=True)
    (project / "pyproject.toml").write_text('[project]\nname = "chub"\nversion = "0.1.0"\n')
    (project / "config" / "settings.example.yaml").write_text('app:\n  version: "0.1.0"\n')
    (project / "config" / "settings.local.yaml").write_text('app:\n  version: "0.1.0"\n')
    (project / "runtime-modules" / "codex-runtime" / "chub-module.json").write_text(
        '{"version":"0.1.0","chub_version":"0.1.0"}\n'
    )
    (project / "orchestration-modules" / "weixin-refinement" / "chub-capability-orchestration.json").write_text(
        '{"version":"0.1.0","chub_version":"0.1.0"}\n'
    )
    monkeypatch.setattr("app.services.deployment_package.PROJECT_ROOT", project)
    settings.deployment_package.artifacts_dir = tmp_path / "releases"
    settings.deployment_package.state_file = tmp_path / "state.json"
    service = DeploymentPackageService(settings)
    configuration = service.status().configuration.model_copy(
        update={
            "chub_release_version": "1.2.3",
            "runtime_release_version": "1.2.3",
            "weixin_release_version": "1.2.3",
        }
    )
    service.save_configuration(configuration)
    updates = service._version_updates(configuration)
    artifact = tmp_path / "release.zip"
    artifact.write_bytes(b"release")
    monkeypatch.setattr(
        service,
        "_build",
        lambda _configuration: SimpleNamespace(
            artifact=artifact,
            sha256="a" * 64,
            build_id="test-build",
            built_at=datetime.now(timezone.utc),
            bundled_modules=(),
            version_updates=updates,
        ),
    )
    monkeypatch.setattr(service, "_require_idle_worker", lambda: None)
    write_state = service._write

    def fail_success_state(state) -> None:
        if state.operation is not None and state.operation.status == "succeeded":
            raise OSError("state write failed")
        write_state(state)

    monkeypatch.setattr(service, "_write", fail_success_state)

    class _InlineThread:
        def __init__(self, *, target, args, **kwargs) -> None:
            self.target = target
            self.args = args

        def start(self) -> None:
            self.target(*self.args)

    monkeypatch.setattr("app.services.deployment_package.threading.Thread", _InlineThread)

    service.start(source_ip="127.0.0.1")

    assert settings.app.version == "0.1.0"
    assert not artifact.exists()
    assert 'version = "0.1.0"' in (project / "pyproject.toml").read_text()
    assert 'version: "0.1.0"' in (project / "config" / "settings.example.yaml").read_text()
    assert 'version: "0.1.0"' in (project / "config" / "settings.local.yaml").read_text()
    assert json.loads((project / "runtime-modules" / "codex-runtime" / "chub-module.json").read_text())["version"] == "0.1.0"
    assert json.loads(
        (project / "orchestration-modules" / "weixin-refinement" / "chub-capability-orchestration.json").read_text()
    )["version"] == "0.1.0"
    assert service.status().operation is not None
    assert service.status().operation.status == "failed"


def test_release_cli_uses_the_guarded_publish_path(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    calls: list[str] = []

    class _Service:
        def __init__(self, settings) -> None:
            assert settings == "settings"

        def publish(self, *, source_ip: str):
            calls.append(source_ip)
            return SimpleNamespace(
                operation=SimpleNamespace(
                    status="succeeded",
                    artifact_name="chub-release-1.0.0.zip",
                    artifact_size=123,
                    sha256="a" * 64,
                    build_id="test-build",
                )
            )

    monkeypatch.setattr(build_chub_release_zip, "load_settings", lambda: "settings")
    monkeypatch.setattr(build_chub_release_zip, "DeploymentPackageService", _Service)

    assert build_chub_release_zip.main() == 0
    assert calls == ["127.0.0.1"]
    assert json.loads(capsys.readouterr().out)["artifact_name"] == "chub-release-1.0.0.zip"


def test_release_cli_reports_a_worker_gate_failure_without_a_traceback(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    class _Service:
        def __init__(self, settings) -> None:
            assert settings == "settings"

        def publish(self, *, source_ip: str):
            raise ApiError(
                409,
                "release_worker_busy",
                "Quick Worker 仍有 1 个执行中、0 个排队任务；完成后再发布。",
            )

    monkeypatch.setattr(build_chub_release_zip, "load_settings", lambda: "settings")
    monkeypatch.setattr(build_chub_release_zip, "DeploymentPackageService", _Service)

    assert build_chub_release_zip.main() == 1
    payload = json.loads(capsys.readouterr().err)
    assert payload == {
        "success": False,
        "code": "release_worker_busy",
        "message": "Quick Worker 仍有 1 个执行中、0 个排队任务；完成后再发布。",
    }


def test_interrupted_release_build_is_closed_and_can_be_retried(
    settings,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings.deployment_package.artifacts_dir = tmp_path / "releases"
    settings.deployment_package.state_file = tmp_path / "state.json"
    service = DeploymentPackageService(settings)
    monkeypatch.setattr(service, "_require_idle_worker", lambda: None)
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
    retried_service = DeploymentPackageService(settings)
    monkeypatch.setattr(retried_service, "_require_idle_worker", lambda: None)
    retried = retried_service.start(source_ip="127.0.0.1")
    assert retried.operation is not None
    assert retried.operation.status == "requested"
