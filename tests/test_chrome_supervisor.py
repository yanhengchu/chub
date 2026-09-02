from __future__ import annotations

import json
import socket
import threading
from uuid import uuid4
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from app.automations.chrome_supervisor import (
    ChromeSupervisorError,
    MAX_REQUEST_BYTES,
    SOCKET_NAME,
    _execute,
    _prepare_socket,
    request,
)
from app.automations.manager import AutomationManager


def test_supervisor_request_uses_bounded_fixed_protocol(tmp_path: Path) -> None:
    # macOS AF_UNIX paths are capped at 104 bytes.  pytest's standard temporary
    # directory can exceed that limit, so use the stable short system temp root
    # while retaining a unique name for parallel-safe isolation.
    socket_root = Path("/tmp") if Path("/tmp").is_dir() else tmp_path
    socket_file = socket_root / f"chub-{uuid4().hex[:12]}.sock"
    listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    listener.bind(str(socket_file))
    listener.listen(1)
    received: list[bytes] = []

    def serve_once() -> None:
        connection, _ = listener.accept()
        with connection:
            received.append(connection.recv(MAX_REQUEST_BYTES))
            connection.sendall(
                (
                    json.dumps(
                        {
                            "ok": True,
                            "data": {
                                "state": "stopped",
                                "mode": None,
                                "endpoint": "http://127.0.0.1:9222",
                                "user_data_dir": "/tmp/debug",
                                "profile_directory": "Profile 1",
                                "process_ids": [],
                            },
                        }
                    )
                    + "\n"
                ).encode()
            )

    thread = threading.Thread(target=serve_once)
    thread.start()
    try:
        result = request(socket_file, "status")
    finally:
        thread.join(timeout=2)
        listener.close()
        socket_file.unlink(missing_ok=True)

    assert result.state == "stopped"
    assert json.loads(received[0]) == {"action": "status"}


def test_supervisor_request_rejects_unavailable_socket(tmp_path: Path) -> None:
    with pytest.raises(ChromeSupervisorError, match="unavailable"):
        request(tmp_path / SOCKET_NAME, "status")


def test_supervisor_refuses_unsafe_stale_socket_path(tmp_path: Path) -> None:
    socket_file = tmp_path / SOCKET_NAME
    socket_file.write_text("not a socket", encoding="utf-8")

    with pytest.raises(ChromeSupervisorError, match="not a socket"):
        _prepare_socket(socket_file)


@pytest.mark.parametrize("action", ["status", "start", "stop"])
def test_supervisor_executes_only_fixed_browser_actions(action: str) -> None:
    module = MagicMock()
    module.status.return_value = SimpleNamespace(
        state="stopped",
        mode=None,
        endpoint="http://127.0.0.1:9222",
        user_data_dir="/tmp/debug",
        profile_directory=None,
        process_ids=[],
    )
    module.start.return_value = module.status.return_value
    module.stop.return_value = module.status.return_value

    with patch(
        "app.automations.chrome_supervisor._chrome_debug_module",
        return_value=module,
    ):
        result = _execute(
            {"action": action, **({"mode": "headless"} if action == "start" else {})}
        )

    assert result.state == "stopped"
    if action == "start":
        module.start.assert_called_once_with(headless=True)
    elif action == "stop":
        module.stop.assert_called_once_with()
    else:
        module.status.assert_called_once_with()


def test_ubuntu_automation_manager_routes_browser_control_to_supervisor(
    settings,
    tmp_path: Path,
) -> None:
    settings.automations.runtime_dir = tmp_path / "automation-runtime"
    manager = AutomationManager(settings, detected_platform="ubuntu")

    with patch(
        "app.automations.manager.start_debug_chrome",
        return_value=SimpleNamespace(state="running", mode="headless"),
    ) as start:
        result = manager.control_browser("start")

    assert result.state == "running"
    start.assert_called_once_with(
        "headless",
        supervisor_socket=settings.automations.runtime_dir / SOCKET_NAME,
    )
