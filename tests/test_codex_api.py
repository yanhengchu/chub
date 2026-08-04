from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock, call

import httpx
import pytest

from app.application import create_app
from app.codex.models import (
    CodexSession,
    QuickInteractionTask,
    SessionInfo,
    SessionListData,
    WorkspaceInfo,
    utc_now,
)
from app.core.config import Settings
from app.core.response import ApiError


def authorization(settings: Settings) -> dict[str, str]:
    token = settings.security.token
    assert token is not None
    return {"Authorization": f"Bearer {token.get_secret_value()}"}


@pytest.mark.anyio
async def test_codex_sessions_require_authentication(settings: Settings) -> None:
    transport = httpx.ASGITransport(app=create_app(settings))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/api/codex/sessions")

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "authentication_required"


@pytest.mark.anyio
async def test_codex_session_list_reports_workspaces(settings: Settings) -> None:
    app = create_app(settings)
    manager = MagicMock()
    manager.available.return_value = False
    manager.unavailable_reason.return_value = "Codex PTY requires Tailscale"
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
    assert data["workspaces"][0]["id"] == "home"
    assert data["dependencies"]["tmux"] is False


@pytest.mark.anyio
async def test_codex_session_list_includes_active_quick_interaction(
    settings: Settings,
) -> None:
    app = create_app(settings)
    manager = MagicMock()
    manager.available.return_value = True
    manager.unavailable_reason.return_value = None
    manager.dependencies.return_value = {"codex": True, "ttyd": True, "tmux": True}
    manager.workspaces.return_value = []
    manager.list_sessions.return_value = [
        SessionInfo(
            id="session-1",
            workspace_id="chub",
            workspace_name="Chub",
            cwd="/workspace/chub",
            title=None,
            codex_session_id="codex-session-1",
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
    quick_interactions.llm_active_sessions.return_value = {}
    app.state.codex_pty_manager = manager
    app.state.quick_interactions = quick_interactions
    transport = httpx.ASGITransport(app=app)

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(
            "/api/codex/sessions",
            headers=authorization(settings),
        )

    session = response.json()["data"]["sessions"][0]
    assert session["quick_interaction_running"] is True
    assert session["quick_interaction_updated_at"] == "2026-07-24T10:02:00Z"


@pytest.mark.anyio
async def test_codex_session_list_exposes_bedrock_activity_separately(
    settings: Settings,
) -> None:
    app = create_app(settings)
    manager = MagicMock()
    manager.available.return_value = True
    manager.unavailable_reason.return_value = None
    manager.dependencies.return_value = {"codex": True, "ttyd": True, "tmux": True}
    manager.workspaces.return_value = []
    manager.list_sessions.return_value = [
        SessionInfo(
            id="session-1",
            workspace_id="chub",
            workspace_name="Chub",
            cwd="/workspace/chub",
            title=None,
            codex_session_id="codex-session-1",
            status="running",
            activity="idle",
            permission_mode="auto-review",
            active_permission_mode="auto-review",
            permission_pending=False,
            error=None,
            created_at="2026-07-24T10:00:00Z",
            updated_at="2026-07-24T10:01:00Z",
        )
    ]
    quick_interactions = MagicMock()
    quick_interactions.active_sessions.return_value = {}
    quick_interactions.llm_active_sessions.return_value = {
        "session-1": datetime(2026, 7, 24, 10, 3, tzinfo=UTC)
    }
    app.state.codex_pty_manager = manager
    app.state.quick_interactions = quick_interactions
    transport = httpx.ASGITransport(app=app)

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(
            "/api/codex/sessions",
            headers=authorization(settings),
        )

    session = response.json()["data"]["sessions"][0]
    assert session["quick_interaction_running"] is False
    assert session["llm_interaction_running"] is True
    assert session["llm_interaction_updated_at"] == "2026-07-24T10:03:00Z"


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
async def test_quick_interaction_keeps_idle_unconnected_terminal_running(
    settings: Settings,
) -> None:
    app = create_app(settings)
    manager = MagicMock()
    session = CodexSession(
        id="session-1",
        workspace_id="chub",
        workspace_name="Chub",
        cwd=Path("/workspace/chub"),
        codex_session_id="codex-session-1",
        status="running",
        activity="idle",
        permission_mode="auto-review",
    )
    manager.get_session.return_value = session
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

    assert response.status_code == 200
    manager.stop_session.assert_not_called()
    app.state.terminal_connections.close_session.assert_not_called()
    quick_interactions.submit.assert_called_once()


@pytest.mark.anyio
async def test_bedrock_quick_interaction_bypasses_codex_terminal_logic(
    settings: Settings,
) -> None:
    app = create_app(settings)
    manager = MagicMock()
    quick_interactions = MagicMock()
    quick_interactions.submit_llm.return_value = quick_task().model_copy(
        update={"engine": "bedrock_api"}
    )
    app.state.codex_pty_manager = manager
    app.state.quick_interactions = quick_interactions
    app.state.terminal_tickets = MagicMock()
    app.state.terminal_connections = MagicMock()
    transport = httpx.ASGITransport(app=app)

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/api/codex/sessions/session-1/quick-interactions",
            headers=authorization(settings),
            json={"prompt": "解释状态", "engine": "bedrock_api"},
        )

    assert response.status_code == 200
    assert response.json()["data"]["task"]["engine"] == "bedrock_api"
    quick_interactions.submit_llm.assert_called_once()
    quick_interactions.session_operation_guard.assert_not_called()
    manager.get_session.assert_not_called()
    manager.stop_session.assert_not_called()
    app.state.terminal_tickets.revoke_session.assert_not_called()
    app.state.terminal_connections.close_session.assert_not_called()


@pytest.mark.anyio
async def test_bedrock_quick_interaction_limits_prompt_length(
    settings: Settings,
) -> None:
    app = create_app(settings)
    transport = httpx.ASGITransport(app=app)

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/api/codex/sessions/session-1/quick-interactions",
            headers=authorization(settings),
            json={"prompt": "x" * 4001, "engine": "bedrock_api"},
        )

    assert response.status_code == 422


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
        codex_session_id="codex-session-1",
        status="running",
        activity=activity,
        permission_mode="auto-review",
    )
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
    assert response.json()["error"]["code"] == expected_code
    manager.stop_session.assert_not_called()
    quick_interactions.submit.assert_not_called()
    if expected_code == "quick_interaction_terminal_confirmation_required":
        write_operation.assert_not_called()
    else:
        write_operation.assert_called_once()


