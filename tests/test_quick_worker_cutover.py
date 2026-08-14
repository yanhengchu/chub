from __future__ import annotations

import socket
import os
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from app.codex.models import CodexSession, QuickInteractionTask, utc_now
from app.quick_worker import PROTOCOL_VERSION, WORKER_CODE_VERSION
from app.quick_worker_cutover import retire_worker_store, run_cutover_preflight


def _health() -> dict[str, object]:
    return {
        "success": True,
        "data": {
            "protocol_version": PROTOCOL_VERSION,
            "status": "ready",
            "generation": "generation-1",
            "code_version": WORKER_CODE_VERSION,
            "pid": 1234,
            "active_tasks": 0,
            "corrupt_tasks": 0,
            "test_tasks_enabled": False,
            "codex_tasks_enabled": True,
            "codex_workspace_ids": [
                "chub",
                "home",
                "weixin-translation",
                "workspace",
            ],
        },
    }


def _prepare_paths(settings, tmp_path: Path) -> None:
    settings.codex_pty.workspace.mkdir()
    (settings.codex_pty.runtime_dir / "translation-workspace").mkdir(parents=True)
    runtime = tmp_path / "worker-runtime"
    runtime.mkdir(mode=0o700)
    socket_path = runtime / "worker.sock"
    worker_socket = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    worker_socket.bind(str(socket_path))
    worker_socket.close()
    socket_path.chmod(0o600)


