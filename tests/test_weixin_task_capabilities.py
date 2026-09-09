from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from app.core.response import ApiError
from app.services.weixin_task_capabilities import WeixinTaskCapabilityHost


def test_task_read_requires_the_request_bound_reference() -> None:
    quick_interactions = MagicMock()
    quick_interactions.get.return_value = SimpleNamespace(id="task-1")
    host = WeixinTaskCapabilityHost(quick_interactions)
    request = SimpleNamespace(id="request-1", task_id="task-1")
    request.task_ref = host.task_ref(request.id, request.task_id)

    assert host.read_task(request, request.task_ref).id == "task-1"
    with pytest.raises(ApiError) as error:
        host.read_task(request, "0" * 64)

    assert error.value.code == "orchestration_task_ref_rejected"
    quick_interactions.get.assert_called_once_with("task-1")


def test_task_await_never_submits_a_second_task() -> None:
    quick_interactions = MagicMock()
    quick_interactions.get.return_value = SimpleNamespace(id="task-1", status="running")
    host = WeixinTaskCapabilityHost(quick_interactions)
    request = SimpleNamespace(id="request-1", task_id="task-1")
    request.task_ref = host.task_ref(request.id, request.task_id)

    assert host.await_task(request, request.task_ref) == "waiting"
    quick_interactions.submit.assert_not_called()


def test_task_await_reports_a_terminal_failure_without_calling_submit() -> None:
    quick_interactions = MagicMock()
    quick_interactions.get.return_value = SimpleNamespace(id="task-1", status="failed")
    host = WeixinTaskCapabilityHost(quick_interactions)
    request = SimpleNamespace(id="request-1", task_id="task-1")
    request.task_ref = host.task_ref(request.id, request.task_id)

    assert host.await_task(request, request.task_ref) == "rejected"
    quick_interactions.submit.assert_not_called()


def test_session_read_requires_the_request_bound_reference() -> None:
    quick_interactions = MagicMock()
    session_manager = MagicMock()
    session_manager.get_session.return_value = SimpleNamespace(id="session-1")
    host = WeixinTaskCapabilityHost(quick_interactions, session_manager)
    request = SimpleNamespace(session_id="session-1", session_ref="a" * 64)

    assert host.read_session(request, "a" * 64).id == "session-1"
    with pytest.raises(ApiError) as error:
        host.read_session(request, "b" * 64)

    assert error.value.code == "orchestration_session_ref_rejected"
    session_manager.get_session.assert_called_once_with("session-1")


def test_session_create_requires_the_request_bound_context() -> None:
    host = WeixinTaskCapabilityHost(MagicMock())
    request = SimpleNamespace(creation_ref="c" * 64)
    creator = MagicMock(return_value=("session-1", True))

    assert host.create_session(request, "c" * 64, creator) == ("session-1", True)
    with pytest.raises(ApiError) as error:
        host.create_session(request, "d" * 64, creator)

    assert error.value.code == "orchestration_creation_ref_rejected"
    creator.assert_called_once()
