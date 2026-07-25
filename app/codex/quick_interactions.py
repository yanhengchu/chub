from __future__ import annotations

import json
import os
import signal
import subprocess
import threading
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from app.codex.models import CodexSession, QuickInteractionTask, utc_now
from app.core.response import ApiError
from app.services.log_reader import redact_log_line
from app.services.operation_log import write_operation


MAX_RESULT_BYTES = 100_000
MAX_EVENT_BYTES = 1_000_000
TIMEOUT_SECONDS = 10 * 60


class QuickInteractionManager:
    def __init__(self, data_file: Path, codex_manager) -> None:
        self.path = data_file.with_name("codex-quick-interactions.json")
        self.result_dir = self.path.with_suffix("")
        self.codex_manager = codex_manager
        self._lock = threading.RLock()
        self._tasks: dict[str, QuickInteractionTask] = {}
        self._running_sessions: set[str] = set()
        self._session_locks: dict[str, threading.RLock] = {}
        self._processes: dict[str, subprocess.Popen[bytes]] = {}
        self._operations: dict[str, tuple[str, str]] = {}
        recovered = self._load()
        if recovered:
            self._write()
        self.result_dir.mkdir(parents=True, exist_ok=True)
        os.chmod(self.result_dir, 0o700)

    def _load(self) -> bool:
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (FileNotFoundError, OSError, json.JSONDecodeError):
            return False
        if not isinstance(payload, list):
            return False
        recovered = False
        for item in payload:
            try:
                task = QuickInteractionTask.model_validate(item)
            except ValueError:
                continue
            if task.status in {"requested", "running"}:
                task.status = "failed"
                task.error = "服务重启时任务未完成。"
                task.updated_at = utc_now()
                recovered = True
            self._tasks[task.id] = task
        return recovered

    def submit(
        self,
        session_id: str,
        prompt: str,
        *,
        operation_id: str,
        source_ip: str,
    ) -> QuickInteractionTask:
        with self._session_lock(session_id):
            session = self.codex_manager.get_session(session_id)
            if not session.codex_session_id:
                raise ApiError(409, "codex_session_not_started", "Codex session has not started yet")
            if session.status == "running":
                raise ApiError(409, "quick_interaction_terminal_active", "该会话已在实时终端中运行，请先停止终端。")
            if session.permission_mode == "ask":
                raise ApiError(409, "quick_interaction_requires_terminal", "Ask for approval 需要进入实时终端完成审批。")
            with self._lock:
                if session_id in self._running_sessions:
                    raise ApiError(409, "quick_interaction_in_progress", "该会话已有快速交互任务正在执行。")
                task = QuickInteractionTask(
                    id=str(uuid.uuid4()),
                    session_id=session_id,
                    prompt=prompt,
                    status="requested",
                    created_at=utc_now(),
                    updated_at=utc_now(),
                )
                self._tasks[task.id] = task
                self._running_sessions.add(session_id)
                self._operations[task.id] = (operation_id, source_ip)
                self._write()
        self._log_status(task.id, "requested", session.id)
        threading.Thread(target=self._run, args=(task.id, session, prompt), daemon=True).start()
        return task

    def _session_lock(self, session_id: str) -> threading.RLock:
        with self._lock:
            return self._session_locks.setdefault(session_id, threading.RLock())

    @contextmanager
    def terminal_access_guard(self, session_id: str) -> Iterator[None]:
        with self._session_lock(session_id):
            with self._lock:
                if session_id in self._running_sessions:
                    raise ApiError(
                        409,
                        "quick_interaction_in_progress",
                        "该会话正在执行快速交互，请等待任务结束。",
                    )
            yield

    def get(self, task_id: str) -> QuickInteractionTask:
        with self._lock:
            task = self._tasks.get(task_id)
            if task is None:
                raise ApiError(404, "quick_interaction_not_found", "快速交互任务不存在。")
            return task.model_copy(deep=True)

    def list_for_session(self, session_id: str) -> list[QuickInteractionTask]:
        self.codex_manager.get_session(session_id)
        with self._lock:
            tasks = [
                task.model_copy(deep=True)
                for task in self._tasks.values()
                if task.session_id == session_id
            ]
        return sorted(tasks, key=lambda item: item.created_at, reverse=True)

    def _run(self, task_id: str, session: CodexSession, prompt: str) -> None:
        with self._lock:
            task = self._tasks[task_id]
            task.status = "running"
            task.updated_at = utc_now()
            self._write()
        self._log_status(task_id, "started", session.id)
        result_path = self.result_dir / f"{task_id}.txt"
        error_path = self.result_dir / f"{task_id}.err"
        event_path = self.result_dir / f"{task_id}.jsonl"
        try:
            self.result_dir.mkdir(parents=True, exist_ok=True)
            command = self._command(session, result_path)
            env = os.environ.copy()
            env["CHUB_PTY_SESSION_ID"] = session.id
            env["CHUB_PTY_HOOK_DIR"] = str(self.codex_manager.hook_dir)
            with (
                error_path.open("w", encoding="utf-8") as error_file,
                event_path.open("wb") as event_file,
            ):
                process = subprocess.Popen(
                    command,
                    cwd=session.cwd,
                    env=env,
                    stdin=subprocess.PIPE,
                    stdout=event_file,
                    stderr=error_file,
                    start_new_session=True,
                )
                with self._lock:
                    self._processes[task_id] = process
                process.communicate(input=prompt.encode("utf-8"), timeout=TIMEOUT_SECONDS)
            if process.returncode != 0:
                error = self._json_error(event_path) or self._read_tail(error_path, 2000)
                self._finish(task_id, "failed", error or "Codex 执行失败。")
                return
            result = self._read_limited(result_path, MAX_RESULT_BYTES)
            self._finish(task_id, "succeeded", result or "Codex 未返回最终结果。")
        except subprocess.TimeoutExpired:
            self._kill_process(process)
            self._finish(task_id, "timed_out", "Codex 执行超时。")
        except Exception:
            self._finish(task_id, "failed", "快速交互执行失败。")
        finally:
            for path in (result_path, error_path, event_path):
                try:
                    path.unlink()
                except FileNotFoundError:
                    pass
            with self._lock:
                self._processes.pop(task_id, None)
                self._running_sessions.discard(session.id)
            self._log_status(task_id, self.get(task_id).status, session.id)
            with self._lock:
                self._operations.pop(task_id, None)

    @staticmethod
    def _command(session: CodexSession, result_path: Path) -> list[str]:
        permission_args = {
            "ask": ['-c', 'default_permissions=":workspace"', '-c', 'approval_policy="on-request"', '-c', 'approvals_reviewer="user"'],
            "auto-review": ['-c', 'default_permissions=":workspace"', '-c', 'approval_policy="on-request"', '-c', 'approvals_reviewer="auto_review"'],
            "read-only": ['-c', 'default_permissions=":read-only"', '-c', 'approval_policy="on-request"', '-c', 'approvals_reviewer="user"'],
            "full-access": ['-c', 'default_permissions=":danger-full-access"', '-c', 'approval_policy="never"'],
        }[session.permission_mode]
        return [
            "codex", "exec", "--profile", "chub", "--json", *permission_args,
            "--output-last-message", str(result_path),
            "resume", session.codex_session_id or "", "-",
        ]

    @staticmethod
    def _read_limited(path: Path, limit: int) -> str:
        with path.open("rb") as file:
            return file.read(limit).decode("utf-8", errors="replace")

    @staticmethod
    def _read_tail(path: Path, limit: int) -> str:
        with path.open("rb") as file:
            file.seek(0, os.SEEK_END)
            file.seek(max(0, file.tell() - limit))
            return file.read(limit).decode("utf-8", errors="replace")

    @classmethod
    def _json_error(cls, path: Path) -> str:
        try:
            content = cls._read_tail(path, MAX_EVENT_BYTES)
        except OSError:
            return ""
        messages: list[str] = []
        for line in content.splitlines():
            try:
                event = json.loads(line)
            except (json.JSONDecodeError, TypeError):
                continue
            if event.get("type") not in {"error", "turn.failed"}:
                continue
            message = cls._event_message(event)
            if message:
                messages.append(message)
        return (
            redact_log_line(messages[-1], (), max_line_bytes=2000)
            if messages
            else ""
        )

    @classmethod
    def _event_message(cls, value: object) -> str:
        if isinstance(value, str):
            return value
        if isinstance(value, dict):
            for key in ("message", "error", "detail", "reason"):
                message = cls._event_message(value.get(key))
                if message:
                    return message
        return ""

    def _finish(self, task_id: str, status: str, result: str) -> None:
        with self._lock:
            task = self._tasks[task_id]
            task.status = status
            if status == "succeeded":
                task.result = result
            else:
                task.error = result
            task.updated_at = utc_now()
            self._write()

    def _log_status(self, task_id: str, status: str, target: str) -> None:
        with self._lock:
            operation = self._operations.get(task_id)
        if operation is None:
            return
        operation_id, source_ip = operation
        write_operation(
            operation_id=operation_id,
            action="quick_interaction",
            status=status,
            target=target,
            source_ip=source_ip,
        )

    def close(self) -> None:
        with self._lock:
            processes = list(self._processes.values())
        for process in processes:
            self._kill_process(process)

    @staticmethod
    def _kill_process(process: subprocess.Popen[bytes]) -> None:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except OSError:
            try:
                process.kill()
            except OSError:
                return
        try:
            process.wait(timeout=2)
        except (OSError, subprocess.TimeoutExpired):
            pass

    def _write(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if len(self._tasks) > 100:
            retained = sorted(
                self._tasks.values(),
                key=lambda item: item.updated_at,
                reverse=True,
            )[:100]
            self._tasks = {item.id: item for item in retained}
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(
            json.dumps([item.model_dump(mode="json") for item in self._tasks.values()], ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        os.chmod(temporary, 0o600)
        temporary.replace(self.path)
