from __future__ import annotations

from functools import lru_cache
from ipaddress import ip_address, ip_network
from pathlib import Path
import re
from typing import Any, Literal

import yaml
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
    model_validator,
)

from app.core.network import is_tailscale_ip


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG_FILE = PROJECT_ROOT / "config" / "settings.local.yaml"


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class AppConfig(StrictModel):
    name: str = Field(min_length=1)
    version: str = Field(min_length=1)
    page_title: str | None = Field(default=None, min_length=1)


class NodeConfig(StrictModel):
    id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    type: Literal["macos", "ubuntu", "windows", "unknown"]


class ServerConfig(StrictModel):
    port: int = Field(ge=1, le=65535)
    tailnet_host: str | None = None

    @field_validator("tailnet_host")
    @classmethod
    def validate_tailnet_host(cls, value: str | None) -> str | None:
        if value is None:
            return None
        try:
            address = ip_address(value)
        except ValueError as error:
            raise ValueError("server.tailnet_host must be a Tailscale IP") from error
        if is_tailscale_ip(value):
            return value
        raise ValueError("server.tailnet_host must be a Tailscale IP")


class SecurityConfig(StrictModel):
    allow_tailscale: bool = True


class LogsConfig(StrictModel):
    file: Path = Path("logs/hub.log")
    operations_file: Path = Path("logs/operations.log")
    worker_operations_file: Path = Path("logs/worker-operations.log")
    level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"
    max_lines: int = Field(default=100, ge=1, le=500)


class CodexPtyConfig(StrictModel):
    enabled: bool = True
    workspace: Path = Path("~/workspace")
    data_file: Path = Path("data/local/state/codex/sessions.json")
    runtime_dir: Path = Path("data/local/runtime/codex")
    ticket_ttl_seconds: int = Field(default=600, ge=60, le=3600)
    max_running: int = Field(default=3, ge=1, le=10)
    quick_interaction_timeout_seconds: int = Field(
        default=6 * 60 * 60,
        ge=10 * 60,
        le=24 * 60 * 60,
    )


class AutomationsConfig(StrictModel):
    enabled: bool = True
    shared_config_file: Path = Path("config/automations.yaml")
    local_config_file: Path = Path("config/automations.local.yaml")
    config_file: Path | None = None
    state_dir: Path = Path("data/local/state/automations")
    runtime_dir: Path = Path("data/local/runtime/automations")
    artifacts_dir: Path = Path("data/local/artifacts/automations/downloads")
    max_home_tasks: int = Field(default=3, ge=1, le=10)

    @property
    def config_files(self) -> tuple[Path, Path]:
        return (
            self.shared_config_file,
            self.config_file or self.local_config_file,
        )


class AiUsageProviderApiConfig(StrictModel):
    subscription_page_url: str | None = Field(default=None, max_length=2048)
    subscription_id: int | None = Field(default=None, ge=1)
    allow_private_http: bool = False

    @field_validator("subscription_page_url", mode="before")
    @classmethod
    def normalize_subscription_page_url(cls, value: object) -> object:
        if isinstance(value, str):
            value = value.strip()
            return value or None
        return value

    @model_validator(mode="after")
    def validate_subscription_target(self) -> "AiUsageProviderApiConfig":
        if (self.subscription_page_url is None) != (self.subscription_id is None):
            raise ValueError(
                "subscription_page_url and subscription_id must be configured together"
            )
        if self.subscription_page_url is None:
            return self
        from urllib.parse import urlsplit

        target = urlsplit(self.subscription_page_url)
        if (
            target.scheme not in {"http", "https"}
            or not target.hostname
            or target.username is not None
            or target.password is not None
            or target.query
            or target.fragment
            or target.path.rstrip("/") != "/subscriptions"
        ):
            raise ValueError("subscription_page_url must be a fixed subscriptions page")
        if target.scheme == "http" and not self.allow_private_http:
            raise ValueError("HTTP subscription pages require allow_private_http")
        if target.scheme == "http":
            hostname = target.hostname.lower()
            if hostname != "localhost":
                try:
                    address = ip_address(hostname)
                except ValueError as exc:
                    raise ValueError(
                        "HTTP subscription pages must use a literal private address"
                    ) from exc
                private_ranges = (
                    ip_network("10.0.0.0/8"),
                    ip_network("172.16.0.0/12"),
                    ip_network("192.168.0.0/16"),
                    ip_network("127.0.0.0/8"),
                    ip_network("fc00::/7"),
                    ip_network("fe80::/10"),
                    ip_network("::1/128"),
                )
                if not any(address in network for network in private_ranges):
                    raise ValueError(
                        "HTTP subscription pages must use a literal private address"
                    )
        return self


