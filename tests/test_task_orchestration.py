import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from app.ai_interactions.models import (
    PromptOptimizationVersions,
    QuickInteractionTask,
    QuickInteractionWeixinRoute,
)
from app.ai_interactions.task_orchestration import (
    PromptOptimizerStageSelection,
    TaskOrchestrationDispatcher,
    TaskOrchestrationRequest,
    retire_weixin_refinement_state,
)
from app.ai_session.models import utc_now
from app.core.response import ApiError
from tests.openclaw_weixin_chub_mode_helpers import configured_manager, delivery_route


def _read_optimization_result(result):
    payload = json.loads(result)
    return PromptOptimizationVersions(
        chinese=payload["optimized_prompt_zh"],
        english=payload["optimized_prompt_en"],
    )


def _optimized_result(chinese, english):
    return json.dumps(
        {"optimized_prompt_zh": chinese, "optimized_prompt_en": english},
        ensure_ascii=False,
    )


@pytest.mark.parametrize("entry", ["web", "weixin"])
@pytest.mark.parametrize("finish", ["success", "cancel"])
def test_auto_uses_one_internal_task_and_one_public_main_task_after_recovery(tmp_path, entry, finish):
    from tests.test_quick_interactions import manager as quick_manager

    quick = quick_manager(tmp_path)
    quick._start_worker_observer = MagicMock()
    child = QuickInteractionTask(id="internal-task", session_id="internal-session", prompt="optimize",
                                status="running", created_at=utc_now(), updated_at=utc_now())
    use_case = SimpleNamespace(
        execution_snapshot=lambda: {"runtime_id": "codex", "implementation_id": "codex-runtime-dev",
                                    "model": None, "reasoning_effort": None},
        restore_session=MagicMock(return_value="internal-session"),
        submit=MagicMock(return_value=child),
        get=lambda _id: child,
        cancel=MagicMock(),
    )
    descriptor = SimpleNamespace(optimization_result_reader=_read_optimization_result)
    selection = PromptOptimizerStageSelection(enabled=True, mode="auto", implementation_ref="development:optimizer+abc",
        stage_runner=lambda mode, prompt: prompt, optimization_prompt_builder=lambda prompt: "optimize",
        optimization_result_reader=descriptor.optimization_result_reader)
    path = tmp_path / "orchestration-source.json"
    dispatcher = TaskOrchestrationDispatcher(path, quick)
    dispatcher.configure_optimization(use_case, lambda _ref: descriptor)
    dispatcher.set_prompt_optimizer_stage_provider(lambda _entry: selection)
    parent = dispatcher._submit(entry=entry, idempotency_key="a" * 64, session_id="session-1", prompt="original",
                                operation_id="parent-op", source_ip="127.0.0.1", notification_route=None)
    request_id = dispatcher._state.requests[0].id
    dispatcher._advance_optimization(request_id)
    assert parent.orchestration_pending
    assert use_case.submit.call_count == 1
    quick._worker_call.assert_not_called()

    # Current mode/enablement changes cannot replace the accepted stage snapshot.
    restored = TaskOrchestrationDispatcher(path, quick)
    restored.configure_optimization(use_case, lambda _ref: descriptor)
    restored.set_prompt_optimizer_stage_provider(lambda _entry: PromptOptimizerStageSelection())
    if finish == "cancel":
        assert quick.cancel_task(parent.id)
        restored._advance_optimization(request_id)
        use_case.cancel.assert_called_once_with(child.id)
        assert restored._state.requests[0].internal_cleanup_complete
        quick._worker_call.assert_not_called()
        return
    child.status = "succeeded"
    child.result = _optimized_result("优化版本", "Optimized version")
    restored._advance_optimization(request_id)
    restored._advance_optimization(request_id)
    assert use_case.restore_session.call_count == 1
    assert use_case.submit.call_count == 1
    assert quick.get(parent.id).prompt == "original"
    assert quick.get(parent.id).execution_prompt == "优化版本"
    assert quick.get(parent.id).prompt_optimization_result.chinese == "优化版本"
    assert quick.get(parent.id).prompt_optimization_result.english == "Optimized version"
    assert quick._worker_call.call_count == 1

    # Once the physical task reaches a terminal state, the dispatcher no longer
    # needs either optimized prompt in its private orchestration checkpoint.
    restored.record_task_finished(quick.get(parent.id).model_copy(update={"status": "succeeded"}))
    assert restored._state.requests[0].optimized_prompt is None
    assert restored._state.requests[0].optimized_prompt_en is None


