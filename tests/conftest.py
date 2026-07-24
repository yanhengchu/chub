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
            "logs": {
                "file": tmp_path / "hub.log",
                "operations_file": tmp_path / "operations.log",
                "level": "INFO",
                "max_lines": 100,
            },
            "codex_pty": {
                "enabled": True,
                "workspace": tmp_path / "workspace",
                "data_file": tmp_path / "codex-sessions.json",
                "ticket_ttl_seconds": 600,
                "max_running": 3,
            },
            "project_documents": {
                "state_file": tmp_path / "project-documents.json",
            },
        }
    )
