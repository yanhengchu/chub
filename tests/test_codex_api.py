from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock, call

import httpx
import pytest
from pydantic import ValidationError

from app.application import create_app
from app.codex.models import (
    CodexModelCatalogData,
    CodexModelInfo,
    CodexReasoningLevel,
    CodexQuotaData,
    CodexQuotaWindow,
    CodexSession,
    QuickInteractionTask,
    RuntimeManagementData,
    RuntimeManagementItem,
    SessionInfo,
    SessionListData,
    SessionRenameRequest,
    WorkspaceInfo,
    utc_now,
)
from app.core.config import Settings
from app.core.response import ApiError


def authorization(settings: Settings) -> dict[str, str]:
    return {}


def allow_session_writes(app) -> None:
    app.state.quick_interactions._recovery_ready = True


def reject_quick_access(manager: MagicMock) -> None:
    manager.require_quick_access.side_effect = ApiError(
        409,
        "codex_quick_access_disabled",
        "实时终端 Session 仅支持实时终端入口。",
    )


def test_session_rename_request_normalizes_title_and_rejects_controls() -> None:
    assert SessionRenameRequest(title="  第一行\n 第二行\t ").title == "第一行 第二行"

    with pytest.raises(ValidationError):
        SessionRenameRequest(title="标题\x00内容")
    with pytest.raises(ValidationError):
        SessionRenameRequest(title="标" * 49)


@pytest.mark.anyio
async def test_codex_sessions_allow_loopback(settings: Settings) -> None:
    transport = httpx.ASGITransport(app=create_app(settings))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/api/codex/sessions")

    assert response.status_code == 200


@pytest.mark.anyio
async def test_codex_session_list_reports_workspaces(settings: Settings) -> None:
    app = create_app(settings)
    manager = MagicMock()
    manager.submission_available.return_value = (
        False,
        "Codex PTY requires Tailscale",
    )
    manager.dependencies.return_value = {"codex": True, "ttyd": True, "tmux": False}
    manager.workspaces.return_value = [
        WorkspaceInfo(id="home", name="用户目录", path="/home/test", available=True)
    ]
    manager.list_sessions.return_value = []
    app.state.codex_pty_manager = manager
    transport = httpx.ASGITransport(app=app)

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(
            "/api/codex/sessions",
            headers=authorization(settings),
        )

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["available"] is False
    assert data["terminal_creation"] == {
        "available": False,
        "reason": "Codex PTY requires Tailscale",
    }
    assert data["quick_creation"] == {
        "available": False,
        "reason": "Codex PTY requires Tailscale",
    }
    assert data["workspaces"][0]["id"] == "home"
    assert data["dependencies"]["tmux"] is False


@pytest.mark.anyio
async def test_codex_session_list_keeps_terminal_creation_available_without_worker(
    settings: Settings,
) -> None:
    app = create_app(settings)
    manager = MagicMock()
    manager.submission_available.return_value = (True, None)
    manager.dependencies.return_value = {"codex": True, "ttyd": True, "tmux": True}
    manager.workspaces.return_value = []
    manager.list_sessions.return_value = []
    quick_interactions = MagicMock()
    quick_interactions.active_sessions.return_value = {}
    quick_interactions.quick_session_creation_availability.return_value = (
        False,
        "Quick Worker 当前不可用，无法创建快速交互 Session。",
    )
    app.state.codex_pty_manager = manager
    app.state.quick_interactions = quick_interactions
    app.state.weixin_chub_mode = MagicMock()
    app.state.weixin_chub_mode.session_slots_snapshot.return_value = {}
    transport = httpx.ASGITransport(app=app)

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(
            "/api/codex/sessions",
            headers=authorization(settings),
        )

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["available"] is True
    assert data["terminal_creation"] == {"available": True, "reason": None}
    assert data["quick_creation"] == {
        "available": False,
        "reason": "Quick Worker 当前不可用，无法创建快速交互 Session。",
    }


@pytest.mark.anyio
async def test_runtime_management_lists_and_updates_enablement(settings: Settings) -> None:
    app = create_app(settings)
    manager = MagicMock()
    current = RuntimeManagementData(
        runtimes=[
            RuntimeManagementItem(
                runtime_id="codex",
                name="Codex Runtime",
                enabled=True,
                healthy=True,
            )
        ],
        basic_mode=False,
    )
    disabled = current.model_copy(
        update={
            "runtimes": [
                current.runtimes[0].model_copy(update={"enabled": False})
            ],
            "basic_mode": True,
        }
    )
    manager.read_runtime_management.return_value = current
    manager.update_runtime_enabled.return_value = disabled
    app.state.codex_pty_manager = manager
    transport = httpx.ASGITransport(app=app)

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        listed = await client.get("/api/codex/runtimes", headers=authorization(settings))
        updated = await client.put(
            "/api/codex/runtimes/codex",
            headers=authorization(settings),
            json={"enabled": False},
        )

    assert listed.status_code == 200
    assert listed.json()["data"]["runtimes"][0]["enabled"] is True
    assert updated.status_code == 200
    assert updated.json()["data"]["basic_mode"] is True
    manager.update_runtime_enabled.assert_called_once_with("codex", False)