def _write_private_state(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")
    os.chmod(path, 0o600)


@pytest.mark.anyio
async def test_cutover_preflight_is_read_only_and_lists_blockers(
    settings,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _prepare_paths(settings, tmp_path)
    _write_private_state(
        settings.codex_pty.data_file,
        CodexSession(
            id="session-1",
            workspace_id="chub",
            workspace_name="Chub",
            cwd=tmp_path,
        ).model_dump_json().join(["[", "]"]),
    )
    task = QuickInteractionTask(
        id="task-1",
        session_id="session-1",
        prompt="active",
        status="running",
        created_at=utc_now(),
        updated_at=utc_now(),
    )
    quick_path = settings.codex_pty.data_file.with_name("quick-interactions.json")
    _write_private_state(quick_path, f"[{task.model_dump_json()}]")
    before = {path: path.read_bytes() for path in (settings.codex_pty.data_file, quick_path)}
    monkeypatch.setattr("app.quick_worker.worker_runtime_dir", lambda _settings: tmp_path / "worker-runtime")
    monkeypatch.setattr("app.quick_worker.worker_socket_path", lambda _settings: tmp_path / "worker-runtime" / "worker.sock")
    monkeypatch.setattr("app.quick_worker.read_health", AsyncMock(return_value=_health()))
    monkeypatch.setattr(
        "app.quick_worker.worker_request",
        AsyncMock(return_value={"success": True, "data": {"tasks": []}}),
    )

    result = await run_cutover_preflight(settings)

    assert result["success"] is False
    codes = {item["code"] for item in result["data"]["blockers"]}
    assert {"active_quick_tasks", "unarchived_sessions"} <= codes
    assert before == {path: path.read_bytes() for path in before}


@pytest.mark.anyio
async def test_cutover_preflight_accepts_clean_final_state(
    settings,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _prepare_paths(settings, tmp_path)
    settings.codex_pty.data_file.parent.mkdir(parents=True, exist_ok=True)
    _write_private_state(settings.codex_pty.data_file, "[]")
    monkeypatch.setattr("app.quick_worker.worker_runtime_dir", lambda _settings: tmp_path / "worker-runtime")
    monkeypatch.setattr("app.quick_worker.worker_socket_path", lambda _settings: tmp_path / "worker-runtime" / "worker.sock")
    monkeypatch.setattr("app.quick_worker.read_health", AsyncMock(return_value=_health()))
    monkeypatch.setattr(
        "app.quick_worker.worker_request",
        AsyncMock(return_value={"success": True, "data": {"tasks": []}}),
    )

    result = await run_cutover_preflight(settings)

    assert result["success"] is True
    assert result["data"]["blockers"] == []


@pytest.mark.anyio
async def test_final_preflight_rejects_failed_recovery_query(
    settings,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _prepare_paths(settings, tmp_path)
    settings.codex_pty.data_file.parent.mkdir(parents=True, exist_ok=True)
    _write_private_state(settings.codex_pty.data_file, "[]")
    monkeypatch.setattr("app.quick_worker.worker_runtime_dir", lambda _settings: tmp_path / "worker-runtime")
    monkeypatch.setattr("app.quick_worker.worker_socket_path", lambda _settings: tmp_path / "worker-runtime" / "worker.sock")
    monkeypatch.setattr("app.quick_worker.read_health", AsyncMock(return_value=_health()))
    monkeypatch.setattr(
        "app.quick_worker.worker_request",
        AsyncMock(return_value={"success": False, "error": {"code": "failed"}}),
    )

    result = await run_cutover_preflight(settings)

    assert result["success"] is False
    assert "worker_recovery_not_empty" in {
        item["code"] for item in result["data"]["blockers"]
    }


@pytest.mark.anyio
async def test_prepare_preflight_allows_delivered_old_worker_task_records(
    settings,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _prepare_paths(settings, tmp_path)
    settings.codex_pty.data_file.parent.mkdir(parents=True, exist_ok=True)
    _write_private_state(settings.codex_pty.data_file, "[]")
    task_dir = settings.codex_pty.data_file.parent / "quick-worker" / "tasks" / "old-task"
    task_dir.mkdir(parents=True)
    task_dir.parent.chmod(0o700)
    monkeypatch.setattr("app.quick_worker.worker_runtime_dir", lambda _settings: tmp_path / "worker-runtime")
    monkeypatch.setattr("app.quick_worker.worker_socket_path", lambda _settings: tmp_path / "worker-runtime" / "worker.sock")
    monkeypatch.setattr("app.quick_worker.read_health", AsyncMock(return_value=_health()))
    monkeypatch.setattr(
        "app.quick_worker.worker_request",
        AsyncMock(return_value={"success": True, "data": {"tasks": []}}),
    )

    result = await run_cutover_preflight(
        settings,
        require_production_worker=False,
    )

    assert result["success"] is True
    assert result["data"]["checks"]["worker_task_records"] == 1
    assert result["data"]["checks"]["worker_recovery_tasks"] == 0


@pytest.mark.anyio
async def test_prepare_preflight_counts_old_worker_recovery_records(
    settings,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _prepare_paths(settings, tmp_path)
    settings.codex_pty.data_file.parent.mkdir(parents=True, exist_ok=True)
    _write_private_state(settings.codex_pty.data_file, "[]")
    monkeypatch.setattr(
        "app.quick_worker.worker_runtime_dir",
        lambda _settings: tmp_path / "worker-runtime",
    )
    monkeypatch.setattr(
        "app.quick_worker.worker_socket_path",
        lambda _settings: tmp_path / "worker-runtime" / "worker.sock",
    )
    monkeypatch.setattr("app.quick_worker.read_health", AsyncMock(return_value=_health()))
    monkeypatch.setattr(
        "app.quick_worker.worker_request",
        AsyncMock(
            return_value={
                "success": True,
                "data": {
                    "tasks": [
                        {
                            "task_id": "qw-1786671300299-6ae6e4e135de4c61aa988bf319c5f686",
                            "status": "running",
                            "prompt_sha256": "a" * 64,
                            "session_id": "session-1",
                            "task_kind": "standard",
                            "native_session_id": None,
                            "delivery_acknowledged": False,
                            "created_at": "2026-08-14T01:35:00Z",
                            "updated_at": "2026-08-14T01:35:01Z",
                        }
                    ]
                },
            }
        ),
    )

    result = await run_cutover_preflight(
        settings,
        require_production_worker=False,
    )

    assert result["success"] is False
    assert result["data"]["checks"]["worker_recovery_tasks"] == 1
    assert "worker_recovery_not_empty" in {
        item["code"] for item in result["data"]["blockers"]
    }


@pytest.mark.anyio
async def test_prepare_preflight_accepts_empty_legacy_worker_without_recovery_query(
    settings,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _prepare_paths(settings, tmp_path)
    settings.codex_pty.data_file.parent.mkdir(parents=True, exist_ok=True)
    _write_private_state(settings.codex_pty.data_file, "[]")
    legacy_health = _health()
    legacy_health["data"].update(
        {
            "protocol_version": 1,
            "code_version": "quick-worker-1",
            "codex_tasks_enabled": False,
            "codex_workspace_ids": [],
        }
    )
    monkeypatch.setattr(
        "app.quick_worker.worker_runtime_dir",
        lambda _settings: tmp_path / "worker-runtime",
    )
    monkeypatch.setattr(
        "app.quick_worker.worker_socket_path",
        lambda _settings: tmp_path / "worker-runtime" / "worker.sock",
    )
    monkeypatch.setattr(
        "app.quick_worker.read_health",
        AsyncMock(return_value=legacy_health),
    )
    monkeypatch.setattr(
        "app.quick_worker.worker_request",
        AsyncMock(
            return_value={
                "success": False,
                "error": {"code": "worker_request_invalid"},
            }
        ),
    )

    result = await run_cutover_preflight(
        settings,
        require_production_worker=False,
    )

    assert result["success"] is True
    assert result["data"]["checks"]["worker_task_records"] == 0
    assert result["data"]["checks"]["worker_recovery_tasks"] == 0
    assert (
        result["data"]["checks"]["worker_recovery_verification"]
        == "empty_legacy_store"
    )


@pytest.mark.anyio
async def test_prepare_preflight_rejects_unqueryable_nonempty_legacy_store(
    settings,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _prepare_paths(settings, tmp_path)
    settings.codex_pty.data_file.parent.mkdir(parents=True, exist_ok=True)
    _write_private_state(settings.codex_pty.data_file, "[]")
    task_dir = settings.codex_pty.data_file.parent / "quick-worker" / "tasks" / "old-task"
    task_dir.mkdir(parents=True)
    task_dir.parent.chmod(0o700)
    legacy_health = _health()
    legacy_health["data"].update(
        {
            "protocol_version": 1,
            "code_version": "quick-worker-1",
            "codex_tasks_enabled": False,
            "codex_workspace_ids": [],
        }
    )
    monkeypatch.setattr(
        "app.quick_worker.worker_runtime_dir",
        lambda _settings: tmp_path / "worker-runtime",
    )
    monkeypatch.setattr(
        "app.quick_worker.worker_socket_path",
        lambda _settings: tmp_path / "worker-runtime" / "worker.sock",
    )
    monkeypatch.setattr(
        "app.quick_worker.read_health",
        AsyncMock(return_value=legacy_health),
    )
    monkeypatch.setattr(
        "app.quick_worker.worker_request",
        AsyncMock(
            return_value={
                "success": False,
                "error": {"code": "worker_request_invalid"},
            }
        ),
    )

    result = await run_cutover_preflight(
        settings,
        require_production_worker=False,
    )

    assert result["success"] is False
    assert result["data"]["checks"]["worker_task_records"] == 1
    assert result["data"]["checks"]["worker_recovery_tasks"] == -1
    assert "worker_recovery_not_empty" in {
        item["code"] for item in result["data"]["blockers"]
    }


@pytest.mark.anyio
async def test_prepare_preflight_allows_missing_managed_translation_workspace(
    settings,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _prepare_paths(settings, tmp_path)
    (settings.codex_pty.runtime_dir / "translation-workspace").rmdir()
    settings.codex_pty.data_file.parent.mkdir(parents=True, exist_ok=True)
    _write_private_state(settings.codex_pty.data_file, "[]")
    monkeypatch.setattr("app.quick_worker.worker_runtime_dir", lambda _settings: tmp_path / "worker-runtime")
    monkeypatch.setattr("app.quick_worker.worker_socket_path", lambda _settings: tmp_path / "worker-runtime" / "worker.sock")
    monkeypatch.setattr("app.quick_worker.read_health", AsyncMock(return_value=_health()))
    monkeypatch.setattr(
        "app.quick_worker.worker_request",
        AsyncMock(return_value={"success": True, "data": {"tasks": []}}),
    )

    result = await run_cutover_preflight(
        settings,
        require_production_worker=False,
    )

    assert result["success"] is True


def test_retire_worker_store_moves_private_store_after_worker_stops(
    settings,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = settings.codex_pty.data_file.parent / "quick-worker"
    task = root / "tasks" / "old-task"
    task.mkdir(parents=True)
    root.chmod(0o700)
    runtime = tmp_path / "worker-runtime"
    runtime.mkdir()
    monkeypatch.setattr(
        "app.quick_worker.worker_socket_path",
        lambda _settings: runtime / "worker.sock",
    )

    archive = retire_worker_store(settings)

    assert archive is not None
    assert not root.exists()
    assert (archive / "tasks" / "old-task").is_dir()
    assert archive.parent == root.parent


def test_retire_worker_store_refuses_while_worker_socket_exists(
    settings,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = settings.codex_pty.data_file.parent / "quick-worker"
    root.mkdir(parents=True)
    root.chmod(0o700)
    socket_path = tmp_path / "worker.sock"
    socket_path.touch()
    monkeypatch.setattr(
        "app.quick_worker.worker_socket_path",
        lambda _settings: socket_path,
    )

    with pytest.raises(OSError, match="must be stopped"):
        retire_worker_store(settings)

    assert root.is_dir()


@pytest.mark.anyio
async def test_cutover_preflight_rejects_pending_delivery_and_restart(
    settings,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _prepare_paths(settings, tmp_path)
    settings.codex_pty.data_file.parent.mkdir(parents=True, exist_ok=True)
    _write_private_state(settings.codex_pty.data_file, "[]")
    task = QuickInteractionTask(
        id="task-1",
        session_id="session-1",
        prompt="completed",
        status="succeeded",
        result="done",
        notification_status="pending",
        created_at=utc_now(),
        updated_at=utc_now(),
    )
    quick_path = settings.codex_pty.data_file.with_name("quick-interactions.json")
    _write_private_state(quick_path, f"[{task.model_dump_json()}]")
    restart_path = settings.codex_pty.data_file.with_name("deferred-restart.json")
    _write_private_state(
        restart_path,
        """{
  "version": 1,
  "operation_id": "operation-1:restart",
  "requested_instance_id": "instance-1",
  "requested_task_id": "task-1",
  "source_ip": "127.0.0.1",
  "status": "waiting",
  "requested_at": "2026-08-14T00:00:00Z",
  "updated_at": "2026-08-14T00:00:00Z"
}\n""",
    )
    monkeypatch.setattr(
        "app.quick_worker.worker_runtime_dir",
        lambda _settings: tmp_path / "worker-runtime",
    )
    monkeypatch.setattr(
        "app.quick_worker.worker_socket_path",
        lambda _settings: tmp_path / "worker-runtime" / "worker.sock",
    )
    monkeypatch.setattr("app.quick_worker.read_health", AsyncMock(return_value=_health()))
    monkeypatch.setattr(
        "app.quick_worker.worker_request",
        AsyncMock(return_value={"success": True, "data": {"tasks": []}}),
    )

    result = await run_cutover_preflight(settings)

    assert result["success"] is False
    codes = {item["code"] for item in result["data"]["blockers"]}
    assert {"pending_result_delivery", "pending_deferred_restart"} <= codes


@pytest.mark.anyio
async def test_cutover_preflight_rejects_unsafe_state_permissions(
    settings,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _prepare_paths(settings, tmp_path)
    settings.codex_pty.data_file.parent.mkdir(parents=True, exist_ok=True)
    _write_private_state(settings.codex_pty.data_file, "[]")
    settings.codex_pty.data_file.chmod(0o644)
    monkeypatch.setattr(
        "app.quick_worker.worker_runtime_dir",
        lambda _settings: tmp_path / "worker-runtime",
    )
    monkeypatch.setattr(
        "app.quick_worker.worker_socket_path",
        lambda _settings: tmp_path / "worker-runtime" / "worker.sock",
    )
    monkeypatch.setattr("app.quick_worker.read_health", AsyncMock(return_value=_health()))
    monkeypatch.setattr(
        "app.quick_worker.worker_request",
        AsyncMock(return_value={"success": True, "data": {"tasks": []}}),
    )

    result = await run_cutover_preflight(settings)

    assert result["success"] is False
    assert "cutover_state_invalid" in {
        item["code"] for item in result["data"]["blockers"]
    }


@pytest.mark.anyio
async def test_final_preflight_rejects_workspace_mapping_mismatch(
    settings,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _prepare_paths(settings, tmp_path)
    settings.codex_pty.data_file.parent.mkdir(parents=True, exist_ok=True)
    _write_private_state(settings.codex_pty.data_file, "[]")
    mismatched = _health()
    mismatched["data"]["codex_workspace_ids"] = ["chub"]
    monkeypatch.setattr(
        "app.quick_worker.worker_runtime_dir",
        lambda _settings: tmp_path / "worker-runtime",
    )
    monkeypatch.setattr(
        "app.quick_worker.worker_socket_path",
        lambda _settings: tmp_path / "worker-runtime" / "worker.sock",
    )
    monkeypatch.setattr(
        "app.quick_worker.read_health",
        AsyncMock(return_value=mismatched),
    )
    monkeypatch.setattr(
        "app.quick_worker.worker_request",
        AsyncMock(return_value={"success": True, "data": {"tasks": []}}),
    )

    result = await run_cutover_preflight(settings)

    assert result["success"] is False
    assert "worker_workspace_mapping_mismatch" in {
        item["code"] for item in result["data"]["blockers"]
    }
