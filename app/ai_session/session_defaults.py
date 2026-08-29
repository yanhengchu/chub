from __future__ import annotations

import json
import os
import stat
import threading
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError


MAX_SESSION_DEFAULTS_BYTES = 16 * 1024


class SessionDefaultsStoreUnavailable(OSError):
    """The node-level Session defaults cannot be used safely."""


class SessionDefaults(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: Literal[2] = 2
    permission_mode: Literal["read-only", "full-access"] = "full-access"


class SessionDefaultsStore:
    """Small, current-format-only storage for the new Session permission default."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = threading.RLock()
        self._defaults = SessionDefaults()
        self._load_error: str | None = None
        self._load()

    @property
    def available(self) -> bool:
        return self._load_error is None

    @property
    def unavailable_reason(self) -> str | None:
        return self._load_error

    def _load(self) -> None:
        try:
            payload = self._read_payload()
        except FileNotFoundError:
            return
        except SessionDefaultsStoreUnavailable as exc:
            self._load_error = str(exc)
            return
        try:
            self._defaults = SessionDefaults.model_validate(payload)
        except ValidationError as exc:
            self._load_error = "节点默认 Session 状态文件格式无效。"
            return

    def _read_payload(self) -> object:
        try:
            descriptor = os.open(
                self.path,
                os.O_RDONLY
                | getattr(os, "O_CLOEXEC", 0)
                | getattr(os, "O_NOFOLLOW", 0),
            )
        except FileNotFoundError:
            raise
        except OSError as exc:
            raise SessionDefaultsStoreUnavailable(
                "节点默认 Session 状态文件无法安全读取。"
            ) from exc
        try:
            metadata = os.fstat(descriptor)
            if (
                not stat.S_ISREG(metadata.st_mode)
                or metadata.st_uid != os.getuid()
                or stat.S_IMODE(metadata.st_mode) & 0o077
                or metadata.st_size > MAX_SESSION_DEFAULTS_BYTES
            ):
                raise SessionDefaultsStoreUnavailable(
                    "节点默认 Session 状态文件的类型、所有者、权限或大小不安全。"
                )
            with os.fdopen(descriptor, "rb") as state_file:
                descriptor = -1
                content = state_file.read(MAX_SESSION_DEFAULTS_BYTES + 1)
            if len(content) > MAX_SESSION_DEFAULTS_BYTES:
                raise SessionDefaultsStoreUnavailable(
                    "节点默认 Session 状态文件大小超过固定上限。"
                )
        except OSError as exc:
            if isinstance(exc, SessionDefaultsStoreUnavailable):
                raise
            raise SessionDefaultsStoreUnavailable(
                "节点默认 Session 状态文件无法读取。"
            ) from exc
        finally:
            if descriptor >= 0:
                os.close(descriptor)
        try:
            return json.loads(content)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise SessionDefaultsStoreUnavailable(
                "节点默认 Session 状态文件格式无效。"
            ) from exc

    def _require_available(self) -> None:
        if self._load_error is not None:
            raise SessionDefaultsStoreUnavailable(self._load_error)

    def _read_current(self) -> SessionDefaults:
        try:
            payload = self._read_payload()
        except FileNotFoundError:
            return SessionDefaults()
        try:
            return SessionDefaults.model_validate(payload)
        except ValidationError as exc:
            raise SessionDefaultsStoreUnavailable(
                "节点默认 Session 状态文件格式无效。"
            ) from exc

    def read(self) -> SessionDefaults:
        with self._lock:
            self._require_available()
            current = self._read_current()
            self._defaults = current
            return current.model_copy(deep=True)

    def save(self, defaults: SessionDefaults) -> None:
        with self._lock:
            self._require_available()
            self._read_current()
            self._defaults = defaults.model_copy(deep=True)
            self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            temporary = self.path.with_name(f".{self.path.name}.tmp")
            flags = (
                os.O_WRONLY
                | os.O_CREAT
                | os.O_TRUNC
                | getattr(os, "O_CLOEXEC", 0)
                | getattr(os, "O_NOFOLLOW", 0)
            )
            descriptor = os.open(temporary, flags, 0o600)
            try:
                os.fchmod(descriptor, 0o600)
                content = (
                    json.dumps(
                        self._defaults.model_dump(mode="json"),
                        ensure_ascii=False,
                        indent=2,
                    )
                    + "\n"
                ).encode("utf-8")
                if len(content) > MAX_SESSION_DEFAULTS_BYTES:
                    raise SessionDefaultsStoreUnavailable(
                        "节点默认 Session 状态超过固定大小上限。"
                    )
                with os.fdopen(descriptor, "wb") as state_file:
                    descriptor = -1
                    state_file.write(content)
                    state_file.flush()
                    os.fsync(state_file.fileno())
                os.replace(temporary, self.path)
                os.chmod(self.path, 0o600)
            finally:
                if descriptor >= 0:
                    os.close(descriptor)
                try:
                    temporary.unlink()
                except FileNotFoundError:
                    pass
