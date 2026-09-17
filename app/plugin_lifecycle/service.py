from __future__ import annotations

import io
import json
import os
import zipfile
from pathlib import Path
from threading import RLock
from typing import Any

from fastapi import Request

from app.ai_runtime.development_plugins import development_runtime_artifact_id
from app.core.config import PROJECT_ROOT, Settings
from app.core.response import ApiError

_PLUGIN_IDS = ("runtime", "weixin-orchestration", "deliveryline")
_PLUGIN_NAMES = {
    "runtime": "AI Runtime",
    "weixin-orchestration": "微信任务润色",
    "deliveryline": "Deliveryline",
}
_DELIVERYLINE_MANIFEST = "chub-business-module.json"
_BUNDLED_MODULE_PREFIXES = {
    "weixin-orchestration": "weixin-refinement-release-",
}


class PluginLifecycleService:
    """Coordinates common operations; plugins retain final-state confirmation."""

    def __init__(self, settings: Settings, ai_session_manager: Any, weixin_chub_mode: Any) -> None:
        self.settings = settings
        self.ai_session_manager = ai_session_manager
        self.weixin_chub_mode = weixin_chub_mode
        self.path = settings.business_modules.state_file.with_name("plugin-lifecycle.json")
        self._lock = RLock()

    def list(self, request: Request) -> dict[str, object]:
        with self._lock:
            state = self._read()
            return {"plugins": [self._status(request, key, state) for key in _PLUGIN_IDS]}

    def imported_plugin_ids(self) -> frozenset[str]:
        with self._lock:
            imports = self._read().get("imports", {})
            if not isinstance(imports, dict):
                return frozenset()
            return frozenset(
                plugin_id
                for plugin_id in _PLUGIN_IDS
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
        if artifact_id.startswith(("zip:", "bundled:")):
            archive = self._read_zip(plugin_id, artifact_id)
            if plugin_id == "runtime":
                from app.api.runtime_plugins import install_runtime_plugin_archive

                result = await install_runtime_plugin_archive(request, artifact_id.split(":", 1)[1], archive)
                final_id = f"runtime:{result.module_id}"
                extension_updated = True
            elif plugin_id == "weixin-orchestration":
                preview = self.weixin_chub_mode.orchestration_plugin_service.install(
                    archive,
                    source_name=artifact_id.split(":", 1)[1],
                )
                final_id = f"orchestration:{preview.implementation_ref}"
                extension_updated = True
            elif plugin_id == "deliveryline":
                metadata = self._inspect_deliveryline_archive(archive)
        elif plugin_id == "weixin-orchestration":
            self.weixin_chub_mode.set_orchestration_implementation("weixin-orchestration-dev")
            extension_updated = True
        with self._lock:
            state = self._read_after_extension_action(extension_updated)
            imports = state.setdefault("imports", {}).setdefault(plugin_id, [])
            if final_id not in imports:
                imports.append(final_id)
            self._metadata(state, plugin_id)[final_id] = metadata
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
        extension_updated = is_enabled and plugin_id in {"runtime", "weixin-orchestration"}
        if artifact_id.startswith("runtime:"):
            from app.api.runtime_plugins import remove_runtime_plugin

            await remove_runtime_plugin(artifact_id[8:], request)
            extension_updated = True
        elif artifact_id.startswith("orchestration:"):
            self.weixin_chub_mode.remove_orchestration_plugin(artifact_id[14:])
            extension_updated = True
        elif plugin_id == "weixin-orchestration":
            self.weixin_chub_mode.set_orchestration_implementation("disabled")
            extension_updated = True
        with self._lock:
            state = self._read_after_extension_action(extension_updated)
            state.setdefault("imports", {}).setdefault(plugin_id, []).remove(artifact_id)
            self._metadata(state, plugin_id).pop(artifact_id, None)
            current = [item for item in self._enabled_ids(state, plugin_id) if item != artifact_id]
            if current:
                state.setdefault("enabled", {})[plugin_id] = current
            else:
                state.setdefault("enabled", {}).pop(plugin_id, None)
            self._write_after_extension_action(state, extension_updated)
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
        elif plugin_id == "weixin-orchestration":
            if artifact_id == "development:weixin-orchestration":
                self.weixin_chub_mode.set_orchestration_implementation("weixin-orchestration-dev")
            else:
                self.weixin_chub_mode.set_orchestration_implementation("module", artifact_id.removeprefix("orchestration:"))
            self.weixin_chub_mode.set_orchestration_enabled(enabled)
            extension_updated = True
        lifecycle_state_written = False
        try:
            with self._lock:
                state = self._read_after_extension_action(extension_updated)
                current = self._enabled_ids(state, plugin_id)
                if enabled and plugin_id in {"weixin-orchestration", "deliveryline"}:
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
            # Runtime/orchestration plugins report their installed metadata directly.
            # Cached metadata is authoritative only for Deliveryline's state-only ZIP
            # import and for artifacts that are no longer available on disk.
            if artifact_id in metadata and (plugin_id == "deliveryline" or artifact_id not in known):
                artifact.update(metadata[artifact_id])
            artifact["imported"] = artifact_id in imports
            artifact["enabled"] = artifact_id in enabled
        artifacts = [
            artifact
            for artifact in artifacts
            if artifact["imported"] or artifact.get("available") is not False
        ]
        return {
            "plugin_id": plugin_id,
            "name": _PLUGIN_NAMES[plugin_id],
            "imported_artifact_ids": imports,
            "enabled_artifact_ids": enabled,
            "artifacts": artifacts,
        }

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
        if plugin_id == "weixin-orchestration":
            status = self.weixin_chub_mode.orchestration_settings()
            development_version = self._development_weixin_version()
            rows = [{"artifact_id": "development:weixin-orchestration", "source": "development", "name": "开发实现", "version": development_version, "description": "处理微信普通文本的润色、确认与任务续提。", "available": status.development_available, "removable": True, "reason": None}]
            rows.extend({"artifact_id": f"orchestration:{item.implementation_ref}", "source": "zip", "name": item.name, "version": item.version, "description": item.description, "available": item.available, "removable": removable, "reason": item.reason} for item, _active, removable in self.weixin_chub_mode.list_orchestration_plugins())
            return rows + self._candidates(plugin_id)
        return [{"artifact_id": "development:deliveryline", "source": "development", "name": "开发实现", "version": "dev", "description": "提供需求提出档案、评审前校验与归档查看；后续交付阶段尚未接入。", "available": (PROJECT_ROOT / "business-modules/deliveryline/chub-business-module.json").is_file(), "removable": True, "reason": None}] + self._candidates(plugin_id)

    @staticmethod
    def _development_weixin_version() -> str:
        try:
            manifest = json.loads(
                (PROJECT_ROOT / "orchestration-modules" / "weixin-refinement" / "chub-capability-orchestration.json").read_text("utf-8")
            )
            version = manifest.get("version") if isinstance(manifest, dict) else None
            return version if isinstance(version, str) and version.strip() else "未知"
        except (OSError, ValueError):
            return "未知"

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
            pattern = "*-runtime-*.zip" if plugin_id == "runtime" else (
                f"{_BUNDLED_MODULE_PREFIXES[plugin_id]}*.zip"
                if plugin_id in _BUNDLED_MODULE_PREFIXES
                else None
            )
            for path in sorted(bundled_directory.glob(pattern)) if pattern else ():
                if not path.is_file() or path.is_symlink():
                    continue
                rows.append({
                    "artifact_id": f"bundled:{path.name}",
                    "source": "bundled",
                    "name": f"{path.stem}（随包）",
                    "version": "",
                    "description": "随正式包提供的 ZIP 制品；可直接导入。",
                    "available": True,
                    "removable": True,
                    "reason": None,
                })
        return rows

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
        elif source == "bundled" and plugin_id == "runtime" and "-runtime-" in filename:
            path = PROJECT_ROOT / "bundled-modules" / filename
        elif (
            source == "bundled"
            and (prefix := _BUNDLED_MODULE_PREFIXES.get(plugin_id)) is not None
            and filename.startswith(prefix)
        ):
            path = PROJECT_ROOT / "bundled-modules" / filename
        else:
            raise ApiError(422, "plugin_artifact_invalid", "插件 ZIP 格式无效。")
        maximum_bytes = 32 * 1024 * 1024
        if plugin_id == "deliveryline":
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

    def _inspect_deliveryline_archive(self, archive: bytes) -> dict[str, object]:
        try:
            with zipfile.ZipFile(io.BytesIO(archive)) as package:
                info = package.getinfo(_DELIVERYLINE_MANIFEST)
                if info.file_size > 64 * 1024:
                    raise ValueError
                manifest = json.loads(package.read(info).decode("utf-8"))
        except (KeyError, UnicodeDecodeError, ValueError, zipfile.BadZipFile):
            raise ApiError(422, "deliveryline_plugin_manifest_invalid", "Deliveryline 插件清单无效。") from None
        if not isinstance(manifest, dict) or manifest.get("module_id") != "deliveryline" or manifest.get("module_type") != "business" or manifest.get("protocol_version") != 1:
            raise ApiError(422, "deliveryline_plugin_manifest_invalid", "Deliveryline 插件清单不兼容。")
        if manifest.get("chub_version") != self.settings.app.version:
            raise ApiError(422, "deliveryline_plugin_version_incompatible", "Deliveryline 插件与当前 Chub 版本不兼容。")
        version = manifest.get("version")
        if not isinstance(version, str) or not version.strip():
            raise ApiError(422, "deliveryline_plugin_manifest_invalid", "Deliveryline 插件版本无效。")
        name = manifest.get("display_name")
        description = manifest.get("description")
        return {
            "source": "zip",
            "name": name.strip() if isinstance(name, str) and name.strip() else "Deliveryline",
            "version": version.strip(),
            "description": description.strip() if isinstance(description, str) and description.strip() else "Deliveryline 业务插件。",
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

    @staticmethod
    def _require(plugin_id: str) -> None:
        if plugin_id not in _PLUGIN_IDS:
            raise ApiError(404, "plugin_not_found", "插件不存在。")

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
                obsolete = False
                for key in ("imports", "enabled", "metadata"):
                    value = state.get(key)
                    if isinstance(value, dict) and "codex-runtime" in value:
                        value.pop("codex-runtime", None)
                        obsolete = True
                if obsolete:
                    self._write(state)
                return state
        except FileNotFoundError:
            pass
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            raise ApiError(503, "plugin_lifecycle_state_unavailable", "插件生命周期状态不可用。") from None
        return {"imports": {}, "enabled": {}, "metadata": {}}

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
