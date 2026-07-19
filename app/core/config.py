from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

import yaml
from dotenv import load_dotenv
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    SecretStr,
    ValidationError,
    field_validator,
)


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG_FILE = PROJECT_ROOT / "config" / "settings.yaml"
DEFAULT_ENV_FILE = PROJECT_ROOT / ".env"


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class AppConfig(StrictModel):
    name: str = Field(min_length=1)
    version: str = Field(min_length=1)


class NodeConfig(StrictModel):
    id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    type: Literal["macos", "ubuntu", "windows", "unknown"]


class ServerConfig(StrictModel):
    host: str = Field(min_length=1)
    port: int = Field(ge=1, le=65535)


class SecurityConfig(StrictModel):
    token: SecretStr | None = None

    @field_validator("token", mode="before")
    @classmethod
    def normalize_token(cls, value: object) -> object:
        if isinstance(value, str):
            value = value.strip()
            return value or None
        return value


class TasksConfig(StrictModel):
    default_timeout: int = Field(default=30, ge=1, le=300)


class LogsConfig(StrictModel):
    file: Path = Path("logs/hub.log")
    level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"
    max_lines: int = Field(default=100, ge=1, le=500)


class Settings(StrictModel):
    app: AppConfig
    node: NodeConfig
    server: ServerConfig
    security: SecurityConfig
    tasks: TasksConfig = TasksConfig()
    logs: LogsConfig = LogsConfig()

    def resolve_runtime_paths(self) -> "Settings":
        if not self.logs.file.is_absolute():
            self.logs.file = PROJECT_ROOT / self.logs.file
        return self


def _read_yaml(path: Path) -> dict[str, Any]:
    try:
        with path.open("r", encoding="utf-8") as file:
            content = yaml.safe_load(file)
    except FileNotFoundError as exc:
        raise RuntimeError(f"Configuration file not found: {path}") from exc
    except yaml.YAMLError as exc:
        raise RuntimeError(f"Invalid YAML configuration: {path}") from exc

    if not isinstance(content, dict):
        raise RuntimeError(f"Configuration root must be a mapping: {path}")
    return content


def load_settings(config_file: str | Path | None = None) -> Settings:
    load_dotenv(DEFAULT_ENV_FILE, override=False)
    configured_path = config_file or os.getenv("HUB_CONFIG_FILE") or DEFAULT_CONFIG_FILE
    path = Path(configured_path).expanduser().resolve()
    data = _read_yaml(path)

    security = data.setdefault("security", {})
    if not isinstance(security, dict):
        raise RuntimeError("Configuration field 'security' must be a mapping")
    security["token"] = os.getenv("HUB_TOKEN")

    try:
        return Settings.model_validate(data).resolve_runtime_paths()
    except ValidationError as exc:
        raise RuntimeError(f"Invalid Hub configuration: {exc}") from exc


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return load_settings()
