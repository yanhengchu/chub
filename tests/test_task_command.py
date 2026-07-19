from __future__ import annotations

import subprocess
import sys
import time
from unittest.mock import MagicMock, Mock, patch

import pytest

from app.tasks.command import CommandTimedOut, run_command


@patch("app.tasks.command.subprocess.Popen")
def test_command_runner_disables_shell_and_limits_output(popen: Mock) -> None:
    process = popen.return_value
    process.returncode = 0

    with (
        patch("app.tasks.command.tempfile.TemporaryFile") as temporary_file,
    ):
        stdout_file = MagicMock()
        stderr_file = MagicMock()
        temporary_file.side_effect = [stdout_file, stderr_file]
        stdout_file.__enter__ = Mock(return_value=stdout_file)
        stdout_file.__exit__ = Mock(return_value=False)
        stderr_file.__enter__ = Mock(return_value=stderr_file)
        stderr_file.__exit__ = Mock(return_value=False)
        stdout_file.read.return_value = b"abcd"
        stderr_file.read.return_value = b"erro"

        result = run_command(["tool", "--version"], timeout=3, max_output_bytes=3)

    assert result.stdout == "abc\n[output truncated]"
    assert result.stderr == "err\n[output truncated]"
    assert popen.call_args.args[0] == ["tool", "--version"]
    assert popen.call_args.kwargs["shell"] is False
    assert popen.call_args.kwargs["stdin"] is subprocess.DEVNULL


@patch("app.tasks.command.subprocess.Popen")
def test_command_runner_kills_timed_out_windows_process(popen: Mock) -> None:
    process = popen.return_value
    process.wait.side_effect = [
        subprocess.TimeoutExpired(["tool"], 1),
        0,
    ]

    with (
        patch("app.tasks.command.sys.platform", "win32"),
        pytest.raises(CommandTimedOut),
    ):
        run_command(["tool"], timeout=1)

    process.kill.assert_called_once_with()
    assert process.wait.call_count == 2


@patch("app.tasks.command.os.killpg")
@patch("app.tasks.command.subprocess.Popen")
def test_command_runner_kills_timed_out_posix_process_group(
    popen: Mock,
    kill_group: Mock,
) -> None:
    process = popen.return_value
    process.pid = 123
    process.wait.side_effect = [
        subprocess.TimeoutExpired(["tool"], 1),
        0,
    ]

    with (
        patch("app.tasks.command.sys.platform", "linux"),
        pytest.raises(CommandTimedOut),
    ):
        run_command(["tool"], timeout=1)

    kill_group.assert_called_once_with(123, 9)
    assert popen.call_args.kwargs["start_new_session"] is True


def test_command_runner_enforces_real_process_timeout() -> None:
    started = time.monotonic()

    with pytest.raises(CommandTimedOut):
        run_command(
            [sys.executable, "-c", "import time; time.sleep(5)"],
            timeout=0.1,
        )

    assert time.monotonic() - started < 2
