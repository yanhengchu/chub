from __future__ import annotations

import logging
from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict, Field

from app.core.response import ApiError, ApiResponse
from app.core.security import require_trusted_network
from app.deliveryline.store import DeliveryLine, DeliverylineError, DeliverylineNotFound, DeliverylineTransitionNotAllowed
from app.services.operation_log import log_operation


router = APIRouter(prefix="/api/deliveryline", tags=["deliveryline"], dependencies=[Depends(require_trusted_network)])
LOGGER = logging.getLogger("hub.deliveryline")


class LineCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source: str = Field(min_length=1, max_length=4000)


class ClarificationStart(BaseModel):
    model_config = ConfigDict(extra="forbid")
    comment: str = Field(default="", max_length=4000)


class GoalConfirmation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str = Field(min_length=1, max_length=120)
    source_role: Literal["相对完整需求", "持续演进需求", "设计提案", "现状说明", "混合资料"]
    overall_goal: str = Field(min_length=1, max_length=4000)
    confirmed_facts: list[str] = Field(default_factory=list, max_length=30)
    scope_boundary: str = Field(default="", max_length=4000)
    open_questions: list[str] = Field(default_factory=list, max_length=30)


class CollaborationSettingsUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    show_sessions: bool


class LineData(BaseModel):
    id: str
    title: str
    original_request_content: str
    status: str
    source_role: str
    overall_goal: str
    confirmed_facts: list[str]
    scope_boundary: str
    open_questions: list[str]
    goal_confirmed: bool
    goal_versions: list[dict[str, object]]
    activity: list[dict[str, str]]
    created_at: datetime
    updated_at: datetime
    collaboration: dict[str, object] | None = None


class DeliverylineOverview(BaseModel):
    pending_clarification: int
    planning: int
    progressing: int
    change_assessment: int
    lines: list[LineData]
    ended_lines: list[LineData]


class LineDeleted(BaseModel):
    id: str


class CollaborationSettingsData(BaseModel):
    show_sessions: bool


def _data(record: DeliveryLine, collaboration: dict[str, object] | None = None) -> LineData:
    return LineData(
        id=record.id,
        title=record.title,
        original_request_content=record.original_request_content,
        status=record.status,
        source_role=record.source_role,
        overall_goal=record.overall_goal,
        confirmed_facts=record.confirmed_facts,
        scope_boundary=record.scope_boundary,
        open_questions=record.open_questions,
        goal_confirmed=record.goal_confirmed,
        goal_versions=[item.model_dump(mode="json") for item in record.goal_versions],
        activity=[{"occurred_at": item.occurred_at.isoformat(), "action": item.action, "summary": item.summary} for item in record.activity],
        created_at=record.created_at,
        updated_at=record.updated_at,
        collaboration=collaboration,
    )


def _collaboration_data(request: Request, record: DeliveryLine) -> dict[str, object] | None:
    try:
        return request.app.state.deliveryline_collaboration.status_for(record, request.app.state.quick_interactions)
    except DeliverylineError:
        LOGGER.warning("Unable to refresh Deliveryline clarification state", exc_info=True)
        return None


def _deliveryline_plugin(request: Request) -> dict[str, object]:
    try:
        lifecycle = request.app.state.plugin_lifecycle.list(request)
        plugin = next(item for item in lifecycle.get("plugins", []) if isinstance(item, dict) and item.get("plugin_id") == "deliveryline")
        if not plugin.get("imported_artifact_ids"):
            raise ApiError(404, "deliveryline_not_imported", "Deliveryline 尚未导入。")
        return plugin
    except StopIteration:
        raise ApiError(404, "deliveryline_not_imported", "Deliveryline 尚未导入。") from None


def _store(request: Request):
    plugin = _deliveryline_plugin(request)
    try:
        enabled_ids = plugin.get("enabled_artifact_ids")
        if not isinstance(enabled_ids, list) or not enabled_ids:
            raise ApiError(409, "deliveryline_not_enabled", "请先在插件管理中启用 Deliveryline。")
        artifacts = plugin.get("artifacts")
        enabled = next((item for item in artifacts if isinstance(item, dict) and item.get("artifact_id") in enabled_ids), None) if isinstance(artifacts, list) else None
        if enabled is None or enabled.get("available") is not True:
            raise ApiError(409, "deliveryline_unavailable", "Deliveryline 当前不可用。请在插件管理中检查状态。")
    except TypeError:
        raise ApiError(503, "deliveryline_unavailable", "Deliveryline 插件状态不可用。") from None
    return request.app.state.deliveryline_store


def _record(store, line_id: str) -> DeliveryLine:
    try:
        return store.get(line_id)
    except DeliverylineError as exc:
        _raise(exc)
    raise AssertionError("unreachable")


def _raise(exc: DeliverylineError) -> None:
    if isinstance(exc, DeliverylineNotFound):
        raise ApiError(404, "deliveryline_line_not_found", str(exc)) from exc
    if isinstance(exc, DeliverylineTransitionNotAllowed):
        raise ApiError(409, "deliveryline_transition_not_allowed", str(exc)) from exc
    raise ApiError(503, "deliveryline_store_unavailable", str(exc)) from exc


@router.get("", response_model=ApiResponse[DeliverylineOverview])
def overview(request: Request) -> ApiResponse[DeliverylineOverview]:
    try:
        records = _store(request).list(include_ended=True)
    except DeliverylineError as exc:
        _raise(exc)
    active = [record for record in records if record.status != "已结束"]
    ended = [record for record in records if record.status == "已结束"]
    counts = {status: sum(record.status == status for record in active) for status in ("待澄清", "规划中", "推进中", "变更评估中")}
    return ApiResponse(data=DeliverylineOverview(
        pending_clarification=counts["待澄清"], planning=counts["规划中"], progressing=counts["推进中"], change_assessment=counts["变更评估中"],
        lines=[_data(record, _collaboration_data(request, record)) for record in active],
        ended_lines=[_data(record, _collaboration_data(request, record)) for record in ended],
    ))


