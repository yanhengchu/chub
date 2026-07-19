from __future__ import annotations

from unittest.mock import patch

import httpx
import pytest

from app.application import create_app
from app.core.config import Settings
from app.tasks.models import TaskFailed


def authorization(settings: Settings) -> dict[str, str]:
    token = settings.security.token
    assert token is not None
    return {"Authorization": f"Bearer {token.get_secret_value()}"}


@pytest.mark.anyio
async def test_task_list_filters_platform_and_hides_internals(
    settings: Settings,
) -> None:
    with patch("app.application.detect_platform", return_value="macos"):
        app = create_app(settings)
    transport = httpx.ASGITransport(app=app)

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/api/tasks", headers=authorization(settings))

    assert response.status_code == 200
    tasks = response.json()["data"]["tasks"]
    assert [task["name"] for task in tasks] == [
        "check_codex",
        "check_system",
        "show_version",
    ]
    serialized = response.text
    assert "handler" not in serialized
    assert "command" not in serialized
    assert "executable" not in serialized


@pytest.mark.anyio
async def test_task_run_returns_success_result(settings: Settings) -> None:
    with patch("app.application.detect_platform", return_value="unknown"):
        app = create_app(settings)
    transport = httpx.ASGITransport(app=app)

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/api/tasks/run",
            headers=authorization(settings),
            json={"task": "show_version", "params": {}},
        )

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["task"] == "show_version"
    assert data["status"] == "success"


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("payload", "status_code", "code"),
    [
        (
            {"task": "missing", "params": {}},
            404,
            "task_not_found",
        ),
        (
            {"task": "check_docker", "params": {}},
            409,
            "task_not_supported",
        ),
        (
            {"task": "show_version", "params": {"command": "anything"}},
            422,
            "invalid_task_parameters",
        ),
        (
            {"task": "show_version", "params": {}, "command": "anything"},
            422,
            "invalid_request",
        ),
    ],
)
async def test_task_run_rejects_invalid_requests(
    settings: Settings,
    payload: dict[str, object],
    status_code: int,
    code: str,
) -> None:
    with patch("app.application.detect_platform", return_value="macos"):
        app = create_app(settings)
    transport = httpx.ASGITransport(app=app)

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/api/tasks/run",
            headers=authorization(settings),
            json=payload,
        )

    assert response.status_code == status_code
    assert response.json()["error"]["code"] == code


@pytest.mark.anyio
async def test_task_failure_does_not_affect_other_endpoints(
    settings: Settings,
) -> None:
    with (
        patch("app.application.detect_platform", return_value="macos"),
        patch(
            "app.tasks.platform.macos.check_codex.run",
            side_effect=TaskFailed("Codex is unavailable"),
        ),
    ):
        app = create_app(settings)
    transport = httpx.ASGITransport(app=app)

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        task_response = await client.post(
            "/api/tasks/run",
            headers=authorization(settings),
            json={"task": "check_codex", "params": {}},
        )
        health_response = await client.get("/api/health")
        status_response = await client.get(
            "/api/status",
            headers=authorization(settings),
        )
        other_task_response = await client.post(
            "/api/tasks/run",
            headers=authorization(settings),
            json={"task": "show_version", "params": {}},
        )

    assert task_response.status_code == 200
    assert task_response.json()["data"]["status"] == "failed"
    assert health_response.status_code == 200
    assert status_response.status_code == 200
    assert other_task_response.status_code == 200
    assert other_task_response.json()["data"]["status"] == "success"


@pytest.mark.anyio
async def test_tasks_require_authentication(settings: Settings) -> None:
    app = create_app(settings)
    transport = httpx.ASGITransport(app=app)

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        list_response = await client.get("/api/tasks")
        run_response = await client.post(
            "/api/tasks/run",
            json={"task": "show_version", "params": {}},
        )

    assert list_response.status_code == 401
    assert run_response.status_code == 401
