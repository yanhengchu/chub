from __future__ import annotations

import json
import stat
import threading
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from app.core.response import ApiError
from app.services.deferred_restart import DeferredRestartCoordinator


def test_deferred_restart_waits_until_ready_and_persists_private_state(
    tmp_path: Path,
) -> None:
    state_file = tmp_path / "state" / "deferred-restart.json"
    restarted = threading.Event()
    ready = False
    coordinator = DeferredRestartCoordinator(
        state_file,
        "instance-1",
        restarted.set,
        grace_seconds=0,
    )
    coordinator.set_ready_check(
        lambda _request: "ready" if ready else "waiting"
    )

    registration = coordinator.request(
        operation_id="operation-1",
        task_id="task-1",
        source_ip="127.0.0.1",
    )
    assert registration.operation_id == "operation-1"
    assert registration.created is True
    assert coordinator.maybe_schedule() is False
    assert stat.S_IMODE(state_file.stat().st_mode) == 0o600
    persisted = json.loads(state_file.read_text(encoding="utf-8"))
    assert persisted["current"]["status"] == "waiting"

    ready = True
    assert coordinator.maybe_schedule() is True
    assert restarted.wait(1)
    assert coordinator.state() is not None
    assert coordinator.state().status == "started"


def test_deferred_restart_coalesces_requests(tmp_path: Path) -> None:
    coordinator = DeferredRestartCoordinator(
        tmp_path / "deferred-restart.json",
        "instance-1",
        MagicMock(),
    )

    first = coordinator.request(
        operation_id="operation-1",
        task_id="task-1",
        source_ip="127.0.0.1",
    )
    second = coordinator.request(
        operation_id="operation-2",
        task_id="task-2",
        source_ip="127.0.0.2",
    )
    assert first.created is True
    assert second.created is False
    assert second.operation_id == "operation-1"
    assert coordinator.state().operation_id == "operation-1"


def test_immediate_restart_without_deferred_request_is_reused_until_failure(
    tmp_path: Path,
) -> None:
    coordinator = DeferredRestartCoordinator(
        tmp_path / "deferred-restart.json",
        "instance-1",
        MagicMock(),
    )

    assert coordinator.begin_immediate_restart() == "launch"
    assert coordinator.begin_immediate_restart() == "in_progress"
    assert coordinator.fail_immediate_restart() is True
    assert coordinator.begin_immediate_restart() == "launch"


def test_request_after_restart_started_is_preserved_for_next_instance(
    tmp_path: Path,
) -> None:
    state_file = tmp_path / "deferred-restart.json"
    restarted = threading.Event()
    first = DeferredRestartCoordinator(
        state_file,
        "instance-1",
        restarted.set,
        grace_seconds=0,
    )
    first.set_ready_check(lambda _request: "ready")
    first.request(
        operation_id="operation-1",
        task_id="task-1",
        source_ip="127.0.0.1",
    )
    assert first.maybe_schedule() is True
    assert restarted.wait(1)

    second_registration = first.request(
        operation_id="operation-2",
        task_id="task-2",
        source_ip="127.0.0.2",
    )
    merged_registration = first.request(
        operation_id="operation-3",
        task_id="task-3",
        source_ip="127.0.0.3",
    )

    assert second_registration.created is True
    assert second_registration.operation_id == "operation-2"
    assert merged_registration.created is False
    assert merged_registration.operation_id == "operation-2"
    assert first.next_state() is not None
    assert first.next_state().requested_task_id == "task-2"

    restarted_again = threading.Event()
    second = DeferredRestartCoordinator(
        state_file,
        "instance-2",
        restarted_again.set,
        grace_seconds=0,
    )
    completion_handler = MagicMock()
    second.set_completion_handler(completion_handler)
    second.set_ready_check(lambda _request: "ready")

    assert second.service_started() is True
    assert second.state() is not None
    assert second.state().operation_id == "operation-2"
    assert second.state().requested_instance_id == "instance-2"
    assert second.next_state() is None
    completion_handler.assert_called_once()
    assert restarted_again.wait(1)
    assert second.state() is not None
    assert second.state().status == "started"


def test_sensitive_task_failure_cancels_waiting_restart(tmp_path: Path) -> None:
    restart = MagicMock()
    coordinator = DeferredRestartCoordinator(
        tmp_path / "deferred-restart.json",
        "instance-1",
        restart,
        grace_seconds=0,
    )
    coordinator.set_ready_check(lambda _request: "sensitive_task_failed")
    completion_handler = MagicMock()
    coordinator.set_completion_handler(completion_handler)
    coordinator.request(
        operation_id="operation-1",
        task_id="task-1",
        source_ip="127.0.0.1",
    )

    with patch("app.services.deferred_restart.write_operation") as write_operation:
        assert coordinator.maybe_schedule() is False

    assert coordinator.pending() is False
    restart.assert_not_called()
    assert completion_handler.call_args.args[:3] == (
        "operation-1",
        "task-1",
        "sensitive_task_failed",
    )
    assert write_operation.call_args.kwargs["status"] == "failed"


