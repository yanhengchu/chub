from __future__ import annotations

import secrets

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.core.response import ApiError
from app.core.network import is_tailscale_ip


bearer_scheme = HTTPBearer(auto_error=False)
AUTHENTICATE_HEADER = {"WWW-Authenticate": "Bearer"}


def _allows_tailscale_request(request: Request) -> bool:
    settings = request.app.state.settings
    client_host = request.client.host if request.client else ""
    return (
        settings.security.allow_tailscale
        and is_tailscale_ip(settings.server.host)
        and is_tailscale_ip(client_host)
    )


def require_token(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
) -> None:
    if _allows_tailscale_request(request):
        request.state.authentication_method = "tailscale"
        return

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
    request.state.authentication_method = "token"


def require_tailscale(request: Request) -> None:
    if _allows_tailscale_request(request):
        request.state.authentication_method = "tailscale"
        return
    raise ApiError(
        403,
        "tailscale_required",
        "A direct Tailscale connection is required",
    )
