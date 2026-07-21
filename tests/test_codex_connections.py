import asyncio

import pytest

from app.codex.connections import TerminalConnectionRegistry


@pytest.mark.anyio
async def test_takeover_becomes_visible_only_after_backend_activation() -> None:
    registry = TerminalConnectionRegistry(release_timeout=1)
    first_page = registry.open_page("session-1", "ticket-1")
    first, released = await registry.claim("session-1", "ticket-1", first_page.id)
    assert released is True
    assert registry.activate(first) is True

    second_page = registry.open_page("session-1", "ticket-2")
    second_claim = asyncio.create_task(
        registry.claim("session-1", "ticket-2", second_page.id)
    )
    await first.takeover.wait()
    assert second_claim.done() is False

    registry.release(first)
    second, released = await second_claim

    assert released is True
    assert registry.page_state("session-1", first_page.id) == "active"
    assert registry.page_state("session-1", second_page.id) == "waiting"

    assert registry.activate(second) is True
    assert registry.page_state("session-1", first_page.id) == "displaced"
    assert registry.page_state("session-1", second_page.id) == "active"


@pytest.mark.anyio
async def test_failed_backend_does_not_displace_the_old_page() -> None:
    registry = TerminalConnectionRegistry(release_timeout=1)
    first_page = registry.open_page("session-1", "ticket-1")
    first, _ = await registry.claim("session-1", "ticket-1", first_page.id)
    registry.activate(first)

    second_page = registry.open_page("session-1", "ticket-2")
    second_claim = asyncio.create_task(
        registry.claim("session-1", "ticket-2", second_page.id)
    )
    await first.takeover.wait()
    registry.release(first)
    second, _ = await second_claim
    registry.release(second)

    assert registry.page_state("session-1", first_page.id) == "active"
    assert registry.page_state("session-1", second_page.id) == "closed"


@pytest.mark.anyio
async def test_pages_have_independent_identity_when_browser_cookie_changes() -> None:
    registry = TerminalConnectionRegistry(release_timeout=1)
    first_page = registry.open_page("session-1", "ticket-1")
    first, _ = await registry.claim("session-1", "ticket-1", first_page.id)
    registry.activate(first)

    second_page = registry.open_page("session-1", "ticket-2")
    second_claim = asyncio.create_task(
        registry.claim("session-1", "ticket-2", second_page.id)
    )
    await first.takeover.wait()
    registry.release(first)
    second, _ = await second_claim
    registry.activate(second)

    assert first_page.id != second_page.id
    assert registry.page_state("session-1", first_page.id) == "displaced"
    assert registry.page_state("session-1", second_page.id) == "active"


@pytest.mark.anyio
async def test_takeover_timeout_allows_a_new_generation() -> None:
    registry = TerminalConnectionRegistry(release_timeout=0.01)
    first_page = registry.open_page("session-1", "ticket-1")
    first, _ = await registry.claim("session-1", "ticket-1", first_page.id)
    registry.activate(first)
    second_page = registry.open_page("session-1", "ticket-2")

    second, released = await registry.claim(
        "session-1", "ticket-2", second_page.id
    )

    assert released is False
    assert first.takeover.is_set()
    assert second.generation == first.generation + 1
    registry.release(first)
    assert registry.activate(second) is True


@pytest.mark.anyio
async def test_explicit_session_close_closes_pages_and_connection() -> None:
    registry = TerminalConnectionRegistry(release_timeout=1)
    page = registry.open_page("session-1", "ticket-1")
    connection, _ = await registry.claim("session-1", "ticket-1", page.id)
    registry.activate(connection)

    registry.close_session("session-1")

    assert connection.takeover.is_set()
    assert registry.page_state("session-1", page.id) == "closed"


@pytest.mark.anyio
async def test_closed_pages_are_pruned_after_ttl() -> None:
    registry = TerminalConnectionRegistry(release_timeout=1, page_ttl=0)
    page = registry.open_page("session-1", "ticket-1")
    connection, _ = await registry.claim("session-1", "ticket-1", page.id)
    registry.activate(connection)
    registry.close_session("session-1")

    assert registry.page_state("session-1", page.id) is None


@pytest.mark.anyio
async def test_open_page_reuses_existing_ticket_page() -> None:
    registry = TerminalConnectionRegistry(release_timeout=1)
    first = registry.open_page("session-1", "ticket-1")
    second = registry.open_page("session-1", "ticket-1")

    assert first.id == second.id
    assert registry.page_state("session-1", first.id) == "waiting"


@pytest.mark.anyio
async def test_shared_browser_cookie_cannot_reclaim_an_old_page() -> None:
    registry = TerminalConnectionRegistry(release_timeout=1)
    old_page = registry.open_page("session-1", "old-ticket")
    old_connection, _ = await registry.claim(
        "session-1", "old-ticket", old_page.id
    )
    registry.activate(old_connection)

    new_page = registry.open_page("session-1", "new-ticket")
    new_claim = asyncio.create_task(
        registry.claim("session-1", "new-ticket", new_page.id)
    )
    await old_connection.takeover.wait()
    registry.release(old_connection)
    new_connection, _ = await new_claim
    registry.activate(new_connection)

    with pytest.raises(ValueError):
        await registry.claim("session-1", "new-ticket", old_page.id)

    assert registry.page_state("session-1", new_page.id) == "active"
    assert new_connection.takeover.is_set() is False
