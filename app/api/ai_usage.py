from fastapi import APIRouter, Depends, Query, Request

from app.ai_usage.models import AiUsageData
from app.ai_runtime import (
    AiRuntimeGeneralSettingsData,
    RuntimeOperationError,
    RuntimeSettingsData,
    RuntimeSettingsField,
    RuntimeSettingsSection,
    RuntimeSettingsUpdate,
)
from app.ai_runtime.general_settings import (
    AiRuntimeGeneralSettings,
    RuntimeSettingsStoreUnavailable,
)
from app.core.response import ApiError, ApiResponse
from app.core.security import require_trusted_network
from app.services.operation_log import log_operation


router = APIRouter(
    prefix="/api/ai",
    tags=["ai-usage"],
    dependencies=[Depends(require_trusted_network)],
)


@router.get("/usage", response_model=ApiResponse[AiUsageData])
def read_ai_usage(
    request: Request,
    refresh: bool = Query(default=False),
) -> ApiResponse[AiUsageData]:
    try:
        usage = request.app.state.ai_usage.read(force=refresh)
    except RuntimeOperationError as exc:
        raise ApiError(
            503,
            "ai_runtime_unavailable",
            "AI Runtime 当前不可用。",
        ) from exc
    return ApiResponse(data=usage)


def _general_runtime_settings(request: Request) -> AiRuntimeGeneralSettingsData:
    try:
        general = request.app.state.ai_session_manager.runtime_settings_store.read_general()
    except RuntimeSettingsStoreUnavailable as exc:
        raise ApiError(
            503,
            "ai_runtime_settings_unavailable",
            "AI Runtime 通用配置暂时无法读取。",
        ) from exc
    manager = request.app.state.ai_session_manager
    weekly_runtime_available = manager.runtime_id in manager.runtime_modules.runtime_ids()
    weekly_report_section = RuntimeSettingsSection(
        id="weekly-report-session",
        title="周报自动化会话",
        description=(
            "用于生成重点确认清单和正式周报的新建 Quick Session。"
            if weekly_runtime_available
            else "当前没有可用于周报生成的 AI Runtime；资料下载仍可独立运行。"
        ),
        fields=(
            (
                RuntimeSettingsField(
                    id="weekly-report-runtime",
                    label="AI Runtime",
                    description="当前只接入 Codex；后续已接入 Runtime 会在这里提供选择。",
                    input_type="select",
                    value=general.weekly_report_session.runtime_id or manager.runtime_id,
                ),
                RuntimeSettingsField(
                    id="weekly-report-permission",
                    label="权限",
                    description="只读权限不能生成周报产物，运行入口会保持不可用。",
                    input_type="select",
                    value=general.weekly_report_session.permission_mode,
                ),
                RuntimeSettingsField(
                    id="weekly-report-model",
                    label="模型",
                    description="仅影响之后新建的周报生成 Session。",
                    input_type="select",
                    value=general.weekly_report_session.model or "__default__",
                ),
                RuntimeSettingsField(
                    id="weekly-report-reasoning",
                    label="推理等级",
                    description="仅影响之后新建的周报生成 Session。",
                    input_type="select",
                    value=general.weekly_report_session.reasoning_effort or "__default__",
                ),
            )
            if weekly_runtime_available
            else ()
        ),
    )
    return AiRuntimeGeneralSettingsData(
        sections=(weekly_report_section,),
    )


@router.get(
    "/settings",
    response_model=ApiResponse[AiRuntimeGeneralSettingsData],
)
def read_general_runtime_settings(
    request: Request,
) -> ApiResponse[AiRuntimeGeneralSettingsData]:
    return ApiResponse(data=_general_runtime_settings(request))


