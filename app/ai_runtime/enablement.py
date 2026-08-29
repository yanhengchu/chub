from __future__ import annotations

import json
import os
import re
import stat
import threading
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.ai_runtime.contracts import RUNTIME_ID_PATTERN


MAX_RUNTIME_ENABLEMENT_BYTES = 16 * 1024


class RuntimeEnablementStoreUnavailable(OSError):
    """The node Runtime enablement state cannot be used safely."""


class RuntimeEnablement(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: Literal[1] = 1
    disabled_runtime_ids: list[str] = Field(default_factory=list, max_length=32)


class RuntimeEnablementStore:
    """Small, current-format-only node preference for registered Runtimes."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = threading.RLock()
        self._load_error: str | None = None
        self._state = RuntimeEnablement()
        self._load()

    def _load(self) -> None:
        try:
            self._state = self._read_current()
        except RuntimeEnablementStoreUnavailable as exc:
            self._load_error = str(exc)

    def _read_current(self) -> RuntimeEnablement:
        try:
            descriptor = os.open(
                self.path,
                os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0),
            )
        except FileNotFoundError:
            return RuntimeEnablement()
        except OSError as exc:
            raise RuntimeEnablementStoreUnavailable("AI Runtime 启用状态无法安全读取。") from exc
        try:
            metadata = os.fstat(descriptor)
            if (
                not stat.S_ISREG(metadata.st_mode)
                or metadata.st_uid != os.getuid()
                or stat.S_IMODE(metadata.st_mode) & 0o077
                or metadata.st_size > MAX_RUNTIME_ENABLEMENT_BYTES
            ):
                raise RuntimeEnablementStoreUnavailable(
                    "AI Runtime 启用状态的类型、所有者、权限或大小不安全。"
                )
            with os.fdopen(descriptor, "rb") as state_file:
                descriptor = -1
                content = state_file.read(MAX_RUNTIME_ENABLEMENT_BYTES + 1)
        except OSError as exc:
            if isinstance(exc, RuntimeEnablementStoreUnavailable):
                raise
            raise RuntimeEnablementStoreUnavailable("AI Runtime 启用状态无法读取。") from exc
        finally:
            if descriptor >= 0:
                os.close(descriptor)
        if len(content) > MAX_RUNTIME_ENABLEMENT_BYTES:
            raise RuntimeEnablementStoreUnavailable("AI Runtime 启用状态超过固定大小上限。")
        try:
            state = RuntimeEnablement.model_validate(json.loads(content))
        except (UnicodeDecodeError, json.JSONDecodeError, ValidationError) as exc:
            raise RuntimeEnablementStoreUnavailable("AI Runtime 启用状态格式无效。") from exc
        if len(set(state.disabled_runtime_ids)) != len(state.disabled_runtime_ids) or any(
            re.fullmatch(RUNTIME_ID_PATTERN, item) is None for item in state.disabled_runtime_ids
        ):
            raise RuntimeEnablementStoreUnavailable("AI Runtime 启用状态格式无效。")
        return state

    def read(self) -> RuntimeEnablement:
        with self._lock:
            if self._load_error is not None:
                raise RuntimeEnablementStoreUnavailable(self._load_error)
            self._state = self._read_current()
            return self._state.model_copy(deep=True)

    def save(self, state: RuntimeEnablement) -> None:
        with self._lock:
            if self._load_error is not None:
                raise RuntimeEnablementStoreUnavailable(self._load_error)
            self._read_current()
            self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            temporary = self.path.with_name(f".{self.path.name}.tmp")
            descriptor = os.open(
                temporary,
                os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0),
                0o600,
            )
            try:
                os.fchmod(descriptor, 0o600)
                content = (json.dumps(state.model_dump(mode="json"), ensure_ascii=False, indent=2) + "\n").encode("utf-8")
                if len(content) > MAX_RUNTIME_ENABLEMENT_BYTES:
                    raise RuntimeEnablementStoreUnavailable("AI Runtime 启用状态超过固定大小上限。")
                with os.fdopen(descriptor, "wb") as state_file:
                    descriptor = -1
                    state_file.write(content)
                    state_file.flush()
                    os.fsync(state_file.fileno())
                os.replace(temporary, self.path)
                os.chmod(self.path, 0o600)
                self._state = state.model_copy(deep=True)
            finally:
                if descriptor >= 0:
                    os.close(descriptor)
                try:
                    temporary.unlink()
                except FileNotFoundError:
                    pass
