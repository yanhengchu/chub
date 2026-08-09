import json
import threading
import stat
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from app.codex.models import CodexSession, QuickInteractionTask, utc_now
from app.codex.quick_interactions import (
    CODEX_QUICK_INTERACTION_INSTRUCTIONS,
    QuickInteractionManager,
)
from app.core.response import ApiError


def manager(tmp_path: Path, completion_notifier=None) -> QuickInteractionManager:
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
    return QuickInteractionManager(
        tmp_path / "codex-sessions.json",
        tmp_path / "runtime",
        codex_manager,
        completion_notifier,
    )


def test_quick_interaction_timeout_is_configurable(tmp_path: Path) -> None:
    quick_interactions = manager(tmp_path)
    configured = QuickInteractionManager(
        tmp_path / "custom-codex-sessions.json",
        tmp_path / "runtime",
        quick_interactions.codex_manager,
        timeout_seconds=7_200,
    )

    assert quick_interactions.timeout_seconds == 21_600
    assert configured.timeout_seconds == 7_200


def test_runtime_attachments_are_private(tmp_path: Path) -> None:
    attachments = [tmp_path / "task.err", tmp_path / "task.jsonl"]
    for path in attachments:
        path.write_text("runtime output", encoding="utf-8")
        path.chmod(0o664)

    QuickInteractionManager._set_private_permissions(*attachments)

    assert all(stat.S_IMODE(path.stat().st_mode) == 0o600 for path in attachments)


def test_command_creates_or_resumes_codex_session(tmp_path: Path) -> None:
    quick_interactions = manager(tmp_path)
    session = quick_interactions.codex_manager.get_session.return_value

    new_command = quick_interactions._command(
        session.model_copy(update={"codex_session_id": None}),
        tmp_path / "new-result.txt",
    )
    resume_command = quick_interactions._command(
        session,
        tmp_path / "resume-result.txt",
    )

    assert new_command[-1] == "-"
    assert "resume" not in new_command
    assert resume_command[-3:] == ["resume", "codex-session-1", "-"]


def test_command_adds_session_model_and_reasoning_level(tmp_path: Path) -> None:
    quick_interactions = manager(tmp_path)
    session = quick_interactions.codex_manager.get_session.return_value.model_copy(
        update={"model": "gpt-test", "reasoning_effort": "high"}
    )

    command = quick_interactions._command(session, tmp_path / "result.txt")

    assert ["--model", "gpt-test"] == command[
        command.index("--model") : command.index("--model") + 2
    ]
    assert 'model_reasoning_effort="high"' in command
    assert command[-3:] == ["resume", "codex-session-1", "-"]


def test_codex_execution_prompt_adds_delivery_guidance_without_changing_request(
    tmp_path: Path,
) -> None:
    quick_interactions = manager(tmp_path)
    prompt = quick_interactions._codex_execution_prompt("调整页面布局")

    assert prompt.startswith("[用户需求]\n调整页面布局")
    assert prompt.endswith(CODEX_QUICK_INTERACTION_INSTRUCTIONS)
    assert "完成效果" in prompt
    assert "验收方法" in prompt


def test_session_title_uses_first_user_request_line(tmp_path: Path) -> None:
    quick_interactions = manager(tmp_path)

    assert quick_interactions._session_title("\n  修复首页会话标题  \n补充测试") == "修复首页会话标题"
    assert quick_interactions._session_title("\n\n") == "快速交互"


