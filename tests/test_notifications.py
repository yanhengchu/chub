from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from unittest.mock import ANY, MagicMock, call

import httpx
import pytest
import yaml

from app.application import create_app
from app.core.config import NotificationsConfig, Settings
from app.core.logger import configure_logging
from app.notifications import NotificationRequest, NotificationService
from app.notifications.models import NotificationRegistry


WEBHOOK = "https://open.feishu.cn/open-apis/bot/v2/hook/test-webhook-token"


def test_notification_registry_example_is_valid() -> None:
    path = Path(__file__).resolve().parents[1] / "config" / "notifications.example.yaml"
    registry = NotificationRegistry.model_validate(
        yaml.safe_load(path.read_text(encoding="utf-8"))
    )

    assert list(registry.targets) == ["test"]
    assert registry.targets["test"].webhook_file == "test.webhook"


def authorization(settings: Settings) -> dict[str, str]:
    token = settings.security.token
    assert token is not None
    return {"Authorization": f"Bearer {token.get_secret_value()}"}


def test_http_client_info_logs_do_not_expose_webhook(
    settings: Settings,
) -> None:
    configure_logging(settings.logs)

    logging.getLogger("httpx").info(
        'HTTP Request: POST %s "HTTP/1.1 200 OK"',
        WEBHOOK,
    )
    logging.getLogger("httpcore").info("request.url=%s", WEBHOOK)
    logging.getLogger("hub.notifications").info(
        "Notification accepted target=test"
    )
    for handler in logging.getLogger().handlers:
        handler.flush()

    log_text = settings.logs.file.read_text(encoding="utf-8")
    assert WEBHOOK not in log_text
    assert "test-webhook-token" not in log_text
    assert "Notification accepted target=test" in log_text


