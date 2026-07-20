from pathlib import Path

import pytest

from app.codex.manager import CodexPtyManager, PROFILE_MARKER
from app.core.config import Settings


def test_accepts_codex_metadata_around_managed_profile() -> None:
    hook = Path("/workspace/chub/scripts/chub-codex-hook")
    content = f"""approvals_reviewer = "user"
{PROFILE_MARKER}
[[hooks.SessionStart]]
matcher = "startup|resume"

[[hooks.SessionStart.hooks]]
type = "command"
command = "{hook}"
timeout = 5

[hooks.state]

[hooks.state."managed-hook"]
trusted_hash = "sha256:test"
"""

    assert CodexPtyManager._profile_has_managed_hook(content, hook) is True


def test_rejects_profile_with_marker_but_different_hook() -> None:
    content = f"""{PROFILE_MARKER}
[[hooks.SessionStart]]
matcher = "startup|resume"

[[hooks.SessionStart.hooks]]
type = "command"
command = "/other/chub-codex-hook"
timeout = 5
"""

    assert (
        CodexPtyManager._profile_has_managed_hook(
            content,
            Path("/workspace/chub/scripts/chub-codex-hook"),
        )
        is False
    )


def test_detects_turn_activity_hooks() -> None:
    hook = Path("/workspace/chub/scripts/chub-codex-hook")
    content = (
        f"{PROFILE_MARKER}\n"
        + CodexPtyManager._activity_hook_content(hook)
    )

    assert CodexPtyManager._profile_has_activity_hooks(content, hook) is True


def test_ensure_profile_adds_activity_hooks_without_removing_metadata(
    settings: Settings,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    codex_home = tmp_path / ".codex"
    codex_home.mkdir()
    hook = Path(__file__).parents[1] / "scripts" / "chub-codex-hook"
    profile = codex_home / "chub.config.toml"
    profile.write_text(
        f"""approvals_reviewer = "user"
{PROFILE_MARKER}
[[hooks.SessionStart]]
matcher = "startup|resume"

[[hooks.SessionStart.hooks]]
type = "command"
command = "{hook}"
timeout = 5

[hooks.state]
""",
        encoding="utf-8",
    )
    monkeypatch.setenv("CODEX_HOME", str(codex_home))
    manager = CodexPtyManager(settings)

    manager._ensure_profile()

    updated = profile.read_text(encoding="utf-8")
    assert 'approvals_reviewer = "user"' in updated
    assert manager._profile_has_activity_hooks(updated, hook) is True


def test_ensure_profile_only_adds_missing_activity_hook(
    settings: Settings,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    codex_home = tmp_path / ".codex"
    codex_home.mkdir()
    hook = Path(__file__).parents[1] / "scripts" / "chub-codex-hook"
    profile = codex_home / "chub.config.toml"
    profile.write_text(
        (
            f"{PROFILE_MARKER}\n"
            "[[hooks.SessionStart]]\n"
            'matcher = "startup|resume"\n\n'
            "[[hooks.SessionStart.hooks]]\n"
            'type = "command"\n'
            f'command = "{hook}"\n'
            "timeout = 5\n"
            f"{CodexPtyManager._event_hook_content('UserPromptSubmit', hook)}"
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("CODEX_HOME", str(codex_home))
    manager = CodexPtyManager(settings)

    manager._ensure_profile()

    updated = profile.read_text(encoding="utf-8")
    assert updated.count("[[hooks.UserPromptSubmit]]") == 1
    assert updated.count("[[hooks.Stop]]") == 1
