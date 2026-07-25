import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from app.codex.models import CodexSession, QuickInteractionTask, utc_now
from app.codex.quick_interactions import QuickInteractionManager
from app.core.response import ApiError


def manager(tmp_path: Path) -> QuickInteractionManager:
    codex_manager = MagicMock()
    codex_manager.get_session.return_value = CodexSession(
        id="session-1",
        workspace_id="chub",
        workspace_name="Chub",
        cwd=tmp_path,
        codex_session_id="codex-session-1",
        status="stopped",
        permission_mode="auto-review",
    )
    codex_manager.hook_dir = tmp_path / "hooks"
    return QuickInteractionManager(tmp_path / "codex-sessions.json", codex_manager)


def test_json_error_extracts_turn_failure_and_redacts_bearer(tmp_path: Path) -> None:
    path = tmp_path / "events.jsonl"
    path.write_text(
        "\n".join(
            [
                json.dumps({"type": "turn.started"}),
                json.dumps(
                    {
                        "type": "turn.failed",
                        "error": {
                            "message": "Usage limit reached Bearer secret-token"
                        },
                    }
                ),
            ]
        ),
        encoding="utf-8",
    )

    assert QuickInteractionManager._json_error(path) == (
        "Usage limit reached Bearer [REDACTED]"
    )


def test_restart_marks_running_task_failed_and_persists_state(tmp_path: Path) -> None:
    state = tmp_path / "codex-quick-interactions.json"
    task = QuickInteractionTask(
        id="task-1",
        session_id="session-1",
        prompt="检查状态",
        status="running",
        created_at=utc_now(),
        updated_at=utc_now(),
    )
    state.write_text(
        json.dumps([task.model_dump(mode="json")]),
        encoding="utf-8",
    )

    quick_interactions = manager(tmp_path)

    assert quick_interactions.get("task-1").status == "failed"
    persisted = json.loads(state.read_text(encoding="utf-8"))
    assert persisted[0]["status"] == "failed"
    assert persisted[0]["error"] == "服务重启时任务未完成。"


def test_list_for_session_returns_latest_first(tmp_path: Path) -> None:
    quick_interactions = manager(tmp_path)
    older = QuickInteractionTask(
        id="older",
        session_id="session-1",
        prompt="较早",
        status="succeeded",
        result="完成",
        created_at=utc_now(),
        updated_at=utc_now(),
    )
    newer = older.model_copy(
        update={"id": "newer", "prompt": "较新", "created_at": utc_now()}
    )
    quick_interactions._tasks = {newer.id: newer, older.id: older}

    tasks = quick_interactions.list_for_session("session-1")

    assert [task.id for task in tasks] == ["newer", "older"]
    assert tasks[0].prompt == "较新"


def test_active_sessions_reports_only_running_session_ids(tmp_path: Path) -> None:
    quick_interactions = manager(tmp_path)
    task = QuickInteractionTask(
        id="active",
        session_id="session-1",
        prompt="检查状态",
        status="running",
        created_at=utc_now(),
        updated_at=utc_now(),
    )
    quick_interactions._tasks = {task.id: task}
    quick_interactions._running_sessions.add("session-1")

    active = quick_interactions.active_sessions()

    assert active == {"session-1": task.updated_at}


def test_local_history_retains_at_most_thirty_tasks(tmp_path: Path) -> None:
    quick_interactions = manager(tmp_path)
    tasks = [
        QuickInteractionTask(
            id=f"task-{index}",
            session_id="session-1",
            prompt=f"任务 {index}",
            status="succeeded",
            result="完成",
            created_at=utc_now(),
            updated_at=utc_now(),
        )
        for index in range(31)
    ]
    quick_interactions._tasks = {task.id: task for task in tasks}

    quick_interactions._write()

    assert len(quick_interactions._tasks) == 30
    persisted = json.loads(quick_interactions.path.read_text(encoding="utf-8"))
    assert len(persisted) == 30


def test_local_history_never_prunes_active_tasks(tmp_path: Path) -> None:
    quick_interactions = manager(tmp_path)
    active = [
        QuickInteractionTask(
            id=f"active-{index}",
            session_id=f"session-{index}",
            prompt=f"运行任务 {index}",
            status="running",
            created_at=utc_now(),
            updated_at=utc_now(),
        )
        for index in range(2)
    ]
    completed = [
        QuickInteractionTask(
            id=f"completed-{index}",
            session_id="session-completed",
            prompt=f"完成任务 {index}",
            status="succeeded",
            result="完成",
            created_at=utc_now(),
            updated_at=utc_now(),
        )
        for index in range(30)
    ]
    quick_interactions._tasks = {
        task.id: task
        for task in [*active, *completed]
    }

    quick_interactions._write()

    assert len(quick_interactions._tasks) == 30
    assert {task.id for task in active} <= quick_interactions._tasks.keys()


def test_local_history_keeps_finished_task_until_session_cleanup(
    tmp_path: Path,
) -> None:
    quick_interactions = manager(tmp_path)
    finished = QuickInteractionTask(
        id="just-finished",
        session_id="active-session",
        prompt="刚完成",
        status="succeeded",
        result="完成",
        created_at=utc_now(),
        updated_at=utc_now(),
    )
    other_active = [
        QuickInteractionTask(
            id=f"active-{index}",
            session_id=f"session-{index}",
            prompt=f"运行任务 {index}",
            status="running",
            created_at=utc_now(),
            updated_at=utc_now(),
        )
        for index in range(30)
    ]
    quick_interactions._tasks = {
        task.id: task
        for task in [finished, *other_active]
    }
    quick_interactions._running_sessions = {
        finished.session_id,
        *(task.session_id for task in other_active),
    }
    quick_interactions._active_task_ids.add(finished.id)

    quick_interactions._write()

    assert "just-finished" in quick_interactions._tasks

    quick_interactions._active_task_ids.discard(finished.id)
    quick_interactions._running_sessions.discard(finished.session_id)
    quick_interactions._write()

    assert "just-finished" not in quick_interactions._tasks


def test_submit_rechecks_terminal_status(tmp_path: Path) -> None:
    quick_interactions = manager(tmp_path)
    quick_interactions.codex_manager.get_session.return_value.status = "running"

    with pytest.raises(ApiError) as error:
        quick_interactions.submit(
            "session-1",
            "检查状态",
            operation_id="operation-1",
            source_ip="127.0.0.1",
        )

    assert error.value.code == "quick_interaction_terminal_active"


def test_session_operation_rejects_running_quick_interaction(tmp_path: Path) -> None:
    quick_interactions = manager(tmp_path)
    quick_interactions._running_sessions.add("session-1")

    with pytest.raises(ApiError) as error:
        with quick_interactions.session_operation_guard("session-1"):
            pass

    assert error.value.code == "quick_interaction_in_progress"
