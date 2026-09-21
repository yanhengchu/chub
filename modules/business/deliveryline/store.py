from __future__ import annotations

import fcntl
import json
import os
import re
import stat
import subprocess
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, ValidationError


MAX_LINE_BYTES = 128 * 1024
MAX_LINES = 500
MAX_ACTIVITY_ITEMS = 200
MAX_FACTS = 30
MAX_QUESTIONS = 30


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class DeliverylineError(RuntimeError):
    pass


class DeliverylineUnavailable(DeliverylineError):
    pass


class DeliverylineNotFound(DeliverylineError):
    pass


class DeliverylineTransitionNotAllowed(DeliverylineError):
    pass


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


LineStatus = Literal["待澄清", "规划中", "推进中", "变更评估中", "已结束"]
SourceRole = Literal["未澄清", "相对完整需求", "持续演进需求", "设计提案", "现状说明", "混合资料"]
ConfirmedSourceRole = Literal["相对完整需求", "持续演进需求", "设计提案", "现状说明", "混合资料"]


class GoalVersion(StrictModel):
    version: int = Field(ge=1)
    title: str = Field(min_length=1, max_length=120)
    source_role: ConfirmedSourceRole
    overall_goal: str = Field(min_length=1, max_length=4000)
    confirmed_facts: list[str] = Field(default_factory=list, max_length=MAX_FACTS)
    scope_boundary: str = Field(default="", max_length=4000)
    open_questions: list[str] = Field(default_factory=list, max_length=MAX_QUESTIONS)
    confirmed_at: datetime


class Activity(StrictModel):
    id: str = Field(min_length=8, max_length=64)
    occurred_at: datetime
    action: Literal["created", "goal_confirmed", "ended"]
    summary: str = Field(min_length=1, max_length=500)


class DeliveryLine(StrictModel):
    version: Literal[2] = 2
    id: str = Field(pattern=r"^DL-\d{8}-[A-Z0-9]{4}$")
    original_request_content: str = Field(min_length=1, max_length=4000)
    status: LineStatus = "待澄清"
    title: str = Field(default="", max_length=120)
    source_role: SourceRole = "未澄清"
    overall_goal: str = Field(default="", max_length=4000)
    confirmed_facts: list[str] = Field(default_factory=list, max_length=MAX_FACTS)
    scope_boundary: str = Field(default="", max_length=4000)
    open_questions: list[str] = Field(default_factory=list, max_length=MAX_QUESTIONS)
    goal_versions: list[GoalVersion] = Field(default_factory=list, max_length=50)
    activity: list[Activity] = Field(default_factory=list, max_length=MAX_ACTIVITY_ITEMS)
    created_at: datetime
    updated_at: datetime

    @property
    def goal_confirmed(self) -> bool:
        return bool(self.goal_versions)


