import logging
from typing import Generic, Literal, TypeVar

from fastapi import Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from starlette.exceptions import HTTPException as StarletteHTTPException


DataT = TypeVar("DataT")
SECURITY_HEADERS = {
    "Cache-Control": "no-store",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
}


class ApiResponse(BaseModel, Generic[DataT]):
    success: bool = True
    data: DataT


class ErrorDetail(BaseModel):
    code: str
    message: str


class ApiErrorResponse(BaseModel):
    success: Literal[False] = False
    error: ErrorDetail


class ApiError(Exception):
    def __init__(
        self,
        status_code: int,
        code: str,
        message: str,
        *,
        headers: dict[str, str] | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message
        self.headers = headers


def error_response(
    status_code: int,
    code: str,
    message: str,
    *,
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    body = ApiErrorResponse(error=ErrorDetail(code=code, message=message))
    return JSONResponse(
        status_code=status_code,
        content=body.model_dump(),
        headers={**SECURITY_HEADERS, **(headers or {})},
    )


async def api_error_handler(_request: Request, exc: ApiError) -> JSONResponse:
    return error_response(
        exc.status_code,
        exc.code,
        exc.message,
        headers=exc.headers,
    )


async def validation_error_handler(
    _request: Request, _exc: RequestValidationError
) -> JSONResponse:
    return error_response(422, "invalid_request", "Request validation failed")


async def http_error_handler(
    _request: Request, exc: StarletteHTTPException
) -> JSONResponse:
    codes = {
        404: ("not_found", "Resource not found"),
        405: ("method_not_allowed", "Method not allowed"),
    }
    code, message = codes.get(
        exc.status_code,
        ("http_error", "Request failed"),
    )
    return error_response(
        exc.status_code,
        code,
        message,
        headers=exc.headers,
    )


async def internal_error_handler(
    _request: Request, exc: Exception
) -> JSONResponse:
    logging.getLogger("hub.api").exception("Unhandled API error", exc_info=exc)
    return error_response(500, "internal_error", "Internal server error")
