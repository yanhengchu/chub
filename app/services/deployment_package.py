from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import threading
import tomllib
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

from app.ai_runtime.runtime_plugin_packages import RuntimePluginService
from app.core.config import PROJECT_ROOT, Settings
from app.core.response import ApiError
from app.services.operation_log import write_operation
from app.services.weixin_orchestration_plugins import WeixinOrchestrationPluginService

FORMAL_CODEX_IMPLEMENTATION_ID = "codex-010000"
FORMAL_CODEX_DESCRIPTION = "Chub Codex Runtime：提供 AI Session、Quick Worker 任务执行和模型配置能力。"
RELEASE_VERSION_PATTERN = r"^[0-9A-Za-z][0-9A-Za-z.+-]{0,63}$"
_PYPROJECT_VERSION_PATTERN = re.compile(r'(?m)^version\s*=\s*(["\']).*?\1\s*$')

def _now() -> datetime:
    return datetime.now(timezone.utc)


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class DeploymentPackageConfiguration(_StrictModel):
    chub_release_version: str = Field(pattern=RELEASE_VERSION_PATTERN)
    runtime_implementation_id: str = Field(pattern=r"^codex-[0-9]{6}$")
    runtime_release_version: str = Field(pattern=RELEASE_VERSION_PATTERN)
    runtime_description: str = Field(min_length=1, max_length=300)
    weixin_release_version: str = Field(pattern=RELEASE_VERSION_PATTERN)
    include_development_sources: bool = False


class DeploymentPackageBundledModule(_StrictModel):
    kind: Literal["runtime", "weixin-orchestration"]
    artifact_name: str = Field(min_length=1, max_length=255)
    module_id: str = Field(min_length=1, max_length=64)
    implementation_id: str | None = Field(default=None, min_length=1, max_length=64)
    version: str = Field(min_length=1, max_length=64)
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")


class DeploymentPackageOperation(_StrictModel):
    operation_id: str
    status: str
    message: str
    started_at: datetime | None = None
    finished_at: datetime | None = None
    artifact_name: str | None = None
    artifact_size: int | None = None
    sha256: str | None = None
    build_id: str | None = None
    built_at: datetime | None = None
    bundled_modules: tuple[DeploymentPackageBundledModule, ...] = ()


class DeploymentPackageSourceVersions(_StrictModel):
    chub: str = Field(pattern=RELEASE_VERSION_PATTERN)
    runtime: str = Field(pattern=RELEASE_VERSION_PATTERN)
    weixin: str = Field(pattern=RELEASE_VERSION_PATTERN)


class DeploymentPackageStatus(_StrictModel):
    app_version: str
    source_versions: DeploymentPackageSourceVersions
    configuration: DeploymentPackageConfiguration
    output_directory: str
    operation: DeploymentPackageOperation | None = None


class _State(_StrictModel):
    configuration: DeploymentPackageConfiguration
    operation: DeploymentPackageOperation | None = None


@dataclass(frozen=True)
class _VersionUpdate:
    path: Path
    before: bytes
    after: bytes


@dataclass(frozen=True)
class _BuiltDeploymentPackage:
    artifact: Path
    sha256: str
    build_id: str
    built_at: datetime
    bundled_modules: tuple[DeploymentPackageBundledModule, ...]
    version_updates: tuple[_VersionUpdate, ...]


