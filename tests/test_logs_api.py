from __future__ import annotations

from unittest.mock import patch

import httpx
import pytest

from app.application import create_app
from app.core.config import Settings
from app.services.log_reader import LogReadError


def authorization(settings: Settings) -> dict[str, str]:
    token = settings.security.token
    assert token is not None
    return {"Authorization": f"Bearer {token.get_secret_value()}"}


@pytest.mark.anyio
async def test_logs_returns_requested_lines_and_redacts_tokens(
    settings: Settings,
) -> None:
    app = create_app(settings)
    token = settings.security.token
    assert token is not None
    secret = token.get_secret_value()
    settings.logs.file.write_text(
        f"first\nsecret={secret}\nAuthorization: Bearer other-secret\nlast\n",
        encoding="utf-8",
    )
    transport = httpx.ASGITransport(app=app)

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(
            "/api/logs?lines=3",
            headers=authorization(settings),
        )

    assert response.status_code == 200
    assert response.json() == {
        "success": True,
        "data": {
            "lines": [
                "secret=[REDACTED]",
                "Authorization: Bearer [REDACTED]",
                "last",
            ],
            "count": 3,
        },
    }


@pytest.mark.anyio
@pytest.mark.parametrize(
    "query",
    [
        "lines=0",
        "lines=101",
        "lines=invalid",
        "lines=1&lines=2",
    ],
)
async def test_logs_rejects_invalid_line_counts(
    settings: Settings,
    query: str,
) -> None:
    app = create_app(settings)
    transport = httpx.ASGITransport(app=app)

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(
            f"/api/logs?{query}",
            headers=authorization(settings),
        )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "invalid_log_lines"


@pytest.mark.anyio
async def test_logs_rejects_path_parameters(settings: Settings) -> None:
    app = create_app(settings)
    transport = httpx.ASGITransport(app=app)

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(
            "/api/logs?path=/etc/passwd",
            headers=authorization(settings),
        )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "invalid_request"


@pytest.mark.anyio
async def test_logs_requires_authentication(settings: Settings) -> None:
    app = create_app(settings)
    transport = httpx.ASGITransport(app=app)

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/api/logs")

    assert response.status_code == 401


@pytest.mark.anyio
async def test_log_read_failure_is_controlled_and_other_endpoints_survive(
    settings: Settings,
) -> None:
    app = create_app(settings)
    transport = httpx.ASGITransport(app=app)

    with patch("app.api.logs.tail_log", side_effect=LogReadError):
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://test",
        ) as client:
            logs_response = await client.get(
                "/api/logs",
                headers=authorization(settings),
            )
            health_response = await client.get("/api/health")
            status_response = await client.get(
                "/api/status",
                headers=authorization(settings),
            )
            tasks_response = await client.get(
                "/api/tasks",
                headers=authorization(settings),
            )

    assert logs_response.status_code == 503
    assert logs_response.json()["error"]["code"] == "logs_unavailable"
    assert health_response.status_code == 200
    assert status_response.status_code == 200
    assert tasks_response.status_code == 200
