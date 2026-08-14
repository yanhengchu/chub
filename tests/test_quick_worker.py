from __future__ import annotations

import asyncio
import fcntl
import json
import os
import shutil
import sqlite3
import stat
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import psutil
import pytest
from pydantic import ValidationError

import app.quick_worker as quick_worker
from app.quick_worker import (
    HEALTH_PROTOCOL_VERSION,
    PROTOCOL_VERSION,
    WORKER_CODE_VERSION,
    QuickWorkerServer,
    read_health,
    worker_request,
    worker_runtime_dir,
    worker_socket_path,
)
from app.quick_worker_tasks import (
    CodexTaskSubmission,
    new_worker_task_id,
    worker_state_dir,
)


async def _request(settings, action: str, **fields: object) -> dict[str, object]:
    return await worker_request(
        settings,
        {
            "protocol_version": PROTOCOL_VERSION,
            "request_id": uuid.uuid4().hex,
            "action": action,
            **fields,
        },
    )


async def _wait_for_status(
    settings,
    task_id: str,
    statuses: set[str],
    *,
    timeout: float = 5.0,
) -> dict[str, object]:
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        response = await _request(settings, "task_get", task_id=task_id)
        if response["success"] is True:
            task = response["data"]["task"]
            if task["status"] in statuses:
                return task
        await asyncio.sleep(0.02)
    raise AssertionError(f"task {task_id} did not reach {statuses}")


async def _submit(
    settings,
    *,
    task_id: str,
    prompt: str = "isolated test",
    behavior: str = "succeed",
    run_seconds: float = 0.0,
    timeout_seconds: float = 5.0,
) -> dict[str, object]:
    return await _request(
        settings,
        "test_task_submit",
        task={
            "task_id": task_id,
            "prompt": prompt,
            "behavior": behavior,
            "run_seconds": run_seconds,
            "timeout_seconds": timeout_seconds,
        },
    )


async def _submit_codex(
    settings,
    *,
    task_id: str,
    session_id: str,
    codex_session_id: str | None = None,
    prompt: str = "isolated Codex task",
    timeout_seconds: float = 5.0,
    task_kind: str = "standard",
    queue_key: str | None = None,
    queue_limit: int | None = None,
    queue_wait_seconds: float | None = None,
) -> dict[str, object]:
    return await _request(
        settings,
        "isolated_codex_submit",
        task={
            "task_id": task_id,
            "session_id": session_id,
            "workspace_id": "isolated",
            "prompt": prompt,
            "permission_mode": "read-only",
            "codex_session_id": codex_session_id,
            "model": None,
            "reasoning_effort": None,
            "timeout_seconds": timeout_seconds,
            "task_kind": task_kind,
            "queue_key": queue_key,
            "queue_limit": queue_limit,
            "queue_wait_seconds": queue_wait_seconds,
        },
    )


def test_restart_sensitive_is_derived_and_cannot_be_spoofed() -> None:
    fields = {
        "task_id": new_worker_task_id(),
        "session_id": "session-1",
        "workspace_id": "chub",
        "prompt": "modify Chub",
        "permission_mode": "auto-review",
        "timeout_seconds": 60,
    }

    derived = CodexTaskSubmission.model_validate(fields)

    assert derived.restart_sensitive is True
    with pytest.raises(ValidationError, match="fixed workspace rule"):
        CodexTaskSubmission.model_validate(
            {
                **fields,
                "task_id": new_worker_task_id(),
                "restart_sensitive": False,
            }
        )
    with pytest.raises(ValidationError, match="fixed workspace rule"):
        CodexTaskSubmission.model_validate(
            {
                **fields,
                "task_id": new_worker_task_id(),
                "workspace_id": "isolated",
                "permission_mode": "read-only",
                "restart_sensitive": True,
            }
        )


def _fake_codex(tmp_path: Path) -> Path:
    executable = tmp_path / "fake-codex"
    executable.write_text(
        """#!/usr/bin/env python3
import json
import os
import sys
import time
from pathlib import Path

args = sys.argv[1:]
result_path = Path(args[args.index("--output-last-message") + 1])
native_id = os.environ["FAKE_CODEX_SESSION_ID"]
if "resume" in args:
    native_id = args[args.index("resume") + 1]
native_id = os.environ.get("FAKE_CODEX_FORCE_ID", native_id)
prompt = sys.stdin.read()
if os.environ.get("FAKE_CODEX_OMIT_EVENT") != "1":
    print(json.dumps({"type": "thread.started", "thread_id": native_id}), flush=True)
time.sleep(float(os.environ.get("FAKE_CODEX_DELAY", "0")))
prefix = "resumed" if "resume" in args else "created"
result_path.write_text(f"{prefix}:{native_id}:{prompt}", encoding="utf-8")
""",
        encoding="utf-8",
    )
    executable.chmod(0o700)
    return executable


def _set_native_archive_state(
    codex_home: Path,
    native_session_id: str,
    *,
    archived: bool,
) -> None:
    codex_home.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(codex_home / "state_5.sqlite")
    try:
        connection.execute("CREATE TABLE IF NOT EXISTS threads (id TEXT, archived INTEGER)")
        connection.execute("DELETE FROM threads WHERE id = ?", (native_session_id,))
        connection.execute(
            "INSERT INTO threads (id, archived) VALUES (?, ?)",
            (native_session_id, int(archived)),
        )
        connection.commit()
    finally:
        connection.close()


@pytest.mark.anyio
async def test_worker_reports_stable_health_over_private_socket(settings) -> None:
    server = QuickWorkerServer(settings)
    await server.start()
    try:
        first = await read_health(settings)
        second = await read_health(settings)

        assert first["success"] is True
        assert first["data"] == second["data"]
        assert first["data"]["protocol_version"] == PROTOCOL_VERSION
        assert first["data"]["status"] == "ready"
        assert first["data"]["generation"] == server.generation
        assert first["data"]["code_version"] == WORKER_CODE_VERSION
        assert first["data"]["pid"] > 0
        assert first["data"]["active_tasks"] == 0
        assert first["data"]["test_tasks_enabled"] is False
        assert first["data"]["codex_tasks_enabled"] is False
        assert first["data"]["codex_workspace_ids"] == []
        assert stat.S_IMODE(worker_runtime_dir(settings).stat().st_mode) == 0o700
        assert stat.S_IMODE(worker_socket_path(settings).stat().st_mode) == 0o600

        server.status = "draining"
        draining = await read_health(settings)
        assert draining["data"]["status"] == "draining"
    finally:
        await server.close()

    assert not worker_socket_path(settings).exists()


