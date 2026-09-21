import json
import zipfile
from types import SimpleNamespace

from app.services.deployment_package import (
    DeploymentPackageConfiguration,
    DeploymentPackageReleaseNoteGeneration,
    DeploymentPackageService,
)


def test_release_build_contains_only_the_codex_runtime_module(settings, tmp_path) -> None:
    settings.deployment_package.artifacts_dir = tmp_path / "releases"
    settings.deployment_package.state_file = tmp_path / "state.json"
    service = DeploymentPackageService(settings)
    source_versions = service._source_versions()
    configuration = DeploymentPackageConfiguration(
        chub_release_version=source_versions.chub,
        runtime_implementation_id="codex-010001",
        runtime_release_version=source_versions.runtime,
        runtime_description="正式 Runtime。",
        include_development_sources=False,
        release_note="正式部署包。",
    )

    built = service._build(configuration, source_commit="a" * 40)

    with zipfile.ZipFile(built.artifact) as archive:
        bundled_modules = [
            name for name in archive.namelist() if name.startswith("bundled-modules/")
        ]
        manifest = json.loads(archive.read("release-manifest.json"))

    assert len(bundled_modules) == 1
    assert bundled_modules[0].startswith("bundled-modules/codex-runtime-release-")
    assert [item["kind"] for item in manifest["bundled_modules"]] == ["runtime"]
    assert manifest["chub_release_version"] == source_versions.chub


def test_source_versions_do_not_expose_a_retired_weixin_release(settings) -> None:
    versions = DeploymentPackageService(settings)._source_versions()

    assert versions.runtime
    assert not hasattr(versions, "weixin")


def test_release_note_generation_recovers_unlinked_worker_task(settings, tmp_path) -> None:
    settings.deployment_package.state_file = tmp_path / "state.json"
    task = SimpleNamespace(
        id="task-1",
        session_id="session-1",
        kind="standard",
        status="requested",
        result=None,
        error=None,
    )
    quick = SimpleNamespace(
        find_for_operation=lambda operation_id: task if operation_id == "operation-1" else None,
        get=lambda task_id: task if task_id == "task-1" else None,
    )
    service = DeploymentPackageService(settings, quick_interactions=quick)
    state = service._read()
    state.release_note_generation = DeploymentPackageReleaseNoteGeneration(
        status="requested",
        session_id="session-1",
        operation_id="operation-1",
    )

    service._refresh_release_note_generation(state)

    assert state.release_note_generation.task_id == "task-1"
    assert state.release_note_generation.status == "running"


def test_release_note_generation_marks_missing_unlinked_task_as_failed(settings, tmp_path) -> None:
    settings.deployment_package.state_file = tmp_path / "state.json"
    quick = SimpleNamespace(find_for_operation=lambda _operation_id: None)
    service = DeploymentPackageService(settings, quick_interactions=quick)
    state = service._read()
    state.release_note_generation = DeploymentPackageReleaseNoteGeneration(
        status="requested",
        session_id="session-1",
        operation_id="operation-1",
    )

    service._refresh_release_note_generation(state)

    assert state.release_note_generation.status == "failed"
    assert "状态未能确认" in state.release_note_generation.message