@pytest.mark.anyio
async def test_codex_session_list_hides_internal_translation_session(
    settings: Settings,
) -> None:
    app = create_app(settings)
    manager = MagicMock()
    manager.submission_available.return_value = (True, None)
    manager.dependencies.return_value = {}
    manager.workspaces.return_value = []
    manager.list_sessions.return_value = [
        SessionInfo(
            id="ordinary-session",
            runtime_id="codex",
            workspace_id="chub",
            workspace_name="Chub",
            cwd="/workspace/chub",
            title=None,
            can_archive=True,
            status="stopped",
            activity="idle",
            permission_mode="full-access",
            active_permission_mode=None,
            permission_pending=False,
            error=None,
            created_at="2026-08-14T10:00:00Z",
            updated_at="2026-08-14T10:00:00Z",
            session_mode="terminal",
        ),
        SessionInfo(
            id="translation-session",
            runtime_id="codex",
            workspace_id="weixin-translation",
            workspace_name="微信文本优化与翻译",
            cwd="/runtime/translation",
            title="文本优化与翻译",
            can_archive=True,
            status="stopped",
            activity="idle",
            permission_mode="read-only",
            active_permission_mode=None,
            permission_pending=False,
            error=None,
            created_at="2026-08-14T11:00:00Z",
            updated_at="2026-08-14T11:00:00Z",
            session_mode="quick",
        ),
    ]
    manager.read_session.return_value = manager.list_sessions.return_value[1]
    app.state.codex_pty_manager = manager
    transport = httpx.ASGITransport(app=app)

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        hidden = await client.get(
            "/api/codex/sessions",
            headers=authorization(settings),
        )
        visible = await client.get(
            "/api/codex/sessions?include_translation=true",
            headers=authorization(settings),
        )
        detail = await client.get(
            "/api/codex/sessions/translation-session",
            headers=authorization(settings),
        )

    assert hidden.status_code == 200
    assert [item["id"] for item in hidden.json()["data"]["sessions"]] == [
        "ordinary-session"
    ]
    assert [item["id"] for item in visible.json()["data"]["sessions"]] == [
        "ordinary-session"
    ]
    assert detail.status_code == 200
    assert detail.json()["data"]["id"] == "translation-session"


@pytest.mark.anyio
async def test_create_session_uses_requested_permission_mode(settings: Settings) -> None:
    app = create_app(settings)
    allow_session_writes(app)
    manager = MagicMock()
    manager.create_session.return_value = SessionInfo(
        id="session-1",
        runtime_id="codex",
        workspace_id="chub",
        workspace_name="Chub",
        cwd="/workspace/chub",
        title=None,
        can_archive=False,
        status="new",
        activity="unknown",
        permission_mode="full-access",
        active_permission_mode=None,
        permission_pending=False,
        error=None,
        created_at="2026-08-07T10:00:00Z",
        updated_at="2026-08-07T10:00:00Z",
    )
    app.state.codex_pty_manager = manager
    transport = httpx.ASGITransport(app=app)

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/api/codex/sessions",
            headers=authorization(settings),
            json={
                "workspace_id": "chub",
                "session_mode": "terminal",
                "permission_mode": "full-access",
            },
        )

    assert response.status_code == 200
    manager.create_session.assert_called_once_with(
        "chub", "full-access", None, None, "terminal"
    )


@pytest.mark.anyio
async def test_create_session_defaults_to_full_access(settings: Settings) -> None:
    app = create_app(settings)
    allow_session_writes(app)
    manager = MagicMock()
    manager.create_session.return_value = SessionInfo(
        id="session-1",
        runtime_id="codex",
        workspace_id="chub",
        workspace_name="Chub",
        cwd="/workspace/chub",
        title=None,
        can_archive=False,
        status="new",
        activity="unknown",
        permission_mode="full-access",
        active_permission_mode=None,
        permission_pending=False,
        error=None,
        created_at="2026-08-07T10:00:00Z",
        updated_at="2026-08-07T10:00:00Z",
    )
    app.state.codex_pty_manager = manager
    transport = httpx.ASGITransport(app=app)

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/api/codex/sessions",
            headers=authorization(settings),
            json={"workspace_id": "chub", "session_mode": "terminal"},
        )

    assert response.status_code == 200
    manager.create_session.assert_called_once_with("chub", None, None, None, "terminal")


@pytest.mark.anyio
async def test_codex_model_catalog_is_protected_and_filtered_by_manager(
    settings: Settings,
) -> None:
    app = create_app(settings)
    manager = MagicMock()
    manager.read_model_catalog.return_value = CodexModelCatalogData(
        models=[
            CodexModelInfo(
                id="gpt-test",
                name="GPT Test",
                description="Test model",
                default_level="medium",
                levels=[CodexReasoningLevel(id="medium", description="Balanced")],
            )
        ],
        default_model="gpt-test",
        default_reasoning_effort="medium",
    )
    app.state.codex_pty_manager = manager
    transport = httpx.ASGITransport(app=app)

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(
            "/api/codex/models",
            headers=authorization(settings),
        )

    assert response.status_code == 200
    assert response.json()["data"]["models"][0]["id"] == "gpt-test"
    assert response.json()["data"]["default_model"] == "gpt-test"
    assert response.json()["data"]["default_reasoning_effort"] == "medium"


@pytest.mark.anyio
async def test_update_codex_session_defaults_uses_manager_and_returns_permission(
    settings: Settings,
) -> None:
    app = create_app(settings)
    manager = MagicMock()
    manager.update_session_defaults.return_value = "read-only"
    app.state.codex_pty_manager = manager
    transport = httpx.ASGITransport(app=app)

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.put(
            "/api/codex/session-defaults",
            headers=authorization(settings),
            json={"permission_mode": "read-only"},
        )

    assert response.status_code == 200
    assert response.json()["data"]["permission_mode"] == "read-only"
    manager.update_session_defaults.assert_called_once_with(
        "read-only",
    )


@pytest.mark.anyio
async def test_update_quick_session_configuration_uses_session_values(
    settings: Settings,
) -> None:
    app = create_app(settings)
    quick_interactions = MagicMock()
    quick_interactions.update_session_configuration.return_value = SessionInfo(
        id="session-1",
        runtime_id="codex",
        workspace_id="chub",
        workspace_name="Chub",
        cwd="/workspace/chub",
        title=None,
        can_archive=True,
        status="stopped",
        activity="idle",
        permission_mode="full-access",
        active_permission_mode=None,
        permission_pending=False,
        model="gpt-test",
        reasoning_effort="high",
        error=None,
        created_at="2026-08-07T10:00:00Z",
        updated_at="2026-08-07T10:00:00Z",
    )
    app.state.quick_interactions = quick_interactions
    transport = httpx.ASGITransport(app=app)

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.patch(
            "/api/codex/sessions/session-1/configuration",
            headers=authorization(settings),
            json={
                "permission_mode": "full-access",
                "model": "gpt-test",
                "reasoning_effort": "high",
            },
        )

    assert response.status_code == 200
    quick_interactions.update_session_configuration.assert_called_once_with(
        "session-1",
        "full-access",
        "gpt-test",
        "high",
    )


