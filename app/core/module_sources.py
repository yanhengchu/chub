from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from app.core.config import PROJECT_ROOT


MODULE_INDEX_NAME = "chub-modules.json"
MAX_MODULE_INDEX_BYTES = 64 * 1024
_MODULE_TYPES = frozenset({"runtime", "orchestration", "business", "script", "patch"})


@dataclass(frozen=True)
class RegisteredModuleSource:
    module_id: str
    module_type: str
    root: Path
    source: str


def builtin_modules_root() -> Path:
    return PROJECT_ROOT / "modules"


def local_modules_root() -> Path:
    return PROJECT_ROOT.parent / "chub-local-modules"


def registered_module_sources(module_type: str) -> tuple[RegisteredModuleSource, ...]:
    """Return fixed, indexed module roots with bundled sources taking precedence."""
    if module_type not in _MODULE_TYPES:
        raise ValueError("unsupported module type")
    sources: list[RegisteredModuleSource] = []
    seen_module_ids: set[str] = set()
    for source_name, root in (
        ("bundled", builtin_modules_root()),
        ("local", local_modules_root()),
    ):
        for entry in _read_index(root, source_name, module_type):
            if entry.module_id in seen_module_ids:
                continue
            seen_module_ids.add(entry.module_id)
            sources.append(entry)
    return tuple(sources)


def registered_module_source(
    module_type: str,
    module_id: str,
) -> RegisteredModuleSource | None:
    return next(
        (
            source
            for source in registered_module_sources(module_type)
            if source.module_id == module_id
        ),
        None,
    )


def _read_index(
    root: Path,
    source_name: str,
    module_type: str,
) -> tuple[RegisteredModuleSource, ...]:
    try:
        if root.is_symlink() or not root.is_dir():
            return ()
        index = root / MODULE_INDEX_NAME
        if index.is_symlink() or not index.is_file():
            return ()
        raw = index.read_bytes()
    except OSError:
        return ()
    if not raw or len(raw) > MAX_MODULE_INDEX_BYTES:
        return ()
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError):
        return ()
    if not isinstance(payload, dict) or payload.get("schema_version") != 1:
        return ()
    entries = payload.get("modules")
    if not isinstance(entries, list):
        return ()
    result: list[RegisteredModuleSource] = []
    seen_module_ids: set[str] = set()
    for item in entries:
        if not isinstance(item, dict):
            continue
        module_id = item.get("module_id")
        registered_type = item.get("module_type")
        relative_path = item.get("path")
        if (
            not isinstance(module_id, str)
            or not module_id
            or module_id in seen_module_ids
            or registered_type != module_type
            or not isinstance(relative_path, str)
        ):
            continue
        path = PurePosixPath(relative_path)
        if path.is_absolute() or ".." in path.parts or not path.parts:
            continue
        candidate = root.joinpath(*path.parts)
        try:
            if candidate.is_symlink() or not candidate.is_dir():
                continue
            candidate.resolve(strict=True).relative_to(root.resolve(strict=True))
        except ValueError:
            continue
        except OSError:
            continue
        if any((root.joinpath(*path.parts[:index])).is_symlink() for index in range(1, len(path.parts))):
            continue
        seen_module_ids.add(module_id)
        result.append(
            RegisteredModuleSource(
                module_id=module_id,
                module_type=module_type,
                root=candidate,
                source=source_name,
            )
        )
    return tuple(result)
