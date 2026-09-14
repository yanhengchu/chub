from __future__ import annotations

import json
import os
import re
import threading
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

from app.ai_session.operations import delete_session
from app.core.response import ApiError
from app.deliveryline.store import EDITABLE_FIELDS, DeliverylineTransitionNotAllowed, DeliverylineUnavailable, Requirement
from app.deliveryline.workflow import collaboration_substage
from app.quick_worker_tasks import FINAL_STATUSES
from app.services.design_documents import DesignDocumentIndexError, get_design_document_source


MAX_STATE_BYTES = 512 * 1024
MAX_LINKED_SOURCES = 3
MAX_PROMPT_FIELD_CHARS = 750
MAX_PROMPT_DOCUMENT_CHARS = 6_000
_HTTP_URL_PATTERN = re.compile(r"https?://[^\s<>\"']+", re.IGNORECASE)
_PROJECT_DOCUMENT_PATH = re.compile(r"^/project-docs/([a-z0-9][a-z0-9-]{0,63})/?$", re.IGNORECASE)

def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class FieldSuggestion(_StrictModel):
    value: str
    status: str = "suggested"
    comment: str | None = Field(default=None, max_length=4000)


class CollaborationSuggestion(_StrictModel):
    fields: dict[str, FieldSuggestion]
    open_questions: list[str] = Field(default_factory=list, max_length=20)


class CollaborationRound(_StrictModel):
    id: str
    stage: str
    stage_goal: str | None = None
    stage_acceptance: str | None = None
    task_id: str | None = None
    operation_id: str | None = None
    fields_to_generate: list[str] = Field(default_factory=list, max_length=len(EDITABLE_FIELDS))
    sources: list[dict[str, str]] = Field(default_factory=list, max_length=MAX_LINKED_SOURCES)
    status: str
    suggestion: CollaborationSuggestion | None = None
    error: str | None = Field(default=None, max_length=1000)
    created_at: datetime
    updated_at: datetime


class RequirementCollaboration(_StrictModel):
    requirement_id: str
    session_id: str
    replacement_required: bool = False
    rounds: list[CollaborationRound] = Field(default_factory=list, max_length=30)


class CollaborationState(_StrictModel):
    version: int = 2
    show_sessions: bool = False
    requirements: list[RequirementCollaboration] = Field(default_factory=list, max_length=500)