@pytest.mark.anyio
async def test_create_session_uses_requested_model_and_reasoning_level(
    settings: Settings,
) -> None:
    app = create_app(settings)
    allow_session_writes(app)
    manager = MagicMock()
    manager.create_session.return_value = SessionInfo(
        id="session-1",
        runtime_id="codex",
        workspace_id="chub",
        workspace_name="Chub",
        cwd="/workspace/chub",
        title=None,
        can_archive=False,
        status="new",
        activity="unknown",
        permission_mode="full-access",
        active_permission_mode=None,
        permission_pending=False,
        model="gpt-test",
        reasoning_effort="high",
        error=None,
        created_at="2026-08-07T10:00:00Z",
        updated_at="2026-08-07T10:00:00Z",
    )
    app.state.codex_pty_manager = manager
    transport = httpx.ASGITransport(app=app)

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/api/codex/sessions",
            headers=authorization(settings),
            json={
                "workspace_id": "chub",
                "session_mode": "terminal",
                "permission_mode": "full-access",
                "model": "gpt-test",
                "reasoning_effort": "high",
            },
        )

    assert response.status_code == 200
    manager.create_session.assert_called_once_with(
        "chub",
        "full-access",
        "gpt-test",
        "high",
        "terminal",
    )


@pytest.mark.anyio
async def test_codex_quota_is_protected_and_can_be_refreshed(settings: Settings) -> None:
    app = create_app(settings)
    rate_limits = MagicMock()
    rate_limits.read.return_value = CodexQuotaData(
        status="available",
        windows=[
            CodexQuotaWindow(
                remaining_percent=75,
                window_duration_minutes=15,
                resets_at="2026-08-06T10:15:00Z",
            )
        ],
    )
    app.state.codex_rate_limits = rate_limits
    transport = httpx.ASGITransport(app=app)

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(
            "/api/codex/quota?refresh=true",
            headers=authorization(settings),
        )

    assert response.status_code == 200
    assert response.json()["data"]["windows"][0]["remaining_percent"] == 75
    rate_limits.read.assert_called_once_with(force=True)


@pytest.mark.anyio
async def test_codex_session_list_includes_active_quick_interaction(
    settings: Settings,
) -> None:
    app = create_app(settings)
    manager = MagicMock()
    manager.submission_available.return_value = (True, None)
    manager.dependencies.return_value = {"codex": True, "ttyd": True, "tmux": True}
    manager.workspaces.return_value = []
    manager.list_sessions.return_value = [
        SessionInfo(
            id="session-1",
            runtime_id="codex",
            workspace_id="chub",
            workspace_name="Chub",
            cwd="/workspace/chub",
            title=None,
            can_archive=True,
            status="stopped",
            activity="idle",
            permission_mode="auto-review",
            active_permission_mode=None,
            permission_pending=False,
            error=None,
            created_at="2026-07-24T10:00:00Z",
            updated_at="2026-07-24T10:01:00Z",
        )
    ]
    quick_interactions = MagicMock()
    quick_interactions.active_sessions.return_value = {
        "session-1": datetime(2026, 7, 24, 10, 2, tzinfo=UTC)
    }
    quick_interactions.quick_session_creation_availability.return_value = (True, None)
    app.state.codex_pty_manager = manager
    app.state.quick_interactions = quick_interactions
    weixin_chub_mode = MagicMock()
    weixin_chub_mode.session_slots_snapshot.return_value = {"session-1": 3}
    app.state.weixin_chub_mode = weixin_chub_mode
    transport = httpx.ASGITransport(app=app)

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(
            "/api/codex/sessions",
            headers=authorization(settings),
        )

    session = response.json()["data"]["sessions"][0]
    assert session["quick_interaction_running"] is True
    assert session["quick_interaction_updated_at"] == "2026-07-24T10:02:00Z"
    assert session["weixin_session_slot"] == 3
    weixin_chub_mode.session_slots_snapshot.assert_called_once_with()


def quick_task() -> QuickInteractionTask:
    return QuickInteractionTask(
        id="task-1",
        session_id="session-1",
        prompt="检查状态",
        status="requested",
        created_at=utc_now(),
        updated_at=utc_now(),
    )


@pytest.mark.anyio
async def test_quick_interaction_takes_over_idle_unconnected_terminal(
    settings: Settings,
) -> None:
    app = create_app(settings)
    manager = MagicMock()
    session = CodexSession(
        id="session-1",
        workspace_id="chub",
        workspace_name="Chub",
        cwd=Path("/workspace/chub"),
        session_mode="terminal",
        codex_session_id="codex-session-1",
        status="running",
        activity="idle",
        permission_mode="auto-review",
    )
    manager.get_session.return_value = session
    reject_quick_access(manager)
    quick_interactions = MagicMock()
    quick_interactions.submit.return_value = quick_task()
    app.state.codex_pty_manager = manager
    app.state.quick_interactions = quick_interactions
    app.state.terminal_connections = MagicMock()
    app.state.terminal_connections.has_active_connection.return_value = False
    transport = httpx.ASGITransport(app=app)

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/api/codex/sessions/session-1/quick-interactions",
            headers=authorization(settings),
            json={"prompt": "检查状态"},
        )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "codex_quick_access_disabled"
    manager.stop_session.assert_not_called()
    quick_interactions.submit.assert_not_called()