class DeliverylineStore:
    def __init__(self, requirements_dir: Path, state_dir: Path | None = None) -> None:
        self.requirements_dir = requirements_dir
        self.state_dir = state_dir or requirements_dir.parent / ".state"
        self.lock_path = self.state_dir / "requirements.lock"
        self._thread_lock = threading.RLock()

    def list(self, *, include_ended: bool = False, include_archived: bool | None = None) -> list[DeliveryLine]:
        if include_archived is not None:
            include_ended = include_archived
        with self._locked():
            records = [self._read(path) for path in self._paths()]
        if not include_ended:
            records = [record for record in records if record.status != "已结束"]
        return sorted(records, key=lambda record: record.updated_at, reverse=True)

    def get(self, line_id: str) -> DeliveryLine:
        with self._locked():
            return self._read(self._path(line_id))

    def create(self, source: str) -> DeliveryLine:
        content = self._text(source, "原始资料", 4000)
        now = utc_now()
        with self._locked():
            line_id = self._new_id(now)
            record = DeliveryLine(
                id=line_id,
                original_request_content=content,
                activity=[Activity(id=uuid4().hex, occurred_at=now, action="created", summary="已将原始资料作为待澄清交付线入库。")],
                created_at=now,
                updated_at=now,
            )
            self._write(record)
        return record

    def confirm_goal(self, line_id: str, values: dict[str, object]) -> DeliveryLine:
        with self._locked():
            record = self._read(self._path(line_id))
            if record.status != "待澄清":
                raise DeliverylineTransitionNotAllowed("仅待澄清交付线可以确认整体目标；目标变更需进入后续变更评估流程。")
            title = self._text(str(values.get("title", "")), "交付线标题", 120)
            overall_goal = self._text(str(values.get("overall_goal", "")), "整体目标", 4000)
            source_role = values.get("source_role")
            if source_role not in {"相对完整需求", "持续演进需求", "设计提案", "现状说明", "混合资料"}:
                raise ValueError("资料定位无效。")
            facts = self._texts(values.get("confirmed_facts"), "已确认事实", MAX_FACTS)
            questions = self._texts(values.get("open_questions"), "待确认事项", MAX_QUESTIONS)
            scope_boundary = self._text(str(values.get("scope_boundary", "")), "范围与边界", 4000, allow_empty=True)
            now = utc_now()
            goal = GoalVersion(version=len(record.goal_versions) + 1, title=title, source_role=source_role, overall_goal=overall_goal, confirmed_facts=facts, scope_boundary=scope_boundary, open_questions=questions, confirmed_at=now)
            record.title = title
            record.source_role = source_role
            record.overall_goal = overall_goal
            record.confirmed_facts = facts
            record.scope_boundary = scope_boundary
            record.open_questions = questions
            record.goal_versions = [*record.goal_versions, goal]
            record.status = "规划中"
            record.updated_at = now
            self._append(record, now, "goal_confirmed", f"已确认整体目标 V{goal.version}，交付线进入规划中。")
            self._write(record)
        return record

    def end(self, line_id: str) -> DeliveryLine:
        with self._locked():
            record = self._read(self._path(line_id))
            if record.status == "已结束":
                return record
            now = utc_now()
            record.status = "已结束"
            record.updated_at = now
            self._append(record, now, "ended", "交付线已结束。")
            self._write(record)
        return record

    def delete(self, line_id: str) -> None:
        with self._locked():
            path = self._path(line_id)
            self._read(path)
            self._assert_git_write_safe(path)
            try:
                path.unlink()
            except FileNotFoundError as exc:
                raise DeliverylineNotFound("交付线不存在。") from exc
            except OSError as exc:
                raise DeliverylineUnavailable("交付线档案无法删除。") from exc

    def _paths(self) -> list[Path]:
        if not self.requirements_dir.exists():
            return []
        try:
            paths = sorted(self.requirements_dir.glob("DL-*.json"))
        except OSError as exc:
            raise DeliverylineUnavailable("交付线档案目录无法读取。") from exc
        if len(paths) > MAX_LINES:
            raise DeliverylineUnavailable("交付线档案数量超过固定上限。")
        return paths

    def _path(self, line_id: str) -> Path:
        if not re.fullmatch(r"DL-\d{8}-[A-Z0-9]{4}", line_id):
            raise DeliverylineNotFound("交付线不存在。")
        return self.requirements_dir / f"{line_id}.json"

    @contextmanager
    def _locked(self) -> Iterator[None]:
        with self._thread_lock:
            try:
                self.requirements_dir.mkdir(parents=True, exist_ok=True)
                os.chmod(self.requirements_dir, 0o700)
                self.state_dir.mkdir(parents=True, exist_ok=True)
                os.chmod(self.state_dir, 0o700)
                descriptor = os.open(self.lock_path, os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0), 0o600)
                os.chmod(self.lock_path, 0o600)
            except OSError as exc:
                raise DeliverylineUnavailable("交付线档案锁不可用。") from exc
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX)
                yield
            finally:
                fcntl.flock(descriptor, fcntl.LOCK_UN)
                os.close(descriptor)

    def _read(self, path: Path) -> DeliveryLine:
        try:
            metadata = path.lstat()
        except FileNotFoundError as exc:
            raise DeliverylineNotFound("交付线不存在。") from exc
        except OSError as exc:
            raise DeliverylineUnavailable("交付线档案无法读取。") from exc
        if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode) or metadata.st_size > MAX_LINE_BYTES:
            raise DeliverylineUnavailable("交付线档案格式或大小无效。")
        try:
            descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
            with os.fdopen(descriptor, encoding="utf-8") as file:
                content = file.read(MAX_LINE_BYTES + 1)
            if len(content.encode("utf-8")) > MAX_LINE_BYTES:
                raise DeliverylineUnavailable("交付线档案格式或大小无效。")
            return DeliveryLine.model_validate(json.loads(content))
        except (OSError, UnicodeError, ValueError, ValidationError) as exc:
            raise DeliverylineUnavailable("交付线档案格式无效。") from exc

    def _write(self, record: DeliveryLine) -> None:
        content = (json.dumps(record.model_dump(mode="json"), ensure_ascii=False, separators=(",", ":")) + "\n").encode()
        if len(content) > MAX_LINE_BYTES:
            raise DeliverylineUnavailable("交付线档案超过固定大小上限。")
        path = self._path(record.id)
        self._assert_git_write_safe(path)
        temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
        try:
            with open(temporary, "xb") as file:
                os.chmod(temporary, 0o600)
                file.write(content)
                file.flush()
                os.fsync(file.fileno())
            os.replace(temporary, path)
            os.chmod(path, 0o600)
        except OSError as exc:
            raise DeliverylineUnavailable("交付线档案无法保存。") from exc
        finally:
            temporary.unlink(missing_ok=True)

    @staticmethod
    def _git_repository_root(path: Path) -> Path | None:
        try:
            result = subprocess.run(["git", "-C", str(path), "rev-parse", "--show-toplevel"], capture_output=True, check=False, timeout=2)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise DeliverylineUnavailable("无法确认交付线档案同步状态。") from exc
        if result.returncode == 0:
            try:
                return Path(result.stdout.decode("utf-8").strip()).resolve()
            except (UnicodeError, OSError) as exc:
                raise DeliverylineUnavailable("无法确认交付线档案同步状态。") from exc
        if result.returncode == 128 and b"not a git repository" in result.stderr.lower():
            return None
        raise DeliverylineUnavailable("无法确认交付线档案同步状态。")

    def _assert_git_write_safe(self, path: Path) -> None:
        repository = self._git_repository_root(path.parent)
        if repository is None:
            return
        try:
            relative_path = path.resolve().relative_to(repository).as_posix()
            result = subprocess.run(["git", "-C", str(repository), "ls-files", "-u", "--", relative_path], capture_output=True, check=False, timeout=2)
        except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
            raise DeliverylineUnavailable("无法确认交付线档案同步状态。") from exc
        if result.returncode != 0:
            raise DeliverylineUnavailable("无法确认交付线档案同步状态。")
        if result.stdout.strip():
            raise DeliverylineUnavailable("交付线档案存在未解决的 Git 冲突，请先处理后再保存。")

    @staticmethod
    def _append(record: DeliveryLine, now: datetime, action: Literal["created", "goal_confirmed", "ended"], summary: str) -> None:
        record.activity = [*record.activity, Activity(id=uuid4().hex, occurred_at=now, action=action, summary=summary)][-MAX_ACTIVITY_ITEMS:]

    def _new_id(self, now: datetime) -> str:
        for _ in range(100):
            identifier = f"DL-{now:%Y%m%d}-{uuid4().hex[:4].upper()}"
            if not (self.requirements_dir / f"{identifier}.json").exists():
                return identifier
        raise DeliverylineUnavailable("无法生成交付线编号。")

    @staticmethod
    def _text(value: str, label: str, maximum: int, *, allow_empty: bool = False) -> str:
        normalized = value.strip()
        if not normalized and not allow_empty:
            raise ValueError(f"{label}不能为空。")
        if len(normalized) > maximum:
            raise ValueError(f"{label}超过长度上限。")
        return normalized

    @staticmethod
    def _texts(value: object, label: str, maximum: int) -> list[str]:
        if not isinstance(value, list):
            raise ValueError(f"{label}格式无效。")
        if len(value) > maximum:
            raise ValueError(f"{label}数量超过上限。")
        result = []
        for item in value:
            if not isinstance(item, str):
                raise ValueError(f"{label}格式无效。")
            normalized = item.strip()
            if normalized:
                if len(normalized) > 1000:
                    raise ValueError(f"{label}单项超过长度上限。")
                result.append(normalized)
        return result
