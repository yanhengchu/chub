import httpx
import pytest

from app.application import create_app


@pytest.mark.anyio
async def test_global_internal_session_visibility_setting_defaults_and_persists(settings) -> None:
    app = create_app(settings)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        initial = await client.get("/api/settings/internal-session-visibility")
        updated = await client.put(
            "/api/settings/internal-session-visibility",
            json={"show_internal_sessions": True},
        )
        restored = await client.get("/api/settings/internal-session-visibility")

    assert initial.status_code == 200
    assert initial.json()["data"] == {"show_internal_sessions": False}
    assert updated.status_code == 200
    assert updated.json()["data"] == {"show_internal_sessions": True}
    assert restored.json()["data"] == {"show_internal_sessions": True}


@pytest.mark.anyio
async def test_session_settings_show_one_visibility_switch_under_session_defaults(settings) -> None:
    app = create_app(settings)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        imported = await client.post(
            "/api/plugins/runtime/imports",
            json={"artifact_id": "development:codex-runtime-dev"},
        )
        response = await client.get("/settings/session")

    assert imported.status_code == 200
    assert response.status_code == 200
    assert 'id="session-defaults-title">新会话默认配置</h3>' in response.text
    assert 'id="show-internal-sessions"' in response.text
    assert 'id="internal-session-visibility-feedback"' in response.text
    assert "id=\"internal-session-visibility-title\"" not in response.text
    assert "show-collaboration-sessions" not in response.text
    assert "today-focus-show-sessions" not in response.text
    assert "show-release-note-session" not in response.text
    assert 'src="/static/js/features/workspace-session-visibility.js"' in response.text
