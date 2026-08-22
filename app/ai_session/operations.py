from __future__ import annotations

import logging

from app.core.response import ApiError


LOGGER = logging.getLogger("hub.ai_session.operations")


def archive_session(
    session_id: str,
    *,
    manager,
    quick_interactions,
    terminal_tickets,
    terminal_connections,
    release_slot=None,
) -> None:
    """Run the one archive workflow shared by Web and ClawBot entry points."""
    with quick_interactions.stop_operation_guard(session_id):
        try:
            manager.archive_native_session(session_id)
        except ApiError as exc:
            # A stale page may race with native reconciliation that already
            # removed the Chub mapping. The archive goal is already reached;
            # continue the idempotent cleanup instead of surfacing a 404.
            if exc.code != "codex_session_not_found":
                raise
        quick_interactions.cancel_codex_session(session_id)
        quick_interactions.remove_session_tasks(session_id)
        terminal_tickets.revoke_session(session_id)
        terminal_connections.close_session(session_id)
        if release_slot is not None and not release_slot(session_id):
            raise ApiError(
                503,
                "weixin_chub_mode_slot_release_unknown",
                "Session 已完成原生归档，但关联槽位释放状态无法确认，请刷新后重试。",
            )
        manager.finalize_archive_session(session_id)


def delete_session(
    session_id: str,
    *,
    manager,
    quick_interactions,
    terminal_tickets,
    terminal_connections,
    release_slot=None,
) -> None:
    """Run the destructive Session workflow shared by Web entry points."""
    with quick_interactions.destructive_operation_guard(session_id):
        try:
            manager.ensure_delete_allowed(session_id, reconcile=False)
        except ApiError as exc:
            # A concurrent native reconciliation may already have removed the
            # mapping. Treat that stale-page case as an idempotent cleanup.
            if exc.code != "codex_session_not_found":
                raise
        # Delete is allowed to attempt a running Quick Worker cleanup. The
        # native action is still blocked until cancellation reaches a final
        # state, so a failed cancellation never deletes a live writer.
        quick_interactions.cancel_codex_session(session_id)
        terminal_tickets.revoke_session(session_id)
        terminal_connections.close_session(session_id)
        try:
            # Close Chub's own terminal carrier before asking Runtime to delete
            # the native Session. This is cleanup, not a state gate: if Chub
            # lost the carrier after a restart, native delete is still tried.
            manager.stop_session(session_id, reconcile=False)
        except Exception as exc:
            if getattr(exc, "code", None) != "codex_session_not_found":
                LOGGER.warning(
                    "Unable to close Chub terminal before Session deletion",
                    extra={
                        "session_id": session_id,
                        "error_code": getattr(exc, "code", None),
                    },
                    exc_info=True,
                )
        try:
            manager.delete_native_session(session_id)
        except ApiError as exc:
            # A stale page may race with native reconciliation that already
            # removed the Chub mapping. Deletion is already complete for that
            # mapping, so continue clearing retained Chub-side state.
            if exc.code != "codex_session_not_found":
                raise
        quick_interactions.remove_session_tasks(session_id)
        if release_slot is not None and not release_slot(session_id):
            raise ApiError(
                503,
                "weixin_chub_mode_slot_release_unknown",
                "Session 已完成原生删除，但关联槽位释放状态无法确认，请稍后重试。",
            )
        manager.finalize_delete_session(
            session_id,
            terminal_already_closed=True,
        )
