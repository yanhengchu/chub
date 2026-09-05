from __future__ import annotations

import logging
import socket
import subprocess
from shutil import which
from collections.abc import Callable

from app.core.config import Settings
from app.core.network import is_tailscale_ip


LOGGER = logging.getLogger("hub.startup")
TAILSCALE_IP_TIMEOUT_SECONDS = 5
MAX_TAILNET_HOSTS = 4
MAX_TAILSCALE_IP_OUTPUT_BYTES = 4096


def listen_socket(host: str, port: int) -> socket.socket:
    family = socket.AF_INET6 if ":" in host else socket.AF_INET
    listener = socket.socket(family, socket.SOCK_STREAM)
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    if family == socket.AF_INET6:
        listener.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 1)
    listener.bind((host, port))
    listener.listen(socket.SOMAXCONN)
    listener.set_inheritable(True)
    return listener


def discover_tailnet_hosts() -> tuple[str, ...]:
    executable = which("tailscale")
    if executable is None:
        return ()
    try:
        result = subprocess.run(
            [executable, "ip", "-4"],
            capture_output=True,
            text=True,
            timeout=TAILSCALE_IP_TIMEOUT_SECONDS,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return ()
    if result.returncode != 0 or len(result.stdout.encode("utf-8")) > MAX_TAILSCALE_IP_OUTPUT_BYTES:
        return ()
    hosts: list[str] = []
    for value in result.stdout.splitlines():
        host = value.strip()
        if is_tailscale_ip(host) and host not in hosts:
            hosts.append(host)
        if len(hosts) >= MAX_TAILNET_HOSTS:
            break
    return tuple(hosts)


def listen_sockets(
    settings: Settings,
    *,
    set_tailnet_listener_available: Callable[[bool | None], None],
    set_tailnet_listener_hosts: Callable[[tuple[str, ...]], None] = lambda _hosts: None,
    bind_listener: Callable[[str, int], socket.socket] = listen_socket,
    find_tailnet_hosts: Callable[[], tuple[str, ...]] = discover_tailnet_hosts,
) -> list[socket.socket]:
    listeners = [bind_listener("127.0.0.1", settings.server.port)]
    tailnet_hosts: list[str] = []
    for host in find_tailnet_hosts():
        try:
            listeners.append(bind_listener(host, settings.server.port))
        except OSError as error:
            LOGGER.warning(
                "Tailnet listener unavailable at %s:%s; continuing with loopback only: %s",
                host,
                settings.server.port,
                error,
            )
        else:
            tailnet_hosts.append(host)
    set_tailnet_listener_available(bool(tailnet_hosts))
    set_tailnet_listener_hosts(tuple(tailnet_hosts))
    return listeners