@pytest.mark.anyio
async def test_page_quick_interaction_preserves_bound_weixin_session_context(
    settings: Settings,
) -> None:
    app = create_app(settings)
    manager = MagicMock()
    manager.get_session.return_value = CodexSession(
        id="session-1",
        workspace_id="chub",
        workspace_name="Chub",
        cwd=Path("/workspace/chub"),
        title="设备状态检查",
        codex_session_id="codex-session-1",
        status="running",
        activity="idle",
        permission_mode="auto-review",
    )
    quick_interactions = MagicMock()
    quick_interactions.submit.return_value = quick_task()
    lock_order: list[str] = []
    session_guard = MagicMock()
    session_guard.__enter__.side_effect = lambda: lock_order.append("session-lock")
    quick_interactions.session_operation_guard.return_value = session_guard
    app.state.codex_pty_manager = manager
    app.state.quick_interactions = quick_interactions
    app.state.weixin_chub_mode = MagicMock()
    app.state.weixin_chub_mode.session_slot.side_effect = lambda _session_id: (
        lock_order.append("slot-snapshot") or 3
    )
    transport = httpx.ASGITransport(app=app)

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/api/codex/sessions/session-1/quick-interactions",
            headers=authorization(settings),
            json={"prompt": "检查状态"},
        )

    assert response.status_code == 200
    assert lock_order == ["session-lock"]
    submitted = quick_interactions.submit.call_args
    assert submitted.args == ("session-1", "检查状态")
    assert "weixin_session_slot" not in submitted.kwargs
    assert "weixin_session_title" not in submitted.kwargs
    assert submitted.kwargs.get("notification_route") is None


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("activity", "connected", "confirm", "expected_code"),
    [
        ("working", False, False, "quick_interaction_terminal_working"),
        (
            "unknown",
            False,
            False,
            "quick_interaction_terminal_confirmation_required",
        ),
    ],
)
async def test_quick_interaction_rejects_unsafe_terminal_switch(
    settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
    activity: str,
    connected: bool,
    confirm: bool,
    expected_code: str,
) -> None:
    write_operation = MagicMock()
    monkeypatch.setattr("app.codex.routes.write_operation", write_operation)
    app = create_app(settings)
    manager = MagicMock()
    manager.get_session.return_value = CodexSession(
        id="session-1",
        workspace_id="chub",
        workspace_name="Chub",
        cwd=Path("/workspace/chub"),
        session_mode="terminal",
        codex_session_id="codex-session-1",
        status="running",
        activity=activity,
        permission_mode="auto-review",
    )
    reject_quick_access(manager)
    quick_interactions = MagicMock()
    app.state.codex_pty_manager = manager
    app.state.quick_interactions = quick_interactions
    app.state.terminal_connections = MagicMock()
    app.state.terminal_connections.has_active_connection.return_value = connected
    transport = httpx.ASGITransport(app=app)

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/api/codex/sessions/session-1/quick-interactions",
            headers=authorization(settings),
            json={
                "prompt": "检查状态",
                "confirm_stop_unknown_terminal": confirm,
            },
        )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "codex_quick_access_disabled"
    manager.stop_session.assert_not_called()
    quick_interactions.submit.assert_not_called()
    write_operation.assert_called_once()


@pytest.mark.anyio
async def test_quick_interaction_takes_over_idle_connected_terminal(
    settings: Settings,
) -> None:
    app = create_app(settings)
    manager = MagicMock()
    manager.get_session.return_value = CodexSession(
        id="session-1",
        workspace_id="chub",
        workspace_name="Chub",
        cwd=Path("/workspace/chub"),
        session_mode="terminal",
        codex_session_id="codex-session-1",
        status="running",
        activity="idle",
        permission_mode="auto-review",
    )
    reject_quick_access(manager)
    quick_interactions = MagicMock()
    quick_interactions.submit.return_value = quick_task()
    app.state.codex_pty_manager = manager
    app.state.quick_interactions = quick_interactions
    app.state.terminal_connections = MagicMock()
    app.state.terminal_connections.has_active_connection.return_value = True
    transport = httpx.ASGITransport(app=app)

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/api/codex/sessions/session-1/quick-interactions",
            headers=authorization(settings),
            json={"prompt": "检查状态"},
        )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "codex_quick_access_disabled"
    manager.stop_session.assert_not_called()
    quick_interactions.submit.assert_not_called()


@pytest.mark.anyio
async def test_quick_interaction_confirmed_unknown_terminal_is_stopped(
    settings: Settings,
) -> None:
    app = create_app(settings)
    manager = MagicMock()
    manager.get_session.return_value = CodexSession(
        id="session-1",
        workspace_id="chub",
        workspace_name="Chub",
        cwd=Path("/workspace/chub"),
        session_mode="terminal",
        codex_session_id="codex-session-1",
        status="running",
        activity="unknown",
        permission_mode="auto-review",
    )
    reject_quick_access(manager)
    quick_interactions = MagicMock()
    quick_interactions.submit.return_value = quick_task()
    app.state.codex_pty_manager = manager
    app.state.quick_interactions = quick_interactions
    app.state.terminal_connections = MagicMock()
    app.state.terminal_connections.has_active_connection.return_value = False
    transport = httpx.ASGITransport(app=app)

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/api/codex/sessions/session-1/quick-interactions",
            headers=authorization(settings),
            json={
                "prompt": "检查状态",
                "confirm_stop_unknown_terminal": True,
            },
        )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "codex_quick_access_disabled"
    manager.stop_session.assert_not_called()


@pytest.mark.anyio
async def test_quick_interaction_rejects_if_idle_terminal_starts_working_on_submit(
    settings: Settings,
) -> None:
    app = create_app(settings)
    manager = MagicMock()
    base = CodexSession(
        id="session-1",
        workspace_id="chub",
        workspace_name="Chub",
        cwd=Path("/workspace/chub"),
        session_mode="terminal",
        codex_session_id="codex-session-1",
        status="running",
        activity="idle",
        permission_mode="auto-review",
    )
    working = base.model_copy(update={"activity": "working"})
    manager.get_session.side_effect = [base, working]
    reject_quick_access(manager)
    quick_interactions = MagicMock()
    app.state.codex_pty_manager = manager
    app.state.quick_interactions = quick_interactions
    app.state.terminal_connections = MagicMock()
    app.state.terminal_connections.has_active_connection.return_value = False
    transport = httpx.ASGITransport(app=app)

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/api/codex/sessions/session-1/quick-interactions",
            headers=authorization(settings),
            json={"prompt": "检查状态"},
        )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "codex_quick_access_disabled"
    manager.stop_session.assert_not_called()
    quick_interactions.submit.assert_not_called()


@pytest.mark.anyio
async def test_quick_interaction_history_is_paginated(
    settings: Settings,
) -> None:
    app = create_app(settings)
    app.state.codex_pty_manager = MagicMock()
    app.state.codex_pty_manager.require_quick_access.return_value = MagicMock()
    tasks = [
        quick_task().model_copy(update={"id": f"task-{index}"})
        for index in range(7)
    ]
    quick_interactions = MagicMock()
    quick_interactions.list_for_session.return_value = tasks
    app.state.quick_interactions = quick_interactions
    transport = httpx.ASGITransport(app=app)

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        first = await client.get(
            "/api/codex/sessions/session-1/quick-interactions",
            headers=authorization(settings),
        )
        second = await client.get(
            "/api/codex/sessions/session-1/quick-interactions?offset=5&limit=5",
            headers=authorization(settings),
        )

    assert len(first.json()["data"]["tasks"]) == 5
    assert first.json()["data"]["total"] == 7
    assert first.json()["data"]["has_more"] is True
    assert len(second.json()["data"]["tasks"]) == 2
    assert second.json()["data"]["has_more"] is False
    assert quick_interactions.list_for_session.call_args_list == [
        call("session-1", order="task"),
        call("session-1", order="task"),
    ]


