from __future__ import annotations

import fcntl
import json
import os
import re
import stat
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, ValidationError


MAX_REQUIREMENT_BYTES = 128 * 1024
MAX_REQUIREMENTS = 500
MAX_ACTIVITY_ITEMS = 200


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class DeliverylineError(RuntimeError):
    pass


class DeliverylineUnavailable(DeliverylineError):
    pass


class DeliverylineNotFound(DeliverylineError):
    pass


class DeliverylineReviewNotReady(DeliverylineError):
    def __init__(self, fields: list[str]) -> None:
        self.fields = fields
        super().__init__("进入需求评审前仍需补充：" + "、".join(fields))


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


DeliveryStatus = Literal["进行中", "待我处理", "存在风险", "已交付", "已归档"]


class Workflow(StrictModel):
    current_stage: Literal["需求提出", "需求评审", "方案设计", "开发实现", "自动化测试", "测试验收"] = "需求提出"
    delivery_status: DeliveryStatus = "待我处理"
    next_action: str = Field(min_length=1, max_length=240)
    latest_stage_conclusion: str = Field(default="", max_length=1000)


class Activity(StrictModel):
    id: str = Field(min_length=8, max_length=64)
    occurred_at: datetime
    action: Literal["created", "updated", "submitted_for_review", "archived"]
    summary: str = Field(min_length=1, max_length=500)


class Requirement(StrictModel):
    version: Literal[1] = 1
    id: str = Field(pattern=r"^DL-\d{8}-[A-Z0-9]{4}$")
    title: str = Field(min_length=1, max_length=120)
    background: str = Field(default="", max_length=4000)
    delivery_goal: str = Field(default="", max_length=4000)
    scope: str = Field(default="", max_length=4000)
    out_of_scope: str = Field(default="", max_length=4000)
    constraints: str = Field(default="", max_length=4000)
    acceptance_criteria: str = Field(default="", max_length=4000)
    risks_and_open_items: str = Field(default="", max_length=4000)
    workflow: Workflow
    activity: list[Activity] = Field(default_factory=list, max_length=MAX_ACTIVITY_ITEMS)
    created_at: datetime
    updated_at: datetime


EDITABLE_FIELDS = (
    "title", "background", "delivery_goal", "scope", "out_of_scope", "constraints", "acceptance_criteria", "risks_and_open_items",
)
REVIEW_FIELDS = {
    "background": "背景与问题",
    "delivery_goal": "交付目标",
    "scope": "本次范围",
    "out_of_scope": "不做什么",
    "constraints": "约束与依赖",
    "acceptance_criteria": "验收标准",
    "risks_and_open_items": "风险与待确认事项",
}


