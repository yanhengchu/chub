import sys
from pathlib import Path

import pytest

from app.core.config import Settings
from app.ai_runtime.external_modules import ExternalRuntimeModuleService
from scripts.build_codex_runtime_zip import build as build_codex_runtime_zip


CODEX_RUNTIME_SOURCE_ROOT = Path(__file__).resolve().parents[1] / "runtime-modules" / "codex-runtime"
if str(CODEX_RUNTIME_SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(CODEX_RUNTIME_SOURCE_ROOT))
TESTS_ROOT = Path(__file__).resolve().parent
if str(TESTS_ROOT) not in sys.path:
    sys.path.insert(0, str(TESTS_ROOT))


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    resolved = Settings.model_validate(
        {
            "app": {"name": "Hub", "version": "0.1.0"},
            "node": {"id": "test-node", "name": "Test Node", "type": "unknown"},
            "server": {"port": 8080},
            "security": {},
            "logs": {
                "file": tmp_path / "hub.log",
                "operations_file": tmp_path / "operations.log",
                "worker_operations_file": tmp_path / "worker-operations.log",
                "level": "INFO",
                "max_lines": 100,
            },
            "ai_runtime": {
                "codex": {
                    "enabled": True,
                    "workspace": tmp_path / "workspace",
                    "data_file": tmp_path / "codex-sessions.json",
                    "runtime_dir": tmp_path / "codex-runtime",
                    "ticket_ttl_seconds": 600,
                    "max_running": 3,
                },
                "modules": {
                    "install_dir": tmp_path / "runtime-modules",
                },
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
            "requests": {
                "state_file": tmp_path / "requests.json",
            },
            "openclaw": {
                "weixin_chub_mode": {
                    "state_file": tmp_path / "weixin-chub-mode.json",
                },
            },
        }
    )
    archive = build_codex_runtime_zip(
        tmp_path / "codex-runtime.zip",
        description="测试用 Codex Runtime 正式版本。",
    )
    service = ExternalRuntimeModuleService(resolved)
    activation = service.install(archive.read_bytes(), source_name=archive.name)
    service.finalize(activation)
    return resolved
