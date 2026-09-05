from __future__ import annotations

import os
from ipaddress import ip_address, ip_network
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from app.core.config import PROJECT_ROOT


CODEX_RUNTIME_SETTINGS_FILE = PROJECT_ROOT / "config" / "ai-runtimes.local.yaml"
MAX_RUNTIME_SETTINGS_BYTES = 64 * 1024


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CodexSub2ApiUsageSettings(_StrictModel):
    base_url: str | None = Field(default=None, max_length=2048)
    subscription_id: int | None = Field(default=None, ge=1)

    @field_validator("base_url", mode="before")
    @classmethod
    def normalize_base_url(cls, value: object) -> object:
        return value.strip() or None if isinstance(value, str) else value

    @model_validator(mode="after")
    def validate_base_url(self) -> "CodexSub2ApiUsageSettings":
        if self.base_url is None:
            return self
        target = urlsplit(self.base_url)
        if (
            target.scheme not in {"http", "https"}
            or not target.hostname
            or target.username is not None
            or target.password is not None
            or target.query
            or target.fragment
            or target.path.rstrip("/")
        ):
            raise ValueError("base_url must be a Sub2API origin")
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
                    ip_network("10.0.0.0/8"), ip_network("172.16.0.0/12"),
                    ip_network("192.168.0.0/16"), ip_network("127.0.0.0/8"),
                    ip_network("fc00::/7"), ip_network("fe80::/10"),
                    ip_network("::1/128"),
                )
                if not any(address in network for network in private_ranges):
                    raise ValueError(
                        "HTTP subscription pages must use a literal private address"
                    )
        return self


class AiRuntimeGeneralSettings(_StrictModel):
    timezone: str = Field(default="Asia/Shanghai", min_length=1, max_length=64)

    @field_validator("timezone")
    @classmethod
    def validate_timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except ZoneInfoNotFoundError as exc:
            raise ValueError("timezone must be a valid IANA timezone") from exc
        return value


class CodexUsageRuntimeSettings(_StrictModel):
    sub2api: CodexSub2ApiUsageSettings = CodexSub2ApiUsageSettings()


class CodexUsageSettings(_StrictModel):
    """Resolved Codex collector settings, including Runtime-general values."""

    timezone: str = Field(default="Asia/Shanghai", min_length=1, max_length=64)
    sub2api: CodexSub2ApiUsageSettings = CodexSub2ApiUsageSettings()

    @field_validator("timezone")
    @classmethod
    def validate_timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except ZoneInfoNotFoundError as exc:
            raise ValueError("timezone must be a valid IANA timezone") from exc
        return value


class CodexRuntimeSettings(_StrictModel):
    usage: CodexUsageRuntimeSettings = CodexUsageRuntimeSettings()


class RuntimeSettingsStoreUnavailable(OSError):
    pass


class AiRuntimeSettingsStore:
    def __init__(self, path: Path = CODEX_RUNTIME_SETTINGS_FILE) -> None:
        self._path = path

    def read(self) -> CodexRuntimeSettings:
        data = self._read_raw()
        try:
            return CodexRuntimeSettings.model_validate(data.get("codex", {}))
        except (ValidationError, ValueError) as exc:
            raise RuntimeSettingsStoreUnavailable("Codex Runtime settings are invalid") from exc

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

    def save(self, settings: CodexRuntimeSettings) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        data = self._read_raw()
        data["codex"] = settings.model_dump(mode="json", exclude_none=True)
        self._write_raw(data)

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
