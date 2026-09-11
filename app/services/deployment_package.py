from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

from app.core.config import PROJECT_ROOT, Settings
from app.core.response import ApiError
from app.services.operation_log import write_operation

FORMAL_CODEX_IMPLEMENTATION_ID = "codex-010000"
FORMAL_CODEX_DESCRIPTION = "Chub Codex Runtime：提供 AI Session、Quick Worker 任务执行和模型配置能力。"

def _now() -> datetime:
    return datetime.now(timezone.utc)


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class DeploymentPackageConfiguration(_StrictModel):
    chub_release_version: str = Field(min_length=1, max_length=64)
    runtime_implementation_id: str = Field(pattern=r"^codex-[0-9]{6}$")
    runtime_release_version: str = Field(min_length=1, max_length=64)
    runtime_description: str = Field(min_length=1, max_length=300)
    weixin_release_version: str = Field(min_length=1, max_length=64)
    include_development_sources: bool = False


class DeploymentPackageOperation(_StrictModel):
    operation_id: str
    status: str
    message: str
    started_at: datetime | None = None
    finished_at: datetime | None = None
    artifact_name: str | None = None
    artifact_size: int | None = None
    sha256: str | None = None


class DeploymentPackageStatus(_StrictModel):
    app_version: str
    configuration: DeploymentPackageConfiguration
    output_directory: str
    operation: DeploymentPackageOperation | None = None


class _State(_StrictModel):
    configuration: DeploymentPackageConfiguration
    operation: DeploymentPackageOperation | None = None


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
            message="正式部署包构建因服务重启而中断，可重新生成。",
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
                configuration=state.configuration,
                output_directory=str(self.output_dir),
                operation=state.operation,
            )

    def save_configuration(self, configuration: DeploymentPackageConfiguration) -> DeploymentPackageStatus:
        with self._lock:
            state = self._read()
            state.configuration = configuration
            self._write(state)
            return self.status()

    def start(self, *, source_ip: str) -> DeploymentPackageStatus:
        with self._lock:
            state = self._recover_interrupted_operation(self._read())
            if state.operation is not None and state.operation.status in {"requested", "started"}:
                return self.status()
            configuration = state.configuration.model_copy(deep=True)
            operation = DeploymentPackageOperation(
                operation_id=uuid4().hex,
                status="requested",
                message="正式部署包已登记，正在后台构建。",
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
            thread = threading.Thread(
                target=self._run,
                args=(operation.operation_id, source_ip, configuration),
                daemon=True,
                name="chub-deployment-package",
            )
            thread.start()
            return self.status()

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
            state.operation.message = "正在构建主包和正式插件 ZIP。"
            self._write(state)
        write_operation(operation_id=operation_id, action="build_deployment_package", status="started", target="chub-release", source_ip=source_ip)
        try:
            artifact, digest = self._build(configuration)
            with self._lock:
                state = self._read()
                state.operation = DeploymentPackageOperation(
                    operation_id=operation_id, status="succeeded", message="正式部署包已生成。",
                    started_at=state.operation.started_at if state.operation else _now(), finished_at=_now(),
                    artifact_name=artifact.name, artifact_size=artifact.stat().st_size, sha256=digest,
                )
                self._write(state)
            write_operation(operation_id=operation_id, action="build_deployment_package", status="succeeded", target=artifact.name, source_ip=source_ip)
        except Exception:
            with self._lock:
                state = self._read()
                state.operation = DeploymentPackageOperation(
                    operation_id=operation_id, status="failed", message="正式部署包构建失败，请查看操作日志。",
                    started_at=state.operation.started_at if state.operation else _now(), finished_at=_now(),
                )
                self._write(state)
            write_operation(operation_id=operation_id, action="build_deployment_package", status="failed", target="chub-release", source_ip=source_ip, reason="build_failed")
        finally:
            with self._lock:
                if self._active_operation_id == operation_id:
                    self._active_operation_id = None

    def _build(self, configuration: DeploymentPackageConfiguration) -> tuple[Path, str]:
        self.output_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="chub-release-", dir=self.output_dir.parent) as temp:
            root = Path(temp)
            modules = root / "bundled-modules"
            modules.mkdir()
            runtime_zip = modules / "codex-runtime.zip"
            weixin_zip = modules / "weixin-refinement.zip"
            subprocess.run([sys.executable, str(PROJECT_ROOT / "scripts" / "build_codex_runtime_zip.py"), "--output", str(runtime_zip), "--implementation-id", configuration.runtime_implementation_id, "--version", configuration.runtime_release_version, "--description", configuration.runtime_description], cwd=PROJECT_ROOT, check=True, capture_output=True, text=True, timeout=60)
            subprocess.run([sys.executable, str(PROJECT_ROOT / "scripts" / "build_weixin_orchestration_plugin_zip.py"), "--output", str(weixin_zip), "--version", configuration.weixin_release_version], cwd=PROJECT_ROOT, check=True, capture_output=True, text=True, timeout=60)
            timestamp = _now().strftime("%Y%m%d%H%M%S")
            name = f"chub-release-{configuration.chub_release_version}-{timestamp}.zip"
            destination = self.output_dir / name
            temporary = root / name
            manifest: dict[str, object] = {"chub_release_version": configuration.chub_release_version, "built_at": _now().isoformat(), "include_development_sources": configuration.include_development_sources, "files": {}}
            with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_DEFLATED) as archive:
                self._add_sources(archive, manifest, configuration.include_development_sources)
                self._add_file(
                    archive,
                    manifest,
                    PROJECT_ROOT / "docs" / "DEPLOY_WITH_AI.md",
                    Path("DEPLOY_WITH_AI.md"),
                )
                for module in (runtime_zip, weixin_zip):
                    self._add_file(archive, manifest, module, Path("bundled-modules") / module.name)
                archive.writestr("release-manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
            os.chmod(temporary, 0o600)
            os.replace(temporary, destination)
        digest = hashlib.sha256(destination.read_bytes()).hexdigest()
        return destination, digest

    def _add_sources(self, archive: zipfile.ZipFile, manifest: dict[str, object], include_development_sources: bool) -> None:
        paths = ["app", "config", "scripts", "docs", "integrations/openclaw", "README.md", "main.py", "pyproject.toml", "requirements.txt"]
        if include_development_sources:
            paths.extend(["runtime-modules", "orchestration-modules"])
        for value in paths:
            source = PROJECT_ROOT / value
            if source.is_file():
                self._add_file(archive, manifest, source, Path(value))
            elif source.is_dir():
                for item in sorted(source.rglob("*")):
                    if self._skip(item):
                        continue
                    if item.is_symlink():
                        raise OSError("release sources must not contain symbolic links")
                    if not item.is_file():
                        continue
                    self._add_file(archive, manifest, item, item.relative_to(PROJECT_ROOT))

    @staticmethod
    def _skip(path: Path) -> bool:
        return "__pycache__" in path.parts or "node_modules" in path.parts or path.suffix in {".pyc", ".pyo"} or path.name.endswith(".local.yaml")

    @staticmethod
    def _add_file(archive: zipfile.ZipFile, manifest: dict[str, object], source: Path, target: Path) -> None:
        if source.is_symlink() or not source.is_file():
            raise OSError("release source is unsafe")
        data = source.read_bytes()
        archive.writestr(str(target), data)
        files = manifest["files"]
        assert isinstance(files, dict)
        files[str(target)] = hashlib.sha256(data).hexdigest()