@router.put(
    "/settings",
    response_model=ApiResponse[AiRuntimeGeneralSettingsData],
)
def update_general_runtime_settings(
    payload: RuntimeSettingsUpdate,
    request: Request,
) -> ApiResponse[AiRuntimeGeneralSettingsData]:
    field_ids = set(payload.values)
    if frozenset(field_ids) != frozenset(
        {
            "weekly-report-runtime",
            "weekly-report-permission",
            "weekly-report-model",
            "weekly-report-reasoning",
        }
    ):
        raise ApiError(
            400,
            "ai_runtime_settings_invalid",
            "AI Runtime 通用配置无效。",
        )
    operation_id = log_operation(
        request,
        action="update_ai_runtime_general_settings",
        status="requested",
        target="general",
    )
    log_operation(
        request,
        action="update_ai_runtime_general_settings",
        status="started",
        target="general",
        operation_id=operation_id,
    )
    try:
        general = request.app.state.ai_session_manager.runtime_settings_store.read_general()
        runtime_id = payload.values["weekly-report-runtime"]
        permission_mode = payload.values["weekly-report-permission"]
        model = payload.values["weekly-report-model"]
        reasoning_effort = payload.values["weekly-report-reasoning"]
        if not all(isinstance(value, str) and value.strip() for value in (runtime_id, permission_mode, model, reasoning_effort)):
            raise ValueError("weekly report session settings are required")
        if runtime_id not in request.app.state.ai_session_manager.runtime_modules.runtime_ids():
            raise ApiError(
                409,
                "weekly_report_runtime_unavailable",
                "当前周报自动化 Runtime 不可用。",
            )
        model = None if model == "__default__" else model
        reasoning_effort = None if reasoning_effort == "__default__" else reasoning_effort
        request.app.state.ai_session_manager.validate_model(model, reasoning_effort)
        general = AiRuntimeGeneralSettings.model_validate(
            {
                **general.model_dump(mode="json"),
                "weekly_report_session": {
                    "runtime_id": runtime_id,
                    "permission_mode": permission_mode,
                    "model": model,
                    "reasoning_effort": reasoning_effort,
                },
            }
        )
    except ApiError:
        log_operation(
            request,
            action="update_ai_runtime_general_settings",
            status="failed",
            target="general",
            operation_id=operation_id,
        )
        raise
    except ValueError as exc:
        log_operation(
            request,
            action="update_ai_runtime_general_settings",
            status="failed",
            target="general",
            operation_id=operation_id,
        )
        raise ApiError(
            400,
            "ai_runtime_settings_invalid",
            "AI Runtime 通用配置无效。",
        ) from exc
    try:
        request.app.state.ai_session_manager.runtime_settings_store.save_general(general)
    except RuntimeSettingsStoreUnavailable as exc:
        log_operation(
            request,
            action="update_ai_runtime_general_settings",
            status="failed",
            target="general",
            operation_id=operation_id,
        )
        raise ApiError(
            503,
            "ai_runtime_settings_unavailable",
            "AI Runtime 通用配置暂时无法保存。",
        ) from exc
    log_operation(
        request,
        action="update_ai_runtime_general_settings",
        status="succeeded",
        target="general",
        operation_id=operation_id,
    )
    return ApiResponse(data=_general_runtime_settings(request))


def _runtime_settings_adapter(request: Request, runtime_id: str):
    try:
        return request.app.state.ai_session_manager.runtime_registry.require(
            runtime_id,
            {"runtime_settings"},
        )
    except RuntimeOperationError as exc:
        status = 404 if exc.code == "runtime_unavailable" else 409
        raise ApiError(status, exc.code, exc.message) from exc


@router.get(
    "/runtimes/{runtime_id}/settings",
    response_model=ApiResponse[RuntimeSettingsData],
)
def read_runtime_settings(
    runtime_id: str,
    request: Request,
) -> ApiResponse[RuntimeSettingsData]:
    adapter = _runtime_settings_adapter(request, runtime_id)
    try:
        return ApiResponse(data=adapter.read_runtime_settings())
    except RuntimeOperationError as exc:
        raise ApiError(503, exc.code, exc.message) from exc


@router.put(
    "/runtimes/{runtime_id}/settings",
    response_model=ApiResponse[RuntimeSettingsData],
)
def update_runtime_settings(
    runtime_id: str,
    payload: RuntimeSettingsUpdate,
    request: Request,
) -> ApiResponse[RuntimeSettingsData]:
    adapter = _runtime_settings_adapter(request, runtime_id)
    operation_id = log_operation(
        request,
        action="update_ai_runtime_settings",
        status="requested",
        target=runtime_id,
    )
    log_operation(
        request,
        action="update_ai_runtime_settings",
        status="started",
        target=runtime_id,
        operation_id=operation_id,
    )
    try:
        data = adapter.update_runtime_settings(payload)
    except RuntimeOperationError as exc:
        log_operation(
            request,
            action="update_ai_runtime_settings",
            status="failed",
            target=runtime_id,
            operation_id=operation_id,
        )
        status = 400 if exc.kind == "invalid_request" else 503
        raise ApiError(status, exc.code, exc.message) from exc
    log_operation(
        request,
        action="update_ai_runtime_settings",
        status="succeeded",
        target=runtime_id,
        operation_id=operation_id,
    )
    return ApiResponse(data=data)
