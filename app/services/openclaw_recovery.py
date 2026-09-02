from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from app.core.config import PROJECT_ROOT
from app.core.response import ApiError


MAX_METADATA_BYTES = 64 * 1024
MAX_PACKAGE_METADATA_BYTES = 512 * 1024
SYNC_TIMEOUT_SECONDS = 120
PATCH_TIMEOUT_SECONDS = 30
PATCHES_ROOT = PROJECT_ROOT / "integrations/openclaw/patches"


@dataclass(frozen=True)
class OpenClawSyncReport:
    changed: bool
    message: str


def expected_openclaw_gateway_version() -> str | None:
    """Return the repository's fixed Gateway baseline without affecting health checks."""
    try:
        registry = _load_json(PATCHES_ROOT / "manifest.json")
    except ApiError:
        return None
    version = registry.get("default_openclaw_version")
    return version if isinstance(version, str) and version else None


def _load_baseline_manifest(openclaw_version: str) -> dict:
    registry = _load_json(PATCHES_ROOT / "manifest.json")
    baselines = registry.get("baselines")
    if not isinstance(baselines, list):
        raise _sync_error("OpenClaw 补丁基线索引不可用。")
    for baseline in baselines:
        if not isinstance(baseline, dict) or baseline.get("openclaw_version") != openclaw_version:
            continue
        if baseline.get("status") != "validated":
            raise _sync_error("当前 OpenClaw 版本的补丁基线尚未验收。")
        relative_path = baseline.get("manifest")
        if not isinstance(relative_path, str):
            break
        path = _fixed_patch_file(relative_path)
        return _load_json(path)
    raise _sync_error("当前 OpenClaw 版本没有已验收的补丁基线。")


def synchronize_openclaw_runtime(executable: str, gateway_version: str | None) -> OpenClawSyncReport:
    """Synchronize only the repository's fixed OpenClaw/Weixin baseline."""
    expected_gateway = expected_openclaw_gateway_version()
    if not expected_gateway:
        raise _sync_error("固定 OpenClaw 版本清单不可用。")
    if gateway_version != expected_gateway:
        raise ApiError(
            409,
            "openclaw_compatibility_mismatch",
            f"OpenClaw Gateway 版本不匹配，当前为 {gateway_version or '未知'}，需要 {expected_gateway}。",
        )
    manifest = _load_baseline_manifest(expected_gateway)
    package = _load_json(PROJECT_ROOT / "integrations/openclaw/chub/package.json")
    target = manifest.get("target")
    if not isinstance(target, dict) or not isinstance(package, dict):
        raise _sync_error("固定 OpenClaw 版本清单不可用。")

    manifest_gateway = target.get("openclaw_version")
    expected_adapter = target.get("package_version")
    expected_package = target.get("package_name")
    expected_integrity = target.get("package_integrity")
    expected_plugin = package.get("version")
    if not all(
        isinstance(value, str) and value
        for value in (
            manifest_gateway,
            expected_adapter,
            expected_package,
            expected_integrity,
            expected_plugin,
        )
    ):
        raise _sync_error("固定 OpenClaw 版本清单不完整。")
    if manifest_gateway != expected_gateway:
        raise _sync_error("固定 OpenClaw 补丁基线与索引不一致。")

    changed = False
    plugin_info = _inspect_plugin(executable, "chub")
    if _plugin_needs_sync(plugin_info, expected_plugin):
        _run_command(
            "npm",
            ["run", "plugin:validate"],
            cwd=PROJECT_ROOT / "integrations/openclaw/chub",
            timeout=SYNC_TIMEOUT_SECONDS,
            message="Chub 插件构建校验失败。",
        )
        _run_command(
            executable,
            [
                "plugins",
                "install",
                str(PROJECT_ROOT / "integrations/openclaw/chub"),
                "--force",
            ],
            cwd=PROJECT_ROOT,
            timeout=SYNC_TIMEOUT_SECONDS,
            message="Chub 插件同步失败。",
        )
        changed = True
        plugin_info = _inspect_plugin(executable, "chub")
        if _plugin_needs_sync(plugin_info, expected_plugin):
            raise _sync_error("Chub 插件同步后仍未达到固定版本。")

    adapter_info = _inspect_plugin(executable, "openclaw-weixin")
    if _adapter_needs_sync(adapter_info, expected_package, expected_adapter, expected_integrity):
        _run_command(
            executable,
            ["plugins", "install", f"{expected_package}@{expected_adapter}", "--force"],
            cwd=PROJECT_ROOT,
            timeout=SYNC_TIMEOUT_SECONDS,
            message="微信适配器固定版本同步失败。",
        )
        changed = True
        adapter_info = _inspect_plugin(executable, "openclaw-weixin")
        if _adapter_needs_sync(
            adapter_info,
            expected_package,
            expected_adapter,
            expected_integrity,
        ):
            raise _sync_error("微信适配器同步后仍未达到固定版本。")

    adapter_root = _plugin_root(adapter_info)
    patches = manifest.get("patches")
    if not adapter_root or not isinstance(patches, list) or not patches:
        raise _sync_error("微信适配器运行目录或补丁清单不可用。")
    if _manifest_patch_state(adapter_root, patches) != "applied":
        _run_command(
            executable,
            ["plugins", "install", f"{expected_package}@{expected_adapter}", "--force"],
            cwd=PROJECT_ROOT,
            timeout=SYNC_TIMEOUT_SECONDS,
            message="微信适配器补丁基线恢复失败。",
        )
        changed = True
        adapter_info = _inspect_plugin(executable, "openclaw-weixin")
        if _adapter_needs_sync(
            adapter_info,
            expected_package,
            expected_adapter,
            expected_integrity,
        ):
            raise _sync_error("微信适配器补丁基线恢复后版本仍不匹配。")
        adapter_root = _plugin_root(adapter_info)
        if not adapter_root:
            raise _sync_error("微信适配器补丁基线恢复后运行目录不可用。")
    changed = _apply_manifest_patches(adapter_root, patches) or changed

    runtime_patches = manifest.get("openclaw_runtime_patches")
    if not isinstance(runtime_patches, list) or not runtime_patches:
        raise _sync_error("OpenClaw 运行产物补丁清单不可用。")
    runtime_root = _openclaw_runtime_root(executable, expected_gateway)
    changed = _apply_manifest_patches(runtime_root, runtime_patches) or changed

    return OpenClawSyncReport(
        changed=changed,
        message="已同步固定插件和补丁基线。" if changed else "插件和补丁基线已同步。",
    )


