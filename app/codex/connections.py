"""Compatibility export for terminal tests.

Production code imports the runtime-neutral implementation from
``app.ai_session.terminal``.  This module remains only so existing regression
tests can be migrated independently in a future cleanup.
"""

from app.ai_session.terminal import (
    PageState,
    TerminalConnection,
    TerminalConnectionRegistry,
    TerminalPage,
)

__all__ = [
    "PageState",
    "TerminalConnection",
    "TerminalConnectionRegistry",
    "TerminalPage",
]
