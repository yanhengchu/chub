import json
import os
import subprocess
from pathlib import Path


def test_codex_hook_correlates_codex_and_chub_sessions(tmp_path: Path) -> None:
    script = Path(__file__).parents[1] / "scripts" / "chub-codex-hook"
    payload = {
        "session_id": "codex-session-id",
        "cwd": "/workspace",
        "source": "startup",
    }

    subprocess.run(
        [str(script)],
        input=json.dumps(payload),
        text=True,
        check=True,
        env={
            **os.environ,
            "CHUB_PTY_SESSION_ID": "chub-session-id",
            "CHUB_PTY_HOOK_DIR": str(tmp_path),
        },
    )

    result = json.loads(
        (tmp_path / "chub-session-id.json").read_text(encoding="utf-8")
    )
    assert result == {
        "chub_session_id": "chub-session-id",
        "codex_session_id": "codex-session-id",
        "cwd": "/workspace",
        "source": "startup",
        "activity": "idle",
    }


def test_codex_hook_records_turn_activity(tmp_path: Path) -> None:
    script = Path(__file__).parents[1] / "scripts" / "chub-codex-hook"
    env = {
        **os.environ,
        "CHUB_PTY_SESSION_ID": "chub-session-id",
        "CHUB_PTY_HOOK_DIR": str(tmp_path),
    }

    for event, expected in (
        ("UserPromptSubmit", "working"),
        ("Stop", "idle"),
    ):
        subprocess.run(
            [str(script)],
            input=json.dumps(
                {
                    "session_id": "codex-session-id",
                    "cwd": "/workspace",
                    "hook_event_name": event,
                }
            ),
            text=True,
            check=True,
            env=env,
        )
        result = json.loads(
            (tmp_path / "chub-session-id.json").read_text(encoding="utf-8")
        )
        assert result["activity"] == expected