def test_auto_requests_reuse_one_session_and_queue_while_optimizer_task_runs(tmp_path):
    from tests.test_quick_interactions import manager as quick_manager

    quick = quick_manager(tmp_path)
    children = {}
    use_case = SimpleNamespace(
        execution_snapshot=lambda: {"runtime_id": "codex", "implementation_id": "codex-runtime-dev",
                                    "model": None, "reasoning_effort": None},
        restore_session=lambda *_args: "shared-optimizer-session",
        submit=lambda session_id, prompt, operation_id: children.setdefault(
            operation_id,
            QuickInteractionTask(id=f"child-{len(children) + 1}", session_id=session_id, prompt=prompt,
                                 status="running", created_at=utc_now(), updated_at=utc_now()),
        ),
        get=lambda task_id: next(child for child in children.values() if child.id == task_id),
        find_for_operation=lambda operation_id: children.get(operation_id),
        cancel=lambda _task_id: None,
    )
    reader = _read_optimization_result
    descriptor = SimpleNamespace(optimization_result_reader=reader)
    dispatcher = TaskOrchestrationDispatcher(tmp_path / "source.json", quick)
    dispatcher.configure_optimization(use_case, lambda _ref: descriptor)
    dispatcher.set_prompt_optimizer_stage_provider(lambda _entry: PromptOptimizerStageSelection(
        enabled=True, mode="auto", implementation_ref="development:optimizer+abc",
        stage_runner=lambda _mode, prompt: prompt,
        optimization_prompt_builder=lambda prompt: f"optimize {prompt}",
        optimization_result_reader=reader,
    ))
    quick.set_task_finished_handler(dispatcher.record_task_finished)
    first = dispatcher.submit_web(session_id="target-1", prompt="first", request_id="first",
                                  operation_id="parent-1", source_ip="127.0.0.1")
    second = dispatcher.submit_web(session_id="target-2", prompt="second", request_id="second",
                                   operation_id="parent-2", source_ip="127.0.0.1")
    first_request, second_request = dispatcher._state.requests

    dispatcher._advance_optimization(first_request.id)
    dispatcher._advance_optimization(second_request.id)

    assert first_request.internal_session_id == second_request.internal_session_id == "shared-optimizer-session"
    assert len(children) == 1
    assert second_request.internal_task_id is None
    assert first.orchestration_pending and second.orchestration_pending

    first_child = next(iter(children.values()))
    assert first_child.prompt == "optimize first"
    first_child.status = "succeeded"
    first_child.result = _optimized_result("优化后的第一个任务", "First optimized task")
    dispatcher._advance_optimization(first_request.id)
    dispatcher._advance_optimization(second_request.id)

    assert len(children) == 2
    assert second_request.internal_task_id == "child-2"
    second_child = next(child for child in children.values() if child.id == second_request.internal_task_id)
    assert second_child.prompt == "optimize second"
    assert quick.get(first.id).orchestration_pending is False
    assert quick.get(second.id).orchestration_pending is True


def test_auto_web_and_weixin_share_optimizer_session_but_keep_task_routes_separate(tmp_path):
    from tests.test_quick_interactions import manager as quick_manager

    route = delivery_route(account_id="weixin-test", recipient="owner-test@im.wechat")
    quick = quick_manager(tmp_path)
    quick._start_worker_observer = MagicMock()
    quick.prompt_optimization_notifier = MagicMock(
        return_value=SimpleNamespace(status="sent", error=None)
    )
    quick._start_prompt_optimization_notification = MagicMock()
    children: dict[str, QuickInteractionTask] = {}
    session_ids: list[str] = []

    def restore_session(_stage_call_id, _snapshot):
        session_ids.append("shared-optimizer-session")
        return "shared-optimizer-session"

    def submit_child(session_id, prompt, operation_id):
        child = QuickInteractionTask(
            id=f"child-{len(children) + 1}",
            session_id=session_id,
            prompt=prompt,
            status="running",
            created_at=utc_now(),
            updated_at=utc_now(),
        )
        children[operation_id] = child
        return child

    use_case = SimpleNamespace(
        execution_snapshot=lambda: {"runtime_id": "codex", "implementation_id": "codex-runtime-dev"},
        restore_session=restore_session,
        submit=submit_child,
        find_for_operation=lambda operation_id: children.get(operation_id),
        get=lambda task_id: next(child for child in children.values() if child.id == task_id),
        cancel=lambda _task_id: None,
    )
    reader = _read_optimization_result
    descriptor = SimpleNamespace(optimization_result_reader=reader)
    dispatcher = TaskOrchestrationDispatcher(tmp_path / "orchestration.json", quick)
    dispatcher.configure_optimization(use_case, lambda _ref: descriptor)
    dispatcher.set_prompt_optimizer_stage_provider(
        lambda _entry: PromptOptimizerStageSelection(
            enabled=True,
            mode="auto",
            implementation_ref="development:optimizer+abc",
            stage_runner=lambda _mode, prompt: prompt,
            optimization_prompt_builder=lambda prompt: f"optimize:{prompt}",
            optimization_result_reader=reader,
        )
    )

    web_task = dispatcher.submit_web(
        session_id="web-target",
        prompt="web-only requirement",
        request_id="web-request",
        operation_id="web-parent",
        source_ip="127.0.0.1",
    )
    weixin_task = dispatcher.submit_weixin(
        message_id="weixin-message",
        route_fingerprint="b" * 64,
        session_id="weixin-target",
        prompt="微信专属需求",
        operation_id="weixin-parent",
        source_ip="127.0.0.1",
        notification_route=route,
        summary_max_chars=48,
        summary_max_width=24,
    )
    web_request, weixin_request = dispatcher._state.requests

    dispatcher._advance_optimization(web_request.id)
    dispatcher._advance_optimization(weixin_request.id)
    assert web_request.internal_session_id == weixin_request.internal_session_id == "shared-optimizer-session"
    assert len(children) == 1
    assert next(iter(children.values())).prompt == "optimize:web-only requirement"
    assert quick._worker_call.call_count == 0
    assert quick.get(web_task.id).notification_route == "default"
    assert quick.get(weixin_task.id).notification_route == "weixin-task"
    assert quick._notification_routes[weixin_task.id] == route

    first_child = next(iter(children.values()))
    first_child.status = "succeeded"
    first_child.result = _optimized_result("优化后的 Web 任务", "Optimized Web task")
    dispatcher._advance_optimization(web_request.id)
    dispatcher._advance_optimization(weixin_request.id)
    assert len(children) == 2
    assert children[weixin_request.stage_call_id].prompt == "optimize:微信专属需求"
    assert quick.get(web_task.id).execution_prompt == "优化后的 Web 任务"
    assert quick.get(web_task.id).prompt_optimization_result.english == "Optimized Web task"
    assert quick.get(weixin_task.id).execution_prompt is None

    second_child = children[weixin_request.stage_call_id]
    second_child.status = "succeeded"
    second_child.result = _optimized_result("优化后的微信任务", "Optimized WeChat task")
    dispatcher._advance_optimization(weixin_request.id)
    assert quick.get(weixin_task.id).execution_prompt == "优化后的微信任务"
    assert quick.get(weixin_task.id).prompt_optimization_result.english == "Optimized WeChat task"
    assert quick.get(web_task.id).prompt_optimization_notification_status == "skipped"
    assert quick.get(weixin_task.id).prompt_optimization_notification_status == "pending"
    quick._start_prompt_optimization_notification.assert_called_once()
    assert quick._start_prompt_optimization_notification.call_args.args[0] == weixin_task.id
    assert quick._worker_call.call_count == 2
    assert session_ids == ["shared-optimizer-session", "shared-optimizer-session"]
    assert quick._notification_routes[weixin_task.id] == route


