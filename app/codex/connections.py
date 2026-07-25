from __future__ import annotations

import asyncio
import secrets
import threading
import time
from dataclasses import dataclass, field
from typing import Literal


PageState = Literal["waiting", "active", "displaced", "closed"]


@dataclass(slots=True)
class TerminalPage:
    id: str
    session_id: str
    ticket: str
    state: PageState = "waiting"
    updated_at: float = field(default_factory=time.monotonic)


@dataclass(slots=True)
class TerminalConnection:
    session_id: str
    generation: int
    page_id: str
    takeover: asyncio.Event = field(default_factory=asyncio.Event)
    released: asyncio.Event = field(default_factory=asyncio.Event)
    activated: bool = False
    loop: asyncio.AbstractEventLoop | None = None


class TerminalConnectionRegistry:
    """Coordinates one active browser terminal for each Codex session."""

    def __init__(
        self,
        release_timeout: float = 2.0,
        page_ttl: float = 3600.0,
    ) -> None:
        self.release_timeout = release_timeout
        self.page_ttl = page_ttl
        self._connections: dict[str, TerminalConnection] = {}
        self._pages: dict[str, TerminalPage] = {}
        self._ticket_pages: dict[tuple[str, str], str] = {}
        self._locks: dict[str, asyncio.Lock] = {}
        self._generations: dict[str, int] = {}
        self._state_lock = threading.RLock()

    def open_page(self, session_id: str, ticket: str) -> TerminalPage:
        with self._state_lock:
            self._prune_pages()
            page_id = self._ticket_pages.get((session_id, ticket))
            page = self._pages.get(page_id) if page_id else None
            if page is not None and page.session_id == session_id:
                page.state = "waiting"
                page.updated_at = time.monotonic()
                return page
            page = TerminalPage(
                id=secrets.token_urlsafe(24),
                session_id=session_id,
                ticket=ticket,
            )
            self._pages[page.id] = page
            self._ticket_pages[(session_id, ticket)] = page.id
            return page

    def page_state(self, session_id: str, page_id: str) -> PageState | None:
        with self._state_lock:
            self._prune_pages()
            page = self._pages.get(page_id)
            if page is None or page.session_id != session_id:
                return None
            page.updated_at = time.monotonic()
            return page.state

    async def claim(
        self,
        session_id: str,
        ticket: str,
        page_id: str,
    ) -> tuple[TerminalConnection, bool]:
        """Reserve ttyd for a page, asking the old connection to release first."""
        lock = self._locks.setdefault(session_id, asyncio.Lock())
        async with lock:
            with self._state_lock:
                page = self._pages.get(page_id)
                if (
                    page is None
                    or page.session_id != session_id
                    or page.ticket != ticket
                    or page.state == "closed"
                ):
                    raise ValueError("Terminal page does not match its access ticket")
                previous = self._connections.get(session_id)
            released = True
            if previous is not None and not previous.released.is_set():
                self._signal(previous.takeover, previous.loop)
                try:
                    await asyncio.wait_for(
                        previous.released.wait(),
                        timeout=self.release_timeout,
                    )
                except TimeoutError:
                    released = False

            with self._state_lock:
                if page.state == "closed":
                    raise ValueError("Terminal page access expired while waiting")
                page.state = "waiting"
                page.updated_at = time.monotonic()
                generation = self._generations.get(session_id, 0) + 1
                self._generations[session_id] = generation
                connection = TerminalConnection(
                    session_id,
                    generation,
                    page.id,
                    loop=asyncio.get_running_loop(),
                )
                self._connections[session_id] = connection
                return connection, released

    def activate(self, connection: TerminalConnection) -> bool:
        with self._state_lock:
            current = self._connections.get(connection.session_id)
            if current is not connection:
                return False
            for page in self._pages.values():
                if (
                    page.session_id == connection.session_id
                    and page.id != connection.page_id
                    and page.state == "active"
                ):
                    page.state = "displaced"
                    page.updated_at = time.monotonic()
            page = self._pages.get(connection.page_id)
            if page is None or page.state == "closed":
                return False
            page.state = "active"
            page.updated_at = time.monotonic()
            connection.activated = True
            return True

    def has_active_connection(self, session_id: str) -> bool:
        with self._state_lock:
            connection = self._connections.get(session_id)
            return bool(connection and not connection.released.is_set())

    def close_session(self, session_id: str) -> None:
        with self._state_lock:
            connection = self._connections.pop(session_id, None)
            now = time.monotonic()
            for page in self._pages.values():
                if page.session_id == session_id:
                    page.state = "closed"
                    page.updated_at = now
            self._prune_pages()
        if connection is not None:
            self._signal(connection.takeover, connection.loop)
            self._signal(connection.released, connection.loop)

    def release(self, connection: TerminalConnection) -> None:
        connection.released.set()
        with self._state_lock:
            current = self._connections.get(connection.session_id)
            if current is connection:
                self._connections.pop(connection.session_id, None)
            if not connection.activated:
                page = self._pages.get(connection.page_id)
                if page is not None and page.state == "waiting":
                    page.state = "closed"
                    page.updated_at = time.monotonic()
            self._prune_pages()

    @staticmethod
    def _signal(
        event: asyncio.Event,
        loop: asyncio.AbstractEventLoop | None,
    ) -> None:
        if loop is None or loop.is_closed():
            event.set()
            return
        try:
            current_loop = asyncio.get_running_loop()
        except RuntimeError:
            current_loop = None
        if current_loop is loop:
            event.set()
        else:
            loop.call_soon_threadsafe(event.set)

    def _prune_pages(self) -> None:
        cutoff = time.monotonic() - self.page_ttl
        expired = [
            page_id
            for page_id, page in self._pages.items()
            if page.updated_at < cutoff and page.state != "active"
        ]
        for page_id in expired:
            page = self._pages.pop(page_id)
            key = (page.session_id, page.ticket)
            if self._ticket_pages.get(key) == page_id:
                self._ticket_pages.pop(key, None)
