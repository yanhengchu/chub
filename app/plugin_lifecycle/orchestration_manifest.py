from __future__ import annotations

import ast
import io
import json
import stat
import zipfile
import hashlib
import zlib
from pathlib import Path, PurePosixPath
from typing import Any

from app.core.response import ApiError

MANIFEST_NAME = "chub-capability-orchestration.json"
PROTOCOL_VERSION = 1
ORCHESTRATION_PROTOCOL_VERSION = 1
CAPABILITY_PROTOCOL_VERSION = 1
ALLOWED_SCOPES = frozenset({"ordinary_user_task"})


def inspect_development_root(root: Path, module_id: str, chub_version: str) -> dict[str, object]:
    try:
        if root.is_symlink() or not root.is_dir():
            raise ValueError
        manifest_path = root / MANIFEST_NAME
        if manifest_path.is_symlink() or not manifest_path.is_file() or manifest_path.stat().st_size > 64 * 1024:
            raise ValueError
        manifest = _parse_manifest(manifest_path.read_bytes(), module_id, chub_version, allow_dev=True)
        _require_root_entry(root, manifest["entry"])
        if manifest["command_entry"] is not None:
            _require_root_entry(root, manifest["command_entry"])
        metadata = _metadata(manifest, "development")
        metadata["development_ref"] = _development_ref(root, module_id)
        return metadata
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError, KeyError, TypeError):
        raise ApiError(422, "orchestration_plugin_manifest_invalid", "任务编排插件清单或入口无效、不兼容。") from None


def inspect_archive(archive: bytes, module_id: str, chub_version: str) -> dict[str, object]:
    try:
        with zipfile.ZipFile(io.BytesIO(archive)) as package:
            infos = package.infolist()
            if not infos or len(infos) > 4096:
                raise ValueError
            names = [item.filename for item in infos]
            if len(set(names)) != len(names):
                raise ValueError
            manifests = [item for item in infos if item.filename == MANIFEST_NAME]
            if len(manifests) != 1 or manifests[0].is_dir() or manifests[0].file_size > 64 * 1024:
                raise ValueError
            manifest = _parse_manifest(package.read(manifests[0]), module_id, chub_version, allow_dev=False)
            _require_archive_entry(package, infos, manifest["entry"])
            if manifest["command_entry"] is not None:
                _require_archive_entry(package, infos, manifest["command_entry"])
            return _metadata(manifest, "zip")
    except (
        OSError,
        UnicodeDecodeError,
        json.JSONDecodeError,
        ValueError,
        KeyError,
        TypeError,
        RuntimeError,
        zlib.error,
        zipfile.BadZipFile,
    ):
        raise ApiError(422, "orchestration_plugin_manifest_invalid", "任务编排插件 ZIP 清单或入口无效、不兼容。") from None


def _parse_manifest(
    raw: bytes, module_id: str, chub_version: str, *, allow_dev: bool
) -> dict[str, Any]:
    manifest = json.loads(raw.decode("utf-8"))
    if not isinstance(manifest, dict):
        raise ValueError
    scope = manifest.get("scope")
    manifest_chub_version = manifest.get("chub_version")
    if (
        manifest.get("module_id") != module_id
        or manifest.get("module_type") != "capability-orchestration"
        or type(manifest.get("protocol_version")) is not int
        or manifest.get("protocol_version") != PROTOCOL_VERSION
        or type(manifest.get("orchestration_protocol_version")) is not int
        or manifest.get("orchestration_protocol_version") != ORCHESTRATION_PROTOCOL_VERSION
        or type(manifest.get("capability_protocol_version")) is not int
        or manifest.get("capability_protocol_version") != CAPABILITY_PROTOCOL_VERSION
        or not isinstance(scope, str)
        or scope not in ALLOWED_SCOPES
        or not isinstance(manifest_chub_version, str)
        or (
            manifest_chub_version not in {"dev", chub_version}
            if allow_dev
            else manifest_chub_version != chub_version
        )
    ):
        raise ValueError
    for field in ("version", "display_name", "description"):
        value = manifest.get(field)
        if not isinstance(value, str) or not value.strip() or len(value) > 256:
            raise ValueError
    if "command_entry" not in manifest:
        raise ValueError
    entry = manifest.get("entry")
    command_entry = manifest.get("command_entry")
    _validate_entry(entry)
    if command_entry is not None:
        _validate_entry(command_entry)
    return manifest