@pytest.mark.parametrize("result", ["", "not json", '{"optimized_prompt_zh":""}',
                                    _optimized_result("合法中文", "x" * 8001),
                                    '{"optimized_prompt_zh":"只有中文版本"}'])
def test_auto_invalid_result_falls_back_to_original_main_submission(tmp_path, result):
    from tests.test_quick_interactions import manager as quick_manager

    quick = quick_manager(tmp_path)
    child = QuickInteractionTask(id="child", session_id="internal-session", status="succeeded", result=result,
                                created_at=utc_now(), updated_at=utc_now())
    use_case = SimpleNamespace(execution_snapshot=lambda: {"runtime_id": "codex", "implementation_id": "codex-runtime-dev"},
                              restore_session=lambda *_args: "internal-session", submit=lambda *_args: child,
                              get=lambda _id: child, cancel=lambda _id: None)
    descriptor = SimpleNamespace(optimization_result_reader=_read_optimization_result)
    dispatcher = TaskOrchestrationDispatcher(tmp_path / "source.json", quick)
    dispatcher.configure_optimization(use_case, lambda _ref: descriptor)
    dispatcher.set_prompt_optimizer_stage_provider(lambda _entry: PromptOptimizerStageSelection(
        enabled=True, mode="auto", implementation_ref="development:optimizer+abc", stage_runner=lambda *args: "original",
        optimization_prompt_builder=lambda _prompt: "optimize", optimization_result_reader=descriptor.optimization_result_reader))
    quick.set_task_finished_handler(dispatcher.record_task_finished)
    parent = dispatcher.submit_web(session_id="session-1", prompt="original", request_id="request",
                                   operation_id="parent", source_ip="127.0.0.1")
    dispatcher._advance_optimization(dispatcher._state.requests[0].id)
    task = quick.get(parent.id)
    assert task.orchestration_pending is False
    assert task.execution_prompt == "original"
    assert task.prompt_optimization_warning is not None
    assert dispatcher._state.requests[0].optimization_fallback is True
    submissions = [
        call for call in quick._worker_call.call_args_list
        if call.args[0] == "runtime_task_submit"
    ]
    assert len(submissions) == 1


@pytest.mark.parametrize(
    ("failure_point", "expected_reason"),
    [
        ("stage_selection", "stage_selection_unavailable"),
        ("execution_snapshot", "execution_snapshot_failed"),
        ("instruction_builder", "instruction_generation_failed"),
    ],
)
def test_auto_stage_setup_failures_continue_same_parent_with_original(
    tmp_path, failure_point, expected_reason, caplog
):
    from tests.test_quick_interactions import manager as quick_manager

    quick = quick_manager(tmp_path)

    def execution_snapshot():
        if failure_point == "execution_snapshot":
            raise OSError("private runtime configuration detail")
        return {"runtime_id": "codex", "implementation_id": "codex-runtime-dev"}

    def build_instruction(_prompt):
        if failure_point == "instruction_builder":
            raise RuntimeError("private task content")
        return "optimize"

    use_case = SimpleNamespace(execution_snapshot=execution_snapshot)
    descriptor = SimpleNamespace(optimization_result_reader=_read_optimization_result)
    dispatcher = TaskOrchestrationDispatcher(tmp_path / "source.json", quick)
    dispatcher.configure_optimization(use_case, lambda _ref: descriptor)

    def select_stage(_entry):
        if failure_point == "stage_selection":
            raise RuntimeError("private stage configuration")
        return PromptOptimizerStageSelection(
            enabled=True,
            mode="auto",
            implementation_ref="development:optimizer+abc",
            stage_runner=lambda *_args: "original",
            optimization_prompt_builder=build_instruction,
            optimization_result_reader=descriptor.optimization_result_reader,
        )

    dispatcher.set_prompt_optimizer_stage_provider(select_stage)
    parent = dispatcher.submit_web(
        session_id="session-1",
        prompt="original",
        request_id="request",
        operation_id="parent",
        source_ip="127.0.0.1",
    )

    dispatcher._advance_optimization(dispatcher._state.requests[0].id)

    request = dispatcher._state.requests[0]
    task = quick.get(parent.id)
    submissions = [
        call for call in quick._worker_call.call_args_list
        if call.args[0] == "runtime_task_submit"
    ]
    assert request.task_id == parent.id
    assert request.optimization_fallback is True
    assert request.optimization_failure_reason == expected_reason
    assert task.orchestration_pending is False
    assert task.execution_prompt == "original"
    assert len(submissions) == 1
    assert "private runtime configuration detail" not in caplog.text
    assert "private task content" not in caplog.text
    assert "private stage configuration" not in caplog.text