@pytest.mark.anyio
async def test_quick_interaction_history_supports_timeline_order(
    settings: Settings,
) -> None:
    app = create_app(settings)
    app.state.codex_pty_manager = MagicMock()
    app.state.codex_pty_manager.require_quick_access.return_value = MagicMock()
    quick_interactions = MagicMock()
    quick_interactions.list_for_session.return_value = [quick_task()]
    app.state.quick_interactions = quick_interactions
    transport = httpx.ASGITransport(app=app)

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(
            "/api/codex/sessions/session-1/quick-interactions?order=timeline",
            headers=authorization(settings),
        )

    assert response.status_code == 200
    quick_interactions.list_for_session.assert_called_once_with(
        "session-1",
        order="timeline",
    )


@pytest.mark.anyio
async def test_quick_interaction_timeline_cursor_is_stable_when_new_task_arrives(
    settings: Settings,
) -> None:
    app = create_app(settings)
    app.state.codex_pty_manager = MagicMock()
    app.state.codex_pty_manager.require_quick_access.return_value = MagicMock()
    base = utc_now()

    def timeline_task(task_id: str, minutes: int) -> QuickInteractionTask:
        return quick_task().model_copy(
            update={
                "id": task_id,
                "created_at": base + timedelta(minutes=minutes),
                "updated_at": base + timedelta(minutes=minutes),
            }
        )

    latest = timeline_task("latest", 3)
    cursor_task = timeline_task("cursor", 2)
    older = timeline_task("older", 1)
    newly_arrived = timeline_task("newly-arrived", 4)
    quick_interactions = MagicMock()
    quick_interactions.list_for_session.side_effect = [
        [latest, cursor_task, older],
        [newly_arrived, latest, cursor_task, older],
    ]
    app.state.quick_interactions = quick_interactions
    transport = httpx.ASGITransport(app=app)

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        first = await client.get(
            "/api/codex/sessions/session-1/quick-interactions",
            params={"order": "timeline", "limit": 2},
            headers=authorization(settings),
        )
        second = await client.get(
            "/api/codex/sessions/session-1/quick-interactions",
            params={
                "order": "timeline",
                "limit": 2,
                "before_created_at": cursor_task.created_at.isoformat(),
                "before_id": cursor_task.id,
            },
            headers=authorization(settings),
        )

    assert [task["id"] for task in first.json()["data"]["tasks"]] == [
        "latest",
        "cursor",
    ]
    assert [task["id"] for task in second.json()["data"]["tasks"]] == ["older"]
    assert second.json()["data"]["has_more"] is False
    assert second.json()["data"]["total"] == 4


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("params", "message"),
    [
        (
            {"order": "timeline", "before_id": "task-1"},
            "时间线游标必须同时包含创建时间和任务 ID。",
        ),
        (
            {
                "order": "timeline",
                "before_created_at": "2026-08-01T10:00:00",
                "before_id": "task-1",
            },
            "时间线游标的创建时间必须包含时区。",
        ),
        (
            {
                "order": "task",
                "before_created_at": "2026-08-01T10:00:00Z",
                "before_id": "task-1",
            },
            "时间线游标只能用于 timeline 排序。",
        ),
        (
            {
                "order": "timeline",
                "offset": 1,
                "before_created_at": "2026-08-01T10:00:00Z",
                "before_id": "task-1",
            },
            "timeline 排序必须使用时间线游标，不能使用非零 offset。",
        ),
        (
            {"order": "timeline", "offset": 1},
            "timeline 排序必须使用时间线游标，不能使用非零 offset。",
        ),
    ],
)
async def test_quick_interaction_timeline_rejects_invalid_cursor(
    settings: Settings,
    params: dict[str, object],
    message: str,
) -> None:
    app = create_app(settings)
    app.state.codex_pty_manager = MagicMock()
    app.state.codex_pty_manager.require_quick_access.return_value = MagicMock()
    quick_interactions = MagicMock()
    app.state.quick_interactions = quick_interactions
    transport = httpx.ASGITransport(app=app)

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(
            "/api/codex/sessions/session-1/quick-interactions",
            params=params,
            headers=authorization(settings),
        )

    assert response.status_code == 422
    assert response.json()["error"] == {
        "code": "invalid_quick_interaction_cursor",
        "message": message,
        "source": "chub",
    }
    quick_interactions.list_for_session.assert_not_called()


@pytest.mark.anyio
async def test_quick_interaction_pin_endpoint_is_removed(settings: Settings) -> None:
    app = create_app(settings)
    transport = httpx.ASGITransport(app=app)

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.patch(
            "/api/codex/sessions/session-1/quick-interactions/task-1/pin",
            headers=authorization(settings),
            json={"pinned": True},
        )

    assert response.status_code == 404


@pytest.mark.anyio
async def test_access_issues_scoped_http_only_cookie(settings: Settings) -> None:
    app = create_app(settings)
    allow_session_writes(app)
    manager = MagicMock()
    manager.ensure_terminal.return_value = CodexSession(
        id="session-1",
        workspace_id="chub",
        workspace_name="Chub",
        cwd=Path("/workspace/chub"),
    )
    app.state.codex_pty_manager = manager
    transport = httpx.ASGITransport(app=app)

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/api/codex/sessions/session-1/access",
            headers=authorization(settings),
        )

    assert response.status_code == 200
    assert response.json()["data"]["terminal_url"] == "/codex/session-1"
    cookie = response.headers["set-cookie"]
    assert "HttpOnly" in cookie
    assert "SameSite=strict" in cookie
    assert "Path=/codex/session-1" in cookie


