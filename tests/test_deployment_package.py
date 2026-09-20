from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import zipfile
from contextlib import nullcontext
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from app.application import create_app
from app.core.response import ApiError
from app.services.deployment_package import (
    DeploymentPackageConfiguration,
    DeploymentPackageSourceVersions,
    DeploymentPackageService,
)
from scripts.build import chub_release_zip
from scripts.build.codex_runtime_zip import default_output as codex_runtime_default_output
from scripts.build.weixin_orchestration_plugin_zip import (
    default_output as weixin_refinement_default_output,
)


class _DeferredThread:
    calls: list[tuple[object, tuple[object, ...], dict[str, object]]] = []

    def __init__(self, *, target, args, **kwargs) -> None:
        self.calls.append((target, args, kwargs))

    def start(self) -> None:
        return None


class _ReleaseNoteSessionManager:
    def __init__(self) -> None:
        self.sessions: dict[str, object] = {}
        self.created_with: tuple[object, ...] | None = None
        self.renamed: tuple[str, str] | None = None

    def get_session(self, session_id: str):
        if session_id not in self.sessions:
            raise ApiError(404, "session_not_found", "not found")
        return self.sessions[session_id]

    def create_session(self, *args):
        self.created_with = args
        session = SimpleNamespace(id="release-note-session")
        self.sessions[session.id] = session
        return session

    def rename_session(self, session_id: str, title: str):
        self.renamed = (session_id, title)

    def discard_unstarted_session(self, session_id: str) -> bool:
        self.sessions.pop(session_id, None)
        return True


class _ReleaseNoteQuickInteractions:
    def __init__(self) -> None:
        self.task = SimpleNamespace(id="release-note-task", status="requested", result=None, error=None)
        self.submissions: list[tuple[str, str, str, str]] = []

    def session_creation_guard(self):
        return nullcontext()

    def session_operation_guard(self, _session_id: str):
        return nullcontext()

    def submit(self, session_id: str, prompt: str, *, operation_id: str, source_ip: str):
        self.submissions.append((session_id, prompt, operation_id, source_ip))
        return self.task

    def get(self, task_id: str):
        assert task_id == self.task.id
        return self.task


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
    source_versions = service._source_versions()
    configuration = DeploymentPackageConfiguration(
        chub_release_version=source_versions.chub,
        runtime_implementation_id="codex-010001",
        runtime_release_version=source_versions.runtime,
        runtime_description="正式 Runtime。",
        weixin_release_version=source_versions.weixin,
        include_development_sources=False,
        release_note="正式部署包。",
    )

    built = service._build(configuration, source_commit="a" * 40)
    artifact = built.artifact
    digest = built.sha256

    assert artifact.parent == settings.deployment_package.artifacts_dir
    assert len(digest) == 64
    assert re.fullmatch(
        rf"{built.built_at.strftime('%Y%m%d%H%M%S%f')}-{'a' * 12}-[a-f0-9]{{8}}",
        built.build_id,
    )
    with zipfile.ZipFile(artifact) as archive:
        names = set(archive.namelist())
        bundled_modules = {
            name for name in names if name.startswith("bundled-modules/")
        }
        assert len(bundled_modules) == 2
        assert any(name.startswith(f"bundled-modules/codex-runtime-release-{source_versions.runtime}-") for name in bundled_modules)
        assert any(name.startswith(f"bundled-modules/weixin-refinement-release-{source_versions.weixin}-") for name in bundled_modules)
        assert "DEPLOY_WITH_AI.md" in names
        deployment_guide = archive.read("DEPLOY_WITH_AI.md").decode("utf-8")
        for marker in (
            "DEPLOYMENT_CHECKPOINT_PATH",
            "DEPLOYMENT_NOTES_PATH",
            "record_stage",
            "unzip -t \"$SOURCE_ZIP\"",
            "core_services_verified",
            "不得使用无上下文正则替换 YAML 字段",
            '"app": {"name": sys.argv[2], "page_title": f"{sys.argv[2]} · Hub"}',
            "部署流程**不执行** `wsl.exe --shutdown`",
            "WSL2 外置 loopback-forwarder",
            "http://localhost:8081/api/health",
            "Runtime、插件包、OpenClaw 和其他第三方能力不属于本条件",
        ):
            assert marker in deployment_guide
        assert "app/automations/debug_chrome/chrome_debug.py" in names
        assert "app/automations/debug_chrome/playwright_session.py" in names
        assert not any(name.startswith(".agents/") for name in names)
        assert "config/automations.yaml" not in names
        assert "config/settings.yaml" in names
        assert "modules/runtime/codex-runtime/chub-module.json" not in names
        assert "modules/chub-local-modules.example.json" in names
        for script in (
            "scripts/chub",
            "scripts/maintenance/chub-data-migrate",
            "scripts/maintenance/chub-system-recovery-reset",
            "scripts/maintenance/chub-system-upgrade-restart",
            "scripts/maintenance/chub-system-upgrade-start",
            "scripts/maintenance/chub-web-restart",
            "scripts/maintenance/chub-worker-reload",
            "scripts/build/build-chub-release-zip.py",
            "scripts/build/build-codex-runtime-zip.py",
            "scripts/build/build-deliveryline-plugin-zip.py",
            "scripts/build/build-runtime-verification-zip.py",
            "scripts/build/build-weixin-orchestration-plugin-zip.py",
            "scripts/platform/service-management.sh",
        ):
            assert (archive.getinfo(script).external_attr >> 16) & 0o777 == 0o755
        recovery_script = archive.read(
            "scripts/maintenance/chub-system-recovery-reset"
        ).decode("utf-8")
        assert 'PY\n    ))"' not in recovery_script
        assert archive.read("pyproject.toml").decode("utf-8").count(f'version = "{source_versions.chub}"') == 1
        assert f'version: "{source_versions.chub}"' in archive.read("config/settings.yaml").decode("utf-8")
        manifest = json.loads(archive.read("release-manifest.json"))
        assert manifest["chub_release_version"] == source_versions.chub
        assert manifest["build_id"] == built.build_id
        assert manifest["release_note"] == "正式部署包。"
        assert manifest["include_development_sources"] is False
        assert manifest["bundled_modules"] == [
            {
                "kind": "runtime",
                "artifact_name": built.bundled_modules[0].artifact_name,
                "module_id": "codex-010001",
                "implementation_id": "codex-010001",
                "version": source_versions.runtime,
                "sha256": built.bundled_modules[0].sha256,
            },
            {
                "kind": "weixin-orchestration",
                "artifact_name": built.bundled_modules[1].artifact_name,
                "module_id": "weixin-refinement",
                "implementation_id": None,
                "version": source_versions.weixin,
                "sha256": built.bundled_modules[1].sha256,
            },
        ]
        assert all(name.endswith(f"-{built.build_id}.zip") for name in bundled_modules)
        runtime_archive = next(name for name in bundled_modules if "codex-runtime" in name)
        weixin_archive = next(name for name in bundled_modules if "weixin-refinement" in name)
        with zipfile.ZipFile(archive.open(runtime_archive)) as module:
            runtime_manifest = json.loads(module.read("chub-module.json"))
        with zipfile.ZipFile(archive.open(weixin_archive)) as module:
            weixin_manifest = json.loads(module.read("chub-capability-orchestration.json"))
        assert runtime_manifest["version"] == source_versions.runtime
        assert runtime_manifest["chub_version"] == source_versions.chub
        assert weixin_manifest["version"] == source_versions.weixin
        assert weixin_manifest["chub_version"] == source_versions.chub

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


