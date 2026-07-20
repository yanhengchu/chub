import pytest

from app.core.network import is_tailscale_ip


@pytest.mark.parametrize(
    "value",
    [
        "100.64.0.1",
        "100.105.63.116",
        "100.127.255.254",
        "fd7a:115c:a1e0::1",
        "fd7a:115c:a1e0:abcd::1234",
    ],
)
def test_tailscale_ip_is_allowed(value: str) -> None:
    assert is_tailscale_ip(value) is True


@pytest.mark.parametrize(
    "value",
    [
        "0.0.0.0",
        "127.0.0.1",
        "192.168.1.20",
        "100.63.255.255",
        "100.128.0.1",
        "::",
        "::1",
        "example.test",
    ],
)
def test_non_tailscale_ip_is_rejected(value: str) -> None:
    assert is_tailscale_ip(value) is False
