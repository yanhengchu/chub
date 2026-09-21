import asyncio
from threading import Event

import httpx
import pytest

from app.application import create_app
from modules.business.deliveryline.store import DeliverylineUnavailable


@pytest.mark.anyio
async def test_session_settings_groups_defaults_and_internal_visibility_for_imported_modules(settings) -> None:
    app = create_app(settings)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        await client.post(
            "/api/plugins/runtime/imports",
            json={"artifact_id": "development:codex-runtime-dev"},
        )
        await client.post(
            "/api/plugins/deliveryline/imports",
            json={"artifact_id": "development:deliveryline"},
        )
        response = await client.get("/settings/session")
        shown = await client.put(
            "/api/settings/internal-session-visibility",
            json={"show_sessions": True},
        )
        hidden = await client.put(
            "/api/settings/internal-session-visibility",
            json={"show_sessions": False},
        )

    assert response.status_code == 200
    assert 'id="session-defaults-title">新会话默认配置</h3>' in response.text
    assert 'id="internal-session-visibility-title">内部会话显示</h3>' in response.text
    assert 'class="internal-session-visibility-copy"><h3 id="internal-session-visibility-title">内部会话显示</h3><p class="settings-subsection-description">选择是否在工作台中显示模块专用的内部会话；不会影响正在执行的任务。</p></div><button id="internal-session-visibility-toggle" class="button-secondary" type="button" disabled>展示</button>' in response.text
    assert 'id="internal-session-visibility-feedback" class="message"' in response.text
    assert 'id="deliveryline-show-collaboration-sessions"' in response.text
    assert 'id="today-focus-show-sessions"' in response.text
    assert 'data-session-visibility-feedback="deliveryline"' in response.text
    assert 'data-session-visibility-feedback="today-focus"' in response.text
    assert 'id="internal-session-visibility-message"' not in response.text
    assert 'src="/static/js/features/workspace-session-visibility.js"' in response.text
    assert shown.json()["data"] == {
        "deliveryline": True,
        "today_focus": True,
        "deployment_package": True,
    }
    assert hidden.json()["data"] == {
        "deliveryline": False,
        "today_focus": False,
        "deployment_package": False,
    }


@pytest.mark.anyio
async def test_internal_session_visibility_restores_prior_values_when_a_write_fails(
    settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = create_app(settings)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        for plugin_id in ("deliveryline",):
            imported = await client.post(
                f"/api/plugins/{plugin_id}/imports",
                json={"artifact_id": f"development:{plugin_id}"},
            )
            assert imported.status_code == 200
        initial = await client.put(
            "/api/settings/internal-session-visibility",
            json={"show_sessions": False},
        )
        assert initial.status_code == 200

        def fail_today_focus_write(_show_sessions: bool) -> bool:
            raise OSError("today focus state is unavailable")

        monkeypatch.setattr(
            app.state.ai_search,
            "set_show_sessions",
            fail_today_focus_write,
        )
        failed = await client.put(
            "/api/settings/internal-session-visibility",
            json={"show_sessions": True},
        )

    assert failed.status_code == 503
    assert failed.json()["error"]["code"] == "internal_session_visibility_update_failed"
    assert app.state.deliveryline_collaboration.show_sessions() is False
    assert app.state.deployment_package.show_release_note_session() is False


@pytest.mark.anyio
async def test_internal_session_visibility_reports_deliveryline_state_failure_before_writing(
    settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = create_app(settings)
    transport = httpx.ASGITransport(app=app)

    def fail_deliveryline_state_read() -> bool:
        raise DeliverylineUnavailable("local state unavailable")

    monkeypatch.setattr(
        app.state.deliveryline_collaboration,
        "show_sessions",
        fail_deliveryline_state_read,
    )
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        imported = await client.post(
            "/api/plugins/deliveryline/imports",
            json={"artifact_id": "development:deliveryline"},
        )
        assert imported.status_code == 200
        failed = await client.put(
            "/api/settings/internal-session-visibility",
            json={"show_sessions": True},
        )

    assert failed.status_code == 503
    assert failed.json()["error"]["code"] == "internal_session_visibility_unavailable"
    assert app.state.ai_search.show_sessions() is False
    assert app.state.deployment_package.show_release_note_session() is False


@pytest.mark.anyio
async def test_internal_session_visibility_reports_deliveryline_write_failure_without_writing_others(
    settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = create_app(settings)
    transport = httpx.ASGITransport(app=app)

    def fail_deliveryline_state_write(_show_sessions: bool) -> bool:
        raise DeliverylineUnavailable("local state unavailable")

    monkeypatch.setattr(
        app.state.deliveryline_collaboration,
        "set_show_sessions",
        fail_deliveryline_state_write,
    )
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        imported = await client.post(
            "/api/plugins/deliveryline/imports",
            json={"artifact_id": "development:deliveryline"},
        )
        assert imported.status_code == 200
        failed = await client.put(
            "/api/settings/internal-session-visibility",
            json={"show_sessions": True},
        )

    assert failed.status_code == 503
    assert failed.json()["error"]["code"] == "internal_session_visibility_update_failed"
    assert app.state.ai_search.show_sessions() is False
    assert app.state.deployment_package.show_release_note_session() is False


@pytest.mark.anyio
async def test_internal_session_visibility_rollback_does_not_overwrite_single_setting_update(
    settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = create_app(settings)
    transport = httpx.ASGITransport(app=app)
    entered = Event()
    release = Event()
    original_today_focus_write = app.state.ai_search.set_show_sessions

    def fail_today_focus_write(show_sessions: bool) -> bool:
        if show_sessions:
            entered.set()
            assert release.wait(timeout=2)
            raise OSError("today focus state is unavailable")
        return original_today_focus_write(show_sessions)

    monkeypatch.setattr(app.state.ai_search, "set_show_sessions", fail_today_focus_write)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        imported = await client.post(
            "/api/plugins/deliveryline/imports",
            json={"artifact_id": "development:deliveryline"},
        )
        assert imported.status_code == 200

        bulk = asyncio.create_task(client.put(
            "/api/settings/internal-session-visibility",
            json={"show_sessions": True},
        ))
        assert await asyncio.to_thread(entered.wait, 2)
        single = asyncio.create_task(client.put(
            "/api/deliveryline/settings",
            json={"show_sessions": True},
        ))
        await asyncio.sleep(0.05)
        assert not single.done()
        release.set()
        failed, updated = await asyncio.gather(bulk, single)

    assert failed.status_code == 503
    assert failed.json()["error"]["code"] == "internal_session_visibility_update_failed"
    assert updated.status_code == 200
    assert app.state.deliveryline_collaboration.show_sessions() is True
