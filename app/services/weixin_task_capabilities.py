"""Bound in-process capabilities for one persisted Weixin orchestration request."""

from __future__ import annotations

import hashlib

from app.quick_worker_tasks import FINAL_STATUSES
from app.core.response import ApiError


class WeixinTaskCapabilityHost:
    """Never accepts a caller-selected Session outside its signed request context."""

    def __init__(self, quick_interactions, session_manager=None) -> None:
        self.quick_interactions = quick_interactions
        self.session_manager = session_manager

    def read_session(self, request, session_ref: str):
        if request.session_id is None or request.session_ref != session_ref:
            raise ApiError(409, "orchestration_session_ref_rejected", "编排请求不允许读取该 Session。")
        if self.session_manager is None:
            raise ApiError(503, "orchestration_session_unavailable", "Session 状态暂时不可用。")
        try:
            return self.session_manager.get_session(request.session_id)
        except ApiError:
            raise
        except Exception as exc:
            raise ApiError(503, "orchestration_session_unavailable", "Session 状态暂时不可用。") from exc

    def create_session(self, request, creation_ref: str, creator):
        if request.creation_ref != creation_ref:
            raise ApiError(409, "orchestration_creation_ref_rejected", "编排请求不允许创建 Session。")
        try:
            return creator()
        except ApiError:
            raise
        except Exception as exc:
            raise ApiError(503, "orchestration_session_unavailable", "Session 创建暂时不可用。") from exc

    def submit_task(self, request, session_ref: str, prompt: str, **kwargs):
        if request.session_ref != session_ref or request.session_id is None:
            raise ApiError(409, "orchestration_session_ref_rejected", "编排请求不允许使用该 Session。")
        return self.quick_interactions.submit(request.session_id, prompt, **kwargs)

    @staticmethod
    def task_ref(request_id: str, task_id: str) -> str:
        return hashlib.sha256(f"{request_id}:{task_id}".encode("utf-8")).hexdigest()

    def read_task(self, request, task_ref: str):
        if request.task_id is None or request.task_ref != task_ref:
            raise ApiError(409, "orchestration_task_ref_rejected", "编排请求不允许读取该任务。")
        try:
            return self.quick_interactions.get(request.task_id)
        except ApiError:
            raise
        except Exception as exc:
            raise ApiError(503, "orchestration_task_unavailable", "任务状态暂时不可用。") from exc

    def await_task(self, request, task_ref: str) -> str:
        """Return only a bounded orchestration result; never resubmit a task."""
        task = self.read_task(request, task_ref)
        status = getattr(task, "status", None)
        if not isinstance(status, str):
            return "unknown"
        if status == "succeeded":
            return "succeeded"
        if status in FINAL_STATUSES:
            return "rejected"
        return "waiting"