def test_same_commit_rebuilds_use_distinct_microsecond_build_identifiers(
    settings,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings.deployment_package.artifacts_dir = tmp_path / "releases"
    service = DeploymentPackageService(settings)
    source_versions = service._source_versions()
    configuration = DeploymentPackageConfiguration(
        chub_release_version=source_versions.chub,
        runtime_implementation_id="codex-010001",
        runtime_release_version=source_versions.runtime,
        runtime_description="正式 Runtime。",
        weixin_release_version=source_versions.weixin,
        release_note="同版本重发。",
    )
    moments = iter(
        (
            datetime(2026, 9, 20, 8, 0, 0, 123456, tzinfo=timezone.utc),
            datetime(2026, 9, 20, 8, 0, 0, 654321, tzinfo=timezone.utc),
        )
    )
    monkeypatch.setattr("app.services.deployment_package._now", lambda: next(moments))

    first = service._build(configuration, source_commit="a" * 40)
    second = service._build(configuration, source_commit="a" * 40)

    assert first.build_id != second.build_id
    assert first.artifact != second.artifact


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
        release_note="下一次发布。",
    )

    saved = service.save_configuration(configuration)

    assert saved.app_version == settings.app.version
    assert saved.configuration == configuration.model_copy(update={"release_note": ""})
    assert json.loads(settings.deployment_package.state_file.read_text())["configuration"]["chub_release_version"] == "9.9.9"