def configure_notifications(
    settings: Settings,
    tmp_path: Path,
    *,
    allow_mention_all: bool = True,
) -> tuple[Path, Path]:
    root = tmp_path / "notifications"
    secrets = root / "secrets"
    secrets.mkdir(parents=True)
    secrets.chmod(0o700)
    webhook = secrets / "test.webhook"
    webhook.write_text(WEBHOOK, encoding="utf-8")
    webhook.chmod(0o600)
    registry = root / "registry.yaml"
    registry.write_text(
        yaml.safe_dump(
            {
                "version": 1,
                "targets": {
                    "test": {
                        "provider": "feishu",
                        "webhook_file": "test.webhook",
                        "allow_mention_all": allow_mention_all,
                        "recipients": {
                            "maintainer": {
                                "open_id": "ou_12345678abcdef",
                            }
                        },
                    }
                },
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    settings.notifications = NotificationsConfig(
        registry_file=registry,
        secrets_dir=secrets,
        timeout_seconds=5,
        max_message_bytes=4000,
        dedup_ttl_seconds=600,
    )
    return registry, webhook


def accepted_transport(requests: list[httpx.Request]) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            json={"code": 0, "msg": "success"},
        )

    return httpx.MockTransport(handler)


@pytest.mark.anyio
async def test_notification_api_is_protected_and_hides_secrets(
    settings: Settings,
    tmp_path: Path,
) -> None:
    configure_notifications(settings, tmp_path)
    requests: list[httpx.Request] = []
    service = NotificationService(
        settings.notifications,
        transport=accepted_transport(requests),
    )
    app = create_app(settings)
    original = app.state.notification_service
    app.state.notification_service = service
    transport = httpx.ASGITransport(app=app)

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        unauthorized = await client.get("/api/notifications/targets")
        targets = await client.get(
            "/api/notifications/targets",
            headers=authorization(settings),
        )
        sent = await client.post(
            "/api/notifications/send",
            headers=authorization(settings),
            json={
                "request_id": "request-0001",
                "target": "test",
                "message": "通知内容",
                "mention_mode": "recipients",
                "recipients": ["maintainer"],
            },
        )

    await service.close()
    await original.close()
    assert unauthorized.status_code == 401
    assert targets.status_code == 200
    assert targets.json()["data"] == [
        {
            "id": "test",
            "provider": "feishu",
            "enabled": True,
            "allow_mention_all": True,
            "recipients": ["maintainer"],
        }
    ]
    assert sent.status_code == 200
    assert sent.json()["data"]["status"] == "accepted"
    assert WEBHOOK not in targets.text + sent.text
    assert "ou_12345678abcdef" not in targets.text + sent.text
    provider_body = json.loads(requests[0].content)
    assert '<at user_id="ou_12345678abcdef">maintainer</at>' in (
        provider_body["content"]["text"]
    )


@pytest.mark.anyio
async def test_notification_escapes_injected_mentions_and_deduplicates(
    settings: Settings,
    tmp_path: Path,
) -> None:
    configure_notifications(settings, tmp_path)
    requests: list[httpx.Request] = []
    service = NotificationService(
        settings.notifications,
        transport=accepted_transport(requests),
    )
    payload = NotificationRequest(
        request_id="request-0002",
        target="test",
        message='<at user_id="all">所有人</at> 普通文本',
    )

    first = await service.send(payload)
    duplicate = await service.send(payload)

    await service.close()
    assert first.status == "accepted"
    assert first.duplicate is False
    assert duplicate.duplicate is True
    assert len(requests) == 1
    text = json.loads(requests[0].content)["content"]["text"]
    assert "&lt;at user_id=\"all\"&gt;所有人&lt;/at&gt;" in text
    assert '<at user_id="all">' not in text


@pytest.mark.anyio
async def test_notification_rejects_reused_request_id_with_different_content(
    settings: Settings,
    tmp_path: Path,
) -> None:
    configure_notifications(settings, tmp_path)
    requests: list[httpx.Request] = []
    service = NotificationService(
        settings.notifications,
        transport=accepted_transport(requests),
    )
    await service.send(
        NotificationRequest(
            request_id="request-0005",
            target="test",
            message="first",
        )
    )

    with pytest.raises(Exception) as captured:
        await service.send(
            NotificationRequest(
                request_id="request-0005",
                target="test",
                message="second",
            )
        )

    await service.close()
    assert getattr(captured.value, "code") == "notification_request_conflict"
    assert len(requests) == 1


@pytest.mark.anyio
async def test_notification_rejects_mention_all_without_target_permission(
    settings: Settings,
    tmp_path: Path,
) -> None:
    configure_notifications(settings, tmp_path, allow_mention_all=False)
    requests: list[httpx.Request] = []
    service = NotificationService(
        settings.notifications,
        transport=accepted_transport(requests),
    )

    with pytest.raises(Exception) as captured:
        await service.send(
            NotificationRequest(
                request_id="request-0003",
                target="test",
                message="维护通知",
                mention_mode="all",
            )
        )

    await service.close()
    assert getattr(captured.value, "code") == "mention_all_not_allowed"
    assert requests == []


def test_notification_registry_keeps_last_valid_configuration(
    settings: Settings,
    tmp_path: Path,
) -> None:
    registry, _ = configure_notifications(settings, tmp_path)
    service = NotificationService(settings.notifications)

    assert [target.id for target in service.targets()] == ["test"]
    registry.write_text("invalid: [", encoding="utf-8")
    os.utime(registry, ns=(registry.stat().st_atime_ns, registry.stat().st_mtime_ns + 1))

    assert [target.id for target in service.targets()] == ["test"]


def test_notification_rejects_oversized_initial_registry(
    settings: Settings,
    tmp_path: Path,
) -> None:
    registry, _ = configure_notifications(settings, tmp_path)
    registry.write_text("x" * (256 * 1024 + 1), encoding="utf-8")
    service = NotificationService(settings.notifications)

    with pytest.raises(Exception) as captured:
        service.targets()

    assert getattr(captured.value, "code") == "notification_registry_invalid"


@pytest.mark.anyio
async def test_notification_api_logs_lifecycle_without_content(
    settings: Settings,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    configure_notifications(settings, tmp_path)
    operation_log = MagicMock(return_value="operation-id")
    monkeypatch.setattr("app.api.notifications.log_operation", operation_log)
    service = NotificationService(
        settings.notifications,
        transport=accepted_transport([]),
    )
    app = create_app(settings)
    original = app.state.notification_service
    app.state.notification_service = service
    transport = httpx.ASGITransport(app=app)

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/api/notifications/send",
            headers=authorization(settings),
            json={
                "request_id": "request-0004",
                "target": "test",
                "message": "secret message body",
            },
        )

    await service.close()
    await original.close()
    assert response.status_code == 200
    assert operation_log.call_args_list == [
        call(
            ANY,
            action="send_notification",
            status="requested",
            target="test",
        ),
        call(
            ANY,
            action="send_notification",
            status="started",
            target="test",
            operation_id="operation-id",
        ),
        call(
            ANY,
            action="send_notification",
            status="succeeded",
            target="test",
            operation_id="operation-id",
        ),
    ]
    assert "secret message body" not in repr(operation_log.call_args_list)