def test_auto_fallback_marker_persistence_failure_keeps_parent_pending(
    tmp_path, monkeypatch
):
    from tests.test_quick_interactions import manager as quick_manager

    quick = quick_manager(tmp_path)
    child = QuickInteractionTask(
        id="failed-child",
        session_id="internal-session",
        status="failed",
        created_at=utc_now(),
        updated_at=utc_now(),
    )
    use_case = SimpleNamespace(
        execution_snapshot=lambda: {
            "runtime_id": "codex",
            "implementation_id": "codex-runtime-dev",
        },
        restore_session=lambda *_args: "internal-session",
        submit=lambda *_args: child,
        get=lambda _task_id: child,
        cancel=lambda _task_id: None,
    )
    descriptor = SimpleNamespace(optimization_result_reader=_read_optimization_result)
    state_path = tmp_path / "source.json"
    dispatcher = TaskOrchestrationDispatcher(state_path, quick)
    dispatcher.configure_optimization(use_case, lambda _ref: descriptor)
    dispatcher.set_prompt_optimizer_stage_provider(lambda _entry: PromptOptimizerStageSelection(
        enabled=True,
        mode="auto",
        implementation_ref="development:optimizer+abc",
        stage_runner=lambda *_args: "original",
        optimization_prompt_builder=lambda _prompt: "optimize",
        optimization_result_reader=descriptor.optimization_result_reader,
    ))
    parent = dispatcher.submit_web(
        session_id="session-1",
        prompt="original",
        request_id="request",
        operation_id="parent",
        source_ip="127.0.0.1",
    )
    original_write = dispatcher._write

    def fail_fallback_write():
        if any(item.optimization_fallback for item in dispatcher._state.requests):
            dispatcher._state_error = True
            raise OSError("state storage unavailable")
        original_write()

    monkeypatch.setattr(dispatcher, "_write", fail_fallback_write)
    dispatcher._advance_optimization(dispatcher._state.requests[0].id)

    task = quick.get(parent.id)
    persisted = json.loads(dispatcher.path.read_text(encoding="utf-8"))
    submissions = [
        call for call in quick._worker_call.call_args_list
        if call.args[0] == "runtime_task_submit"
    ]
    assert dispatcher._state_error is True
    assert dispatcher._state.requests[0].optimization_fallback is True
    assert persisted["requests"][0]["optimization_fallback"] is False
    assert task.orchestration_pending is True
    assert task.worker_task_id is None
    assert submissions == []

    recovered = TaskOrchestrationDispatcher(dispatcher.path, quick)
    recovered.configure_optimization(use_case, lambda _ref: descriptor)
    recovered._advance_optimization(recovered._state.requests[0].id)

    recovered_task = quick.get(parent.id)
    recovered_submissions = [
        call for call in quick._worker_call.call_args_list
        if call.args[0] == "runtime_task_submit"
    ]
    assert recovered._state.requests[0].id == dispatcher._state.requests[0].id
    assert recovered._state.requests[0].optimization_fallback is True
    assert recovered_task.id == parent.id
    assert recovered_task.execution_prompt == "original"
    assert recovered_task.orchestration_pending is False
    assert len(recovered_submissions) == 1


@pytest.mark.parametrize("failure_point", ["warning", "notice"])
def test_auto_fallback_presentation_failure_does_not_block_original_submission(
    tmp_path, monkeypatch, failure_point
):
    from tests.test_quick_interactions import manager as quick_manager

    quick = quick_manager(tmp_path)
    child = QuickInteractionTask(
        id="failed-child",
        session_id="internal-session",
        status="failed",
        created_at=utc_now(),
        updated_at=utc_now(),
    )
    use_case = SimpleNamespace(
        execution_snapshot=lambda: {
            "runtime_id": "codex",
            "implementation_id": "codex-runtime-dev",
        },
        restore_session=lambda *_args: "internal-session",
        submit=lambda *_args: child,
        get=lambda _task_id: child,
        cancel=lambda _task_id: None,
    )
    descriptor = SimpleNamespace(optimization_result_reader=_read_optimization_result)
    dispatcher = TaskOrchestrationDispatcher(tmp_path / "source.json", quick)
    dispatcher.configure_optimization(use_case, lambda _ref: descriptor)
    dispatcher.set_prompt_optimizer_stage_provider(lambda _entry: PromptOptimizerStageSelection(
        enabled=True,
        mode="auto",
        implementation_ref="development:optimizer+abc",
        stage_runner=lambda *_args: "original",
        optimization_prompt_builder=lambda _prompt: "optimize",
        optimization_result_reader=descriptor.optimization_result_reader,
    ))
    if failure_point == "warning":
        monkeypatch.setattr(
            quick,
            "set_prompt_optimization_warning",
            MagicMock(side_effect=OSError("warning projection unavailable")),
        )
    else:
        monkeypatch.setattr(
            quick,
            "notify_prompt_optimization_completed",
            MagicMock(side_effect=OSError("notification unavailable")),
        )
    parent = dispatcher.submit_web(
        session_id="session-1",
        prompt="original",
        request_id="request",
        operation_id="parent",
        source_ip="127.0.0.1",
    )

    dispatcher._advance_optimization(dispatcher._state.requests[0].id)

    task = quick.get(parent.id)
    submissions = [
        call for call in quick._worker_call.call_args_list
        if call.args[0] == "runtime_task_submit"
    ]
    assert task.orchestration_pending is False
    assert task.execution_prompt == "original"
    assert len(submissions) == 1


