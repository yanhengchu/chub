from __future__ import annotations

import os
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.core.config import PROJECT_ROOT


RUNTIME_GENERAL_SETTINGS_FILE = PROJECT_ROOT / "config" / "ai-runtimes.local.yaml"
MAX_RUNTIME_SETTINGS_BYTES = 64 * 1024


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class AiRuntimeGeneralSettings(_StrictModel):
    # Runtime discovery owns the first concrete default.  A settings file must
    # not identify a first-party Runtime before one has been discovered.
    default_runtime_id: str | None = Field(default=None, pattern=r"^[a-z][a-z0-9-]{0,31}$")
    new_session_permission: Literal["auto-review", "read-only", "full-access"] = (
        "full-access"
    )
    model: str | None = Field(default=None, min_length=1, max_length=128)
    reasoning_effort: str | None = Field(default=None, min_length=1, max_length=32)


class RuntimeSettingsStoreUnavailable(OSError):
    pass


class AiRuntimeSettingsStore:
    def __init__(self, path: Path = RUNTIME_GENERAL_SETTINGS_FILE) -> None:
        self._path = path

    def read_general(self) -> AiRuntimeGeneralSettings:
        data = self._read_raw()
        general = data.get("general", {})
        if isinstance(general, dict):
            # Retired general settings must not influence the current Runtime.
            general = {key: value for key, value in general.items() if key != "timezone"}
            general.pop("weekly_report_session", None)
        try:
            return AiRuntimeGeneralSettings.model_validate(general)
        except (ValidationError, ValueError) as exc:
            raise RuntimeSettingsStoreUnavailable(
                "AI Runtime general settings are invalid"
            ) from exc

    def save_general(self, settings: AiRuntimeGeneralSettings) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        data = self._read_raw()
        data["general"] = settings.model_dump(mode="json", exclude_none=True)
        payload = yaml.safe_dump(data, allow_unicode=False, sort_keys=False).encode("utf-8")
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

    def _read_raw(self) -> dict[str, Any]:
        try:
            if self._path.is_symlink():
                raise OSError("runtime settings cannot be a symlink")
            with self._path.open("rb") as file:
                content = file.read(MAX_RUNTIME_SETTINGS_BYTES + 1)
        except FileNotFoundError:
            return {}
        except OSError as exc:
            raise RuntimeSettingsStoreUnavailable("AI Runtime settings unavailable") from exc
        if len(content) > MAX_RUNTIME_SETTINGS_BYTES:
            raise RuntimeSettingsStoreUnavailable("AI Runtime settings are too large")
        try:
            data = yaml.safe_load(content) or {}
            if not isinstance(data, dict):
                raise ValueError("settings root must be a mapping")
            return data
        except (yaml.YAMLError, ValueError) as exc:
            raise RuntimeSettingsStoreUnavailable("AI Runtime settings are invalid") from exc
