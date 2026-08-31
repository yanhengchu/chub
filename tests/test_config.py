from pathlib import Path

import pytest

from app.core import config
from app.core.config import (
    NetworkRecoveryConfig,
    OpenClawWeixinChubModeConfig,
    load_settings,
)


VALID_CONFIG = """
app:
  name: Hub
  version: 0.1.0
node:
  id: test
  name: Test
  type: unknown
server:
  tailnet_host: null
  port: 8080
security:
  {}
"""


def test_load_settings_defaults_to_trusted_network_access(tmp_path: Path) -> None:
    config_file = tmp_path / "settings.yaml"
    config_file.write_text(VALID_CONFIG, encoding="utf-8")

    settings = load_settings(config_file)

    assert settings.security.allow_tailscale is True
    assert settings.node.id == "test"


@pytest.mark.parametrize("tailnet_host", ["127.0.0.1", "192.168.1.20", "example.com"])
def test_load_settings_rejects_non_tailscale_listener_hosts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, tailnet_host: str
) -> None:
    config_file = tmp_path / "settings.yaml"
    config_file.write_text(
        VALID_CONFIG.replace("tailnet_host: null", f"tailnet_host: {tailnet_host}"),
        encoding="utf-8",
    )
    with pytest.raises(RuntimeError, match="tailnet_host must be a Tailscale IP"):
        load_settings(config_file)


def test_load_settings_ignores_removed_legacy_tasks_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config_file = tmp_path / "settings.yaml"
    config_file.write_text(
        f"{VALID_CONFIG}\ntasks:\n  default_timeout: 30\n",
        encoding="utf-8",
    )
    settings = load_settings(config_file)

    assert "tasks" not in settings.model_fields_set


def test_load_settings_migrates_legacy_provider_api_config(tmp_path: Path) -> None:
    config_file = tmp_path / "settings.yaml"
    config_file.write_text(
        f"""{VALID_CONFIG}
ai_usage:
  provider_api:
    subscription_page_url: http://10.20.30.40/subscriptions
    subscription_id: 179
    allow_private_http: true
""",
        encoding="utf-8",
    )

    settings = load_settings(config_file)

    assert settings.ai_usage.sub2api.base_url == "http://10.20.30.40"
    assert settings.ai_usage.sub2api.subscription_id == 179


def test_quick_interaction_timeout_defaults_to_six_hours(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_file = tmp_path / "settings.yaml"
    config_file.write_text(VALID_CONFIG, encoding="utf-8")
    settings = load_settings(config_file)

    assert settings.codex_pty.quick_interaction_timeout_seconds == 21_600


def test_network_recovery_requires_fixed_connection_uuids_when_enabled() -> None:
    with pytest.raises(
        ValueError, match="requires a Wi-Fi device and connection UUIDs"
    ):
        NetworkRecoveryConfig(enabled=True)

    configured = NetworkRecoveryConfig(
        enabled=True,
        wifi_device="wlp3s0",
        wifi_connection_uuid="61243ed4-ca59-4f3f-87bb-8e9d3ebe381c",
        vpn_connection_uuid="c583eb7c-9e3a-4686-8980-f3978fd6a6f6",
    )

    assert configured.enabled is True
    assert configured.wifi_connection_uuid == "61243ed4-ca59-4f3f-87bb-8e9d3ebe381c"


def test_network_recovery_rejects_non_uuid_connection_targets() -> None:
    with pytest.raises(ValueError, match="connection IDs must be UUIDs"):
        NetworkRecoveryConfig(
            enabled=True,
            wifi_device="wlp3s0",
            wifi_connection_uuid="home-wifi",
            vpn_connection_uuid="c583eb7c-9e3a-4686-8980-f3978fd6a6f6",
        )


def test_network_recovery_rejects_unsafe_wifi_device_name() -> None:
    with pytest.raises(ValueError, match="Wi-Fi device name is invalid"):
        NetworkRecoveryConfig(
            enabled=True,
            wifi_device="wlp3s0; rm -rf /",
            wifi_connection_uuid="61243ed4-ca59-4f3f-87bb-8e9d3ebe381c",
            vpn_connection_uuid="c583eb7c-9e3a-4686-8980-f3978fd6a6f6",
        )


def test_weixin_chub_display_name_limits_are_configurable() -> None:
    defaults = OpenClawWeixinChubModeConfig()
    configured = OpenClawWeixinChubModeConfig(
        session_name_max_width=42,
        task_name_max_width=72,
    )

    assert defaults.session_name_max_width == 30
    assert defaults.task_name_max_width == 64
    assert configured.session_name_max_width == 42
    assert configured.task_name_max_width == 72


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("session_name_max_width", 3),
        ("session_name_max_width", 97),
        ("task_name_max_width", 3),
        ("task_name_max_width", 97),
    ],
)
def test_weixin_chub_display_name_limits_reject_unsupported_values(
    field: str,
    value: int,
) -> None:
    with pytest.raises(ValueError):
        OpenClawWeixinChubModeConfig.model_validate({field: value})


@pytest.mark.parametrize("field", ["session_name_max_chars", "task_name_max_chars"])
def test_weixin_chub_rejects_removed_character_limit_fields(field: str) -> None:
    with pytest.raises(ValueError):
        OpenClawWeixinChubModeConfig.model_validate({field: 15})


def test_runtime_data_defaults_are_separated(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_file = tmp_path / "settings.yaml"
    config_file.write_text(VALID_CONFIG, encoding="utf-8")
    settings = load_settings(config_file)

    assert settings.codex_pty.data_file.name == "sessions.json"
    assert settings.codex_pty.runtime_dir.parts[-3:] == ("local", "runtime", "codex")
    assert settings.automations.state_dir.parts[-3:] == ("local", "state", "automations")
    assert settings.automations.runtime_dir.parts[-3:] == ("local", "runtime", "automations")
    assert settings.automations.artifacts_dir.parts[-4:] == (
        "local",
        "artifacts",
        "automations",
        "downloads",
    )
    assert settings.project_documents.state_file.parts[-3:] == (
        "local",
        "state",
        "project-documents.json",
    )
    assert settings.requests.state_file.parts[-3:] == (
        "shared",
        "chub",
        "requests.json",
    )


def test_shared_config_example_is_valid() -> None:
    settings = load_settings(config.PROJECT_ROOT / "config" / "settings.example.yaml")

    assert settings.node.type == "unknown"
    assert settings.security.allow_tailscale is True
    assert settings.app.page_title == f"{settings.node.name} · Hub"


def test_load_settings_rejects_missing_file(tmp_path: Path) -> None:
    with pytest.raises(RuntimeError, match="Configuration file not found"):
        load_settings(tmp_path / "missing.yaml")
