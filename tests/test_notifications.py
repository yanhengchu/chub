from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from threading import Event, Thread
from unittest.mock import ANY, MagicMock, call

import httpx
import pytest
import yaml

from app.application import create_app
from app.core.config import NotificationsConfig, Settings
from app.core.logger import configure_logging
from app.notifications.delivery_state import NotificationDeliveryStateStore
from app.notifications.feishu.models import NotificationRegistry
from app.notifications.feishu.service import NotificationService
from app.notifications.models import NotificationRequest


WEBHOOK = "https://open.feishu.cn/open-apis/bot/v2/hook/test-webhook-token"


def test_notification_registry_example_is_valid() -> None:
    path = Path(__file__).resolve().parents[1] / "config" / "notifications.example.yaml"
    registry = NotificationRegistry.model_validate(
        yaml.safe_load(path.read_text(encoding="utf-8"))
    )

    assert list(registry.targets) == ["test"]
    assert registry.targets["test"].display_name == "Test Feishu Group"
    assert registry.targets["test"].webhook_file == "test.webhook"

    users_path = Path(__file__).resolve().parents[1] / "config" / "notification_users.example.yaml"
    assert yaml.safe_load(users_path.read_text(encoding="utf-8")) == {
        "version": 1,
        "users": {},
    }


