from __future__ import annotations

import logging
from uuid import uuid4

from fastapi import Request


LOGGER = logging.getLogger("hub.operations")


def log_operation(
    request: Request,
    *,
    action: str,
    status: str,
    target: str,
    operation_id: str | None = None,
) -> str:
    resolved_id = operation_id or uuid4().hex
    source_ip = request.client.host if request.client else "unknown"
    LOGGER.info(
        "operation_id=%s action=%s status=%s target=%s source_ip=%s",
        resolved_id,
        action,
        status,
        target,
        source_ip,
    )
    return resolved_id
