from __future__ import annotations

import fcntl
import hashlib
import json
import os
import stat
import time
from contextlib import contextmanager
from pathlib import Path
from threading import RLock
from typing import Iterator

from pydantic import ValidationError

from app.notifications.errors import NotificationError
from app.notifications.models import (
    NotificationDeliveryRecord,
    NotificationDeliveryState,
    NotificationRequest,
    NotificationResult,
)


MAX_DELIVERY_STATE_BYTES = 256 * 1024


class NotificationDeliveryStateStore:
    """Persist bounded notification request outcomes without retaining message text."""

    def __init__(self, path: Path, *, ttl_seconds: int) -> None:
        self._path = path
        self._lock_path = path.with_name(f".{path.name}.lock")
        self._ttl_seconds = ttl_seconds
        self._lock = RLock()

    def claim(self, request: NotificationRequest) -> NotificationResult | None:
        with self._locked():
            state = self._load()
            now = int(time.time())
            changed = self._prune(state, now)
            fingerprint = self._fingerprint(request)
            record = state.entries.get(request.request_id)
            if record is not None:
                if record.fingerprint != fingerprint:
                    raise NotificationError(
                        409,
                        "notification_request_conflict",
                        "Notification request ID was already used for different content",
                    )
                if record.status == "accepted":
                    if changed:
                        self._save(state)
                    return NotificationResult(
                        request_id=request.request_id,
                        target=request.target,
                        provider="feishu",
                        status="accepted",
                        duplicate=True,
                    )
                if changed:
                    self._save(state)
                raise NotificationError(
                    409,
                    "notification_delivery_unknown",
                    "Notification delivery status is unknown; do not resend",
                )

            if len(state.entries) >= 1000:
                raise NotificationError(
                    503,
                    "notification_delivery_state_unavailable",
                    "Notification delivery state is unavailable",
                )
            state.entries[request.request_id] = NotificationDeliveryRecord(
                fingerprint=fingerprint,
                status="sending",
                expires_at=now + self._ttl_seconds,
            )
            self._save(state)
            return None

    def mark_accepted(self, request: NotificationRequest) -> None:
        self._mark(request, "accepted")

    def mark_unknown(self, request: NotificationRequest) -> None:
        self._mark(request, "unknown")

    def remove(self, request: NotificationRequest) -> None:
        with self._locked():
            state = self._load()
            record = state.entries.get(request.request_id)
            if record is None:
                return
            if record.fingerprint != self._fingerprint(request):
                raise NotificationError(
                    409,
                    "notification_request_conflict",
                    "Notification request ID was already used for different content",
                )
            del state.entries[request.request_id]
            self._save(state)

    def _mark(
        self,
        request: NotificationRequest,
        status: str,
    ) -> None:
        with self._locked():
            state = self._load()
            record = state.entries.get(request.request_id)
            if record is None or record.fingerprint != self._fingerprint(request):
                raise NotificationError(
                    503,
                    "notification_delivery_state_unavailable",
                    "Notification delivery state is unavailable",
                )
            state.entries[request.request_id] = record.model_copy(
                update={"status": status}
            )
            self._save(state)

    def _load(self) -> NotificationDeliveryState:
        self._validate_parent(create=True)
        try:
            if self._path.is_symlink():
                raise ValueError("state file is a symlink")
            if not self._path.exists():
                return NotificationDeliveryState(version=1, entries={})
            file_stat = self._path.stat()
            if not stat.S_ISREG(file_stat.st_mode):
                raise ValueError("state path is not a regular file")
            if file_stat.st_mode & 0o077:
                raise PermissionError("state file permissions are too broad")
            if file_stat.st_size > MAX_DELIVERY_STATE_BYTES:
                raise ValueError("state file is too large")
            payload = json.loads(self._path.read_text(encoding="utf-8"))
            return NotificationDeliveryState.model_validate(payload)
        except FileNotFoundError:
            return NotificationDeliveryState(version=1, entries={})
        except PermissionError as exc:
            raise NotificationError(
                503,
                "notification_delivery_state_permissions",
                "Notification delivery state permissions are too broad",
            ) from exc
        except (OSError, UnicodeError, json.JSONDecodeError, ValidationError, ValueError) as exc:
            raise NotificationError(
                503,
                "notification_delivery_state_unavailable",
                "Notification delivery state is unavailable",
            ) from exc

    @contextmanager
    def _locked(self) -> Iterator[None]:
        """Serialize delivery-state changes across Web and CLI processes."""

        with self._lock:
            descriptor = self._acquire_file_lock()
            try:
                yield
            finally:
                try:
                    fcntl.flock(descriptor, fcntl.LOCK_UN)
                except OSError:
                    pass
                os.close(descriptor)

    def _acquire_file_lock(self) -> int:
        self._validate_parent(create=True)
        try:
            if self._lock_path.is_symlink():
                raise ValueError("state lock file is a symlink")
            flags = os.O_RDWR | os.O_CREAT
            if hasattr(os, "O_NOFOLLOW"):
                flags |= os.O_NOFOLLOW
            descriptor = os.open(self._lock_path, flags, 0o600)
            try:
                file_stat = os.fstat(descriptor)
                if not stat.S_ISREG(file_stat.st_mode):
                    raise ValueError("state lock path is not a regular file")
                if file_stat.st_mode & 0o077:
                    raise PermissionError("state lock file permissions are too broad")
                fcntl.flock(descriptor, fcntl.LOCK_EX)
            except BaseException:
                os.close(descriptor)
                raise
            return descriptor
        except PermissionError as exc:
            raise NotificationError(
                503,
                "notification_delivery_state_permissions",
                "Notification delivery state permissions are too broad",
            ) from exc
        except (OSError, ValueError) as exc:
            raise NotificationError(
                503,
                "notification_delivery_state_unavailable",
                "Notification delivery state is unavailable",
            ) from exc

    def _save(self, state: NotificationDeliveryState) -> None:
        self._validate_parent(create=True)
        temporary = self._path.with_name(f".{self._path.name}.tmp")
        try:
            descriptor = os.open(
                temporary,
                os.O_WRONLY | os.O_CREAT | os.O_TRUNC,
                0o600,
            )
            with os.fdopen(descriptor, "w", encoding="utf-8") as file:
                file.write(state.model_dump_json())
                file.write("\n")
                file.flush()
                os.fsync(file.fileno())
            os.chmod(temporary, 0o600)
            os.replace(temporary, self._path)
            os.chmod(self._path, 0o600)
        except OSError as exc:
            temporary.unlink(missing_ok=True)
            raise NotificationError(
                503,
                "notification_delivery_state_unavailable",
                "Notification delivery state is unavailable",
            ) from exc

    def _validate_parent(self, *, create: bool) -> None:
        try:
            if create:
                self._path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            if self._path.parent.is_symlink():
                raise ValueError("state directory is a symlink")
            directory_stat = self._path.parent.stat()
            if not stat.S_ISDIR(directory_stat.st_mode):
                raise ValueError("state parent is not a directory")
            if directory_stat.st_mode & 0o077:
                raise PermissionError("state directory permissions are too broad")
        except PermissionError as exc:
            raise NotificationError(
                503,
                "notification_delivery_state_permissions",
                "Notification delivery state permissions are too broad",
            ) from exc
        except (OSError, ValueError) as exc:
            raise NotificationError(
                503,
                "notification_delivery_state_unavailable",
                "Notification delivery state is unavailable",
            ) from exc

    @staticmethod
    def _fingerprint(request: NotificationRequest) -> str:
        value = json.dumps(
            {
                "target": request.target,
                "message": request.message,
                "mention_mode": request.mention_mode,
                "recipients": request.recipients,
            },
            ensure_ascii=False,
            separators=(",", ":"),
        )
        return hashlib.sha256(value.encode("utf-8")).hexdigest()

    @staticmethod
    def _prune(state: NotificationDeliveryState, now: int) -> bool:
        expired = [
            request_id
            for request_id, record in state.entries.items()
            if record.expires_at <= now
        ]
        for request_id in expired:
            del state.entries[request_id]
        return bool(expired)
