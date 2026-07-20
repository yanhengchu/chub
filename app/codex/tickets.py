from __future__ import annotations

import secrets
import threading
import time


class TerminalTicketStore:
    def __init__(self, ttl_seconds: int) -> None:
        self.ttl_seconds = ttl_seconds
        self._tickets: dict[str, tuple[str, float]] = {}
        self._lock = threading.Lock()

    def issue(self, session_id: str) -> str:
        ticket = secrets.token_urlsafe(32)
        with self._lock:
            self._prune()
            self._tickets[ticket] = (session_id, time.monotonic() + self.ttl_seconds)
        return ticket

    def valid(self, ticket: str | None, session_id: str) -> bool:
        if not ticket:
            return False
        with self._lock:
            self._prune()
            record = self._tickets.get(ticket)
            return bool(record and record[0] == session_id)

    def revoke_session(self, session_id: str) -> None:
        with self._lock:
            self._tickets = {
                ticket: record
                for ticket, record in self._tickets.items()
                if record[0] != session_id
            }

    def _prune(self) -> None:
        now = time.monotonic()
        self._tickets = {
            ticket: record
            for ticket, record in self._tickets.items()
            if record[1] > now
        }
