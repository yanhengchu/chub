import os
from pathlib import Path
from unittest.mock import patch

import pytest
from pydantic import SecretStr

from app.core import config
from app.core.config import load_settings


VALID_CONFIG = """
app:
  name: Hub
  version: 0.1.0
node:
  id: test
  name: Test
  type: unknown
server:
  host: 127.0.0.1
  port: 8080
security:
  {}
"""


def test_load_settings_uses_environment_token(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config_file = tmp_path / "settings.yaml"
    config_file.write_text(VALID_CONFIG, encoding="utf-8")
    monkeypatch.setenv("HUB_TOKEN", "secret-token")

    settings = load_settings(config_file)

    assert settings.security.token is not None
    assert settings.security.token.get_secret_value() == "secret-token"
    assert settings.node.id == "test"


def test_load_settings_allows_missing_token_during_startup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config_file = tmp_path / "settings.yaml"
    config_file.write_text(VALID_CONFIG, encoding="utf-8")
    monkeypatch.delenv("HUB_TOKEN", raising=False)

    with patch("app.core.config.load_dotenv"):
        settings = load_settings(config_file)

    assert settings.security.token is None


def test_load_settings_treats_blank_token_as_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config_file = tmp_path / "settings.yaml"
    config_file.write_text(VALID_CONFIG, encoding="utf-8")
    monkeypatch.setenv("HUB_TOKEN", "   ")

    settings = load_settings(config_file)

    assert settings.security.token is None


def test_load_settings_reads_project_env_file(
    tmp_path: Path,
) -> None:
    config_file = tmp_path / "settings.yaml"
    env_file = tmp_path / ".env"
    config_file.write_text(VALID_CONFIG, encoding="utf-8")
    env_file.write_text(
        "HUB_TOKEN=token-loaded-from-local-env-file\n",
        encoding="utf-8",
    )
    with (
        patch.dict(os.environ, {}, clear=False),
        patch.object(config, "DEFAULT_ENV_FILE", env_file),
    ):
        os.environ.pop("HUB_TOKEN", None)
        settings = load_settings(config_file)

    assert settings.security.token == SecretStr("token-loaded-from-local-env-file")


def test_system_environment_overrides_project_env_file(
    tmp_path: Path,
) -> None:
    config_file = tmp_path / "settings.yaml"
    env_file = tmp_path / ".env"
    config_file.write_text(VALID_CONFIG, encoding="utf-8")
    env_file.write_text("HUB_TOKEN=token-from-file\n", encoding="utf-8")
    with (
        patch.dict(
            os.environ,
            {"HUB_TOKEN": "token-from-system-environment"},
        ),
        patch.object(config, "DEFAULT_ENV_FILE", env_file),
    ):
        settings = load_settings(config_file)

    assert settings.security.token == SecretStr("token-from-system-environment")


def test_project_env_file_can_select_node_config(tmp_path: Path) -> None:
    config_file = tmp_path / "selected-settings.yaml"
    env_file = tmp_path / ".env"
    config_file.write_text(VALID_CONFIG, encoding="utf-8")
    env_file.write_text(
        (
            "HUB_TOKEN=token-from-env-file\n"
            f"HUB_CONFIG_FILE={config_file}\n"
        ),
        encoding="utf-8",
    )

    with (
        patch.dict(os.environ, {}, clear=False),
        patch.object(config, "DEFAULT_ENV_FILE", env_file),
    ):
        os.environ.pop("HUB_TOKEN", None)
        os.environ.pop("HUB_CONFIG_FILE", None)
        settings = load_settings()

    assert settings.node.id == "test"
    assert settings.security.token == SecretStr("token-from-env-file")


def test_relative_config_path_is_resolved_from_project_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_root = tmp_path / "project"
    config_file = project_root / "config" / "settings.local.yaml"
    config_file.parent.mkdir(parents=True)
    config_file.write_text(VALID_CONFIG, encoding="utf-8")
    monkeypatch.setattr(config, "PROJECT_ROOT", project_root)
    monkeypatch.setenv("HUB_CONFIG_FILE", "config/settings.local.yaml")

    settings = load_settings()

    assert settings.node.id == "test"


@pytest.mark.parametrize(
    ("filename", "expected_platform"),
    [
        ("settings.macos.example.yaml", "macos"),
        ("settings.ubuntu.example.yaml", "ubuntu"),
    ],
)
def test_platform_config_examples_are_valid(
    filename: str,
    expected_platform: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("HUB_TOKEN", raising=False)

    with patch("app.core.config.load_dotenv"):
        settings = load_settings(config.PROJECT_ROOT / "config" / filename)

    assert settings.node.type == expected_platform
    assert settings.security.token is None


def test_load_settings_rejects_missing_file(tmp_path: Path) -> None:
    with pytest.raises(RuntimeError, match="Configuration file not found"):
        load_settings(tmp_path / "missing.yaml")
