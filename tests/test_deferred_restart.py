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
    coordinator.set_ready_check(lambda: ready)

    assert coordinator.request(
        operation_id="operation-1",
        task_id="task-1",
        source_ip="127.0.0.1",
    ) is True
    assert coordinator.maybe_schedule() is False
    assert stat.S_IMODE(state_file.stat().st_mode) == 0o600
    assert json.loads(state_file.read_text(encoding="utf-8"))["status"] == "waiting"

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

    assert coordinator.request(
        operation_id="operation-1",
        task_id="task-1",
        source_ip="127.0.0.1",
    ) is True
    assert coordinator.request(
        operation_id="operation-2",
        task_id="task-2",
        source_ip="127.0.0.2",
    ) is False
    assert coordinator.state().operation_id == "operation-1"


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
    assert completion_handler.call_args.args[0] == "task-1"
    assert completion_handler.call_args.args[1] is False


def test_new_instance_reports_automatic_restart_completion(tmp_path: Path) -> None:
    state_file = tmp_path / "deferred-restart.json"
    restarted = threading.Event()
    first = DeferredRestartCoordinator(
        state_file,
        "instance-1",
        restarted.set,
        grace_seconds=0,
    )
    first.set_ready_check(lambda: True)
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

    assert completion_handler.call_args.args[0] == "task-1"
    assert completion_handler.call_args.args[1] is True
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
    coordinator.set_ready_check(lambda: True)
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
    assert completion_handler.call_args.args[0] == "task-1"
    assert completion_handler.call_args.args[1] is False


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
