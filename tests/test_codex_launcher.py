from pathlib import Path
from runpy import run_path
from types import SimpleNamespace
import sys
from unittest.mock import MagicMock

import pytest


LAUNCHER = Path(__file__).parents[1] / "scripts" / "chub-codex-launcher"


@pytest.mark.parametrize(
    ("permission_mode", "permission_args"),
    [
        (
            "ask",
            [
                "-c",
                'default_permissions=":workspace"',
                "-c",
                'approval_policy="on-request"',
                "-c",
                'approvals_reviewer="user"',
            ],
        ),
        (
            "auto-review",
            [
                "-c",
                'default_permissions=":workspace"',
                "-c",
                'approval_policy="on-request"',
                "-c",
                'approvals_reviewer="auto_review"',
            ],
        ),
        (
            "read-only",
            [
                "-c",
                'default_permissions=":read-only"',
                "-c",
                'approval_policy="on-request"',
                "-c",
                'approvals_reviewer="user"',
            ],
        ),
        (
            "full-access",
            [
                "-c",
                'default_permissions=":danger-full-access"',
                "-c",
                'approval_policy="never"',
            ],
        ),
    ],
)
def test_build_codex_command_maps_permission_profile(
    permission_mode: str,
    permission_args: list[str],
) -> None:
    build_codex_command = run_path(str(LAUNCHER))["build_codex_command"]

    command = build_codex_command(permission_mode, "codex-session-id")

    assert command == [
        "codex",
        "--profile",
        "chub",
        *permission_args,
        "resume",
        "codex-session-id",
    ]


def test_build_codex_command_adds_session_model_and_reasoning_level() -> None:
    build_codex_command = run_path(str(LAUNCHER))["build_codex_command"]

    command = build_codex_command(
        "full-access",
        "codex-session-id",
        "gpt-test",
        "high",
    )

    assert ["--model", "gpt-test"] == command[
        command.index("--model") : command.index("--model") + 2
    ]
    assert 'model_reasoning_effort="high"' in command
    assert command[-2:] == ["resume", "codex-session-id"]


def test_launcher_creates_detached_tmux_before_attaching(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    namespace = run_path(str(LAUNCHER))
    main = namespace["main"]
    run = MagicMock(
        side_effect=[
            SimpleNamespace(returncode=1),
            SimpleNamespace(returncode=0),
        ]
    )
    execvp = MagicMock(side_effect=SystemExit(0))
    monkeypatch.setattr(main.__globals__["subprocess"], "run", run)
    monkeypatch.setattr(main.__globals__["os"], "execvp", execvp)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "chub-codex-launcher",
            "--name",
            "chub-session-1",
            "--cwd",
            "/workspace",
            "--chub-session",
            "session-1",
            "--hook-dir",
            "/hooks",
            "--permission-mode",
            "read-only",
        ],
    )

    with pytest.raises(SystemExit):
        main()

    assert run.call_args_list[1].args[0][:5] == [
        "tmux",
        "new-session",
        "-d",
        "-s",
        "chub-session-1",
    ]
    execvp.assert_called_once_with(
        "tmux",
        ["tmux", "attach-session", "-t", "chub-session-1"],
    )
