from pathlib import Path
from runpy import run_path

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
