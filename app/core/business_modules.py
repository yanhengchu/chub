from __future__ import annotations

import importlib
import importlib.util
import json
import logging
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from fastapi import APIRouter, FastAPI, Request

from app.core.config import Settings
from app.core.module_sources import registered_module_sources

BUSINESS_MANIFEST_NAME = "chub-business-module.json"
LOGGER = logging.getLogger("hub.business_modules")


@dataclass(frozen=True)
class BusinessModuleDefinition:
    """The small host contract exposed by an independent business module."""

    module_id: str
    name: str
    description: str
    version: str
    root: Path
    template_dir: Path
    static_dir: Path
    api_router: APIRouter | None = None
    workspace_template: str | None = None
    settings_template: str | None = None
    initialize: Callable[[FastAPI, Settings], None] | None = None
    workspace_state: Callable[[Request], dict[str, Any] | None] | None = None
    workspace_records: Callable[[Request], tuple[list[Any], list[Any]]] | None = None
    recovery_state_paths: Callable[[Settings], tuple[Path, ...]] | None = None


def _load_factory(entry: str) -> Callable[..., BusinessModuleDefinition]:
    module_name, separator, attribute = entry.partition(":")
    if (
        not separator
        or not module_name
        or not attribute
        or not all(part.isidentifier() for part in module_name.split("."))
        or not attribute.isidentifier()
    ):
        raise ValueError("business module entry is invalid")
    factory = getattr(importlib.import_module(module_name), attribute, None)
    if not callable(factory):
        raise ValueError("business module entry is not callable")
    return factory


def _load_installed_factory(
    entry: str,
    module_root: Path,
    module_id: str,
) -> Callable[..., BusinessModuleDefinition]:
    module_name, separator, attribute = entry.partition(":")
    if (
        not separator
        or not module_name
        or not attribute
        or not all(part.isidentifier() for part in module_name.split("."))
        or not attribute.isidentifier()
    ):
        raise ValueError("business module entry is invalid")
    relative = Path(*module_name.split("."))
    package_path = module_root / relative / "__init__.py"
    if package_path.is_file():
        location = package_path
        search_locations = [str(package_path.parent)]
    else:
        location = module_root / f"{relative}.py"
        search_locations = None
    if not location.is_file() or location.is_symlink():
        raise ValueError("business module entry file is missing")
    namespace = f"_chub_business_{module_id}_{abs(hash(str(location.resolve()))):x}"
    spec = importlib.util.spec_from_file_location(
        namespace,
        location,
        submodule_search_locations=search_locations,
    )
    if spec is None or spec.loader is None:
        raise ValueError("business module entry cannot be loaded")
    module = importlib.util.module_from_spec(spec)
    sys.modules[namespace] = module
    try:
        spec.loader.exec_module(module)
    except Exception:
        sys.modules.pop(namespace, None)
        raise
    factory = getattr(module, attribute, None)
    if not callable(factory):
        raise ValueError("business module entry is not callable")
    return factory


def _read_lifecycle_state(settings: Settings) -> dict[str, object]:
    try:
        payload = json.loads(settings.business_modules.state_file.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, UnicodeDecodeError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _installed_module_roots(settings: Settings) -> dict[str, Path]:
    state = _read_lifecycle_state(settings)
    enabled = state.get("enabled")
    installations = state.get("installations")
    if not isinstance(enabled, dict) or not isinstance(installations, dict):
        return {}
    result: dict[str, Path] = {}
    for module_id, artifacts in enabled.items():
        if not isinstance(module_id, str) or not isinstance(artifacts, list):
            continue
        installed = installations.get(module_id)
        if not isinstance(installed, dict):
            continue
        for artifact_id in artifacts:
            if not isinstance(artifact_id, str) or not artifact_id.startswith(("zip:", "bundled:")):
                continue
            relative = installed.get(artifact_id)
            if not isinstance(relative, str) or not relative:
                continue
            candidate = settings.business_modules.install_dir / relative
            try:
                candidate.resolve(strict=True).relative_to(
                    settings.business_modules.install_dir.resolve(strict=True)
                )
            except (OSError, ValueError):
                continue
            if candidate.is_dir() and not candidate.is_symlink():
                result[module_id] = candidate
                break
    return result


def _load_manifest(root: Path) -> dict[str, object] | None:
    try:
        manifest = json.loads((root / BUSINESS_MANIFEST_NAME).read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    return manifest if isinstance(manifest, dict) else None


def load_business_modules(settings: Settings) -> tuple[BusinessModuleDefinition, ...]:
    modules: list[BusinessModuleDefinition] = []
    installed_roots = _installed_module_roots(settings)
    for source in registered_module_sources("business"):
        root = installed_roots.get(source.module_id, source.root)
        manifest = _load_manifest(root)
        if (
            manifest is None
            or manifest.get("module_id") != source.module_id
            or manifest.get("module_type") != "business"
            or manifest.get("protocol_version") != 1
            or not isinstance(manifest.get("entry"), str)
            or (
                root == source.root
                and manifest.get("chub_version") not in {"dev", settings.app.version}
            )
            or (
                root != source.root
                and manifest.get("chub_version") != settings.app.version
            )
        ):
            continue
        try:
            factory = (
                _load_factory(manifest["entry"])
                if root == source.root
                else _load_installed_factory(manifest["entry"], root, source.module_id)
            )
            if root == source.root:
                definition = factory(settings)
            else:
                module_name = manifest["entry"].partition(":")[0]
                package_root = root / Path(*module_name.split("."))
                definition = factory(
                    settings,
                    package_root if package_root.is_dir() else root,
                )
        except Exception:
            LOGGER.warning("Unable to load business module %s", source.module_id, exc_info=True)
            continue
        if (
            not isinstance(definition, BusinessModuleDefinition)
            or definition.module_id != source.module_id
            or not definition.template_dir.is_dir()
            or not definition.static_dir.is_dir()
        ):
            continue
        modules.append(definition)
    return tuple(modules)


def business_module_template_dirs(settings: Settings | None = None) -> tuple[Path, ...]:
    active_settings = settings
    if active_settings is None:
        try:
            from app.core.config import load_settings

            active_settings = load_settings()
        except Exception:
            active_settings = None
    installed_roots = (
        _installed_module_roots(active_settings) if active_settings is not None else {}
    )
    result: list[Path] = []
    for source in registered_module_sources("business"):
        root = installed_roots.get(source.module_id, source.root)
        template_dir = root / "templates"
        if template_dir.is_dir():
            result.append(template_dir)
    return tuple(result)


def loaded_business_modules(request: Request) -> tuple[BusinessModuleDefinition, ...]:
    return tuple(getattr(request.app.state, "business_modules", ()))


def loaded_business_module(
    request: Request, module_id: str
) -> BusinessModuleDefinition | None:
    return next(
        (module for module in loaded_business_modules(request) if module.module_id == module_id),
        None,
    )
