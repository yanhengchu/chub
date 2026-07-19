from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

from app.core.config import Settings
from app.services.system_status import collect_system_status


def test_collect_system_status_returns_normalized_data(settings: Settings) -> None:
    memory = SimpleNamespace(total=1000, used=400, percent=40.0)
    disk = SimpleNamespace(total=2000, used=500, percent=25.0)

    with (
        patch("app.services.system_status.psutil.virtual_memory", return_value=memory),
        patch("app.services.system_status.psutil.disk_usage", return_value=disk),
        patch("app.services.system_status.psutil.boot_time", return_value=100.0),
        patch("app.services.system_status.psutil.cpu_percent", return_value=12.5),
        patch("app.services.system_status.time.time", return_value=160.0),
        patch("app.services.system_status.platform.node", return_value="test-host"),
        patch("app.services.system_status.platform.system", return_value="TestOS"),
        patch("app.services.system_status.platform.release", return_value="1.0"),
        patch(
            "app.services.system_status.platform.python_version",
            return_value="3.13.0",
        ),
    ):
        result = collect_system_status(settings, "macos")

    assert result.node.id == "test-node"
    assert result.node.detected_platform == "macos"
    assert result.system.hostname == "test-host"
    assert result.system.cpu_percent == 12.5
    assert result.system.memory_used_bytes == 400
    assert result.system.disk_used_bytes == 500
    assert result.system.uptime_seconds == 60
    assert result.hub.version == "0.1.0"