@pytest.mark.anyio
async def test_access_revokes_old_session_tickets_before_issuing_new_one(
    settings: Settings,
) -> None:
    app = create_app(settings)
    allow_session_writes(app)
    manager = MagicMock()
    tickets = MagicMock()
    tickets.ttl_seconds = 600
    tickets.issue.return_value = "new-ticket"
    app.state.codex_pty_manager = manager
    app.state.terminal_tickets = tickets
    transport = httpx.ASGITransport(app=app)

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/api/codex/sessions/session-1/access",
            headers=authorization(settings),
        )

    assert response.status_code == 200
    tickets.revoke_session.assert_called_once_with("session-1")
    tickets.issue.assert_called_once_with("session-1")


@pytest.mark.anyio
async def test_access_rejects_running_quick_interaction(settings: Settings) -> None:
    app = create_app(settings)
    manager = MagicMock()
    quick_interactions = MagicMock()
    guard = MagicMock()
    guard.__enter__.side_effect = ApiError(
        409,
        "quick_interaction_in_progress",
        "该会话正在执行快速交互，请等待任务结束。",
    )
    quick_interactions.terminal_access_guard.return_value = guard
    app.state.codex_pty_manager = manager
    app.state.quick_interactions = quick_interactions
    transport = httpx.ASGITransport(app=app)

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/api/codex/sessions/session-1/access",
            headers=authorization(settings),
        )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "quick_interaction_in_progress"
    manager.ensure_terminal.assert_not_called()


@pytest.mark.anyio
async def test_stop_cancels_running_quick_interaction_before_session(
    settings: Settings,
) -> None:
    app = create_app(settings)
    manager = MagicMock()
    manager.stop_session.return_value = SessionInfo(
        id="session-1",
        runtime_id="codex",
        workspace_id="chub",
        workspace_name="Chub",
        cwd="/workspace/chub",
        title=None,
        can_archive=True,
        status="stopped",
        activity="idle",
        permission_mode="auto-review",
        active_permission_mode=None,
        permission_pending=False,
        error=None,
        created_at=utc_now(),
        updated_at=utc_now(),
    )
    quick_interactions = MagicMock()
    events = []
    quick_interactions.cancel_codex_session.side_effect = lambda _id: events.append("cancel")
    manager.stop_session.side_effect = lambda _id: (
        events.append("stop") or manager.stop_session.return_value
    )
    app.state.codex_pty_manager = manager
    app.state.quick_interactions = quick_interactions
    app.state.terminal_tickets = MagicMock()
    app.state.terminal_connections = MagicMock()
    weixin_chub_mode = MagicMock()
    weixin_chub_mode.session_slot.return_value = 4
    app.state.weixin_chub_mode = weixin_chub_mode
    transport = httpx.ASGITransport(app=app)

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/api/codex/sessions/session-1/stop",
            headers=authorization(settings),
        )

    assert response.status_code == 200
    assert events == ["cancel", "stop"]
    manager.ensure_stop_allowed.assert_called_once_with("session-1")
    assert response.json()["data"]["weixin_session_slot"] == 4
    weixin_chub_mode.session_slot.assert_called_once_with("session-1")


@pytest.mark.anyio
async def test_stop_rejects_external_writer_before_cancelling_chub_work(
    settings: Settings,
) -> None:
    app = create_app(settings)
    manager = MagicMock()
    manager.ensure_stop_allowed.side_effect = ApiError(
        409,
        "codex_session_writer_active",
        "This is open in another app, close it there to continue here.",
    )
    quick_interactions = MagicMock()
    app.state.codex_pty_manager = manager
    app.state.quick_interactions = quick_interactions
    app.state.terminal_tickets = MagicMock()
    app.state.terminal_connections = MagicMock()
    transport = httpx.ASGITransport(app=app)

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/api/codex/sessions/session-1/stop",
            headers=authorization(settings),
        )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "codex_session_writer_active"
    manager.ensure_stop_allowed.assert_called_once_with("session-1")
    quick_interactions.cancel_codex_session.assert_not_called()
    app.state.terminal_tickets.revoke_session.assert_not_called()
    app.state.terminal_connections.close_session.assert_not_called()


@pytest.mark.anyio
async def test_rename_session_allows_running_task_and_logs_lifecycle(
    settings: Settings,
    monkeypatch,
) -> None:
    app = create_app(settings)
    manager = MagicMock()
    manager.rename_session.return_value = SessionInfo(
        id="session-1",
        runtime_id="codex",
        workspace_id="chub",
        workspace_name="Chub",
        cwd="/workspace/chub",
        title="新标题",
        can_archive=True,
        status="stopped",
        activity="working",
        activity_source="quick",
        permission_mode="auto-review",
        active_permission_mode=None,
        permission_pending=False,
        error=None,
        created_at=utc_now(),
        updated_at=utc_now(),
    )
    quick_interactions = MagicMock()
    weixin_chub_mode = MagicMock()
    weixin_chub_mode.session_slot.return_value = 2
    app.state.codex_pty_manager = manager
    app.state.quick_interactions = quick_interactions
    app.state.weixin_chub_mode = weixin_chub_mode
    statuses = []

    def record_operation(_request, **kwargs):
        statuses.append(kwargs["status"])
        return kwargs.get("operation_id") or "rename-operation"

    monkeypatch.setattr("app.codex.routes.log_operation", record_operation)
    transport = httpx.ASGITransport(app=app)

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.patch(
            "/api/codex/sessions/session-1/title",
            headers=authorization(settings),
            json={"title": "  新标题  "},
        )

    assert response.status_code == 200
    assert response.json()["data"]["title"] == "新标题"
    assert response.json()["data"]["weixin_session_slot"] == 2
    quick_interactions.session_operation_guard.assert_not_called()
    manager.rename_session.assert_called_once_with("session-1", "新标题")
    assert statuses == ["requested", "started", "succeeded"]


@pytest.mark.anyio
async def test_rename_session_logs_manager_failure(
    settings: Settings,
    monkeypatch,
) -> None:
    app = create_app(settings)
    manager = MagicMock()
    manager.rename_session.side_effect = ApiError(
        404,
        "codex_session_not_found",
        "Codex session not found",
    )
    app.state.codex_pty_manager = manager
    statuses = []

    def record_operation(_request, **kwargs):
        statuses.append(kwargs["status"])
        return kwargs.get("operation_id") or "rename-operation"

    monkeypatch.setattr("app.codex.routes.log_operation", record_operation)
    transport = httpx.ASGITransport(app=app)

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.patch(
            "/api/codex/sessions/session-1/title",
            headers=authorization(settings),
            json={"title": "新标题"},
        )

    assert response.status_code == 404
    manager.rename_session.assert_called_once_with("session-1", "新标题")
    assert statuses == ["requested", "started", "failed"]