@pytest.mark.anyio
async def test_worker_rejects_incompatible_or_expanded_protocol(settings) -> None:
    server = QuickWorkerServer(settings)
    await server.start()
    try:
        reader, writer = await asyncio.open_unix_connection(
            worker_socket_path(settings)
        )
        writer.write(
            (
                json.dumps(
                    {
                        "protocol_version": PROTOCOL_VERSION + 1,
                        "request_id": "request-1",
                        "action": "health",
                    }
                )
                + "\n"
            ).encode()
        )
        await writer.drain()
        response = json.loads(await reader.readline())
        writer.close()
        await writer.wait_closed()

        assert response["success"] is False
        assert response["error"]["code"] == "worker_protocol_incompatible"

        reader, writer = await asyncio.open_unix_connection(
            worker_socket_path(settings)
        )
        writer.write(
            b'{"protocol_version":1,"request_id":"request-2",'
            b'"action":"health","command":"arbitrary"}\n'
        )
        await writer.drain()
        response = json.loads(await reader.readline())
        writer.close()
        await writer.wait_closed()

        assert response["success"] is False
        assert response["error"]["code"] == "worker_request_invalid"
    finally:
        await server.close()


@pytest.mark.anyio
async def test_worker_lock_prevents_second_instance(settings) -> None:
    first = QuickWorkerServer(settings)
    second = QuickWorkerServer(settings)
    await first.start()
    try:
        with pytest.raises(OSError, match="already running"):
            await second.start()
    finally:
        await second.close()
        await first.close()


@pytest.mark.anyio
async def test_worker_refuses_non_socket_at_fixed_path(settings) -> None:
    runtime_dir = worker_runtime_dir(settings)
    runtime_dir.mkdir(parents=True)
    socket_path = worker_socket_path(settings)
    socket_path.write_text("keep", encoding="utf-8")
    server = QuickWorkerServer(settings)

    with pytest.raises(OSError, match="not a socket"):
        await server.start()

    assert socket_path.read_text(encoding="utf-8") == "keep"