class AiUsageConfig(StrictModel):
    provider: Literal["openai"] = "openai"
    timezone: str = Field(default="Asia/Shanghai", min_length=1, max_length=64)
    provider_api: AiUsageProviderApiConfig = AiUsageProviderApiConfig()

    @field_validator("timezone")
    @classmethod
    def validate_timezone(cls, value: str) -> str:
        from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

        try:
            ZoneInfo(value)
        except ZoneInfoNotFoundError as exc:
            raise ValueError("timezone must be a valid IANA timezone") from exc
        return value


class ProjectDocumentsConfig(StrictModel):
    state_file: Path = Path("data/local/state/project-documents.json")


class RequestsConfig(StrictModel):
    state_file: Path = Path("data/shared/chub/requests.json")


class NotificationsConfig(StrictModel):
    enabled: bool = True
    registry_file: Path = Path("~/.config/chub/notifications/registry.yaml")
    secrets_dir: Path = Path("~/.config/chub/notifications/secrets")
    timeout_seconds: float = Field(default=5, ge=1, le=30)
    max_message_bytes: int = Field(default=4000, ge=256, le=16 * 1024)
    dedup_ttl_seconds: int = Field(default=600, ge=60, le=3600)


_NETWORK_CONNECTION_UUID = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    re.IGNORECASE,
)
_NETWORK_DEVICE_NAME = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")


class NetworkRecoveryConfig(StrictModel):
    """Fixed NetworkManager recovery targets; disabled unless locally configured."""

    enabled: bool = False
    wifi_device: str | None = None
    wifi_connection_uuid: str | None = None
    vpn_connection_uuid: str | None = None
    wifi_timeout_seconds: int = Field(default=45, ge=10, le=120)
    vpn_timeout_seconds: int = Field(default=60, ge=10, le=180)
    lock_file: Path = Path("data/local/runtime/network-recovery.lock")

    @field_validator(
        "wifi_device", "wifi_connection_uuid", "vpn_connection_uuid", mode="before"
    )
    @classmethod
    def normalize_network_identifier(cls, value: object) -> object:
        if isinstance(value, str):
            value = value.strip()
            return value.lower() or None
        return value

    @field_validator("wifi_device")
    @classmethod
    def validate_wifi_device(cls, value: str | None) -> str | None:
        if value is not None and _NETWORK_DEVICE_NAME.fullmatch(value) is None:
            raise ValueError("network recovery Wi-Fi device name is invalid")
        return value

    @field_validator("wifi_connection_uuid", "vpn_connection_uuid")
    @classmethod
    def validate_connection_uuid(cls, value: str | None) -> str | None:
        if value is not None and _NETWORK_CONNECTION_UUID.fullmatch(value) is None:
            raise ValueError("network recovery connection IDs must be UUIDs")
        return value

    @model_validator(mode="after")
    def validate_enabled_targets(self) -> "NetworkRecoveryConfig":
        if self.enabled and (
            self.wifi_device is None
            or self.wifi_connection_uuid is None
            or self.vpn_connection_uuid is None
        ):
            raise ValueError(
                "enabled network recovery requires a Wi-Fi device and connection UUIDs"
            )
        return self


