from __future__ import annotations

import logging

from app.automations.models import AutomationState


LOGGER = logging.getLogger("hub.operations")


def log_final_operation(state: AutomationState) -> AutomationState:
    if not state.operation_id or state.operation_logged:
        return state
    LOGGER.info(
        "operation_id=%s action=run_automation status=%s target=%s source_ip=%s",
        state.operation_id,
        "succeeded" if state.status == "success" else "failed",
        state.task_id,
        state.source_ip or "unknown",
    )
    return state.model_copy(update={"operation_logged": True})
