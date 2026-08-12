import json
import threading
import stat
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from app.codex.models import (
    CodexSession,
    QuickInteractionDeferredRestartContext,
    QuickInteractionTask,
    QuickInteractionWeixinRoute,
    utc_now,
)
from app.codex.quick_interactions import (
    CODEX_QUICK_INTERACTION_INSTRUCTIONS,
    QuickInteractionManager,
    build_task_summary,
)
from app.core.response import ApiError


def manager(
    tmp_path: Path,
    completion_notifier=None,
    deferred_restart=None,
    restart_notifier=None,
) -> QuickInteractionManager:
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
    codex_manager.has_active_writer.return_value = False
    codex_manager.hook_dir = tmp_path / "hooks"
    return QuickInteractionManager(
        tmp_path / "codex-sessions.json",
        tmp_path / "runtime",
        codex_manager,
        completion_notifier,
        deferred_restart,
        restart_notifier=restart_notifier,
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
    assert "只能调用 scripts/chub-web-restart 一次" in prompt


def test_result_suffix_stays_within_persisted_limit(tmp_path: Path) -> None:
    quick_interactions = manager(tmp_path)

    result = quick_interactions._append_result_suffix(
        "a" * 100_000,
        "本次处理已完成，即将重启 Chub 服务。",
    )

    assert len(result.encode("utf-8")) <= 100_000
    assert result.endswith("本次处理已完成，即将重启 Chub 服务。")


def test_restart_result_explains_waiting_for_other_quick_tasks(tmp_path: Path) -> None:
    quick_interactions = manager(tmp_path)

    assert quick_interactions._deferred_restart_suffix(0) == (
        "本次处理已完成，即将重启 Chub 服务。"
    )
    assert quick_interactions._deferred_restart_suffix(2) == (
        "本次处理已完成，已安排重启；正在等待其他 2 个快速交互结束，"
        "全部完成后将自动重启 Chub。"
    )


def test_session_title_uses_first_user_request_line(tmp_path: Path) -> None:
    quick_interactions = manager(tmp_path)

    assert quick_interactions._session_title("\n  修复首页会话标题  \n补充测试") == "修复首页会话标题"
    assert quick_interactions._session_title("\n\n") == "快速交互"


def test_task_summary_is_stable_bounded_and_redacted() -> None:
    assert build_task_summary("\n检查设备状态。\n补充说明") == "检查设备状态。"
    sensitive_prompts = (
        "使用 Bearer secret-token 检查接口",
        "webhook=https://example.test/private 执行通知",
        "Authorization: Bearer private-value 检查接口",
        "Cookie: session=private-value 检查页面",
    )
    for prompt in sensitive_prompts:
        summary = build_task_summary(prompt)
        assert len(summary) <= 13
        assert "secret-token" not in summary
        assert "private" not in summary
        assert "session=" not in summary
    assert build_task_summary("任务" * 100) == "任务" * 6 + "…"
    assert build_task_summary("检查 Ubuntu 服务状态") == "检查 Ubuntu 服务…"


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
    assert task.summary == "执行第一条任务"
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
    notification_task, notification_route = notifier.call_args.args
    assert notification_task.id == finished.id
    assert notification_task.notification_status == "sending"
    assert notification_route is None


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


def test_status_check_returns_matching_running_weixin_task(tmp_path: Path) -> None:
    quick_interactions = manager(tmp_path)
    route = QuickInteractionWeixinRoute(
        account_id="weixin-account",
        recipient="owner@im.wechat",
    )
    running = QuickInteractionTask(
        id="running-task",
        session_id="session-1",
        prompt="检查设备",
        summary="检查设备",
        status="running",
        notification_route="weixin-task",
        created_at=utc_now(),
        updated_at=utc_now(),
    )
    other = running.model_copy(update={"id": "other-task"})
    quick_interactions._tasks = {running.id: running, other.id: other}
    quick_interactions._notification_routes = {
        running.id: route,
        other.id: QuickInteractionWeixinRoute(
            account_id="weixin-account",
            recipient="other@im.wechat",
        ),
    }

    checked = quick_interactions.check_weixin_task_status(
        route,
        operation_id="status-check-1",
        source_ip="100.64.0.21",
    )

    assert checked.outcome == "running"
    assert checked.task is not None
    assert checked.task.id == running.id


def test_status_check_retries_failed_notification_through_original_route(
    tmp_path: Path,
) -> None:
    notifier_started = threading.Event()
    notifier_release = threading.Event()

    def notify(*_args):
        notifier_started.set()
        assert notifier_release.wait(timeout=2)
        return SimpleNamespace(status="sent", error=None)

    notifier = MagicMock(side_effect=notify)
    quick_interactions = manager(tmp_path, completion_notifier=notifier)
    route = QuickInteractionWeixinRoute(
        account_id="weixin-account",
        recipient="owner@im.wechat",
    )
    task = QuickInteractionTask(
        id="ended-task",
        session_id="session-1",
        prompt="检查设备",
        summary="检查设备",
        status="succeeded",
        result="设备正常",
        notification_status="failed",
        notification_route="weixin-task",
        created_at=utc_now(),
        updated_at=utc_now(),
    )
    quick_interactions._tasks[task.id] = task
    quick_interactions._notification_routes[task.id] = route

    checked = quick_interactions.check_weixin_task_status(
        route,
        operation_id="status-check-2",
        source_ip="100.64.0.21",
    )

    assert checked.outcome == "notification_queued"
    assert checked.task is not None
    assert checked.task.notification_status in {"pending", "sending"}
    assert notifier_started.wait(timeout=1)
    assert quick_interactions.get(task.id).notification_status == "sending"
    notifier_release.set()
    for _attempt in range(100):
        if quick_interactions.get(task.id).notification_status == "sent":
            break
        threading.Event().wait(0.01)
    assert quick_interactions.get(task.id).notification_status == "sent"
    notification_task, notification_route = notifier.call_args.args
    assert notification_task.id == task.id
    assert notification_route == route


def test_status_check_does_not_duplicate_notification_in_progress(
    tmp_path: Path,
) -> None:
    notifier = MagicMock(return_value=SimpleNamespace(status="sent", error=None))
    quick_interactions = manager(tmp_path, completion_notifier=notifier)
    route = QuickInteractionWeixinRoute(
        account_id="weixin-account",
        recipient="owner@im.wechat",
    )
    task = QuickInteractionTask(
        id="sending-task",
        session_id="session-1",
        prompt="检查设备",
        status="succeeded",
        result="设备正常",
        notification_status="sending",
        notification_route="weixin-task",
        created_at=utc_now(),
        updated_at=utc_now(),
    )
    quick_interactions._tasks[task.id] = task
    quick_interactions._notification_routes[task.id] = route

    checked = quick_interactions.check_weixin_task_status(
        route,
        operation_id="status-check-3",
        source_ip="100.64.0.21",
    )

    assert checked.outcome == "notification_sending"
    notifier.assert_not_called()


def test_status_check_restores_failed_state_when_notification_thread_cannot_start(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    notifier = MagicMock(return_value=SimpleNamespace(status="sent", error=None))
    quick_interactions = manager(tmp_path, completion_notifier=notifier)
    route = QuickInteractionWeixinRoute(
        account_id="weixin-account",
        recipient="owner@im.wechat",
    )
    task = QuickInteractionTask(
        id="thread-failure-task",
        session_id="session-1",
        prompt="检查设备",
        status="succeeded",
        result="设备正常",
        notification_status="failed",
        notification_route="weixin-task",
        created_at=utc_now(),
        updated_at=utc_now(),
    )
    quick_interactions._tasks[task.id] = task
    quick_interactions._notification_routes[task.id] = route

    thread = MagicMock()
    thread.start.side_effect = RuntimeError("cannot start")
    monkeypatch.setattr(
        "app.codex.quick_interactions.threading.Thread",
        MagicMock(return_value=thread),
    )

    checked = quick_interactions.check_weixin_task_status(
        route,
        operation_id="status-check-thread-failure",
        source_ip="100.64.0.21",
    )

    assert checked.outcome == "notification_failed"
    assert quick_interactions.get(task.id).notification_status == "failed"
    assert quick_interactions.get(task.id).notification_error == "微信通知线程未能启动。"
    notifier.assert_not_called()


def test_status_check_ignores_sent_and_other_sender_tasks(tmp_path: Path) -> None:
    quick_interactions = manager(tmp_path)
    route = QuickInteractionWeixinRoute(
        account_id="weixin-account",
        recipient="owner@im.wechat",
    )
    task = QuickInteractionTask(
        id="sent-task",
        session_id="session-1",
        prompt="检查设备",
        status="succeeded",
        result="设备正常",
        notification_status="sent",
        notification_route="weixin-task",
        created_at=utc_now(),
        updated_at=utc_now(),
    )
    quick_interactions._tasks[task.id] = task
    quick_interactions._notification_routes[task.id] = route

    checked = quick_interactions.check_weixin_task_status(
        route,
        operation_id="status-check-4",
        source_ip="100.64.0.21",
    )

    assert checked.outcome == "empty"
    assert checked.task is None


def test_weixin_notification_route_is_private_and_survives_restart(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first = manager(tmp_path)
    thread = MagicMock()
    monkeypatch.setattr(
        "app.codex.quick_interactions.threading.Thread",
        MagicMock(return_value=thread),
    )
    route = QuickInteractionWeixinRoute(
        account_id="weixin-account",
        recipient="owner@im.wechat",
    )

    task = first.submit(
        "session-1",
        "检查设备",
        operation_id="operation-1",
        source_ip="100.64.0.1",
        notification_route=route,
    )

    public_payload = task.model_dump(mode="json")
    assert public_payload["notification_route"] == "weixin-task"
    assert "weixin-account" not in json.dumps(public_payload)
    persisted = json.loads(first.path.read_text(encoding="utf-8"))
    assert persisted[0]["_notification_route"] == route.model_dump(mode="json")

    reloaded = manager(tmp_path)
    assert reloaded.get(task.id).notification_route == "weixin-task"
    assert reloaded._notification_routes[task.id] == route

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
    assert persisted[0]["error"] == "服务重启导致正在执行的任务中断，请重新提交任务。"
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


def test_local_history_never_prunes_pending_restart_task_or_private_route(
    tmp_path: Path,
) -> None:
    quick_interactions = manager(tmp_path)
    pending = QuickInteractionTask(
        id="pending-restart",
        session_id="session-pending",
        prompt="等待重启",
        status="succeeded",
        result="已安排重启。",
        notification_route="weixin-task",
        deferred_restart_status="pending",
        created_at=utc_now() - timedelta(days=30),
        updated_at=utc_now() - timedelta(days=30),
    )
    completed = [
        QuickInteractionTask(
            id=f"completed-{index}",
            session_id="session-completed",
            prompt=f"完成任务 {index}",
            status="succeeded",
            result="完成",
            created_at=utc_now() + timedelta(minutes=index),
            updated_at=utc_now() + timedelta(minutes=index),
        )
        for index in range(30)
    ]
    route = QuickInteractionWeixinRoute(
        account_id="weixin-account",
        recipient="owner@im.wechat",
    )
    context = QuickInteractionDeferredRestartContext(
        operation_id="operation-1:restart",
        coordinator_operation_id="operation-1:restart",
        source_ip="100.64.0.1",
    )
    quick_interactions._tasks = {
        task.id: task for task in [pending, *completed]
    }
    quick_interactions._notification_routes[pending.id] = route
    quick_interactions._deferred_restart_contexts[pending.id] = context

    quick_interactions._write()

    assert pending.id in quick_interactions._tasks
    assert quick_interactions._notification_routes[pending.id] == route
    assert quick_interactions._deferred_restart_contexts[pending.id] == context


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


def test_submit_rejects_active_native_writer(tmp_path: Path) -> None:
    quick_interactions = manager(tmp_path)
    quick_interactions.codex_manager.has_active_writer.return_value = True

    with pytest.raises(ApiError) as error:
        quick_interactions.submit(
            "session-1",
            "检查状态",
            operation_id="operation-1",
            source_ip="127.0.0.1",
        )

    assert error.value.code == "quick_interaction_writer_active"


def test_active_writer_runtime_error_is_recognized() -> None:
    assert QuickInteractionManager._is_active_writer_error(
        "thread-store conflict: thread abc already has an active writer"
    ) is True
    assert QuickInteractionManager._is_active_writer_error(
        "unrelated Codex failure"
    ) is False


def test_submit_rejects_new_task_while_restart_is_pending(tmp_path: Path) -> None:
    deferred_restart = MagicMock()
    deferred_restart.pending.return_value = True
    quick_interactions = manager(
        tmp_path,
        deferred_restart=deferred_restart,
    )

    with pytest.raises(ApiError) as error:
        quick_interactions.submit(
            "session-1",
            "检查状态",
            operation_id="operation-1",
            source_ip="127.0.0.1",
        )

    assert error.value.code == "chub_restart_pending"
    quick_interactions.codex_manager.prepare_quick_interaction.assert_not_called()


def test_deferred_restart_ready_waits_for_tasks_and_notifications(
    tmp_path: Path,
) -> None:
    quick_interactions = manager(tmp_path)
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
    quick_interactions._tasks[task.id] = task

    assert quick_interactions.deferred_restart_ready() is False
    task.notification_status = "sent"
    quick_interactions._active_task_ids.add(task.id)
    assert quick_interactions.deferred_restart_ready() is False
    quick_interactions._active_task_ids.clear()
    assert quick_interactions.deferred_restart_ready() is True


def test_deferred_restart_completion_distinguishes_automatic_and_manual(
    tmp_path: Path,
) -> None:
    quick_interactions = manager(tmp_path)
    task = QuickInteractionTask(
        id="task-1",
        session_id="session-1",
        prompt="重启服务",
        status="succeeded",
        result="已安排重启。",
        deferred_restart_status="pending",
        deferred_restart_updated_at=utc_now(),
        created_at=utc_now(),
        updated_at=utc_now(),
    )
    quick_interactions._tasks[task.id] = task
    completed_at = utc_now()

    quick_interactions.record_deferred_restart_completion(
        "operation-1:restart",
        task.id,
        "succeeded",
        completed_at,
    )
    completed = quick_interactions.get(task.id)
    assert completed.deferred_restart_status == "succeeded"
    assert completed.deferred_restart_updated_at == completed_at
    assert completed.result == "已安排重启。"

    persisted = json.loads(quick_interactions.path.read_text(encoding="utf-8"))
    assert persisted[0]["deferred_restart_status"] == "succeeded"

    quick_interactions.record_deferred_restart_completion(
        "operation-1:restart",
        task.id,
        "cleared",
        utc_now(),
    )
    assert quick_interactions.get(task.id).deferred_restart_status == "succeeded"

    manual_task = task.model_copy(
        update={
            "id": "task-2",
            "deferred_restart_status": "pending",
        }
    )
    quick_interactions._tasks[manual_task.id] = manual_task
    quick_interactions.record_deferred_restart_completion(
        "operation-2:restart",
        manual_task.id,
        "cleared",
        utc_now(),
    )
    assert quick_interactions.get(manual_task.id).deferred_restart_status == "cleared"


def test_deferred_restart_completion_notifies_only_coalesced_weixin_task(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    notifier = MagicMock(return_value=SimpleNamespace(status="sent", error=None))
    quick_interactions = manager(tmp_path, restart_notifier=notifier)
    now = utc_now()
    page_task = QuickInteractionTask(
        id="page-task",
        session_id="page-session",
        prompt="页面重启",
        status="succeeded",
        result="已安排重启。",
        deferred_restart_status="pending",
        created_at=now,
        updated_at=now,
    )
    weixin_task = page_task.model_copy(
        update={
            "id": "weixin-task",
            "session_id": "weixin-session",
            "prompt": "微信重启",
            "notification_route": "weixin-task",
        }
    )
    quick_interactions._tasks = {
        page_task.id: page_task,
        weixin_task.id: weixin_task,
    }
    quick_interactions._deferred_restart_contexts = {
        page_task.id: QuickInteractionDeferredRestartContext(
            operation_id="page-operation:restart",
            coordinator_operation_id="page-operation:restart",
            source_ip="100.64.0.1",
        ),
        weixin_task.id: QuickInteractionDeferredRestartContext(
            operation_id="weixin-operation:restart",
            coordinator_operation_id="page-operation:restart",
            source_ip="100.64.0.2",
        ),
    }
    route = QuickInteractionWeixinRoute(
        account_id="weixin-account",
        recipient="owner@im.wechat",
    )
    quick_interactions._notification_routes[weixin_task.id] = route

    class ImmediateThread:
        def __init__(self, *, target, args, daemon, name=None):
            self.target = target
            self.args = args

        def start(self) -> None:
            self.target(*self.args)

    monkeypatch.setattr(
        "app.codex.quick_interactions.threading.Thread",
        ImmediateThread,
    )

    quick_interactions.record_deferred_restart_started(
        "page-operation:restart",
        page_task.id,
        now,
    )
    quick_interactions.record_deferred_restart_completion(
        "page-operation:restart",
        page_task.id,
        "succeeded",
        now,
    )

    assert quick_interactions.get(page_task.id).deferred_restart_status == "succeeded"
    assert (
        quick_interactions.get(page_task.id).deferred_restart_notification_status
        is None
    )
    completed_weixin = quick_interactions.get(weixin_task.id)
    assert completed_weixin.deferred_restart_status == "succeeded"
    assert completed_weixin.deferred_restart_notification_status == "sent"
    notifier.assert_called_once()
    assert notifier.call_args.args[1] == route
    assert notifier.call_args.args[2] == "succeeded"
    public_payload = completed_weixin.model_dump(mode="json")
    assert "coordinator_operation_id" not in public_payload
    assert "owner@im.wechat" not in json.dumps(public_payload)
    persisted = json.loads(quick_interactions.path.read_text(encoding="utf-8"))
    persisted_weixin = next(item for item in persisted if item["id"] == weixin_task.id)
    assert persisted_weixin["_notification_route"] == route.model_dump(mode="json")
    assert persisted_weixin["_deferred_restart_context"][
        "coordinator_operation_id"
    ] == "page-operation:restart"


def test_restart_notification_interrupted_while_sending_is_not_retried(
    tmp_path: Path,
) -> None:
    state = tmp_path / "quick-interactions.json"
    task = QuickInteractionTask(
        id="task-1",
        session_id="session-1",
        prompt="重启",
        status="succeeded",
        result="完成",
        notification_route="weixin-task",
        deferred_restart_status="succeeded",
        deferred_restart_notification_status="sending",
        created_at=utc_now(),
        updated_at=utc_now(),
    )
    state.write_text(json.dumps([task.model_dump(mode="json")]), encoding="utf-8")

    quick_interactions = manager(tmp_path)

    recovered = quick_interactions.get(task.id)
    assert recovered.deferred_restart_notification_status == "failed"
    assert recovered.deferred_restart_notification_error == (
        "服务重启时微信重启通知未完成。"
    )
    assert quick_interactions.has_pending_deferred_restart_notifications() is False


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


def test_successful_task_registers_script_restart_and_appends_result(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    deferred_restart = MagicMock()
    deferred_restart.pending.return_value = False
    deferred_restart.request.return_value = SimpleNamespace(
        operation_id="operation-1:restart",
        created=True,
    )
    quick_interactions = manager(
        tmp_path,
        deferred_restart=deferred_restart,
    )
    session = quick_interactions.codex_manager.get_session.return_value
    task = QuickInteractionTask(
        id="task-1",
        session_id=session.id,
        prompt="修改配置并重启",
        status="requested",
        created_at=utc_now(),
        updated_at=utc_now(),
    )
    quick_interactions._tasks[task.id] = task
    quick_interactions._running_sessions.add(session.id)
    quick_interactions._active_task_ids.add(task.id)
    quick_interactions._task_done_events[task.id] = threading.Event()
    quick_interactions._operations[task.id] = ("operation-1", "127.0.0.1")

    class Process:
        returncode = 0
        pid = 12345

        def __init__(self, command, env) -> None:
            self.result_path = Path(command[command.index("--output-last-message") + 1])
            self.request_path = (
                Path(env["CHUB_QUICK_RESTART_DIR"])
                / f"{env['CHUB_QUICK_TASK_ID']}.request"
            )

        def communicate(self, **_kwargs) -> None:
            self.result_path.write_text("功能已完成。", encoding="utf-8")
            self.request_path.touch()

    monkeypatch.setattr(
        "app.codex.quick_interactions.subprocess.Popen",
        lambda command, **kwargs: Process(command, kwargs["env"]),
    )

    quick_interactions._run(task.id, session, task.prompt or "")

    finished = quick_interactions.get(task.id)
    assert finished.status == "succeeded"
    assert finished.result == (
        "功能已完成。\n\n本次处理已完成，即将重启 Chub 服务。"
    )
    assert finished.deferred_restart_status == "pending"
    assert finished.deferred_restart_updated_at is not None
    deferred_restart.request.assert_called_once_with(
        operation_id="operation-1:restart",
        task_id="task-1",
        source_ip="127.0.0.1",
    )
    deferred_restart.maybe_schedule.assert_called()


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


def test_close_marks_active_task_as_interrupted_before_killing_process(
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
    quick_interactions._tasks[task.id] = task
    quick_interactions._active_task_ids.add(task.id)
    quick_interactions._processes[task.id] = process
    kill = MagicMock()
    monkeypatch.setattr(quick_interactions, "_kill_process", kill)

    quick_interactions.close()

    assert quick_interactions.get(task.id).status == "failed"
    assert quick_interactions.get(task.id).error == (
        "服务重启导致正在执行的任务中断，请重新提交任务。"
    )
    persisted = json.loads(quick_interactions.path.read_text(encoding="utf-8"))
    assert persisted[0]["error"] == task.error
    kill.assert_called_once_with(process)
