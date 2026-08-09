import json
import sys
from subprocess import CompletedProcess
from unittest.mock import MagicMock

import pytest

from app.codex.model_catalog import (
    CodexModelCatalog,
    _ModelCatalogOutputTooLarge,
)
from app.core.response import ApiError


def catalog_payload() -> bytes:
    return json.dumps(
        {
            "models": [
                {
                    "slug": "gpt-test",
                    "display_name": "GPT Test",
                    "description": "Visible test model",
                    "visibility": "list",
                    "default_reasoning_level": "medium",
                    "supported_reasoning_levels": [
                        {"effort": "low", "description": "Fast"},
                        {"effort": "medium", "description": "Balanced"},
                    ],
                },
                {
                    "slug": "internal-review",
                    "display_name": "Internal Review",
                    "description": "Hidden model",
                    "visibility": "hide",
                    "default_reasoning_level": "medium",
                    "supported_reasoning_levels": [],
                },
            ]
        }
    ).encode()


def test_catalog_returns_only_visible_models(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("app.codex.model_catalog.shutil.which", lambda _name: "/codex")
    run = MagicMock(return_value=CompletedProcess([], 0, catalog_payload(), b""))
    monkeypatch.setattr(CodexModelCatalog, "_run_bounded", run)

    models = CodexModelCatalog(tmp_path).read()

    assert [model.id for model in models] == ["gpt-test"]
    assert models[0].default_level == "medium"
    assert [level.id for level in models[0].levels] == ["low", "medium"]
    run.assert_called_once_with(["codex", "debug", "models"])


def test_catalog_validates_model_and_reasoning_level(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("app.codex.model_catalog.shutil.which", lambda _name: "/codex")
    monkeypatch.setattr(
        CodexModelCatalog,
        "_run_bounded",
        MagicMock(return_value=CompletedProcess([], 0, catalog_payload(), b"")),
    )
    catalog = CodexModelCatalog(tmp_path)

    catalog.validate(None, None)
    catalog.validate("gpt-test", None)
    catalog.validate("gpt-test", "medium")

    with pytest.raises(ApiError) as missing_model:
        catalog.validate(None, "medium")
    assert missing_model.value.code == "codex_reasoning_effort_requires_model"

    with pytest.raises(ApiError) as unavailable:
        catalog.validate("internal-review", "medium")
    assert unavailable.value.code == "codex_model_unavailable"

    with pytest.raises(ApiError) as unsupported:
        catalog.validate("gpt-test", "high")
    assert unsupported.value.code == "codex_reasoning_effort_unsupported"


def test_catalog_rejects_oversized_output(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("app.codex.model_catalog.shutil.which", lambda _name: "/codex")
    monkeypatch.setattr(
        CodexModelCatalog,
        "_run_bounded",
        MagicMock(return_value=CompletedProcess([], 0, b"x" * (2 * 1024 * 1024 + 1), b"")),
    )

    with pytest.raises(ApiError) as error:
        CodexModelCatalog(tmp_path).read()

    assert error.value.code == "codex_model_catalog_unavailable"


def test_catalog_reports_chub_profile_defaults(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (tmp_path / "config.toml").write_text(
        'model = "gpt-user"\nmodel_reasoning_effort = "low"\n',
        encoding="utf-8",
    )
    (tmp_path / "chub.config.toml").write_text(
        'model = "gpt-test"\nmodel_reasoning_effort = "medium"\n',
        encoding="utf-8",
    )
    monkeypatch.setattr("app.codex.model_catalog.shutil.which", lambda _name: "/codex")
    monkeypatch.setattr(
        CodexModelCatalog,
        "_run_bounded",
        MagicMock(return_value=CompletedProcess([], 0, catalog_payload(), b"")),
    )

    data = CodexModelCatalog(tmp_path).data()

    assert data.default_model == "gpt-test"
    assert data.default_reasoning_effort == "medium"


def test_catalog_stops_reading_when_output_exceeds_limit() -> None:
    command = [
        sys.executable,
        "-c",
        "import sys; sys.stdout.buffer.write(b'x' * (2 * 1024 * 1024 + 1))",
    ]

    with pytest.raises(_ModelCatalogOutputTooLarge):
        CodexModelCatalog._run_bounded(command)
