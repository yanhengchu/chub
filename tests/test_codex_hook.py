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
            **{
                key: value
                for key, value in os.environ.items()
                if key != "CHUB_TERMINAL_LAUNCH_ID"
            },
            "CHUB_PTY_SESSION_ID": "chub-session-id",
            "CHUB_PTY_HOOK_DIR": str(tmp_path),
            "CHUB_ACTIVITY_SOURCE": "terminal",
        },
    )

    result = json.loads(
        (tmp_path / "chub-session-id.json").read_text(encoding="utf-8")
    )
    assert result == {
        "chub_session_id": "chub-session-id",
        "codex_session_id": "codex-session-id",
        "launch_id": None,
        "cwd": "/workspace",
        "source": "startup",
        "activity": "idle",
        "activity_source": "none",
    }


def test_codex_hook_rejects_invalid_native_session_id(tmp_path: Path) -> None:
    script = Path(__file__).parents[1] / "scripts" / "chub-codex-hook"

    subprocess.run(
        [str(script)],
        input=json.dumps({"session_id": "--help", "source": "startup"}),
        text=True,
        check=True,
        env={
            **os.environ,
            "CHUB_PTY_SESSION_ID": "chub-session-id",
            "CHUB_PTY_HOOK_DIR": str(tmp_path),
        },
    )

    assert not (tmp_path / "chub-session-id.json").exists()


def test_codex_hook_records_turn_activity(tmp_path: Path) -> None:
    script = Path(__file__).parents[1] / "scripts" / "chub-codex-hook"
    env = {
        **os.environ,
        "CHUB_PTY_SESSION_ID": "chub-session-id",
        "CHUB_PTY_HOOK_DIR": str(tmp_path),
        "CHUB_ACTIVITY_SOURCE": "terminal",
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
        assert result["activity_source"] == (
            "terminal" if expected == "working" else "none"
        )


def test_codex_hook_resolves_rebound_session_id(tmp_path: Path) -> None:
    script = Path(__file__).parents[1] / "scripts" / "chub-codex-hook"
    old_session_id = "old-session-id"
    new_session_id = "new-session-id"
    alias = tmp_path / f".{old_session_id}.rebind"
    alias.write_text(f"{new_session_id}\n", encoding="ascii")
    alias.chmod(0o600)

    subprocess.run(
        [str(script)],
        input=json.dumps(
            {
                "session_id": "codex-session-id",
                "hook_event_name": "UserPromptSubmit",
            }
        ),
        text=True,
        check=True,
        env={
            **os.environ,
            "CHUB_PTY_SESSION_ID": old_session_id,
            "CHUB_PTY_HOOK_DIR": str(tmp_path),
            "CHUB_ACTIVITY_SOURCE": "terminal",
        },
    )

    assert (tmp_path / f"{new_session_id}.json").exists()
    assert not (tmp_path / f"{old_session_id}.json").exists()


def test_codex_hook_attributes_quick_interaction_activity(tmp_path: Path) -> None:
    script = Path(__file__).parents[1] / "scripts" / "chub-codex-hook"
    subprocess.run(
        [str(script)],
        input=json.dumps(
            {
                "session_id": "codex-session-id",
                "hook_event_name": "UserPromptSubmit",
            }
        ),
        text=True,
        check=True,
        env={
            **os.environ,
            "CHUB_PTY_SESSION_ID": "chub-session-id",
            "CHUB_PTY_HOOK_DIR": str(tmp_path),
            "CHUB_ACTIVITY_SOURCE": "quick",
        },
    )

    result = json.loads(
        (tmp_path / "chub-session-id.json").read_text(encoding="utf-8")
    )
    assert result["activity"] == "working"
    assert result["activity_source"] == "quick"

    subprocess.run(
        [str(script)],
        input=json.dumps(
            {
                "session_id": "codex-session-id",
                "hook_event_name": "Stop",
            }
        ),
        text=True,
        check=True,
        env={
            **os.environ,
            "CHUB_PTY_SESSION_ID": "chub-session-id",
            "CHUB_PTY_HOOK_DIR": str(tmp_path),
            "CHUB_ACTIVITY_SOURCE": "quick",
        },
    )
    result = json.loads(
        (tmp_path / "chub-session-id.json").read_text(encoding="utf-8")
    )
    assert result["activity"] == "idle"
    assert result["activity_source"] == "quick"
