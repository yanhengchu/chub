from __future__ import annotations

import logging
from datetime import datetime

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict, Field

from app.core.response import ApiError, ApiResponse
from app.core.security import require_trusted_network
from app.deliveryline.store import DeliverylineError, DeliverylineNotFound, DeliverylineReviewNotReady, DeliverylineTransitionNotAllowed, Requirement
from app.deliveryline.workflow import workflow_stage_data
from app.services.operation_log import log_operation


router = APIRouter(prefix="/api/deliveryline", tags=["deliveryline"], dependencies=[Depends(require_trusted_network)])
LOGGER = logging.getLogger("hub.deliveryline")


class RequirementCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    description: str = Field(min_length=1, max_length=4000)


class RequirementUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str = Field(min_length=1, max_length=120)
    background: str = Field(default="", max_length=4000)
    delivery_goal: str = Field(default="", max_length=4000)
    scope: str = Field(default="", max_length=4000)
    out_of_scope: str = Field(default="", max_length=4000)
    constraints: str = Field(default="", max_length=4000)
    acceptance_criteria: str = Field(default="", max_length=4000)
    risks_and_open_items: str = Field(default="", max_length=4000)


class CollaborationStart(BaseModel):
    model_config = ConfigDict(extra="forbid")
    comments: dict[str, str] = Field(default_factory=dict, max_length=8)


class CollaborationSettingsUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    show_sessions: bool


class RequirementData(BaseModel):
    id: str
    title: str
    is_initialized: bool
    original_request_content: str | None
    background: str
    delivery_goal: str
    scope: str
    out_of_scope: str
    constraints: str
    acceptance_criteria: str
    risks_and_open_items: str
    current_stage: str
    delivery_status: str
    next_action: str
    latest_stage_conclusion: str
    readiness_missing: list[str]
    activity: list[dict[str, str]]
    created_at: datetime
    updated_at: datetime
    collaboration: dict[str, object] | None = None


class DeliverylineOverview(BaseModel):
    in_progress: int
    action_required: int
    at_risk: int
    delivered: int
    requirements: list[RequirementData]
    archived_requirements: list[RequirementData]
    workflow_stages: list[dict[str, object]]


class RequirementDeleted(BaseModel):
    id: str


class CollaborationSettingsData(BaseModel):
    show_sessions: bool


def _data(record: Requirement, collaboration: dict[str, object] | None = None) -> RequirementData:
    from app.deliveryline.store import DeliverylineStore
    return RequirementData(
        id=record.id, title=record.title, is_initialized=record.is_initialized, original_request_content=record.original_request_content,
        background=record.background,
        delivery_goal=record.delivery_goal, scope=record.scope, out_of_scope=record.out_of_scope,
        constraints=record.constraints, acceptance_criteria=record.acceptance_criteria,
        risks_and_open_items=record.risks_and_open_items,
        current_stage=record.workflow.current_stage, delivery_status=record.workflow.delivery_status,
        next_action=record.workflow.next_action, latest_stage_conclusion=record.workflow.latest_stage_conclusion,
        readiness_missing=DeliverylineStore.review_missing(record),
        activity=[{"occurred_at": item.occurred_at.isoformat(), "action": item.action, "summary": item.summary} for item in record.activity],
        created_at=record.created_at, updated_at=record.updated_at,
        collaboration=collaboration,
    )


def _collaboration_data(request: Request, record: Requirement) -> dict[str, object] | None:
    try:
        return request.app.state.deliveryline_collaboration.status_for(record, request.app.state.quick_interactions)
    except DeliverylineError:
        LOGGER.warning("Unable to refresh Deliveryline collaboration state", exc_info=True)
        return None


def _deliveryline_plugin(request: Request) -> dict[str, object]:
    try:
        lifecycle = request.app.state.plugin_lifecycle.list(request)
        plugin = next(
            item
            for item in lifecycle.get("plugins", [])
            if isinstance(item, dict) and item.get("plugin_id") == "deliveryline"
        )
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
        enabled = next(
            (
                item
                for item in artifacts
                if isinstance(item, dict)
                and item.get("artifact_id") in enabled_ids
            ),
            None,
        ) if isinstance(artifacts, list) else None
        if enabled is None or enabled.get("available") is not True:
            raise ApiError(409, "deliveryline_unavailable", "Deliveryline 当前不可用。请在插件管理中检查状态。")
    except TypeError:
        raise ApiError(503, "deliveryline_unavailable", "Deliveryline 插件状态不可用。") from None
    return request.app.state.deliveryline_store


