from __future__ import annotations

import logging

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.api.health import router as health_router
from app.api.status import router as status_router
from app.api.tasks import router as tasks_router
from app.core.config import Settings, load_settings
from app.core.logger import configure_logging
from app.core.platform import detect_platform
from app.core.response import (
    ApiError,
    api_error_handler,
    http_error_handler,
    internal_error_handler,
    validation_error_handler,
)
from app.tasks.definitions import build_task_registry
from app.tasks.executor import TaskExecutor


def create_app(settings: Settings | None = None) -> FastAPI:
    resolved_settings = settings or load_settings()
    configure_logging(resolved_settings.logs)

    detected_platform = detect_platform()
    logger = logging.getLogger("hub.startup")
    if not resolved_settings.security.token:
        logger.warning(
            "HUB_TOKEN is not set; health check remains available but protected APIs are disabled"
        )
    elif len(resolved_settings.security.token.get_secret_value()) < 32:
        logger.warning(
            "HUB_TOKEN is shorter than 32 characters; use a longer random token"
        )
    if resolved_settings.node.type != detected_platform:
        logger.warning(
            "configured_platform=%s detected_platform=%s",
            resolved_settings.node.type,
            detected_platform,
        )
    logger.info(
        "node_id=%s node_name=%s platform=%s version=%s",
        resolved_settings.node.id,
        resolved_settings.node.name,
        detected_platform,
        resolved_settings.app.version,
    )

    application = FastAPI(
        title=resolved_settings.app.name,
        version=resolved_settings.app.version,
    )
    application.state.settings = resolved_settings
    application.state.detected_platform = detected_platform
    application.state.task_registry = build_task_registry(
        resolved_settings.tasks.default_timeout
    )
    application.state.task_executor = TaskExecutor(
        application.state.task_registry,
        resolved_settings,
        detected_platform,
    )
    application.add_exception_handler(ApiError, api_error_handler)
    application.add_exception_handler(
        RequestValidationError,
        validation_error_handler,
    )
    application.add_exception_handler(StarletteHTTPException, http_error_handler)
    application.add_exception_handler(Exception, internal_error_handler)
    application.include_router(health_router)
    application.include_router(status_router)
    application.include_router(tasks_router)
    return application
