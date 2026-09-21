from __future__ import annotations

from fastapi import Request

from app.core.response import ApiError

from .store import DeliveryLine, DeliverylineError


def workspace_state(request: Request) -> dict[str, str] | None:
    """Return the module-owned projection used by the shared workspace shell."""
    try:
        lifecycle = request.app.state.plugin_lifecycle.list(request)
    except (ApiError, OSError):
        return None
    plugins = lifecycle.get("plugins")
    if not isinstance(plugins, list):
        return None
    plugin = next(
        (
            item
            for item in plugins
            if isinstance(item, dict) and item.get("plugin_id") == "deliveryline"
        ),
        None,
    )
    if plugin is None:
        return None
    imported_ids = plugin.get("imported_artifact_ids")
    enabled_ids = plugin.get("enabled_artifact_ids")
    artifacts = plugin.get("artifacts")
    if not isinstance(imported_ids, list) or not imported_ids:
        return None
    if not isinstance(enabled_ids, list):
        enabled_ids = []
    if not isinstance(artifacts, list):
        artifacts = []
    imported = next(
        (
            item
            for item in artifacts
            if isinstance(item, dict) and item.get("artifact_id") in imported_ids
        ),
        {},
    )
    enabled = next(
        (
            item
            for item in artifacts
            if isinstance(item, dict) and item.get("artifact_id") in enabled_ids
        ),
        None,
    )
    if enabled is None or not enabled.get("available"):
        return None
    return {
        "status": "enabled",
        "label": "已启用",
        "description": str(
            imported.get("description") or "需求交付管理业务插件；当前提供插件生命周期与状态入口。"
        ),
        "detail": "Deliveryline 插件已启用，业务页面由当前模块版本提供。",
    }


def workspace_records(
    request: Request,
) -> tuple[list[DeliveryLine], list[DeliveryLine]]:
    """Return active and ended records for the module-owned workspace page."""
    try:
        records = request.app.state.deliveryline_store.list(include_ended=True)
    except DeliverylineError:
        raise
    return (
        [record for record in records if record.status != "已结束"],
        [record for record in records if record.status == "已结束"],
    )
