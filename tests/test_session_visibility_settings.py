import httpx
import pytest

from app.application import create_app


@pytest.mark.anyio
async def test_session_settings_groups_defaults_and_internal_visibility_for_imported_modules(settings) -> None:
    app = create_app(settings)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        await client.post(
            "/api/plugins/codex-runtime/imports",
            json={"artifact_id": "development:codex-runtime"},
        )
        await client.post(
            "/api/plugins/weixin-orchestration/imports",
            json={"artifact_id": "development:weixin-orchestration"},
        )
        await client.post(
            "/api/plugins/deliveryline/imports",
            json={"artifact_id": "development:deliveryline"},
        )
        response = await client.get("/settings/session")

    assert response.status_code == 200
    assert 'id="session-defaults-title">新会话默认配置</h3>' in response.text
    assert 'id="internal-session-visibility-title">内部会话显示</h3>' in response.text
    assert 'id="deliveryline-show-collaboration-sessions"' in response.text
    assert 'id="workspace-task-show-internal-native-session"' in response.text
    assert 'id="today-focus-show-sessions"' in response.text
    assert 'data-session-visibility-feedback="deliveryline"' in response.text
    assert 'data-session-visibility-feedback="translation"' in response.text
    assert 'data-session-visibility-feedback="today-focus"' in response.text
    assert 'id="internal-session-visibility-message"' not in response.text
    assert 'src="/static/js/features/workspace-session-visibility.js"' in response.text
