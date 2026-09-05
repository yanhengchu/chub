import json
from pathlib import Path
import sqlite3

import pytest

from app.core.response import ApiError
from app.services.openclaw_recovery import (
    _openclaw_runtime_root,
    _patch_state,
    _verify_patch_integrity,
    inspect_openclaw_integration,
)


def test_openclaw_runtime_root_requires_matching_package_metadata(
    tmp_path: Path,
) -> None:
    runtime_root = tmp_path / "openclaw"
    runtime_root.mkdir()
    executable = runtime_root / "openclaw.mjs"
    executable.write_text("#!/usr/bin/env node\n", encoding="utf-8")
    (runtime_root / "dist").mkdir()
    (runtime_root / "package.json").write_text(
        '{"name":"openclaw","version":"2026.8.1"}',
        encoding="utf-8",
    )

    assert _openclaw_runtime_root(str(executable), "2026.8.1") == runtime_root


def test_patch_state_distinguishes_missing_partial_and_applied(tmp_path: Path) -> None:
    root = tmp_path / "package"
    target = root / "src" / "module.ts"
    target.parent.mkdir(parents=True)
    patch_file = tmp_path / "change.patch"
    patch_file.write_text(
        "--- a/src/module.ts\n"
        "+++ b/src/module.ts\n"
        "@@ -1,1 +1,2 @@\n"
        " old\n"
        "+added one\n"
        "+added two\n",
        encoding="utf-8",
    )

    target.write_text("old\n", encoding="utf-8")
    assert _patch_state(root, patch_file) == "missing"
    target.write_text("old\nadded one\n", encoding="utf-8")
    assert _patch_state(root, patch_file) == "partial"
    target.write_text("old\nadded one\nadded two\n", encoding="utf-8")
    assert _patch_state(root, patch_file) == "applied"


def test_patch_state_rejects_missing_target(tmp_path: Path) -> None:
    root = tmp_path / "package"
    root.mkdir()
    patch_file = tmp_path / "change.patch"
    patch_file.write_text(
        "--- a/src/module.ts\n+++ b/src/module.ts\n@@ -1 +1 @@\n+added\n",
        encoding="utf-8",
    )

    assert _patch_state(root, patch_file) == "missing"


def test_patch_state_rejects_patch_without_additions(tmp_path: Path) -> None:
    root = tmp_path / "package"
    root.mkdir()
    patch_file = tmp_path / "empty.patch"
    patch_file.write_text("--- a/file\n+++ b/file\n", encoding="utf-8")

    with pytest.raises(ApiError, match="不包含可校验内容"):
        _patch_state(root, patch_file)


def test_patch_integrity_rejects_modified_patch(tmp_path: Path) -> None:
    patch_file = tmp_path / "change.patch"
    patch_file.write_text("+fixed\n", encoding="utf-8")

    with pytest.raises(ApiError, match="完整性校验失败"):
        _verify_patch_integrity(
            patch_file,
            {
                "status": "validated",
                "sha256": "0" * 64,
            },
        )


def test_patch_integrity_requires_validated_manifest(tmp_path: Path) -> None:
    patch_file = tmp_path / "change.patch"
    patch_file.write_text("+fixed\n", encoding="utf-8")

    with pytest.raises(ApiError, match="已验证的完整性信息"):
        _verify_patch_integrity(
            patch_file,
            {
                "status": "draft",
                "sha256": "0" * 64,
            },
        )


def test_integration_check_reads_json5_configuration_and_plugin_index_without_plugin_cli(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state_dir = tmp_path / "openclaw-state"
    config_path = tmp_path / "openclaw-config" / "main.json5"
    plugins_path = config_path.parent / "plugins.json5"
    chub_root = tmp_path / "installed" / "chub"
    adapter_root = tmp_path / "installed" / "weixin-adapter"
    chub_root.mkdir(parents=True)
    adapter_root.mkdir(parents=True)
    config_path.parent.mkdir(parents=True)
    config_path.write_text(
        "// OpenClaw accepts JSON5 configuration.\n"
        "{ plugins: { $include: './plugins.json5' } }\n",
        encoding="utf-8",
    )
    plugins_path.write_text(
        "{\n"
        "  allow: ['chub', 'openclaw-weixin',],\n"
        "  entries: {\n"
        "    chub: { enabled: true, },\n"
        "    'openclaw-weixin': { enabled: true, },\n"
        "  },\n"
        "}\n",
        encoding="utf-8",
    )
    (chub_root / "package.json").write_text('{"name":"chub","version":"0.1.1"}', encoding="utf-8")
    (chub_root / "openclaw.plugin.json").write_text('{"id":"chub","version":"0.1.1"}', encoding="utf-8")
    (adapter_root / "package.json").write_text(
        '{"name":"@tencent-weixin/openclaw-weixin","version":"2.4.8"}',
        encoding="utf-8",
    )
    (adapter_root / "openclaw.plugin.json").write_text(
        '{"id":"openclaw-weixin"}',
        encoding="utf-8",
    )
    database_path = state_dir / "state" / "openclaw.sqlite"
    database_path.parent.mkdir(parents=True)
    connection = sqlite3.connect(database_path)
    connection.execute(
        "CREATE TABLE config_machine_state ("
        "state_key TEXT NOT NULL PRIMARY KEY, value_json TEXT NOT NULL, "
        "updated_at_ms INTEGER NOT NULL)"
    )
    connection.execute(
        "INSERT INTO config_machine_state VALUES (?, ?, ?)",
        (
            "plugins.installedIndex",
            json.dumps(
                {
                    "revision": 1,
                    "index": {
                        "installRecords": {
                            "chub": {
                                "source": "path",
                                "installPath": str(chub_root),
                                "version": "0.1.1",
                            },
                            "openclaw-weixin": {
                                "source": "npm",
                                "installPath": str(adapter_root),
                                "resolvedName": "@tencent-weixin/openclaw-weixin",
                                "resolvedVersion": "2.4.8",
                                "integrity": "sha512-example",
                            },
                        }
                    },
                }
            ),
            1,
        ),
    )
    connection.commit()
    connection.close()
    monkeypatch.setattr(
        "app.services.openclaw_recovery.expected_openclaw_gateway_version",
        lambda: "2026.8.1",
    )
    monkeypatch.setattr(
        "app.services.openclaw_recovery._load_baseline_manifest",
        lambda _version: {
            "target": {
                "package_name": "@tencent-weixin/openclaw-weixin",
                "package_version": "2.4.8",
                "package_integrity": "sha512-example",
            },
            "patches": [],
            "openclaw_runtime_patches": [],
        },
    )
    monkeypatch.setattr(
        "app.services.openclaw_recovery._inspect_plugin",
        lambda *_args, **_kwargs: pytest.fail("settings integration check must not start OpenClaw plugin CLI"),
    )

    report = inspect_openclaw_integration(
        config_path=config_path,
        state_dir=state_dir,
    )

    assert report.chub_plugin.state == "verified"
    assert report.weixin_adapter.state == "verified"
    assert report.patches == ()
    assert "补丁内容仅在重启与恢复时核验" in report.message