def test_worker_cli_fails_cleanly_when_configuration_is_unavailable(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    def fail_settings():
        raise RuntimeError("configuration details must not be printed")

    monkeypatch.setattr(quick_worker, "get_settings", fail_settings)
    monkeypatch.setattr("sys.argv", ["quick-worker", "health"])

    assert quick_worker.main() == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == "quick-worker: configuration is unavailable\n"


@pytest.mark.anyio
async def test_fixed_test_tasks_are_disabled_by_default(settings) -> None:
    server = QuickWorkerServer(settings)
    await server.start()
    try:
        response = await _submit(settings, task_id=new_worker_task_id())
        assert response["success"] is False
        assert response["error"]["code"] == "worker_action_unavailable"
    finally:
        await server.close()


@pytest.mark.anyio
async def test_codex_capability_does_not_enable_fixed_test_tasks(
    settings,
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    server = QuickWorkerServer(
        settings,
        codex_workspaces={"isolated": workspace},
        codex_executable=_fake_codex(tmp_path),
        codex_home=tmp_path / "codex-home",
    )
    await server.start()
    try:
        health = await read_health(settings)
        assert health["data"]["test_tasks_enabled"] is False
        assert health["data"]["codex_tasks_enabled"] is True
        assert health["data"]["codex_workspace_ids"] == ["isolated"]
        rejected = await _submit(settings, task_id=new_worker_task_id())
        assert rejected["success"] is False
        assert rejected["error"]["code"] == "worker_action_unavailable"
        accepted = await _submit_codex(
            settings,
            task_id=new_worker_task_id(),
            session_id="production-session",
        )
        assert accepted["success"] is True
    finally:
        await server.close()


@pytest.mark.anyio
async def test_worker_persists_restart_sensitive_through_final_state(
    settings,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(
        "FAKE_CODEX_SESSION_ID",
        "11111111-1111-4111-8111-111111111111",
    )
    workspace = tmp_path / "chub-workspace"
    workspace.mkdir()
    server = QuickWorkerServer(
        settings,
        codex_workspaces={"chub": workspace},
        codex_executable=_fake_codex(tmp_path),
        codex_home=tmp_path / "codex-home",
    )
    await server.start()
    task_id = new_worker_task_id()
    try:
        submitted = await _request(
            settings,
            "isolated_codex_submit",
            task={
                "task_id": task_id,
                "session_id": "sensitive-session",
                "workspace_id": "chub",
                "prompt": "modify Chub",
                "permission_mode": "auto-review",
                "codex_session_id": None,
                "model": None,
                "reasoning_effort": None,
                "timeout_seconds": 5,
                "task_kind": "standard",
                "restart_sensitive": True,
                "queue_key": None,
                "queue_limit": None,
                "queue_wait_seconds": None,
            },
        )
        assert submitted["success"] is True
        assert submitted["data"]["task"]["restart_sensitive"] is True

        completed = await _wait_for_status(settings, task_id, {"succeeded"})
        assert completed["restart_sensitive"] is True
        listed = await _request(settings, "task_list", limit=100)
        summary = next(
            item for item in listed["data"]["tasks"] if item["task_id"] == task_id
        )
        assert summary["restart_sensitive"] is True
        spec = json.loads(
            (
                worker_state_dir(settings)
                / "tasks"
                / task_id
                / "spec.json"
            ).read_text(encoding="utf-8")
        )
        assert spec["restart_sensitive"] is True
    finally:
        await server.close()


@pytest.mark.anyio
async def test_worker_reads_legacy_v5_persisted_history(settings) -> None:
    first = QuickWorkerServer(settings, allow_test_tasks=True)
    await first.start()
    task_id = new_worker_task_id()
    try:
        submitted = await _submit(settings, task_id=task_id)
        assert submitted["success"] is True
        await _wait_for_status(settings, task_id, {"succeeded"})
    finally:
        await first.close()

    spec_path = worker_state_dir(settings) / "tasks" / task_id / "spec.json"
    spec = json.loads(spec_path.read_text(encoding="utf-8"))
    spec["protocol_version"] = 5
    spec.pop("restart_sensitive")
    spec_path.write_text(json.dumps(spec), encoding="utf-8")
    os.chmod(spec_path, 0o600)

    second = QuickWorkerServer(settings, allow_test_tasks=True)
    await second.start()
    try:
        recovered = await _request(settings, "task_get", task_id=task_id)
        health = await read_health(settings)

        assert recovered["success"] is True
        assert recovered["data"]["task"]["restart_sensitive"] is False
        assert health["data"]["corrupt_tasks"] == 0
    finally:
        await second.close()


@pytest.mark.anyio
async def test_codex_recovery_operations_remain_available_without_executable(
    settings,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    monkeypatch.setattr("app.quick_worker_tasks.shutil.which", lambda _name: None)
    server = QuickWorkerServer(
        settings,
        codex_workspaces={"isolated": workspace},
        codex_home=tmp_path / "codex-home",
    )
    await server.start()
    try:
        health = await read_health(settings)
        listed = await _request(
            settings,
            "task_list",
            limit=100,
            recovery_only=True,
        )
        submitted = await _submit_codex(
            settings,
            task_id=new_worker_task_id(),
            session_id="production-session",
        )

        assert health["data"]["codex_tasks_enabled"] is False
        assert listed == {
            "success": True,
            "request_id": listed["request_id"],
            "data": {"tasks": []},
        }
        assert submitted["success"] is False
        assert submitted["error"]["code"] == "codex_unavailable"
    finally:
        await server.close()


@pytest.mark.anyio
async def test_worker_lists_active_recovery_metadata_and_acknowledges_final_delivery(
    settings,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    native_id = "11111111-1111-4111-8111-111111111111"
    monkeypatch.setenv("FAKE_CODEX_SESSION_ID", native_id)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    server = QuickWorkerServer(
        settings,
        allow_test_tasks=True,
        codex_workspaces={"isolated": workspace},
        codex_executable=_fake_codex(tmp_path),
        codex_home=tmp_path / "codex-home",
    )
    await server.start()
    task_id = new_worker_task_id()
    try:
        submitted = await _submit_codex(
            settings,
            task_id=task_id,
            session_id="recovery-session",
            prompt="sleep:0.2",
        )
        assert submitted["success"] is True

        listed = await _request(
            settings,
            "task_list",
            limit=100,
            active_only=True,
        )
        summary = next(
            item for item in listed["data"]["tasks"] if item["task_id"] == task_id
        )
        assert summary["session_id"] == "recovery-session"
        assert summary["task_kind"] == "standard"
        assert summary["delivery_acknowledged"] is False

        await _wait_for_status(settings, task_id, {"succeeded"})
        pending_delivery = await _request(
            settings,
            "task_list",
            limit=100,
            recovery_only=True,
        )
        assert task_id in {
            item["task_id"] for item in pending_delivery["data"]["tasks"]
        }
        acknowledged = await _request(
            settings,
            "task_acknowledge",
            task_id=task_id,
        )
        repeated = await _request(
            settings,
            "task_acknowledge",
            task_id=task_id,
        )
        assert acknowledged["success"] is True
        assert repeated["data"]["delivery"] == acknowledged["data"]["delivery"]
        delivered = await _request(
            settings,
            "task_list",
            limit=100,
            recovery_only=True,
        )
        assert task_id not in {item["task_id"] for item in delivered["data"]["tasks"]}
        delivery_path = worker_state_dir(settings) / "tasks" / task_id / "delivery.json"
        assert stat.S_IMODE(delivery_path.stat().st_mode) == 0o600
    finally:
        await server.close()


@pytest.mark.anyio
async def test_worker_task_list_fails_closed_on_corrupt_record(settings) -> None:
    server = QuickWorkerServer(settings, allow_test_tasks=True)
    await server.start()
    try:
        task_id = new_worker_task_id()
        submitted = await _submit(settings, task_id=task_id)
        assert submitted["success"] is True
        await _wait_for_status(settings, task_id, {"succeeded"})
        state_path = worker_state_dir(settings) / "tasks" / task_id / "state.json"
        state_path.write_text("{}", encoding="utf-8")

        listed = await _request(
            settings,
            "task_list",
            limit=100,
            active_only=True,
        )

        assert listed["success"] is False
        assert listed["error"]["code"] == "worker_task_store_corrupt"
    finally:
        await server.close()


@pytest.mark.anyio
async def test_worker_recovery_list_fails_closed_on_corrupt_delivery(settings) -> None:
    server = QuickWorkerServer(settings, allow_test_tasks=True)
    await server.start()
    try:
        task_id = new_worker_task_id()
        submitted = await _submit(settings, task_id=task_id)
        assert submitted["success"] is True
        await _wait_for_status(settings, task_id, {"succeeded"})
        delivery_path = worker_state_dir(settings) / "tasks" / task_id / "delivery.json"
        delivery_path.write_text("{}", encoding="utf-8")

        listed = await _request(
            settings,
            "task_list",
            limit=100,
            recovery_only=True,
        )

        assert listed["success"] is False
        assert listed["error"]["code"] == "worker_task_store_corrupt"
    finally:
        await server.close()


@pytest.mark.anyio
async def test_worker_recovery_list_rejects_truncation(settings) -> None:
    server = QuickWorkerServer(settings, allow_test_tasks=True)
    await server.start()
    try:
        server.task_manager._recovery_task_ids = {
            f"qw-1750000000000-{index:032x}" for index in range(101)
        }

        listed = await _request(
            settings,
            "task_list",
            limit=100,
            recovery_only=True,
        )

        assert listed["success"] is False
        assert listed["error"]["code"] == "worker_recovery_set_oversized"
    finally:
        await server.close()


@pytest.mark.anyio
async def test_task_succeeds_persists_private_input_and_survives_restart(settings) -> None:
    task_id = new_worker_task_id()
    prompt = "阶段二隔离任务"
    first = QuickWorkerServer(settings, allow_test_tasks=True)
    await first.start()
    try:
        submitted = await _submit(settings, task_id=task_id, prompt=prompt)
        assert submitted["success"] is True
        assert prompt not in json.dumps(submitted, ensure_ascii=False)
        task = await _wait_for_status(settings, task_id, {"succeeded"})
        assert task["result"] == f"completed: {prompt}"
        assert task["exit_code"] == 0
        assert task["runner_pid"] is None

        task_dir = worker_state_dir(settings) / "tasks" / task_id
        assert stat.S_IMODE(worker_state_dir(settings).stat().st_mode) == 0o700
        assert stat.S_IMODE((worker_state_dir(settings) / "tasks").stat().st_mode) == 0o700
        assert stat.S_IMODE(
            (worker_state_dir(settings) / "tombstones").stat().st_mode
        ) == 0o700
        assert stat.S_IMODE(task_dir.stat().st_mode) == 0o700
        assert json.loads((task_dir / "spec.json").read_text(encoding="utf-8"))[
            "prompt"
        ] == prompt
        for name in (
            "spec.json",
            "state.json",
            "completion.json",
            "stdout.txt",
            "stderr.txt",
        ):
            assert stat.S_IMODE((task_dir / name).stat().st_mode) == 0o600
        tombstone = worker_state_dir(settings) / "tombstones" / f"{task_id}.json"
        assert tombstone.is_file()
        assert stat.S_IMODE(tombstone.stat().st_mode) == 0o600
    finally:
        await first.close()

    second = QuickWorkerServer(settings, allow_test_tasks=True)
    await second.start()
    try:
        restored = await _request(settings, "task_get", task_id=task_id)
        assert restored["success"] is True
        assert restored["data"]["task"]["status"] == "succeeded"
        assert restored["data"]["task"]["result"] == f"completed: {prompt}"
    finally:
        await second.close()


@pytest.mark.anyio
async def test_same_task_is_idempotent_and_changed_spec_conflicts(settings) -> None:
    server = QuickWorkerServer(settings, allow_test_tasks=True)
    await server.start()
    task_id = new_worker_task_id()
    try:
        first = await _submit(
            settings,
            task_id=task_id,
            prompt="one execution",
            run_seconds=0.2,
        )
        duplicate = await _submit(
            settings,
            task_id=task_id,
            prompt="one execution",
            run_seconds=0.2,
        )
        conflict = await _submit(
            settings,
            task_id=task_id,
            prompt="different execution",
            run_seconds=0.2,
        )

        assert first["success"] is True
        assert duplicate["success"] is True
        assert duplicate["data"]["task"]["task_id"] == task_id
        assert conflict["success"] is False
        assert conflict["error"]["code"] == "worker_task_conflict"
        await _wait_for_status(settings, task_id, {"succeeded"})
        listed = await _request(settings, "task_list", limit=100)
        assert [item["task_id"] for item in listed["data"]["tasks"]].count(task_id) == 1
    finally:
        await server.close()


@pytest.mark.anyio
async def test_failed_acceptance_rolls_back_owned_session_lease(
    settings,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    native_id = "10101010-1010-4010-8010-101010101010"
    monkeypatch.setenv("FAKE_CODEX_SESSION_ID", native_id)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    server = QuickWorkerServer(
        settings,
        allow_test_tasks=True,
        codex_workspaces={"isolated": workspace},
        codex_executable=_fake_codex(tmp_path),
        codex_home=tmp_path / "codex-home",
    )
    await server.start()
    failed_task = new_worker_task_id()
    replacement_task = new_worker_task_id()
    original_write_state = server.task_manager._write_state
    failed_once = False

    def fail_first_state_write(state) -> None:
        nonlocal failed_once
        if state.task_id == failed_task and not failed_once:
            failed_once = True
            raise OSError("simulated state persistence failure")
        original_write_state(state)

    monkeypatch.setattr(server.task_manager, "_write_state", fail_first_state_write)
    try:
        rejected = await _submit_codex(
            settings,
            task_id=failed_task,
            session_id="rollback-session",
            codex_session_id=native_id,
        )
        accepted = await _submit_codex(
            settings,
            task_id=replacement_task,
            session_id="rollback-session",
            codex_session_id=native_id,
        )

        assert rejected["success"] is False
        assert rejected["error"]["code"] == "worker_internal_error"
        assert accepted["success"] is True
        await _wait_for_status(settings, replacement_task, {"succeeded"})
        assert not (
            worker_state_dir(settings) / "tasks" / failed_task
        ).exists()
    finally:
        await server.close()


@pytest.mark.anyio
async def test_worker_rejects_new_task_at_fixed_active_capacity(
    settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("app.quick_worker_tasks.MAX_ACTIVE_TASKS", 1)
    server = QuickWorkerServer(settings, allow_test_tasks=True)
    await server.start()
    first_task = new_worker_task_id()
    try:
        await _submit(
            settings,
            task_id=first_task,
            behavior="ignore_term",
            run_seconds=20.0,
            timeout_seconds=30.0,
        )
        await _wait_for_status(settings, first_task, {"running"})

        rejected = await _submit(settings, task_id=new_worker_task_id())

        assert rejected["success"] is False
        assert rejected["error"]["code"] == "worker_capacity_reached"
    finally:
        await server.close()


@pytest.mark.anyio
async def test_tombstone_prevents_replay_after_completed_payload_is_retired(settings) -> None:
    server = QuickWorkerServer(settings, allow_test_tasks=True)
    await server.start()
    task_id = new_worker_task_id()
    try:
        await _submit(settings, task_id=task_id, prompt="retired result")
        await _wait_for_status(settings, task_id, {"succeeded"})
        shutil.rmtree(worker_state_dir(settings) / "tasks" / task_id)

        duplicate = await _submit(settings, task_id=task_id, prompt="retired result")
        conflict = await _submit(settings, task_id=task_id, prompt="different result")

        assert duplicate["success"] is False
        assert duplicate["error"]["code"] == "worker_task_expired"
        assert conflict["success"] is False
        assert conflict["error"]["code"] == "worker_task_conflict"
    finally:
        await server.close()


@pytest.mark.anyio
async def test_task_id_outside_retry_window_is_rejected_before_persistence(settings) -> None:
    server = QuickWorkerServer(settings, allow_test_tasks=True)
    await server.start()
    old_id = new_worker_task_id(datetime.now(UTC) - timedelta(days=8))
    try:
        response = await _submit(settings, task_id=old_id)
        assert response["success"] is False
        assert response["error"]["code"] == "worker_task_id_expired"
        assert not (worker_state_dir(settings) / "tasks" / old_id).exists()
    finally:
        await server.close()


@pytest.mark.anyio
async def test_cancel_stops_entire_fixed_runner_process_group(settings) -> None:
    server = QuickWorkerServer(settings, allow_test_tasks=True)
    await server.start()
    task_id = new_worker_task_id()
    try:
        assert (
            await _submit(
                settings,
                task_id=task_id,
                behavior="ignore_term",
                run_seconds=20.0,
                timeout_seconds=30.0,
            )
        )["success"] is True
        running = await _wait_for_status(settings, task_id, {"running"})
        runner_pid = running["runner_pid"]
        assert psutil.pid_exists(runner_pid)

        cancelled = await _request(settings, "task_cancel", task_id=task_id)

        assert cancelled["success"] is True
        assert cancelled["data"]["task"]["status"] == "cancelled"
        await asyncio.sleep(0.05)
        assert not psutil.pid_exists(runner_pid)
    finally:
        await server.close()


@pytest.mark.anyio
async def test_absolute_timeout_stops_runner_and_records_final_state(settings) -> None:
    server = QuickWorkerServer(settings, allow_test_tasks=True)
    await server.start()
    task_id = new_worker_task_id()
    try:
        await _submit(
            settings,
            task_id=task_id,
            behavior="ignore_term",
            run_seconds=20.0,
            timeout_seconds=0.15,
        )
        running = await _wait_for_status(settings, task_id, {"running"})
        runner_pid = running["runner_pid"]
        timed_out = await _wait_for_status(settings, task_id, {"timed_out"})

        assert timed_out["error_code"] == "deadline_exceeded"
        assert timed_out["runner_pid"] is None
        assert not psutil.pid_exists(runner_pid)
    finally:
        await server.close()


@pytest.mark.anyio
async def test_runner_failure_is_bounded_and_not_reported_as_success(settings) -> None:
    server = QuickWorkerServer(settings, allow_test_tasks=True)
    await server.start()
    task_id = new_worker_task_id()
    try:
        await _submit(settings, task_id=task_id, behavior="fail")
        failed = await _wait_for_status(settings, task_id, {"failed"})
        assert failed["error_code"] == "runner_failed"
        assert failed["exit_code"] == 23
        assert "failed as requested" in failed["error"]
        assert len(failed["error"].encode("utf-8")) <= 4_000
    finally:
        await server.close()


@pytest.mark.anyio
async def test_worker_restart_interrupts_uncertain_runner_without_replay(settings) -> None:
    task_id = new_worker_task_id()
    first = QuickWorkerServer(settings, allow_test_tasks=True)
    await first.start()
    await _submit(
        settings,
        task_id=task_id,
        prompt="must not replay",
        behavior="ignore_term",
        run_seconds=20.0,
        timeout_seconds=30.0,
    )
    running = await _wait_for_status(settings, task_id, {"running"})
    runner_pid = running["runner_pid"]
    await first.close(interrupt_tasks=False)
    assert psutil.pid_exists(runner_pid)

    second = QuickWorkerServer(settings, allow_test_tasks=True)
    await second.start()
    try:
        recovered = await _request(settings, "task_get", task_id=task_id)
        assert recovered["success"] is True
        assert recovered["data"]["task"]["status"] == "failed"
        assert recovered["data"]["task"]["error_code"] == "worker_restarted"
        assert recovered["data"]["task"]["result"] is None
        await asyncio.sleep(0.05)
        assert not psutil.pid_exists(runner_pid)

        duplicate = await _submit(
            settings,
            task_id=task_id,
            prompt="must not replay",
            behavior="ignore_term",
            run_seconds=20.0,
            timeout_seconds=30.0,
        )
        assert duplicate["success"] is True
        assert duplicate["data"]["task"]["status"] == "failed"
    finally:
        await second.close()


@pytest.mark.anyio
async def test_corrupt_record_fails_closed_and_is_not_replayed(settings) -> None:
    task_id = new_worker_task_id()
    first = QuickWorkerServer(settings, allow_test_tasks=True)
    await first.start()
    try:
        await _submit(settings, task_id=task_id, prompt="keep private")
        await _wait_for_status(settings, task_id, {"succeeded"})
    finally:
        await first.close()

    spec_path = worker_state_dir(settings) / "tasks" / task_id / "spec.json"
    spec_path.write_text("{not-json", encoding="utf-8")
    os.chmod(spec_path, 0o600)

    second = QuickWorkerServer(settings, allow_test_tasks=True)
    await second.start()
    try:
        health = await read_health(settings)
        assert health["data"]["corrupt_tasks"] == 1
        queried = await _request(settings, "task_get", task_id=task_id)
        assert queried["success"] is False
        assert queried["error"]["code"] == "worker_task_corrupt"
        duplicate = await _submit(settings, task_id=task_id, prompt="keep private")
        assert duplicate["success"] is False
        assert duplicate["error"]["code"] == "worker_task_corrupt"
    finally:
        await second.close()


@pytest.mark.anyio
async def test_full_8000_character_multibyte_prompt_fits_task_protocol(settings) -> None:
    server = QuickWorkerServer(settings, allow_test_tasks=True)
    await server.start()
    task_id = new_worker_task_id()
    prompt = "𠮷" * 8_000
    try:
        submitted = await _submit(settings, task_id=task_id, prompt=prompt)
        assert submitted["success"] is True
        finished = await _wait_for_status(settings, task_id, {"succeeded"})
        assert finished["result"] == f"completed: {prompt}"
    finally:
        await server.close()


@pytest.mark.anyio
async def test_incomplete_connection_is_closed_after_fixed_read_timeout(settings) -> None:
    server = QuickWorkerServer(
        settings,
        allow_test_tasks=True,
        request_timeout_seconds=0.05,
    )
    await server.start()
    try:
        reader, writer = await asyncio.open_unix_connection(worker_socket_path(settings))
        response = json.loads(await asyncio.wait_for(reader.readline(), timeout=1.0))
        assert response["success"] is False
        assert response["error"]["code"] == "worker_request_invalid"
        writer.close()
        await writer.wait_closed()
    finally:
        await server.close()


@pytest.mark.anyio
async def test_legacy_health_probe_remains_diagnostic_but_tasks_require_v2(settings) -> None:
    server = QuickWorkerServer(settings, allow_test_tasks=True)
    await server.start()
    try:
        health = await worker_request(
            settings,
            {
                "protocol_version": HEALTH_PROTOCOL_VERSION,
                "request_id": uuid.uuid4().hex,
                "action": "health",
            },
        )
        legacy_task = await worker_request(
            settings,
            {
                "protocol_version": HEALTH_PROTOCOL_VERSION,
                "request_id": uuid.uuid4().hex,
                "action": "test_task_submit",
                "task": {
                    "task_id": new_worker_task_id(),
                    "prompt": "must not run",
                    "behavior": "succeed",
                    "run_seconds": 0.0,
                    "timeout_seconds": 5.0,
                },
            },
        )

        assert health["success"] is True
        assert health["data"]["protocol_version"] == PROTOCOL_VERSION
        assert legacy_task["success"] is False
        assert legacy_task["error"]["code"] == "worker_protocol_incompatible"
    finally:
        await server.close()


@pytest.mark.anyio
async def test_cancel_confirms_orphan_child_process_group_is_gone(settings) -> None:
    server = QuickWorkerServer(settings, allow_test_tasks=True)
    await server.start()
    task_id = new_worker_task_id()
    try:
        await _submit(
            settings,
            task_id=task_id,
            behavior="orphan_child",
            run_seconds=20.0,
            timeout_seconds=30.0,
        )
        running = await _wait_for_status(settings, task_id, {"running"})
        runner_pid = running["runner_pid"]
        deadline = asyncio.get_running_loop().time() + 2.0
        members: list[int] = []
        while asyncio.get_running_loop().time() < deadline:
            members = server.task_manager._process_group_members(runner_pid)
            if len(members) >= 2:
                break
            await asyncio.sleep(0.02)
        assert len(members) >= 2

        cancelled = await _request(settings, "task_cancel", task_id=task_id)

        assert cancelled["success"] is True
        assert cancelled["data"]["task"]["status"] == "cancelled"
        assert server.task_manager._process_group_members(runner_pid) == []
    finally:
        await server.close()


@pytest.mark.anyio
async def test_mismatched_state_identity_fails_closed(settings) -> None:
    server = QuickWorkerServer(settings, allow_test_tasks=True)
    await server.start()
    task_id = new_worker_task_id()
    try:
        await _submit(settings, task_id=task_id)
        await _wait_for_status(settings, task_id, {"succeeded"})
        state_path = worker_state_dir(settings) / "tasks" / task_id / "state.json"
        state = json.loads(state_path.read_text(encoding="utf-8"))
        state["task_id"] = new_worker_task_id()
        state_path.write_text(json.dumps(state), encoding="utf-8")
        state_path.chmod(0o600)

        response = await _request(settings, "task_get", task_id=task_id)

        assert response["success"] is False
        assert response["error"]["code"] == "worker_task_corrupt"
    finally:
        await server.close()


@pytest.mark.anyio
async def test_inconsistent_execution_deadline_fails_closed(settings) -> None:
    server = QuickWorkerServer(settings, allow_test_tasks=True)
    await server.start()
    task_id = new_worker_task_id()
    try:
        await _submit(settings, task_id=task_id)
        await _wait_for_status(settings, task_id, {"succeeded"})
        state_path = worker_state_dir(settings) / "tasks" / task_id / "state.json"
        state = json.loads(state_path.read_text(encoding="utf-8"))
        state["execution_deadline_at"] = "2999-01-01T00:00:00Z"
        state_path.write_text(json.dumps(state), encoding="utf-8")
        state_path.chmod(0o600)

        response = await _request(settings, "task_get", task_id=task_id)

        assert response["success"] is False
        assert response["error"]["code"] == "worker_task_corrupt"
    finally:
        await server.close()


@pytest.mark.anyio
async def test_codex_first_turn_persists_native_id_and_resume_uses_it(
    settings,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    native_id = "019ffb1c-b704-72e1-9a12-ae38aa6e572a"
    monkeypatch.setenv("FAKE_CODEX_SESSION_ID", native_id)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    server = QuickWorkerServer(
        settings,
        allow_test_tasks=True,
        codex_workspaces={"isolated": workspace},
        codex_executable=_fake_codex(tmp_path),
        codex_home=tmp_path / "codex-home",
    )
    await server.start()
    first_task_id = new_worker_task_id()
    second_task_id = new_worker_task_id()
    try:
        first = await _submit_codex(
            settings,
            task_id=first_task_id,
            session_id="chub-session-1",
            prompt="first turn",
        )
        assert first["success"] is True
        created = await _wait_for_status(settings, first_task_id, {"succeeded"})
        assert created["native_session_id"] == native_id
        assert created["result"] == f"created:{native_id}:first turn"

        resumed_submission = await _submit_codex(
            settings,
            task_id=second_task_id,
            session_id="chub-session-1",
            codex_session_id=native_id,
            prompt="second turn",
        )
        assert resumed_submission["success"] is True
        resumed = await _wait_for_status(settings, second_task_id, {"succeeded"})
        assert resumed["native_session_id"] == native_id
        assert resumed["result"] == f"resumed:{native_id}:second turn"

        for task_id in (first_task_id, second_task_id):
            task_dir = worker_state_dir(settings) / "tasks" / task_id
            state = json.loads((task_dir / "state.json").read_text(encoding="utf-8"))
            completion = json.loads(
                (task_dir / "completion.json").read_text(encoding="utf-8")
            )
            assert state["native_session_id"] == native_id
            assert completion["native_session_id"] == native_id
        assert list((worker_state_dir(settings) / "session-leases").iterdir()) == []
    finally:
        await server.close()


@pytest.mark.anyio
async def test_translation_queue_is_fifo_and_uses_latest_native_session(
    settings,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    native_id = "019ffb1c-b704-72e1-9a12-ae38aa6e572a"
    monkeypatch.setenv("FAKE_CODEX_SESSION_ID", native_id)
    monkeypatch.setenv("FAKE_CODEX_DELAY", "0.15")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    codex_home = tmp_path / "codex-home"
    _set_native_archive_state(codex_home, native_id, archived=False)
    server = QuickWorkerServer(
        settings,
        allow_test_tasks=True,
        codex_workspaces={"isolated": workspace},
        codex_executable=_fake_codex(tmp_path),
        codex_home=codex_home,
    )
    await server.start()
    first_id = new_worker_task_id()
    second_id = new_worker_task_id()
    try:
        first = await _submit_codex(
            settings,
            task_id=first_id,
            session_id="translation-session",
            prompt="first",
            task_kind="translation",
            queue_key="translation-queue",
            queue_limit=2,
            queue_wait_seconds=5,
        )
        second = await _submit_codex(
            settings,
            task_id=second_id,
            session_id="translation-session",
            prompt="second",
            task_kind="translation",
            queue_key="translation-queue",
            queue_limit=2,
            queue_wait_seconds=5,
        )
        assert first["success"] is True
        assert second["success"] is True
        queued = await _request(settings, "task_get", task_id=second_id)
        assert queued["data"]["task"]["status"] == "queued"

        created = await _wait_for_status(settings, first_id, {"succeeded"})
        resumed = await _wait_for_status(settings, second_id, {"succeeded"})

        assert created["result"] == f"created:{native_id}:first"
        assert resumed["result"] == f"resumed:{native_id}:second"
        assert resumed["native_session_id"] == native_id
    finally:
        await server.close()


@pytest.mark.anyio
async def test_translation_queue_replaces_archived_native_session(
    settings,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    archived_id = "019ffb1c-b704-72e1-9a12-ae38aa6e572a"
    replacement_id = "019ffb1c-b704-72e1-9a12-ae38aa6e572b"
    monkeypatch.setenv("FAKE_CODEX_SESSION_ID", replacement_id)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    codex_home = tmp_path / "codex-home"
    _set_native_archive_state(codex_home, archived_id, archived=True)
    server = QuickWorkerServer(
        settings,
        allow_test_tasks=True,
        codex_workspaces={"isolated": workspace},
        codex_executable=_fake_codex(tmp_path),
        codex_home=codex_home,
    )
    await server.start()
    task_id = new_worker_task_id()
    try:
        submitted = await _submit_codex(
            settings,
            task_id=task_id,
            session_id="translation-session",
            codex_session_id=archived_id,
            prompt="replace archived",
            task_kind="translation",
            queue_key="translation-queue",
            queue_limit=2,
            queue_wait_seconds=5,
        )
        assert submitted["success"] is True

        completed = await _wait_for_status(settings, task_id, {"succeeded"})

        assert completed["native_session_id"] == replacement_id
        assert completed["result"] == f"created:{replacement_id}:replace archived"
        state = json.loads(
            (worker_state_dir(settings) / "tasks" / task_id / "state.json").read_text(
                encoding="utf-8"
            )
        )
        assert state["expected_native_session_id"] is None
    finally:
        await server.close()


@pytest.mark.anyio
async def test_translation_queue_new_session_starts_new_native_session(
    settings,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    native_id = "019ffb1c-b704-72e1-9a12-ae38aa6e572a"
    monkeypatch.setenv("FAKE_CODEX_SESSION_ID", native_id)
    monkeypatch.setenv("FAKE_CODEX_DELAY", "0.15")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    server = QuickWorkerServer(
        settings,
        allow_test_tasks=True,
        codex_workspaces={"isolated": workspace},
        codex_executable=_fake_codex(tmp_path),
        codex_home=tmp_path / "codex-home",
    )
    await server.start()
    old_id = new_worker_task_id()
    new_id = new_worker_task_id()
    try:
        old_submission = await _submit_codex(
            settings,
            task_id=old_id,
            session_id="old-translation-session",
            prompt="old generation",
            task_kind="translation",
            queue_key="translation-queue",
            queue_limit=2,
            queue_wait_seconds=5,
        )
        assert old_submission["success"] is True
        new_submission = await _submit_codex(
            settings,
            task_id=new_id,
            session_id="new-translation-session",
            prompt="new generation",
            task_kind="translation",
            queue_key="translation-queue",
            queue_limit=2,
            queue_wait_seconds=5,
        )
        assert new_submission["success"] is True
        queued = await _request(settings, "task_get", task_id=new_id)
        assert queued["data"]["task"]["status"] == "queued"

        old_task = await _wait_for_status(settings, old_id, {"succeeded"})
        new_task = await _wait_for_status(settings, new_id, {"succeeded"})

        assert old_task["result"] == f"created:{native_id}:old generation"
        assert new_task["result"] == f"created:{native_id}:new generation"
        state = json.loads(
            (
                worker_state_dir(settings) / "tasks" / new_id / "state.json"
            ).read_text(encoding="utf-8")
        )
        assert state["expected_native_session_id"] is None
    finally:
        await server.close()


@pytest.mark.anyio
async def test_translation_queue_capacity_and_wait_deadline_fail_closed(
    settings,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(
        "FAKE_CODEX_SESSION_ID", "019ffb1c-b704-72e1-9a12-ae38aa6e572a"
    )
    monkeypatch.setenv("FAKE_CODEX_DELAY", "0.3")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    server = QuickWorkerServer(
        settings,
        allow_test_tasks=True,
        codex_workspaces={"isolated": workspace},
        codex_executable=_fake_codex(tmp_path),
        codex_home=tmp_path / "codex-home",
    )
    await server.start()
    first_id = new_worker_task_id()
    timed_id = new_worker_task_id()
    rejected_id = new_worker_task_id()
    try:
        for task_id, wait_seconds in ((first_id, 5), (timed_id, 0.1)):
            response = await _submit_codex(
                settings,
                task_id=task_id,
                session_id="translation-session",
                task_kind="translation",
                queue_key="translation-queue",
                queue_limit=2,
                queue_wait_seconds=wait_seconds,
            )
            assert response["success"] is True
        rejected = await _submit_codex(
            settings,
            task_id=rejected_id,
            session_id="translation-session",
            task_kind="translation",
            queue_key="translation-queue",
            queue_limit=2,
            queue_wait_seconds=5,
        )
        assert rejected["success"] is False
        assert rejected["error"]["code"] == "worker_queue_capacity_reached"

        timed = await _wait_for_status(settings, timed_id, {"timed_out"})
        assert timed["error_code"] == "queue_deadline_exceeded"
        await _wait_for_status(settings, first_id, {"succeeded"})
    finally:
        await server.close()


@pytest.mark.anyio
async def test_codex_result_is_retained_when_native_id_cannot_be_confirmed(
    settings,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    native_id = "66666666-6666-4666-8666-666666666666"
    monkeypatch.setenv("FAKE_CODEX_SESSION_ID", native_id)
    monkeypatch.setenv("FAKE_CODEX_OMIT_EVENT", "1")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    server = QuickWorkerServer(
        settings,
        allow_test_tasks=True,
        codex_workspaces={"isolated": workspace},
        codex_executable=_fake_codex(tmp_path),
        codex_home=tmp_path / "codex-home",
    )
    await server.start()
    task_id = new_worker_task_id()
    try:
        await _submit_codex(
            settings,
            task_id=task_id,
            session_id="unconfirmed-session",
            prompt="preserve this result",
        )
        failed = await _wait_for_status(settings, task_id, {"failed"})
        assert failed["error_code"] == "native_session_unconfirmed"
        assert failed["result"] == f"created:{native_id}:preserve this result"
        assert failed["native_session_id"] is None
    finally:
        await server.close()


@pytest.mark.anyio
async def test_codex_resume_rejects_unexpected_native_session_id(
    settings,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    expected_id = "77777777-7777-4777-8777-777777777777"
    unexpected_id = "88888888-8888-4888-8888-888888888888"
    monkeypatch.setenv("FAKE_CODEX_SESSION_ID", expected_id)
    monkeypatch.setenv("FAKE_CODEX_FORCE_ID", unexpected_id)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    server = QuickWorkerServer(
        settings,
        allow_test_tasks=True,
        codex_workspaces={"isolated": workspace},
        codex_executable=_fake_codex(tmp_path),
        codex_home=tmp_path / "codex-home",
    )
    await server.start()
    task_id = new_worker_task_id()
    try:
        await _submit_codex(
            settings,
            task_id=task_id,
            session_id="mismatched-resume",
            codex_session_id=expected_id,
        )
        failed = await _wait_for_status(settings, task_id, {"failed"})
        assert failed["error_code"] == "native_session_unconfirmed"
        assert failed["native_session_id"] is None
    finally:
        await server.close()


@pytest.mark.anyio
async def test_codex_session_lease_blocks_same_session_but_not_other_sessions(
    settings,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    native_a = "22222222-2222-4222-8222-222222222222"
    native_b = "33333333-3333-4333-8333-333333333333"
    monkeypatch.setenv("FAKE_CODEX_SESSION_ID", native_a)
    monkeypatch.setenv("FAKE_CODEX_DELAY", "0.25")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    server = QuickWorkerServer(
        settings,
        allow_test_tasks=True,
        codex_workspaces={"isolated": workspace},
        codex_executable=_fake_codex(tmp_path),
        codex_home=tmp_path / "codex-home",
    )
    await server.start()
    first_task = new_worker_task_id()
    blocked_task = new_worker_task_id()
    parallel_task = new_worker_task_id()
    try:
        assert (
            await _submit_codex(
                settings,
                task_id=first_task,
                session_id="session-a",
                codex_session_id=native_a,
            )
        )["success"] is True
        await _wait_for_status(settings, first_task, {"running"})

        blocked = await _submit_codex(
            settings,
            task_id=blocked_task,
            session_id="session-a",
            codex_session_id=native_a,
        )
        parallel = await _submit_codex(
            settings,
            task_id=parallel_task,
            session_id="session-b",
            codex_session_id=native_b,
        )

        assert blocked["success"] is False
        assert blocked["error"]["code"] == "worker_session_busy"
        assert parallel["success"] is True
        await _wait_for_status(settings, first_task, {"succeeded"})
        await _wait_for_status(settings, parallel_task, {"succeeded"})
    finally:
        await server.close()


@pytest.mark.anyio
async def test_codex_runner_process_is_owned_by_worker_process(
    settings,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    native_id = "22222222-2222-4222-8222-222222222222"
    monkeypatch.setenv("FAKE_CODEX_SESSION_ID", native_id)
    monkeypatch.setenv("FAKE_CODEX_DELAY", "0.3")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    server = QuickWorkerServer(
        settings,
        allow_test_tasks=True,
        codex_workspaces={"isolated": workspace},
        codex_executable=_fake_codex(tmp_path),
        codex_home=tmp_path / "codex-home",
    )
    await server.start()
    task_id = new_worker_task_id()
    try:
        submitted = await _submit_codex(
            settings,
            task_id=task_id,
            session_id="owned-runner-session",
            codex_session_id=native_id,
        )
        assert submitted["success"] is True
        running = await _wait_for_status(settings, task_id, {"running"})
        runner_pid = running["runner_pid"]
        assert isinstance(runner_pid, int)
        assert psutil.Process(runner_pid).ppid() == os.getpid()
        assert server.task_manager._processes[task_id].pid == runner_pid
        await _wait_for_status(settings, task_id, {"succeeded"})
    finally:
        await server.close()


@pytest.mark.anyio
async def test_codex_runner_restart_script_only_registers_deferred_request(
    settings,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    native_id = "33333333-3333-4333-8333-333333333333"
    monkeypatch.setenv("FAKE_CODEX_SESSION_ID", native_id)
    monkeypatch.setenv("CHUB_TEST_PLATFORM", "Unsupported")
    restart_script = Path(__file__).resolve().parents[1] / "scripts/chub-web-restart"
    monkeypatch.setenv("FAKE_CODEX_RESTART_SCRIPT", str(restart_script))
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    executable = tmp_path / "fake-codex-restart"
    executable.write_text(
        """#!/usr/bin/env python3
import json
import os
import subprocess
import sys
from pathlib import Path
args = sys.argv[1:]
result_path = Path(args[args.index("--output-last-message") + 1])
native_id = os.environ["FAKE_CODEX_SESSION_ID"]
print(json.dumps({"type": "thread.started", "thread_id": native_id}), flush=True)
completed = subprocess.run(
    [os.environ["FAKE_CODEX_RESTART_SCRIPT"]],
    check=False,
    capture_output=True,
    text=True,
)
if completed.returncode != 0:
    print(completed.stderr, file=sys.stderr)
    raise SystemExit(completed.returncode)
result_path.write_text(completed.stdout.strip(), encoding="utf-8")
""",
        encoding="utf-8",
    )
    executable.chmod(0o700)
    server = QuickWorkerServer(
        settings,
        allow_test_tasks=True,
        codex_workspaces={"isolated": workspace},
        codex_executable=executable,
        codex_home=tmp_path / "codex-home",
    )
    await server.start()
    task_id = new_worker_task_id()
    try:
        submitted = await _submit_codex(
            settings,
            task_id=task_id,
            session_id="restart-registration-session",
        )
        assert submitted["success"] is True
        finished = await _wait_for_status(settings, task_id, {"succeeded"})
        assert "restart registered" in finished["result"]
        request_path = (
            settings.codex_pty.runtime_dir / "restart-requests" / f"{task_id}.request"
        )
        assert request_path.is_file()
        assert stat.S_IMODE(request_path.stat().st_mode) == 0o600
    finally:
        await server.close()


@pytest.mark.anyio
async def test_codex_native_writer_is_final_start_arbitrator(
    settings,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    native_id = "44444444-4444-4444-8444-444444444444"
    monkeypatch.setenv("FAKE_CODEX_SESSION_ID", native_id)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    codex_home = tmp_path / "codex-home"
    lock_dir = codex_home / "thread-writer-locks"
    lock_dir.mkdir(parents=True)
    lock_path = lock_dir / f"{native_id}.lock"
    descriptor = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o600)
    fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
    server = QuickWorkerServer(
        settings,
        allow_test_tasks=True,
        codex_workspaces={"isolated": workspace},
        codex_executable=_fake_codex(tmp_path),
        codex_home=codex_home,
    )
    await server.start()
    task_id = new_worker_task_id()
    try:
        submitted = await _submit_codex(
            settings,
            task_id=task_id,
            session_id="session-with-writer",
            codex_session_id=native_id,
        )
        assert submitted["success"] is True
        failed = await _wait_for_status(settings, task_id, {"failed"})
        assert failed["error_code"] == "codex_session_busy"
        assert failed["runner_pid"] is None
    finally:
        await server.close()
        fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)


@pytest.mark.anyio
async def test_codex_recovery_preserves_observed_native_id_without_replay(
    settings,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    native_id = "55555555-5555-4555-8555-555555555555"
    monkeypatch.setenv("FAKE_CODEX_SESSION_ID", native_id)
    monkeypatch.setenv("FAKE_CODEX_DELAY", "20")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    executable = _fake_codex(tmp_path)
    first = QuickWorkerServer(
        settings,
        allow_test_tasks=True,
        codex_workspaces={"isolated": workspace},
        codex_executable=executable,
        codex_home=tmp_path / "codex-home",
    )
    await first.start()
    task_id = new_worker_task_id()
    await _submit_codex(
        settings,
        task_id=task_id,
        session_id="recovered-session",
        prompt="do not replay",
        timeout_seconds=30.0,
    )
    running = await _wait_for_status(settings, task_id, {"running"})
    runner_pid = running["runner_pid"]
    deadline = asyncio.get_running_loop().time() + 2.0
    while asyncio.get_running_loop().time() < deadline:
        current = await _request(settings, "task_get", task_id=task_id)
        if current["data"]["task"]["native_session_id"] == native_id:
            break
        await asyncio.sleep(0.02)
    else:
        raise AssertionError("native Session ID was not persisted while running")
    await first.close(interrupt_tasks=False)
    assert psutil.pid_exists(runner_pid)

    second = QuickWorkerServer(
        settings,
        allow_test_tasks=True,
        codex_workspaces={"isolated": workspace},
        codex_executable=executable,
        codex_home=tmp_path / "codex-home",
    )
    await second.start()
    try:
        recovered = await _request(settings, "task_get", task_id=task_id)
        assert recovered["success"] is True
        assert recovered["data"]["task"]["status"] == "failed"
        assert recovered["data"]["task"]["error_code"] == "worker_restarted"
        assert recovered["data"]["task"]["native_session_id"] == native_id
        assert not psutil.pid_exists(runner_pid)
        assert list((worker_state_dir(settings) / "session-leases").iterdir()) == []
    finally:
        await second.close()


@pytest.mark.anyio
async def test_codex_recovery_does_not_adopt_mismatched_resume_id(
    settings,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    expected_id = "30303030-3030-4030-8030-303030303030"
    unexpected_id = "40404040-4040-4040-8040-404040404040"
    monkeypatch.setenv("FAKE_CODEX_SESSION_ID", expected_id)
    monkeypatch.setenv("FAKE_CODEX_FORCE_ID", unexpected_id)
    monkeypatch.setenv("FAKE_CODEX_DELAY", "20")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    executable = _fake_codex(tmp_path)
    first = QuickWorkerServer(
        settings,
        allow_test_tasks=True,
        codex_workspaces={"isolated": workspace},
        codex_executable=executable,
        codex_home=tmp_path / "codex-home",
    )
    await first.start()
    task_id = new_worker_task_id()
    await _submit_codex(
        settings,
        task_id=task_id,
        session_id="mismatched-recovery",
        codex_session_id=expected_id,
        timeout_seconds=30.0,
    )
    running = await _wait_for_status(settings, task_id, {"running"})
    runner_pid = running["runner_pid"]
    await asyncio.sleep(0.1)
    await first.close(interrupt_tasks=False)
    assert psutil.pid_exists(runner_pid)

    second = QuickWorkerServer(
        settings,
        allow_test_tasks=True,
        codex_workspaces={"isolated": workspace},
        codex_executable=executable,
        codex_home=tmp_path / "codex-home",
    )
    await second.start()
    try:
        recovered = await _request(settings, "task_get", task_id=task_id)
        assert recovered["success"] is True
        assert recovered["data"]["task"]["status"] == "failed"
        assert recovered["data"]["task"]["error_code"] == "worker_restarted"
        assert recovered["data"]["task"]["native_session_id"] is None
        assert not psutil.pid_exists(runner_pid)
    finally:
        await second.close()
