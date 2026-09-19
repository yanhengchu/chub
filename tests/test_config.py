import logging
from pathlib import Path

import pytest

from app.core import config
from app.core.config import (
    NetworkRecoveryConfig,
    OpenClawConfig,
    OpenClawWeixinChubModeConfig,
    log_local_config_fallback,
    load_settings,
)
from app.core.logger import configure_logging


VALID_CONFIG = """
app:
  name: Hub
  version: 0.1.0
node:
  id: test
  name: Test
  type: unknown
server:
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


def test_load_settings_rejects_removed_tailnet_host(tmp_path: Path) -> None:
    config_file = tmp_path / "settings.yaml"
    config_file.write_text(
        VALID_CONFIG.replace("server:\n", "server:\n  tailnet_host: auto\n"),
        encoding="utf-8",
    )
    with pytest.raises(RuntimeError, match="tailnet_host"):
        load_settings(config_file)


def test_load_settings_rejects_removed_legacy_tasks_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config_file = tmp_path / "settings.yaml"
    config_file.write_text(
        f"{VALID_CONFIG}\ntasks:\n  default_timeout: 30\n",
        encoding="utf-8",
    )
    with pytest.raises(RuntimeError, match="tasks"):
        load_settings(config_file)


def test_load_settings_rejects_retired_weixin_chub_session_profile(tmp_path: Path) -> None:
    config_file = tmp_path / "settings.yaml"
    config_file.write_text(
        f"""{VALID_CONFIG}
openclaw:
  weixin_chub_mode:
    permission_mode: read-only
    model: legacy-model
    reasoning_effort: high
""",
        encoding="utf-8",
    )

    with pytest.raises(RuntimeError, match="permission_mode"):
        load_settings(config_file)


def test_load_settings_rejects_legacy_translation_enabled_config(tmp_path: Path) -> None:
    config_file = tmp_path / "settings.yaml"
    config_file.write_text(
        f"""{VALID_CONFIG}
openclaw:
  weixin_chub_mode:
    translation_enabled: true
""",
        encoding="utf-8",
    )

    with pytest.raises(RuntimeError, match="translation_enabled"):
        load_settings(config_file)


def test_load_settings_rejects_removed_ai_usage_config(tmp_path: Path) -> None:
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

    with pytest.raises(RuntimeError, match="ai_usage"):
        load_settings(config_file)


def test_quick_interaction_timeout_defaults_to_six_hours(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_file = tmp_path / "settings.yaml"
    config_file.write_text(VALID_CONFIG, encoding="utf-8")
    settings = load_settings(config_file)

    assert settings.ai_runtime.shared.quick_interaction_timeout_seconds == 21_600


def test_extra_workspaces_are_resolved_and_keep_their_display_name(
    tmp_path: Path,
) -> None:
    config_file = tmp_path / "settings.yaml"
    config_file.write_text(
        f"""{VALID_CONFIG}
ai_runtime:
  shared:
    extra_workspaces:
      - id: Deliveryline
        name: Deliveryline Platform
        path: {tmp_path}/deliveryline
""",
        encoding="utf-8",
    )

    settings = load_settings(config_file)

    assert settings.ai_runtime.shared.extra_workspaces[0].id == "deliveryline"
    assert settings.ai_runtime.shared.extra_workspaces[0].name == "Deliveryline Platform"
    assert settings.ai_runtime.shared.extra_workspaces[0].path == tmp_path / "deliveryline"


def test_rejects_retired_codex_shared_fields(
    tmp_path: Path,
) -> None:
    config_file = tmp_path / "settings.yaml"
    config_file.write_text(
        f"""{VALID_CONFIG}
ai_runtime:
  codex:
    workspace: {tmp_path}/workspace
    data_file: {tmp_path}/legacy-state/sessions.json
    runtime_dir: {tmp_path}/legacy-runtime
""",
        encoding="utf-8",
    )

    with pytest.raises(RuntimeError, match="Extra inputs are not permitted"):
        load_settings(config_file)


def test_rejects_retired_shared_cleanup_fields(
    tmp_path: Path,
) -> None:
    config_file = tmp_path / "settings.yaml"
    config_file.write_text(
        f"""{VALID_CONFIG}
ai_runtime:
  shared:
    legacy_state_file: {tmp_path}/outside/sessions.json
    legacy_state_dir: {tmp_path}/outside/state
    legacy_runtime_dir: {tmp_path}/outside/runtime
""",
        encoding="utf-8",
    )

    with pytest.raises(RuntimeError, match="Extra inputs are not permitted"):
        load_settings(config_file)


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("state_dir", "data/local/state/codex"),
        ("runtime_dir", "data/local/runtime/codex"),
    ],
)
def test_shared_runtime_config_rejects_retired_codex_directories(
    tmp_path: Path,
    key: str,
    value: str,
) -> None:
    config_file = tmp_path / "settings.yaml"
    config_file.write_text(
        f"{VALID_CONFIG}\nai_runtime:\n  shared:\n    {key}: {value}\n",
        encoding="utf-8",
    )

    with pytest.raises(RuntimeError, match="must not use retired"):
        load_settings(config_file)


@pytest.mark.parametrize(
    ("workspace_id", "message"),
    [
        ("chub", "extra workspace ID is reserved"),
        ("workspace", "extra workspace ID is reserved"),
        ("deliveryline!", "String should match pattern"),
    ],
)
def test_extra_workspaces_reject_reserved_or_invalid_ids(
    tmp_path: Path,
    workspace_id: str,
    message: str,
) -> None:
    config_file = tmp_path / "settings.yaml"
    config_file.write_text(
        f"""{VALID_CONFIG}
