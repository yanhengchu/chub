from types import SimpleNamespace
from datetime import datetime, timezone

from app.services.openclaw_weixin_chub_models import (
    WeixinChubModeRuntimeConfig,
    WeixinChubModeSessionSlot,
)
from app.services.openclaw_weixin_chub_sessions import (
    build_synced_slots,
    collect_assigned_session_snapshots,
    visible_sessions,
)


def _configuration() -> WeixinChubModeRuntimeConfig:
    return WeixinChubModeRuntimeConfig(
        workspace_id="chub",
        permission_mode="full-access",
    )


def _session(session_id: str, *, title: str, activity: str = "idle") -> object:
    return SimpleNamespace(
        id=session_id,
        title=title,
        workspace_name="Chub",
        workspace_id="chub",
        permission_mode="full-access",
        activity=activity,
        model="gpt-5",
        reasoning_effort="medium",
        created_at=datetime(
            2026,
            9,
            11 if session_id == "session-1" else 10,
            tzinfo=timezone.utc,
        ),
    )


def _matches(session: object, _configuration: object) -> bool:
    return getattr(session, "workspace_id", None) == "chub"


def _state(session: object) -> str:
    return "Busy" if getattr(session, "activity", None) == "working" else "Available"


def test_session_directory_preserves_slots_and_builds_read_only_snapshots() -> None:
    configuration = _configuration()
    sessions = [
        _session("session-2", title="第二项"),
        _session("session-1", title="第一项", activity="working"),
    ]
    original_slots = [WeixinChubModeSessionSlot(slot=3, session_id="session-1")]

    slots = build_synced_slots(
        configuration=configuration,
        original_slots=original_slots,
        current_session_id="session-1",
        sessions=sessions,
        weixin_session_ids={"session-1"},
        fill_candidates=True,
        session_matches=_matches,
        session_state=_state,
    )

    assert [(item.slot, item.session_id) for item in slots] == [
        (1, "session-2"),
        (3, "session-1"),
    ]
    snapshots = collect_assigned_session_snapshots(
        sessions=sessions,
        configuration=configuration,
        current_session_id="session-1",
        slots=slots,
        session_name_max_width=30,
        session_matches=_matches,
        session_state=_state,
        workspace_name=lambda session: getattr(session, "workspace_name", None),
    )

    assert [(item.slot, item.title, item.state, item.current) for item in snapshots] == [
        (3, "第一项", "Busy", True),
        (1, "第二项", "Available", False),
    ]


def test_visible_sessions_excludes_unassigned_sessions_from_the_list() -> None:
    configuration = _configuration()
    sessions = [
        _session("session-1", title="当前"),
        _session("session-2", title="候选"),
    ]

    visible, remaining = visible_sessions(
        sessions=sessions,
        configuration=configuration,
        slots=[WeixinChubModeSessionSlot(slot=2, session_id="session-1")],
        session_matches=_matches,
        session_state=_state,
    )

    assert [(slot, session.id, state) for slot, session, state in visible] == [
        (2, "session-1", "Available"),
    ]
    assert remaining == 1


def test_slot_sync_without_fill_only_assigns_the_current_session() -> None:
    configuration = _configuration()
    sessions = [
        _session("session-1", title="当前"),
        _session("session-2", title="候选"),
    ]

    slots = build_synced_slots(
        configuration=configuration,
        original_slots=[],
        current_session_id="session-1",
        sessions=sessions,
        weixin_session_ids=set(),
        fill_candidates=False,
        session_matches=_matches,
        session_state=_state,
    )

    assert [(item.slot, item.session_id) for item in slots] == [(1, "session-1")]
