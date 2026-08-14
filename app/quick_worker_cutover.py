from __future__ import annotations

import json
import os
import stat
import uuid
from datetime import UTC, datetime
from pathlib import Path

from pydantic import ValidationError

from app.codex.models import CodexSession, QuickInteractionTask
from app.core.config import Settings
from app.quick_worker_tasks import MAX_TASK_DIRECTORIES, WorkerTaskSummary, worker_state_dir
from app.services.deferred_restart import parse_deferred_restart_state
from app.services.weixin_translation import TranslationState


MAX_PREFLIGHT_STATE_BYTES = 8 * 1024 * 1024
EXPECTED_WORKSPACE_IDS = {
    "chub",
    "home",
    "weixin-translation",
    "workspace",
}


def _blocker(code: str, message: str, *, count: int | None = None) -> dict[str, object]:
    blocker: dict[str, object] = {"code": code, "message": message}
    if count is not None:
        blocker["count"] = count
    return blocker


def _read_json(path: Path, *, max_bytes: int = MAX_PREFLIGHT_STATE_BYTES) -> object:
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        return []
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
        raise OSError("state path is not a regular file")
    if metadata.st_uid != os.getuid() or stat.S_IMODE(metadata.st_mode) & 0o077:
        raise OSError("state file owner or permissions are unsafe")
    if metadata.st_size > max_bytes:
        raise OSError("state file exceeds its fixed limit")
    with path.open("rb") as state_file:
        content = state_file.read(max_bytes + 1)
    if len(content) > max_bytes:
        raise OSError("state file exceeds its fixed limit")
    return json.loads(content.decode("utf-8"))


def _quick_state(settings: Settings) -> tuple[int, int, int]:
    payload = _read_json(settings.codex_pty.data_file.with_name("quick-interactions.json"))
    if not isinstance(payload, list):
        raise ValueError("quick interaction state root is invalid")
    active = 0
    undelivered = 0
    pending_delivery = 0
    for item in payload:
        if not isinstance(item, dict):
            raise ValueError("quick interaction state entry is invalid")
        task_payload = dict(item)
        confirmed = task_payload.pop("_worker_delivery_confirmed", False)
        task_payload.pop("_notification_route", None)
        task_payload.pop("_deferred_restart_context", None)
        task_payload.pop("_operation_context", None)
        task = QuickInteractionTask.model_validate(task_payload)
        active += task.status in {"requested", "running"}
        undelivered += bool(
            task.worker_task_id
            and task.status not in {"requested", "running"}
            and confirmed is not True
        )
        pending_delivery += bool(
            task.notification_status in {"pending", "sending"}
            or task.deferred_restart_status in {"pending", "started"}
            or task.deferred_restart_notification_status in {"pending", "sending"}
        )
    return active, undelivered, pending_delivery


def _deferred_restart_state(settings: Settings) -> int:
    payload = _read_json(
        settings.codex_pty.data_file.with_name("deferred-restart.json"),
        max_bytes=64 * 1024,
    )
    if payload == []:
        return 0
    parse_deferred_restart_state(payload)
    return 1


def _translation_state(settings: Settings) -> int:
    path = settings.openclaw.weixin_chub_mode.state_file.with_name(
        "weixin-translation.json"
    )
    payload = _read_json(path, max_bytes=2 * 1024 * 1024)
    if payload == []:
        return 0
    state = TranslationState.model_validate(payload)
    return sum(item.status in {"queued", "running"} for item in state.entries)


def _session_count(settings: Settings) -> int:
    payload = _read_json(settings.codex_pty.data_file, max_bytes=2 * 1024 * 1024)
    if not isinstance(payload, list):
        raise ValueError("Session state root is invalid")
    for item in payload:
        CodexSession.model_validate(item)
    return len(payload)


def _workspace_blockers(
    workspaces: dict[str, Path],
    *,
    allow_missing_managed_translation: bool,
) -> list[dict[str, object]]:
    blockers: list[dict[str, object]] = []
    for workspace_id, path in workspaces.items():
        try:
            metadata = path.lstat()
        except FileNotFoundError:
            if allow_missing_managed_translation and workspace_id == "weixin-translation":
                continue
            blockers.append(
                _blocker(
                    "worker_workspace_unavailable",
                    f"固定工作区 {workspace_id} 不存在。",
                )
            )
            continue
        if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
            blockers.append(
                _blocker(
                    "worker_workspace_invalid",
                    f"固定工作区 {workspace_id} 不是可用的真实目录。",
                )
            )
    return blockers