def test_auto_deadline_falls_back_and_waits_for_worker_recovery(tmp_path):
    from datetime import timedelta
    from tests.test_quick_interactions import manager as quick_manager

    quick = quick_manager(tmp_path)
    use_case = SimpleNamespace(execution_snapshot=lambda: {"runtime_id": "codex", "implementation_id": "codex-runtime-dev"})
    descriptor = SimpleNamespace(optimization_result_reader=_read_optimization_result)
    dispatcher = TaskOrchestrationDispatcher(tmp_path / "source.json", quick)
    dispatcher.configure_optimization(use_case, lambda _ref: descriptor)
    dispatcher.set_prompt_optimizer_stage_provider(lambda _entry: PromptOptimizerStageSelection(
        enabled=True, mode="auto", implementation_ref="development:optimizer+abc", stage_runner=lambda *args: "original",
        optimization_prompt_builder=lambda _prompt: "optimize", optimization_result_reader=descriptor.optimization_result_reader))
    quick.set_task_finished_handler(dispatcher.record_task_finished)
    parent = dispatcher.submit_web(session_id="session-1", prompt="original", request_id="request",
                                   operation_id="parent", source_ip="127.0.0.1")
    dispatcher._state.requests[0].optimization_deadline = utc_now() - timedelta(seconds=1)
    quick._recovery_ready = False
    request_id = dispatcher._state.requests[0].id
    dispatcher._advance_optimization(request_id)
    task = quick.get(parent.id)
    assert task.status == "requested"
    assert task.orchestration_pending is True
    assert task.prompt_optimization_warning is not None
    quick._worker_call.assert_not_called()

    quick._recovery_ready = True
    dispatcher._advance_optimization(request_id)
    task = quick.get(parent.id)
    assert task.orchestration_pending is False
    assert task.execution_prompt == "original"
    submissions = [
        call for call in quick._worker_call.call_args_list
        if call.args[0] == "runtime_task_submit"
    ]
    assert len(submissions) == 1


def test_concurrent_duplicate_does_not_clear_auto_checkpoint(tmp_path):
    import threading
    from concurrent.futures import ThreadPoolExecutor

    quick = _QuickInteractions()
    entered, release = threading.Event(), threading.Event()
    original_submit = quick.submit

    def delayed_submit(*args, **kwargs):
        entered.set()
        assert release.wait(5)
        return original_submit(*args, **kwargs)

    quick.submit = delayed_submit
    dispatcher = TaskOrchestrationDispatcher(tmp_path / "source.json", quick)
    dispatcher.configure_optimization(SimpleNamespace(execution_snapshot=lambda: {
        "runtime_id": "codex", "implementation_id": "codex-runtime-dev"}), lambda _ref: None)
    dispatcher.set_prompt_optimizer_stage_provider(lambda _entry: PromptOptimizerStageSelection(
        enabled=True, mode="auto", implementation_ref="development:optimizer+abc", stage_runner=lambda *args: "original",
        optimization_prompt_builder=lambda _prompt: "optimize", optimization_result_reader=_read_optimization_result))
    arguments = dict(session_id="session-1", prompt="original", request_id="same-request",
                     operation_id="parent", source_ip="127.0.0.1")
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(dispatcher.submit_web, **arguments)
        try:
            assert entered.wait(5)
            with pytest.raises(ApiError) as error:
                dispatcher.submit_web(**arguments)
            assert error.value.code == "task_orchestration_submission_in_progress"
            assert dispatcher._state.requests[0].optimization_instruction == "optimize"
        finally:
            release.set()
        assert future.result(timeout=5).id == "task-1"
    assert len(quick.calls) == 1


class _QuickInteractions:
    def __init__(self) -> None:
        self.calls = []
        self.tasks = {}
        self.operations = {}
        self.notification_routes = []
        self.raise_after_acceptance = False

    def session_operation_guard(self, _session_id):
        class _Guard:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

        return _Guard()

    def submit(self, session_id, prompt, **kwargs):
        task = QuickInteractionTask(
            id=f"task-{len(self.calls) + 1}",
            session_id=session_id,
            prompt=prompt,
            status="requested",
            created_at=utc_now(),
            updated_at=utc_now(),
        )
        self.calls.append((session_id, prompt))
        self.notification_routes.append(kwargs.get("notification_route"))
        self.tasks[task.id] = task
        self.operations[kwargs["operation_id"]] = task
        if self.raise_after_acceptance:
            raise OSError("response lost after task acceptance")
        return task

    def get(self, task_id):
        try:
            return self.tasks[task_id]
        except KeyError as exc:
            raise ApiError(404, "quick_interaction_not_found", "task unavailable") from exc

    def find_for_operation(self, operation_id):
        task = self.operations.get(operation_id)
        return task.model_copy(deep=True) if task is not None else None


def test_web_request_is_idempotent_and_uses_empty_stage_chain(tmp_path: Path) -> None:
    quick = _QuickInteractions()
    dispatcher = TaskOrchestrationDispatcher(tmp_path / "quick-interactions.json", quick)

    first = dispatcher.submit_web(
        session_id="session-1",
        prompt="hello",
        request_id="11111111-1111-4111-8111-111111111111",
        operation_id="op-1",
        source_ip="127.0.0.1",
    )
    retry = dispatcher.submit_web(
        session_id="session-1",
        prompt="hello",
        request_id="11111111-1111-4111-8111-111111111111",
        operation_id="op-2",
        source_ip="127.0.0.1",
    )

    assert first.id == retry.id
    assert len(quick.calls) == 1
    assert dispatcher._state.requests[0].stage_chain == []
    assert dispatcher._state.requests[0].entry == "web"

    with pytest.raises(ApiError) as error:
        dispatcher.submit_web(
            session_id="session-1",
            prompt="different",
            request_id="11111111-1111-4111-8111-111111111111",
            operation_id="op-3",
            source_ip="127.0.0.1",
        )
    assert error.value.code == "task_orchestration_request_conflict"


