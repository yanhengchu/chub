from __future__ import annotations

import secrets

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.core.response import ApiError


bearer_scheme = HTTPBearer(auto_error=False)
AUTHENTICATE_HEADER = {"WWW-Authenticate": "Bearer"}


def require_token(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
) -> None:
    configured_token = request.app.state.settings.security.token
    if configured_token is None:
        raise ApiError(
            503,
            "security_not_configured",
            "Access token is not configured",
        )

    authorization = request.headers.get("Authorization")
    if credentials is None:
        if authorization is None:
            raise ApiError(
                401,
                "authentication_required",
                "Bearer authentication is required",
                headers=AUTHENTICATE_HEADER,
            )
        raise ApiError(
            401,
            "invalid_credentials",
            "Invalid access token",
            headers=AUTHENTICATE_HEADER,
        )

    if not secrets.compare_digest(
        credentials.credentials,
        configured_token.get_secret_value(),
    ):
        raise ApiError(
            401,
            "invalid_credentials",
            "Invalid access token",
            headers=AUTHENTICATE_HEADER,
        )