@pytest.mark.anyio
async def test_rename_session_preserves_external_writer_error(
    settings: Settings,
    monkeypatch,
) -> None:
    app = create_app(settings)
    manager = MagicMock()
    manager.rename_session.side_effect = ApiError(
        409,
        "codex_session_writer_active",
        "This is open in another app, close it there to continue here.",
    )
    app.state.codex_pty_manager = manager
    statuses = []

    def record_operation(_request, **kwargs):
        statuses.append(kwargs["status"])
        return kwargs.get("operation_id") or "rename-operation"

    monkeypatch.setattr("app.codex.routes.log_operation", record_operation)
    transport = httpx.ASGITransport(app=app)

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.patch(
            "/api/codex/sessions/session-1/title",
            headers=authorization(settings),
            json={"title": "新标题"},
        )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "codex_session_writer_active"
    manager.rename_session.assert_called_once_with("session-1", "新标题")
    assert statuses == ["requested", "started", "failed"]


@pytest.mark.anyio
async def test_archive_session_revokes_access_and_calls_manager(
    settings: Settings,
) -> None:
    app = create_app(settings)
    manager = MagicMock()
    tickets = MagicMock()
    events: list[str] = []
    guard = MagicMock()
    guard.__enter__.side_effect = lambda: events.append("guard-enter")
    guard.__exit__.side_effect = lambda *_args: events.append("guard-exit") or False
    app.state.quick_interactions = MagicMock()
    app.state.quick_interactions.stop_operation_guard.return_value = guard
    manager.archive_native_session.side_effect = lambda _id: events.append(
        "native-archive"
    )
    app.state.quick_interactions.cancel_codex_session.side_effect = (
        lambda _id: events.append("cancel")
    )
    app.state.quick_interactions.remove_session_tasks.side_effect = (
        lambda _id: events.append("remove-tasks")
    )
    manager.finalize_archive_session.side_effect = lambda _id: events.append(
        "finalize"
    )
    app.state.weixin_chub_mode = MagicMock()
    app.state.weixin_chub_mode.release_session_slot.side_effect = (
        lambda _id: events.append("release") or True
    )
    app.state.codex_pty_manager = manager
    app.state.terminal_tickets = tickets
    transport = httpx.ASGITransport(app=app)

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/api/codex/sessions/session-1/archive",
            headers=authorization(settings),
        )

    assert response.status_code == 200
    tickets.revoke_session.assert_called_once_with("session-1")
    manager.archive_native_session.assert_called_once_with("session-1")
    manager.finalize_archive_session.assert_called_once_with("session-1")
    app.state.quick_interactions.cancel_codex_session.assert_called_once_with(
        "session-1"
    )
    assert events == [
        "guard-enter",
        "native-archive",
        "cancel",
        "remove-tasks",
        "release",
        "finalize",
        "guard-exit",
    ]


@pytest.mark.anyio
async def test_archive_session_is_idempotent_when_stale_mapping_is_gone(
    settings: Settings,
) -> None:
    app = create_app(settings)
    manager = MagicMock()
    quick_interactions = MagicMock()
    quick_interactions.stop_operation_guard.return_value = MagicMock()
    manager.archive_native_session.side_effect = ApiError(
        404,
        "codex_session_not_found",
        "Codex session not found",
    )
    manager.finalize_archive_session = MagicMock()
    app.state.codex_pty_manager = manager
    app.state.quick_interactions = quick_interactions
    app.state.terminal_tickets = MagicMock()
    app.state.terminal_connections = MagicMock()
    app.state.weixin_chub_mode = MagicMock()
    transport = httpx.ASGITransport(app=app)

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/api/codex/sessions/session-1/archive",
            headers=authorization(settings),
        )

    assert response.status_code == 200
    quick_interactions.cancel_codex_session.assert_called_once_with("session-1")
    quick_interactions.remove_session_tasks.assert_called_once_with("session-1")
    manager.finalize_archive_session.assert_called_once_with("session-1")


@pytest.mark.anyio
async def test_delete_session_is_idempotent_when_stale_mapping_is_gone(
    settings: Settings,
) -> None:
    app = create_app(settings)
    manager = MagicMock()
    quick_interactions = MagicMock()
    quick_interactions.destructive_operation_guard.return_value = MagicMock()
    manager.ensure_delete_allowed.side_effect = ApiError(
        404,
        "codex_session_not_found",
        "Codex session not found",
    )
    manager.delete_native_session.side_effect = ApiError(
        404,
        "codex_session_not_found",
        "Codex session not found",
    )
    manager.finalize_delete_session = MagicMock()
    app.state.codex_pty_manager = manager
    app.state.quick_interactions = quick_interactions
    app.state.terminal_tickets = MagicMock()
    app.state.terminal_connections = MagicMock()
    app.state.weixin_chub_mode = MagicMock()
    transport = httpx.ASGITransport(app=app)

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.delete(
            "/api/codex/sessions/session-1",
            headers=authorization(settings),
        )

    assert response.status_code == 200
    quick_interactions.remove_session_tasks.assert_called_once_with("session-1")
    manager.finalize_delete_session.assert_called_once_with(
        "session-1",
        terminal_already_closed=True,
    )


@pytest.mark.anyio
async def test_archive_session_fails_when_slot_release_cannot_be_confirmed(
    settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    write_operation = MagicMock()
    monkeypatch.setattr("app.codex.routes.write_operation", write_operation)
    app = create_app(settings)
    allow_session_writes(app)
    manager = MagicMock()
    app.state.codex_pty_manager = manager
    app.state.weixin_chub_mode = MagicMock()
    app.state.weixin_chub_mode.release_session_slot.side_effect = OSError(
        "disk unavailable"
    )
    transport = httpx.ASGITransport(app=app)

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/api/codex/sessions/session-1/archive",
            headers=authorization(settings),
        )

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "weixin_chub_mode_slot_release_unknown"
    manager.finalize_archive_session.assert_not_called()
    assert [item.kwargs["status"] for item in write_operation.call_args_list] == [
        "requested",
        "started",
        "failed",
    ]
    assert {
        item.kwargs["action"] for item in write_operation.call_args_list
    } == {"weixin_chub_mode_session_slot_release"}


