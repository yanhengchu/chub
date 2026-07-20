from __future__ import annotations

from ipaddress import ip_address, ip_network


TAILSCALE_IPV4_NETWORK = ip_network("100.64.0.0/10")
TAILSCALE_IPV6_NETWORK = ip_network("fd7a:115c:a1e0::/48")


def is_tailscale_ip(value: str) -> bool:
    try:
        address = ip_address(value)
    except ValueError:
        return False
    return address in TAILSCALE_IPV4_NETWORK or address in TAILSCALE_IPV6_NETWORK