def test_request_during_sensitive_completion_is_preserved_as_next(
    tmp_path: Path,
) -> None:
    completion_started = threading.Event()
    release_completion = threading.Event()
    completion_handler = MagicMock()

    def complete(*args) -> None:
        completion_handler(*args)
        completion_started.set()
        assert release_completion.wait(1)

    coordinator = DeferredRestartCoordinator(
        tmp_path / "deferred-restart.json",
        "instance-1",
        MagicMock(),
        grace_seconds=0,
    )
    coordinator.set_ready_check(
        lambda request: (
            "sensitive_task_failed"
            if request.requested_task_id == "task-1"
            else "waiting"
        )
    )
    coordinator.set_completion_handler(complete)
    coordinator.request(
        operation_id="operation-1",
        task_id="task-1",
        source_ip="127.0.0.1",
    )

    cancellation = threading.Thread(target=coordinator.maybe_schedule)
    cancellation.start()
    assert completion_started.wait(1)

    second = coordinator.request(
        operation_id="operation-2",
        task_id="task-2",
        source_ip="127.0.0.2",
    )
    assert second.created is True
    assert second.operation_id == "operation-2"
    assert coordinator.next_state() is not None
    assert coordinator.next_state().requested_task_id == "task-2"

    release_completion.set()
    cancellation.join(1)

    assert not cancellation.is_alive()
    assert coordinator.state() is not None
    assert coordinator.state().requested_task_id == "task-2"
    assert coordinator.next_state() is None
    completion_handler.assert_called_once()


def test_legacy_waiting_state_is_migrated_without_losing_request(
    tmp_path: Path,
) -> None:
    state_file = tmp_path / "deferred-restart.json"
    state_file.write_text(
        json.dumps(
            {
                "version": 1,
                "operation_id": "operation-1",
                "requested_instance_id": "instance-1",
                "requested_task_id": "task-1",
                "source_ip": "127.0.0.1",
                "status": "waiting",
                "requested_at": "2026-08-14T00:00:00Z",
                "updated_at": "2026-08-14T00:00:00Z",
            }
        ),
        encoding="utf-8",
    )

    coordinator = DeferredRestartCoordinator(
        state_file,
        "instance-1",
        MagicMock(),
    )

    assert coordinator.state() is not None
    assert coordinator.state().operation_id == "operation-1"
    assert coordinator.state().status == "waiting"


def test_new_instance_consumes_restart_satisfied_manually(tmp_path: Path) -> None:
    state_file = tmp_path / "deferred-restart.json"
    first = DeferredRestartCoordinator(
        state_file,
        "instance-1",
        MagicMock(),
    )
    first.request(
        operation_id="operation-1",
        task_id="task-1",
        source_ip="127.0.0.1",
    )
    second = DeferredRestartCoordinator(
        state_file,
        "instance-2",
        MagicMock(),
    )
    completion_handler = MagicMock()
    second.set_completion_handler(completion_handler)

    with patch("app.services.deferred_restart.write_operation") as write_operation:
        assert second.service_started() is True

    assert second.pending() is False
    assert not state_file.exists()
    assert write_operation.call_args.kwargs["status"] == "succeeded"
    assert completion_handler.call_args.args[:3] == (
        "operation-1",
        "task-1",
        "cleared",
    )


def test_new_request_before_manual_restart_confirmation_is_not_cleared(
    tmp_path: Path,
) -> None:
    state_file = tmp_path / "deferred-restart.json"
    first = DeferredRestartCoordinator(
        state_file,
        "instance-1",
        MagicMock(),
    )
    first.request(
        operation_id="operation-1",
        task_id="task-1",
        source_ip="127.0.0.1",
    )
    second = DeferredRestartCoordinator(
        state_file,
        "instance-2",
        MagicMock(),
    )

    registration = second.request(
        operation_id="operation-2",
        task_id="task-2",
        source_ip="127.0.0.2",
    )
    assert registration.created is True
    assert second.next_state() is not None
    assert second.next_state().operation_id == "operation-2"

    completion_handler = MagicMock()
    second.set_completion_handler(completion_handler)
    assert second.service_started() is True

    assert second.state() is not None
    assert second.state().operation_id == "operation-2"
    assert second.state().requested_instance_id == "instance-2"
    assert completion_handler.call_args.args[:3] == (
        "operation-1",
        "task-1",
        "cleared",
    )


def test_new_instance_reports_automatic_restart_completion(tmp_path: Path) -> None:
    state_file = tmp_path / "deferred-restart.json"
    restarted = threading.Event()
    first = DeferredRestartCoordinator(
        state_file,
        "instance-1",
        restarted.set,
        grace_seconds=0,
    )
    first.set_ready_check(lambda _request: "ready")
    started_handler = MagicMock()
    first.set_started_handler(started_handler)
    first.request(
        operation_id="operation-1",
        task_id="task-1",
        source_ip="127.0.0.1",
    )
    assert first.maybe_schedule() is True
    assert restarted.wait(1)

    second = DeferredRestartCoordinator(
        state_file,
        "instance-2",
        MagicMock(),
    )
    completion_handler = MagicMock()
    second.set_completion_handler(completion_handler)

    assert second.service_started() is True

    assert started_handler.call_args.args[:2] == ("operation-1", "task-1")
    assert completion_handler.call_args.args[:3] == (
        "operation-1",
        "task-1",
        "succeeded",
    )
    assert not state_file.exists()


