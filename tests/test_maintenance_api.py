from unittest.mock import patch

import httpx
import pytest

from app.application import create_app
from app.core.config import Settings


@pytest.mark.anyio
async def test_restart_requires_authentication(settings: Settings) -> None:
    transport = httpx.ASGITransport(app=create_app(settings))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post("/api/maintenance/restart")

    assert response.status_code == 401


@pytest.mark.anyio
async def test_restart_uses_chub_service_command(settings: Settings) -> None:
    transport = httpx.ASGITransport(app=create_app(settings))
    with (
        patch("app.api.maintenance.PROJECT_ROOT") as project_root,
        patch("app.api.maintenance.subprocess.Popen") as popen,
    ):
        command = project_root / "scripts" / "chub-web-restart"
        command.is_file.return_value = True
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://test",
            headers={"Authorization": "Bearer test-token-that-is-long-enough-for-tests"},
        ) as client:
            response = await client.post("/api/maintenance/restart")

    assert response.status_code == 200
    assert response.json()["data"] == {"status": "restarting"}
    assert popen.call_args.args[0] == [str(command)]
    assert popen.call_args.kwargs["start_new_session"] is True
