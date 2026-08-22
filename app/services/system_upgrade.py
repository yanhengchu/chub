from __future__ import annotations

import hashlib
import json
import logging
import os
import shutil
import stat
import threading
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Callable, Iterator, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.codex.models import utc_now
from app.core.build_info import (
    SESSION_SCHEMA_VERSION,
    SYSTEM_UPGRADE_CONTRACT_VERSION,
    WEB_CODE_VERSION,
)
from app.core.response import ApiError
from app.quick_worker import PROTOCOL_VERSION
from app.services.operation_log import write_operation


LOGGER = logging.getLogger("hub.system_upgrade")
MAX_PLAN_BYTES = 32 * 1024
MAX_STATE_BYTES = 256 * 1024


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SystemUpgradePlan(_StrictModel):
    version: Literal[1] = 1
    contract_version: Literal[1] = SYSTEM_UPGRADE_CONTRACT_VERSION
    plan_id: str = Field(pattern=r"^[a-z][a-z0-9-]{2,63}$")
    action: Literal["runtime-data-reset"]
    title: str = Field(min_length=1, max_length=80)
    summary: str = Field(min_length=1, max_length=300)
    source_code_version: str = Field(min_length=1, max_length=80)
    target_code_version: str = Field(min_length=1, max_length=80)
    source_session_schema: int = Field(ge=1, le=1000)
    target_session_schema: int = Field(ge=1, le=1000)
    source_worker_protocol: int = Field(ge=1, le=1000)
    target_worker_protocol: int = Field(ge=1, le=1000)
    effects: list[str] = Field(min_length=1, max_length=8)
    preserves: list[str] = Field(min_length=1, max_length=8)


class LoadedSystemUpgradePlan(_StrictModel):
    plan: SystemUpgradePlan
    fingerprint: str = Field(min_length=64, max_length=64)


class SystemUpgradeSession(_StrictModel):
    session_id: str = Field(min_length=1, max_length=128)
    native_session_id: str | None = Field(default=None, max_length=128)
    status: Literal["pending", "archived", "discarded"] = "pending"


UpgradeStatus = Literal["requested", "started", "succeeded", "failed"]
UpgradeStage = Literal[
    "waiting_for_writes",
    "draining_worker",
    "freezing_sessions",
    "archiving_sessions",
    "cleaning_state",
    "launching_services",
    "restarting_services",
    "verifying_new_instance",
    "completed",
    "failed",
]
RestartLaunchState = Literal["not_started", "launching", "launched", "failed"]


class SystemUpgradeOperation(_StrictModel):
    version: Literal[1] = 1
    operation_id: str = Field(min_length=32, max_length=32)
    plan: SystemUpgradePlan
    fingerprint: str = Field(min_length=64, max_length=64)
    status: UpgradeStatus
    stage: UpgradeStage
    source_ip: str = Field(min_length=1, max_length=128)
    old_instance_id: str = Field(min_length=1, max_length=128)
    old_worker_generation: str | None = Field(default=None, min_length=1, max_length=128)
    old_worker_protocol: int | None = Field(default=None, ge=1, le=1000)
    sessions: list[SystemUpgradeSession] = Field(default_factory=list)
    archived_sessions: int = Field(default=0, ge=0)
    discarded_sessions: int = Field(default=0, ge=0)
    worker_drain_started: bool = False
    destructive_started: bool = False
    restart_launch_state: RestartLaunchState = "not_started"
    restart_process_id: int | None = Field(default=None, ge=1)
    failed_stage: UpgradeStage | None = None
    message: str = Field(default="", max_length=500)
    requested_at: datetime
    updated_at: datetime


class SystemUpgradePlanView(_StrictModel):
    plan_id: str
    title: str
    summary: str
    target_code_version: str
    target_session_schema: int
    source_worker_protocol: int
    target_worker_protocol: int
    fingerprint: str
    session_count: int = Field(ge=0)
    session_labels: list[str] = Field(default_factory=list, max_length=100)
    effects: list[str]
    preserves: list[str]


class SystemUpgradeOperationView(_StrictModel):
    operation_id: str
    status: UpgradeStatus
    stage: UpgradeStage
    message: str
    archived_sessions: int = Field(ge=0)
    discarded_sessions: int = Field(ge=0)
    total_sessions: int = Field(ge=0)
    updated_at: datetime


