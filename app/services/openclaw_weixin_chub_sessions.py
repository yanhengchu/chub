from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from app.codex.models import sessions_newest_first
from app.services.openclaw_weixin_chub_messages import build_session_title
from app.services.openclaw_weixin_chub_models import (
    MAX_WEIXIN_SESSION_SLOTS,
    WeixinChubModeRuntimeConfig,
    WeixinChubModeSessionSlot,
)


@dataclass(frozen=True)
class ChubSessionSnapshot:
    """Read-only Session projection used by Weixin replies and status snapshots."""

    slot: int
    session_id: str
    title: str
    state: str
    current: bool
    workspace_name: str | None = None
    model: str | None = None
    reasoning_effort: str | None = None


def collect_assigned_session_snapshots(
    *,
    sessions: list[object],
    configuration: WeixinChubModeRuntimeConfig,
    current_session_id: str | None,
    slots: list[WeixinChubModeSessionSlot],
    session_name_max_width: int,
    session_matches: Callable[[object, WeixinChubModeRuntimeConfig], bool],
    session_state: Callable[[object], str],
    workspace_name: Callable[[object], str | None],
) -> tuple[ChubSessionSnapshot, ...]:
    eligible = {
        session.id: session
        for session in sessions
        if session_matches(session, configuration)
    }
    slots_by_session_id = {entry.session_id: entry.slot for entry in slots}
    return tuple(
        ChubSessionSnapshot(
            slot=slots_by_session_id[session.id],
            session_id=session.id,
            title=build_session_title(
                getattr(session, "title", None) or "Unnamed Session",
                session_name_max_width,
            ),
            state=session_state(session),
            current=session.id == current_session_id,
            workspace_name=workspace_name(session),
            model=getattr(session, "model", None),
            reasoning_effort=getattr(session, "reasoning_effort", None),
        )
        for session in sessions_newest_first(eligible.values())
        if session.id in slots_by_session_id
    )


def build_synced_slots(
    *,
    configuration: WeixinChubModeRuntimeConfig,
    original_slots: list[WeixinChubModeSessionSlot],
    current_session_id: str | None,
    sessions: list[object],
    weixin_session_ids: set[str],
    fill_candidates: bool,
    session_matches: Callable[[object, WeixinChubModeRuntimeConfig], bool],
    session_state: Callable[[object], str],
) -> list[WeixinChubModeSessionSlot]:
    eligible = {
        session.id: session
        for session in sessions
        if session_matches(session, configuration)
    }
    retained: list[WeixinChubModeSessionSlot] = []
    used_slots: set[int] = set()
    used_sessions: set[str] = set()
    for entry in sorted(original_slots, key=lambda item: item.slot):
        if (
            entry.session_id in eligible
            and entry.slot not in used_slots
            and entry.session_id not in used_sessions
        ):
            retained.append(entry.model_copy(deep=True))
            used_slots.add(entry.slot)
            used_sessions.add(entry.session_id)
    candidates: list[object] = []
    current = eligible.get(current_session_id or "")
    if (
        current is not None
        and current.id not in used_sessions
        and session_state(current) != "Unavailable"
    ):
        candidates.append(current)
    if fill_candidates:
        candidates.extend(
            sorted(
                (
                    session
                    for session in eligible.values()
                    if session.id not in used_sessions
                    and session.id != getattr(current, "id", None)
                    and session_state(session) != "Unavailable"
                ),
                key=lambda session: (
                    session.id not in weixin_session_ids,
                    session.id,
                ),
            )
        )
    free_slots = [
        slot
        for slot in range(1, MAX_WEIXIN_SESSION_SLOTS + 1)
        if slot not in used_slots
    ]
    retained.extend(
        WeixinChubModeSessionSlot(slot=slot, session_id=session.id)
        for slot, session in zip(free_slots, candidates, strict=False)
    )
    return sorted(retained, key=lambda item: item.slot)


def visible_sessions(
    *,
    sessions: list[object],
    configuration: WeixinChubModeRuntimeConfig,
    slots: list[WeixinChubModeSessionSlot],
    session_matches: Callable[[object, WeixinChubModeRuntimeConfig], bool],
    session_state: Callable[[object], str],
) -> tuple[list[tuple[int, object, str]], int]:
    eligible = {
        session.id: session
        for session in sessions
        if session_matches(session, configuration)
    }
    slots_by_session_id = {entry.session_id: entry.slot for entry in slots}
    visible = [
        (
            slots_by_session_id[session.id],
            session,
            session_state(session),
        )
        for session in sessions_newest_first(eligible.values())
        if session.id in slots_by_session_id
    ]
    assigned = {entry.session_id for entry in slots}
    remaining = sum(
        session_id not in assigned and session_state(session) != "Unavailable"
        for session_id, session in eligible.items()
    )
    return visible, remaining
