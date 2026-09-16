from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from app.task_capabilities import command


def test_page_read_returns_only_the_bounded_snapshot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
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


def test_page_interact_returns_only_the_bounded_snapshot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
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