def _record(store, requirement_id: str) -> Requirement:
    try:
        return store.get(requirement_id)
    except DeliverylineError as exc:
        _raise(exc)
    raise AssertionError("unreachable")


def _raise(exc: DeliverylineError) -> None:
    if isinstance(exc, DeliverylineNotFound):
        raise ApiError(404, "deliveryline_requirement_not_found", str(exc)) from exc
    if isinstance(exc, DeliverylineReviewNotReady):
        raise ApiError(422, "deliveryline_review_not_ready", str(exc)) from exc
    if isinstance(exc, DeliverylineTransitionNotAllowed):
        raise ApiError(409, "deliveryline_transition_not_allowed", str(exc)) from exc
    raise ApiError(503, "deliveryline_store_unavailable", str(exc)) from exc


@router.get("", response_model=ApiResponse[DeliverylineOverview])
def overview(request: Request) -> ApiResponse[DeliverylineOverview]:
    try:
        records = _store(request).list(include_archived=True)
    except DeliverylineError as exc:
        _raise(exc)
    active = [record for record in records if record.workflow.delivery_status != "已归档"]
    archived = [record for record in records if record.workflow.delivery_status == "已归档"]
    counts = {status: sum(record.workflow.delivery_status == status for record in active) for status in ("进行中", "待我处理", "存在风险", "已交付")}
    return ApiResponse(data=DeliverylineOverview(in_progress=counts["进行中"], action_required=counts["待我处理"], at_risk=counts["存在风险"], delivered=counts["已交付"], requirements=[_data(record, _collaboration_data(request, record)) for record in active], archived_requirements=[_data(record, _collaboration_data(request, record)) for record in archived], workflow_stages=workflow_stage_data()))


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
        raise ApiError(422, "deliveryline_requirement_invalid", str(exc)) from exc
    log_operation(request, action=action, status="succeeded", target=target, operation_id=operation_id)
    return ApiResponse(data=_data(result))


@router.post("/requirements", response_model=ApiResponse[RequirementData])
def create_requirement(payload: RequirementCreate, request: Request) -> ApiResponse[RequirementData]:
    store = _store(request)
    return _operation(request, "create_deliveryline_requirement", "new", lambda: store.create(payload.description))


@router.put("/requirements/{requirement_id}", response_model=ApiResponse[RequirementData])
def update_requirement(requirement_id: str, payload: RequirementUpdate, request: Request) -> ApiResponse[RequirementData]:
    store = _store(request)
    return _operation(request, "update_deliveryline_requirement", requirement_id, lambda: store.update(requirement_id, payload.model_dump()))


@router.post("/requirements/{requirement_id}/submit-review", response_model=ApiResponse[RequirementData])
def submit_review(requirement_id: str, request: Request) -> ApiResponse[RequirementData]:
    store = _store(request)
    def submit() -> Requirement:
        record = _record(store, requirement_id)
        request.app.state.deliveryline_collaboration.ensure_stage_confirmation_ready(record)
        return store.submit_for_review(requirement_id)
    return _operation(request, "submit_deliveryline_requirement_review", requirement_id, submit)


@router.put("/requirements/{requirement_id}/archive", response_model=ApiResponse[RequirementData])
def archive_requirement(requirement_id: str, request: Request) -> ApiResponse[RequirementData]:
    store = _store(request)
    return _operation(request, "archive_deliveryline_requirement", requirement_id, lambda: store.archive(requirement_id))


