from pathlib import Path

import pytest

from app.core.response import ApiError
from app.services.openclaw_recovery import (
    _openclaw_runtime_root,
    _patch_state,
    _verify_patch_integrity,
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