@pytest.mark.anyio
async def test_quick_interaction_keeps_idle_connected_terminal_running(
    settings: Settings,
) -> None:
    app = create_app(settings)
    manager = MagicMock()
    manager.get_session.return_value = CodexSession(
        id="session-1",
        workspace_id="chub",
        workspace_name="Chub",
        cwd=Path("/workspace/chub"),
        codex_session_id="codex-session-1",
        status="running",
        activity="idle",
        permission_mode="auto-review",
    )
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

    assert response.status_code == 200
    manager.stop_session.assert_not_called()
    app.state.terminal_connections.close_session.assert_not_called()
    quick_interactions.submit.assert_called_once()


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
        codex_session_id="codex-session-1",
        status="running",
        activity="unknown",
        permission_mode="auto-review",
    )
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

    assert response.status_code == 200
    manager.stop_session.assert_called_once_with("session-1")


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
        codex_session_id="codex-session-1",
        status="running",
        activity="idle",
        permission_mode="auto-review",
    )
    manager.get_session.return_value = base
    quick_interactions = MagicMock()
    quick_interactions.submit.side_effect = ApiError(
        409,
        "quick_interaction_terminal_working",
        "实时终端正在执行，请等待当前任务结束。",
    )
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
    assert response.json()["error"]["code"] == "quick_interaction_terminal_working"
    manager.stop_session.assert_not_called()
    quick_interactions.submit.assert_called_once()


@pytest.mark.anyio
async def test_quick_interaction_history_is_paginated(
    settings: Settings,
) -> None:
    app = create_app(settings)
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
    }
    quick_interactions.list_for_session.assert_not_called()


@pytest.mark.anyio
async def test_quick_interaction_can_be_pinned(settings: Settings) -> None:
    app = create_app(settings)
    task = quick_task().model_copy(update={"pinned_at": utc_now()})
    quick_interactions = MagicMock()
    quick_interactions.set_pinned.return_value = task
    app.state.quick_interactions = quick_interactions
    transport = httpx.ASGITransport(app=app)

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        unauthorized = await client.patch(
            "/api/codex/sessions/session-1/quick-interactions/task-1/pin",
            json={"pinned": True},
        )
        response = await client.patch(
            "/api/codex/sessions/session-1/quick-interactions/task-1/pin",
            headers=authorization(settings),
            json={"pinned": True},
        )

    assert unauthorized.status_code == 401
    assert response.status_code == 200
    assert response.json()["data"]["task"]["pinned_at"] is not None
    quick_interactions.set_pinned.assert_called_once_with(
        "session-1",
        "task-1",
        True,
    )


@pytest.mark.anyio
async def test_access_issues_scoped_http_only_cookie(settings: Settings) -> None:
    app = create_app(settings)
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
        workspace_id="chub",
        workspace_name="Chub",
        cwd="/workspace/chub",
        title=None,
        codex_session_id="codex-session-1",
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
    transport = httpx.ASGITransport(app=app)

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/api/codex/sessions/session-1/stop",
            headers=authorization(settings),
        )

    assert response.status_code == 200
    assert events == ["cancel", "stop"]


@pytest.mark.anyio
async def test_archive_session_revokes_access_and_calls_manager(
    settings: Settings,
) -> None:
    app = create_app(settings)
    manager = MagicMock()
    tickets = MagicMock()
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
    manager.archive_session.assert_called_once_with("session-1")


