from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
from tempfile import NamedTemporaryFile
import threading
from uuid import uuid4

from app.codex.usage_settings import RuntimeSettingsStoreUnavailable
from app.core.response import ApiError
from app.services.weekly_reports import (
    confirm_weekly_report_focus,
    reporting_period,
    weekly_report_focus_confirmed,
    weekly_report_inputs_available,
)


MAX_STATE_BYTES = 64 * 1024
_STAGES = frozenset({"focus", "report"})
_RUNNING_STATUSES = frozenset({"requested", "started", "running"})


@dataclass(frozen=True)
class WeeklyReportGenerationStep:
    stage: str
    session_id: str | None
    task_id: str | None
    status: str
    message: str


class WeeklyReportGenerationService:
    """Run both report-generation stages in one bounded Quick Session per period."""

    def __init__(self, state_file: Path, session_manager, quick_interactions) -> None:
        self._state_file = state_file
        self._session_manager = session_manager
        self._quick_interactions = quick_interactions
        self._lock = threading.RLock()

    def read_current(self) -> dict[str, WeeklyReportGenerationStep]:
        period = reporting_period()
        return {
            stage: self._read_stage(period, stage)
            for stage in ("focus", "report")
        }

    def configuration_ready(self) -> tuple[bool, str | None]:
        if self._read_period_session_id(reporting_period()) is not None:
            return True, None
        try:
            settings = self._read_settings()
        except ApiError as exc:
            return False, exc.message
        if settings.permission_mode == "read-only":
            return False, "当前周报自动化会话为只读权限，无法生成周报产物。"
        return True, None

    def start(self, stage: str, *, source_ip: str) -> WeeklyReportGenerationStep:
        if stage not in _STAGES:
            raise ApiError(404, "weekly_report_stage_not_found", "周报生成步骤不存在。")
        period = reporting_period()
        with self._lock:
            if any(
                self._read_stage(period, candidate).status == "running"
                for candidate in _STAGES
            ):
                raise ApiError(
                    409,
                    "weekly_report_generation_running",
                    "周报生成会话正在执行，请等待当前任务完成。",
                )
            self._require_prerequisites(period, stage)
            session_id = self._read_period_session_id(period)
            created_session = False
            try:
                if session_id is None:
                    settings = self._read_settings()
                    if settings.permission_mode == "read-only":
                        raise ApiError(
                            409,
                            "weekly_report_generation_read_only",
                            "当前周报自动化会话为只读权限，无法生成周报产物。",
                        )
                    with self._quick_interactions.session_creation_guard("quick"):
                        session = self._session_manager.create_session(
                            "chub",
                            settings.permission_mode,
                            settings.model,
                            settings.reasoning_effort,
                            "quick",
                        )
                    session_id = session.id
                    created_session = True
                    self._session_manager.rename_session(
                        session_id,
                        self._session_title(period),
                    )
                prompt = self._prompt(stage, period)
                with self._quick_interactions.session_operation_guard(session_id):
                    task = self._quick_interactions.submit(
                        session_id,
                        prompt,
                        operation_id=f"weekly-report-{stage}-{uuid4().hex}",
                        source_ip=source_ip,
                    )
            except Exception:
                if created_session and session_id is not None:
                    self._session_manager.discard_unstarted_session(session_id)
                raise
            self._save_stage(
                period,
                stage,
                session_id=session_id,
                task_id=task.id,
            )
            return self._read_stage(period, stage)

    def confirm_and_start_report(self, *, source_ip: str) -> WeeklyReportGenerationStep:
        period = reporting_period()
        with self._lock:
            if any(
                self._read_stage(period, candidate).status == "running"
                for candidate in _STAGES
            ):
                raise ApiError(
                    409,
                    "weekly_report_generation_running",
                    "周报生成会话正在执行，请等待当前任务完成。",
                )
            self._require_prerequisites(period, "focus")
            try:
                confirm_weekly_report_focus(period)
            except ValueError as exc:
                raise ApiError(
                    409,
                    "weekly_report_focus_confirmation_failed",
                    str(exc),
                ) from exc
            return self.start("report", source_ip=source_ip)

    def _require_prerequisites(self, period: str, stage: str) -> None:
        if not weekly_report_inputs_available(period):
            raise ApiError(
                409,
                "weekly_report_inputs_not_ready",
                "本期资料尚未完整发布或 Manifest 校验未通过，暂不能开始周报生成。",
            )
        if stage == "report" and not weekly_report_focus_confirmed(period):
            raise ApiError(
                409,
                "weekly_report_focus_not_confirmed",
                "重点确认清单尚未完成维护者确认，暂不能生成正式周报。",
            )

    def _read_settings(self):
        try:
            settings = self._session_manager.runtime_settings_store.read_general()
        except RuntimeSettingsStoreUnavailable as exc:
            raise ApiError(
                503,
                "ai_runtime_settings_unavailable",
                "AI Runtime 通用配置暂时无法读取。",
            ) from exc
        if settings.weekly_report_session.runtime_id != self._session_manager.runtime_id:
            raise ApiError(
                409,
                "weekly_report_runtime_unavailable",
                "当前周报自动化 Runtime 不可用。",
            )
        return settings.weekly_report_session

    def _read_stage(self, period: str, stage: str) -> WeeklyReportGenerationStep:
        period_data = self._period_data(period)
        session_id = period_data.get("session_id")
        stages = period_data.get("stages")
        entry = stages.get(stage) if isinstance(stages, dict) else None
        task_id = entry.get("task_id") if isinstance(entry, dict) else None
        if not isinstance(session_id, str) or not isinstance(task_id, str):
            return WeeklyReportGenerationStep(stage, None, None, "idle", "等待前序步骤完成")
        available_session_id = self._available_session_id(session_id)
        try:
            task = self._quick_interactions.get(task_id)
        except (ApiError, KeyError):
            return WeeklyReportGenerationStep(
                stage, available_session_id, task_id, "failed", "生成会话记录不可用"
            )
        if task.status in _RUNNING_STATUSES:
            return WeeklyReportGenerationStep(
                stage,
                available_session_id,
                task_id,
                "running",
                "已创建生成会话，正在执行",
            )
        if task.status == "succeeded":
            return WeeklyReportGenerationStep(
                stage,
                available_session_id,
                task_id,
                "succeeded",
                "生成会话已完成，正在核对产物",
            )
        return WeeklyReportGenerationStep(
            stage,
            available_session_id,
            task_id,
            "failed",
            task.error or "生成会话未完成，请查看会话结果。",
        )

    def _read_period_session_id(self, period: str) -> str | None:
        session_id = self._period_data(period).get("session_id")
        return self._available_session_id(session_id)

    def _available_session_id(self, session_id: object) -> str | None:
        if not isinstance(session_id, str):
            return None
        try:
            self._session_manager.get_session(session_id)
        except ApiError as exc:
            if exc.code == "codex_session_not_found":
                return None
            raise
        return session_id

    def _period_data(self, period: str) -> dict[str, object]:
        periods = self._load().get("periods")
        entry = periods.get(period) if isinstance(periods, dict) else None
        if not isinstance(entry, dict):
            return {}
        # The old per-step session state is intentionally discarded: one period
        # now owns one workflow Session and its stage task records.
        return entry if isinstance(entry.get("stages"), dict) else {}

    def _load(self) -> dict[str, object]:
        try:
            if self._state_file.is_symlink():
                raise OSError("state file cannot be a symlink")
            data = self._state_file.read_bytes()
        except FileNotFoundError:
            return {"periods": {}}
        except OSError:
            return {"periods": {}}
        if len(data) > MAX_STATE_BYTES:
            return {"periods": {}}
        try:
            value = json.loads(data.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return {"periods": {}}
        return value if isinstance(value, dict) else {"periods": {}}

    def _save_stage(self, period: str, stage: str, *, session_id: str, task_id: str) -> None:
        data = self._load()
        periods = data.setdefault("periods", {})
        if not isinstance(periods, dict):
            periods = data["periods"] = {}
        period_data = periods.get(period)
        if not isinstance(period_data, dict) or not isinstance(
            period_data.get("stages"), dict
        ):
            period_data = periods[period] = {}
        period_data["session_id"] = session_id
        stages = period_data.setdefault("stages", {})
        if not isinstance(stages, dict):
            stages = period_data["stages"] = {}
        stages[stage] = {"task_id": task_id}
        payload = json.dumps(data, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        self._state_file.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        with NamedTemporaryFile(
            dir=self._state_file.parent,
            prefix=f".{self._state_file.name}.",
            suffix=".tmp",
            delete=False,
        ) as file:
            temporary = Path(file.name)
            file.write(payload)
        try:
            os.chmod(temporary, 0o600)
            temporary.replace(self._state_file)
        except OSError:
            temporary.unlink(missing_ok=True)
            raise

    @staticmethod
    def _session_title(period: str) -> str:
        return f"V 国内业务周报 · {period}"

    @staticmethod
    def _prompt(stage: str, period: str) -> str:
        stage_instruction = (
            "仅执行 Stage A：读取本期已发布输入并生成工作重点确认清单。清单业务内容只能包含"
            "“本周需要同步的事项”和“需要维护者确认的重点事项”两个简洁列表；保留最小输入指纹"
            "和维护者确认占位，但不得增加其他业务章节。完成后停止，等待维护者确认。不得生成正式周报，"
            "不得自行填写维护者确认结果或创建有效确认记录。"
            if stage == "focus"
            else "仅执行 Stage B：先读取本期已发布输入、已有重点确认清单和有效确认记录；"
            "确认记录缺失或无效时停止并说明原因。不得重新生成重点确认清单；"
            "生成正式周报、核对记录并完成规定校验。"
        )
        return (
            f"处理 Chub 自动化任务“V 国内业务周报下载”的本期周报工作流，周期为 {period}。"
            f"当前 Session 名称为“V 国内业务周报 · {period}”。使用 generate-weekly-report 技能，"
            f"严格遵循其输入校验、工作区和产物边界。{stage_instruction}"
            "不得打开飞书、不得重新下载资料、不得修改自动化任务状态；"
            "来源资料只读；只在该周期受控工作区读取必要的已有产物，并将新产物写入 output 目录。"
        )
