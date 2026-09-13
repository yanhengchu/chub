from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict, Field

from app.core.response import ApiError, ApiResponse
from app.core.security import require_trusted_network
from app.deliveryline.store import DeliverylineError, DeliverylineNotFound, DeliverylineReviewNotReady, Requirement
from app.services.operation_log import log_operation


router = APIRouter(prefix="/api/deliveryline", tags=["deliveryline"], dependencies=[Depends(require_trusted_network)])


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


class RequirementData(BaseModel):
    id: str
    title: str
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


class DeliverylineOverview(BaseModel):
    in_progress: int
    action_required: int
    at_risk: int
    delivered: int
    requirements: list[RequirementData]
    archived_requirements: list[RequirementData]


def _data(record: Requirement) -> RequirementData:
    from app.deliveryline.store import DeliverylineStore
    return RequirementData(
        id=record.id, title=record.title, background=record.background,
        delivery_goal=record.delivery_goal, scope=record.scope, out_of_scope=record.out_of_scope,
        constraints=record.constraints, acceptance_criteria=record.acceptance_criteria,
        risks_and_open_items=record.risks_and_open_items,
        current_stage=record.workflow.current_stage, delivery_status=record.workflow.delivery_status,
        next_action=record.workflow.next_action, latest_stage_conclusion=record.workflow.latest_stage_conclusion,
        readiness_missing=DeliverylineStore.review_missing(record),
        activity=[{"occurred_at": item.occurred_at.isoformat(), "action": item.action, "summary": item.summary} for item in record.activity],
        created_at=record.created_at, updated_at=record.updated_at,
    )


def _store(request: Request):
    try:
        lifecycle = request.app.state.plugin_lifecycle.list(request)
        plugin = next(
            item
            for item in lifecycle.get("plugins", [])
            if isinstance(item, dict) and item.get("plugin_id") == "deliveryline"
        )
        if not plugin.get("imported_artifact_ids"):
            raise ApiError(404, "deliveryline_not_imported", "Deliveryline 尚未导入。")
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
    except StopIteration:
        raise ApiError(404, "deliveryline_not_imported", "Deliveryline 尚未导入。") from None
    return request.app.state.deliveryline_store


def _raise(exc: DeliverylineError) -> None:
    if isinstance(exc, DeliverylineNotFound):
        raise ApiError(404, "deliveryline_requirement_not_found", str(exc)) from exc
    if isinstance(exc, DeliverylineReviewNotReady):
        raise ApiError(422, "deliveryline_review_not_ready", str(exc)) from exc
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
    return ApiResponse(data=DeliverylineOverview(in_progress=counts["进行中"], action_required=counts["待我处理"], at_risk=counts["存在风险"], delivered=counts["已交付"], requirements=[_data(record) for record in active], archived_requirements=[_data(record) for record in archived]))


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
    return _operation(request, "submit_deliveryline_requirement_review", requirement_id, lambda: store.submit_for_review(requirement_id))


@router.put("/requirements/{requirement_id}/archive", response_model=ApiResponse[RequirementData])
def archive_requirement(requirement_id: str, request: Request) -> ApiResponse[RequirementData]:
    store = _store(request)
    return _operation(request, "archive_deliveryline_requirement", requirement_id, lambda: store.archive(requirement_id))
