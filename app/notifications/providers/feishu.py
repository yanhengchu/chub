from __future__ import annotations

import html
import json
from pathlib import Path
from urllib.parse import urlsplit

import httpx

from app.notifications.models import FeishuTarget, NotificationRequest


MAX_WEBHOOK_BYTES = 2048
MAX_RESPONSE_BYTES = 64 * 1024
MAX_FEISHU_REQUEST_BYTES = 20 * 1024


class FeishuProviderError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def load_webhook(secrets_dir: Path, file_name: str) -> str:
    try:
        root = secrets_dir.resolve(strict=True)
        candidate = root / file_name
        if candidate.is_symlink():
            raise FeishuProviderError(
                "notification_secret_invalid",
                "Notification secret file is invalid",
            )
        resolved = candidate.resolve(strict=True)
        if resolved.parent != root or not resolved.is_file():
            raise FeishuProviderError(
                "notification_secret_invalid",
                "Notification secret file is invalid",
            )
        stat = resolved.stat()
        if stat.st_mode & 0o077:
            raise FeishuProviderError(
                "notification_secret_permissions",
                "Notification secret file permissions are too broad",
            )
        if stat.st_size < 1 or stat.st_size > MAX_WEBHOOK_BYTES:
            raise FeishuProviderError(
                "notification_secret_invalid",
                "Notification secret file is invalid",
            )
        webhook = resolved.read_text(encoding="utf-8").strip()
    except FeishuProviderError:
        raise
    except (OSError, UnicodeError) as exc:
        raise FeishuProviderError(
            "notification_secret_unavailable",
            "Notification secret file is unavailable",
        ) from exc

    try:
        parsed = urlsplit(webhook)
        valid = (
            parsed.scheme == "https"
            and parsed.hostname == "open.feishu.cn"
            and not parsed.username
            and not parsed.password
            and parsed.port is None
            and not parsed.query
            and not parsed.fragment
            and parsed.path.startswith("/open-apis/bot/v2/hook/")
            and len(parsed.path.removeprefix("/open-apis/bot/v2/hook/")) >= 16
        )
    except ValueError:
        valid = False
    if not valid:
        raise FeishuProviderError(
            "notification_secret_invalid",
            "Notification secret file is invalid",
        )
    return webhook


def build_text(target: FeishuTarget, request: NotificationRequest) -> str:
    mentions: list[str] = []
    if request.mention_mode == "all":
        if not target.allow_mention_all:
            raise FeishuProviderError(
                "mention_all_not_allowed",
                "This notification target does not allow mentioning everyone",
            )
        mentions.append('<at user_id="all">所有人</at>')
    elif request.mention_mode == "recipients":
        for recipient_id in request.recipients:
            recipient = target.recipients.get(recipient_id)
            if recipient is None:
                raise FeishuProviderError(
                    "notification_recipient_not_found",
                    "Notification recipient is not configured for this target",
                )
            safe_label = html.escape(recipient_id, quote=False)
            mentions.append(
                f'<at user_id="{recipient.open_id}">{safe_label}</at>'
            )

    safe_message = html.escape(request.message, quote=False)
    return " ".join([*mentions, safe_message])


class FeishuProvider:
    def __init__(
        self,
        *,
        timeout_seconds: float,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._client = httpx.AsyncClient(
            timeout=timeout_seconds,
            follow_redirects=False,
            transport=transport,
        )

    async def close(self) -> None:
        await self._client.aclose()

    async def send(
        self,
        webhook: str,
        target: FeishuTarget,
        request: NotificationRequest,
    ) -> None:
        body = {
            "msg_type": "text",
            "content": {"text": build_text(target, request)},
        }
        encoded = json.dumps(
            body,
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
        if len(encoded) > MAX_FEISHU_REQUEST_BYTES:
            raise FeishuProviderError(
                "notification_message_too_large",
                "Notification message exceeds the provider limit",
            )

        try:
            async with self._client.stream(
                "POST",
                webhook,
                headers={"Content-Type": "application/json"},
                content=encoded,
            ) as response:
                try:
                    declared = int(
                        response.headers.get("content-length", "0") or "0"
                    )
                except ValueError as exc:
                    raise FeishuProviderError(
                        "notification_provider_invalid",
                        "Notification provider returned an invalid response",
                    ) from exc
                if declared > MAX_RESPONSE_BYTES:
                    raise FeishuProviderError(
                        "notification_provider_invalid",
                        "Notification provider returned an invalid response",
                    )
                chunks: list[bytes] = []
                total = 0
                async for chunk in response.aiter_bytes():
                    total += len(chunk)
                    if total > MAX_RESPONSE_BYTES:
                        raise FeishuProviderError(
                            "notification_provider_invalid",
                            "Notification provider returned an invalid response",
                        )
                    chunks.append(chunk)
                if response.status_code < 200 or response.status_code >= 300:
                    raise FeishuProviderError(
                        "notification_provider_unavailable",
                        "Notification provider is unavailable",
                    )
        except FeishuProviderError:
            raise
        except httpx.TimeoutException as exc:
            raise FeishuProviderError(
                "notification_timeout",
                "Notification request timed out",
            ) from exc
        except httpx.HTTPError as exc:
            raise FeishuProviderError(
                "notification_provider_unavailable",
                "Notification provider is unavailable",
            ) from exc

        try:
            payload = json.loads(b"".join(chunks))
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise FeishuProviderError(
                "notification_provider_invalid",
                "Notification provider returned an invalid response",
            ) from exc
        if not isinstance(payload, dict) or payload.get("code") != 0:
            raise FeishuProviderError(
                "notification_rejected",
                "Notification provider rejected the request",
            )
