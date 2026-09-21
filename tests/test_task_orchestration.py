import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.ai_interactions.models import QuickInteractionTask, QuickInteractionWeixinRoute
from app.ai_interactions.task_orchestration import (
    TaskOrchestrationDispatcher,
    TaskOrchestrationRequest,
    retire_weixin_refinement_state,
)
from app.ai_session.models import utc_now
from app.core.response import ApiError
from tests.openclaw_weixin_chub_mode_helpers import configured_manager, delivery_route


class _QuickInteractions:
    def __init__(self) -> None:
        self.calls = []
        self.tasks = {}
        self.operations = {}
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
    assert dispatcher._state.requests[0].prompt == "active prompt"


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
