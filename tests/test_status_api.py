from __future__ import annotations

from unittest.mock import patch

import httpx
import pytest
from pydantic import SecretStr

from app.application import create_app
from app.core.config import Settings


@pytest.mark.anyio
async def test_status_accepts_valid_bearer_token(settings: Settings) -> None:
    transport = httpx.ASGITransport(app=create_app(settings))
    token = settings.security.token
    assert token is not None

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(
            "/api/status",
            headers={"Authorization": f"Bearer {token.get_secret_value()}"},
        )

    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    assert body["data"]["node"]["id"] == settings.node.id
    assert body["data"]["node"]["detected_platform"]
    assert body["data"]["system"]["uptime_seconds"] >= 0


@pytest.mark.anyio
async def test_status_requires_authentication(settings: Settings) -> None:
    transport = httpx.ASGITransport(app=create_app(settings))

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/api/status")

    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"
    assert response.json() == {
        "success": False,
        "error": {
            "code": "authentication_required",
            "message": "Bearer authentication is required",
        },
    }


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("authorization", "code"),
    [
        ("Bearer wrong-token", "invalid_credentials"),
        ("Basic credentials", "invalid_credentials"),
    ],
)
async def test_status_rejects_invalid_credentials(
    settings: Settings, authorization: str, code: str
) -> None:
    transport = httpx.ASGITransport(app=create_app(settings))

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(
            "/api/status",
            headers={"Authorization": authorization},
        )

    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"
    assert response.json()["error"]["code"] == code


@pytest.mark.anyio
async def test_status_is_unavailable_when_server_token_is_missing(
    settings: Settings,
) -> None:
    settings.security.token = None
    transport = httpx.ASGITransport(app=create_app(settings))

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(
            "/api/status",
            headers={"Authorization": "Bearer any-token"},
        )

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "security_not_configured"


@pytest.mark.anyio
async def test_authentication_does_not_log_token(
    settings: Settings,
    capsys: pytest.CaptureFixture[str],
) -> None:
    secret = "unique-secret-token-that-must-never-appear"
    settings.security.token = SecretStr(secret)
    transport = httpx.ASGITransport(app=create_app(settings))

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(
            "/api/status",
            headers={"Authorization": f"Bearer {secret}-wrong"},
        )

    assert response.status_code == 401
    assert secret not in capsys.readouterr().err
    assert secret not in settings.logs.file.read_text(encoding="utf-8")


@pytest.mark.anyio
async def test_status_uses_constant_time_token_comparison(
    settings: Settings,
) -> None:
    token = settings.security.token
    assert token is not None
    transport = httpx.ASGITransport(app=create_app(settings))

    with patch(
        "app.core.security.secrets.compare_digest",
        return_value=True,
    ) as compare:
        async with httpx.AsyncClient(
            transport=transport, base_url="http://test"
        ) as client:
            response = await client.get(
                "/api/status",
                headers={"Authorization": "Bearer supplied-token"},
            )

    assert response.status_code == 200
    compare.assert_called_once_with(
        "supplied-token",
        token.get_secret_value(),
    )


@pytest.mark.anyio
async def test_status_returns_controlled_error_when_collection_fails(
    settings: Settings,
) -> None:
    transport = httpx.ASGITransport(app=create_app(settings))
    token = settings.security.token
    assert token is not None

    with patch(
        "app.api.status.collect_system_status",
        side_effect=RuntimeError("sensitive internal path"),
    ):
        async with httpx.AsyncClient(
            transport=transport, base_url="http://test"
        ) as client:
            response = await client.get(
                "/api/status",
                headers={"Authorization": f"Bearer {token.get_secret_value()}"},
            )

    assert response.status_code == 503
    assert response.json() == {
        "success": False,
        "error": {
            "code": "status_unavailable",
            "message": "System status is temporarily unavailable",
        },
    }
    assert "sensitive internal path" not in response.text


@pytest.mark.anyio
async def test_framework_errors_use_the_common_error_shape(
    settings: Settings,
) -> None:
    transport = httpx.ASGITransport(app=create_app(settings))

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/api/missing")

    assert response.status_code == 404
    assert response.json() == {
        "success": False,
        "error": {
            "code": "not_found",
            "message": "Resource not found",
        },
    }