def authorization(settings: Settings) -> dict[str, str]:
    return {}


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
    root.chmod(0o700)
    secrets.chmod(0o700)
    webhook = secrets / "test.webhook"
    webhook.write_text(WEBHOOK, encoding="utf-8")
    webhook.chmod(0o600)
    registry = root / "registry.yaml"
    users = root / "users.yaml"
    registry.write_text(
        yaml.safe_dump(
            {
                "version": 2,
                "targets": {
                    "test": {
                        "display_name": "Test Feishu Group",
                        "provider": "feishu",
                        "webhook_file": "test.webhook",
                        "allow_mention_all": allow_mention_all,
                    }
                },
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    registry.chmod(0o600)
    users.write_text(
        yaml.safe_dump(
            {
                "version": 1,
                "users": {
                    "maintainer": {
                        "display_name": "维护者",
                        "open_id": "ou_12345678abcdef",
                    }
                },
            },
            sort_keys=False,
            allow_unicode=True,
        ),
        encoding="utf-8",
    )
    users.chmod(0o600)
    settings.notifications = NotificationsConfig(
        registry_file=registry,
        users_file=users,
        secrets_dir=secrets,
        state_file=root / "delivery-state.json",
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
        targets = await client.get(
            "/api/notifications/targets",
            headers=authorization(settings),
        )
        users = await client.get(
            "/api/notifications/users",
            headers=authorization(settings),
            params={"query": "维护"},
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
    assert targets.status_code == 200
    assert targets.json()["data"] == [
        {
            "id": "test",
            "display_name": "Test Feishu Group",
            "provider": "feishu",
            "enabled": True,
            "allow_mention_all": True,
        }
    ]
    assert sent.status_code == 200
    assert sent.json()["data"]["status"] == "accepted"
    assert users.status_code == 200
    assert users.json()["data"] == {
        "users": [{"id": "maintainer", "display_name": "维护者"}],
        "truncated": False,
    }
    assert WEBHOOK not in targets.text + users.text + sent.text
    assert "ou_12345678abcdef" not in targets.text + users.text + sent.text
    provider_body = json.loads(requests[0].content)
    assert '<at user_id="ou_12345678abcdef">维护者</at>' in (
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
async def test_notification_persists_accepted_deduplication_without_message_body(
    settings: Settings,
    tmp_path: Path,
) -> None:
    configure_notifications(settings, tmp_path)
    payload = NotificationRequest(
        request_id="request-persisted-accepted",
        target="test",
        message="private notification body",
    )
    first_requests: list[httpx.Request] = []
    first_service = NotificationService(
        settings.notifications,
        transport=accepted_transport(first_requests),
    )

    first = await first_service.send(payload)
    await first_service.close()

    second_requests: list[httpx.Request] = []
    second_service = NotificationService(
        settings.notifications,
        transport=accepted_transport(second_requests),
    )
    duplicate = await second_service.send(payload)
    await second_service.close()

    state_text = settings.notifications.state_file.read_text(encoding="utf-8")
    assert first.duplicate is False
    assert duplicate.duplicate is True
    assert len(first_requests) == 1
    assert second_requests == []
    assert "private notification body" not in state_text
    assert settings.notifications.state_file.stat().st_mode & 0o077 == 0


@pytest.mark.anyio
async def test_notification_timeout_persists_unknown_without_retry(
    settings: Settings,
    tmp_path: Path,
) -> None:
    configure_notifications(settings, tmp_path)
    payload = NotificationRequest(
        request_id="request-persisted-unknown",
        target="test",
        message="timeout body",
    )
    attempts: list[httpx.Request] = []

    def timeout_handler(request: httpx.Request) -> httpx.Response:
        attempts.append(request)
        raise httpx.ReadTimeout("timeout", request=request)

    first_service = NotificationService(
        settings.notifications,
        transport=httpx.MockTransport(timeout_handler),
    )
    with pytest.raises(Exception) as first_error:
        await first_service.send(payload)
    await first_service.close()

    second_service = NotificationService(
        settings.notifications,
        transport=accepted_transport([]),
    )
    with pytest.raises(Exception) as second_error:
        await second_service.send(payload)
    await second_service.close()

    assert getattr(first_error.value, "code") == "notification_timeout"
    assert getattr(second_error.value, "code") == "notification_delivery_unknown"
    assert len(attempts) == 1


def test_notification_delivery_state_serializes_independent_stores(
    settings: Settings,
    tmp_path: Path,
) -> None:
    configure_notifications(settings, tmp_path)
    request = NotificationRequest(
        request_id="request-cross-process-lock",
        target="test",
        message="concurrent notification",
    )
    first_store = NotificationDeliveryStateStore(
        settings.notifications.state_file,
        ttl_seconds=600,
    )
    second_store = NotificationDeliveryStateStore(
        settings.notifications.state_file,
        ttl_seconds=600,
    )
    started = Event()
    completed = Event()
    result: list[object] = []

    def claim_from_second_store() -> None:
        started.set()
        result.append(second_store.claim(request))
        completed.set()

    with first_store._locked():
        worker = Thread(target=claim_from_second_store)
        worker.start()
        assert started.wait(timeout=1)
        assert not completed.wait(timeout=0.1)

    worker.join(timeout=1)
    assert completed.is_set()
    assert result == [None]
    lock_file = settings.notifications.state_file.with_name(
        f".{settings.notifications.state_file.name}.lock"
    )
    assert lock_file.stat().st_mode & 0o077 == 0


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


def test_notification_rejects_invalid_registry_after_cached_configuration(
    settings: Settings,
    tmp_path: Path,
) -> None:
    registry, _ = configure_notifications(settings, tmp_path)
    service = NotificationService(settings.notifications)

    assert [target.id for target in service.targets()] == ["test"]
    registry.write_text("invalid: [", encoding="utf-8")
    os.utime(registry, ns=(registry.stat().st_atime_ns, registry.stat().st_mtime_ns + 1))

    with pytest.raises(Exception) as captured:
        service.targets()

    assert getattr(captured.value, "code") == "notification_registry_invalid"


def test_notification_rejects_insecure_registry_file(
    settings: Settings,
    tmp_path: Path,
) -> None:
    registry, _ = configure_notifications(settings, tmp_path)
    registry.chmod(0o644)
    service = NotificationService(settings.notifications)

    with pytest.raises(Exception) as captured:
        service.targets()

    assert getattr(captured.value, "code") == "notification_registry_permissions"


def test_notification_rejects_insecure_users_file_after_cached_configuration(
    settings: Settings,
    tmp_path: Path,
) -> None:
    configure_notifications(settings, tmp_path)
    service = NotificationService(settings.notifications)

    assert [target.id for target in service.targets()] == ["test"]
    settings.notifications.users_file.chmod(0o644)

    with pytest.raises(Exception) as captured:
        service.targets()

    assert getattr(captured.value, "code") == "notification_users_permissions"


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


def test_notification_rejects_missing_users_file(
    settings: Settings,
    tmp_path: Path,
) -> None:
    registry, _ = configure_notifications(settings, tmp_path)
    settings.notifications.users_file.unlink()
    service = NotificationService(settings.notifications)

    with pytest.raises(Exception) as captured:
        service.targets()

    assert registry.exists()
    assert getattr(captured.value, "code") == "notification_users_unavailable"


def test_notification_rejects_insecure_users_file(
    settings: Settings,
    tmp_path: Path,
) -> None:
    configure_notifications(settings, tmp_path)
    settings.notifications.users_file.chmod(0o644)
    service = NotificationService(settings.notifications)

    with pytest.raises(Exception) as captured:
        service.targets()

    assert getattr(captured.value, "code") == "notification_users_permissions"


def test_notification_rejects_duplicate_user_open_ids(
    settings: Settings,
    tmp_path: Path,
) -> None:
    configure_notifications(settings, tmp_path)
    users_path = settings.notifications.users_file
    users = yaml.safe_load(users_path.read_text(encoding="utf-8"))
    users["users"]["duplicate"] = {
        "display_name": "重复用户",
        "open_id": "ou_12345678abcdef",
    }
    users_path.write_text(
        yaml.safe_dump(users, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )
    users_path.chmod(0o600)
    service = NotificationService(settings.notifications)

    with pytest.raises(Exception) as captured:
        service.targets()

    assert getattr(captured.value, "code") == "notification_users_invalid"


def test_notification_user_search_matches_name_and_stable_id(
    settings: Settings,
    tmp_path: Path,
) -> None:
    configure_notifications(settings, tmp_path)
    service = NotificationService(settings.notifications)

    by_name = service.search_users("维护")
    by_id = service.search_users("main")

    assert by_name.model_dump() == {
        "users": [{"id": "maintainer", "display_name": "维护者"}],
        "truncated": False,
    }
    assert by_id == by_name


def test_notification_user_search_limits_results(
    settings: Settings,
    tmp_path: Path,
) -> None:
    configure_notifications(settings, tmp_path)
    users_path = settings.notifications.users_file
    users = yaml.safe_load(users_path.read_text(encoding="utf-8"))
    users["users"] = {
        f"candidate_{index:02d}": {
            "display_name": "候选人员",
            "open_id": f"ou_{index:032x}",
        }
        for index in range(21)
    }
    users_path.write_text(
        yaml.safe_dump(users, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )
    users_path.chmod(0o600)
    service = NotificationService(settings.notifications)

    result = service.search_users("候选")

    assert len(result.users) == 20
    assert result.truncated is True
    assert all(item.display_name == "候选人员" for item in result.users)


@pytest.mark.anyio
async def test_notification_rejects_unknown_global_user(
    settings: Settings,
    tmp_path: Path,
) -> None:
    configure_notifications(settings, tmp_path)
    requests: list[httpx.Request] = []
    service = NotificationService(
        settings.notifications,
        transport=accepted_transport(requests),
    )

    with pytest.raises(Exception) as captured:
        await service.send(
            NotificationRequest(
                request_id="request-0006",
                target="test",
                message="提醒测试",
                mention_mode="recipients",
                recipients=["unknown"],
            )
        )

    await service.close()
    assert getattr(captured.value, "code") == "notification_recipient_not_found"
    assert requests == []


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
