from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel

from app.core.response import ApiError, ApiResponse
from app.core.security import require_token
from app.services.log_reader import LogReadError, tail_log


class LogData(BaseModel):
    lines: list[str]
    count: int


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


@router.get("/logs", response_model=ApiResponse[LogData])
def logs(request: Request) -> ApiResponse[LogData]:
    settings = request.app.state.settings
    lines = _requested_lines(request, settings.logs.max_lines)
    token = settings.security.token
    sensitive_values = (
        (token.get_secret_value(),)
        if token is not None
        else ()
    )
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