def test_web_direct_stage_is_snapshotted_and_submits_original_prompt_once(
    tmp_path: Path,
) -> None:
    quick = _QuickInteractions()
    dispatcher = TaskOrchestrationDispatcher(tmp_path / "quick-interactions.json", quick)
    stage_calls = []
    implementation_ref = "development:chub-task-prompt-optimizer+" + "a" * 64

    def run_stage(mode, prompt):
        stage_calls.append((mode, prompt))
        return prompt

    dispatcher.set_prompt_optimizer_stage_provider(
        lambda entry: PromptOptimizerStageSelection(
            enabled=entry == "web",
            mode="direct" if entry == "web" else None,
            implementation_ref=implementation_ref if entry == "web" else None,
            stage_runner=run_stage if entry == "web" else None,
        )
    )
    prompt = "normalized ordinary task"

    task = dispatcher.submit_web(
        session_id="session-1",
        prompt=prompt,
        request_id="11111111-1111-4111-8111-111111111111",
        operation_id="op-1",
        source_ip="127.0.0.1",
    )

    request = dispatcher._state.requests[0]
    assert task.prompt == prompt
    assert quick.calls == [("session-1", prompt)]
    assert stage_calls == [("direct", prompt)]
    assert request.plugin_enabled is True
    assert request.plugin_mode == "direct"
    assert request.implementation_ref == implementation_ref
    assert request.stage_chain[0].stage_id == "prompt_optimization"
    assert request.stage_chain[0].implementation_ref == implementation_ref
    assert request.cursor == 1

    dispatcher.set_prompt_optimizer_stage_provider(lambda _entry: PromptOptimizerStageSelection())
    replay = dispatcher.submit_web(
        session_id="session-1",
        prompt=prompt,
        request_id="11111111-1111-4111-8111-111111111111",
        operation_id="op-retry",
        source_ip="127.0.0.1",
    )
    assert replay.id == task.id
    assert len(quick.calls) == 1
    assert stage_calls == [("direct", prompt)]
    assert request.stage_chain[0].implementation_ref == implementation_ref


def test_web_auto_stage_setup_failure_falls_back_to_original_prompt(tmp_path: Path) -> None:
    quick = _QuickInteractions()
    dispatcher = TaskOrchestrationDispatcher(tmp_path / "quick-interactions.json", quick)
    dispatcher.set_prompt_optimizer_stage_provider(
        lambda _entry: PromptOptimizerStageSelection(
            enabled=True,
            mode="auto",
            implementation_ref="development:optimizer+" + "b" * 64,
            stage_runner=lambda mode, prompt: prompt,
        )
    )

    task = dispatcher.submit_web(
        session_id="session-1",
        prompt="normalized ordinary task",
        request_id="11111111-1111-4111-8111-111111111111",
        operation_id="op-1",
        source_ip="127.0.0.1",
    )

    request = dispatcher._state.requests[0]
    assert task.id == request.task_id
    assert quick.calls == [("session-1", "normalized ordinary task")]
    assert request.optimization_fallback is True
    assert request.optimization_failure_reason == "stage_unavailable"


def test_web_invalid_direct_stage_fails_without_worker_submission(tmp_path: Path) -> None:
    quick = _QuickInteractions()
    dispatcher = TaskOrchestrationDispatcher(tmp_path / "quick-interactions.json", quick)
    dispatcher.set_prompt_optimizer_stage_provider(
        lambda _entry: PromptOptimizerStageSelection(
            enabled=True,
            mode="direct",
            implementation_ref="development:optimizer+" + "b" * 64,
            stage_runner=lambda _mode, _prompt: "rewritten prompt",
        )
    )

    with pytest.raises(ApiError) as error:
        dispatcher.submit_web(
            session_id="session-1",
            prompt="normalized ordinary task",
            request_id="11111111-1111-4111-8111-111111111111",
            operation_id="op-1",
            source_ip="127.0.0.1",
        )

    assert error.value.code == "task_orchestration_stage_failed"
    assert quick.calls == []
    assert dispatcher._state.requests[0].status == "failed"


def test_weixin_route_stays_outside_generic_request_state(tmp_path: Path) -> None:
    quick = _QuickInteractions()
    dispatcher = TaskOrchestrationDispatcher(tmp_path / "quick-interactions.json", quick)
    route = QuickInteractionWeixinRoute(
        account_id="account-1",
        recipient="user@im.wechat",
    )

    task = dispatcher.submit_weixin(
        message_id="message-1",
        route_fingerprint="a" * 64,
        session_id="session-1",
        prompt="hello",
        operation_id="op-1",
        source_ip="127.0.0.1",
        notification_route=route,
        summary_max_chars=48,
        summary_max_width=24,
    )

    assert task.id == "task-1"
    serialized = (tmp_path / "task-orchestration.json").read_text()
    assert "account-1" not in serialized
    assert "user@im.wechat" not in serialized


