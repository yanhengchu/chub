from __future__ import annotations

import json
import os
import re
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

from app.ai_session.operations import delete_session
from app.core.response import ApiError
from app.deliveryline.store import DeliveryLine, DeliverylineTransitionNotAllowed, DeliverylineUnavailable
from app.quick_worker_tasks import FINAL_STATUSES
from app.services.design_documents import DesignDocumentIndexError, get_design_document_source


MAX_STATE_BYTES = 512 * 1024
MAX_LINKED_SOURCES = 3
MAX_PROMPT_DOCUMENT_CHARS = 6_000
_HTTP_URL_PATTERN = re.compile(r"https?://[^\s<>\"']+", re.IGNORECASE)
_PROJECT_DOCUMENT_PATH = re.compile(r"^/project-docs/([a-z0-9][a-z0-9-]{0,63})/?$", re.IGNORECASE)


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ClarificationSuggestion(_StrictModel):
    source_role: Literal["相对完整需求", "持续演进需求", "设计提案", "现状说明", "混合资料"]
    known_facts: list[str] = Field(default_factory=list, max_length=30)
    title: str = Field(min_length=1, max_length=120)
    overall_goal: str = Field(min_length=1, max_length=4000)
    scope_boundary: str = Field(default="", max_length=4000)
    open_questions: list[str] = Field(default_factory=list, max_length=30)
    assumptions: list[str] = Field(default_factory=list, max_length=30)


class CollaborationRound(_StrictModel):
    id: str
    task_id: str | None = None
    operation_id: str | None = None
    sources: list[dict[str, str]] = Field(default_factory=list, max_length=MAX_LINKED_SOURCES)
    status: str
    suggestion: ClarificationSuggestion | None = None
    error: str | None = Field(default=None, max_length=1000)
    created_at: datetime
    updated_at: datetime


class LineCollaboration(_StrictModel):
    line_id: str
    session_id: str
    rounds: list[CollaborationRound] = Field(default_factory=list, max_length=30)


class CollaborationState(_StrictModel):
    version: int = 3
    show_sessions: bool = False
    lines: list[LineCollaboration] = Field(default_factory=list, max_length=500)


