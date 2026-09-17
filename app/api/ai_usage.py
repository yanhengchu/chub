from fastapi import APIRouter, Depends, Query, Request

from app.ai_usage.models import AiUsageData
from app.ai_runtime import (
    AiRuntimeGeneralSettingsData,
    RuntimeOperationError,
    RuntimeSettingsData,
    RuntimeSettingsField,
    RuntimeSettingsOption,
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
            "Runtime 默认项暂时无法读取。",
        ) from exc
    manager = request.app.state.ai_session_manager
    runtime_options: list[RuntimeSettingsOption] = []
    for runtime_id in manager.runtime_plugins.runtime_ids():
        try:
            implementation_id = manager.new_session_implementation_id(runtime_id)
        except ApiError:
            continue
        navigation = manager.runtime_plugins.require_navigation(implementation_id)
        runtime_options.append(
            RuntimeSettingsOption(
                value=runtime_id,
                label=navigation.name,
                description=navigation.description,
            )
        )
    session_defaults = RuntimeSettingsSection(
        id="session-defaults",
        title="会话默认配置",
        description="用于之后新建的 Chub Session 和未指定专属配置的自动化任务；已有 Session 保持创建时快照。",
        fields=(
            RuntimeSettingsField(
                id="session-default-runtime",
                label="默认 Runtime",
                description="当前可用于新建 Chub Session 的 AI Runtime。",
                input_type="select",
                value=general.default_runtime_id,
                options=tuple(runtime_options),
            ),
            RuntimeSettingsField(
                id="session-default-permission",
                label="默认权限",
                description="用于未在创建时明确选择权限的新 Session。",
                input_type="select",
                value=general.new_session_permission,
            ),
            RuntimeSettingsField(
                id="session-default-model",
                label="默认模型",
                description="未明确指定模型时使用；可选择跟随 Runtime 默认。",
                input_type="select",
                value=general.model or "__default__",
            ),
            RuntimeSettingsField(
                id="session-default-reasoning",
                label="默认推理等级",
                description="未明确指定推理等级时使用；可选择跟随 Runtime 默认。",
                input_type="select",
                value=general.reasoning_effort or "__default__",
            ),
        ),
    )
    return AiRuntimeGeneralSettingsData(
        sections=(session_defaults,),
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
            "session-default-runtime",
            "session-default-permission",
            "session-default-model",
            "session-default-reasoning",
        }
    ):
        raise ApiError(
            400,
            "ai_runtime_settings_invalid",
            "Runtime 默认项无效。",
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
        manager = request.app.state.ai_session_manager
        general = manager.runtime_settings_store.read_general()
        runtime_id = payload.values["session-default-runtime"]
        permission_mode = payload.values["session-default-permission"]
        model = payload.values["session-default-model"]
        reasoning_effort = payload.values["session-default-reasoning"]
        if not all(isinstance(value, str) and value.strip() for value in (runtime_id, permission_mode, model, reasoning_effort)):
            raise ValueError("session default settings are required")
        if permission_mode not in {"auto-review", "read-only", "full-access"}:
            raise ValueError("session default permission is invalid")
        implementation_id = manager.new_session_implementation_id(runtime_id)
        model = None if model == "__default__" else model
        reasoning_effort = None if reasoning_effort == "__default__" else reasoning_effort
        manager.validate_model(
            model,
            reasoning_effort,
            implementation_id=implementation_id,
        )
        general = AiRuntimeGeneralSettings.model_validate(
            {
                **general.model_dump(mode="json"),
                "default_runtime_id": runtime_id,
                "new_session_permission": permission_mode,
                "model": model,
                "reasoning_effort": reasoning_effort,
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
            "Runtime 默认项无效。",
        ) from exc
    try:
        manager.runtime_settings_store.save_general(general)
        manager.sync_default_runtime_selection()
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
            "Runtime 默认项暂时无法保存。",
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
