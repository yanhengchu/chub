from __future__ import annotations

import logging
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, Depends, Request
from fastapi.responses import Response
from pydantic import BaseModel

from app.core.response import ApiError, ApiResponse
from app.core.security import require_token
from app.core.config import PROJECT_ROOT
from app.services.log_reader import (
    LogReadError,
    read_log_page,
    redact_log_line,
    tail_log,
)


class LogData(BaseModel):
    lines: list[str]
    count: int


LogSource = Literal["operations", "application", "service-out", "service-error"]


class LogPageData(LogData):
    source: LogSource
    next_cursor: int | None


router = APIRouter(
    prefix="/api",
    tags=["logs"],
    dependencies=[Depends(require_token)],
)


def _requested_lines(request: Request, maximum: int) -> int:
    values = request.query_params.getlist("lines")
    extra = set(request.query_params) - {"lines"}
    if extra:
        raise ApiError(422, "invalid_request", "Request validation failed")
    if not values:
        return maximum
    if len(values) != 1:
        raise ApiError(422, "invalid_log_lines", "Log line count is invalid")
    try:
        lines = int(values[0])
    except ValueError as exc:
        raise ApiError(
            422,
            "invalid_log_lines",
            "Log line count is invalid",
        ) from exc
    if lines < 1 or lines > maximum:
        raise ApiError(422, "invalid_log_lines", "Log line count is invalid")
    return lines


def _log_paths(request: Request) -> dict[LogSource, Path]:
    settings = request.app.state.settings
    return {
        "operations": settings.logs.operations_file,
        "application": settings.logs.file,
        "service-out": PROJECT_ROOT / "logs" / "service.out.log",
        "service-error": PROJECT_ROOT / "logs" / "service.err.log",
    }


def _sensitive_values(request: Request) -> tuple[str, ...]:
    token = request.app.state.settings.security.token
    return (token.get_secret_value(),) if token is not None else ()


@router.get("/logs", response_model=ApiResponse[LogData])
def logs(request: Request) -> ApiResponse[LogData]:
    settings = request.app.state.settings
    lines = _requested_lines(request, settings.logs.max_lines)
    sensitive_values = _sensitive_values(request)
    try:
        content = tail_log(
            settings.logs.file,
            lines,
            sensitive_values=sensitive_values,
        )
    except LogReadError as exc:
        logging.getLogger("hub.logs").error("Unable to read Hub log")
        raise ApiError(
            503,
            "logs_unavailable",
            "Hub logs are temporarily unavailable",
        ) from exc
    return ApiResponse(data=LogData(lines=content, count=len(content)))


@router.get("/logs/page", response_model=ApiResponse[LogPageData])
def log_page(
    request: Request,
    source: LogSource = "operations",
    lines: int = 500,
    before: int | None = None,
) -> ApiResponse[LogPageData]:
    if lines < 1 or lines > 500 or (before is not None and before < 1):
        raise ApiError(422, "invalid_log_page", "Log page request is invalid")
    try:
        content, next_cursor = read_log_page(
            _log_paths(request)[source],
            lines,
            before=before,
            sensitive_values=_sensitive_values(request),
        )
    except LogReadError as exc:
        raise ApiError(503, "logs_unavailable", "Hub logs are temporarily unavailable") from exc
    return ApiResponse(
        data=LogPageData(
            source=source,
            lines=content,
            count=len(content),
            next_cursor=next_cursor,
        )
    )


@router.get("/logs/download")
def download_log(request: Request, source: LogSource = "operations") -> Response:
    path = _log_paths(request)[source]
    try:
        if not path.exists():
            content = ""
        elif path.stat().st_size > 5 * 1024 * 1024:
            raise ApiError(413, "log_too_large", "Log file is too large to download")
        else:
            content = path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        raise ApiError(503, "logs_unavailable", "Hub logs are temporarily unavailable") from exc
    redacted = "\n".join(
        redact_log_line(
            line,
            _sensitive_values(request),
            max_line_bytes=None,
        )
        for line in content.splitlines()
    )
    return Response(
        redacted,
        media_type="text/plain; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{source}.log"'},
    )
