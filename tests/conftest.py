from pathlib import Path

import pytest

from app.core.config import Settings


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings.model_validate(
        {
            "app": {"name": "Hub", "version": "0.1.0"},
            "node": {"id": "test-node", "name": "Test Node", "type": "unknown"},
            "server": {"tailnet_host": None, "port": 8080},
            "security": {},
            "logs": {
                "file": tmp_path / "hub.log",
                "operations_file": tmp_path / "operations.log",
                "worker_operations_file": tmp_path / "worker-operations.log",
                "level": "INFO",
                "max_lines": 100,
            },
            "codex_pty": {
                "enabled": True,
                "workspace": tmp_path / "workspace",
                "data_file": tmp_path / "codex-sessions.json",
                "runtime_dir": tmp_path / "codex-runtime",
                "ticket_ttl_seconds": 600,
                "max_running": 3,
            },
            "automations": {
                "shared_config_file": tmp_path / "automations.yaml",
                "local_config_file": tmp_path / "automations.local.yaml",
                "state_dir": tmp_path / "automation-state",
                "runtime_dir": tmp_path / "automation-runtime",
                "artifacts_dir": tmp_path / "automation-artifacts",
            },
            "project_documents": {
                "state_file": tmp_path / "project-documents.json",
            },
            "openclaw": {
                "weixin_chub_mode": {
                    "state_file": tmp_path / "weixin-chub-mode.json",
                },
            },
        }
    )