@pytest.mark.anyio
async def test_delete_session_releases_slot_after_destructive_guard(
    settings: Settings,
) -> None:
    app = create_app(settings)
    events: list[str] = []
    guard = MagicMock()
    guard.__enter__.side_effect = lambda: events.append("guard-enter")
    guard.__exit__.side_effect = lambda *_args: events.append("guard-exit") or False
    app.state.quick_interactions = MagicMock()
    app.state.quick_interactions.destructive_operation_guard.return_value = guard
    app.state.quick_interactions.cancel_codex_session.side_effect = (
        lambda _id: events.append("cancel")
    )
    manager = MagicMock()
    manager.delete_native_session.side_effect = (
        lambda _id: events.append("delete")
    )
    manager.stop_session.side_effect = lambda _id, **_kwargs: events.append("stop")
    app.state.quick_interactions.remove_session_tasks.side_effect = (
        lambda _id: events.append("remove-tasks")
    )
    manager.finalize_delete_session.side_effect = (
        lambda _id, **_kwargs: events.append("finalize")
    )
    app.state.codex_pty_manager = manager
    app.state.weixin_chub_mode = MagicMock()
    app.state.weixin_chub_mode.release_session_slot.side_effect = (
        lambda _id: events.append("release") or True
    )
    transport = httpx.ASGITransport(app=app)

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.delete(
            "/api/codex/sessions/session-1",
            headers=authorization(settings),
        )

    assert response.status_code == 200
    assert events == [
        "guard-enter",
        "cancel",
        "stop",
        "delete",
        "remove-tasks",
        "release",
        "finalize",
        "guard-exit",
    ]


@pytest.mark.anyio
async def test_delete_session_does_not_gate_native_delete_on_terminal_cleanup_error(
    settings: Settings,
) -> None:
    app = create_app(settings)
    manager = MagicMock()
    quick_interactions = MagicMock()
    quick_interactions.destructive_operation_guard.return_value = MagicMock()
    manager.stop_session.side_effect = OSError("terminal carrier unavailable")
    app.state.codex_pty_manager = manager
    app.state.quick_interactions = quick_interactions
    app.state.terminal_tickets = MagicMock()
    app.state.terminal_connections = MagicMock()
    app.state.weixin_chub_mode = MagicMock()
    transport = httpx.ASGITransport(app=app)

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.delete(
            "/api/codex/sessions/session-1",
            headers=authorization(settings),
        )

    assert response.status_code == 200
    manager.delete_native_session.assert_called_once_with("session-1")
    manager.finalize_delete_session.assert_called_once_with(
        "session-1",
        terminal_already_closed=True,
    )


@pytest.mark.anyio
async def test_archive_session_uses_stop_guard_before_manager_gate(
    settings: Settings,
) -> None:
    app = create_app(settings)
    manager = MagicMock()
    quick_interactions = MagicMock()
    guard = MagicMock()
    quick_interactions.stop_operation_guard.return_value = guard
    app.state.codex_pty_manager = manager
    app.state.quick_interactions = quick_interactions
    app.state.terminal_tickets = MagicMock()
    app.state.terminal_connections = MagicMock()
    transport = httpx.ASGITransport(app=app)

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/api/codex/sessions/session-1/archive",
            headers=authorization(settings),
        )

    assert response.status_code == 200
    manager.archive_native_session.assert_called_once_with("session-1")
    quick_interactions.cancel_codex_session.assert_called_once_with("session-1")
    app.state.terminal_tickets.revoke_session.assert_called_once_with("session-1")
    app.state.terminal_connections.close_session.assert_called_once_with("session-1")
    manager.finalize_archive_session.assert_called_once_with("session-1")


@pytest.mark.anyio
async def test_archive_session_stops_before_cleaning_when_native_archive_fails(
    settings: Settings,
) -> None:
    app = create_app(settings)
    manager = MagicMock()
    manager.archive_native_session.side_effect = ApiError(
        409,
        "codex_session_writer_active",
        "This is open in another app, close it there to continue here.",
    )
    app.state.codex_pty_manager = manager
    app.state.quick_interactions = MagicMock()
    app.state.quick_interactions.stop_operation_guard.return_value = MagicMock()
    app.state.terminal_tickets = MagicMock()
    app.state.terminal_connections = MagicMock()
    transport = httpx.ASGITransport(app=app)

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/api/codex/sessions/session-1/archive",
            headers=authorization(settings),
        )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "codex_session_writer_active"
    app.state.quick_interactions.cancel_codex_session.assert_not_called()
    app.state.terminal_tickets.revoke_session.assert_not_called()
    app.state.terminal_connections.close_session.assert_not_called()
    manager.finalize_archive_session.assert_not_called()


@pytest.mark.anyio
async def test_delete_session_preserves_state_when_quick_cancellation_fails(
    settings: Settings,
) -> None:
    app = create_app(settings)
    guard = MagicMock()
    app.state.quick_interactions = MagicMock()
    app.state.quick_interactions.destructive_operation_guard.return_value = guard
    app.state.quick_interactions.cancel_codex_session.side_effect = ApiError(
        409,
        "quick_interaction_cancel_failed",
        "快速交互停止状态无法确认，请稍后重试。",
    )
    manager = MagicMock()
    app.state.codex_pty_manager = manager
    app.state.terminal_tickets = MagicMock()
    app.state.terminal_connections = MagicMock()
    transport = httpx.ASGITransport(app=app)

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.delete(
            "/api/codex/sessions/session-1",
            headers=authorization(settings),
        )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "quick_interaction_cancel_failed"
    manager.delete_native_session.assert_not_called()
    manager.finalize_delete_session.assert_not_called()
    app.state.quick_interactions.remove_session_tasks.assert_not_called()
    app.state.terminal_tickets.revoke_session.assert_not_called()
    app.state.terminal_connections.close_session.assert_not_called()