class SystemUpgradeStatusData(_StrictModel):
    state: Literal[
        "idle",
        "available",
        "blocked",
        "preparing",
        "draining",
        "archiving",
        "cleaning",
        "restarting",
        "succeeded",
        "failed",
    ]
    message: str = Field(min_length=1, max_length=500)
    can_start: bool = False
    resume: bool = False
    writes_blocked: bool = False
    plan: SystemUpgradePlanView | None = None
    operation: SystemUpgradeOperationView | None = None


class SystemUpgradeBusy(RuntimeError):
    pass


def load_system_upgrade_plan(path: Path) -> LoadedSystemUpgradePlan | None:
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise OSError("系统升级方案无法读取。") from exc
    if (
        not stat.S_ISREG(metadata.st_mode)
        or stat.S_ISLNK(metadata.st_mode)
        or metadata.st_uid != os.getuid()
        or stat.S_IMODE(metadata.st_mode) & 0o022
        or metadata.st_size > MAX_PLAN_BYTES
    ):
        raise OSError("系统升级方案的类型、所有者、权限或大小不安全。")
    try:
        content = path.read_bytes()
        plan = SystemUpgradePlan.model_validate_json(content)
    except (OSError, ValueError, ValidationError) as exc:
        raise OSError("系统升级方案格式无效。") from exc
    if plan.source_code_version != WEB_CODE_VERSION:
        raise OSError("系统升级方案与当前 Chub 代码版本不匹配。")
    if plan.source_session_schema != SESSION_SCHEMA_VERSION:
        raise OSError("系统升级方案与当前 Session 数据版本不匹配。")
    return LoadedSystemUpgradePlan(
        plan=plan,
        fingerprint=hashlib.sha256(content).hexdigest(),
    )


def runtime_recovery_plan() -> LoadedSystemUpgradePlan:
    """Build the fixed current-version plan used when no upgrade is pending."""
    plan = SystemUpgradePlan(
        plan_id="runtime-recovery",
        action="runtime-data-reset",
        title="系统升级与恢复",
        summary="重建 Chub AI 运行态并确认 Web 与 Quick Worker 健康。",
        source_code_version=WEB_CODE_VERSION,
        target_code_version=WEB_CODE_VERSION,
        source_session_schema=SESSION_SCHEMA_VERSION,
        target_session_schema=SESSION_SCHEMA_VERSION,
        source_worker_protocol=PROTOCOL_VERSION,
        target_worker_protocol=PROTOCOL_VERSION,
        effects=["在途快速任务将停止，Chub Session 关联和 Worker 运行态将清理"],
        preserves=["Codex 原生 Session、配置、日志、资料和业务数据继续保留"],
    )
    content = plan.model_dump_json().encode("utf-8")
    return LoadedSystemUpgradePlan(
        plan=plan,
        fingerprint=hashlib.sha256(content).hexdigest(),
    )


def runtime_cleanup_readiness(settings) -> str | None:
    """Validate the fixed local paths before accepting a destructive reset."""
    files = (
        settings.codex_pty.data_file,
        settings.codex_pty.data_file.with_name("ai-sessions.json"),
    )
    directories = (
        settings.codex_pty.runtime_dir / "hooks",
        settings.codex_pty.runtime_dir / "restart-requests",
    )
    for path in files:
        try:
            metadata = path.lstat()
        except FileNotFoundError:
            continue
        except OSError:
            return "Chub Session 运行态文件无法安全检查。"
        if (
            not stat.S_ISREG(metadata.st_mode)
            or stat.S_ISLNK(metadata.st_mode)
            or metadata.st_uid != os.getuid()
            or stat.S_IMODE(metadata.st_mode) & 0o077
        ):
            return "Chub Session 运行态文件的类型、所有者或权限不安全。"
    for path in directories:
        try:
            metadata = path.lstat()
        except FileNotFoundError:
            continue
        except OSError:
            return "Chub Session 运行态目录无法安全检查。"
        if (
            not stat.S_ISDIR(metadata.st_mode)
            or metadata.st_uid != os.getuid()
            or stat.S_IMODE(metadata.st_mode) & 0o077
        ):
            return "Chub Session 运行态目录的类型、所有者或权限不安全。"
    return None