def test_new_instance_keeps_running_when_satisfied_state_cannot_be_removed(
    tmp_path: Path,
) -> None:
    state_file = tmp_path / "deferred-restart.json"
    first = DeferredRestartCoordinator(
        state_file,
        "instance-1",
        MagicMock(),
    )
    first.request(
        operation_id="operation-1",
        task_id="task-1",
        source_ip="127.0.0.1",
    )
    second = DeferredRestartCoordinator(
        state_file,
        "instance-2",
        MagicMock(),
    )

    with patch.object(second, "_delete_state", side_effect=PermissionError):
        assert second.service_started() is False

    assert second.pending() is True
    assert state_file.exists()


def test_restart_start_failure_clears_gate(tmp_path: Path) -> None:
    state_file = tmp_path / "deferred-restart.json"
    failed = threading.Event()

    def fail_restart() -> None:
        failed.set()
        raise OSError("restart unavailable")

    coordinator = DeferredRestartCoordinator(
        state_file,
        "instance-1",
        fail_restart,
        grace_seconds=0,
    )
    coordinator.set_ready_check(lambda _request: "ready")
    completion_handler = MagicMock()
    coordinator.set_completion_handler(completion_handler)
    coordinator.request(
        operation_id="operation-1",
        task_id="task-1",
        source_ip="127.0.0.1",
    )

    assert coordinator.maybe_schedule() is True
    assert failed.wait(1)
    for _ in range(100):
        if not coordinator.pending():
            break
        threading.Event().wait(0.01)

    assert coordinator.pending() is False
    assert not state_file.exists()
    assert completion_handler.call_args.args[:3] == (
        "operation-1",
        "task-1",
        "start_failed",
    )


def test_restart_process_async_failure_clears_gate(tmp_path: Path) -> None:
    state_file = tmp_path / "deferred-restart.json"
    process = MagicMock()
    process.wait.return_value = 1
    coordinator = DeferredRestartCoordinator(
        state_file,
        "instance-1",
        MagicMock(return_value=process),
        grace_seconds=0,
    )
    coordinator.set_ready_check(lambda _request: "ready")
    completion_handler = MagicMock()
    coordinator.set_completion_handler(completion_handler)
    coordinator.request(
        operation_id="operation-1",
        task_id="task-1",
        source_ip="127.0.0.1",
    )

    assert coordinator.maybe_schedule() is True
    for _ in range(100):
        if not coordinator.pending():
            break
        threading.Event().wait(0.01)

    assert coordinator.pending() is False
    completion_handler.assert_called_once()
    assert completion_handler.call_args.args[:3] == (
        "operation-1",
        "task-1",
        "start_failed",
    )


def test_async_failure_advances_and_schedules_next_restart(tmp_path: Path) -> None:
    state_file = tmp_path / "deferred-restart.json"
    wait_started = threading.Event()
    release_failure = threading.Event()
    next_restarted = threading.Event()
    failed_process = MagicMock()

    def wait_for_failure() -> int:
        wait_started.set()
        assert release_failure.wait(1)
        return 1

    failed_process.wait.side_effect = wait_for_failure
    restart_count = 0

    def restart():
        nonlocal restart_count
        restart_count += 1
        if restart_count == 1:
            return failed_process
        next_restarted.set()
        return None

    coordinator = DeferredRestartCoordinator(
        state_file,
        "instance-1",
        restart,
        grace_seconds=0,
    )
    coordinator.set_ready_check(lambda _request: "ready")
    coordinator.set_completion_handler(MagicMock())
    coordinator.request(
        operation_id="operation-1",
        task_id="task-1",
        source_ip="127.0.0.1",
    )
    assert coordinator.maybe_schedule() is True
    assert wait_started.wait(1)

    coordinator.request(
        operation_id="operation-2",
        task_id="task-2",
        source_ip="127.0.0.2",
    )
    assert coordinator.next_state() is not None
    release_failure.set()

    assert next_restarted.wait(1)
    assert coordinator.state() is not None
    assert coordinator.state().operation_id == "operation-2"
    assert coordinator.state().status == "started"


def test_invalid_restart_state_blocks_new_request(tmp_path: Path) -> None:
    state_file = tmp_path / "deferred-restart.json"
    state_file.write_text("not json", encoding="utf-8")
    coordinator = DeferredRestartCoordinator(
        state_file,
        "instance-1",
        MagicMock(),
    )

    with pytest.raises(ApiError) as error:
        coordinator.request(
            operation_id="operation-1",
            task_id="task-1",
            source_ip="127.0.0.1",
        )

    assert error.value.code == "deferred_restart_state_unavailable"
