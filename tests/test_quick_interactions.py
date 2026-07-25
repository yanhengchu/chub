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


def test_terminal_access_rejects_running_quick_interaction(tmp_path: Path) -> None:
    quick_interactions = manager(tmp_path)
    quick_interactions._running_sessions.add("session-1")

    with pytest.raises(ApiError) as error:
        with quick_interactions.terminal_access_guard("session-1"):
            pass

    assert error.value.code == "quick_interaction_in_progress"