def _validate_entry(value: object) -> tuple[str, str]:
    if not isinstance(value, str):
        raise ValueError
    module_name, separator, attribute = value.partition(":")
    if (
        not separator
        or not module_name
        or not attribute
        or not all(part.isidentifier() for part in module_name.split("."))
        or not attribute.isidentifier()
    ):
        raise ValueError
    return module_name, attribute


def _require_root_entry(root: Path, value: object) -> None:
    module_name, _ = _validate_entry(value)
    relative = Path(*module_name.split("."))
    candidates = (root / relative.with_suffix(".py"), root / relative / "__init__.py")
    entry_path = next((path for path in candidates if path.is_file() and not path.is_symlink()), None)
    if entry_path is None or entry_path.stat().st_size > 1024 * 1024:
        raise ValueError
    try:
        resolved_root = root.resolve(strict=True)
        cursor = entry_path.parent
        while cursor != root:
            if cursor.is_symlink():
                raise ValueError
            cursor = cursor.parent
        entry_path.resolve(strict=True).relative_to(resolved_root)
        source = entry_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError, ValueError):
        raise ValueError from None
    _require_callable(source, value)


def _require_archive_entry(
    package: zipfile.ZipFile, infos: list[zipfile.ZipInfo], value: object
) -> None:
    module_name, _ = _validate_entry(value)
    relative = PurePosixPath(module_name.replace(".", "/"))
    expected = {f"{relative}.py", f"{relative}/__init__.py"}
    matches = [item for item in infos if item.filename in expected and not item.is_dir()]
    if len(matches) != 1 or matches[0].file_size > 1024 * 1024:
        raise ValueError
    for item in infos:
        path = PurePosixPath(item.filename)
        if (
            not item.filename
            or path.is_absolute()
            or ".." in path.parts
            or stat.S_IFMT(item.external_attr >> 16) == stat.S_IFLNK
        ):
            raise ValueError
    try:
        source = package.read(matches[0]).decode("utf-8")
    except (KeyError, OSError, UnicodeDecodeError, zipfile.BadZipFile):
        raise ValueError from None
    _require_callable(source, value)


def _require_callable(source: str, value: object) -> None:
    _, attribute = _validate_entry(value)
    try:
        tree = ast.parse(source)
    except SyntaxError:
        raise ValueError from None
    if not any(
        isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
        and node.name == attribute
        for node in tree.body
    ):
        raise ValueError


def _development_ref(root: Path, module_id: str) -> str:
    digest = hashlib.sha256()
    entries = sorted(root.rglob("*"))
    if len(entries) > 4096 or any(path.is_symlink() for path in entries):
        raise ValueError
    files = [
        path for path in entries
        if path.is_file() and path.name != ".DS_Store"
        and "__pycache__" not in path.parts and path.suffix != ".pyc"
    ]
    if not files or len(files) > 2048:
        raise ValueError
    total = 0
    for path in files:
        if path.is_symlink():
            raise ValueError
        relative = path.relative_to(root).as_posix().encode("utf-8")
        size = path.stat().st_size
        total += size
        if size > 8 * 1024 * 1024 or total > 32 * 1024 * 1024:
            raise ValueError
        digest.update(len(relative).to_bytes(4, "big"))
        digest.update(relative)
        with path.open("rb") as source:
            while chunk := source.read(64 * 1024):
                digest.update(chunk)
    return f"development:{module_id}+{digest.hexdigest()}"


def _metadata(manifest: dict[str, Any], source: str) -> dict[str, object]:
    return {
        "source": source,
        "name": manifest["display_name"].strip(),
        "version": manifest["version"].strip(),
        "description": manifest["description"].strip(),
        "scope": manifest["scope"],
        "module_type": "orchestration",
    }
