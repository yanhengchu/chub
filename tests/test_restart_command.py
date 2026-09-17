from __future__ import annotations

import threading
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from app.services.restart_command import (
    describe_restart_launch_error,
    launch_restart_process,
    monitor_restart_process,
)
from app.services.web_restart import WebRestartUnavailableError, WebRestartUseCase


def test_launch_restart_process_uses_detached_fixed_command() -> None:
    command = Path("/project/scripts/maintenance/chub-web-restart")

    with patch("app.services.restart_command.subprocess.Popen") as popen:
        process = launch_restart_process(command)

    assert process is popen.return_value
    assert popen.call_args.args[0] == [str(command)]
    assert popen.call_args.kwargs["start_new_session"] is True


def test_web_restart_use_case_uses_only_the_canonical_maintenance_adapter(
    tmp_path: Path,
) -> None:
    command = tmp_path / "scripts" / "maintenance" / "chub-web-restart"
    command.parent.mkdir(parents=True)
    command.touch()
    restart = WebRestartUseCase(project_root=tmp_path)

    with patch("app.services.web_restart.launch_restart_process") as launch:
        process = restart.launch(operation_id="operation-1", source_ip="127.0.0.1")

    assert process is launch.return_value
    assert launch.call_args.args == (command,)
    assert launch.call_args.kwargs["environment"] == {
        "CHUB_OPERATION_ID": "operation-1",
        "CHUB_OPERATION_SOURCE_IP": "127.0.0.1",
    }


def test_web_restart_use_case_rejects_a_missing_canonical_adapter(
    tmp_path: Path,
) -> None:
    with pytest.raises(WebRestartUnavailableError, match="找不到 Chub 重启脚本"):
        WebRestartUseCase(project_root=tmp_path).ensure_available()


def test_monitor_restart_process_reports_nonzero_exit() -> None:
    process = MagicMock()
    process.wait.return_value = 1
    failed = threading.Event()
    reasons: list[str] = []

    def record_failure(reason: str) -> None:
        reasons.append(reason)
        failed.set()

    monitor_restart_process(process, record_failure)

    assert failed.wait(1)
    assert reasons == [
        "重启脚本返回退出码 1，服务管理器未接受重启请求，旧 Chub 实例继续运行。"
    ]


def test_monitor_restart_process_does_not_treat_expected_sigterm_as_failure() -> None:
    process = MagicMock()
    process.wait.return_value = -15
    grace_checked = threading.Event()
    failed = MagicMock()

    def old_instance_stopped(_seconds: float) -> bool:
        grace_checked.set()
        return False

    monitor_restart_process(
        process,
        failed,
        interrupted_survival_check=old_instance_stopped,
    )

    assert grace_checked.wait(1)
    failed.assert_not_called()


def test_monitor_restart_process_reports_signal_if_old_instance_survives() -> None:
    process = MagicMock()
    process.wait.return_value = -15
    failed = threading.Event()
    reasons: list[str] = []

    def record_failure(reason: str) -> None:
        reasons.append(reason)
        failed.set()

    monitor_restart_process(
        process,
        record_failure,
        interrupted_grace_seconds=5,
        interrupted_survival_check=lambda _seconds: True,
    )

    assert failed.wait(1)
    assert reasons == [
        "重启脚本被 SIGTERM（信号 15）中断，等待 5 秒后旧 Chub 实例仍在运行。"
    ]


def test_monitor_restart_process_ignores_successful_exit() -> None:
    process = MagicMock()
    process.wait.return_value = 0
    failed = MagicMock()

    monitor_restart_process(process, failed)
    for _ in range(100):
        if process.wait.called:
            break
        threading.Event().wait(0.01)

    process.wait.assert_called_once_with()
    failed.assert_not_called()


def test_monitor_restart_process_does_not_guess_observation_failure() -> None:
    process = MagicMock()
    process.wait.side_effect = OSError("wait unavailable")
    failed = MagicMock()

    monitor_restart_process(process, failed)
    for _ in range(100):
        if process.wait.called:
            break
        threading.Event().wait(0.01)

    process.wait.assert_called_once_with()
    failed.assert_not_called()


def test_restart_launch_error_description_is_specific_without_exposing_paths() -> None:
    assert describe_restart_launch_error(PermissionError()) == (
        "重启脚本没有执行权限，旧 Chub 实例继续运行。"
    )
    assert describe_restart_launch_error(OSError(24, "too many open files")) == (
        "系统无法启动重启脚本（错误码 24），旧 Chub 实例继续运行。"
    )
