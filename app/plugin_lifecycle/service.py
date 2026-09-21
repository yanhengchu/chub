from __future__ import annotations

import io
import hashlib
import json
import logging
import os
import shutil
import stat
import tempfile
import zipfile
from pathlib import Path
from threading import RLock
from typing import Any

from fastapi import Request

from app.ai_runtime.development_plugins import development_runtime_artifact_id
from app.core.module_sources import registered_module_source, registered_module_sources
from app.core.config import PROJECT_ROOT, Settings
from app.core.response import ApiError

_BUSINESS_MANIFEST = "chub-business-module.json"
LOGGER = logging.getLogger("hub.plugin_lifecycle")


class PluginLifecycleService:
    """Coordinates common operations; plugins retain final-state confirmation."""

    def __init__(
        self,
        settings: Settings,
        ai_session_manager: Any,
    ) -> None:
        self.settings = settings
        self.ai_session_manager = ai_session_manager
        self.path = settings.business_modules.state_file
        self._lock = RLock()

    def list(self, request: Request) -> dict[str, object]:
        with self._lock:
            state = self._read()
            return {"plugins": [self._status(request, key, state) for key in self._plugin_ids()]}

    def imported_plugin_ids(self) -> frozenset[str]:
        with self._lock:
            imports = self._read().get("imports", {})
            if not isinstance(imports, dict):
                return frozenset()
            return frozenset(
                plugin_id
                for plugin_id in self._plugin_ids()
                if isinstance(imports.get(plugin_id), list) and imports[plugin_id]
            )

    def runtime_implementation_lifecycle_state(
        self, implementation_id: str
    ) -> tuple[bool, bool]:
        """Return the lifecycle-authoritative import and enablement state."""
        artifact_id = (
            development_runtime_artifact_id(implementation_id)
            if implementation_id
            in self.ai_session_manager.development_runtime_implementation_ids()
            else f"runtime:{implementation_id}"
        )
        with self._lock:
            state = self._read()
            imports = state.setdefault("imports", {}).setdefault("runtime", [])
            enabled = self._enabled_ids(state, "runtime")
            return artifact_id in imports, artifact_id in enabled

    async def import_artifact(self, request: Request, plugin_id: str, artifact_id: str) -> dict[str, object]:
        self._require(plugin_id)
        with self._lock:
            artifact = self._find(request, plugin_id, artifact_id)
        if artifact.get("available") is False:
            raise ApiError(
                409,
                "plugin_artifact_unavailable",
                str(artifact.get("reason") or "插件制品当前不可用，无法导入。"),
            )
        final_id = artifact_id
        metadata = self._artifact_metadata(artifact)
        extension_updated = False
        installation: tuple[str, bool] | None = None
        if artifact_id.startswith(("zip:", "bundled:")):
            archive = self._read_zip(plugin_id, artifact_id)
            if plugin_id == "runtime":
                from app.api.runtime_plugins import install_runtime_plugin_archive

                result = await install_runtime_plugin_archive(request, artifact_id.split(":", 1)[1], archive)
                final_id = f"runtime:{result.module_id}"
                extension_updated = True
            else:
                metadata = self._inspect_business_archive(plugin_id, archive)
                installation = self._install_business_archive(plugin_id, artifact_id, archive)
                extension_updated = True
        with self._lock:
            state = self._read_after_extension_action(extension_updated)
            imports = state.setdefault("imports", {}).setdefault(plugin_id, [])
            if final_id not in imports:
                imports.append(final_id)
            self._metadata(state, plugin_id)[final_id] = metadata
            if installation is not None:
                relative, _ = installation
                state.setdefault("installations", {}).setdefault(plugin_id, {})[final_id] = relative
            self._write_after_extension_action(state, extension_updated)
            return self._status(request, plugin_id, state)

    async def remove(self, request: Request, plugin_id: str, artifact_id: str) -> dict[str, object]:
        self._require(plugin_id)
        with self._lock:
            state = self._read()
            if artifact_id not in state.setdefault("imports", {}).setdefault(plugin_id, []):
                raise ApiError(404, "plugin_import_not_found", "插件尚未导入。")
            is_enabled = artifact_id in self._enabled_ids(state, plugin_id)
        if is_enabled:
            await self.set_enabled(request, plugin_id, artifact_id, False)
        extension_updated = is_enabled and plugin_id == "runtime"
        if artifact_id.startswith("runtime:"):
            from app.api.runtime_plugins import remove_runtime_plugin

            await remove_runtime_plugin(artifact_id[8:], request)
            extension_updated = True
        with self._lock:
            state = self._read_after_extension_action(extension_updated)
            state.setdefault("imports", {}).setdefault(plugin_id, []).remove(artifact_id)
            self._metadata(state, plugin_id).pop(artifact_id, None)
            installation = self._installation_path(state, plugin_id, artifact_id)
            installed = state.setdefault("installations", {}).get(plugin_id)
            if isinstance(installed, dict):
                installed.pop(artifact_id, None)
                if not installed:
                    state.setdefault("installations", {}).pop(plugin_id, None)
            current = [item for item in self._enabled_ids(state, plugin_id) if item != artifact_id]
            if current:
                state.setdefault("enabled", {})[plugin_id] = current
            else:
                state.setdefault("enabled", {}).pop(plugin_id, None)
            self._write_after_extension_action(state, extension_updated)
            if installation is not None:
                self._remove_installation(installation)
            return self._status(request, plugin_id, state)

    async def set_enabled(self, request: Request, plugin_id: str, artifact_id: str, enabled: bool) -> dict[str, object]:
        self._require(plugin_id)
        with self._lock:
            state = self._read()
            if artifact_id not in state.setdefault("imports", {}).setdefault(plugin_id, []):
                raise ApiError(409, "plugin_not_imported", "请先导入插件后再启用。")
            if enabled:
                artifact = next(
                    (
                        item
                        for item in self._status(request, plugin_id, state)["artifacts"]
                        if item["artifact_id"] == artifact_id
                    ),
                    None,
                )
                if artifact is None or artifact.get("available") is False:
                    raise ApiError(
                        409,
                        "plugin_artifact_unavailable",
                        str((artifact or {}).get("reason") or "插件制品当前不可用，无法启用。"),
                    )
        extension_updated = False
        runtime_preferences = None
        if plugin_id == "runtime":
            implementation_id = self._runtime_implementation_id(artifact_id)
            runtime_preferences = (
                self.ai_session_manager.runtime_implementation_preferences.read()
            )
            self.ai_session_manager.update_runtime_implementation_enabled(implementation_id, enabled)
            extension_updated = True
        lifecycle_state_written = False
        try:
            with self._lock:
                state = self._read_after_extension_action(extension_updated)
                current = self._enabled_ids(state, plugin_id)
                if enabled and plugin_id != "runtime":
                    current = [artifact_id]
                elif enabled and artifact_id not in current:
                    current.append(artifact_id)
                if not enabled and artifact_id in current:
                    current.remove(artifact_id)
                if current:
                    state.setdefault("enabled", {})[plugin_id] = current
                else:
                    state.setdefault("enabled", {}).pop(plugin_id, None)
                self._write_after_extension_action(state, extension_updated)
                lifecycle_state_written = True
                return self._status(request, plugin_id, state)
        except ApiError as exc:
            if (
                runtime_preferences is None
                or not extension_updated
                or lifecycle_state_written
            ):
                raise
            try:
                self.ai_session_manager.restore_runtime_implementation_preferences(
                    runtime_preferences
                )
            except Exception as rollback_error:
                raise ApiError(
                    503,
                    "runtime_lifecycle_state_unconfirmed",
                    "Runtime 启停状态无法确认，请恢复本机状态存储后重试。",
                ) from rollback_error
            raise ApiError(
                503,
                "runtime_lifecycle_state_rolled_back",
                "Runtime 启停未完成，原有 Runtime 状态已恢复。",
            ) from exc

    def _status(self, request: Request, plugin_id: str, state: dict[str, object]) -> dict[str, object]:
        imports = list(state.setdefault("imports", {}).setdefault(plugin_id, []))
        enabled = self._enabled_ids(state, plugin_id)
        metadata = self._metadata(state, plugin_id)
        artifacts = [dict(item) for item in self._artifacts(request, plugin_id)]
        known = {item["artifact_id"] for item in artifacts}
        for artifact_id in imports:
            if artifact_id not in known:
                artifacts.append(self._missing_artifact(artifact_id, metadata.get(artifact_id)))
        for artifact in artifacts:
            artifact_id = artifact["artifact_id"]
            # Runtime modules report their installed metadata directly. Cached metadata
            # is authoritative for business-module state-only ZIP imports and for
            # artifacts that are no longer available on disk.
            if artifact_id in metadata and (plugin_id != "runtime" or artifact_id not in known):
                artifact.update(metadata[artifact_id])
            artifact["imported"] = artifact_id in imports
            artifact["enabled"] = artifact_id in enabled
        imported_implementation_refs = self._imported_implementation_refs(plugin_id, imports)
        artifacts = [
            artifact
            for artifact in artifacts
            if (
                artifact["imported"]
                or artifact.get("available") is not False
                or artifact.get("source") == "bundled"
            )
            and not (
                artifact.get("source") == "bundled"
                and artifact.get("implementation_ref") in imported_implementation_refs
            )
        ]
        for artifact in artifacts:
            artifact.pop("implementation_ref", None)
        result: dict[str, object] = {
            "plugin_id": plugin_id,
            "name": self._plugin_name(plugin_id),
            "imported_artifact_ids": imports,
            "enabled_artifact_ids": enabled,
            "artifacts": artifacts,
        }
        return result

    def _artifacts(self, request: Request, plugin_id: str) -> list[dict[str, object]]:
        if plugin_id == "runtime":
            from app.api.runtime_plugins import _module_list

            rows = []
            for item in _module_list(request).modules:
                identifier = (
                    development_runtime_artifact_id(item.module_id)
                    if item.source == "development"
                    else f"runtime:{item.module_id}"
                )
                rows.append({"artifact_id": identifier, "source": item.source, "name": item.name, "version": item.version, "description": item.description or "提供 AI Runtime 执行能力。", "available": item.status == "active", "removable": item.removable, "reason": item.reason})
            return rows + self._candidates(plugin_id)
        return [{"artifact_id": f"development:{plugin_id}", "source": "development", "name": "开发实现", "version": "dev", "description": f"{self._plugin_name(plugin_id)}业务插件开发实现。", "available": self._business_source_available(plugin_id), "removable": True, "reason": None if self._business_source_available(plugin_id) else "业务模块清单缺失或与当前 Chub 版本不兼容。"}] + self._candidates(plugin_id)

    def _candidates(self, plugin_id: str) -> list[dict[str, object]]:
        rows = []
        local_directory = PROJECT_ROOT / "data/local/artifacts/plugins" / plugin_id
        if local_directory.is_dir() and not local_directory.is_symlink():
            for path in sorted(local_directory.glob("*.zip")):
                if not path.is_file() or path.is_symlink():
                    continue
                rows.append({
                    "artifact_id": f"zip:{path.name}",
                    "source": "zip",
                    "name": path.stem,
                    "version": "",
                    "description": "待导入的 ZIP 制品；导入时由插件校验其能力。",
                    "available": True,
                    "removable": True,
                    "reason": None,
                })
        bundled_directory = PROJECT_ROOT / "bundled-modules"
        if bundled_directory.is_dir() and not bundled_directory.is_symlink():
            pattern = "*-runtime-*.zip" if plugin_id == "runtime" else f"{plugin_id}-*.zip"
            for path in sorted(bundled_directory.glob(pattern)) if pattern else ():
                if not path.is_file() or path.is_symlink():
                    continue
                row: dict[str, object] = {
                    "artifact_id": f"bundled:{path.name}",
                    "source": "bundled",
                    "name": f"{path.stem}（随包）",
                    "version": "",
                    "description": "随正式包提供的 ZIP 制品；可直接导入。",
                    "available": True,
                    "removable": True,
                    "reason": None,
                }
                rows.append(row)
        return rows

    def _imported_implementation_refs(
        self, plugin_id: str, imports: list[str]
    ) -> set[str]:
        return set()

    def _find(self, request: Request, plugin_id: str, artifact_id: str) -> dict[str, object]:
        item = next((item for item in self._artifacts(request, plugin_id) if item["artifact_id"] == artifact_id), None)
        if item is None:
            raise ApiError(404, "plugin_artifact_not_found", "本机插件制品不存在或已不可用。")
        return item

    def _read_zip(self, plugin_id: str, artifact_id: str) -> bytes:
        source, separator, filename = artifact_id.partition(":")
        if not separator or Path(filename).name != filename:
            raise ApiError(422, "plugin_artifact_invalid", "插件 ZIP 格式无效。")
        if source == "zip":
            path = PROJECT_ROOT / "data/local/artifacts/plugins" / plugin_id / filename
        elif source == "bundled" and ((plugin_id == "runtime" and "-runtime-" in filename) or (plugin_id != "runtime" and filename.startswith(f"{plugin_id}-"))):
            path = PROJECT_ROOT / "bundled-modules" / filename
        else:
            raise ApiError(422, "plugin_artifact_invalid", "插件 ZIP 格式无效。")
        maximum_bytes = 32 * 1024 * 1024
        if plugin_id != "runtime":
            maximum_bytes = self.settings.business_modules.max_archive_bytes
        try:
            if not path.is_file() or path.is_symlink() or path.stat().st_size > maximum_bytes:
                raise OSError
            with zipfile.ZipFile(path) as package:
                if not package.infolist() or any(".." in Path(info.filename).parts or Path(info.filename).is_absolute() for info in package.infolist()):
                    raise ValueError
            return path.read_bytes()
        except (OSError, ValueError, zipfile.BadZipFile):
            raise ApiError(422, "plugin_artifact_invalid", "插件 ZIP 格式无效。") from None

    def _inspect_business_archive(self, plugin_id: str, archive: bytes) -> dict[str, object]:
        try:
            with zipfile.ZipFile(io.BytesIO(archive)) as package:
                info = package.getinfo(_BUSINESS_MANIFEST)
                if info.file_size > 64 * 1024:
                    raise ValueError
                manifest = json.loads(package.read(info).decode("utf-8"))
        except (KeyError, UnicodeDecodeError, ValueError, zipfile.BadZipFile):
            raise ApiError(422, f"{plugin_id}_plugin_manifest_invalid", f"{self._plugin_name(plugin_id)}插件清单无效。") from None
        entry = manifest.get("entry") if isinstance(manifest, dict) else None
        entry_module, entry_separator, entry_attribute = (
            entry.partition(":") if isinstance(entry, str) else ("", "", "")
        )
        entry_valid = (
            bool(entry_separator)
            and bool(entry_module)
            and bool(entry_attribute)
            and all(part.isidentifier() for part in entry_module.split("."))
            and entry_attribute.isidentifier()
        )
        if (
            not isinstance(manifest, dict)
            or manifest.get("module_id") != plugin_id
            or manifest.get("module_type") != "business"
            or manifest.get("protocol_version") != 1
            or not entry_valid
        ):
            raise ApiError(422, f"{plugin_id}_plugin_manifest_invalid", f"{self._plugin_name(plugin_id)}插件清单不兼容。")
        if manifest.get("chub_version") != self.settings.app.version:
            raise ApiError(422, f"{plugin_id}_plugin_version_incompatible", f"{self._plugin_name(plugin_id)}插件与当前 Chub 版本不兼容。")
        version = manifest.get("version")
        if not isinstance(version, str) or not version.strip():
            raise ApiError(422, f"{plugin_id}_plugin_manifest_invalid", f"{self._plugin_name(plugin_id)}插件版本无效。")
        name = manifest.get("display_name")
        description = manifest.get("description")
        return {
            "source": "zip",
            "name": name.strip() if isinstance(name, str) and name.strip() else self._plugin_name(plugin_id),
            "version": version.strip(),
            "description": description.strip() if isinstance(description, str) and description.strip() else f"{self._plugin_name(plugin_id)}业务插件。",
        }

    @staticmethod
    def _artifact_metadata(artifact: dict[str, object]) -> dict[str, object]:
        return {key: artifact[key] for key in ("source", "name", "version", "description") if key in artifact}

    @staticmethod
    def _missing_artifact(artifact_id: str, metadata: object) -> dict[str, object]:
        saved = metadata if isinstance(metadata, dict) else {}
        return {
            "artifact_id": artifact_id,
            "source": saved.get("source") if isinstance(saved.get("source"), str) else "zip",
            "name": saved.get("name") if isinstance(saved.get("name"), str) else artifact_id,
            "version": saved.get("version") if isinstance(saved.get("version"), str) else "",
            "description": saved.get("description") if isinstance(saved.get("description"), str) else "已导入的插件制品当前不可用。",
            "available": False,
            "removable": True,
            "reason": "已导入的插件制品当前不可用；可恢复制品后继续使用，或显式移除。",
        }

    def _installation_path(
        self,
        state: dict[str, object],
        plugin_id: str,
        artifact_id: str,
    ) -> Path | None:
        installations = state.get("installations")
        module_installations = installations.get(plugin_id) if isinstance(installations, dict) else None
        relative = module_installations.get(artifact_id) if isinstance(module_installations, dict) else None
        if not isinstance(relative, str) or not relative:
            return None
        candidate = self.settings.business_modules.install_dir / relative
        try:
            candidate.resolve(strict=True).relative_to(
                self.settings.business_modules.install_dir.resolve(strict=True)
            )
        except (OSError, ValueError):
            return None
        return candidate if candidate.is_dir() and not candidate.is_symlink() else None

    def _install_business_archive(
        self,
        plugin_id: str,
        artifact_id: str,
        archive: bytes,
    ) -> tuple[str, bool]:
        digest = hashlib.sha256(archive).hexdigest()
        root = self.settings.business_modules.install_dir / plugin_id
        target = root / digest
        try:
            root.mkdir(mode=0o700, parents=True, exist_ok=True)
            if target.is_dir() and not target.is_symlink():
                return f"{plugin_id}/{digest}", False
            with zipfile.ZipFile(io.BytesIO(archive)) as package:
                infos = package.infolist()
                total_size = 0
                for info in infos:
                    path = Path(info.filename)
                    if (
                        path.is_absolute()
                        or ".." in path.parts
                        or not info.filename
                        or stat.S_IFMT(info.external_attr >> 16) == stat.S_IFLNK
                    ):
                        raise ValueError
                    total_size += info.file_size
                    if total_size > 64 * 1024 * 1024:
                        raise ValueError
                temporary = Path(tempfile.mkdtemp(prefix=f".{digest}-", dir=root))
                try:
                    for info in infos:
                        destination = temporary / Path(info.filename)
                        if info.is_dir():
                            destination.mkdir(mode=0o700, parents=True, exist_ok=True)
                            continue
                        destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
                        with package.open(info, "r") as source, destination.open("wb") as target_file:
                            shutil.copyfileobj(source, target_file, length=64 * 1024)
                        os.chmod(destination, 0o600)
                    os.chmod(temporary, 0o700)
                    os.replace(temporary, target)
                finally:
                    if temporary.exists():
                        shutil.rmtree(temporary, ignore_errors=True)
        except (OSError, ValueError, zipfile.BadZipFile):
            raise ApiError(422, f"{plugin_id}_plugin_install_invalid", f"{self._plugin_name(plugin_id)}插件无法安装。") from None
        return f"{plugin_id}/{digest}", True

    @staticmethod
    def _remove_installation(path: Path) -> None:
        try:
            shutil.rmtree(path)
        except OSError:
            LOGGER.warning("Unable to remove business module installation %s", path, exc_info=True)

    @staticmethod
    def _require(plugin_id: str) -> None:
        if plugin_id not in ("runtime",) + tuple(source.module_id for source in registered_module_sources("business")):
            raise ApiError(404, "plugin_not_found", "插件不存在。")

    @staticmethod
    def _plugin_ids() -> tuple[str, ...]:
        return ("runtime",) + tuple(
            source.module_id for source in registered_module_sources("business")
        )

    @staticmethod
    def _plugin_name(plugin_id: str) -> str:
        if plugin_id == "runtime":
            return "AI Runtime"
        source = registered_module_source("business", plugin_id)
        if source is not None:
            try:
                manifest = json.loads(
                    (source.root / _BUSINESS_MANIFEST).read_text(encoding="utf-8")
                )
                name = manifest.get("display_name")
                if isinstance(name, str) and name.strip():
                    return name.strip()
            except (OSError, UnicodeDecodeError, json.JSONDecodeError):
                pass
        return plugin_id

    def _business_source_available(self, plugin_id: str) -> bool:
        source = registered_module_source("business", plugin_id)
        if source is None:
            return False
        try:
            manifest = json.loads(
                (source.root / _BUSINESS_MANIFEST).read_text(encoding="utf-8")
            )
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            return False
        return (
            isinstance(manifest, dict)
            and manifest.get("module_id") == plugin_id
            and manifest.get("module_type") == "business"
            and manifest.get("protocol_version") == 1
            and manifest.get("chub_version") in {"dev", self.settings.app.version}
        )

    def _runtime_implementation_id(self, artifact_id: str) -> str:
        if artifact_id.startswith("development:"):
            implementation_id = artifact_id.removeprefix("development:")
            if implementation_id in self.ai_session_manager.development_runtime_implementation_ids():
                return implementation_id
        elif artifact_id.startswith("runtime:"):
            return artifact_id.removeprefix("runtime:")
        raise ApiError(422, "plugin_artifact_invalid", "Runtime 插件制品标识无效。")

    @staticmethod
    def _enabled_ids(state: dict[str, object], plugin_id: str) -> list[str]:
        value = state.setdefault("enabled", {}).get(plugin_id, [])
        if isinstance(value, str):
            return [value]
        return [item for item in value if isinstance(item, str)] if isinstance(value, list) else []

    @staticmethod
    def _metadata(state: dict[str, object], plugin_id: str) -> dict[str, dict[str, object]]:
        root = state.setdefault("metadata", {})
        if not isinstance(root, dict):
            state["metadata"] = {}
            root = state["metadata"]
        value = root.setdefault(plugin_id, {})
        if not isinstance(value, dict):
            root[plugin_id] = {}
            value = root[plugin_id]
        return value

    def _read(self) -> dict[str, object]:
        try:
            state = json.loads(self.path.read_text(encoding="utf-8"))
            if isinstance(state, dict) and isinstance(state.get("imports", {}), dict) and isinstance(state.get("enabled", {}), dict):
                if not isinstance(state.get("installations", {}), dict):
                    state["installations"] = {}
                obsolete = False
                for key in ("imports", "enabled", "metadata"):
                    value = state.get(key)
                    if isinstance(value, dict):
                        for obsolete_key in ("codex-runtime", "weixin-orchestration"):
                            if obsolete_key in value:
                                value.pop(obsolete_key, None)
                                obsolete = True
                if obsolete:
                    self._write(state)
                return state
        except FileNotFoundError:
            pass
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            raise ApiError(503, "plugin_lifecycle_state_unavailable", "插件生命周期状态不可用。") from None
        return {"imports": {}, "enabled": {}, "metadata": {}, "installations": {}}

    def _write(self, state: dict[str, object]) -> None:
        temporary = self.path.with_suffix(".tmp")
        try:
            self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            temporary.write_text(json.dumps(state, ensure_ascii=True, separators=(",", ":")), encoding="utf-8")
            os.chmod(temporary, 0o600)
            os.replace(temporary, self.path)
        except OSError:
            raise ApiError(503, "plugin_lifecycle_state_unavailable", "插件生命周期状态不可写。") from None
        finally:
            if temporary.exists():
                temporary.unlink(missing_ok=True)

    def _write_after_extension_action(self, state: dict[str, object], extension_updated: bool) -> None:
        try:
            self._write(state)
        except ApiError as exc:
            if extension_updated and exc.code == "plugin_lifecycle_state_unavailable":
                raise ApiError(
                    503,
                    "plugin_lifecycle_state_unconfirmed",
                    "插件自身操作可能已经完成，但统一生命周期状态未能保存。恢复本机状态存储后，请重试同一操作确认结果。",
                ) from None
            raise

    def _read_after_extension_action(self, extension_updated: bool) -> dict[str, object]:
        try:
            return self._read()
        except ApiError as exc:
            if extension_updated and exc.code == "plugin_lifecycle_state_unavailable":
                raise ApiError(
                    503,
                    "plugin_lifecycle_state_unconfirmed",
                    "插件自身操作可能已经完成，但统一生命周期状态未能读取。恢复本机状态存储后，请重试同一操作确认结果。",
                ) from None
            raise
