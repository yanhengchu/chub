from __future__ import annotations

import copy
from functools import lru_cache
import logging
from pathlib import Path
import re
from typing import Any, Literal

import yaml
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    PrivateAttr,
    ValidationError,
    field_validator,
    model_validator,
)



PROJECT_ROOT = Path(__file__).resolve().parents[2]
SETTINGS_FILE = PROJECT_ROOT / "config" / "settings.yaml"
LOCAL_SETTINGS_FILE = PROJECT_ROOT / "config" / "settings.local.yaml"


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


class SecurityConfig(StrictModel):
    allow_tailscale: bool = True


class LogsConfig(StrictModel):
    file: Path = Path("logs/hub.log")
    operations_file: Path = Path("logs/operations.log")
    worker_operations_file: Path = Path("logs/worker-operations.log")
    level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"
    max_lines: int = Field(default=100, ge=1, le=500)


_WORKSPACE_ID_PATTERN = r"^[a-z][a-z0-9-]{0,63}$"
_BUILTIN_WORKSPACE_IDS = frozenset(
    {"chub", "home", "workspace", "weixin-translation"}
)


class ExtraWorkspaceConfig(StrictModel):
    """A locally trusted workspace that can be selected for new Sessions."""

    id: str = Field(pattern=_WORKSPACE_ID_PATTERN)
    name: str = Field(min_length=1, max_length=128)
    path: Path

    @field_validator("id", mode="before")
    @classmethod
    def normalize_id(cls, value: object) -> object:
        if not isinstance(value, str):
            return value
        return value.strip().lower()

    @field_validator("name", mode="before")
    @classmethod
    def normalize_name(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value


class CodexRuntimeConfig(StrictModel):
    enabled: bool = True


class AiRuntimeSharedConfig(StrictModel):
    """Chub-owned state and workspace mapping shared by every Runtime."""

    workspace: Path = Path("~/workspace")
    extra_workspaces: list[ExtraWorkspaceConfig] = Field(default_factory=list)
    state_dir: Path = Path("data/local/state/ai-runtime")
    runtime_dir: Path = Path("data/local/runtime/ai-runtime")
    max_running: int = Field(default=3, ge=1, le=10)
    quick_interaction_timeout_seconds: int = Field(
        default=6 * 60 * 60, ge=10 * 60, le=24 * 60 * 60
    )

    @model_validator(mode="after")
    def validate_extra_workspaces(self) -> "AiRuntimeSharedConfig":
        workspace_ids = [workspace.id for workspace in self.extra_workspaces]
        if len(workspace_ids) != len(set(workspace_ids)):
            raise ValueError("extra workspace IDs must be unique")
        reserved = set(workspace_ids) & _BUILTIN_WORKSPACE_IDS
        if reserved:
            raise ValueError("extra workspace ID is reserved")
        return self


class RuntimePluginsConfig(StrictModel):
    """Fixed local storage for trusted Runtime ZIP installations."""

    install_dir: Path = Path("data/local/runtime/modules")
    max_archive_bytes: int = Field(default=32 * 1024 * 1024, ge=1024)


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


class AiRuntimeConfig(StrictModel):
    shared: AiRuntimeSharedConfig = AiRuntimeSharedConfig()
    codex: CodexRuntimeConfig = CodexRuntimeConfig()
    modules: RuntimePluginsConfig = RuntimePluginsConfig()


class MaintenanceTerminalConfig(StrictModel):
    ticket_ttl_seconds: int = Field(default=600, ge=60, le=3600)


class DeploymentPackageConfig(StrictModel):
    state_file: Path = Path("data/local/state/deployment-package.json")
    artifacts_dir: Path = Path("data/local/artifacts/releases")


class BusinessModulesConfig(StrictModel):
    install_dir: Path = Path("data/local/runtime/business-modules")
    state_file: Path = Path("data/local/state/business-modules/deliveryline.json")
    deliveryline_requirements_dir: Path = Path("data/shared/deliveryline/requirements")
    deliveryline_state_dir: Path = Path("data/local/state/deliveryline")
    max_archive_bytes: int = Field(default=8 * 1024 * 1024, ge=1024, le=32 * 1024 * 1024)


class ProjectDocumentsConfig(StrictModel):
    state_file: Path = Path("data/local/state/project-documents.json")


class RequestsConfig(StrictModel):
    state_file: Path = Path("data/shared/chub/requests.json")


class NotificationsConfig(StrictModel):
    enabled: bool = True
    registry_file: Path = Path("~/.config/chub/notifications/registry.yaml")
    users_file: Path = Path("~/.config/chub/notifications/users.yaml")
    secrets_dir: Path = Path("~/.config/chub/notifications/secrets")
    state_file: Path = Path("data/local/state/notifications/delivery-state.json")
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
    enabled: bool = True
    workspace_id: Literal["home", "workspace", "chub"] = "chub"
    state_file: Path = Path("data/local/state/openclaw/weixin-chub-mode.json")
    # Orchestration plugin ZIPs are intentionally separate from Runtime plugins. They
    # are loaded only by the Web coordinator and never by Quick Worker.
    orchestration_modules_dir: Path = Path(
        "data/local/runtime/openclaw/weixin-orchestration-modules"
    )
    orchestration_module_max_archive_bytes: int = Field(
        default=8 * 1024 * 1024,
        ge=1024,
        le=32 * 1024 * 1024,
    )
    session_name_max_width: int = Field(default=30, ge=4, le=96)
    task_name_max_width: int = Field(default=64, ge=4, le=96)
    # Translation runs an LLM over untrusted message text and must be opted in.
    translation_mode: Literal["direct", "auto", "confirm"] = "direct"
    translation_queue_limit: int = Field(default=10, ge=1, le=50)
    translation_max_wait_seconds: int = Field(default=1800, ge=60, le=7200)
    translation_max_input_chars: int = Field(default=8000, ge=256, le=8000)
    # Longer task bodies submit directly so a confirmation response can remain
    # within the fixed Weixin reply boundary.
    translation_preprocess_max_input_chars: int = Field(default=1200, ge=1, le=8000)

class OpenClawConfig(StrictModel):
    integration_config_path: Path | None = None
    integration_state_dir: Path | None = None
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
    ai_runtime: AiRuntimeConfig = AiRuntimeConfig()
    maintenance_terminal: MaintenanceTerminalConfig = MaintenanceTerminalConfig()
    deployment_package: DeploymentPackageConfig = DeploymentPackageConfig()
    business_modules: BusinessModulesConfig = BusinessModulesConfig()
    automations: AutomationsConfig = AutomationsConfig()
    project_documents: ProjectDocumentsConfig = ProjectDocumentsConfig()
    requests: RequestsConfig = RequestsConfig()
    notifications: NotificationsConfig = NotificationsConfig()
    network_recovery: NetworkRecoveryConfig = NetworkRecoveryConfig()
    openclaw: OpenClawConfig = OpenClawConfig()
    _local_config_fallback: tuple[str, str] | None = PrivateAttr(default=None)
    _local_config_warning: tuple[str, str] | None = PrivateAttr(default=None)

    def resolve_runtime_paths(self) -> "Settings":
        if not self.logs.file.is_absolute():
            self.logs.file = PROJECT_ROOT / self.logs.file
        if not self.logs.operations_file.is_absolute():
            self.logs.operations_file = PROJECT_ROOT / self.logs.operations_file
        if not self.logs.worker_operations_file.is_absolute():
            self.logs.worker_operations_file = (
                PROJECT_ROOT / self.logs.worker_operations_file
            )
        self.ai_runtime.shared.workspace = self.ai_runtime.shared.workspace.expanduser().resolve()
        for workspace in self.ai_runtime.shared.extra_workspaces:
            workspace.path = workspace.path.expanduser().resolve()
        if not self.ai_runtime.shared.state_dir.is_absolute():
            self.ai_runtime.shared.state_dir = PROJECT_ROOT / self.ai_runtime.shared.state_dir
        if not self.ai_runtime.shared.runtime_dir.is_absolute():
            self.ai_runtime.shared.runtime_dir = PROJECT_ROOT / self.ai_runtime.shared.runtime_dir
        retired_state_dir = PROJECT_ROOT / "data/local/state/codex"
        retired_runtime_dir = PROJECT_ROOT / "data/local/runtime/codex"
        if self.ai_runtime.shared.state_dir == retired_state_dir:
            raise RuntimeError(
                "ai_runtime.shared.state_dir must not use retired data/local/state/codex"
            )
        if self.ai_runtime.shared.runtime_dir == retired_runtime_dir:
            raise RuntimeError(
                "ai_runtime.shared.runtime_dir must not use retired data/local/runtime/codex"
            )
        if not self.ai_runtime.modules.install_dir.is_absolute():
            self.ai_runtime.modules.install_dir = (
                PROJECT_ROOT / self.ai_runtime.modules.install_dir
            )
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
        if not self.deployment_package.state_file.is_absolute():
            self.deployment_package.state_file = (
                PROJECT_ROOT / self.deployment_package.state_file
            )
        if not self.deployment_package.artifacts_dir.is_absolute():
            self.deployment_package.artifacts_dir = (
                PROJECT_ROOT / self.deployment_package.artifacts_dir
            )
        if not self.business_modules.install_dir.is_absolute():
            self.business_modules.install_dir = PROJECT_ROOT / self.business_modules.install_dir
        if not self.business_modules.state_file.is_absolute():
            self.business_modules.state_file = PROJECT_ROOT / self.business_modules.state_file
        if not self.business_modules.deliveryline_requirements_dir.is_absolute():
            self.business_modules.deliveryline_requirements_dir = PROJECT_ROOT / self.business_modules.deliveryline_requirements_dir
        if not self.business_modules.deliveryline_state_dir.is_absolute():
            self.business_modules.deliveryline_state_dir = PROJECT_ROOT / self.business_modules.deliveryline_state_dir
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
        if not self.openclaw.weixin_chub_mode.orchestration_modules_dir.is_absolute():
            self.openclaw.weixin_chub_mode.orchestration_modules_dir = (
                PROJECT_ROOT
                / self.openclaw.weixin_chub_mode.orchestration_modules_dir
            )
        if self.openclaw.integration_config_path is not None:
            self.openclaw.integration_config_path = (
                self.openclaw.integration_config_path.expanduser().resolve()
            )
        if self.openclaw.integration_state_dir is not None:
            self.openclaw.integration_state_dir = (
                self.openclaw.integration_state_dir.expanduser().resolve()
            )
        for field_name in ("registry_file", "users_file", "secrets_dir"):
            value = getattr(self.notifications, field_name).expanduser()
            if not value.is_absolute():
                value = PROJECT_ROOT / value
            setattr(self.notifications, field_name, value)
        if not self.notifications.state_file.is_absolute():
            self.notifications.state_file = (
                PROJECT_ROOT / self.notifications.state_file
            )
        return self


def _read_yaml(path: Path) -> dict[str, Any]:
    try:
        with path.open("r", encoding="utf-8") as file:
            content = yaml.safe_load(file)
    except FileNotFoundError as exc:
        raise RuntimeError(f"Configuration file not found: {path}") from exc
    except OSError as exc:
        raise RuntimeError(f"Configuration file cannot be read: {path}") from exc
    except yaml.YAMLError as exc:
        raise RuntimeError(f"Invalid YAML configuration: {path}") from exc

    if not isinstance(content, dict):
        raise RuntimeError(f"Configuration root must be a mapping: {path}")
    return content


def _config_path(value: str | Path) -> Path:
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = PROJECT_ROOT / path
    return path.resolve()


def _validate_settings(data: dict[str, Any]) -> Settings:
    try:
        return Settings.model_validate(data).resolve_runtime_paths()
    except ValidationError as exc:
        raise RuntimeError(f"Invalid Hub configuration: {exc}") from exc


def _merge_settings(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    merged = copy.deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _merge_settings(merged[key], value)
        else:
            merged[key] = copy.deepcopy(value)
    return merged


def _without_release_controlled_local_override(
    override: dict[str, Any],
) -> tuple[dict[str, Any], bool]:
    """Keep the installed node's displayed version tied to tracked source."""
    sanitized = copy.deepcopy(override)
    app = sanitized.get("app")
    if not isinstance(app, dict) or "version" not in app:
        return sanitized, False
    app.pop("version")
    if not app:
        sanitized.pop("app")
    return sanitized, True


def _local_config_error_summary(error: RuntimeError) -> str:
    message = str(error)
    if message.startswith("Configuration file cannot be read"):
        return "cannot be read"
    if message.startswith("Invalid YAML configuration"):
        marker = getattr(error.__cause__, "problem_mark", None)
        if marker is not None:
            return f"invalid YAML at line {marker.line + 1}, column {marker.column + 1}"
        return "invalid YAML"
    if message.startswith("Configuration root must be a mapping"):
        return "root is not a mapping"
    if isinstance(error.__cause__, ValidationError):
        locations = []
        for item in error.__cause__.errors(include_url=False):
            location = item.get("loc")
            if isinstance(location, tuple) and location:
                locations.append(".".join(str(part) for part in location))
        unique_locations = list(dict.fromkeys(locations))
        if unique_locations:
            visible = unique_locations[:5]
            suffix = "" if len(unique_locations) <= 5 else ", ..."
            return f"invalid fields: {', '.join(visible)}{suffix}"
    return "does not satisfy the current configuration schema"


def _record_local_config_fallback(
    settings: Settings, path: Path, reason: str
) -> Settings:
    settings._local_config_fallback = (path.name, reason)
    return settings


def _record_local_config_warning(
    settings: Settings, path: Path, reason: str
) -> Settings:
    settings._local_config_warning = (path.name, reason)
    return settings


def log_local_config_fallback(settings: Settings) -> None:
    fallback = settings._local_config_fallback
    if fallback is not None:
        settings._local_config_fallback = None
        file_name, reason = fallback
        logging.getLogger("hub.config").error(
            "Ignoring local configuration override %s: %s; using config/settings.yaml",
            file_name,
            reason,
        )
    warning = settings._local_config_warning
    if warning is None:
        return
    settings._local_config_warning = None
    file_name, reason = warning
    logging.getLogger("hub.config").warning(
        "Ignoring %s in local configuration override %s; applying its other fields",
        reason,
        file_name,
    )


def load_settings(
    config_file: str | Path | None = None,
    *,
    local_config_file: str | Path | None = None,
) -> Settings:
    """Load the tracked baseline and, when valid, a complete local override layer."""
    base_path = _config_path(config_file or SETTINGS_FILE)
    base_data = _read_yaml(base_path)
    base_settings = _validate_settings(base_data)

    if local_config_file is None:
        if config_file is not None:
            return base_settings
        local_path = _config_path(LOCAL_SETTINGS_FILE)
    else:
        local_path = _config_path(local_config_file)

    try:
        local_data = _read_yaml(local_path)
    except RuntimeError as exc:
        if isinstance(exc.__cause__, FileNotFoundError):
            return base_settings
        return _record_local_config_fallback(
            base_settings, local_path, _local_config_error_summary(exc)
        )

    try:
        sanitized_local_data, ignored_version = _without_release_controlled_local_override(
            local_data
        )
        settings = _validate_settings(_merge_settings(base_data, sanitized_local_data))
    except RuntimeError as exc:
        return _record_local_config_fallback(
            base_settings, local_path, _local_config_error_summary(exc)
        )
    if ignored_version:
        return _record_local_config_warning(
            settings, local_path, "release-controlled app.version"
        )
    return settings


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return load_settings()
