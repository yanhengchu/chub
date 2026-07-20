from unittest.mock import patch

from app.codex.tickets import TerminalTicketStore


def test_ticket_is_bound_to_session_and_expires() -> None:
    with patch("app.codex.tickets.time.monotonic", return_value=100):
        store = TerminalTicketStore(60)
        ticket = store.issue("session-1")

    with patch("app.codex.tickets.time.monotonic", return_value=120):
        assert store.valid(ticket, "session-1") is True
        assert store.valid(ticket, "session-2") is False

    with patch("app.codex.tickets.time.monotonic", return_value=161):
        assert store.valid(ticket, "session-1") is False


def test_revoke_session_only_removes_matching_tickets() -> None:
    store = TerminalTicketStore(60)
    first = store.issue("session-1")
    second = store.issue("session-2")

    store.revoke_session("session-1")

    assert store.valid(first, "session-1") is False
    assert store.valid(second, "session-2") is True