class DeliverylineCollaboration:
    """Own Deliveryline's local field-review projection, never Session state."""

    def __init__(self, state_dir: Path) -> None:
        self.path = state_dir / "ai-collaboration.json"
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
            return {item.session_id for item in self._state.requirements}

    def status_for(self, requirement: Requirement, quick_interactions) -> dict[str, object] | None:
        with self._lock:
            if self._state_error is not None:
                return None
            association = self._association(requirement.id)
            if association is None:
                return None
            self._refresh_latest(association, quick_interactions)
            latest = association.rounds[-1] if association.rounds else None
            return self._public(association, latest) if latest else None

    def start(self, requirement: Requirement, manager, quick_interactions, *, comments: dict[str, str] | None = None, source_ip: str) -> dict[str, object]:
        self._require_active_proposal(requirement)
        submitted_comments = comments or {}
        with self._lock:
            self._require_state_available()
            association = self._association(requirement.id)
            if association is not None:
                if association.replacement_required:
                    delete_session(
                        association.session_id,
                        manager=manager,
                        quick_interactions=quick_interactions,
                    )
                    next_state = self._state.model_copy(deep=True)
                    next_state.requirements = [item for item in next_state.requirements if item.requirement_id != requirement.id]
                    self._commit(next_state)
                    association = None
                else:
                    self._refresh_latest(association, quick_interactions)
                    latest = association.rounds[-1] if association.rounds else None
                    if latest is not None and latest.status in {"submitting", "requested", "running"}:
                        raise ApiError(409, "deliveryline_ai_collaboration_in_progress", "当前 AI 协作仍在处理，请等待结果。")
                    if latest is not None and latest.status == "suggested":
                        self._record_comments(latest, submitted_comments)
                        if any(item.status == "applying" for item in latest.suggestion.fields.values()):
                            raise ApiError(409, "deliveryline_ai_field_applying", "当前字段正在采纳，请等待保存完成后再继续 AI 协作。")
                    try:
                        manager.get_session(association.session_id)
                    except ApiError as exc:
                        if exc.code != "codex_session_not_found":
                            raise
                        next_state = self._state.model_copy(deep=True)
                        next_state.requirements = [item for item in next_state.requirements if item.requirement_id != requirement.id]
                        self._commit(next_state)
                        association = None
            elif submitted_comments:
                raise ApiError(409, "deliveryline_ai_suggestion_unavailable", "当前没有可提交意见的 AI 建议。")
            if association is None:
                with quick_interactions.session_creation_guard():
                    session = manager.create_session("chub")
                association = RequirementCollaboration(requirement_id=requirement.id, session_id=session.id)
                try:
                    manager.rename_session(session.id, f"Deliveryline · {requirement.id}")
                    next_state = self._state.model_copy(deep=True)
                    next_state.requirements.append(association)
                    self._commit(next_state)
                except Exception:
                    manager.discard_unstarted_session(session.id)
                    raise
            latest = association.rounds[-1] if association.rounds else None
            fields_to_generate = self._fields_to_generate(latest)
            if latest is not None and not fields_to_generate:
                raise ApiError(409, "deliveryline_ai_no_fields_to_revise", "当前没有需要根据建议重新整理的字段。")
            operation_id = uuid4().hex
            substage = collaboration_substage()
            sources, prompt_sources, capability_ids = self._linked_sources(requirement)
            pending = CollaborationRound(
                id=uuid4().hex,
                stage=requirement.workflow.current_stage,
                stage_goal=substage.objective,
                stage_acceptance=substage.acceptance,
                operation_id=operation_id,
                fields_to_generate=list(fields_to_generate),
                sources=sources,
                status="submitting",
                created_at=utc_now(),
                updated_at=utc_now(),
            )
            next_state = self._state.model_copy(deep=True)
            next_association = self._association_in(next_state, requirement.id)
            assert next_association is not None
            next_association.rounds.append(pending)
            self._commit(next_state)
            association = next_association
            prompt = self._prompt(
                requirement,
                latest.suggestion if latest else None,
                fields_to_generate,
                prompt_sources=prompt_sources,
            )
            try:
                with quick_interactions.session_operation_guard(association.session_id):
                    task = quick_interactions.submit(
                        association.session_id,
                        prompt,
                        operation_id=operation_id,
                        source_ip=source_ip,
                        capability_ids=capability_ids,
                    )
            except Exception:
                finder = getattr(quick_interactions, "find_for_operation", None)
                task = finder(operation_id) if callable(finder) else None
                if task is None:
                    self._mark_failed(pending, "AI 协作任务未能提交，可再次 AI 协作。")
                    raise
            pending.task_id = task.id
            pending.status = "requested"
            pending.updated_at = utc_now()
            self._write(self._state)
            return self._public(association, pending)

    def prepare_field_accept(self, requirement: Requirement, field: str) -> str:
        self._require_active_proposal(requirement)
        self._require_field(field)
        with self._lock:
            self._require_state_available()
            latest = self._latest(requirement.id)
            item = self._suggested_field(latest, field)
            if item.status not in {"suggested", "applying"}:
                raise ApiError(409, "deliveryline_ai_field_unavailable", "当前字段没有可采纳的 AI 建议。")
            if item.status == "suggested":
                item.status = "applying"
                latest.updated_at = utc_now()
                self._write(self._state)
            return item.value

    def reconcile_field_accept(self, requirement: Requirement, field: str) -> None:
        with self._lock:
            if self._state_error is not None:
                return
            latest = self._latest(requirement.id)
            item = self._suggested_field(latest, field)
            if item.status != "applying":
                return
            item.status = "accepted" if getattr(requirement, field) == item.value else "suggested"
            latest.updated_at = utc_now()
            self._write(self._state)

    def ensure_stage_confirmation_ready(self, requirement: Requirement) -> None:
        with self._lock:
            if self._state_error is not None:
                raise DeliverylineUnavailable("AI 协作本机状态不可读取，请恢复状态文件后再确认阶段。")
            latest = self._latest(requirement.id, required=False)
            if latest is not None and latest.status in {"submitting", "requested", "running"}:
                raise DeliverylineTransitionNotAllowed("当前 AI 协作仍在处理，请等待结果后再确认阶段。")
            if latest is not None and latest.status == "suggested" and not self._round_resolved(latest):
                raise DeliverylineTransitionNotAllowed("请先逐项处理当前 AI 建议，再确认阶段。")

    def remove(self, requirement_id: str) -> None:
        with self._lock:
            self._require_state_available()
            if not any(item.requirement_id == requirement_id for item in self._state.requirements):
                return
            next_state = self._state.model_copy(deep=True)
            next_state.requirements = [item for item in next_state.requirements if item.requirement_id != requirement_id]
            self._commit(next_state)

    def _refresh_latest(self, association: RequirementCollaboration, quick_interactions) -> None:
        latest = association.rounds[-1] if association.rounds else None
        if latest is None or latest.status not in {"submitting", "requested", "running"}:
            return
        if latest.task_id is None and latest.operation_id:
            finder = getattr(quick_interactions, "find_for_operation", None)
            task = finder(latest.operation_id) if callable(finder) else None
            if task is None:
                self._mark_failed(latest, "AI 协作任务状态无法确认，可再次 AI 协作。")
                return
            latest.task_id = task.id
            latest.status = "requested"
            latest.updated_at = utc_now()
            self._write(self._state)
        try:
            task = quick_interactions.get(latest.task_id)
        except ApiError:
            self._mark_failed(latest, "AI 协作任务不存在或无法读取，可再次 AI 协作。")
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
                latest.suggestion = self._parse_suggestion(task.result, expected_fields=tuple(latest.fields_to_generate) or EDITABLE_FIELDS)
                latest.status = "suggested"
                latest.error = None
            except ValueError as exc:
                latest.status = "failed"
                latest.error = str(exc) or "AI 返回内容不符合字段建议格式，可再次 AI 协作。"
        else:
            latest.status = "failed"
            latest.error = task.error or "AI 协作未能生成建议。"
        self._write(self._state)

    @staticmethod
    def _parse_suggestion(result: str, *, expected_fields: tuple[str, ...] = EDITABLE_FIELDS) -> CollaborationSuggestion:
        content = result.strip()
        match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", content, re.DOTALL)
        if match:
            content = match.group(1)
        payload = json.loads(content)
        if not isinstance(payload, dict):
            raise ValueError("not an object")
        fields = payload.get("fields")
        questions = payload.get("open_questions", [])
        if not isinstance(fields, dict) or not isinstance(questions, list):
            raise ValueError("missing fields")
        normalized = {}
        for field, value in fields.items():
            if field not in EDITABLE_FIELDS or not isinstance(value, str) or not value.strip():
                continue
            candidate = value.strip()
            if field == "title" and DeliverylineCollaboration._title_has_presentation_prefix(candidate):
                raise ValueError("AI 返回的标题包含展示或待确认前缀，请再次 AI 协作。")
            normalized[field] = FieldSuggestion(value=candidate)
        if set(normalized) != set(expected_fields):
            raise ValueError("incomplete fields")
        if not all(isinstance(item, str) and item.strip() for item in questions):
            raise ValueError("invalid questions")
        return CollaborationSuggestion(fields=normalized, open_questions=[item.strip() for item in questions])

    @staticmethod
    def _title_has_presentation_prefix(value: str) -> bool:
        return re.match(r"^(?:(?:AI\s*建议|建议|待确认|标题|展示|显示)\s*[:：]\s*)+", value.strip(), flags=re.IGNORECASE) is not None

    @staticmethod
    def _require_active_proposal(requirement: Requirement) -> None:
        if requirement.workflow.delivery_status == "已归档" or requirement.workflow.current_stage != "需求提出":
            raise DeliverylineTransitionNotAllowed("当前需求不能发起 AI 协作。")

    @staticmethod
    def _require_field(field: str) -> None:
        if field not in EDITABLE_FIELDS:
            raise ApiError(404, "deliveryline_ai_field_not_found", "需求字段不存在。")

    def _latest(self, requirement_id: str, *, required: bool = True) -> CollaborationRound | None:
        association = self._association(requirement_id)
        latest = association.rounds[-1] if association and association.rounds else None
        if latest is None and required:
            raise ApiError(409, "deliveryline_ai_suggestion_unavailable", "当前没有可处理的 AI 协作建议。")
        return latest

    @staticmethod
    def _suggested_field(latest: CollaborationRound | None, field: str) -> FieldSuggestion:
        if latest is None or latest.status != "suggested" or latest.suggestion is None or field not in latest.suggestion.fields:
            raise ApiError(409, "deliveryline_ai_field_unavailable", "当前字段没有可处理的 AI 建议。")
        return latest.suggestion.fields[field]

    @staticmethod
    def _round_resolved(round_: CollaborationRound) -> bool:
        return bool(round_.suggestion) and all(item.status in {"accepted", "commented"} for item in round_.suggestion.fields.values())

    @staticmethod
    def _fields_to_generate(round_: CollaborationRound | None) -> tuple[str, ...]:
        if round_ is None or round_.suggestion is None:
            return EDITABLE_FIELDS
        return tuple(field for field, item in round_.suggestion.fields.items() if item.status != "accepted")

    def _record_comments(self, latest: CollaborationRound, comments: dict[str, str]) -> None:
        if not comments:
            return
        if latest.status != "suggested" or latest.suggestion is None:
            raise ApiError(409, "deliveryline_ai_suggestion_unavailable", "当前没有可提交意见的 AI 建议。")
        for field, raw_value in comments.items():
            self._require_field(field)
            value = raw_value.strip()
            if not value:
                raise ApiError(422, "deliveryline_ai_field_comment_required", "请输入对该字段 AI 建议的意见。")
            item = latest.suggestion.fields.get(field)
            if item is None or item.status != "suggested":
                raise ApiError(409, "deliveryline_ai_field_unavailable", "当前字段没有可提出建议的 AI 建议。")
            item.comment = value
            item.status = "commented"
        latest.updated_at = utc_now()
        self._write(self._state)

    def _association(self, requirement_id: str) -> RequirementCollaboration | None:
        return next((item for item in self._state.requirements if item.requirement_id == requirement_id), None)

    @staticmethod
    def _association_in(state: CollaborationState, requirement_id: str) -> RequirementCollaboration | None:
        return next((item for item in state.requirements if item.requirement_id == requirement_id), None)

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
            raise DeliverylineUnavailable("AI 协作本机状态不可读取，请恢复状态文件后重试。")

    @staticmethod
    def _public(association: RequirementCollaboration, latest: CollaborationRound) -> dict[str, object]:
        suggestion = latest.suggestion.model_dump() if latest.suggestion else None
        return {
            "session_id": association.session_id,
            "status": latest.status,
            "suggestion": suggestion,
            "can_continue": latest.status != "suggested" or bool(DeliverylineCollaboration._fields_to_generate(latest)),
            "error": latest.error,
            "sources": latest.sources,
            "updated_at": latest.updated_at.isoformat(),
        }

    @staticmethod
    def _prompt(
        requirement: Requirement,
        previous: CollaborationSuggestion | None,
        fields_to_generate: tuple[str, ...],
        *,
        prompt_sources: list[dict[str, object]] | None = None,
    ) -> str:
        substage = collaboration_substage()
        fields = {
            field: DeliverylineCollaboration._prompt_text(getattr(requirement, field))
            for field in EDITABLE_FIELDS
        }
        previous_fields = (
            {
                field: {
                    "value": DeliverylineCollaboration._prompt_text(item.value),
                    "status": item.status,
                    "comment": DeliverylineCollaboration._prompt_text(item.comment or ""),
                }
                for field, item in previous.fields.items()
            }
            if previous
            else None
        )
        payload = {
            "stage": f"需求提出 · {substage.name}",
            "goal": substage.objective,
            "acceptance": substage.acceptance,
            "original_request_content": DeliverylineCollaboration._prompt_text(requirement.original_request_content or "", maximum=4_000),
            "current_fields": fields,
            "previous_field_suggestions": previous_fields,
            "fields_to_generate": fields_to_generate,
            "linked_sources": prompt_sources or [],
        }
        return (
            "你正在协作整理 Deliveryline 需求档案。只完成当前阶段的资料整理，"
            "不要执行命令、编辑文件、提交评审或声称验收完成。原始需求内容只读。\n\n"
            "请只为 fields_to_generate 中列出的字段生成候选建议。维护者已经采纳的字段以 current_fields 为准，"
            "不得重新生成或改写；未采纳的上一轮建议可以被本轮替换，"
            "previous_field_suggestions 中的维护者评论必须优先处理。\n\n"
            "必须只返回一个 JSON 对象，不要使用 Markdown，也不要输出解释文字："
            "{\"fields\":{fields_to_generate 中字段名对应的非空字符串},\"open_questions\":[字符串]}。"
            "fields 必须与 fields_to_generate 完全一致。\n\n"
            "title 是档案标题值，不是展示标签或问题说明。title 必须是可直接写入档案的简洁标题正文，"
            "开头不得包含 AI 建议、建议、待确认、标题、展示、显示或类似标签，不得以这些标签加冒号开头。"
            "即使标题信息不完整，也要给出中性的可用工作标题；所有不确定、待确认或缺失信息只能写入 open_questions，"
            "不能写进 title 的前缀。有效示例：\"Deliveryline 协作标题规范\"；"
            "无效示例：\"AI 建议：Deliveryline 协作标题规范\"、\"待确认：Deliveryline 协作标题规范\"、"
            "\"标题：Deliveryline 协作标题规范\"、\"展示：Deliveryline 协作标题规范\"。"
            "其他字段信息不足时，可以在该字段正文说明缺口，并同时列入 open_questions。\n\n"
            + json.dumps(payload, ensure_ascii=False)
        )

    @staticmethod
    def _prompt_text(value: str, *, maximum: int = MAX_PROMPT_FIELD_CHARS) -> str:
        normalized = value.strip()
        if len(normalized) <= maximum:
            return normalized
        return normalized[:maximum] + "\n[内容已截断]"

    @staticmethod
    def _linked_sources(requirement: Requirement) -> tuple[list[dict[str, str]], list[dict[str, object]], tuple[str, ...]]:
        raw_content = requirement.original_request_content or ""
        urls = []
        for match in _HTTP_URL_PATTERN.finditer(raw_content):
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
            document_id = match.group(1).lower()
            try:
                document = get_design_document_source(
                    document_id,
                    max_chars=MAX_PROMPT_DOCUMENT_CHARS,
                )
            except (DesignDocumentIndexError, OSError, UnicodeError, ValueError):
                document = None
            if document is None:
                sources.append({"kind": "project_document_unavailable", "label": "项目资料暂不可读取", "url": url})
                prompt_sources.append({
                    "kind": "project_document_unavailable",
                    "url": url,
                    "instruction": "该项目资料当前无法读取。不要臆测正文；将所需补充信息写入 open_questions。",
                })
                continue
            sources.append({"kind": "project_document", "label": document.title, "url": url})
            prompt_sources.append({
                "kind": "project_document",
                "url": url,
                "document_id": document.id,
                "title": document.title,
                "content": document.content,
                "truncated": document.truncated,
            })
        if public_urls:
            prompt_sources.append({
                "kind": "public_pages",
                "urls": public_urls,
                "instruction": "必须先用已授予的 chub capability page-read 命令逐一读取这些公共网页。"
                "只能依据命令返回的正文整理档案；读取失败时不要臆测，写入 open_questions。",
            })
        capability_ids = ("chub.debug_chrome.page.read",) if public_urls else ()
        return sources, prompt_sources, capability_ids

    def _read(self) -> CollaborationState:
        try:
            if not self.path.exists():
                return CollaborationState()
            if self.path.stat().st_size > MAX_STATE_BYTES:
                raise OSError("state too large")
            payload = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(payload, dict):
                raise ValueError("invalid root")
            if payload.get("version") == 1:
                associations = []
                for item in payload.get("requirements", []):
                    if not isinstance(item, dict):
                        continue
                    requirement_id = item.get("requirement_id")
                    session_id = item.get("session_id")
                    if isinstance(requirement_id, str) and requirement_id and isinstance(session_id, str) and session_id:
                        associations.append(RequirementCollaboration(
                            requirement_id=requirement_id,
                            session_id=session_id,
                            replacement_required=True,
                        ))
                return CollaborationState(show_sessions=payload.get("show_sessions") is True, requirements=associations)
            state = CollaborationState.model_validate(payload)
            for association in state.requirements:
                for round_ in association.rounds:
                    if round_.suggestion is None:
                        continue
                    title = round_.suggestion.fields.get("title")
                    if title is not None and self._title_has_presentation_prefix(title.value):
                        round_.status = "failed"
                        round_.error = "AI 返回的标题包含展示或待确认前缀，请再次 AI 协作。"
                        round_.suggestion = None
            return state
        except (OSError, ValueError, json.JSONDecodeError):
            self._state_error = "AI 协作本机状态不可读取。"
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
            raise DeliverylineUnavailable("AI 协作本机状态无法保存。") from exc
        finally:
            temporary.unlink(missing_ok=True)
