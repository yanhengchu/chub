"""Restricted host use case for a stage's internal standard AI task.

The plugin supplies instructions and validates its result. It never receives a
Session Manager, Worker client, notification route or configurable filesystem path.
"""

import hashlib
import json
from uuid import NAMESPACE_URL, uuid5

from app.ai_interactions.models import QuickInteractionTask


class PromptOptimizationUseCase:
    def __init__(self, session_manager, quick_interactions):
        self.session_manager = session_manager
        self.quick_interactions = quick_interactions

    def execution_snapshot(self) -> dict[str, str | None]:
        return self.session_manager.internal_session_execution_snapshot()

    def restore_session(self, stage_call_id: str, snapshot: dict[str, str | None]) -> str:
        del stage_call_id  # Stage requests share one plugin-owned internal Session.
        session_key = "chub:task-prompt-optimizer:internal-session:v1"
        fingerprint = hashlib.sha256(session_key.encode("utf-8")).hexdigest()
        session = self.session_manager.create_session(
            "chub", session_kind="internal",
            creation_request_id=str(uuid5(NAMESPACE_URL, session_key)),
            creation_request_fingerprint=fingerprint,
            internal_execution_snapshot=snapshot,
            internal_title="任务提示词优化",
        )
        return session.id

    def submit(self, session_id: str, prompt: str, stage_call_id: str) -> QuickInteractionTask:
        existing = self.quick_interactions.find_for_operation(stage_call_id)
        if existing is not None:
            if existing.session_id != session_id or existing.prompt != prompt:
                raise ValueError("Internal stage task identity mismatch")
            return existing
        with self.quick_interactions.session_operation_guard(session_id):
            return self.quick_interactions.submit(
                session_id, prompt, operation_id=stage_call_id, source_ip="127.0.0.1",
                suppress_completion_notification=True,
                prompt_processing=True,
                accepted_orchestration_task=True,
            )

    def find_for_operation(self, operation_id: str) -> QuickInteractionTask | None:
        return self.quick_interactions.find_for_operation(operation_id)

    def get(self, task_id: str) -> QuickInteractionTask:
        return self.quick_interactions.get(task_id)

    def cancel(self, task_id: str) -> bool:
        return self.quick_interactions.cancel_task(task_id)