@router.delete("/requirements/{requirement_id}", response_model=ApiResponse[RequirementDeleted])
def delete_requirement(requirement_id: str, request: Request) -> ApiResponse[RequirementDeleted]:
    store = _store(request)
    operation_id = log_operation(request, action="delete_deliveryline_requirement", status="requested", target=requirement_id)
    log_operation(request, action="delete_deliveryline_requirement", status="started", target=requirement_id, operation_id=operation_id)
    try:
        store.delete(requirement_id)
    except DeliverylineError as exc:
        log_operation(request, action="delete_deliveryline_requirement", status="failed", target=requirement_id, operation_id=operation_id, reason=exc.__class__.__name__)
        _raise(exc)
    try:
        request.app.state.deliveryline_collaboration.remove(requirement_id)
    except DeliverylineError:
        LOGGER.warning("Unable to remove deleted Deliveryline collaboration state", exc_info=True)
    log_operation(request, action="delete_deliveryline_requirement", status="succeeded", target=requirement_id, operation_id=operation_id)
    return ApiResponse(data=RequirementDeleted(id=requirement_id))


@router.post("/requirements/{requirement_id}/ai-collaboration", response_model=ApiResponse[RequirementData])
def start_ai_collaboration(requirement_id: str, request: Request, payload: CollaborationStart | None = None) -> ApiResponse[RequirementData]:
    store = _store(request)
    operation_id = log_operation(request, action="start_deliveryline_ai_collaboration", status="requested", target=requirement_id)
    log_operation(request, action="start_deliveryline_ai_collaboration", status="started", target=requirement_id, operation_id=operation_id)
    try:
        record = _record(store, requirement_id)
        result = request.app.state.deliveryline_collaboration.start(
            record,
            request.app.state.ai_session_manager,
            request.app.state.quick_interactions,
            comments=payload.comments if payload else {},
            source_ip=request.client.host if request.client else "unknown",
        )
    except DeliverylineError as exc:
        log_operation(request, action="start_deliveryline_ai_collaboration", status="failed", target=requirement_id, operation_id=operation_id, reason=exc.__class__.__name__)
        _raise(exc)
    except ApiError:
        log_operation(request, action="start_deliveryline_ai_collaboration", status="failed", target=requirement_id, operation_id=operation_id, reason="collaboration_unavailable")
        raise
    log_operation(request, action="start_deliveryline_ai_collaboration", status="succeeded", target=requirement_id, operation_id=operation_id)
    return ApiResponse(data=_data(record, result))


@router.post("/requirements/{requirement_id}/ai-collaboration/fields/{field}/accept", response_model=ApiResponse[RequirementData])
def accept_ai_collaboration_field(requirement_id: str, field: str, request: Request) -> ApiResponse[RequirementData]:
    store = _store(request)
    record = _record(store, requirement_id)
    try:
        value = request.app.state.deliveryline_collaboration.prepare_field_accept(record, field)
    except DeliverylineError as exc:
        _raise(exc)
    try:
        updated = store.update(requirement_id, {field: value})
    except DeliverylineError as exc:
        try:
            request.app.state.deliveryline_collaboration.reconcile_field_accept(record, field)
        except DeliverylineError:
            LOGGER.warning("Unable to restore Deliveryline field suggestion after a failed apply", exc_info=True)
        _raise(exc)
    try:
        request.app.state.deliveryline_collaboration.reconcile_field_accept(updated, field)
    except DeliverylineError:
        LOGGER.warning("Unable to finalize accepted Deliveryline field suggestion", exc_info=True)
    return ApiResponse(data=_data(updated, _collaboration_data(request, updated)))


@router.get("/settings", response_model=ApiResponse[CollaborationSettingsData])
def get_collaboration_settings(request: Request) -> ApiResponse[CollaborationSettingsData]:
    _deliveryline_plugin(request)
    return ApiResponse(data=CollaborationSettingsData(show_sessions=request.app.state.deliveryline_collaboration.show_sessions()))


@router.put("/settings", response_model=ApiResponse[CollaborationSettingsData])
def update_collaboration_settings(payload: CollaborationSettingsUpdate, request: Request) -> ApiResponse[CollaborationSettingsData]:
    _deliveryline_plugin(request)
    show_sessions = request.app.state.deliveryline_collaboration.set_show_sessions(payload.show_sessions)
    return ApiResponse(data=CollaborationSettingsData(show_sessions=show_sessions))