ai_runtime:
  shared:
    extra_workspaces:
      - id: {workspace_id}
        name: Extra
        path: {tmp_path}/extra
""",
        encoding="utf-8",
    )

    with pytest.raises(RuntimeError, match=message):
        load_settings(config_file)


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


def test_weixin_chub_mode_is_enabled_by_default() -> None:
    assert OpenClawWeixinChubModeConfig().enabled is True


def test_openclaw_integration_paths_are_optional_and_resolved(tmp_path: Path) -> None:
    config_file = tmp_path / "settings.yaml"
    config_file.write_text(
        f"""{VALID_CONFIG}
openclaw:
  integration_config_path: ~/openclaw-config.json
  integration_state_dir: {tmp_path}/openclaw-state
""",
        encoding="utf-8",
    )

    settings = load_settings(config_file)

    assert settings.openclaw.integration_config_path == Path.home() / "openclaw-config.json"
    assert settings.openclaw.integration_state_dir == tmp_path / "openclaw-state"
    assert OpenClawConfig().integration_config_path is None


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

    assert settings.ai_runtime.shared.state_dir.parts[-3:] == ("local", "state", "ai-runtime")
    assert settings.ai_runtime.shared.runtime_dir.parts[-3:] == ("local", "runtime", "ai-runtime")
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


def test_tracked_default_config_is_valid() -> None:
    settings = load_settings(config.PROJECT_ROOT / "config" / "settings.yaml")

    assert settings.node.type == "unknown"
    assert settings.security.allow_tailscale is True
    assert settings.app.page_title == f"{settings.node.name} · Hub"


def test_load_settings_rejects_missing_file(tmp_path: Path) -> None:
    with pytest.raises(RuntimeError, match="Configuration file not found"):
        load_settings(tmp_path / "missing.yaml")


def test_local_config_overrides_the_tracked_default(tmp_path: Path) -> None:
    base_file = tmp_path / "settings.yaml"
    local_file = tmp_path / "settings.local.yaml"
    base_file.write_text(VALID_CONFIG, encoding="utf-8")
    local_file.write_text(
        "node:\n  name: Local Node\nserver:\n  port: 9090\n", encoding="utf-8"
    )

    settings = load_settings(base_file, local_config_file=local_file)

    assert settings.node.id == "test"
    assert settings.node.name == "Local Node"
    assert settings.server.port == 9090


def test_invalid_local_config_is_discarded_as_a_whole(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    base_file = tmp_path / "settings.yaml"
    local_file = tmp_path / "settings.local.yaml"
    base_file.write_text(VALID_CONFIG, encoding="utf-8")
    local_file.write_text(
        "node:\n  name: Local Node\nremoved_setting: secret-value\n",
        encoding="utf-8",
    )

    settings = load_settings(base_file, local_config_file=local_file)
    log_local_config_fallback(settings)

    assert settings.node.name == "Test"
    assert settings.server.port == 8080
    assert local_file.read_text(encoding="utf-8").endswith("secret-value\n")
    assert "Ignoring local configuration override settings.local.yaml" in caplog.text
    assert "invalid fields: removed_setting" in caplog.text
    assert "secret-value" not in caplog.text


def test_malformed_local_config_uses_default_and_logs_a_redacted_error(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    base_file = tmp_path / "settings.yaml"
    local_file = tmp_path / "settings.local.yaml"
    base_file.write_text(VALID_CONFIG, encoding="utf-8")
    local_file.write_text("node: [broken\n", encoding="utf-8")

    settings = load_settings(base_file, local_config_file=local_file)
    log_local_config_fallback(settings)

    assert settings.node.name == "Test"
    assert "invalid YAML at line" in caplog.text
    assert "[broken" not in caplog.text


def test_missing_local_config_uses_the_default_without_a_warning(tmp_path: Path) -> None:
    base_file = tmp_path / "settings.yaml"
    base_file.write_text(VALID_CONFIG, encoding="utf-8")

    settings = load_settings(
        base_file, local_config_file=tmp_path / "settings.local.yaml"
    )

    assert settings.node.name == "Test"


def test_unreadable_local_config_is_discarded_with_a_redacted_error(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    base_file = tmp_path / "settings.yaml"
    local_file = tmp_path / "settings.local.yaml"
    base_file.write_text(VALID_CONFIG, encoding="utf-8")
    local_file.mkdir()

    settings = load_settings(base_file, local_config_file=local_file)
    log_local_config_fallback(settings)

    assert settings.node.name == "Test"
    assert "cannot be read" in caplog.text


def test_local_config_fallback_is_written_after_logging_is_configured(
    tmp_path: Path,
) -> None:
    base_file = tmp_path / "settings.yaml"
    local_file = tmp_path / "settings.local.yaml"
    base_file.write_text(VALID_CONFIG, encoding="utf-8")
    local_file.write_text("removed_setting: secret-value\n", encoding="utf-8")

    settings = load_settings(base_file, local_config_file=local_file)
    settings.logs.file = tmp_path / "hub.log"
    settings.logs.operations_file = tmp_path / "operations.log"
    settings.logs.worker_operations_file = tmp_path / "worker-operations.log"
    configure_logging(settings.logs)
    log_local_config_fallback(settings)
    for handler in logging.getLogger().handlers:
        handler.flush()

    log_text = settings.logs.file.read_text(encoding="utf-8")
    assert "Ignoring local configuration override settings.local.yaml" in log_text
    assert "invalid fields: removed_setting" in log_text
    assert "secret-value" not in log_text
