from __future__ import annotations

import os
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any, Literal
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from app.core.config import PROJECT_ROOT


CODEX_RUNTIME_SETTINGS_FILE = PROJECT_ROOT / "config" / "ai-runtimes.local.yaml"
MAX_RUNTIME_SETTINGS_BYTES = 64 * 1024
MAX_CODEX_CONFIG_BYTES = 512 * 1024


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class AiRuntimeGeneralSettings(_StrictModel):
    timezone: str = Field(default="Asia/Shanghai", min_length=1, max_length=64)
    weekly_report_session: "WeeklyReportSessionSettings" = Field(
        default_factory=lambda: WeeklyReportSessionSettings()
    )

    @field_validator("timezone")
    @classmethod
    def validate_timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except ZoneInfoNotFoundError as exc:
            raise ValueError("timezone must be a valid IANA timezone") from exc
        return value


class WeeklyReportSessionSettings(_StrictModel):
    runtime_id: Literal["codex"] = "codex"
    permission_mode: Literal["auto-review", "read-only", "full-access"] = "full-access"
    model: str | None = Field(default=None, min_length=1, max_length=128)
    reasoning_effort: str | None = Field(default=None, min_length=1, max_length=32)


class CodexUsageSettings(_StrictModel):
    """Resolved Codex collector settings from Runtime-general and Codex config."""

    timezone: str = Field(default="Asia/Shanghai", min_length=1, max_length=64)
    provider_base_url: str | None = Field(default=None, max_length=2048)

    @field_validator("timezone")
    @classmethod
    def validate_timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except ZoneInfoNotFoundError as exc:
            raise ValueError("timezone must be a valid IANA timezone") from exc
        return value


class RuntimeSettingsStoreUnavailable(OSError):
    pass


class CodexProviderConfigUnavailable(OSError):
    pass


class CodexProviderConfigReader:
    """Reads the active Codex provider origin without exposing credentials."""

    def __init__(self, codex_home: Path) -> None:
        self._path = codex_home / "config.toml"

    def read_base_url(self) -> str | None:
        try:
            if self._path.is_symlink():
                raise OSError("Codex config cannot be a symlink")
            with self._path.open("rb") as config_file:
                content = config_file.read(MAX_CODEX_CONFIG_BYTES + 1)
        except OSError as exc:
            raise CodexProviderConfigUnavailable("Codex provider config unavailable") from exc
        if len(content) > MAX_CODEX_CONFIG_BYTES:
            raise CodexProviderConfigUnavailable("Codex provider config is too large")
        try:
            import tomllib

            data = tomllib.loads(content.decode("utf-8"))
        except (UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
            raise CodexProviderConfigUnavailable("Codex provider config is invalid") from exc
        provider_name = data.get("model_provider")
        providers = data.get("model_providers")
        if not isinstance(provider_name, str) or not isinstance(providers, dict):
            return None
        provider = providers.get(provider_name)
        base_url = provider.get("base_url") if isinstance(provider, dict) else None
        if not isinstance(base_url, str):
            return None
        return self._normalize_origin(base_url)

    @staticmethod
    def _normalize_origin(value: str) -> str | None:
        target = urlsplit(value.strip())
        if (
            target.scheme not in {"http", "https"}
            or not target.hostname
            or target.username is not None
            or target.password is not None
            or target.query
            or target.fragment
            or target.path.rstrip("/")
        ):
            return None
        try:
            target.port
        except ValueError:
            return None
        return target.geturl().rstrip("/")


class AiRuntimeSettingsStore:
    def __init__(self, path: Path = CODEX_RUNTIME_SETTINGS_FILE) -> None:
        self._path = path

    def read_general(self) -> AiRuntimeGeneralSettings:
        data = self._read_raw()
        try:
            return AiRuntimeGeneralSettings.model_validate(data.get("general", {}))
        except (ValidationError, ValueError) as exc:
            raise RuntimeSettingsStoreUnavailable(
                "AI Runtime general settings are invalid"
            ) from exc

    def _read_raw(self) -> dict[str, Any]:
        try:
            if self._path.is_symlink():
                raise OSError("runtime settings cannot be a symlink")
            with self._path.open("rb") as file:
                content = file.read(MAX_RUNTIME_SETTINGS_BYTES + 1)
        except FileNotFoundError:
            return {}
        except OSError as exc:
            raise RuntimeSettingsStoreUnavailable("Codex Runtime settings unavailable") from exc
        if len(content) > MAX_RUNTIME_SETTINGS_BYTES:
            raise RuntimeSettingsStoreUnavailable("Codex Runtime settings are too large")
        try:
            data = yaml.safe_load(content) or {}
            if not isinstance(data, dict):
                raise ValueError("settings root must be a mapping")
            return data
        except (yaml.YAMLError, ValueError) as exc:
            raise RuntimeSettingsStoreUnavailable("Codex Runtime settings are invalid") from exc

    def save_general(self, settings: AiRuntimeGeneralSettings) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        data = self._read_raw()
        data["general"] = settings.model_dump(mode="json", exclude_none=True)
        self._write_raw(data)

    def _write_raw(self, data: dict[str, Any]) -> None:
        payload = yaml.safe_dump(
            data,
            allow_unicode=False,
            sort_keys=False,
        ).encode("utf-8")
        try:
            with NamedTemporaryFile(
                dir=self._path.parent,
                prefix=f".{self._path.name}.",
                suffix=".tmp",
                delete=False,
            ) as file:
                temporary = Path(file.name)
                file.write(payload)
            os.chmod(temporary, 0o600)
            temporary.replace(self._path)
        except OSError as exc:
            try:
                temporary.unlink()
            except (UnboundLocalError, FileNotFoundError):
                pass
            raise RuntimeSettingsStoreUnavailable("AI Runtime settings unavailable") from exc
