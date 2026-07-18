from __future__ import annotations

import logging

from fastapi import FastAPI

from app.api.health import router as health_router
from app.core.config import Settings, load_settings
from app.core.logger import configure_logging
from app.core.platform import detect_platform


def create_app(settings: Settings | None = None) -> FastAPI:
    resolved_settings = settings or load_settings()
    configure_logging(resolved_settings.logs)

    detected_platform = detect_platform()
    logger = logging.getLogger("hub.startup")
    if resolved_settings.security.enabled and not resolved_settings.security.token:
        logger.warning(
            "HUB_TOKEN is not set; health check remains available but protected APIs are disabled"
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
    application.include_router(health_router)
    return application
