from __future__ import annotations

import platform
import time
from datetime import datetime, timezone
from pathlib import Path

import psutil
from pydantic import BaseModel

from app.core.config import Settings


class NodeStatus(BaseModel):
    id: str
    name: str
    configured_platform: str
    detected_platform: str


class SystemStatus(BaseModel):
    hostname: str
    operating_system: str
    operating_system_version: str
    python_version: str
    cpu_percent: float
    memory_total_bytes: int
    memory_used_bytes: int
    memory_percent: float
    disk_total_bytes: int
    disk_used_bytes: int
    disk_percent: float
    boot_time: datetime
    uptime_seconds: int


class HubStatus(BaseModel):
    version: str
    current_time: datetime


class StatusData(BaseModel):
    node: NodeStatus
    system: SystemStatus
    hub: HubStatus


def collect_system_status(settings: Settings, detected_platform: str) -> StatusData:
    memory = psutil.virtual_memory()
    disk_root = Path.home().anchor or "/"
    disk = psutil.disk_usage(disk_root)
    boot_timestamp = psutil.boot_time()
    now_timestamp = time.time()

    return StatusData(
        node=NodeStatus(
            id=settings.node.id,
            name=settings.node.name,
            configured_platform=settings.node.type,
            detected_platform=detected_platform,
        ),
        system=SystemStatus(
            hostname=platform.node(),
            operating_system=platform.system(),
            operating_system_version=platform.release(),
            python_version=platform.python_version(),
            cpu_percent=psutil.cpu_percent(interval=None),
            memory_total_bytes=memory.total,
            memory_used_bytes=memory.used,
            memory_percent=memory.percent,
            disk_total_bytes=disk.total,
            disk_used_bytes=disk.used,
            disk_percent=disk.percent,
            boot_time=datetime.fromtimestamp(boot_timestamp, timezone.utc),
            uptime_seconds=max(0, int(now_timestamp - boot_timestamp)),
        ),
        hub=HubStatus(
            version=settings.app.version,
            current_time=datetime.fromtimestamp(now_timestamp, timezone.utc),
        ),
    )