def _load_json(path: Path) -> dict:
    return _load_bounded_json(path, max_bytes=MAX_METADATA_BYTES)


def _load_package_metadata(path: Path) -> dict:
    return _load_bounded_json(path, max_bytes=MAX_PACKAGE_METADATA_BYTES)


def _load_bounded_json(path: Path, *, max_bytes: int) -> dict:
    try:
        if path.stat().st_size > max_bytes:
            raise OSError("metadata too large")
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise _sync_error("固定 OpenClaw 元数据读取失败。") from exc
    if not isinstance(value, dict):
        raise _sync_error("固定 OpenClaw 元数据格式无效。")
    return value


def _inspect_plugin(executable: str, plugin_id: str) -> dict:
    return _run_json(
        executable,
        ["plugins", "inspect", plugin_id, "--runtime", "--json"],
        timeout=SYNC_TIMEOUT_SECONDS,
        message=f"无法检查 OpenClaw 插件 {plugin_id}。",
    )


def _run_json(executable: str, arguments: list[str], *, timeout: int, message: str) -> dict:
    try:
        result = subprocess.run(
            [executable, *arguments],
            cwd=PROJECT_ROOT,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise _sync_error(message) from exc
    if result.returncode != 0 or len(result.stdout) > MAX_METADATA_BYTES:
        raise _sync_error(message)
    try:
        value = json.loads(result.stdout.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise _sync_error(message) from exc
    if not isinstance(value, dict):
        raise _sync_error(message)
    return value


def _run_command(
    executable: str,
    arguments: list[str],
    *,
    cwd: Path,
    timeout: int,
    message: str,
) -> None:
    resolved = shutil.which(executable) if "/" not in executable else executable
    if not resolved:
        raise _sync_error(message)
    try:
        result = subprocess.run(
            [resolved, *arguments],
            cwd=cwd,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise _sync_error(message) from exc
    if result.returncode != 0:
        raise _sync_error(message)


def _plugin_needs_sync(info: dict, expected_version: str) -> bool:
    plugin = info.get("plugin")
    install = info.get("install")
    if not isinstance(plugin, dict) or not isinstance(install, dict):
        return True
    source_path = install.get("sourcePath")
    expected_root: Path | None = None
    actual_root: Path | None = None
    try:
        expected_root = (PROJECT_ROOT / "integrations/openclaw/chub").resolve()
        actual_root = Path(source_path).resolve() if isinstance(source_path, str) else None
    except (OSError, ValueError):
        pass
    return bool(
        plugin.get("version") != expected_version
        or install.get("version") != expected_version
        or plugin.get("status") != "loaded"
        or actual_root != expected_root
    )


def _adapter_needs_sync(
    info: dict,
    expected_package: str,
    expected_version: str,
    expected_integrity: object,
) -> bool:
    plugin = info.get("plugin")
    install = info.get("install")
    return bool(
        not isinstance(plugin, dict)
        or not isinstance(install, dict)
        or plugin.get("packageName") != expected_package
        or plugin.get("version") != expected_version
        or plugin.get("status") != "loaded"
        or install.get("resolvedVersion") != expected_version
        or install.get("integrity") != expected_integrity
    )


def _plugin_root(info: dict) -> Path | None:
    plugin = info.get("plugin")
    install = info.get("install")
    root = (
        plugin.get("rootDir")
        if isinstance(plugin, dict)
        else None
    ) or (install.get("installPath") if isinstance(install, dict) else None)
    if not isinstance(root, str):
        return None
    try:
        path = Path(root).resolve()
    except (OSError, ValueError):
        return None
    if not path.is_dir():
        return None
    return path


def _openclaw_runtime_root(executable: str, expected_version: str) -> Path:
    try:
        executable_path = Path(executable).resolve(strict=True)
    except (OSError, ValueError) as exc:
        raise _sync_error("OpenClaw 运行目录不可用。") from exc
    for candidate in (executable_path.parent, *executable_path.parents[:2]):
        package_file = candidate / "package.json"
        try:
            package = _load_package_metadata(package_file)
        except ApiError:
            continue
        if (
            package.get("name") == "openclaw"
            and package.get("version") == expected_version
            and (candidate / "dist").is_dir()
        ):
            return candidate
    raise _sync_error("OpenClaw 运行目录或版本不可用。")


def _fixed_patch_file(value: object) -> Path:
    if not isinstance(value, str) or not value.startswith("integrations/openclaw/patches/"):
        raise _sync_error("补丁路径不在固定目录内。")
    path = (PROJECT_ROOT / value).resolve()
    patches_root = (PROJECT_ROOT / "integrations/openclaw/patches").resolve()
    if patches_root not in path.parents or not path.is_file():
        raise _sync_error("固定补丁文件不存在或路径不安全。")
    return path


def _verify_patch_integrity(path: Path, metadata: dict) -> None:
    expected = metadata.get("sha256")
    if metadata.get("status") != "validated" or not isinstance(expected, str):
        raise _sync_error("补丁清单缺少已验证的完整性信息。")
    if len(expected) != 64 or any(char not in "0123456789abcdefABCDEF" for char in expected):
        raise _sync_error("补丁清单的完整性信息无效。")
    digest = hashlib.sha256()
    try:
        with path.open("rb") as patch_file:
            for chunk in iter(lambda: patch_file.read(64 * 1024), b""):
                digest.update(chunk)
    except OSError as exc:
        raise _sync_error("补丁文件完整性校验失败。") from exc
    if digest.hexdigest().lower() != expected.lower():
        raise _sync_error("固定补丁文件完整性校验失败。")


def _manifest_patch_state(root: Path, patches: list[object]) -> str:
    states = []
    for patch in patches:
        if not isinstance(patch, dict):
            raise _sync_error("补丁清单包含无法识别的项目。")
        patch_file = _fixed_patch_file(patch.get("file"))
        _verify_patch_integrity(patch_file, patch)
        states.append(_patch_state(root, patch_file))
    if any(state == "partial" for state in states):
        return "partial"
    return "applied" if all(state == "applied" for state in states) else "missing"


def _apply_manifest_patches(root: Path, patches: list[object]) -> bool:
    changed = False
    for patch in patches:
        if not isinstance(patch, dict):
            raise _sync_error("补丁清单包含无法识别的项目。")
        patch_file = _fixed_patch_file(patch.get("file"))
        _verify_patch_integrity(patch_file, patch)
        patch_state = _patch_state(root, patch_file)
        if patch_state == "applied":
            continue
        if patch_state == "partial":
            raise _sync_error(f"补丁 {patch.get('id', 'unknown')} 只应用了一部分。")
        _apply_patch(root, patch_file)
        changed = True
        if _patch_state(root, patch_file) != "applied":
            raise _sync_error(f"补丁 {patch.get('id', 'unknown')} 应用后校验失败。")
    return changed


def _patch_state(root: Path, patch_file: Path) -> str:
    try:
        lines = patch_file.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError) as exc:
        raise _sync_error("补丁文件读取失败。") from exc
    additions: dict[Path, list[str]] = {}
    current: Path | None = None
    for line in lines:
        if line.startswith("+++ b/"):
            current = Path(line[6:])
            additions.setdefault(current, [])
        elif current is not None and line.startswith("+") and not line.startswith("+++"):
            if line[1:]:
                additions[current].append(line[1:])
    if not additions or not any(additions.values()):
        raise _sync_error("补丁文件不包含可校验内容。")
    present = 0
    total = 0
    for relative, markers in additions.items():
        try:
            target = (root / relative).resolve()
        except (OSError, ValueError):
            continue
        total += len(markers)
        if root not in target.parents or not target.is_file():
            continue
        content = target.read_text(encoding="utf-8")
        for marker in markers:
            present += marker in content
    if present == total:
        return "applied"
    if present:
        return "partial"
    return "missing"


def _apply_patch(root: Path, patch_file: Path) -> None:
    patch = shutil.which("patch")
    if not patch:
        raise _sync_error("系统未安装固定 patch 工具。")
    arguments = [patch, "--batch", "--forward", "--fuzz=0", "-p1", "-i", str(patch_file)]
    for dry_run in (True, False):
        command = arguments[:1] + (["--dry-run"] if dry_run else []) + arguments[1:]
        try:
            result = subprocess.run(
                command,
                cwd=root,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=PATCH_TIMEOUT_SECONDS,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise _sync_error("补丁应用失败。") from exc
        if result.returncode != 0:
            raise _sync_error("补丁校验失败，未执行不安全的覆盖。")


def _sync_error(message: str) -> ApiError:
    return ApiError(409, "openclaw_recovery_sync_failed", message)
