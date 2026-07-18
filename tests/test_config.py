from pathlib import Path

import pytest

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
  enabled: true
"""


def test_load_settings_uses_environment_token(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config_file = tmp_path / "settings.yaml"
    config_file.write_text(VALID_CONFIG, encoding="utf-8")
    monkeypatch.setenv("HUB_TOKEN", "secret-token")

    settings = load_settings(config_file)

    assert settings.security.token == "secret-token"
    assert settings.node.id == "test"


def test_load_settings_allows_missing_token_during_startup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config_file = tmp_path / "settings.yaml"
    config_file.write_text(VALID_CONFIG, encoding="utf-8")
    monkeypatch.delenv("HUB_TOKEN", raising=False)

    settings = load_settings(config_file)

    assert settings.security.enabled is True
    assert settings.security.token is None


def test_load_settings_rejects_missing_file(tmp_path: Path) -> None:
    with pytest.raises(RuntimeError, match="Configuration file not found"):
        load_settings(tmp_path / "missing.yaml")
