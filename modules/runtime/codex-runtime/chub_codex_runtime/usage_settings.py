from __future__ import annotations

from pathlib import Path
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, Field, field_validator


MAX_CODEX_CONFIG_BYTES = 512 * 1024


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


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