def test_release_note_generation_reuses_a_general_internal_session_and_keeps_generated_draft(
    settings,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings.deployment_package.artifacts_dir = tmp_path / "releases"
    settings.deployment_package.state_file = tmp_path / "state.json"
    sessions = _ReleaseNoteSessionManager()
    quick = _ReleaseNoteQuickInteractions()
    service = DeploymentPackageService(settings, sessions, quick)
    operation_log: list[dict[str, object]] = []
    monkeypatch.setattr(
        "app.services.deployment_package.write_operation",
        lambda **fields: operation_log.append(fields),
    )
    source_versions = DeploymentPackageSourceVersions(chub="1.0.0", runtime="1.0.0", weixin="1.0.0")
    head = {"value": "b" * 40, "fingerprint": "d" * 64}
    monkeypatch.setattr(service, "_source_versions", lambda: source_versions)
    monkeypatch.setattr(
        service,
        "_require_git_generation_target",
        lambda: (head["value"], head["fingerprint"]),
    )
    monkeypatch.setattr(service, "_git", lambda *arguments, **_kwargs: head["value"])
    monkeypatch.setattr(service, "_working_tree_fingerprint", lambda: head["fingerprint"])
    history = settings.deployment_package.artifacts_dir / "history"
    history.mkdir(parents=True)
    (history / "chub-release-0.9.0.json").write_text(
        json.dumps(
            {
                "release_version": "0.9.0",
                "tag_name": "chub-v0.9.0",
                "commit": "a" * 40,
                "built_at": "2026-09-15T00:00:00+00:00",
            }
        )
    )

    draft_token = "a" * 32
    requested = service.generate_release_note(
        release_version="1.0.0",
        include_development_sources=False,
        source_ip="127.0.0.1",
        operation_id="release-note-operation",
        release_note_draft_token=draft_token,
    )

    assert sessions.created_with == ("chub",)
    assert sessions.renamed == ("release-note-session", "版本发布说明")
    assert requested.release_note_generation.status == "running"
    assert requested.release_note_generation.baseline_tag_name == "chub-v0.9.0"
    assert requested.release_note_generation.baseline_commit == "a" * 40
    assert "当前 commit" in quick.submissions[0][1]
    assert service.hidden_release_note_session_ids() == {"release-note-session"}

    quick.task.status = "succeeded"
    quick.task.result = "- 支持正式发布\n- 补充部署流程"
    service.record_release_note_task_finished(quick.task)

    persisted = json.loads(settings.deployment_package.state_file.read_text())
    assert persisted["configuration"]["release_note"] == ""
    assert persisted["release_note_generation"]["status"] == "succeeded"
    assert operation_log == [
        {
            "operation_id": "release-note-operation",
            "action": "generate_deployment_package_release_note",
            "status": "succeeded",
            "target": "1.0.0",
            "source_ip": "127.0.0.1",
            "reason": None,
        }
    ]
    completed = service.status(release_note_draft_token=draft_token)

    assert completed.configuration.release_note == ""
    assert completed.generated_release_note == "- 支持正式发布\n- 补充部署流程"
    assert completed.release_note_generation.status == "succeeded"
    assert service.status().generated_release_note is None
    assert service.status(release_note_draft_token="b" * 32).generated_release_note is None
    assert DeploymentPackageService(settings, sessions, quick).status().generated_release_note is None
    head["fingerprint"] = "c" * 64
    unchanged = service.status(release_note_draft_token=draft_token)

    assert unchanged.release_note_generation.status == "succeeded"
    assert unchanged.generated_release_note == "- 支持正式发布\n- 补充部署流程"
    service._release_note_draft_expires_at = 0
    assert service.status(release_note_draft_token=draft_token).generated_release_note is None
    persisted["release_note_generation"]["status"] = "stale"
    settings.deployment_package.state_file.write_text(json.dumps(persisted), encoding="utf-8")
    reset = DeploymentPackageService(settings, sessions, quick).status()
    assert reset.release_note_generation.status == "idle"
    assert json.loads(settings.deployment_package.state_file.read_text())["release_note_generation"]["status"] == "idle"
    service.save_configuration(completed.configuration)
    assert service.hidden_release_note_session_ids() == {"release-note-session"}
    assert service.set_show_release_note_session(True) is True
    assert service.hidden_release_note_session_ids() == set()


def test_release_note_prompt_requires_a_direct_summary_and_change_focused_bullets() -> None:
    prompt = DeploymentPackageService._release_note_prompt(
        SimpleNamespace(
            target_version="1.0.0",
            target_commit="b" * 40,
            baseline_version="0.9.0",
            baseline_tag_name="chub-v0.9.0",
            baseline_commit="a" * 40,
        )
    )

    assert "不加“概述：”或其他前缀" in prompt
    assert "随后输出 3-6 条" in prompt
    assert "未变化的能力域不得重复" in prompt


def test_release_note_generation_allows_dirty_worktree_without_git_identity(
    settings,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings.deployment_package.artifacts_dir = tmp_path / "releases"
    service = DeploymentPackageService(settings)
    calls: list[tuple[str, ...]] = []

    def git(*arguments: str, **_kwargs: object) -> str:
        calls.append(arguments)
        if arguments[:2] == ("rev-parse", "--is-inside-work-tree"):
            return "true"
        if arguments == ("rev-parse", "HEAD"):
            return "a" * 40
        if arguments[:2] == ("status", "--porcelain=v1"):
            return " M app/services/deployment_package.py\0?? notes.txt\0"
        if arguments[:3] == ("diff", "--binary", "--no-ext-diff"):
            return "diff --git a/file b/file\n"
        if arguments[:2] == ("ls-files", "--others"):
            return "notes.txt\0"
        if arguments == ("hash-object", "--no-filters", "--", "notes.txt"):
            return "b" * 40
        raise AssertionError(arguments)

    monkeypatch.setattr(service, "_git", git)

    commit, fingerprint = service._require_git_generation_target()

    assert commit == "a" * 40
    assert re.fullmatch(r"[a-f0-9]{64}", fingerprint)
    assert ("var", "GIT_COMMITTER_IDENT") not in calls


@pytest.mark.anyio
async def test_release_note_generation_api_records_no_premature_success(
    settings,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings.deployment_package.artifacts_dir = tmp_path / "releases"
    settings.deployment_package.state_file = tmp_path / "state.json"
    app = create_app(settings)
    result = app.state.deployment_package.status()
    submitted: list[dict[str, object]] = []
    logs: list[dict[str, object]] = []

    def generate_release_note(**fields: object):
        submitted.append(fields)
        return result

    def log_operation(_request, **fields: object) -> str:
        logs.append(fields)
        return "release-note-operation"

    app.state.deployment_package.generate_release_note = generate_release_note
    monkeypatch.setattr("app.api.settings.log_operation", log_operation)
    transport = httpx.ASGITransport(app=app)

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/api/settings/deployment-package/release-note",
            headers={"X-Chub-Release-Note-Draft-Token": "a" * 32},
            json={"release_version": "1.0.0"},
        )

    assert response.status_code == 200
    assert submitted == [
        {
            "release_version": "1.0.0",
            "include_development_sources": False,
            "source_ip": "127.0.0.1",
            "operation_id": "release-note-operation",
            "release_note_draft_token": "a" * 32,
        }
    ]
    assert [item["status"] for item in logs] == ["requested", "started"]


def test_release_note_generation_uses_the_latest_successful_release_record(
    settings,
    tmp_path: Path,
) -> None:
    settings.deployment_package.artifacts_dir = tmp_path / "releases"
    service = DeploymentPackageService(settings)
    history = settings.deployment_package.artifacts_dir / "history"
    history.mkdir(parents=True)
    for version, commit, built_at in (
        ("0.9.0", "a" * 40, "2026-09-15T10:00:00+00:00"),
        ("1.0.0", "b" * 40, "2026-09-16T10:00:00+00:00"),
    ):
        (history / f"chub-release-{version}.json").write_text(
            json.dumps(
                {
                    "release_version": version,
                    "tag_name": f"chub-v{version}",
                    "commit": commit,
                    "built_at": built_at,
                }
            )
        )

    baseline = service._latest_successful_release_record()

    assert baseline.version == "1.0.0"
    assert baseline.tag_name == "chub-v1.0.0"
    assert baseline.commit == "b" * 40


def test_release_output_directory_uses_the_fixed_platform_file_manager(
    settings,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings.deployment_package.artifacts_dir = tmp_path / "releases"
    service = DeploymentPackageService(settings)
    calls: list[list[str]] = []
    monkeypatch.setattr("app.services.deployment_package.detect_platform", lambda: "macos")
    monkeypatch.setattr(
        "app.services.deployment_package.subprocess.run",
        lambda command, **_kwargs: calls.append(command) or SimpleNamespace(returncode=0),
    )

    service.open_output_directory()

    assert calls == [["open", str(settings.deployment_package.artifacts_dir)]]
    assert settings.deployment_package.artifacts_dir.is_dir()


def test_release_source_versions_require_all_chub_declarations_to_match(
    settings,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = tmp_path / "project"
    (project / "config").mkdir(parents=True)
    (project / "modules" / "runtime" / "codex-runtime").mkdir(parents=True)
    (project / "modules" / "orchestration" / "weixin-refinement").mkdir(parents=True)
    (project / "pyproject.toml").write_text('[project]\nversion = "1.2.3"\n')
    (project / "config" / "settings.yaml").write_text('app:\n  version: "1.2.3"\n')
    (project / "modules" / "runtime" / "codex-runtime" / "chub-module.json").write_text(
        '{"version":"1.2.3","chub_version":"1.2.3"}\n'
    )
    (project / "modules" / "orchestration" / "weixin-refinement" / "chub-capability-orchestration.json").write_text(
        '{"version":"1.2.3","chub_version":"1.2.2"}\n'
    )
    monkeypatch.setattr("app.services.deployment_package.PROJECT_ROOT", project)
    service = DeploymentPackageService(settings)

    assert service._source_versions().chub == "1.2.3"
    assert service._source_declarations_match("1.2.3") is False


def test_release_version_commit_updates_only_fixed_version_declarations(
    settings,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = tmp_path / "project"
    (project / "config").mkdir(parents=True)
    (project / "modules" / "runtime" / "codex-runtime").mkdir(parents=True)
    (project / "modules" / "orchestration" / "weixin-refinement").mkdir(parents=True)
    (project / "pyproject.toml").write_text('[project]\nversion = "1.0.0"\n')
    (project / "config" / "settings.yaml").write_text('app:\n  version: "1.0.0"\n')
    (project / "modules" / "runtime" / "codex-runtime" / "chub-module.json").write_text(
        '{"version":"1.0.0","chub_version":"1.0.0"}\n'
    )
    (project / "modules" / "orchestration" / "weixin-refinement" / "chub-capability-orchestration.json").write_text(
        '{"version":"1.0.0","chub_version":"1.0.0"}\n'
    )
    monkeypatch.setattr("app.services.deployment_package.PROJECT_ROOT", project)
    service = DeploymentPackageService(settings)
    calls: list[tuple[str, ...]] = []
    monkeypatch.setattr(
        service,
        "_git",
        lambda *arguments, **_kwargs: calls.append(arguments) or "",
    )

    assert service._commit_release_version_if_needed("1.0.1") is True
    assert service._source_declarations_match("1.0.1") is True
    assert calls == [
        (
            "commit",
            "--only",
            "-m",
            "chore(release): v1.0.1",
            "--",
            "pyproject.toml",
            "config/settings.yaml",
            "modules/runtime/codex-runtime/chub-module.json",
            "modules/orchestration/weixin-refinement/chub-capability-orchestration.json",
        )
    ]
    assert service._commit_release_version_if_needed("1.0.1") is False
    assert len(calls) == 1


def test_release_version_commit_creates_a_real_git_commit_with_only_fixed_files(
    settings,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = tmp_path / "project"
    (project / "config").mkdir(parents=True)
    (project / "modules" / "runtime" / "codex-runtime").mkdir(parents=True)
    (project / "modules" / "orchestration" / "weixin-refinement").mkdir(parents=True)
    (project / "pyproject.toml").write_text('[project]\nversion = "1.0.0"\n')
    (project / "config" / "settings.yaml").write_text('app:\n  version: "1.0.0"\n')
    (project / "modules" / "runtime" / "codex-runtime" / "chub-module.json").write_text(
        '{"version":"1.0.0","chub_version":"1.0.0"}\n'
    )
    (project / "modules" / "orchestration" / "weixin-refinement" / "chub-capability-orchestration.json").write_text(
        '{"version":"1.0.0","chub_version":"1.0.0"}\n'
    )

    def git(*arguments: str) -> str:
        return subprocess.run(
            ["git", *arguments], cwd=project, check=True, text=True, capture_output=True
        ).stdout

    git("init")
    git("config", "user.name", "Chub Test")
    git("config", "user.email", "chub-test@example.invalid")
    git("add", ".")
    git("commit", "-m", "initial")
    monkeypatch.setattr("app.services.deployment_package.PROJECT_ROOT", project)

    service = DeploymentPackageService(settings)

    assert service._commit_release_version_if_needed("1.0.1") is True
    assert service._source_declarations_match("1.0.1") is True
    assert git("log", "-1", "--format=%s").strip() == "chore(release): v1.0.1"
    assert sorted(git("show", "--format=", "--name-only", "--no-renames", "HEAD").splitlines()) == [
        "config/settings.yaml",
        "modules/orchestration/weixin-refinement/chub-capability-orchestration.json",
        "modules/runtime/codex-runtime/chub-module.json",
        "pyproject.toml",
    ]


def test_release_rejects_target_version_below_current_declarations(
    settings,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = tmp_path / "project"
    (project / "config").mkdir(parents=True)
    (project / "modules" / "runtime" / "codex-runtime").mkdir(parents=True)
    (project / "modules" / "orchestration" / "weixin-refinement").mkdir(parents=True)
    (project / "pyproject.toml").write_text('[project]\nversion = "1.0.1"\n')
    (project / "config" / "settings.yaml").write_text('app:\n  version: "1.0.1"\n')
    (project / "modules" / "runtime" / "codex-runtime" / "chub-module.json").write_text(
        '{"version":"1.0.1","chub_version":"1.0.1"}\n'
    )
    (project / "modules" / "orchestration" / "weixin-refinement" / "chub-capability-orchestration.json").write_text(
        '{"version":"1.0.1","chub_version":"1.0.1"}\n'
    )
    monkeypatch.setattr("app.services.deployment_package.PROJECT_ROOT", project)
    service = DeploymentPackageService(settings)

    with pytest.raises(ApiError, match="不能低于") as error:
        service._require_release_version_not_lower("1.0.0")

    assert error.value.code == "release_version_downgrade"


def test_release_rejects_dirty_git_worktree(
    settings,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings.deployment_package.artifacts_dir = tmp_path / "releases"
    settings.deployment_package.state_file = tmp_path / "state.json"
    service = DeploymentPackageService(settings)
    monkeypatch.setattr(
        service,
        "_git",
        lambda *arguments, **_kwargs: "true"
        if arguments[:2] == ("rev-parse", "--is-inside-work-tree")
        else " M docs/DEPLOY_WITH_AI.md",
    )

    with pytest.raises(ApiError, match="提交所有") as error:
        service._require_git_release_baseline(service.status().configuration)

    assert error.value.code == "release_git_worktree_dirty"


def test_release_rejects_a_version_that_differs_from_committed_sources(
    settings,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings.deployment_package.artifacts_dir = tmp_path / "releases"
    settings.deployment_package.state_file = tmp_path / "state.json"
    service = DeploymentPackageService(settings)

    def git(*arguments: str, **_kwargs: object) -> str:
        if arguments[:2] == ("rev-parse", "--is-inside-work-tree"):
            return "true"
        if arguments[:2] == ("status", "--porcelain=v1"):
            return ""
        if arguments == ("rev-parse", "HEAD"):
            return "a" * 40
        if arguments == ("var", "GIT_COMMITTER_IDENT"):
            return "Chub <chub@example.test> 0 +0000"
        raise AssertionError(arguments)

    monkeypatch.setattr(service, "_git", git)
    configuration = service.status().configuration.model_copy(
        update={"chub_release_version": "9.9.9"}
    )

    with pytest.raises(ApiError, match="版本声明") as error:
        service._require_git_release_baseline(configuration)

    assert error.value.code == "release_source_version_mismatch"
    assert "Chub 项目版本" in error.value.message


def test_release_preview_reports_tag_movement_and_declaration_mismatches(
    settings,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings.deployment_package.artifacts_dir = tmp_path / "releases"
    service = DeploymentPackageService(settings)
    monkeypatch.setattr(
        service,
        "_all_source_version_declarations",
        lambda: ("1.0.1",) * 6,
    )

    def git(*arguments: str, **_kwargs: object) -> str:
        if arguments == ("rev-parse", "HEAD"):
            return "a" * 40
        if arguments == ("rev-parse", "chub-v1.0.1^{commit}"):
            return "b" * 40
        if arguments == ("rev-parse", "chub-v1.0.2^{commit}"):
            return ""
        raise AssertionError(arguments)

    monkeypatch.setattr(service, "_git", git)

    same = service.release_preview("1.0.1")
    upgrade = service.release_preview("1.0.2")

    assert same.release_kind == "same_version_republish"
    assert same.head_commit == "a" * 40
    assert same.current_tag_commit == "b" * 40
    assert same.mismatched_declarations == ()
    assert upgrade.release_kind == "version_upgrade"
    assert upgrade.current_tag_commit is None
    assert upgrade.mismatched_declarations == (
        "Chub 项目版本",
        "Codex Runtime 版本",
        "微信编排版本",
        "Chub 默认配置版本",
        "Codex Runtime Chub 兼容版本",
        "微信编排 Chub 兼容版本",
    )

    history = settings.deployment_package.artifacts_dir / "history"
    history.mkdir(parents=True)
    (history / "chub-release-1.0.2.json").write_text(
        json.dumps(
            {
                "release_version": "1.0.2",
                "tag_name": "chub-v1.0.2",
                "commit": "c" * 40,
                "built_at": "2026-09-20T16:00:00+00:00",
            }
        ),
        encoding="utf-8",
    )

    blocked = service.release_preview("1.0.1")

    assert blocked.release_kind == "requires_repair"


def test_release_rejects_without_a_local_git_committer_identity(
    settings,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings.deployment_package.artifacts_dir = tmp_path / "releases"
    settings.deployment_package.state_file = tmp_path / "state.json"
    service = DeploymentPackageService(settings)

    def git(*arguments: str, **_kwargs: object) -> str:
        if arguments[:2] == ("rev-parse", "--is-inside-work-tree"):
            return "true"
        if arguments[:2] == ("status", "--porcelain=v1"):
            return ""
        if arguments == ("rev-parse", "HEAD"):
            return "a" * 40
        if arguments[:1] == ("check-ref-format",):
            return ""
        if arguments == ("var", "GIT_COMMITTER_IDENT"):
            return ""
        raise AssertionError(arguments)

    monkeypatch.setattr(service, "_git", git)
    source_versions = service._source_versions()
    configuration = DeploymentPackageConfiguration(
        chub_release_version=source_versions.chub,
        runtime_implementation_id="codex-010000",
        runtime_release_version=source_versions.runtime,
        runtime_description="发布测试。",
        weixin_release_version=source_versions.weixin,
    )

    with pytest.raises(ApiError) as error:
        service._require_git_release_baseline(configuration)

    assert error.value.code == "release_git_identity_unavailable"


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
        assert "modules/runtime/codex-runtime/chub-module.json" in archive.namelist()
        assert "modules/orchestration/weixin-refinement/chub-capability-orchestration.json" in archive.namelist()


def test_release_record_replaces_the_previous_same_version_record(
    settings, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings.deployment_package.artifacts_dir = tmp_path / "releases"
    settings.deployment_package.state_file = tmp_path / "state.json"
    service = DeploymentPackageService(settings)
    service.output_dir.mkdir()
    first_artifact = service.output_dir / "chub-release-1.2.3-202609161122-a1b2c3d4e5f6.zip"
    second_artifact = service.output_dir / "chub-release-1.2.3-202609161123-b1c2d3e4f5a6.zip"
    first_artifact.write_bytes(b"first")
    second_artifact.write_bytes(b"second")
    baseline = SimpleNamespace(tag_name="chub-v1.2.3", commit="a" * 40, previous_tag_ref=None, previous_tag_commit=None)
    monkeypatch.setattr(service, "_dependency_snapshot", lambda: ())
    configuration = service.status().configuration.model_copy(update={"release_note": "首次发布。"})
    first = service._write_release_record(
        baseline,
        SimpleNamespace(artifact=first_artifact, build_id="202609161122-a1b2c3d4e5f6", built_at=datetime.now(timezone.utc), sha256="b" * 64, bundled_modules=()),
        configuration,
    )
    second = service._write_release_record(
        baseline,
        SimpleNamespace(artifact=second_artifact, build_id="202609161123-b1c2d3e4f5a6", built_at=datetime.now(timezone.utc), sha256="c" * 64, bundled_modules=()),
        configuration.model_copy(update={"release_note": "重新发布。"}),
    )

    assert first.path == second.path
    assert first.previous is None
    assert second.previous is not None
    assert json.loads(second.path.read_text())["release_note"] == "重新发布。"
    assert first_artifact.exists() and second_artifact.exists()


def test_release_cleanup_replaces_only_same_version_artifacts(settings, tmp_path: Path) -> None:
    settings.deployment_package.artifacts_dir = tmp_path / "releases"
    service = DeploymentPackageService(settings)
    service.output_dir.mkdir()
    current = service.output_dir / "chub-release-1.2.3-202609161123-b1c2d3e4f5a6.zip"
    previous = service.output_dir / "chub-release-1.2.3-202609161122-a1b2c3d4e5f6.zip"
    other = service.output_dir / "chub-release-1.2.4-202609161122-a1b2c3d4e5f6.zip"
    for artifact in (current, previous, other):
        artifact.write_bytes(b"release")

    assert service._remove_superseded_release_artifacts(current, "1.2.3") == 0

    assert current.exists()
    assert not previous.exists()
    assert other.exists()


def test_release_cleanup_reports_unremovable_same_version_artifact(
    settings,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings.deployment_package.artifacts_dir = tmp_path / "releases"
    service = DeploymentPackageService(settings)
    service.output_dir.mkdir()
    current = service.output_dir / "chub-release-1.2.3-202609161123-b1c2c3d4e5f6.zip"
    previous = service.output_dir / "chub-release-1.2.3-202609161122-a1b2c3d4e5f6.zip"
    current.write_bytes(b"current")
    previous.write_bytes(b"previous")
    original_unlink = Path.unlink

    def unlink(path: Path, *args, **kwargs) -> None:
        if path == previous:
            raise OSError("permission denied")
        original_unlink(path, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", unlink)

    assert service._remove_superseded_release_artifacts(current, "1.2.3") == 1
    assert previous.exists()


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
    app = create_app(settings)
    opened: list[bool] = []
    app.state.deployment_package.open_output_directory = lambda: opened.append(True)
    transport = httpx.ASGITransport(app=app)
    payload = {
        "release_version": "4.0.0",
        "include_development_sources": True,
    }

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        before = await client.get("/api/settings/deployment-package")
        updated = await client.put("/api/settings/deployment-package", json=payload)
        rejected_note = await client.put(
            "/api/settings/deployment-package",
            json={**payload, "release_note": "不应保存。"},
        )
        visibility_before = await client.get(
            "/api/settings/deployment-package/release-note-session"
        )
        visibility_updated = await client.put(
            "/api/settings/deployment-package/release-note-session",
            json={"show_sessions": True},
        )
        opened_output = await client.post("/api/settings/deployment-package/open-output")

    assert before.status_code == 200
    assert before.json()["data"]["app_version"] == settings.app.version
    assert set(before.json()["data"]["source_versions"]) == {"chub", "runtime", "weixin"}
    assert updated.status_code == 200
    assert rejected_note.status_code == 422
    assert visibility_before.json()["data"] == {"show_sessions": False}
    assert visibility_updated.json()["data"] == {"show_sessions": True}
    assert opened_output.status_code == 200
    assert opened_output.json()["data"] == {"status": "succeeded"}
    assert opened == [True]
    configuration = updated.json()["data"]["configuration"]
    assert configuration["chub_release_version"] == payload["release_version"]
    assert configuration["runtime_release_version"] == payload["release_version"]
    assert configuration["weixin_release_version"] == payload["release_version"]
    assert configuration["include_development_sources"] is payload["include_development_sources"]
    assert configuration["release_note"] == ""
    assert configuration["runtime_implementation_id"] == "codex-010000"
    assert "AI Session" in configuration["runtime_description"]
    assert rejected_note.json()["error"]["code"] == "invalid_request"


@pytest.mark.anyio
async def test_deployment_package_build_api_uses_the_current_request_configuration(
    settings,
    tmp_path: Path,
) -> None:
    settings.deployment_package.artifacts_dir = tmp_path / "releases"
    settings.deployment_package.state_file = tmp_path / "state.json"
    app = create_app(settings)
    captured: dict[str, object] = {}

    def start(*, source_ip: str, configuration) -> object:
        captured["source_ip"] = source_ip
        captured["configuration"] = configuration
        return app.state.deployment_package.status()

    app.state.deployment_package.start = start
    transport = httpx.ASGITransport(app=app)
    payload = {
        "release_version": "4.0.0",
        "include_development_sources": True,
        "release_note": "仅供本次发布的说明。",
    }

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post("/api/settings/deployment-package/build", json=payload)

    assert response.status_code == 200
    configuration = captured["configuration"]
    assert configuration.chub_release_version == payload["release_version"]
    assert configuration.include_development_sources is True
    assert configuration.release_note == payload["release_note"]


def test_release_build_snapshots_configuration_at_start(
    settings,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings.deployment_package.artifacts_dir = tmp_path / "releases"
    settings.deployment_package.state_file = tmp_path / "state.json"
    service = DeploymentPackageService(settings)
    monkeypatch.setattr(service, "_require_idle_worker", lambda: None)
    monkeypatch.setattr(service, "_require_release_version_not_lower", lambda _version: None)
    monkeypatch.setattr(service, "_require_git_release_preconditions", lambda: "a" * 40)
    monkeypatch.setattr(service, "_commit_release_version_if_needed", lambda _version: False)
    monkeypatch.setattr(
        service,
        "_require_git_release_baseline",
        lambda _configuration, **_kwargs: SimpleNamespace(commit="a" * 40, tag_name="chub-v1.0.0", previous_tag_ref=None, previous_tag_commit=None, version_commit_created=False),
    )
    initial = service.status().configuration.model_copy(
        update={"chub_release_version": "1.0.0", "release_note": "冻结说明。"}
    )
    service.save_configuration(initial)
    _DeferredThread.calls = []
    monkeypatch.setattr("app.services.deployment_package.threading.Thread", _DeferredThread)

    service.start(source_ip="127.0.0.1", configuration=initial)
    service.save_configuration(initial.model_copy(update={"chub_release_version": "2.0.0"}))

    assert _DeferredThread.calls[0][1][2].chub_release_version == "1.0.0"


def test_release_records_a_failed_preparation_after_the_version_commit(
    settings,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings.deployment_package.artifacts_dir = tmp_path / "releases"
    settings.deployment_package.state_file = tmp_path / "state.json"
    service = DeploymentPackageService(settings)
    configuration = service.status().configuration.model_copy(
        update={"chub_release_version": "1.0.1", "release_note": "升级发布。"}
    )
    monkeypatch.setattr(service, "_require_idle_worker", lambda: None)
    monkeypatch.setattr(service, "_require_release_version_not_lower", lambda _version: None)
    monkeypatch.setattr(service, "_require_git_release_preconditions", lambda: "a" * 40)
    monkeypatch.setattr(service, "_commit_release_version_if_needed", lambda _version: True)
    monkeypatch.setattr(
        service,
        "_require_git_release_baseline",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            ApiError(409, "release_source_version_mismatch", "版本声明尚未完成本次发布升级，请重新发起发布。")
        ),
    )

    with pytest.raises(ApiError, match="版本声明尚未完成"):
        service.start(source_ip="127.0.0.1", configuration=configuration)

    operation = service.status().operation
    assert operation is not None
    assert operation.status == "failed"
    assert operation.target_version == "1.0.1"
    assert operation.version_commit_created is True
    assert "版本声明已提交" in operation.message


def test_release_rejects_a_busy_worker_before_registering_the_operation(
    settings,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings.deployment_package.artifacts_dir = tmp_path / "releases"
    settings.deployment_package.state_file = tmp_path / "state.json"
    service = DeploymentPackageService(settings)
    monkeypatch.setattr(
        "app.services.deployment_package.read_health_sync",
        lambda _settings: {
            "success": True,
            "data": {"status": "ready", "active_tasks": 1, "queued_tasks": 0},
        },
    )

    for publish in (service.start, service.publish):
        with pytest.raises(ApiError) as error:
            publish(source_ip="127.0.0.1")

        assert error.value.status_code == 409
        assert error.value.code == "release_worker_busy"
    assert not settings.deployment_package.state_file.exists()


def test_cli_release_runs_the_registered_publish_once(
    settings,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings.deployment_package.artifacts_dir = tmp_path / "releases"
    settings.deployment_package.state_file = tmp_path / "state.json"
    service = DeploymentPackageService(settings)
    operation = SimpleNamespace(operation_id="release-operation")
    release_configuration = SimpleNamespace()
    baseline = SimpleNamespace()
    begin_calls: list[str] = []
    run_calls: list[tuple[object, ...]] = []
    completed = SimpleNamespace(operation=SimpleNamespace(status="succeeded"))

    def begin(*, source_ip: str, configuration=None):
        assert configuration is None
        begin_calls.append(source_ip)
        return operation, release_configuration, baseline

    monkeypatch.setattr(service, "_begin_publish", begin)
    monkeypatch.setattr(service, "_run", lambda *args: run_calls.append(args))
    monkeypatch.setattr(service, "status", lambda: completed)

    assert service.publish(source_ip="127.0.0.1") is completed
    assert begin_calls == ["127.0.0.1"]
    assert run_calls == [
        ("release-operation", "127.0.0.1", release_configuration, baseline)
    ]


def test_release_tag_rollback_restores_the_previous_local_ref(
    settings,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings.deployment_package.artifacts_dir = tmp_path / "releases"
    settings.deployment_package.state_file = tmp_path / "state.json"
    service = DeploymentPackageService(settings)
    calls: list[tuple[str, ...]] = []
    updated = SimpleNamespace(
        baseline=SimpleNamespace(
            tag_name="chub-v1.2.3",
            previous_tag_ref="old-tag-object",
        ),
        current_ref="new-tag-object",
    )
    monkeypatch.setattr(
        service,
        "_git",
        lambda *arguments, **_kwargs: calls.append(arguments) or "",
    )

    service._restore_local_release_tag(updated)

    assert calls == [
        (
            "update-ref",
            "refs/tags/chub-v1.2.3",
            "old-tag-object",
            "new-tag-object",
        )
    ]


def test_release_updates_the_local_annotated_tag_for_a_republished_version(
    settings,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings.deployment_package.artifacts_dir = tmp_path / "releases"
    settings.deployment_package.state_file = tmp_path / "state.json"
    service = DeploymentPackageService(settings)
    artifact = tmp_path / "chub-release-1.2.3-20260916112233.zip"
    artifact.write_bytes(b"release")
    baseline = SimpleNamespace(
        tag_name="chub-v1.2.3",
        commit="a" * 40,
        previous_tag_ref="old-tag-object",
    )
    built = SimpleNamespace(
        artifact=artifact,
        build_id="20260916112233",
        sha256="b" * 64,
    )
    calls: list[tuple[str, ...]] = []

    def git(*arguments: str, **_kwargs: object) -> str:
        calls.append(arguments)
        if arguments[:3] == ("rev-parse", "--verify", "-q"):
            return "old-tag-object"
        if arguments[:3] == ("rev-parse", "--verify", "refs/tags/chub-v1.2.3"):
            return "new-tag-object"
        return ""

    monkeypatch.setattr(service, "_git", git)

    updated = service._update_local_release_tag(baseline, built)

    assert updated.current_ref == "new-tag-object"
    assert ("tag", "-f", "-a", "chub-v1.2.3", "a" * 40) == calls[1][:5]


def test_release_cli_uses_the_guarded_publish_path(
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

    assert (
        chub_release_zip.main(
            load_settings_fn=lambda: "settings",
            deployment_package_service=_Service,
        )
        == 0
    )
    assert calls == ["127.0.0.1"]
    assert json.loads(capsys.readouterr().out)["artifact_name"] == "chub-release-1.0.0.zip"


def test_release_cli_reports_a_worker_gate_failure_without_a_traceback(
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

    assert (
        chub_release_zip.main(
            load_settings_fn=lambda: "settings",
            deployment_package_service=_Service,
        )
        == 1
    )
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
    configuration = service.status().configuration.model_copy(update={"release_note": "重试说明。"})
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
    monkeypatch.setattr(retried_service, "_require_release_version_not_lower", lambda _version: None)
    monkeypatch.setattr(retried_service, "_require_git_release_preconditions", lambda: "a" * 40)
    monkeypatch.setattr(retried_service, "_commit_release_version_if_needed", lambda _version: False)
    monkeypatch.setattr(
        retried_service,
        "_require_git_release_baseline",
        lambda _configuration, **_kwargs: SimpleNamespace(commit="a" * 40, tag_name="chub-v1.0.0", previous_tag_ref=None, previous_tag_commit=None, version_commit_created=False),
    )
    retried = retried_service.start(source_ip="127.0.0.1", configuration=configuration)
    assert retried.operation is not None
    assert retried.operation.status == "requested"