def _operation(request: Request, action: str, target: str, callback):
    operation_id = log_operation(request, action=action, status="requested", target=target)
    log_operation(request, action=action, status="started", target=target, operation_id=operation_id)
    try:
        result = callback()
    except DeliverylineError as exc:
        log_operation(request, action=action, status="failed", target=target, operation_id=operation_id, reason=exc.__class__.__name__)
        _raise(exc)
    except ValueError as exc:
        log_operation(request, action=action, status="failed", target=target, operation_id=operation_id, reason="invalid_data")
        raise ApiError(422, "deliveryline_line_invalid", str(exc)) from exc
    log_operation(request, action=action, status="succeeded", target=target, operation_id=operation_id)
    return result


@router.post("/lines", response_model=ApiResponse[LineData])
def create_line(payload: LineCreate, request: Request) -> ApiResponse[LineData]:
    record = _operation(request, "create_deliveryline_line", "new", lambda: _store(request).create(payload.source))
    return ApiResponse(data=_data(record))


@router.post("/lines/{line_id}/ai-clarification", response_model=ApiResponse[LineData])
def start_ai_clarification(line_id: str, request: Request, payload: ClarificationStart | None = None) -> ApiResponse[LineData]:
    store = _store(request)
    operation_id = log_operation(request, action="start_deliveryline_ai_clarification", status="requested", target=line_id)
    log_operation(request, action="start_deliveryline_ai_clarification", status="started", target=line_id, operation_id=operation_id)
    try:
        record = _record(store, line_id)
        result = request.app.state.deliveryline_collaboration.start(record, request.app.state.ai_session_manager, request.app.state.quick_interactions, comment=payload.comment if payload else "", source_ip=request.client.host if request.client else "unknown")
    except DeliverylineError as exc:
        log_operation(request, action="start_deliveryline_ai_clarification", status="failed", target=line_id, operation_id=operation_id, reason=exc.__class__.__name__)
        _raise(exc)
    except ApiError:
        log_operation(request, action="start_deliveryline_ai_clarification", status="failed", target=line_id, operation_id=operation_id, reason="clarification_unavailable")
        raise
    log_operation(request, action="start_deliveryline_ai_clarification", status="succeeded", target=line_id, operation_id=operation_id)
    return ApiResponse(data=_data(record, result))


@router.post("/lines/{line_id}/confirm-goal", response_model=ApiResponse[LineData])
def confirm_goal(line_id: str, payload: GoalConfirmation, request: Request) -> ApiResponse[LineData]:
    store = _store(request)
    def confirm() -> DeliveryLine:
        record = _record(store, line_id)
        request.app.state.deliveryline_collaboration.ensure_goal_confirmation_ready(record, request.app.state.quick_interactions)
        return store.confirm_goal(line_id, payload.model_dump())
    record = _operation(request, "confirm_deliveryline_goal", line_id, confirm)
    return ApiResponse(data=_data(record, _collaboration_data(request, record)))


@router.put("/lines/{line_id}/end", response_model=ApiResponse[LineData])
def end_line(line_id: str, request: Request) -> ApiResponse[LineData]:
    record = _operation(request, "end_deliveryline_line", line_id, lambda: _store(request).end(line_id))
    return ApiResponse(data=_data(record, _collaboration_data(request, record)))


@router.delete("/lines/{line_id}", response_model=ApiResponse[LineDeleted])
def delete_line(line_id: str, request: Request) -> ApiResponse[LineDeleted]:
    store = _store(request)
    operation_id = log_operation(request, action="delete_deliveryline_line", status="requested", target=line_id)
    log_operation(request, action="delete_deliveryline_line", status="started", target=line_id, operation_id=operation_id)
    try:
        _record(store, line_id)
        request.app.state.deliveryline_collaboration.delete_associated_session(
            line_id,
            request.app.state.ai_session_manager,
            request.app.state.quick_interactions,
        )
        store.delete(line_id)
    except DeliverylineError as exc:
        log_operation(request, action="delete_deliveryline_line", status="failed", target=line_id, operation_id=operation_id, reason=exc.__class__.__name__)
        _raise(exc)
    except ApiError as exc:
        log_operation(request, action="delete_deliveryline_line", status="failed", target=line_id, operation_id=operation_id, reason=exc.code)
        raise
    try:
        request.app.state.deliveryline_collaboration.remove(line_id)
    except DeliverylineError:
        LOGGER.warning("Unable to remove deleted Deliveryline clarification state", exc_info=True)
    log_operation(request, action="delete_deliveryline_line", status="succeeded", target=line_id, operation_id=operation_id)
    return ApiResponse(data=LineDeleted(id=line_id))


@router.get("/settings", response_model=ApiResponse[CollaborationSettingsData])
def get_collaboration_settings(request: Request) -> ApiResponse[CollaborationSettingsData]:
    _deliveryline_plugin(request)
    return ApiResponse(data=CollaborationSettingsData(show_sessions=request.app.state.deliveryline_collaboration.show_sessions()))


@router.put("/settings", response_model=ApiResponse[CollaborationSettingsData])
def update_collaboration_settings(payload: CollaborationSettingsUpdate, request: Request) -> ApiResponse[CollaborationSettingsData]:
    _deliveryline_plugin(request)
    return ApiResponse(data=CollaborationSettingsData(show_sessions=request.app.state.deliveryline_collaboration.set_show_sessions(payload.show_sessions)))
