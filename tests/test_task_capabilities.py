from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from app.task_capabilities import TaskCapabilityContext
from app.task_capabilities import command


def _write_context(tmp_path, capability_ids: list[str]) -> tuple[str, object]:
    token = "a" * 32
    path = tmp_path / "capability-context.json"
    context = TaskCapabilityContext(
        task_id="task-1",
        token=token,
        capability_ids=capability_ids,
    )
    path.write_text(context.model_dump_json(), encoding="utf-8")
    path.chmod(0o600)
    return token, path


def test_page_read_requires_a_task_bound_grant(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    token, path = _write_context(tmp_path, [])
    monkeypatch.setenv("CHUB_TASK_CAPABILITY_CONTEXT", str(path))
    monkeypatch.setenv("CHUB_TASK_CAPABILITY_TOKEN", token)

    with pytest.raises(command.TaskCapabilityError, match="was not granted"):
        asyncio.run(
            command.run(SimpleNamespace(command="page-read", url="https://example.com"))
        )


def test_page_read_returns_only_the_bounded_snapshot(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    token, path = _write_context(tmp_path, ["chub.debug_chrome.page.read"])
    monkeypatch.setenv("CHUB_TASK_CAPABILITY_CONTEXT", str(path))
    monkeypatch.setenv("CHUB_TASK_CAPABILITY_TOKEN", token)

    async def read_page(_url: str):
        return SimpleNamespace(
            source_url="https://example.com",
            final_url="https://example.com/final",
            title="Example",
            content="bounded page text",
            truncated=False,
        )

    monkeypatch.setattr(command, "read_debug_chrome_page", read_page)
    result = asyncio.run(
        command.run(SimpleNamespace(command="page-read", url="https://example.com"))
    )

    assert result == {
        "source_url": "https://example.com",
        "final_url": "https://example.com/final",
        "title": "Example",
        "content": "bounded page text",
        "truncated": False,
    }


def test_page_read_rejects_an_unmatched_task_token(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, path = _write_context(tmp_path, ["chub.debug_chrome.page.read"])
    monkeypatch.setenv("CHUB_TASK_CAPABILITY_CONTEXT", str(path))
    monkeypatch.setenv("CHUB_TASK_CAPABILITY_TOKEN", "b" * 32)

    with pytest.raises(command.TaskCapabilityError, match="token was rejected"):
        command._load_context()


def test_page_interact_requires_a_task_bound_grant(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    token, path = _write_context(tmp_path, [])
    monkeypatch.setenv("CHUB_TASK_CAPABILITY_CONTEXT", str(path))
    monkeypatch.setenv("CHUB_TASK_CAPABILITY_TOKEN", token)

    with pytest.raises(command.TaskCapabilityError, match="was not granted"):
        asyncio.run(
            command.run(
                SimpleNamespace(
                    command="page-interact",
                    url="https://example.com",
                    follow_link="Next",
                )
            )
        )


def test_page_interact_returns_only_the_bounded_snapshot(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    token, path = _write_context(tmp_path, ["chub.debug_chrome.page.interact"])
    monkeypatch.setenv("CHUB_TASK_CAPABILITY_CONTEXT", str(path))
    monkeypatch.setenv("CHUB_TASK_CAPABILITY_TOKEN", token)

    async def interact_page(_url: str, *, follow_link_text: str):
        assert follow_link_text == "Next"
        return SimpleNamespace(
            source_url="https://example.com",
            final_url="https://example.com/next",
            title="Next page",
            content="bounded page text",
            truncated=False,
        )

    monkeypatch.setattr(command, "interact_debug_chrome_page", interact_page)
    result = asyncio.run(
        command.run(
            SimpleNamespace(
                command="page-interact",
                url="https://example.com",
                follow_link="Next",
            )
        )
    )

    assert result["final_url"] == "https://example.com/next"