class DeliverylineCollaboration:
    """Local projection for AI overall-clarification rounds, never source-of-truth line data."""

    def __init__(self, state_dir: Path) -> None:
        self.path = state_dir / "line-ai-clarification.json"
        self._lock = threading.RLock()
        self._state_error: str | None = None
        self._state = self._read()

    def show_sessions(self) -> bool:
        with self._lock:
            self._require_state_available()
            return self._state.show_sessions

    def set_show_sessions(self, show: bool) -> bool:
        with self._lock:
            self._require_state_available()
            if self._state.show_sessions != show:
                next_state = self._state.model_copy(deep=True)
                next_state.show_sessions = show
                self._commit(next_state)
            return self._state.show_sessions

    def hidden_session_ids(self) -> set[str]:
        with self._lock:
            if self._state_error is not None or self._state.show_sessions:
                return set()
            return {item.session_id for item in self._state.lines}

    def status_for(self, line: DeliveryLine, quick_interactions) -> dict[str, object] | None:
        with self._lock:
            if self._state_error is not None:
                return None
            association = self._association(line.id)
            if association is None:
                return None
            self._refresh_latest(association, quick_interactions)
            latest = association.rounds[-1] if association.rounds else None
            return self._public(association, latest) if latest else None

    def start(self, line: DeliveryLine, manager, quick_interactions, *, comment: str = "", source_ip: str) -> dict[str, object]:
        if line.status != "待澄清":
            raise DeliverylineTransitionNotAllowed("仅待澄清交付线可以发起 AI 整体澄清。")
        normalized_comment = comment.strip()
        with self._lock:
            self._require_state_available()
            association = self._association(line.id)
            if association is not None:
                self._refresh_latest(association, quick_interactions)
                latest = association.rounds[-1] if association.rounds else None
                if latest is not None and latest.status in {"submitting", "requested", "running"}:
                    raise ApiError(409, "deliveryline_ai_clarification_in_progress", "当前 AI 整体澄清仍在处理，请等待结果。")
                try:
                    manager.get_session(association.session_id)
                except ApiError as exc:
                    if exc.code != "session_not_found":
                        raise
                    next_state = self._state.model_copy(deep=True)
                    next_state.lines = [item for item in next_state.lines if item.line_id != line.id]
                    self._commit(next_state)
                    association = None
            if association is None:
                with quick_interactions.session_creation_guard():
                    session = manager.create_session("chub")
                association = LineCollaboration(line_id=line.id, session_id=session.id)
                try:
                    manager.rename_session(session.id, f"Deliveryline · {line.id}")
                    next_state = self._state.model_copy(deep=True)
                    next_state.lines.append(association)
                    self._commit(next_state)
                except Exception:
                    manager.discard_unstarted_session(session.id)
                    raise
            operation_id = uuid4().hex
            sources, prompt_sources = self._linked_sources(line)
            pending = CollaborationRound(id=uuid4().hex, operation_id=operation_id, sources=sources, status="submitting", created_at=utc_now(), updated_at=utc_now())
            next_state = self._state.model_copy(deep=True)
            next_association = self._association_in(next_state, line.id)
            assert next_association is not None
            previous = next_association.rounds[-1].suggestion if next_association.rounds else None
            next_association.rounds.append(pending)
            self._commit(next_state)
            try:
                with quick_interactions.session_operation_guard(next_association.session_id):
                    task = quick_interactions.submit(
                        next_association.session_id,
                        self._prompt(line, previous, normalized_comment, prompt_sources),
                        operation_id=operation_id,
                        source_ip=source_ip,
                    )
            except Exception:
                finder = getattr(quick_interactions, "find_for_operation", None)
                task = finder(operation_id) if callable(finder) else None
                if task is None:
                    self._mark_failed(pending, "AI 整体澄清任务未能提交，可再次发起。")
                    raise
            pending.task_id = task.id
            pending.status = "requested"
            pending.updated_at = utc_now()
            self._write(self._state)
            return self._public(next_association, pending)

    def ensure_goal_confirmation_ready(self, line: DeliveryLine, quick_interactions) -> ClarificationSuggestion:
        with self._lock:
            self._require_state_available()
            association = self._association(line.id)
            if association is None or not association.rounds:
                raise DeliverylineTransitionNotAllowed("请先完成 AI 整体澄清，再确认整体目标。")
            self._refresh_latest(association, quick_interactions)
            latest = association.rounds[-1]
            if latest.status in {"submitting", "requested", "running"}:
                raise DeliverylineTransitionNotAllowed("当前 AI 整体澄清仍在处理，请等待结果后再确认。")
            if latest.status != "suggested" or latest.suggestion is None:
                raise DeliverylineTransitionNotAllowed("当前没有可确认的 AI 整体澄清结果。")
            return latest.suggestion

    def remove(self, line_id: str) -> None:
        with self._lock:
            self._require_state_available()
            if not any(item.line_id == line_id for item in self._state.lines):
                return
            next_state = self._state.model_copy(deep=True)
            next_state.lines = [item for item in next_state.lines if item.line_id != line_id]
            self._commit(next_state)

    def delete_associated_session(self, line_id: str, manager, quick_interactions) -> None:
        """Delete only the Chub Session owned by this Deliveryline association."""
        with self._lock:
            self._require_state_available()
            association = self._association(line_id)
            if association is None:
                return
            delete_session(
                association.session_id,
                manager=manager,
                quick_interactions=quick_interactions,
            )

    def _refresh_latest(self, association: LineCollaboration, quick_interactions) -> None:
        latest = association.rounds[-1] if association.rounds else None
        if latest is None or latest.status not in {"submitting", "requested", "running"}:
            return
        if latest.task_id is None and latest.operation_id:
            finder = getattr(quick_interactions, "find_for_operation", None)
            task = finder(latest.operation_id) if callable(finder) else None
            if task is None:
                self._mark_failed(latest, "AI 整体澄清任务状态无法确认，可再次发起。")
                return
            latest.task_id = task.id
            latest.status = "requested"
            latest.updated_at = utc_now()
            self._write(self._state)
        if latest.task_id is None:
            self._mark_failed(latest, "AI 整体澄清任务状态无法确认，可再次发起。")
            return
        try:
            task = quick_interactions.get(latest.task_id)
        except ApiError:
            self._mark_failed(latest, "AI 整体澄清任务不存在或无法读取，可再次发起。")
            return
        if task.status not in FINAL_STATUSES:
            if latest.status != "running":
                latest.status = "running"
                latest.updated_at = utc_now()
                self._write(self._state)
            return
        latest.updated_at = utc_now()
        if task.status == "succeeded" and task.result:
            try:
                latest.suggestion = self._parse_suggestion(task.result)
                latest.status = "suggested"
                latest.error = None
            except ValueError as exc:
                latest.status = "failed"
                latest.error = str(exc) or "AI 返回内容不符合整体澄清格式，可再次发起。"
        else:
            latest.status = "failed"
            latest.error = task.error or "AI 整体澄清未能生成建议。"
        self._write(self._state)

    @staticmethod
    def _parse_suggestion(result: str) -> ClarificationSuggestion:
        content = result.strip()
        match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", content, re.DOTALL)
        if match:
            content = match.group(1)
        try:
            payload = json.loads(content)
            suggestion = ClarificationSuggestion.model_validate(payload)
        except (ValueError, TypeError) as exc:
            raise ValueError("AI 返回内容不是完整的整体澄清 JSON。") from exc
        for values in (suggestion.known_facts, suggestion.open_questions, suggestion.assumptions):
            if not all(isinstance(item, str) and item.strip() and len(item.strip()) <= 1000 for item in values):
                raise ValueError("AI 返回的清单项格式无效。")
        if DeliverylineCollaboration._has_presentation_prefix(suggestion.title):
            raise ValueError("AI 返回的标题包含展示或待确认前缀，请再次 AI 协作。")
        return suggestion.model_copy(update={
            "title": suggestion.title.strip(),
            "source_role": suggestion.source_role.strip(),
            "known_facts": [item.strip() for item in suggestion.known_facts],
            "overall_goal": suggestion.overall_goal.strip(),
            "scope_boundary": suggestion.scope_boundary.strip(),
            "open_questions": [item.strip() for item in suggestion.open_questions],
            "assumptions": [item.strip() for item in suggestion.assumptions],
        })

    @staticmethod
    def _has_presentation_prefix(value: str) -> bool:
        return re.match(r"^(?:(?:AI\s*建议|建议|待确认|标题|展示|显示)\s*[:：]\s*)+", value.strip(), flags=re.IGNORECASE) is not None

    def _association(self, line_id: str) -> LineCollaboration | None:
        return next((item for item in self._state.lines if item.line_id == line_id), None)

    @staticmethod
    def _association_in(state: CollaborationState, line_id: str) -> LineCollaboration | None:
        return next((item for item in state.lines if item.line_id == line_id), None)

    def _mark_failed(self, latest: CollaborationRound, error: str) -> None:
        latest.status = "failed"
        latest.error = error
        latest.updated_at = utc_now()
        self._write(self._state)

    def _commit(self, state: CollaborationState) -> None:
        self._write(state)
        self._state = state

    def _require_state_available(self) -> None:
        if self._state_error is not None:
            raise DeliverylineUnavailable("AI 整体澄清本机状态不可读取，请恢复状态文件后重试。")

    @staticmethod
    def _public(association: LineCollaboration, latest: CollaborationRound) -> dict[str, object]:
        return {
            "session_id": association.session_id,
            "status": latest.status,
            "suggestion": latest.suggestion.model_dump() if latest.suggestion else None,
            "error": latest.error,
            "sources": latest.sources,
            "updated_at": latest.updated_at.isoformat(),
        }

    @staticmethod
    def _prompt(line: DeliveryLine, previous: ClarificationSuggestion | None, comment: str, prompt_sources: list[dict[str, object]]) -> str:
        payload = {
            "original_request_content": line.original_request_content,
            "previous_candidate": previous.model_dump() if previous else None,
            "maintainer_comment": comment,
            "linked_sources": prompt_sources,
        }
        return (
            "你正在完成 Deliveryline 的 AI 整体澄清。原始资料只读。你的职责是理解资料，"
            "生成维护者可以确认或修正的整体目标候选；不要执行命令、编辑文件、创建交付项、"
            "拆分路线图、排优先级、提交评审或声称工作完成。\n\n"
            "必须区分事实、推断与待确认内容。known_facts 只能写来源明确支持的事实；"
            "assumptions 写推断；资料不足、网页无法读取或需维护者决定的内容写 open_questions。"
            "title 是可直接确认的交付线标题，不是展示标签，开头不得包含 AI 建议、建议、待确认、标题、展示或显示等前缀。\n\n"
            "只返回一个 JSON 对象，不要 Markdown 或解释，键必须完全为："
            "source_role、known_facts、title、overall_goal、scope_boundary、open_questions、assumptions。"
            "source_role 使用下列之一：相对完整需求、持续演进需求、设计提案、现状说明、混合资料。\n\n"
            + json.dumps(payload, ensure_ascii=False)
        )

    @staticmethod
    def _linked_sources(line: DeliveryLine) -> tuple[list[dict[str, str]], list[dict[str, object]]]:
        urls: list[str] = []
        for match in _HTTP_URL_PATTERN.finditer(line.original_request_content):
            candidate = match.group(0).rstrip(").,;!?")
            if candidate and candidate not in urls:
                urls.append(candidate)
            if len(urls) >= MAX_LINKED_SOURCES:
                break
        sources: list[dict[str, str]] = []
        prompt_sources: list[dict[str, object]] = []
        public_urls: list[str] = []
        for url in urls:
            try:
                parsed = urlsplit(url)
            except ValueError:
                continue
            match = _PROJECT_DOCUMENT_PATH.fullmatch(parsed.path)
            if match is None:
                public_urls.append(url)
                sources.append({"kind": "public_page", "label": "公共网页资料", "url": url})
                continue
            try:
                document = get_design_document_source(match.group(1).lower(), max_chars=MAX_PROMPT_DOCUMENT_CHARS)
            except (DesignDocumentIndexError, OSError, UnicodeError, ValueError):
                document = None
            if document is None:
                sources.append({"kind": "project_document_unavailable", "label": "项目资料暂不可读取", "url": url})
                prompt_sources.append({"kind": "project_document_unavailable", "url": url, "instruction": "资料无法读取。不要臆测正文，写入 open_questions。"})
                continue
            sources.append({"kind": "project_document", "label": document.title, "url": url})
            prompt_sources.append({"kind": "project_document", "url": url, "document_id": document.id, "title": document.title, "content": document.content, "truncated": document.truncated})
        if public_urls:
            prompt_sources.append({"kind": "public_pages", "urls": public_urls, "instruction": "必须先用 chub capability page-read 命令逐一读取网页。只能依据读取结果生成候选；失败时写入 open_questions。"})
        return sources, prompt_sources

    def _read(self) -> CollaborationState:
        try:
            if not self.path.exists():
                return CollaborationState()
            if self.path.stat().st_size > MAX_STATE_BYTES:
                raise OSError("state too large")
            return CollaborationState.model_validate(json.loads(self.path.read_text(encoding="utf-8")))
        except (OSError, ValueError, json.JSONDecodeError):
            self._state_error = "AI 整体澄清本机状态不可读取。"
            return CollaborationState()

    def _write(self, state: CollaborationState) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        os.chmod(self.path.parent, 0o700)
        temporary = self.path.with_suffix(".tmp")
        try:
            with open(temporary, "w", encoding="utf-8") as handle:
                os.chmod(temporary, 0o600)
                handle.write(state.model_dump_json(indent=2))
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self.path)
            os.chmod(self.path, 0o600)
        except OSError as exc:
            raise DeliverylineUnavailable("AI 整体澄清本机状态无法保存。") from exc
        finally:
            temporary.unlink(missing_ok=True)