def test_submit_allows_new_session_and_prepares_managed_profile(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    quick_interactions = manager(tmp_path)
    session = quick_interactions.codex_manager.get_session.return_value
    session.codex_session_id = None
    thread = MagicMock()
    monkeypatch.setattr(
        "app.codex.quick_interactions.threading.Thread",
        MagicMock(return_value=thread),
    )

    task = quick_interactions.submit(
        session.id,
        "执行第一条任务",
        operation_id="operation-1",
        source_ip="127.0.0.1",
    )

    assert task.status == "requested"
    assert task.prompt == "执行第一条任务"
    quick_interactions.codex_manager.prepare_quick_interaction.assert_called_once_with()
    quick_interactions.codex_manager.set_initial_quick_interaction_title.assert_called_once_with(
        session.id,
        "执行第一条任务",
    )
    thread.start.assert_called_once_with()


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


def test_quick_interaction_completion_notification_is_independent(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    notifier = MagicMock(return_value=SimpleNamespace(status="sent", error=None))
    quick_interactions = manager(tmp_path, completion_notifier=notifier)
    task = QuickInteractionTask(
        id="task-1",
        session_id="session-1",
        prompt="检查状态",
        status="running",
        created_at=utc_now(),
        updated_at=utc_now(),
    )
    quick_interactions._tasks[task.id] = task
    quick_interactions._operations[task.id] = ("operation-1", "127.0.0.1")

    class ImmediateThread:
        def __init__(self, *, target, args, daemon):
            self.target = target
            self.args = args

        def start(self) -> None:
            self.target(*self.args)

    monkeypatch.setattr(
        "app.codex.quick_interactions.threading.Thread",
        ImmediateThread,
    )

    quick_interactions._finish(task.id, "succeeded", "完成")

    finished = quick_interactions.get(task.id)
    assert finished.status == "succeeded"
    assert finished.result == "完成"
    assert finished.notification_status == "sent"
    notifier.assert_called_once()


def test_notification_failure_does_not_change_task_result(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    notifier = MagicMock(
        return_value=SimpleNamespace(status="failed", error="微信通知未送达。")
    )
    quick_interactions = manager(tmp_path, completion_notifier=notifier)
    task = QuickInteractionTask(
        id="task-1",
        session_id="session-1",
        prompt="检查状态",
        status="running",
        created_at=utc_now(),
        updated_at=utc_now(),
    )
    quick_interactions._tasks[task.id] = task
    quick_interactions._operations[task.id] = ("operation-1", "127.0.0.1")

    class ImmediateThread:
        def __init__(self, *, target, args, daemon):
            self.target = target
            self.args = args

        def start(self) -> None:
            self.target(*self.args)

    monkeypatch.setattr(
        "app.codex.quick_interactions.threading.Thread",
        ImmediateThread,
    )

    quick_interactions._finish(task.id, "succeeded", "完成")

    finished = quick_interactions.get(task.id)
    assert finished.status == "succeeded"
    assert finished.result == "完成"
    assert finished.notification_status == "failed"
    assert finished.notification_error == "微信通知未送达。"

def test_restart_marks_running_task_failed_and_persists_state(tmp_path: Path) -> None:
    state = tmp_path / "quick-interactions.json"
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
    quick_interactions.codex_manager.recover_interrupted_quick_interaction.assert_called_once_with(
        "session-1"
    )


def test_restart_marks_incomplete_notification_failed(tmp_path: Path) -> None:
    state = tmp_path / "quick-interactions.json"
    task = QuickInteractionTask(
        id="task-1",
        session_id="session-1",
        prompt="检查状态",
        status="succeeded",
        result="完成",
        notification_status="sending",
        created_at=utc_now(),
        updated_at=utc_now(),
    )
    state.write_text(json.dumps([task.model_dump(mode="json")]), encoding="utf-8")

    quick_interactions = manager(tmp_path)

    recovered = quick_interactions.get("task-1")
    assert recovered.status == "succeeded"
    assert recovered.notification_status == "failed"
    assert recovered.notification_error == "服务重启时微信通知未完成。"


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


def test_list_for_session_keeps_latest_before_pinned_and_ordinary(
    tmp_path: Path,
) -> None:
    quick_interactions = manager(tmp_path)
    base = utc_now()
    tasks = [
        QuickInteractionTask(
            id="older-pinned",
            session_id="session-1",
            prompt="较早置顶",
            status="succeeded",
            result="完成",
            pinned_at=base + timedelta(minutes=4),
            created_at=base,
            updated_at=base,
        ),
        QuickInteractionTask(
            id="newer-pinned",
            session_id="session-1",
            prompt="较晚置顶",
            status="succeeded",
            result="完成",
            pinned_at=base + timedelta(minutes=5),
            created_at=base + timedelta(minutes=1),
            updated_at=base + timedelta(minutes=1),
        ),
        QuickInteractionTask(
            id="ordinary",
            session_id="session-1",
            prompt="普通记录",
            status="succeeded",
            result="完成",
            created_at=base + timedelta(minutes=2),
            updated_at=base + timedelta(minutes=2),
        ),
        QuickInteractionTask(
            id="latest",
            session_id="session-1",
            prompt="最新记录",
            status="succeeded",
            result="完成",
            created_at=base + timedelta(minutes=3),
            updated_at=base + timedelta(minutes=3),
        ),
    ]
    quick_interactions._tasks = {task.id: task for task in tasks}

    listed = quick_interactions.list_for_session("session-1")

    assert [task.id for task in listed] == [
        "latest",
        "newer-pinned",
        "older-pinned",
        "ordinary",
    ]


def test_list_for_session_can_return_timeline_order(tmp_path: Path) -> None:
    quick_interactions = manager(tmp_path)
    base = utc_now()
    tasks = [
        QuickInteractionTask(
            id="older-pinned",
            session_id="session-1",
            prompt="较早置顶",
            status="succeeded",
            result="完成",
            pinned_at=base + timedelta(minutes=3),
            created_at=base,
            updated_at=base,
        ),
        QuickInteractionTask(
            id="latest",
            session_id="session-1",
            prompt="最新记录",
            status="succeeded",
            result="完成",
            created_at=base + timedelta(minutes=2),
            updated_at=base + timedelta(minutes=2),
        ),
        QuickInteractionTask(
            id="middle",
            session_id="session-1",
            prompt="中间记录",
            status="succeeded",
            result="完成",
            created_at=base + timedelta(minutes=1),
            updated_at=base + timedelta(minutes=1),
        ),
    ]
    quick_interactions._tasks = {task.id: task for task in tasks}

    listed = quick_interactions.list_for_session(
        "session-1",
        order="timeline",
    )

    assert [task.id for task in listed] == ["latest", "middle", "older-pinned"]


def test_set_pinned_persists_and_can_be_cancelled(tmp_path: Path) -> None:
    quick_interactions = manager(tmp_path)
    task = QuickInteractionTask(
        id="task-1",
        session_id="session-1",
        prompt="检查状态",
        status="succeeded",
        result="完成",
        created_at=utc_now(),
        updated_at=utc_now(),
    )
    quick_interactions._tasks = {task.id: task}

    pinned = quick_interactions.set_pinned("session-1", task.id, True)

    assert pinned.pinned_at is not None
    persisted = json.loads(quick_interactions.path.read_text(encoding="utf-8"))
    assert persisted[0]["pinned_at"] is not None

    unpinned = quick_interactions.set_pinned("session-1", task.id, False)

    assert unpinned.pinned_at is None
    persisted = json.loads(quick_interactions.path.read_text(encoding="utf-8"))
    assert persisted[0]["pinned_at"] is None


def test_set_pinned_rejects_task_from_another_session(tmp_path: Path) -> None:
    quick_interactions = manager(tmp_path)
    task = QuickInteractionTask(
        id="task-1",
        session_id="another-session",
        prompt="检查状态",
        status="succeeded",
        result="完成",
        created_at=utc_now(),
        updated_at=utc_now(),
    )
    quick_interactions._tasks = {task.id: task}

    with pytest.raises(ApiError) as error:
        quick_interactions.set_pinned("session-1", task.id, True)

    assert error.value.code == "quick_interaction_not_found"


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


def test_is_running_reports_session_input_lock(tmp_path: Path) -> None:
    quick_interactions = manager(tmp_path)

    assert quick_interactions.is_running("session-1") is False
    quick_interactions._running_sessions.add("session-1")
    assert quick_interactions.is_running("session-1") is True


def test_terminal_input_guard_serializes_session_operations(tmp_path: Path) -> None:
    quick_interactions = manager(tmp_path)
    entered = threading.Event()

    def enter_session_operation() -> None:
        with quick_interactions.session_operation_guard("session-1"):
            entered.set()

    with quick_interactions.terminal_input_guard("session-1") as allowed:
        assert allowed is True
        worker = threading.Thread(target=enter_session_operation)
        worker.start()
        assert entered.wait(0.05) is False

    worker.join(timeout=1)
    assert entered.is_set()


def test_terminal_input_guard_rejects_input_during_quick_interaction(
    tmp_path: Path,
) -> None:
    quick_interactions = manager(tmp_path)
    quick_interactions._running_sessions.add("session-1")

    with quick_interactions.terminal_input_guard("session-1") as allowed:
        assert allowed is False


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


def test_local_history_never_prunes_pinned_tasks(tmp_path: Path) -> None:
    quick_interactions = manager(tmp_path)
    pinned = QuickInteractionTask(
        id="pinned",
        session_id="session-1",
        prompt="长期保留",
        status="succeeded",
        result="完成",
        pinned_at=utc_now(),
        created_at=utc_now() - timedelta(days=30),
        updated_at=utc_now() - timedelta(days=30),
    )
    completed = [
        QuickInteractionTask(
            id=f"completed-{index}",
            session_id="session-1",
            prompt=f"完成任务 {index}",
            status="succeeded",
            result="完成",
            created_at=utc_now() + timedelta(minutes=index),
            updated_at=utc_now() + timedelta(minutes=index),
        )
        for index in range(30)
    ]
    quick_interactions._tasks = {
        task.id: task
        for task in [pinned, *completed]
    }

    quick_interactions._write()

    assert len(quick_interactions._tasks) == 30
    assert pinned.id in quick_interactions._tasks


def test_local_history_keeps_latest_when_all_older_tasks_are_pinned(
    tmp_path: Path,
) -> None:
    quick_interactions = manager(tmp_path)
    base = utc_now()
    pinned = [
        QuickInteractionTask(
            id=f"pinned-{index}",
            session_id="session-1",
            prompt=f"置顶任务 {index}",
            status="succeeded",
            result="完成",
            pinned_at=base + timedelta(minutes=index),
            created_at=base + timedelta(minutes=index),
            updated_at=base + timedelta(minutes=index),
        )
        for index in range(30)
    ]
    latest = QuickInteractionTask(
        id="latest",
        session_id="session-1",
        prompt="最新任务",
        status="succeeded",
        result="完成",
        created_at=base + timedelta(minutes=31),
        updated_at=base + timedelta(minutes=31),
    )
    quick_interactions._tasks = {
        task.id: task
        for task in [*pinned, latest]
    }

    quick_interactions._write()

    assert latest.id in quick_interactions._tasks
    assert all(task.id in quick_interactions._tasks for task in pinned)


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
    quick_interactions.codex_manager.get_session.return_value.activity = "working"

    with pytest.raises(ApiError) as error:
        quick_interactions.submit(
            "session-1",
            "检查状态",
            operation_id="operation-1",
            source_ip="127.0.0.1",
        )

    assert error.value.code == "quick_interaction_terminal_working"


def test_submit_rejects_session_error(tmp_path: Path) -> None:
    quick_interactions = manager(tmp_path)
    session = quick_interactions.codex_manager.get_session.return_value
    session.status = "error"
    session.activity = "unknown"

    with pytest.raises(ApiError) as error:
        quick_interactions.submit(
            "session-1",
            "检查状态",
            operation_id="operation-1",
            source_ip="127.0.0.1",
        )

    assert error.value.code == "quick_interaction_session_error"


def test_submit_allows_idle_running_terminal(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    quick_interactions = manager(tmp_path)
    session = quick_interactions.codex_manager.get_session.return_value
    session.status = "running"
    session.activity = "idle"
    thread = MagicMock()
    monkeypatch.setattr(
        "app.codex.quick_interactions.threading.Thread",
        MagicMock(return_value=thread),
    )

    task = quick_interactions.submit(
        "session-1",
        "检查状态",
        operation_id="operation-1",
        source_ip="127.0.0.1",
    )

    assert task.status == "requested"
    thread.start.assert_called_once()


def test_session_operation_rejects_running_quick_interaction(tmp_path: Path) -> None:
    quick_interactions = manager(tmp_path)
    quick_interactions._running_sessions.add("session-1")

    with pytest.raises(ApiError) as error:
        with quick_interactions.session_operation_guard("session-1"):
            pass

    assert error.value.code == "quick_interaction_in_progress"


def test_cancel_before_process_start_finishes_task_as_cancelled(tmp_path: Path) -> None:
    quick_interactions = manager(tmp_path)
    session = quick_interactions.codex_manager.get_session.return_value
    task = QuickInteractionTask(
        id="task-1",
        session_id=session.id,
        prompt="检查状态",
        status="requested",
        created_at=utc_now(),
        updated_at=utc_now(),
    )
    quick_interactions._tasks[task.id] = task
    quick_interactions._running_sessions.add(session.id)
    quick_interactions._active_task_ids.add(task.id)
    quick_interactions._cancelled_task_ids.add(task.id)
    done = threading.Event()
    quick_interactions._task_done_events[task.id] = done

    quick_interactions._run(task.id, session, task.prompt or "")

    finished = quick_interactions.get(task.id)
    assert finished.status == "cancelled"
    assert finished.error == "已由用户停止。"
    assert done.is_set()
    assert quick_interactions.is_running(session.id) is False


def test_cancel_codex_session_kills_process_and_waits_for_cleanup(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    quick_interactions = manager(tmp_path)
    task = QuickInteractionTask(
        id="task-1",
        session_id="session-1",
        prompt="检查状态",
        status="running",
        created_at=utc_now(),
        updated_at=utc_now(),
    )
    process = MagicMock()
    done = threading.Event()
    quick_interactions._tasks[task.id] = task
    quick_interactions._running_sessions.add(task.session_id)
    quick_interactions._active_task_ids.add(task.id)
    quick_interactions._processes[task.id] = process
    quick_interactions._task_done_events[task.id] = done
    kill = MagicMock(side_effect=lambda _process: done.set())
    monkeypatch.setattr(quick_interactions, "_kill_process", kill)

    assert quick_interactions.cancel_codex_session(task.session_id) is True

    kill.assert_called_once_with(process)
    assert task.id in quick_interactions._cancelled_task_ids
