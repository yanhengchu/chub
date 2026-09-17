from __future__ import annotations

import json
import os
import re
import threading
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from app.ai_runtime.contracts import (
    RUNTIME_ID_PATTERN,
    RUNTIME_IMPLEMENTATION_ID_PATTERN,
)


class RuntimeImplementationPreferencesUnavailable(OSError):
    pass


class RuntimeImplementationPreferences(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: Literal[2] = 2
    default_implementation_ids: dict[str, str] = Field(default_factory=dict)
    disabled_implementation_ids: list[str] = Field(default_factory=list, max_length=32)

    @model_validator(mode="before")
    @classmethod
    def migrate_single_default(cls, value: Any) -> Any:
        if not isinstance(value, dict):
            return value
        # New in-memory objects may not have serialized the default version
        # yet. Only the former single-default shape is eligible for migration.
        if "default_implementation_ids" in value or value.get("version", 1) != 1:
            return value
        default_implementation_id = value.get("default_implementation_id")
        defaults = (
            {"codex": default_implementation_id}
            if isinstance(default_implementation_id, str)
            else {}
        )
        return {
            "version": 2,
            "default_implementation_ids": defaults,
            "disabled_implementation_ids": value.get(
                "disabled_implementation_ids", []
            ),
        }


class RuntimeImplementationPreferencesStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = threading.RLock()

    def read(self) -> RuntimeImplementationPreferences:
        with self._lock:
            try:
                raw = self.path.read_bytes()
            except FileNotFoundError:
                return RuntimeImplementationPreferences()
            except OSError as exc:
                raise RuntimeImplementationPreferencesUnavailable(
                    "Runtime 实现偏好不可读取。"
                ) from exc
            if len(raw) > 16 * 1024:
                raise RuntimeImplementationPreferencesUnavailable("Runtime 实现偏好超过固定大小上限。")
            try:
                value = RuntimeImplementationPreferences.model_validate_json(raw)
            except ValidationError as exc:
                raise RuntimeImplementationPreferencesUnavailable("Runtime 实现偏好格式无效。") from exc
            identifiers = [
                *value.disabled_implementation_ids,
                *value.default_implementation_ids.values(),
            ]
            if len(set(value.disabled_implementation_ids)) != len(value.disabled_implementation_ids) or any(
                re.fullmatch(RUNTIME_IMPLEMENTATION_ID_PATTERN, item) is None
                for item in identifiers
            ) or any(
                re.fullmatch(RUNTIME_ID_PATTERN, runtime_id) is None
                for runtime_id in value.default_implementation_ids
            ):
                raise RuntimeImplementationPreferencesUnavailable("Runtime 实现偏好格式无效。")
            return value

    def save(self, value: RuntimeImplementationPreferences) -> None:
        with self._lock:
            self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            temporary = self.path.with_name(f".{self.path.name}.tmp")
            try:
                temporary.write_text(value.model_dump_json(indent=2) + "\n", encoding="utf-8")
                os.chmod(temporary, 0o600)
                os.replace(temporary, self.path)
                os.chmod(self.path, 0o600)
            except OSError as exc:
                temporary.unlink(missing_ok=True)
                raise RuntimeImplementationPreferencesUnavailable("Runtime 实现偏好不可写入。") from exc
