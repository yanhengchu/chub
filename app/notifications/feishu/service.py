from __future__ import annotations

import asyncio
import logging
import os
import stat
from pathlib import Path
from threading import RLock

import httpx
import yaml
from pydantic import ValidationError

from app.core.config import NotificationsConfig
from app.notifications.delivery_state import NotificationDeliveryStateStore
from app.notifications.errors import NotificationError
from app.notifications.feishu.models import (
    NotificationRegistry,
    NotificationUsers,
)
from app.notifications.feishu.webhook import (
    FeishuProvider,
    FeishuProviderError,
    build_payload,
    load_webhook,
)
from app.notifications.models import (
    NotificationRequest,
    NotificationResult,
    NotificationTargetSummary,
    NotificationUserSearchResult,
    NotificationUserSummary,
)


MAX_REGISTRY_BYTES = 256 * 1024
MAX_USERS_BYTES = 512 * 1024
MAX_USER_SEARCH_RESULTS = 20
LOGGER = logging.getLogger("hub.notifications")


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
        self._configuration: tuple[
            NotificationRegistry,
            NotificationUsers,
        ] | None = None
        self._configuration_signature: tuple[
            tuple[int, int, int],
            tuple[int, int, int],
        ] | None = None
        self._delivery_state = NotificationDeliveryStateStore(
            config.state_file,
            ttl_seconds=config.dedup_ttl_seconds,
        )

    async def close(self) -> None:
        await self._provider.close()

    def targets(self) -> list[NotificationTargetSummary]:
        registry, _ = self._load_configuration()
        return [
            NotificationTargetSummary(
                id=target_id,
                display_name=target.display_name,
                provider=target.provider,
                enabled=target.enabled,
                allow_mention_all=target.allow_mention_all,
            )
            for target_id, target in sorted(registry.targets.items())
        ]

    def search_users(self, query: str) -> NotificationUserSearchResult:
        normalized_query = query.strip()
        if not normalized_query or len(normalized_query) > 128:
            raise NotificationError(
                422,
                "notification_user_query_invalid",
                "Notification user query is invalid",
            )

        _, users = self._load_configuration()
        query_key = normalized_query.casefold()
        matches = [
            NotificationUserSummary(id=user_id, display_name=user.display_name)
            for user_id, user in users.users.items()
            if query_key in user_id.casefold()
            or query_key in user.display_name.casefold()
        ]
        matches.sort(
            key=lambda item: (
                item.display_name.casefold() != query_key,
                item.id.casefold() != query_key,
                not item.display_name.casefold().startswith(query_key),
                not item.id.casefold().startswith(query_key),
                item.display_name.casefold(),
                item.id,
            )
        )
        return NotificationUserSearchResult(
            users=matches[:MAX_USER_SEARCH_RESULTS],
            truncated=len(matches) > MAX_USER_SEARCH_RESULTS,
        )

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
            registry, users = self._load_configuration()
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
                payload = build_payload(target, users, request)
            except FeishuProviderError as exc:
                raise self._provider_error(exc) from exc

            duplicate = self._delivery_state.claim(request)
            if duplicate is not None:
                return duplicate

            try:
                await self._provider.send(webhook, payload)
            except FeishuProviderError as exc:
                if exc.code == "notification_rejected":
                    self._remove_delivery_state_safely(request)
                else:
                    self._mark_delivery_unknown_safely(request)
                raise self._provider_error(exc) from exc

            result = NotificationResult(
                request_id=request.request_id,
                target=request.target,
                provider="feishu",
                status="accepted",
            )
            self._mark_delivery_accepted_safely(request)
            return result

    def _load_configuration(
        self,
    ) -> tuple[NotificationRegistry, NotificationUsers]:
        if not self._config.enabled:
            raise NotificationError(
                503,
                "notifications_disabled",
                "Notifications are disabled",
            )
        registry_path = self._config.registry_file
        users_path = self._config.users_file
        try:
            registry_stat = self._private_config_file_stat(
                registry_path,
                max_bytes=MAX_REGISTRY_BYTES,
            )
        except PermissionError as exc:
            raise NotificationError(
                503,
                "notification_registry_permissions",
                "Notification registry permissions are too broad",
            ) from exc
        except OSError as exc:
            raise NotificationError(
                503,
                "notification_registry_unavailable",
                "Notification registry is unavailable",
            ) from exc
        except ValueError as exc:
            raise NotificationError(
                503,
                "notification_registry_invalid",
                "Notification registry is invalid",
            ) from exc

        try:
            users_stat = self._private_config_file_stat(
                users_path,
                max_bytes=MAX_USERS_BYTES,
            )
        except PermissionError as exc:
            raise NotificationError(
                503,
                "notification_users_permissions",
                "Notification users file permissions are too broad",
            ) from exc
        except OSError as exc:
            raise NotificationError(
                503,
                "notification_users_unavailable",
                "Notification users file is unavailable",
            ) from exc
        except ValueError as exc:
            raise NotificationError(
                503,
                "notification_users_invalid",
                "Notification users file is invalid",
            ) from exc

        signature = (
            (registry_stat.st_ino, registry_stat.st_mtime_ns, registry_stat.st_size),
            (users_stat.st_ino, users_stat.st_mtime_ns, users_stat.st_size),
        )

        with self._registry_lock:
            if (
                self._configuration is not None
                and signature == self._configuration_signature
            ):
                return self._configuration
            try:
                registry_content = yaml.safe_load(
                    registry_path.read_text(encoding="utf-8")
                )
                registry = NotificationRegistry.model_validate(registry_content)
            except OSError as exc:
                raise NotificationError(
                    503,
                    "notification_registry_unavailable",
                    "Notification registry is unavailable",
                ) from exc
            except (UnicodeError, yaml.YAMLError, ValidationError, ValueError) as exc:
                raise NotificationError(
                    503,
                    "notification_registry_invalid",
                    "Notification registry is invalid",
                ) from exc
            try:
                users_content = yaml.safe_load(
                    users_path.read_text(encoding="utf-8")
                )
                users = NotificationUsers.model_validate(users_content)
            except OSError as exc:
                raise NotificationError(
                    503,
                    "notification_users_unavailable",
                    "Notification users file is unavailable",
                ) from exc
            except (UnicodeError, yaml.YAMLError, ValidationError, ValueError) as exc:
                raise NotificationError(
                    503,
                    "notification_users_invalid",
                    "Notification users file is invalid",
                ) from exc
            self._configuration = (registry, users)
            self._configuration_signature = signature
            return self._configuration

    @staticmethod
    def _private_config_file_stat(
        path: Path,
        *,
        max_bytes: int,
    ) -> os.stat_result:
        if path.parent.is_symlink() or path.is_symlink():
            raise ValueError("notification configuration paths must not be symlinks")
        parent_stat = path.parent.stat()
        if not stat.S_ISDIR(parent_stat.st_mode):
            raise ValueError("notification configuration parent is not a directory")
        if parent_stat.st_mode & 0o077:
            raise PermissionError("notification configuration directory permissions are too broad")
        file_stat = path.stat()
        if not stat.S_ISREG(file_stat.st_mode):
            raise ValueError("notification configuration is not a regular file")
        if file_stat.st_mode & 0o077:
            raise PermissionError("notification configuration file permissions are too broad")
        if file_stat.st_size > max_bytes:
            raise ValueError("notification configuration is too large")
        return file_stat

    @staticmethod
    def _provider_error(exc: FeishuProviderError) -> NotificationError:
        status_code = 422 if exc.code in {
            "mention_all_not_allowed",
            "notification_message_too_large",
            "notification_recipient_not_found",
            "notification_secret_invalid",
            "notification_secret_permissions",
        } else 502
        return NotificationError(status_code, exc.code, exc.message)

    def _mark_delivery_accepted_safely(self, request: NotificationRequest) -> None:
        try:
            self._delivery_state.mark_accepted(request)
        except NotificationError:
            LOGGER.error("Unable to persist accepted notification delivery state")

    def _mark_delivery_unknown_safely(self, request: NotificationRequest) -> None:
        try:
            self._delivery_state.mark_unknown(request)
        except NotificationError:
            LOGGER.error("Unable to persist unknown notification delivery state")

    def _remove_delivery_state_safely(self, request: NotificationRequest) -> None:
        try:
            self._delivery_state.remove(request)
        except NotificationError:
            LOGGER.error("Unable to clear rejected notification delivery state")