class DeploymentPackageService:
    """Own the local release settings and one bounded package build at a time."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.state_path = settings.deployment_package.state_file
        self.output_dir = settings.deployment_package.artifacts_dir
        self._lock = threading.RLock()
        self._active_operation_id: str | None = None

    def _defaults(self) -> DeploymentPackageConfiguration:
        return DeploymentPackageConfiguration(
            chub_release_version=self.settings.app.version,
            runtime_implementation_id=FORMAL_CODEX_IMPLEMENTATION_ID,
            runtime_release_version="1.0.0",
            runtime_description=FORMAL_CODEX_DESCRIPTION,
            weixin_release_version="1.0.0",
        )

    def _read(self) -> _State:
        try:
            raw = self.state_path.read_bytes()
        except FileNotFoundError:
            return _State(configuration=self._defaults())
        except OSError as exc:
            raise ApiError(503, "deployment_package_state_unavailable", "部署包发布配置暂时无法读取。") from exc
        if len(raw) > 64 * 1024:
            raise ApiError(503, "deployment_package_state_invalid", "部署包发布配置无效。")
        try:
            return _State.model_validate_json(raw)
        except ValueError as exc:
            raise ApiError(503, "deployment_package_state_invalid", "部署包发布配置无效。") from exc

    def _write(self, state: _State) -> None:
        self.state_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=self.state_path.parent, delete=False
        ) as file:
            json.dump(state.model_dump(mode="json"), file, ensure_ascii=False, separators=(",", ":"))
            file.write("\n")
            temporary = Path(file.name)
        try:
            os.chmod(temporary, 0o600)
            os.replace(temporary, self.state_path)
        finally:
            temporary.unlink(missing_ok=True)

    def _recover_interrupted_operation(self, state: _State) -> _State:
        operation = state.operation
        if (
            operation is None
            or operation.status not in {"requested", "started"}
            or operation.operation_id == self._active_operation_id
        ):
            return state
        state.operation = DeploymentPackageOperation(
            operation_id=operation.operation_id,
            status="failed",
            message="版本发布因服务重启而中断，可重新发布。",
            started_at=operation.started_at,
            finished_at=_now(),
        )
        self._write(state)
        write_operation(
            operation_id=operation.operation_id,
            action="build_deployment_package",
            status="failed",
            target="chub-release",
            source_ip="unknown",
            reason="interrupted_by_service_restart",
        )
        return state

    def status(self) -> DeploymentPackageStatus:
        with self._lock:
            state = self._recover_interrupted_operation(self._read())
            return DeploymentPackageStatus(
                app_version=self.settings.app.version,
                source_versions=self._source_versions(),
                configuration=state.configuration,
                output_directory=str(self.output_dir),
                operation=state.operation,
            )

    @staticmethod
    def _source_versions() -> DeploymentPackageSourceVersions:
        try:
            with (PROJECT_ROOT / "pyproject.toml").open("rb") as file:
                chub = tomllib.load(file)["project"]["version"]
            runtime = json.loads(
                (PROJECT_ROOT / "runtime-modules" / "codex-runtime" / "chub-module.json").read_text("utf-8")
            )["version"]
            weixin = json.loads(
                (
                    PROJECT_ROOT
                    / "orchestration-modules"
                    / "weixin-refinement"
                    / "chub-capability-orchestration.json"
                ).read_text("utf-8")
            )["version"]
            return DeploymentPackageSourceVersions(chub=chub, runtime=runtime, weixin=weixin)
        except (KeyError, OSError, TypeError, ValueError) as exc:
            raise ApiError(503, "deployment_package_versions_unavailable", "项目版本暂时无法读取。") from exc

    def save_configuration(self, configuration: DeploymentPackageConfiguration) -> DeploymentPackageStatus:
        with self._lock:
            state = self._read()
            state.configuration = configuration
            self._write(state)
            return self.status()

    def start(self, *, source_ip: str) -> DeploymentPackageStatus:
        with self._lock:
            publish = self._begin_publish(source_ip=source_ip)
            if publish is None:
                return self.status()
            operation, configuration = publish
            thread = threading.Thread(
                target=self._run,
                args=(operation.operation_id, source_ip, configuration),
                daemon=True,
                name="chub-deployment-package",
            )
            thread.start()
            return self.status()

    def publish(self, *, source_ip: str) -> DeploymentPackageStatus:
        """Run the same guarded release flow synchronously for the fixed CLI entry."""
        with self._lock:
            publish = self._begin_publish(source_ip=source_ip)
            if publish is None:
                return self.status()
            operation, configuration = publish
        self._run(operation.operation_id, source_ip, configuration)
        return self.status()

    def _begin_publish(
        self,
        *,
        source_ip: str,
    ) -> tuple[DeploymentPackageOperation, DeploymentPackageConfiguration] | None:
        state = self._recover_interrupted_operation(self._read())
        if state.operation is not None and state.operation.status in {"requested", "started"}:
            return None
        self._require_idle_worker()
        configuration = state.configuration.model_copy(deep=True)
        operation = DeploymentPackageOperation(
            operation_id=uuid4().hex,
            status="requested",
            message="版本发布已登记，正在后台处理。",
            started_at=_now(),
        )
        state.operation = operation
        self._write(state)
        write_operation(
            operation_id=operation.operation_id,
            action="build_deployment_package",
            status="requested",
            target="chub-release",
            source_ip=source_ip,
        )
        self._active_operation_id = operation.operation_id
        return operation, configuration

    @staticmethod
    def _require_idle_worker() -> None:
        try:
            result = subprocess.run(
                [str(PROJECT_ROOT / "scripts" / "chub"), "worker", "health"],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                timeout=5,
                check=False,
            )
            if result.returncode != 0 or len(result.stdout) > 16 * 1024:
                raise ValueError("worker health unavailable")
            payload = json.loads(result.stdout)
            data = payload.get("data") if payload.get("success") is True else None
            if not isinstance(data, dict):
                raise ValueError("worker health unavailable")
            active = data.get("active_tasks")
            queued = data.get("queued_tasks")
            if (
                data.get("status") != "ready"
                or not isinstance(active, int)
                or isinstance(active, bool)
                or active < 0
                or not isinstance(queued, int)
                or isinstance(queued, bool)
                or queued < 0
            ):
                raise ValueError("worker health unavailable")
        except (OSError, subprocess.TimeoutExpired, ValueError, json.JSONDecodeError):
            raise ApiError(503, "release_worker_status_unavailable", "无法确认 Quick Worker 空闲，本次不能发布。") from None
        if active or queued:
            raise ApiError(
                409,
                "release_worker_busy",
                f"Quick Worker 仍有 {active} 个执行中、{queued} 个排队任务；完成后再发布。",
            )

    def _run(
        self,
        operation_id: str,
        source_ip: str,
        configuration: DeploymentPackageConfiguration,
    ) -> None:
        with self._lock:
            state = self._read()
            if state.operation is None or state.operation.operation_id != operation_id:
                return
            state.operation.status = "started"
            state.operation.message = "正在发布主包和正式插件 ZIP。"
            self._write(state)
        write_operation(operation_id=operation_id, action="build_deployment_package", status="started", target="chub-release", source_ip=source_ip)
        built: _BuiltDeploymentPackage | None = None
        versions_synchronized = False
        original_app_version = self.settings.app.version
        failure_message = "版本发布失败，请检查项目版本文件和操作日志。"
        failure_reason = "build_failed"
        try:
            built = self._build(configuration)
            self._synchronize_project_versions(built.version_updates)
            versions_synchronized = True
            self.settings.app.version = configuration.chub_release_version
            with self._lock:
                state = self._read()
                state.operation = DeploymentPackageOperation(
                    operation_id=operation_id, status="succeeded", message="版本已发布，项目版本与正式包已同步。",
                    started_at=state.operation.started_at if state.operation else _now(), finished_at=_now(),
                    artifact_name=built.artifact.name,
                    artifact_size=built.artifact.stat().st_size,
                    sha256=built.sha256,
                    build_id=built.build_id,
                    built_at=built.built_at,
                    bundled_modules=built.bundled_modules,
                )
                self._write(state)
            write_operation(operation_id=operation_id, action="build_deployment_package", status="succeeded", target=built.artifact.name, source_ip=source_ip)
        except Exception:
            if versions_synchronized and built is not None:
                try:
                    self._restore_project_versions(built.version_updates)
                    self.settings.app.version = original_app_version
                except OSError:
                    failure_message = "版本发布失败，项目版本回滚未完成，请检查操作日志。"
                    failure_reason = "project_version_rollback_failed"
            if built is not None and failure_reason != "project_version_rollback_failed":
                try:
                    built.artifact.unlink(missing_ok=True)
                except OSError:
                    pass
            with self._lock:
                state = self._read()
                state.operation = DeploymentPackageOperation(
                    operation_id=operation_id, status="failed", message=failure_message,
                    started_at=state.operation.started_at if state.operation else _now(), finished_at=_now(),
                )
                self._write(state)
            write_operation(operation_id=operation_id, action="build_deployment_package", status="failed", target="chub-release", source_ip=source_ip, reason=failure_reason)
        finally:
            with self._lock:
                if self._active_operation_id == operation_id:
                    self._active_operation_id = None

    def _build(self, configuration: DeploymentPackageConfiguration) -> _BuiltDeploymentPackage:
        self.output_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        version_updates = self._version_updates(configuration)
        source_overrides = {update.path: update.after for update in version_updates}
        with tempfile.TemporaryDirectory(prefix="chub-release-", dir=self.output_dir.parent) as temp:
            root = Path(temp)
            modules = root / "bundled-modules"
            modules.mkdir()
            built_at = _now()
            timestamp = built_at.strftime("%Y%m%d%H%M%S")
            runtime_zip = modules / f"codex-runtime-release-{configuration.runtime_release_version}-{timestamp}.zip"
            weixin_zip = modules / f"weixin-refinement-release-{configuration.weixin_release_version}-{timestamp}.zip"
            subprocess.run([sys.executable, str(PROJECT_ROOT / "scripts" / "build_codex_runtime_zip.py"), "--output", str(runtime_zip), "--implementation-id", configuration.runtime_implementation_id, "--version", configuration.runtime_release_version, "--description", configuration.runtime_description, "--chub-version", configuration.chub_release_version], cwd=PROJECT_ROOT, check=True, capture_output=True, text=True, timeout=60)
            subprocess.run([sys.executable, str(PROJECT_ROOT / "scripts" / "build_weixin_orchestration_plugin_zip.py"), "--output", str(weixin_zip), "--version", configuration.weixin_release_version, "--chub-version", configuration.chub_release_version], cwd=PROJECT_ROOT, check=True, capture_output=True, text=True, timeout=60)
            bundled_modules = self._validate_bundled_modules(
                root,
                runtime_zip,
                weixin_zip,
                configuration.chub_release_version,
            )
            name = f"chub-release-{configuration.chub_release_version}-{timestamp}.zip"
            destination = self.output_dir / name
            temporary = root / name
            manifest: dict[str, object] = {
                "chub_release_version": configuration.chub_release_version,
                "build_id": timestamp,
                "built_at": built_at.isoformat(),
                "include_development_sources": configuration.include_development_sources,
                "bundled_modules": [item.model_dump(mode="json") for item in bundled_modules],
                "files": {},
            }
            with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_DEFLATED) as archive:
                self._add_sources(
                    archive,
                    manifest,
                    configuration.include_development_sources,
                    source_overrides,
                )
                self._add_file(
                    archive,
                    manifest,
                    PROJECT_ROOT / "docs" / "DEPLOY_WITH_AI.md",
                    Path("DEPLOY_WITH_AI.md"),
                )
                for module in (runtime_zip, weixin_zip):
                    self._add_file(archive, manifest, module, Path("bundled-modules") / module.name)
                archive.writestr("release-manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
            self._validate_release_archive(temporary, manifest)
            os.chmod(temporary, 0o600)
            os.replace(temporary, destination)
        return _BuiltDeploymentPackage(
            artifact=destination,
            sha256=self._digest_file(destination),
            build_id=timestamp,
            built_at=built_at,
            bundled_modules=bundled_modules,
            version_updates=version_updates,
        )

    def _validate_bundled_modules(
        self,
        root: Path,
        runtime_zip: Path,
        weixin_zip: Path,
        chub_version: str,
    ) -> tuple[DeploymentPackageBundledModule, ...]:
        validation_settings = self.settings.model_copy(deep=True)
        validation_settings.app.version = chub_version
        validation_settings.ai_runtime.modules.install_dir = root / "validation-runtime-modules"
        validation_settings.openclaw.weixin_chub_mode.orchestration_modules_dir = (
            root / "validation-weixin-modules"
        )
        runtime_service = RuntimePluginService(validation_settings)
        runtime_activation = runtime_service.install(
            runtime_zip.read_bytes(), source_name=runtime_zip.name
        )
        try:
            runtime = runtime_activation.installed.manifest
        finally:
            runtime_service.finalize(runtime_activation)
        weixin = WeixinOrchestrationPluginService(validation_settings).inspect_archive(
            weixin_zip.read_bytes(), source_name=weixin_zip.name
        )
        return (
            DeploymentPackageBundledModule(
                kind="runtime",
                artifact_name=runtime_zip.name,
                module_id=runtime.module_id,
                implementation_id=runtime.implementation_id,
                version=runtime.version,
                sha256=self._digest_file(runtime_zip),
            ),
            DeploymentPackageBundledModule(
                kind="weixin-orchestration",
                artifact_name=weixin_zip.name,
                module_id=weixin.module_id,
                version=weixin.version,
                sha256=self._digest_file(weixin_zip),
            ),
        )

    def _version_updates(
        self,
        configuration: DeploymentPackageConfiguration,
    ) -> tuple[_VersionUpdate, ...]:
        pyproject = PROJECT_ROOT / "pyproject.toml"
        settings_example = PROJECT_ROOT / "config" / "settings.example.yaml"
        settings_local = PROJECT_ROOT / "config" / "settings.local.yaml"
        runtime_manifest = PROJECT_ROOT / "runtime-modules" / "codex-runtime" / "chub-module.json"
        weixin_manifest = (
            PROJECT_ROOT
            / "orchestration-modules"
            / "weixin-refinement"
            / "chub-capability-orchestration.json"
        )
        return (
            _VersionUpdate(
                path=pyproject,
                before=pyproject.read_bytes(),
                after=self._replace_pyproject_version(
                    pyproject.read_text("utf-8"), configuration.chub_release_version
                ).encode("utf-8"),
            ),
            _VersionUpdate(
                path=settings_example,
                before=settings_example.read_bytes(),
                after=self._replace_example_app_version(
                    settings_example.read_text("utf-8"), configuration.chub_release_version
                ).encode("utf-8"),
            ),
            _VersionUpdate(
                path=settings_local,
                before=settings_local.read_bytes(),
                after=self._replace_example_app_version(
                    settings_local.read_text("utf-8"), configuration.chub_release_version
                ).encode("utf-8"),
            ),
            _VersionUpdate(
                path=runtime_manifest,
                before=runtime_manifest.read_bytes(),
                after=self._replace_json_versions(
                    runtime_manifest.read_text("utf-8"),
                    version=configuration.runtime_release_version,
                    chub_version=configuration.chub_release_version,
                ),
            ),
            _VersionUpdate(
                path=weixin_manifest,
                before=weixin_manifest.read_bytes(),
                after=self._replace_json_versions(
                    weixin_manifest.read_text("utf-8"),
                    version=configuration.weixin_release_version,
                    chub_version=configuration.chub_release_version,
                ),
            ),
        )

    @staticmethod
    def _replace_pyproject_version(source: str, version: str) -> str:
        updated, count = _PYPROJECT_VERSION_PATTERN.subn(
            f'version = "{version}"', source, count=1
        )
        if count != 1:
            raise OSError("project version declaration is unavailable")
        return updated

    @staticmethod
    def _replace_example_app_version(source: str, version: str) -> str:
        lines = source.splitlines(keepends=True)
        in_app = False
        for index, line in enumerate(lines):
            if line.startswith("app:"):
                in_app = True
                continue
            if in_app and line and not line.startswith((" ", "\t", "\n", "\r")):
                break
            if in_app and line.startswith("  version:"):
                ending = "\r\n" if line.endswith("\r\n") else "\n"
                lines[index] = f'  version: "{version}"{ending}'
                return "".join(lines)
        raise OSError("example app version declaration is unavailable")

    @staticmethod
    def _replace_json_versions(source: str, *, version: str, chub_version: str) -> bytes:
        try:
            manifest = json.loads(source)
        except json.JSONDecodeError as exc:
            raise OSError("module source manifest is invalid") from exc
        if not isinstance(manifest, dict):
            raise OSError("module source manifest is invalid")
        manifest["version"] = version
        manifest["chub_version"] = chub_version
        return (json.dumps(manifest, ensure_ascii=False, indent=2) + "\n").encode("utf-8")

    @staticmethod
    def _synchronize_project_versions(updates: tuple[_VersionUpdate, ...]) -> None:
        for update in updates:
            if update.path.is_symlink() or update.path.read_bytes() != update.before:
                raise OSError("project version sources changed during release")
        applied: list[_VersionUpdate] = []
        try:
            for update in updates:
                DeploymentPackageService._atomic_write(update.path, update.after)
                applied.append(update)
        except OSError:
            try:
                for update in reversed(applied):
                    DeploymentPackageService._atomic_write(update.path, update.before)
            except OSError as exc:
                raise OSError("project version rollback could not be confirmed") from exc
            raise

    @staticmethod
    def _restore_project_versions(updates: tuple[_VersionUpdate, ...]) -> None:
        DeploymentPackageService._synchronize_project_versions(
            tuple(
                _VersionUpdate(path=update.path, before=update.after, after=update.before)
                for update in updates
            )
        )

    @staticmethod
    def _atomic_write(path: Path, data: bytes) -> None:
        mode = path.stat().st_mode & 0o777
        with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as file:
            file.write(data)
            temporary = Path(file.name)
        try:
            os.chmod(temporary, mode)
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)

    @staticmethod
    def _validate_release_archive(archive_path: Path, manifest: dict[str, object]) -> None:
        files = manifest["files"]
        if not isinstance(files, dict):
            raise OSError("release manifest files are invalid")
        with zipfile.ZipFile(archive_path) as archive:
            if archive.testzip() is not None:
                raise OSError("release archive content is invalid")
            names = set(archive.namelist())
            if names != {*files, "release-manifest.json"}:
                raise OSError("release archive manifest does not match contents")
            for target, expected in files.items():
                if not isinstance(target, str) or not isinstance(expected, str):
                    raise OSError("release manifest entry is invalid")
                if hashlib.sha256(archive.read(target)).hexdigest() != expected:
                    raise OSError("release archive digest does not match manifest")

    @staticmethod
    def _digest_file(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as file:
            for chunk in iter(lambda: file.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()

    def _add_sources(
        self,
        archive: zipfile.ZipFile,
        manifest: dict[str, object],
        include_development_sources: bool,
        source_overrides: dict[Path, bytes],
    ) -> None:
        paths = [
            "app",
            "scripts",
            "docs",
            "integrations/openclaw",
            "README.md",
            "main.py",
            "pyproject.toml",
            "requirements.txt",
            "config/settings.example.yaml",
            "config/automations.example.yaml",
            "config/notifications.example.yaml",
            "config/automation_templates",
            "config/system-upgrade.json",
        ]
        if include_development_sources:
            paths.extend(["runtime-modules", "orchestration-modules"])
        for value in paths:
            source = PROJECT_ROOT / value
            if source.is_file():
                self._add_file(
                    archive,
                    manifest,
                    source,
                    Path(value),
                    source_overrides.get(source),
                )
            elif source.is_dir():
                for item in sorted(source.rglob("*")):
                    if self._skip(item):
                        continue
                    if item.is_symlink():
                        raise OSError("release sources must not contain symbolic links")
                    if not item.is_file():
                        continue
                    self._add_file(
                        archive,
                        manifest,
                        item,
                        item.relative_to(PROJECT_ROOT),
                        source_overrides.get(item),
                    )

    @staticmethod
    def _skip(path: Path) -> bool:
        return (
            "__pycache__" in path.parts
            or "node_modules" in path.parts
            or path.suffix in {".pyc", ".pyo"}
            or path.name.startswith(".")
            or path.name.endswith(".local.yaml")
        )

    @staticmethod
    def _add_file(
        archive: zipfile.ZipFile,
        manifest: dict[str, object],
        source: Path,
        target: Path,
        data: bytes | None = None,
    ) -> None:
        if source.is_symlink() or not source.is_file():
            raise OSError("release source is unsafe")
        if data is None:
            data = source.read_bytes()
        archive.writestr(str(target), data)
        files = manifest["files"]
        assert isinstance(files, dict)
        files[str(target)] = hashlib.sha256(data).hexdigest()
