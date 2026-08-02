from __future__ import annotations

import asyncio
import logging
import time
from pathlib import Path
from threading import RLock

import httpx
import yaml
from pydantic import ValidationError

from app.core.config import NotificationsConfig
from app.notifications.models import (
    NotificationRegistry,
    NotificationRequest,
    NotificationResult,
    NotificationTargetSummary,
)
from app.notifications.providers.feishu import (
    FeishuProvider,
    FeishuProviderError,
    load_webhook,
)


MAX_REGISTRY_BYTES = 256 * 1024
LOGGER = logging.getLogger("hub.notifications")


class NotificationError(Exception):
    def __init__(self, status_code: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message


class NotificationService:
    def __init__(
        self,
        config: NotificationsConfig,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._config = config
        self._provider = FeishuProvider(
            timeout_seconds=config.timeout_seconds,
            transport=transport,
        )
        self._registry_lock = RLock()
        self._send_lock = asyncio.Lock()
        self._registry: NotificationRegistry | None = None
        self._registry_signature: tuple[int, int] | None = None
        self._dedup: dict[
            str,
            tuple[float, NotificationRequest, NotificationResult],
        ] = {}

    async def close(self) -> None:
        await self._provider.close()

    def targets(self) -> list[NotificationTargetSummary]:
        registry = self._load_registry()
        return [
            NotificationTargetSummary(
                id=target_id,
                provider=target.provider,
                enabled=target.enabled,
                allow_mention_all=target.allow_mention_all,
                recipients=sorted(target.recipients),
            )
            for target_id, target in sorted(registry.targets.items())
        ]

    async def send(self, request: NotificationRequest) -> NotificationResult:
        if not self._config.enabled:
            raise NotificationError(
                503,
                "notifications_disabled",
                "Notifications are disabled",
            )
        if len(request.message.encode("utf-8")) > self._config.max_message_bytes:
            raise NotificationError(
                422,
                "notification_message_too_large",
                "Notification message exceeds the configured limit",
            )

        async with self._send_lock:
            now = time.monotonic()
            self._prune_dedup(now)
            previous = self._dedup.get(request.request_id)
            if previous is not None:
                if previous[1] != request:
                    raise NotificationError(
                        409,
                        "notification_request_conflict",
                        "Notification request ID was already used for different content",
                    )
                return previous[2].model_copy(update={"duplicate": True})

            registry = self._load_registry()
            target = registry.targets.get(request.target)
            if target is None:
                raise NotificationError(
                    404,
                    "notification_target_not_found",
                    "Notification target is not configured",
                )
            if not target.enabled:
                raise NotificationError(
                    409,
                    "notification_target_disabled",
                    "Notification target is disabled",
                )
            try:
                webhook = load_webhook(
                    self._config.secrets_dir,
                    target.webhook_file,
                )
                await self._provider.send(webhook, target, request)
            except FeishuProviderError as exc:
                status_code = 422 if exc.code in {
                    "mention_all_not_allowed",
                    "notification_message_too_large",
                    "notification_recipient_not_found",
                    "notification_secret_invalid",
                    "notification_secret_permissions",
                } else 502
                raise NotificationError(status_code, exc.code, exc.message) from exc

            result = NotificationResult(
                request_id=request.request_id,
                target=request.target,
                provider="feishu",
                status="accepted",
            )
            self._dedup[request.request_id] = (now, request, result)
            return result

    def _prune_dedup(self, now: float) -> None:
        expires_before = now - self._config.dedup_ttl_seconds
        self._dedup = {
            key: value
            for key, value in self._dedup.items()
            if value[0] >= expires_before
        }

    def _load_registry(self) -> NotificationRegistry:
        if not self._config.enabled:
            raise NotificationError(
                503,
                "notifications_disabled",
                "Notifications are disabled",
            )
        path = self._config.registry_file
        try:
            stat = path.stat()
            if stat.st_size > MAX_REGISTRY_BYTES:
                raise ValueError("registry is too large")
            signature = (stat.st_mtime_ns, stat.st_size)
        except OSError as exc:
            return self._fallback_registry(
                "notification_registry_unavailable",
                "Notification registry is unavailable",
                exc,
            )
        except ValueError as exc:
            return self._fallback_registry(
                "notification_registry_invalid",
                "Notification registry is invalid",
                exc,
            )

        with self._registry_lock:
            if self._registry is not None and signature == self._registry_signature:
                return self._registry
            try:
                content = yaml.safe_load(path.read_text(encoding="utf-8"))
                registry = NotificationRegistry.model_validate(content)
            except (OSError, UnicodeError, yaml.YAMLError, ValidationError, ValueError) as exc:
                return self._fallback_registry(
                    "notification_registry_invalid",
                    "Notification registry is invalid",
                    exc,
                )
            self._registry = registry
            self._registry_signature = signature
            return registry

    def _fallback_registry(
        self,
        code: str,
        message: str,
        exc: Exception,
    ) -> NotificationRegistry:
        with self._registry_lock:
            if self._registry is not None:
                LOGGER.warning("%s; using last valid configuration", code)
                return self._registry
        raise NotificationError(503, code, message) from exc
