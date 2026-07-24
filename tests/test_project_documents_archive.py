import json
import stat
from unittest.mock import MagicMock

import httpx
import pytest

from app.application import create_app
from app.core.config import Settings


TOKEN_HEADERS = {
    "Authorization": "Bearer test-token-that-is-long-enough-for-tests",
}


@pytest.mark.anyio
async def test_project_document_archive_requires_authentication(
    settings: Settings,
) -> None:
    transport = httpx.ASGITransport(app=create_app(settings))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.put(
            "/api/project-docs/automation-download/archive",
            json={"archived": True},
        )

    assert response.status_code == 401
    assert not settings.project_documents.state_file.exists()


@pytest.mark.anyio
async def test_archived_document_is_hidden_from_home_api_and_kept_in_full_list(
    settings: Settings,
) -> None:
    transport = httpx.ASGITransport(app=create_app(settings))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        archived = await client.put(
            "/api/project-docs/automation-download/archive",
            headers=TOKEN_HEADERS,
            json={"archived": True},
        )
        home_api = await client.get("/api/project-docs", headers=TOKEN_HEADERS)
        home_page = await client.get("/")
        full_list = await client.get("/project-docs")
        detail = await client.get("/project-docs/automation-download")

    assert archived.status_code == 200
    assert archived.json()["data"]["archived"] is True
    assert home_api.json()["data"]["count"] == 1
    assert {
        document["id"] for document in home_api.json()["data"]["documents"]
    } == {"openclaw-research"}
    assert 'href="/project-docs/automation-download"' not in home_page.text
    assert 'data-archived="true"' in full_list.text
    assert "已归档" in full_list.text
    assert detail.status_code == 200

    state = json.loads(
        settings.project_documents.state_file.read_text(encoding="utf-8")
    )
    assert state == {"archived_document_ids": ["automation-download"]}
    assert stat.S_IMODE(settings.project_documents.state_file.stat().st_mode) == 0o600


@pytest.mark.anyio
async def test_archived_document_can_be_restored(settings: Settings) -> None:
    transport = httpx.ASGITransport(app=create_app(settings))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        await client.put(
            "/api/project-docs/automation-download/archive",
            headers=TOKEN_HEADERS,
            json={"archived": True},
        )
        restored = await client.put(
            "/api/project-docs/automation-download/archive",
            headers=TOKEN_HEADERS,
            json={"archived": False},
        )
        home_api = await client.get("/api/project-docs", headers=TOKEN_HEADERS)

    assert restored.status_code == 200
    assert restored.json()["data"]["archived"] is False
    assert home_api.json()["data"]["count"] == 2
    assert {
        document["id"] for document in home_api.json()["data"]["documents"]
    } == {"automation-download", "openclaw-research"}


@pytest.mark.anyio
async def test_archive_operation_logs_full_lifecycle(
    settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    log_operation = MagicMock(return_value="operation-id")
    monkeypatch.setattr(
        "app.api.project_documents.log_operation",
        log_operation,
    )
    transport = httpx.ASGITransport(app=create_app(settings))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.put(
            "/api/project-docs/automation-download/archive",
            headers=TOKEN_HEADERS,
            json={"archived": True},
        )

    assert response.status_code == 200
    assert [call.kwargs["status"] for call in log_operation.call_args_list] == [
        "requested",
        "started",
        "succeeded",
    ]


@pytest.mark.anyio
async def test_unknown_document_cannot_create_archive_state(
    settings: Settings,
) -> None:
    transport = httpx.ASGITransport(app=create_app(settings))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.put(
            "/api/project-docs/not-registered/archive",
            headers=TOKEN_HEADERS,
            json={"archived": True},
        )

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "project_document_not_found"
    assert not settings.project_documents.state_file.exists()


@pytest.mark.anyio
async def test_document_list_updates_archive_state_without_page_reload(
    settings: Settings,
) -> None:
    transport = httpx.ASGITransport(app=create_app(settings))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        script = await client.get("/static/design_documents.js")

    assert script.status_code == 200
    assert "window.location.reload()" not in script.text
    assert 'card.dataset.archived = String(payload.data.archived)' in script.text
    assert "button.disabled = false" in script.text
    assert "applyFilter(activeFilter)" in script.text
    assert 'sessionStorage.setItem(PROJECT_DOCS_REFRESH_KEY, "1")' in script.text