def system_upgrade_restart_readiness(
    project_root: Path,
    detected_platform: str,
    *,
    environment: dict[str, str] | None = None,
) -> str | None:
    environment = os.environ if environment is None else environment
    command = project_root / "scripts" / "chub-system-upgrade-restart"
    try:
        metadata = command.lstat()
    except OSError:
        return "系统升级服务切换脚本不可用。"
    if (
        not stat.S_ISREG(metadata.st_mode)
        or stat.S_ISLNK(metadata.st_mode)
        or metadata.st_uid != os.getuid()
        or stat.S_IMODE(metadata.st_mode) & 0o022
        or not os.access(command, os.X_OK)
    ):
        return "系统升级服务切换脚本的类型、所有者或权限不安全。"
    python = project_root / ".venv" / "bin" / "python"
    if not python.is_file() or not os.access(python, os.X_OK):
        return "系统升级使用的 Python 运行环境不可用。"

    if detected_platform == "ubuntu":
        if shutil.which("systemctl") is None:
            return "当前系统缺少 systemctl，不能执行服务切换。"
        config_home = environment.get(
            "XDG_CONFIG_HOME",
            str(Path.home() / ".config"),
        )
        systemd_root = Path(
            environment.get(
                "CHUB_SYSTEMD_USER_DIR",
                str(Path(config_home) / "systemd" / "user"),
            )
        )
        definitions = (
            systemd_root / "chub.service",
            systemd_root / "chub-quick-worker.service",
        )
    elif detected_platform == "macos":
        if shutil.which("launchctl") is None:
            return "当前系统缺少 launchctl，不能执行服务切换。"
        launch_agents = Path(
            environment.get(
                "CHUB_LAUNCH_AGENTS_DIR",
                str(Path.home() / "Library" / "LaunchAgents"),
            )
        )
        definitions = (
            launch_agents / "com.chub.node.plist",
            launch_agents / "com.chub.quick-worker.plist",
        )
    else:
        return "当前平台不支持从页面执行系统升级。"

    for definition in definitions:
        try:
            metadata = definition.lstat()
        except OSError:
            return "Chub Web 或 Quick Worker 的服务定义尚未安装。"
        if (
            not stat.S_ISREG(metadata.st_mode)
            or stat.S_ISLNK(metadata.st_mode)
            or metadata.st_uid != os.getuid()
            or stat.S_IMODE(metadata.st_mode) & 0o022
        ):
            return "Chub Web 或 Quick Worker 的服务定义不安全。"
    return None


