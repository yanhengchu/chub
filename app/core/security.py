from __future__ import annotations

from ipaddress import ip_address

from fastapi import Request

from app.core.response import ApiError
from app.core.network import is_tailscale_ip


def _allows_loopback_request(request: Request) -> bool:
    client_host = request.client.host if request.client else ""
    try:
        return ip_address(client_host).is_loopback
    except ValueError:
        return False


def _allows_tailscale_request(request: Request) -> bool:
    settings = request.app.state.settings
    client_host = request.client.host if request.client else ""
    return (
        settings.security.allow_tailscale
        and getattr(request.app.state, "tailnet_listener_available", None) is True
        and is_tailscale_ip(client_host)
    )


def require_trusted_network(request: Request) -> None:
    if _allows_loopback_request(request):
        request.state.authentication_method = "loopback"
        return

    if _allows_tailscale_request(request):
        request.state.authentication_method = "tailscale"
        return

    raise ApiError(403, "trusted_network_required", "A local or Tailnet connection is required")