class DeliverylineStore:
    def __init__(self, requirements_dir: Path, state_dir: Path | None = None) -> None:
        self.requirements_dir = requirements_dir
        self.state_dir = state_dir or requirements_dir.parent / ".state"
        self.lock_path = self.state_dir / "requirements.lock"
        self._thread_lock = threading.RLock()

    def list(self, *, include_archived: bool = False) -> list[Requirement]:
        with self._locked():
            records = [self._read(path) for path in self._paths()]
        if not include_archived:
            records = [record for record in records if record.workflow.delivery_status != "已归档"]
        return sorted(records, key=lambda record: record.updated_at, reverse=True)

    def get(self, requirement_id: str) -> Requirement:
        with self._locked():
            return self._read(self._path(requirement_id))

    def create(self, description: str) -> Requirement:
        content = self._text(description, "需求描述", 4000)
        now = utc_now()
        with self._locked():
            requirement_id = self._new_id(now)
            title = " ".join(content.split())[:120]
            record = Requirement(
                id=requirement_id,
                title=title,
                background=content,
                workflow=Workflow(next_action="补充需求档案，准备进入评审"),
                activity=[Activity(id=uuid4().hex, occurred_at=now, action="created", summary="已创建需求提出档案。")],
                created_at=now,
                updated_at=now,
            )
            self._write(record)
        return record

    def update(self, requirement_id: str, values: dict[str, str]) -> Requirement:
        with self._locked():
            record = self._read(self._path(requirement_id))
            for field in EDITABLE_FIELDS:
                if field in values:
                    setattr(record, field, self._text(values[field], REVIEW_FIELDS.get(field, "标题"), 4000 if field != "title" else 120, allow_empty=field != "title"))
            now = utc_now()
            if record.workflow.current_stage == "需求提出":
                record.workflow.delivery_status = "存在风险" if record.risks_and_open_items.strip() and record.risks_and_open_items.strip() != "暂无" else ("进行中" if not self.review_missing(record) else "待我处理")
                record.workflow.next_action = "提交需求评审" if not self.review_missing(record) else "补充需求档案，准备进入评审"
            record.updated_at = now
            self._append(record, now, "updated", "已更新需求档案。")
            self._write(record)
        return record

    def submit_for_review(self, requirement_id: str) -> Requirement:
        with self._locked():
            record = self._read(self._path(requirement_id))
            missing = self.review_missing(record)
            if missing:
                raise DeliverylineReviewNotReady(missing)
            now = utc_now()
            record.workflow.current_stage = "需求评审"
            record.workflow.delivery_status = "进行中"
            record.workflow.next_action = "开展需求评审"
            record.workflow.latest_stage_conclusion = "需求档案已提交评审。"
            record.updated_at = now
            self._append(record, now, "submitted_for_review", "需求提出资料已完整，已进入需求评审。")
            self._write(record)
        return record

    def archive(self, requirement_id: str) -> Requirement:
        with self._locked():
            record = self._read(self._path(requirement_id))
            now = utc_now()
            record.workflow.delivery_status = "已归档"
            record.workflow.next_action = "已归档"
            record.updated_at = now
            self._append(record, now, "archived", "需求已归档。")
            self._write(record)
        return record

    @staticmethod
    def review_missing(record: Requirement) -> list[str]:
        return [label for field, label in REVIEW_FIELDS.items() if not getattr(record, field).strip()]

    def _paths(self) -> list[Path]:
        if not self.requirements_dir.exists():
            return []
        try:
            paths = sorted(self.requirements_dir.glob("DL-*.json"))
        except OSError as exc:
            raise DeliverylineUnavailable("需求档案目录无法读取。") from exc
        if len(paths) > MAX_REQUIREMENTS:
            raise DeliverylineUnavailable("需求档案数量超过固定上限。")
        return paths

    def _path(self, requirement_id: str) -> Path:
        if not re.fullmatch(r"DL-\d{8}-[A-Z0-9]{4}", requirement_id):
            raise DeliverylineNotFound("需求不存在。")
        return self.requirements_dir / f"{requirement_id}.json"

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
                raise DeliverylineUnavailable("需求档案锁不可用。") from exc
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX)
                yield
            finally:
                fcntl.flock(descriptor, fcntl.LOCK_UN)
                os.close(descriptor)

    def _read(self, path: Path) -> Requirement:
        try:
            metadata = path.lstat()
        except FileNotFoundError as exc:
            raise DeliverylineNotFound("需求不存在。") from exc
        except OSError as exc:
            raise DeliverylineUnavailable("需求档案无法读取。") from exc
        if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode) or metadata.st_size > MAX_REQUIREMENT_BYTES:
            raise DeliverylineUnavailable("需求档案格式或大小无效。")
        try:
            descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
            with os.fdopen(descriptor, encoding="utf-8") as file:
                content = file.read(MAX_REQUIREMENT_BYTES + 1)
            if len(content.encode("utf-8")) > MAX_REQUIREMENT_BYTES:
                raise DeliverylineUnavailable("需求档案格式或大小无效。")
            return Requirement.model_validate(json.loads(content))
        except (OSError, UnicodeError, ValueError, ValidationError) as exc:
            raise DeliverylineUnavailable("需求档案格式无效。") from exc

    def _write(self, record: Requirement) -> None:
        content = (json.dumps(record.model_dump(mode="json"), ensure_ascii=False, separators=(",", ":")) + "\n").encode()
        if len(content) > MAX_REQUIREMENT_BYTES:
            raise DeliverylineUnavailable("需求档案超过固定大小上限。")
        path = self._path(record.id)
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
            raise DeliverylineUnavailable("需求档案无法保存。") from exc
        finally:
            temporary.unlink(missing_ok=True)

    @staticmethod
    def _append(record: Requirement, now: datetime, action: Literal["created", "updated", "submitted_for_review", "archived"], summary: str) -> None:
        record.activity = [*record.activity, Activity(id=uuid4().hex, occurred_at=now, action=action, summary=summary)][-MAX_ACTIVITY_ITEMS:]

    def _new_id(self, now: datetime) -> str:
        for _ in range(100):
            identifier = f"DL-{now:%Y%m%d}-{uuid4().hex[:4].upper()}"
            if not (self.requirements_dir / f"{identifier}.json").exists():
                return identifier
        raise DeliverylineUnavailable("无法生成需求编号。")

    @staticmethod
    def _text(value: str, label: str, maximum: int, *, allow_empty: bool = False) -> str:
        normalized = value.strip()
        if not normalized and not allow_empty:
            raise ValueError(f"{label}不能为空。")
        if len(normalized) > maximum:
            raise ValueError(f"{label}超过长度上限。")
        return normalized
