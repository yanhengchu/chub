import httpx
import pytest

from app.application import create_app
from app.core.config import Settings


@pytest.mark.anyio
async def test_health_is_public(settings: Settings) -> None:
    transport = httpx.ASGITransport(app=create_app(settings))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/api/health")

    assert response.status_code == 200
    assert response.json() == {
        "success": True,
        "data": {
            "service": "hub",
            "status": "ok",
            "version": "0.1.0",
        },
    }


def test_missing_token_logs_warning(
    settings: Settings, capsys: pytest.CaptureFixture[str]
) -> None:
    settings.security.token = None

    create_app(settings)

    assert "HUB_TOKEN is not set" in capsys.readouterr().err