@pytest.mark.anyio
async def test_archive_session_rejects_running_quick_interaction(
    settings: Settings,
) -> None:
    app = create_app(settings)
    manager = MagicMock()
    quick_interactions = MagicMock()
    guard = MagicMock()
    guard.__enter__.side_effect = ApiError(
        409,
        "quick_interaction_in_progress",
        "该会话正在执行快速交互，请等待任务结束。",
    )
    quick_interactions.destructive_operation_guard.return_value = guard
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

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "quick_interaction_in_progress"
    manager.archive_session.assert_not_called()
    app.state.terminal_tickets.revoke_session.assert_not_called()
    app.state.terminal_connections.close_session.assert_not_called()


@pytest.mark.anyio
async def test_delete_session_rejects_running_quick_without_closing_terminal(
    settings: Settings,
) -> None:
    app = create_app(settings)
    guard = MagicMock()
    guard.__enter__.side_effect = ApiError(
        409,
        "quick_interaction_in_progress",
        "该会话正在执行快速交互，请等待任务结束。",
    )
    app.state.quick_interactions = MagicMock()
    app.state.quick_interactions.destructive_operation_guard.return_value = guard
    app.state.codex_pty_manager = MagicMock()
    app.state.terminal_tickets = MagicMock()
    app.state.terminal_connections = MagicMock()
    transport = httpx.ASGITransport(app=app)

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.delete(
            "/api/codex/sessions/session-1",
            headers=authorization(settings),
        )

    assert response.status_code == 409
    app.state.codex_pty_manager.delete_session.assert_not_called()
    app.state.terminal_tickets.revoke_session.assert_not_called()
    app.state.terminal_connections.close_session.assert_not_called()


@pytest.mark.anyio
async def test_update_permission_rejects_running_quick_interaction(
    settings: Settings,
) -> None:
    app = create_app(settings)
    guard = MagicMock()
    guard.__enter__.side_effect = ApiError(
        409,
        "quick_interaction_in_progress",
        "该会话正在执行快速交互，请等待任务结束。",
    )
    app.state.quick_interactions = MagicMock()
    app.state.quick_interactions.session_operation_guard.return_value = guard
    app.state.codex_pty_manager = MagicMock()
    transport = httpx.ASGITransport(app=app)

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.patch(
            "/api/codex/sessions/session-1/permission",
            headers=authorization(settings),
            json={"permission_mode": "auto-review"},
        )

    assert response.status_code == 409
    app.state.codex_pty_manager.update_permission_and_stop.assert_not_called()


@pytest.mark.anyio
async def test_update_session_permission_calls_manager(settings: Settings) -> None:
    app = create_app(settings)
    manager = MagicMock()
    session = SessionInfo(
        id="session-1",
        workspace_id="chub",
        workspace_name="Chub",
        cwd="/workspace/chub",
        title=None,
        codex_session_id=None,
        status="running",
        activity="idle",
        permission_mode="auto-review",
        active_permission_mode="ask",
        permission_pending=True,
        error=None,
        created_at="2026-07-24T10:00:00Z",
        updated_at="2026-07-24T10:01:00Z",
    )
    manager.update_permission_and_stop.return_value = (session, False)
    app.state.codex_pty_manager = manager
    transport = httpx.ASGITransport(app=app)

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.patch(
            "/api/codex/sessions/session-1/permission",
            headers=authorization(settings),
            json={"permission_mode": "auto-review"},
        )

    assert response.status_code == 200
    assert response.json()["data"]["application"] == "pending"
    assert response.json()["data"]["session"]["permission_pending"] is True
    manager.update_permission_and_stop.assert_called_once_with(
        "session-1",
        "auto-review",
    )


@pytest.mark.anyio
async def test_update_session_permission_auto_stops_idle_unconnected_session(
    settings: Settings,
) -> None:
    app = create_app(settings)
    manager = MagicMock()
    session = SessionInfo(
        id="session-1",
        workspace_id="chub",
        workspace_name="Chub",
        cwd="/workspace/chub",
        title=None,
        codex_session_id="codex-session-1",
        status="stopped",
        activity="unknown",
        permission_mode="full-access",
        active_permission_mode=None,
        permission_pending=False,
        error=None,
        created_at="2026-07-24T10:00:00Z",
        updated_at="2026-07-24T10:01:00Z",
    )
    manager.update_permission_and_stop.return_value = (session, True)
    tickets = MagicMock()
    connections = MagicMock()
    app.state.codex_pty_manager = manager
    app.state.terminal_tickets = tickets
    app.state.terminal_connections = connections
    transport = httpx.ASGITransport(app=app)

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.patch(
            "/api/codex/sessions/session-1/permission",
            headers=authorization(settings),
            json={"permission_mode": "full-access"},
        )

    assert response.status_code == 200
    assert response.json()["data"]["application"] == "stopped"
    tickets.revoke_session.assert_called_once_with("session-1")
    connections.close_session.assert_called_once_with("session-1")


@pytest.mark.anyio
async def test_update_session_permission_rejects_unknown_mode(
    settings: Settings,
) -> None:
    app = create_app(settings)
    app.state.codex_pty_manager = MagicMock()
    transport = httpx.ASGITransport(app=app)

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.patch(
            "/api/codex/sessions/session-1/permission",
            headers=authorization(settings),
            json={"permission_mode": "custom"},
        )

    assert response.status_code == 422