def _worker_task_record_count(settings: Settings) -> int:
    path = worker_state_dir(settings) / "tasks"
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        return 0
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
        raise OSError("Worker task store is not a regular directory")
    if metadata.st_uid != os.getuid() or stat.S_IMODE(metadata.st_mode) != 0o700:
        raise OSError("Worker task store owner or permissions are unsafe")
    entries = list(path.iterdir())
    if len(entries) > MAX_TASK_DIRECTORIES:
        raise OSError("Worker task store exceeds its fixed limit")
    return len(entries)


def retire_worker_store(settings: Settings) -> Path | None:
    """Atomically retain the stopped legacy Worker store outside the active path."""
    from app.quick_worker import worker_socket_path

    socket_path = worker_socket_path(settings)
    if socket_path.exists() or socket_path.is_symlink():
        raise OSError("Quick Worker must be stopped before retiring its task store")

    root = worker_state_dir(settings)
    try:
        metadata = root.lstat()
    except FileNotFoundError:
        return None
    if (
        stat.S_ISLNK(metadata.st_mode)
        or not stat.S_ISDIR(metadata.st_mode)
        or metadata.st_uid != os.getuid()
        or stat.S_IMODE(metadata.st_mode) != 0o700
    ):
        raise OSError("Quick Worker task store owner or permissions are unsafe")

    suffix = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    archive = root.with_name(f"{root.name}-cutover-{suffix}-{uuid.uuid4().hex[:8]}")
    root.rename(archive)
    return archive