def test_weixin_enabled_plugin_uses_direct_stage_without_persisting_route(
    tmp_path: Path,
) -> None:
    quick = _QuickInteractions()
    dispatcher = TaskOrchestrationDispatcher(tmp_path / "quick-interactions.json", quick)
    implementation_ref = "development:chub-task-prompt-optimizer+" + "c" * 64
    stage_calls = []

    dispatcher.set_prompt_optimizer_stage_provider(
        lambda entry: PromptOptimizerStageSelection(
            enabled=True,
            mode="direct",
            implementation_ref=implementation_ref,
            stage_runner=lambda mode, prompt: stage_calls.append((mode, prompt)) or prompt,
        )
    )
    route = QuickInteractionWeixinRoute(
        account_id="account-1",
        recipient="user@im.wechat",
    )
    task = dispatcher.submit_weixin(
        message_id="message-2",
        route_fingerprint="b" * 64,
        session_id="session-1",
        prompt="normalized WeChat task",
        operation_id="weixin-op-1",
        source_ip="127.0.0.1",
        notification_route=route,
        summary_max_chars=48,
        summary_max_width=24,
    )

    request = dispatcher._state.requests[0]
    assert task.prompt == "normalized WeChat task"
    assert quick.calls == [("session-1", "normalized WeChat task")]
    assert quick.notification_routes == [route]
    assert stage_calls == [("direct", "normalized WeChat task")]
    assert request.entry == "weixin"
    assert request.plugin_enabled is True
    assert request.implementation_ref == implementation_ref
    assert request.stage_chain[0].stage_id == "prompt_optimization"
    assert request.cursor == 1
    serialized = (tmp_path / "task-orchestration.json").read_text()
    assert "account-1" not in serialized
    assert "user@im.wechat" not in serialized

    retry = dispatcher.submit_weixin(
        message_id="message-2",
        route_fingerprint="b" * 64,
        session_id="session-1",
        prompt="normalized WeChat task",
        operation_id="weixin-op-retry",
        source_ip="127.0.0.1",
        notification_route=route,
        summary_max_chars=48,
        summary_max_width=24,
    )
    assert retry.id == task.id
    assert len(quick.calls) == 1
    assert len(quick.notification_routes) == 1
    assert len(stage_calls) == 1


def test_submission_response_loss_recovers_by_operation_without_duplicate(
    tmp_path: Path,
) -> None:
    quick = _QuickInteractions()
    quick.raise_after_acceptance = True
    dispatcher = TaskOrchestrationDispatcher(tmp_path / "quick-interactions.json", quick)

    with pytest.raises(OSError):
        dispatcher.submit_web(
            session_id="session-1",
            prompt="hello",
            request_id="11111111-1111-4111-8111-111111111111",
            operation_id="op-1",
            source_ip="127.0.0.1",
        )

    request = dispatcher._state.requests[0]
    assert request.task_id == "task-1"
    assert request.status == "running"
    quick.raise_after_acceptance = False

    replay = dispatcher.submit_web(
        session_id="session-1",
        prompt="hello",
        request_id="11111111-1111-4111-8111-111111111111",
        operation_id="op-2",
        source_ip="127.0.0.1",
    )

    assert replay.id == "task-1"
    assert len(quick.calls) == 1


def test_unlinked_request_without_operation_record_becomes_explicit_failure(
    tmp_path: Path,
) -> None:
    quick = _QuickInteractions()
    dispatcher = TaskOrchestrationDispatcher(tmp_path / "quick-interactions.json", quick)
    now = utc_now()
    dispatcher._state.requests.append(
        TaskOrchestrationRequest(
            id="11111111-1111-4111-8111-111111111111",
            entry="web",
            idempotency_key=dispatcher._fingerprint("web", "session-1", "22222222-2222-4222-8222-222222222222"),
            payload_fingerprint=dispatcher._fingerprint("session-1", "hello"),
            session_id="session-1",
            prompt="hello",
            operation_id="op-missing",
            created_at=now,
            updated_at=now,
        )
    )
    dispatcher._write()

    with pytest.raises(ApiError) as error:
        dispatcher.submit_web(
            session_id="session-1",
            prompt="hello",
            request_id="22222222-2222-4222-8222-222222222222",
            operation_id="op-2",
            source_ip="127.0.0.1",
        )

    assert error.value.code == "task_orchestration_submission_failed"
    assert dispatcher._state.requests[0].status == "failed"


def test_retained_terminal_task_is_not_replayed(tmp_path: Path) -> None:
    quick = _QuickInteractions()
    dispatcher = TaskOrchestrationDispatcher(tmp_path / "quick-interactions.json", quick)
    task = dispatcher.submit_web(
        session_id="session-1",
        prompt="hello",
        request_id="11111111-1111-4111-8111-111111111111",
        operation_id="op-1",
        source_ip="127.0.0.1",
    )
    quick.tasks[task.id] = task.model_copy(update={"status": "succeeded"})
    dispatcher.record_task_finished(quick.tasks[task.id])
    quick.tasks.clear()

    with pytest.raises(ApiError) as error:
        dispatcher.submit_web(
            session_id="session-1",
            prompt="hello",
            request_id="11111111-1111-4111-8111-111111111111",
            operation_id="op-2",
            source_ip="127.0.0.1",
        )

    assert error.value.code == "task_orchestration_retained"
    assert quick.calls == [("session-1", "hello")]