class SystemUpgradeCoordinator:
    """Persist one fixed upgrade and keep writes closed across Web restarts."""

    def __init__(self, state_file: Path, plan_file: Path, instance_id: str) -> None:
        self.path = state_file
        self.plan_path = plan_file
        self.instance_id = instance_id
        self._lock = threading.RLock()
        self._write_condition = threading.Condition(self._lock)
        self._active_writes = 0
        self._state_error = False
        self._state = self._load()
        self._writes_blocked = self._state_error or bool(
            self._state is not None
            and (
                self._state.status in {"requested", "started"}
                or (
                    self._state.status == "failed"
                    and self._state.destructive_started
                )
            )
        )
        self._runner: threading.Thread | None = None

    def plan(self) -> LoadedSystemUpgradePlan | None:
        return load_system_upgrade_plan(self.plan_path)

    def operation(self) -> SystemUpgradeOperation | None:
        with self._lock:
            return self._state.model_copy(deep=True) if self._state else None

    def in_progress(self) -> bool:
        with self._lock:
            return self._state is not None and self._state.status in {
                "requested",
                "started",
            }

    def writes_blocked(self) -> bool:
        with self._lock:
            return self._writes_blocked

    @contextmanager
    def mutation_guard(self) -> Iterator[None]:
        with self._lock:
            if self._writes_blocked:
                raise SystemUpgradeBusy("系统升级期间暂不接受新的写入操作。")
            self._active_writes += 1
        try:
            yield
        finally:
            with self._lock:
                self._active_writes -= 1
                self._write_condition.notify_all()

    def wait_for_writes(self, timeout_seconds: float) -> bool:
        deadline = __import__("time").monotonic() + timeout_seconds
        with self._lock:
            while self._active_writes:
                remaining = deadline - __import__("time").monotonic()
                if remaining <= 0:
                    return False
                self._write_condition.wait(remaining)
            return True

    def begin(
        self,
        loaded: LoadedSystemUpgradePlan,
        *,
        source_ip: str,
        old_worker_generation: str | None,
        runner: Callable[[str], None],
        old_worker_protocol: int | None = None,
    ) -> SystemUpgradeOperation:
        with self._lock:
            if self._state_error:
                raise ApiError(
                    503,
                    "system_upgrade_state_unavailable",
                    "系统升级状态不可用，本次未执行升级。",
                )
            if self._state is not None and self._state.status in {
                "requested",
                "started",
            }:
                return self._state.model_copy(deep=True)
            if (
                self._state is not None
                and self._state.status == "failed"
                and self._state.destructive_started
            ):
                raise ApiError(
                    409,
                    "system_upgrade_recovery_required",
                    "上次升级已清理运行状态但未通过最终验证，写入仍保持关闭。",
                )
            now = utc_now()
            state = SystemUpgradeOperation(
                operation_id=uuid4().hex,
                plan=loaded.plan,
                fingerprint=loaded.fingerprint,
                status="requested",
                stage="waiting_for_writes",
                source_ip=source_ip,
                old_instance_id=self.instance_id,
                old_worker_generation=old_worker_generation,
                old_worker_protocol=old_worker_protocol,
                message="正在关闭新的写入并等待已有操作结束。",
                requested_at=now,
                updated_at=now,
            )
            self._writes_blocked = True
            try:
                self._write(state)
            except OSError:
                self._writes_blocked = False
                self._state_error = True
                raise ApiError(
                    503,
                    "system_upgrade_state_unavailable",
                    "系统升级状态无法保存，本次未执行升级。",
                ) from None
            self._state = state
        self._record(state, "requested")
        self._start_runner(state.operation_id, runner)
        return state.model_copy(deep=True)

    def resume(self, runner: Callable[[str], None]) -> bool:
        with self._lock:
            if self._state is None or self._state.status not in {"requested", "started"}:
                return False
            operation_id = self._state.operation_id
            if self._state.stage in {"restarting_services", "verifying_new_instance"}:
                return False
        self._start_runner(operation_id, runner)
        return True

    def resume_verification(self) -> SystemUpgradeOperation | None:
        with self._lock:
            state = self._state
            can_verify = bool(
                state is not None
                and (
                    state.failed_stage == "verifying_new_instance"
                    or (
                        state.failed_stage == "restarting_services"
                        and state.restart_launch_state == "launched"
                    )
                )
            )
            if (
                state is None
                or state.status != "failed"
                or not state.destructive_started
                or not can_verify
            ):
                return None
            state = state.model_copy(deep=True)
            state.status = "started"
            state.stage = "verifying_new_instance"
            state.failed_stage = None
            state.message = "正在重新确认新服务的最终状态。"
            state.updated_at = utc_now()
            self._write(state)
            self._state = state
        self._record(state, "started")
        return state.model_copy(deep=True)

    def rebase_failed_verification(
        self,
        loaded: LoadedSystemUpgradePlan,
    ) -> bool:
        """Bind a post-cleanup verification to the running version only."""
        return self._rebase_failed_recovery(
            loaded,
            allowed_stages={"verifying_new_instance"},
            message="已按当前 Chub 版本更新恢复目标，正在重新确认新服务状态。",
        )

    def rebase_failed_recovery(
        self,
        loaded: LoadedSystemUpgradePlan,
    ) -> bool:
        """Bind a failed destructive recovery to the fixed current plan."""
        return self._rebase_failed_recovery(
            loaded,
            allowed_stages={
                "cleaning_state",
                "launching_services",
                "restarting_services",
                "verifying_new_instance",
            },
            message="已按当前 Chub 版本更新恢复目标，正在继续失败的恢复操作。",
        )

    def _rebase_failed_recovery(
        self,
        loaded: LoadedSystemUpgradePlan,
        *,
        allowed_stages: set[str],
        message: str,
    ) -> bool:
        if not _is_current_runtime_recovery_plan(loaded):
            return False
        with self._lock:
            state = self._state
            if (
                state is None
                or state.status != "failed"
                or not state.destructive_started
                or state.failed_stage not in allowed_stages
                or (
                    state.failed_stage == "verifying_new_instance"
                    and state.restart_launch_state != "launched"
                )
            ):
                return False
            previous_fingerprint = state.fingerprint
            state = state.model_copy(deep=True)
            state.plan = loaded.plan
            state.fingerprint = loaded.fingerprint
            state.message = message
            state.updated_at = utc_now()
            self._write(state)
            self._state = state
        LOGGER.warning(
            "Rebound failed system upgrade recovery to current runtime "
            "operation_id=%s previous_fingerprint=%s current_fingerprint=%s",
            state.operation_id,
            previous_fingerprint,
            loaded.fingerprint,
        )
        return True

    def resume_failed(
        self,
        runner: Callable[[str], None],
    ) -> SystemUpgradeOperation | None:
        """Resume a failed destructive upgrade from its durable checkpoint."""
        with self._lock:
            state = self._state
            if (
                state is None
                or state.status != "failed"
                or not state.destructive_started
            ):
                return None
            state = state.model_copy(deep=True)
            if state.failed_stage == "cleaning_state":
                state.stage = "draining_worker"
                state.message = "正在重新排空 Quick Worker，并继续未完成的运行状态清理。"
            elif state.failed_stage in {
                "launching_services",
                "restarting_services",
            }:
                state.stage = "launching_services"
                state.restart_launch_state = "not_started"
                state.restart_process_id = None
                state.message = "正在重新启动固定服务切换程序。"
            elif state.failed_stage == "verifying_new_instance":
                state.stage = "verifying_new_instance"
                state.message = "正在重新确认新服务的最终状态。"
            else:
                return None
            state.status = "started"
            state.failed_stage = None
            state.updated_at = utc_now()
            self._write(state)
            self._state = state
        self._record(state, "started")
        self._start_runner(state.operation_id, runner)
        return state.model_copy(deep=True)

    def _start_runner(self, operation_id: str, runner: Callable[[str], None]) -> None:
        with self._lock:
            if self._runner is not None and self._runner.is_alive():
                return
            thread = threading.Thread(
                target=runner,
                args=(operation_id,),
                daemon=True,
                name=f"chub-system-upgrade-{operation_id[:8]}",
            )
            self._runner = thread
        try:
            thread.start()
        except RuntimeError as exc:
            self.fail(operation_id, "系统升级执行线程未能启动。")
            raise ApiError(503, "system_upgrade_start_failed", str(exc)) from exc

    def update(
        self,
        operation_id: str,
        *,
        stage: UpgradeStage | None = None,
        message: str | None = None,
        status: UpgradeStatus | None = None,
        worker_drain_started: bool | None = None,
        destructive_started: bool | None = None,
        restart_launch_state: RestartLaunchState | None = None,
        restart_process_id: int | None = None,
        sessions: list[SystemUpgradeSession] | None = None,
    ) -> SystemUpgradeOperation:
        with self._lock:
            state = self._require(operation_id)
            if stage is not None:
                state.stage = stage
            if message is not None:
                state.message = message[:500]
            if status is not None:
                state.status = status
            if worker_drain_started is not None:
                state.worker_drain_started = worker_drain_started
            if destructive_started is not None:
                state.destructive_started = destructive_started
            if restart_launch_state is not None:
                state.restart_launch_state = restart_launch_state
            if restart_process_id is not None:
                state.restart_process_id = restart_process_id
            if sessions is not None:
                state.sessions = [item.model_copy(deep=True) for item in sessions]
                state.archived_sessions = sum(item.status == "archived" for item in sessions)
                state.discarded_sessions = sum(item.status == "discarded" for item in sessions)
            state.updated_at = utc_now()
            self._write(state)
            self._state = state
            return state.model_copy(deep=True)

    def fail(
        self,
        operation_id: str,
        message: str,
        *,
        restart_launch_failed: bool = False,
    ) -> None:
        with self._lock:
            state = self._require(operation_id)
            if state.status in {"succeeded", "failed"}:
                return
            state.status = "failed"
            state.failed_stage = state.stage
            state.stage = "failed"
            if restart_launch_failed:
                state.restart_launch_state = "failed"
            state.message = message[:500]
            state.updated_at = utc_now()
            self._write(state)
            self._state = state
            if not state.destructive_started:
                self._writes_blocked = False
        self._record(state, "failed")

    def succeed(self, operation_id: str) -> None:
        with self._lock:
            state = self._require(operation_id)
            state.status = "succeeded"
            state.stage = "completed"
            state.message = "系统升级与恢复已完成，服务和运行态均已确认。"
            state.updated_at = utc_now()
            self._write(state)
            self._state = state
            self._writes_blocked = False
        self._record(state, "succeeded")

    def mark_started(self, operation_id: str) -> None:
        with self._lock:
            state = self._require(operation_id)
            if state.status == "requested":
                state.status = "started"
                state.updated_at = utc_now()
                self._write(state)
                self._state = state
            else:
                return
        self._record(state, "started")

    def status_data(
        self,
        loaded: LoadedSystemUpgradePlan | None,
        *,
        session_count: int,
        session_labels: list[str] | None = None,
        plan_error: str | None = None,
    ) -> SystemUpgradeStatusData:
        with self._lock:
            operation = self._state.model_copy(deep=True) if self._state else None
            writes_blocked = self._writes_blocked
            state_error = self._state_error
        operation_view = None
        if operation is not None:
            operation_view = SystemUpgradeOperationView(
                operation_id=operation.operation_id,
                status=operation.status,
                stage=operation.stage,
                message=operation.message,
                archived_sessions=operation.archived_sessions,
                discarded_sessions=operation.discarded_sessions,
                total_sessions=len(operation.sessions),
                updated_at=operation.updated_at,
            )
            if operation.status in {"requested", "started"}:
                presentation = {
                    "waiting_for_writes": "preparing",
                    "draining_worker": "draining",
                    "freezing_sessions": "preparing",
                    "archiving_sessions": "cleaning",
                    "cleaning_state": "cleaning",
                    "launching_services": "restarting",
                    "restarting_services": "restarting",
                    "verifying_new_instance": "restarting",
                }.get(operation.stage, "preparing")
                return SystemUpgradeStatusData(
                    state=presentation,
                    message=operation.message,
                    writes_blocked=True,
                    operation=operation_view,
                )
            if operation.status == "failed":
                effective_plan = loaded or runtime_recovery_plan()
                retryable = bool(
                    (
                        effective_plan.fingerprint == operation.fingerprint
                        or not operation.destructive_started
                        or self._can_rebase_failed_recovery(
                            operation,
                            effective_plan,
                        )
                    )
                    and (
                        not operation.destructive_started
                        or operation.failed_stage
                        in {
                            "cleaning_state",
                            "launching_services",
                            "restarting_services",
                            "verifying_new_instance",
                        }
                    )
                )
                return SystemUpgradeStatusData(
                    state="failed",
                    message=operation.message,
                    can_start=retryable,
                    resume=retryable and operation.destructive_started,
                    writes_blocked=writes_blocked,
                    plan=(
                        self._plan_view(effective_plan, session_count, session_labels)
                        if retryable
                        else None
                    ),
                    operation=operation_view,
                )
            # A completed operation is history, not a permanent maintenance lock.
            # Fall through to the current plan so runtime recovery stays available.
        if state_error or plan_error:
            return SystemUpgradeStatusData(
                state="blocked",
                message=plan_error or "系统升级状态不可用，升级入口已关闭。",
                writes_blocked=writes_blocked,
            )
        if loaded is None:
            loaded = runtime_recovery_plan()
        return SystemUpgradeStatusData(
            state="available",
            message=loaded.plan.summary,
            can_start=True,
            plan=self._plan_view(loaded, session_count, session_labels),
            operation=operation_view,
        )

    @staticmethod
    def _can_rebase_failed_verification(
        operation: SystemUpgradeOperation,
        loaded: LoadedSystemUpgradePlan,
    ) -> bool:
        return bool(
            operation.destructive_started
            and operation.failed_stage == "verifying_new_instance"
            and operation.restart_launch_state == "launched"
            and _is_current_runtime_recovery_plan(loaded)
        )

    @staticmethod
    def _can_rebase_failed_recovery(
        operation: SystemUpgradeOperation,
        loaded: LoadedSystemUpgradePlan,
    ) -> bool:
        return bool(
            operation.destructive_started
            and operation.failed_stage
            in {
                "cleaning_state",
                "launching_services",
                "restarting_services",
                "verifying_new_instance",
            }
            and (
                operation.failed_stage != "verifying_new_instance"
                or operation.restart_launch_state == "launched"
            )
            and _is_current_runtime_recovery_plan(loaded)
        )

    @staticmethod
    def _plan_view(
        loaded: LoadedSystemUpgradePlan,
        session_count: int,
        session_labels: list[str] | None = None,
    ) -> SystemUpgradePlanView:
        plan = loaded.plan
        return SystemUpgradePlanView(
            plan_id=plan.plan_id,
            title=plan.title,
            summary=plan.summary,
            target_code_version=plan.target_code_version,
            target_session_schema=plan.target_session_schema,
            source_worker_protocol=plan.source_worker_protocol,
            target_worker_protocol=plan.target_worker_protocol,
            fingerprint=loaded.fingerprint,
            session_count=session_count,
            session_labels=list(session_labels or []),
            effects=list(plan.effects),
            preserves=list(plan.preserves),
        )

    def _require(self, operation_id: str) -> SystemUpgradeOperation:
        if self._state is None or self._state.operation_id != operation_id:
            raise OSError("系统升级操作标识不匹配。")
        return self._state.model_copy(deep=True)

    def _load(self) -> SystemUpgradeOperation | None:
        try:
            metadata = self.path.lstat()
            if (
                not stat.S_ISREG(metadata.st_mode)
                or stat.S_ISLNK(metadata.st_mode)
                or metadata.st_uid != os.getuid()
                or stat.S_IMODE(metadata.st_mode) & 0o077
                or metadata.st_size > MAX_STATE_BYTES
            ):
                raise OSError("unsafe state")
            content = self.path.read_bytes()
            return SystemUpgradeOperation.model_validate_json(content)
        except FileNotFoundError:
            return None
        except (OSError, ValueError, ValidationError):
            self._state_error = True
            LOGGER.warning("System upgrade state is unavailable", exc_info=True)
            return None

    def _write(self, state: SystemUpgradeOperation) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        os.chmod(self.path.parent, 0o700)
        if self.path.is_symlink():
            raise OSError("System upgrade state must not be a symlink")
        content = state.model_dump_json().encode("utf-8") + b"\n"
        if len(content) > MAX_STATE_BYTES:
            raise OSError("System upgrade state exceeds its fixed limit")
        temporary = self.path.with_name(f".{self.path.name}.{uuid4().hex}.tmp")
        try:
            descriptor = os.open(
                temporary,
                os.O_CREAT | os.O_EXCL | os.O_WRONLY | getattr(os, "O_NOFOLLOW", 0),
                0o600,
            )
            with os.fdopen(descriptor, "wb") as state_file:
                state_file.write(content)
                state_file.flush()
                os.fsync(state_file.fileno())
            os.replace(temporary, self.path)
            os.chmod(self.path, 0o600)
        finally:
            temporary.unlink(missing_ok=True)

    @staticmethod
    def _record(state: SystemUpgradeOperation, status: UpgradeStatus) -> None:
        try:
            write_operation(
                operation_id=state.operation_id,
                action="system_upgrade",
                status=status,
                target=state.plan.plan_id,
                source_ip=state.source_ip,
            )
        except Exception:
            LOGGER.warning("Unable to record system upgrade operation", exc_info=True)


def _is_current_runtime_recovery_plan(loaded: LoadedSystemUpgradePlan) -> bool:
    plan = loaded.plan
    return bool(
        plan.plan_id == "runtime-recovery"
        and plan.action == "runtime-data-reset"
        and plan.target_code_version == WEB_CODE_VERSION
        and plan.target_session_schema == SESSION_SCHEMA_VERSION
        and plan.target_worker_protocol == PROTOCOL_VERSION
    )