async def run_cutover_preflight(
    settings: Settings,
    *,
    require_production_worker: bool = True,
) -> dict[str, object]:
    from app.quick_worker import (
        PROTOCOL_VERSION,
        WORKER_CODE_VERSION,
        production_codex_workspaces,
        read_health,
        worker_request,
        worker_runtime_dir,
        worker_socket_path,
    )

    blockers: list[dict[str, object]] = []
    checks: dict[str, object] = {}
    state_checks = (
        ("active_quick_tasks", _quick_state),
        ("active_translations", _translation_state),
        ("unarchived_sessions", _session_count),
        ("pending_deferred_restart", _deferred_restart_state),
    )
    for name, check in state_checks:
        try:
            value = check(settings)
        except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValidationError, ValueError):
            blockers.append(
                _blocker("cutover_state_invalid", f"{name} 状态无法安全读取。")
            )
            continue
        if name == "active_quick_tasks":
            active, undelivered, pending_delivery = value
            checks[name] = active
            checks["undelivered_worker_results"] = undelivered
            checks["pending_result_delivery"] = pending_delivery
            if active:
                blockers.append(
                    _blocker("active_quick_tasks", "仍有快速交互任务未结束。", count=active)
                )
            if undelivered:
                blockers.append(
                    _blocker(
                        "undelivered_worker_results",
                        "仍有 Worker 结果未完成交付确认。",
                        count=undelivered,
                    )
                )
            if pending_delivery:
                blockers.append(
                    _blocker(
                        "pending_result_delivery",
                        "仍有通知或延迟重启结果未完成交付。",
                        count=pending_delivery,
                    )
                )
            continue
        checks[name] = value
        if value:
            code = name
            message = (
                "仍有翻译任务未结束。"
                if name == "active_translations"
                else (
                    "仍有 Codex Session 未归档。"
                    if name == "unarchived_sessions"
                    else "仍有延迟重启请求尚未完成。"
                )
            )
            blockers.append(_blocker(code, message, count=value))

    workspaces = production_codex_workspaces(settings)
    blockers.extend(
        _workspace_blockers(
            workspaces,
            allow_missing_managed_translation=not require_production_worker,
        )
    )
    checks["workspace_ids"] = sorted(workspaces)

    if not require_production_worker:
        try:
            worker_task_records = _worker_task_record_count(settings)
        except OSError:
            blockers.append(
                _blocker(
                    "worker_task_store_invalid",
                    "旧 Worker 任务存储无法安全读取。",
                )
            )
        else:
            checks["worker_task_records"] = worker_task_records

    try:
        runtime_metadata = worker_runtime_dir(settings).lstat()
        socket_metadata = worker_socket_path(settings).lstat()
        private_runtime = (
            stat.S_ISDIR(runtime_metadata.st_mode)
            and not stat.S_ISLNK(runtime_metadata.st_mode)
            and stat.S_IMODE(runtime_metadata.st_mode) == 0o700
            and runtime_metadata.st_uid == os.getuid()
            and stat.S_ISSOCK(socket_metadata.st_mode)
            and stat.S_IMODE(socket_metadata.st_mode) == 0o600
            and socket_metadata.st_uid == os.getuid()
        )
    except OSError:
        private_runtime = False
    checks["private_runtime"] = private_runtime
    if not private_runtime:
        blockers.append(
            _blocker("worker_runtime_not_private", "Worker 私有目录或 IPC 权限不符合要求。")
        )

    try:
        health_payload = await read_health(settings)
        health = health_payload.get("data") if health_payload.get("success") is True else None
    except (OSError, ValueError):
        health = None
    checks["worker_health"] = health
    if not isinstance(health, dict):
        blockers.append(_blocker("worker_unavailable", "Worker 健康检查不可用。"))
    else:
        expected = (
            health.get("protocol_version") == PROTOCOL_VERSION
            and health.get("code_version") == WORKER_CODE_VERSION
        )
        if require_production_worker and not expected:
            blockers.append(
                _blocker("worker_version_mismatch", "Worker 协议或代码版本尚未切换到正式版本。")
            )
        if health.get("status") != "ready":
            blockers.append(_blocker("worker_not_ready", "Worker 当前不是 ready 状态。"))
        if health.get("active_tasks"):
            blockers.append(
                _blocker(
                    "active_worker_tasks",
                    "Worker 仍有活动任务。",
                    count=int(health["active_tasks"]),
                )
            )
        if health.get("corrupt_tasks"):
            blockers.append(
                _blocker(
                    "corrupt_worker_tasks",
                    "Worker 存在损坏任务记录。",
                    count=int(health["corrupt_tasks"]),
                )
            )
        if health.get("test_tasks_enabled") is not False:
            blockers.append(_blocker("test_tasks_enabled", "正式 Worker 仍启用了测试任务。"))
        if require_production_worker and health.get("codex_tasks_enabled") is not True:
            blockers.append(_blocker("codex_tasks_disabled", "正式 Worker 尚未启用 Codex 任务。"))
        if require_production_worker and set(health.get("codex_workspace_ids") or []) != EXPECTED_WORKSPACE_IDS:
            blockers.append(
                _blocker(
                    "worker_workspace_mapping_mismatch",
                    "正式 Worker 未加载完整的固定工作区映射。",
                )
            )
        worker_protocol = health.get("protocol_version")
        if isinstance(worker_protocol, int):
            try:
                recovery = await worker_request(
                    settings,
                    {
                        "protocol_version": worker_protocol,
                        "request_id": uuid.uuid4().hex,
                        "action": "task_list",
                        "limit": 100,
                        "recovery_only": True,
                    },
                )
                if recovery.get("success") is not True:
                    raise ValueError("Worker recovery query failed")
                recovery_data = recovery.get("data")
                if not isinstance(recovery_data, dict):
                    raise ValueError("Worker recovery data is invalid")
                tasks = recovery_data.get("tasks")
                if not isinstance(tasks, list):
                    raise ValueError("Worker recovery task list is invalid")
                recovery_count = len(
                    [
                        WorkerTaskSummary.model_validate_json(
                            json.dumps(item, ensure_ascii=False)
                        )
                        for item in tasks
                    ]
                )
            except (OSError, TypeError, ValidationError, ValueError):
                recovery_count = -1
            checks["worker_recovery_tasks"] = recovery_count
            if recovery_count != 0:
                blockers.append(
                    _blocker(
                        "worker_recovery_not_empty",
                        "Worker 仍有未交付、未终态或无法验证的任务记录。",
                        count=(recovery_count if recovery_count > 0 else None),
                    )
                )

    return {
        "success": not blockers,
        "data": {
            "ready": not blockers,
            "scope": "final" if require_production_worker else "prepare",
            "checks": checks,
            "blockers": blockers,
        },
    }