def test_terminal_compaction_preserves_active_requests_and_removes_legacy_prompt(
    tmp_path: Path,
) -> None:
    quick = _QuickInteractions()
    dispatcher = TaskOrchestrationDispatcher(tmp_path / "quick-interactions.json", quick)
    completed = dispatcher.submit_web(
        session_id="session-1",
        prompt="completed prompt",
        request_id="11111111-1111-4111-8111-111111111111",
        operation_id="op-1",
        source_ip="127.0.0.1",
    )
    quick.tasks[completed.id] = completed.model_copy(update={"status": "succeeded"})
    dispatcher.record_task_finished(quick.tasks[completed.id])
    active = dispatcher.submit_web(
        session_id="session-2",
        prompt="active prompt",
        request_id="22222222-2222-4222-8222-222222222222",
        operation_id="op-2",
        source_ip="127.0.0.1",
    )

    removed, active_requests = dispatcher.compact_terminal_records()

    assert removed == 1
    assert active_requests == 1
    assert [request.task_id for request in dispatcher._state.requests] == [active.id]
    assert dispatcher._state.requests[0].prompt is None


def test_loading_legacy_terminal_record_removes_its_prompt(tmp_path: Path) -> None:
    state = tmp_path / "task-orchestration.json"
    now = utc_now()
    state.write_text(
        json.dumps({
            "version": 1,
            "requests": [{
                "id": "11111111-1111-4111-8111-111111111111",
                "entry": "web",
                "idempotency_key": "a" * 64,
                "payload_fingerprint": "b" * 64,
                "session_id": "session-1",
                "prompt": "legacy prompt",
                "stage_chain": [],
                "cursor": 0,
                "operation_id": "op-1",
                "task_id": "task-1",
                "status": "completed",
                "created_at": now.isoformat(),
                "updated_at": now.isoformat(),
            }],
        }),
        encoding="utf-8",
    )

    TaskOrchestrationDispatcher(state, _QuickInteractions())

    assert "legacy prompt" not in state.read_text(encoding="utf-8")


def test_corrupt_state_is_preserved_and_rejects_new_submissions(tmp_path: Path) -> None:
    state = tmp_path / "task-orchestration.json"
    state.write_text("not json", encoding="utf-8")
    dispatcher = TaskOrchestrationDispatcher(state, _QuickInteractions())

    with pytest.raises(ApiError) as error:
        dispatcher.submit_web(
            session_id="session-1",
            prompt="hello",
            request_id="11111111-1111-4111-8111-111111111111",
            operation_id="op-1",
            source_ip="127.0.0.1",
        )

    assert error.value.code == "task_orchestration_state_unavailable"
    assert state.read_text(encoding="utf-8") == "not json"


def test_retirement_discards_old_state_even_when_work_was_non_terminal(tmp_path: Path) -> None:
    weixin_state = tmp_path / "weixin-chub-mode.json"
    translation_state = tmp_path / "weixin-translation.json"
    plugin_state = tmp_path / "plugin-lifecycle.json"
    modules = tmp_path / "weixin-orchestration-modules"
    modules.mkdir()
    (modules / "old.zip").write_text("old")
    weixin_state.write_text(
        '{"version":2,"orchestration_requests":[{"status":"waiting"}],"configuration":{}}'
    )
    translation_state.write_text('{"entries":[{"status":"waiting_confirmation"}]}')
    plugin_state.write_text(
        '{"imports":{"weixin-orchestration":["development:weixin-orchestration"]},'
        '"enabled":{"weixin-orchestration":["development:weixin-orchestration"]},'
        '"metadata":{"weixin-orchestration":{}}}'
    )

    retire_weixin_refinement_state(
        weixin_state_file=weixin_state,
        translation_state_file=translation_state,
        orchestration_modules_dir=modules,
        plugin_lifecycle_file=plugin_state,
        retirement_marker_file=tmp_path / "weixin-refinement-retired-v1.json",
    )

    assert "orchestration_requests" not in weixin_state.read_text()
    assert not translation_state.exists()
    assert not modules.exists()
    assert "weixin-orchestration" not in plugin_state.read_text()


def test_retirement_runs_once_and_invalid_old_state_stays_isolated(tmp_path: Path) -> None:
    marker = tmp_path / "weixin-refinement-retired-v1.json"
    retire_weixin_refinement_state(
        weixin_state_file=tmp_path / "weixin-chub-mode.json",
        translation_state_file=tmp_path / "weixin-translation.json",
        orchestration_modules_dir=tmp_path / "weixin-orchestration-modules",
        plugin_lifecycle_file=tmp_path / "plugin-lifecycle.json",
        retirement_marker_file=marker,
    )
    assert marker.is_file()

    (tmp_path / "weixin-chub-mode.json").write_text("invalid", encoding="utf-8")
    retire_weixin_refinement_state(
        weixin_state_file=tmp_path / "weixin-chub-mode.json",
        translation_state_file=tmp_path / "weixin-translation.json",
        orchestration_modules_dir=tmp_path / "weixin-orchestration-modules",
        plugin_lifecycle_file=tmp_path / "plugin-lifecycle.json",
        retirement_marker_file=marker,
    )
    assert (tmp_path / "weixin-chub-mode.json").read_text(encoding="utf-8") == "invalid"


def test_weixin_entry_submits_once_through_the_shared_dispatcher(settings) -> None:
    manager, _sessions, quick_interactions = configured_manager(settings)

    result = manager.dispatch(
        message_id="message-1",
        prompt="检查设备状态",
        message_type="text",
        correlation_id=None,
        source_ip="127.0.0.1",
        delivery_route=delivery_route(),
    )

    assert result.disposition == "reply"
    assert quick_interactions.submit.call_count == 1
    request = manager.task_orchestrator._state.requests[0]
    assert request.entry == "weixin"
    assert request.stage_chain == []