class OpenClawCompletionNotificationConfig(StrictModel):
    enabled: bool = True
    weixin_account_id: str | None = Field(
        default=None,
        min_length=1,
        max_length=200,
        description="Legacy compatibility field; task routes provide the account.",
    )
    weixin_recipient: str | None = Field(
        default=None,
        min_length=1,
        max_length=500,
        description="Legacy compatibility field; Web tasks never use this recipient.",
    )
    timeout_seconds: int = Field(default=20, ge=1, le=60)
    max_message_chars: int = Field(
        default=2000,
        ge=256,
        le=4000,
        description="Maximum characters in each quick-interaction Weixin message part.",
    )

    @field_validator("weixin_account_id", "weixin_recipient", mode="before")
    @classmethod
    def normalize_optional_identifier(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value

    @field_validator("weixin_recipient")
    @classmethod
    def validate_weixin_recipient(cls, value: str | None) -> str | None:
        if value is not None and not value.endswith("@im.wechat"):
            raise ValueError("weixin_recipient must be a Weixin recipient identifier")
        return value


class OpenClawWeixinChubModeConfig(StrictModel):
    enabled: bool = False
    workspace_id: Literal["home", "workspace", "chub"] = "chub"
    permission_mode: Literal[
        "ask",
        "auto-review",
        "read-only",
        "full-access",
    ] = "full-access"
    model: str | None = Field(default=None, min_length=1, max_length=128)
    reasoning_effort: str | None = Field(default=None, min_length=1, max_length=32)
    state_file: Path = Path("data/local/state/openclaw/weixin-chub-mode.json")
    session_name_max_width: int = Field(default=30, ge=4, le=96)
    task_name_max_width: int = Field(default=64, ge=4, le=96)
    # Translation runs an LLM over untrusted message text and must be opted in.
    translation_enabled: bool = False
    # When set, this supersedes the legacy boolean above.  Keeping the boolean
    # lets an existing local configuration retain its direct/automatic meaning.
    translation_mode: Literal["direct", "auto", "confirm"] | None = None
    translation_queue_limit: int = Field(default=10, ge=1, le=50)
    translation_max_wait_seconds: int = Field(default=1800, ge=60, le=7200)
    translation_max_input_chars: int = Field(default=8000, ge=256, le=8000)
    # Longer task bodies submit directly so a confirmation response can remain
    # within the fixed Weixin reply boundary.
    translation_preprocess_max_input_chars: int = Field(default=1200, ge=1, le=8000)

    @field_validator("model", "reasoning_effort", mode="before")
    @classmethod
    def normalize_optional_selection(cls, value: object) -> object:
        if isinstance(value, str):
            value = value.strip()
            return value or None
        return value


class OpenClawConfig(StrictModel):
    quick_interaction_completion: OpenClawCompletionNotificationConfig = (
        OpenClawCompletionNotificationConfig()
    )
    weixin_chub_mode: OpenClawWeixinChubModeConfig = (
        OpenClawWeixinChubModeConfig()
    )


class Settings(StrictModel):
    app: AppConfig
    node: NodeConfig
    server: ServerConfig
    security: SecurityConfig
    logs: LogsConfig = LogsConfig()
    codex_pty: CodexPtyConfig = CodexPtyConfig()
    automations: AutomationsConfig = AutomationsConfig()
    ai_usage: AiUsageConfig = AiUsageConfig()
    project_documents: ProjectDocumentsConfig = ProjectDocumentsConfig()
    requests: RequestsConfig = RequestsConfig()
    notifications: NotificationsConfig = NotificationsConfig()
    network_recovery: NetworkRecoveryConfig = NetworkRecoveryConfig()
    openclaw: OpenClawConfig = OpenClawConfig()

    @model_validator(mode="before")
    @classmethod
    def discard_legacy_tasks_config(cls, value: object) -> object:
        if isinstance(value, dict):
            value = dict(value)
            value.pop("tasks", None)
        return value

    def resolve_runtime_paths(self) -> "Settings":
        if not self.logs.file.is_absolute():
            self.logs.file = PROJECT_ROOT / self.logs.file
        if not self.logs.operations_file.is_absolute():
            self.logs.operations_file = PROJECT_ROOT / self.logs.operations_file
        if not self.logs.worker_operations_file.is_absolute():
            self.logs.worker_operations_file = (
                PROJECT_ROOT / self.logs.worker_operations_file
            )
        self.codex_pty.workspace = self.codex_pty.workspace.expanduser().resolve()
        if not self.codex_pty.data_file.is_absolute():
            self.codex_pty.data_file = PROJECT_ROOT / self.codex_pty.data_file
        if not self.codex_pty.runtime_dir.is_absolute():
            self.codex_pty.runtime_dir = PROJECT_ROOT / self.codex_pty.runtime_dir
        if not self.automations.shared_config_file.is_absolute():
            self.automations.shared_config_file = (
                PROJECT_ROOT / self.automations.shared_config_file
            )
        if not self.automations.local_config_file.is_absolute():
            self.automations.local_config_file = (
                PROJECT_ROOT / self.automations.local_config_file
            )
        if (
            self.automations.config_file is not None
            and not self.automations.config_file.is_absolute()
        ):
            self.automations.config_file = PROJECT_ROOT / self.automations.config_file
        if not self.automations.state_dir.is_absolute():
            self.automations.state_dir = PROJECT_ROOT / self.automations.state_dir
        if not self.automations.runtime_dir.is_absolute():
            self.automations.runtime_dir = PROJECT_ROOT / self.automations.runtime_dir
        if not self.automations.artifacts_dir.is_absolute():
            self.automations.artifacts_dir = (
                PROJECT_ROOT / self.automations.artifacts_dir
            )
        if not self.project_documents.state_file.is_absolute():
            self.project_documents.state_file = (
                PROJECT_ROOT / self.project_documents.state_file
            )
        if not self.requests.state_file.is_absolute():
            self.requests.state_file = PROJECT_ROOT / self.requests.state_file
        if not self.network_recovery.lock_file.is_absolute():
            self.network_recovery.lock_file = (
                PROJECT_ROOT / self.network_recovery.lock_file
            )
        if not self.openclaw.weixin_chub_mode.state_file.is_absolute():
            self.openclaw.weixin_chub_mode.state_file = (
                PROJECT_ROOT / self.openclaw.weixin_chub_mode.state_file
            )
        self.notifications.registry_file = (
            self.notifications.registry_file.expanduser().resolve()
        )
        self.notifications.secrets_dir = (
            self.notifications.secrets_dir.expanduser().resolve()
        )
        return self


def _read_yaml(path: Path) -> dict[str, Any]:
    try:
        with path.open("r", encoding="utf-8") as file:
            content = yaml.safe_load(file)
    except FileNotFoundError as exc:
        if path == DEFAULT_CONFIG_FILE:
            raise RuntimeError(
                "Configuration file not found: "
                f"{path}. Copy config/settings.example.yaml to "
                "config/settings.local.yaml"
            ) from exc
        raise RuntimeError(f"Configuration file not found: {path}") from exc
    except yaml.YAMLError as exc:
        raise RuntimeError(f"Invalid YAML configuration: {path}") from exc

    if not isinstance(content, dict):
        raise RuntimeError(f"Configuration root must be a mapping: {path}")
    return content


def load_settings(config_file: str | Path | None = None) -> Settings:
    path = Path(config_file or DEFAULT_CONFIG_FILE).expanduser()
    if not path.is_absolute():
        path = PROJECT_ROOT / path
    path = path.resolve()
    data = _read_yaml(path)

    security = data.setdefault("security", {})
    if not isinstance(security, dict):
        raise RuntimeError("Configuration field 'security' must be a mapping")
    try:
        return Settings.model_validate(data).resolve_runtime_paths()
    except ValidationError as exc:
        raise RuntimeError(f"Invalid Hub configuration: {exc}") from exc


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return load_settings()
