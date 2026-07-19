from pathlib import Path

import pytest

from app.core.config import Settings


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings.model_validate(
        {
            "app": {"name": "Hub", "version": "0.1.0"},
            "node": {"id": "test-node", "name": "Test Node", "type": "unknown"},
            "server": {"host": "127.0.0.1", "port": 8080},
            "security": {"token": "test-token-that-is-long-enough-for-tests"},
            "tasks": {"default_timeout": 30},
            "logs": {
                "file": tmp_path / "hub.log",
                "level": "INFO",
                "max_lines": 100,
            },
        }
    )
