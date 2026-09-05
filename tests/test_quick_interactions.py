import json
import asyncio
import hashlib
import threading
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from app.ai_session.store import AiSessionStoreUnavailable
from app.codex.models import (
    CodexSession,
    QuickInteractionDeferredRestartContext,
    QuickInteractionOperationContext,
    QuickInteractionTask,
    QuickInteractionWeixinRoute,
    utc_now,
)
from app.codex.quick_interactions import (
    CODEX_QUICK_INTERACTION_INSTRUCTIONS,
    MAX_QUICK_INTERACTION_STATE_BYTES,
    QuickInteractionManager,
    build_task_summary,
)
from app.core.response import ApiError
from app.quick_worker import QuickWorkerServer
from app.quick_worker import WorkerRequestNotSent
from app.services.deferred_restart import DeferredRestartRequest
from app.services.weixin_translation import TRANSLATION_PROMPT


def manager(
    tmp_path: Path,
    completion_notifier=None,
    deferred_restart=None,
    restart_notifier=None,
) -> QuickInteractionManager:
    codex_manager = MagicMock()
    codex_manager.get_session.return_value = CodexSession(
        id="session-1",
        workspace_id="chub",
        workspace_name="Chub",
        cwd=tmp_path,
        codex_session_id="11111111-1111-4111-8111-111111111111",
        status="stopped",
        permission_mode="auto-review",
    )
    codex_manager.has_active_writer.return_value = False
    codex_manager.hook_dir = tmp_path / "hooks"
    quick_interactions = QuickInteractionManager(
        tmp_path / "codex-sessions.json",
        tmp_path / "runtime",
        codex_manager,
        completion_notifier,
        deferred_restart,
        restart_notifier=restart_notifier,
    )
    quick_interactions.worker_settings = SimpleNamespace()
    quick_interactions._recovery_ready = True
    quick_interactions._worker_call = MagicMock(
        side_effect=lambda action, **payload: (
            accepted_worker_task(payload["task"])
            if action == "runtime_task_submit"
            else {"success": True, "data": {}}
        )
    )
    return quick_interactions


def worker_manager(
    tmp_path: Path,
    settings,
    completion_notifier=None,
) -> QuickInteractionManager:
    quick_interactions = manager(
        tmp_path,
        completion_notifier=completion_notifier,
    )
    quick_interactions.worker_settings = settings
    quick_interactions._recovery_ready = False
    return quick_interactions


def accepted_worker_task(submission: dict[str, object]) -> dict[str, object]:
    now = utc_now()
    prompt = submission["prompt"]
    assert isinstance(prompt, str)
    return {
        "success": True,
        "data": {
            "task": {
                "task_id": submission["task_id"],
                "runtime_id": submission["runtime_id"],
                "status": "accepted",
                "prompt_sha256": hashlib.sha256(prompt.encode("utf-8")).hexdigest(),
                "created_at": now.isoformat(),
                "updated_at": now.isoformat(),
                "deadline_at": (now + timedelta(minutes=5)).isoformat(),
            "worker_generation": "generation-1",
            "execution_id": "11111111111111111111111111111111",
                "runner_pid": None,
                "cancellation_requested": False,
                "restart_sensitive": submission.get("restart_sensitive", False),
            }
        },
    }


def test_quick_interaction_timeout_is_configurable(tmp_path: Path) -> None:
    quick_interactions = manager(tmp_path)
    configured = QuickInteractionManager(
        tmp_path / "custom-codex-sessions.json",
        tmp_path / "runtime",
        quick_interactions.codex_manager,
        timeout_seconds=7_200,
    )

    assert quick_interactions.timeout_seconds == 21_600
    assert configured.timeout_seconds == 7_200


def test_model_update_is_serialized_with_quick_session_tasks(tmp_path: Path) -> None:
    quick_interactions = manager(tmp_path)

    quick_interactions.update_session_model("session-1", "gpt-test", "high")

    quick_interactions.codex_manager.update_quick_session_model.assert_called_once_with(
        "session-1",
        "gpt-test",
        "high",
    )


def test_model_update_rejects_running_quick_session(tmp_path: Path) -> None:
    quick_interactions = manager(tmp_path)
    quick_interactions._running_sessions.add("session-1")

    with pytest.raises(ApiError, match="正在执行"):
        quick_interactions.update_session_model("session-1", "gpt-test", "high")

    quick_interactions.codex_manager.update_quick_session_model.assert_not_called()


def test_codex_execution_prompt_adds_delivery_guidance_without_changing_request(
    tmp_path: Path,
) -> None:
    quick_interactions = manager(tmp_path)
    prompt = quick_interactions._codex_execution_prompt("调整页面布局")

    assert prompt.startswith("[用户需求]\n调整页面布局")
    assert prompt.endswith(CODEX_QUICK_INTERACTION_INSTRUCTIONS)
    assert "完成效果" in prompt
    assert "验收方法" in prompt
    assert "只能调用 scripts/chub-web-restart 一次" in prompt


def test_system_upgrade_reset_prevents_late_task_state_rewrite(tmp_path: Path) -> None:
    quick_interactions = manager(tmp_path)

    quick_interactions.reset_for_system_upgrade(force=True)
    reset_state = quick_interactions.path.read_text(encoding="utf-8")
    quick_interactions._write()

    assert quick_interactions.path.read_text(encoding="utf-8") == reset_state


def test_result_suffix_stays_within_persisted_limit(tmp_path: Path) -> None:
    quick_interactions = manager(tmp_path)

    result = quick_interactions._append_result_suffix(
        "a" * 100_000,
        "本次处理已完成，即将重启 Chub 服务。",
    )

    assert len(result.encode("utf-8")) <= 100_000
    assert result.endswith("本次处理已完成，即将重启 Chub 服务。")


def test_session_title_uses_first_user_request_line(tmp_path: Path) -> None:
    quick_interactions = manager(tmp_path)

    assert quick_interactions._session_title("\n  修复首页会话标题  \n补充测试") == "修复首页会话标题"
    assert quick_interactions._session_title("\n\n") == "快速交互"


def test_task_summary_is_stable_bounded_and_redacted() -> None:
    assert build_task_summary("\n检查设备状态。\n补充说明") == "检查设备状态。"
    sensitive_prompts = (
        "使用 Bearer secret-token 检查接口",
        "webhook=https://example.test/private 执行通知",
        "Authorization: Bearer private-value 检查接口",
        "Cookie: session=private-value 检查页面",
    )
    for prompt in sensitive_prompts:
        summary = build_task_summary(prompt)
        assert len(summary) <= 27
        assert "secret-token" not in summary
        assert "private" not in summary
        assert "session=" not in summary
    assert build_task_summary("任务" * 100) == "任务" * 13 + "…"
    assert build_task_summary("检查 Ubuntu 服务状态") == "检查 Ubuntu 服务状态"


def test_submit_allows_new_session_and_prepares_managed_profile(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    quick_interactions = manager(tmp_path)
    session = quick_interactions.codex_manager.get_session.return_value
    session.codex_session_id = None
    thread = MagicMock()
    monkeypatch.setattr(
        "app.codex.quick_interactions.threading.Thread",
        MagicMock(return_value=thread),
    )

    task = quick_interactions.submit(
        session.id,
        "执行第一条任务",
        operation_id="operation-1",
        source_ip="127.0.0.1",
    )

    assert task.status == "requested"
    assert task.prompt == "执行第一条任务"
    assert task.summary == "执行第一条任务"
    quick_interactions.codex_manager.prepare_quick_interaction.assert_called_once_with()
    quick_interactions.codex_manager.set_initial_quick_interaction_title.assert_called_once_with(
        session.id,
        "执行第一条任务",
    )
    thread.start.assert_called_once_with()


def test_submit_accepts_configured_weixin_summary_limit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    quick_interactions = manager(tmp_path)
    session = quick_interactions.codex_manager.get_session.return_value
    thread = MagicMock()
    monkeypatch.setattr(
        "app.codex.quick_interactions.threading.Thread",
        MagicMock(return_value=thread),
    )

    task = quick_interactions.submit(
        session.id,
        "任" * 60,
        operation_id="operation-summary-limit",
        source_ip="127.0.0.1",
        summary_max_chars=40,
    )

    assert task.summary == "任" * 39 + "…"


def test_submit_thread_start_failure_rolls_back_registration(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    quick_interactions = manager(tmp_path)
    session = quick_interactions.codex_manager.get_session.return_value
    thread = MagicMock()
    thread.start.side_effect = RuntimeError("thread unavailable")
    monkeypatch.setattr(
        "app.codex.quick_interactions.threading.Thread",
        MagicMock(return_value=thread),
    )

    with pytest.raises(ApiError) as error:
        quick_interactions.submit(
            session.id,
            "执行任务",
            operation_id="operation-start-failure",
            source_ip="127.0.0.1",
        )

    assert error.value.code == "quick_interaction_start_failed"
    assert quick_interactions.is_running(session.id) is False
    assert quick_interactions._tasks == {}


def test_submit_observer_failure_preserves_observer_and_cancel_errors(
    tmp_path: Path,
) -> None:
    quick_interactions = manager(tmp_path)
    quick_interactions._start_worker_observer = MagicMock(
        side_effect=RuntimeError("observer unavailable")
    )

    def worker_call(action: str, **payload):
        if action == "runtime_task_submit":
            return accepted_worker_task(payload["task"])
        if action == "task_cancel":
            raise OSError("cancel unavailable")
        raise AssertionError(action)

    quick_interactions._worker_call = worker_call

    with pytest.raises(ApiError) as error:
        quick_interactions.submit(
            "session-1",
            "执行任务",
            operation_id="operation-observer-failure",
            source_ip="127.0.0.1",
        )

    assert error.value.code == "quick_worker_observer_unavailable"
    assert "observer unavailable" in error.value.message
    assert "cancel unavailable" in error.value.message
    failed = next(iter(quick_interactions._tasks.values()))
    assert failed.error_source == "chub"
    assert "observer unavailable" in (failed.error or "")
    assert "cancel unavailable" in (failed.error or "")


def test_submit_persistence_failure_rolls_back_registration(
    tmp_path: Path,
) -> None:
    quick_interactions = manager(tmp_path)
    session = quick_interactions.codex_manager.get_session.return_value
    quick_interactions._write = MagicMock(side_effect=OSError("write failed"))

    with pytest.raises(OSError):
        quick_interactions.submit(
            session.id,
            "执行任务",
            operation_id="operation-write-failure",
            source_ip="127.0.0.1",
        )

    assert quick_interactions.is_running(session.id) is False
    assert quick_interactions._tasks == {}


def test_isolated_worker_maps_page_weixin_and_translation_to_one_protocol(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    quick_interactions = manager(tmp_path)
    quick_interactions.worker_settings = SimpleNamespace()
    quick_interactions.codex_manager.get_session.return_value.codex_session_id = (
        "11111111-1111-4111-8111-111111111111"
    )
    submissions: list[dict[str, object]] = []

    def worker_call(action: str, **payload: object) -> dict[str, object]:
        assert action == "runtime_task_submit"
        task = payload["task"]
        assert isinstance(task, dict)
        submissions.append(task)
        return accepted_worker_task(task)

    quick_interactions._worker_call = MagicMock(side_effect=worker_call)
    thread = MagicMock()
    monkeypatch.setattr(
        "app.codex.quick_interactions.threading.Thread",
        MagicMock(return_value=thread),
    )
    session = quick_interactions.codex_manager.get_session.return_value
    session.codex_session_id = "11111111-1111-4111-8111-111111111111"
    session.model = "gpt-page"
    session.reasoning_effort = "high"

    page = quick_interactions.submit(
        session.id,
        "page",
        operation_id="page-operation",
        source_ip="127.0.0.1",
    )
    quick_interactions._active_task_ids.clear()
    quick_interactions._running_sessions.clear()
    route = QuickInteractionWeixinRoute(
        account_id="weixin-account",
        recipient="owner@im.wechat",
    )
    session.model = "gpt-next"
    session.reasoning_effort = "low"
    session.permission_mode = "read-only"
    weixin = quick_interactions.submit(
        session.id,
        "weixin",
        operation_id="weixin-operation",
        source_ip="127.0.0.1",
        notification_route=route,
    )
    quick_interactions._active_task_ids.clear()
    quick_interactions._running_sessions.clear()
    translation = quick_interactions.submit(
        session.id,
        "translation",
        operation_id="translation-operation",
        source_ip="127.0.0.1",
        notification_route=route,
        kind="translation",
        model="gpt-translation",
        reasoning_effort="high",
    )

    assert [item["task_kind"] for item in submissions] == [
        "standard",
        "weixin",
        "translation",
    ]
    assert all(task.worker_task_id for task in (page, weixin, translation))
    assert submissions[2]["queue_key"] == "weixin-translation"
    assert all(item["runtime_id"] == "codex" for item in submissions)
    assert submissions[2]["permission_profile"] == "read-only"
    assert submissions[2]["model"] == "gpt-translation"
    assert submissions[2]["reasoning_effort"] == "high"
    assert submissions[0]["permission_profile"] == "auto-review"
    assert submissions[0]["model"] == "gpt-page"
    assert submissions[0]["reasoning_effort"] == "high"
    assert submissions[1]["permission_profile"] == "read-only"
    assert submissions[1]["model"] == "gpt-next"
    assert submissions[1]["reasoning_effort"] == "low"
    assert thread.start.call_count == 3


def test_isolated_worker_unavailable_fails_without_web_runner(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    quick_interactions = manager(tmp_path)
    quick_interactions.worker_settings = SimpleNamespace()
    quick_interactions.codex_manager.get_session.return_value.codex_session_id = (
        "11111111-1111-4111-8111-111111111111"
    )
    quick_interactions._worker_call = MagicMock(
        side_effect=WorkerRequestNotSent("worker down")
    )
    monkeypatch.setattr("app.codex.quick_interactions.time.sleep", lambda _delay: None)
    with pytest.raises(ApiError) as error:
        quick_interactions.submit(
            "session-1",
            "do not fall back",
            operation_id="operation-1",
            source_ip="127.0.0.1",
        )

    assert error.value.code == "quick_worker_unavailable"
    assert not hasattr(quick_interactions, "_processes")
    assert quick_interactions._tasks == {}


def test_discovered_session_outside_fixed_workspace_is_submitted_to_worker(
    tmp_path: Path,
) -> None:
    quick_interactions = manager(tmp_path)
    quick_interactions.codex_manager.get_session.return_value.workspace_id = "runtime-session"
    quick_interactions._start_worker_observer = MagicMock()

    task = quick_interactions.submit(
        "session-1",
        "submit restored session",
        operation_id="operation-1",
        source_ip="127.0.0.1",
    )

    assert task.status == "requested"
    submitted = quick_interactions._worker_call.call_args.kwargs["task"]
    assert submitted["workspace_id"] == "runtime-session"


def test_worker_connection_failure_retries_before_rejecting_submission(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    quick_interactions = manager(tmp_path)
    session = quick_interactions.codex_manager.get_session.return_value
    thread = MagicMock()
    monkeypatch.setattr(
        "app.codex.quick_interactions.threading.Thread",
        MagicMock(return_value=thread),
    )
    sleep = MagicMock()
    monkeypatch.setattr("app.codex.quick_interactions.time.sleep", sleep)
    attempts = 0

    def submit_after_worker_starts(action: str, **payload: object) -> dict[str, object]:
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            raise WorkerRequestNotSent("worker starting")
        assert action == "runtime_task_submit"
        task_payload = payload["task"]
        assert isinstance(task_payload, dict)
        return accepted_worker_task(task_payload)

    quick_interactions._worker_call = MagicMock(side_effect=submit_after_worker_starts)

    task = quick_interactions.submit(
        session.id,
        "wait for the worker",
        operation_id="operation-1",
        source_ip="127.0.0.1",
    )

    assert task.status == "requested"
    assert quick_interactions._worker_call.call_count == 3
    assert sleep.call_args_list == [((0.2,), {}), ((0.5,), {})]
    thread.start.assert_called_once_with()


def test_failed_sensitive_submission_rechecks_deferred_restart(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    deferred_restart = MagicMock()
    quick_interactions = manager(tmp_path, deferred_restart=deferred_restart)
    quick_interactions._worker_call = MagicMock(
        side_effect=WorkerRequestNotSent("worker down")
    )
    monkeypatch.setattr("app.codex.quick_interactions.time.sleep", lambda _delay: None)

    with pytest.raises(ApiError):
        quick_interactions.submit(
            "session-1",
            "do not fall back",
            operation_id="operation-1",
            source_ip="127.0.0.1",
        )

    deferred_restart.maybe_schedule.assert_called_once_with()


def test_isolated_worker_submit_response_loss_adopts_accepted_task(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    quick_interactions = manager(tmp_path)
    quick_interactions.worker_settings = SimpleNamespace()
    quick_interactions.codex_manager.get_session.return_value.codex_session_id = (
        "11111111-1111-4111-8111-111111111111"
    )
    calls = iter(
        [
            OSError("submit response lost"),
            OSError("retry response lost"),
            None,
        ]
    )

    def worker_call(_action: str, **_payload: object) -> dict[str, object]:
        result = next(calls)
        if isinstance(result, Exception):
            raise result
        if result is None:
            return accepted_worker_task(_payload["task"])
        return result

    quick_interactions._worker_call = MagicMock(side_effect=worker_call)
    reconciler_started = threading.Event()
    reconciler_release = threading.Event()

    def start_reconciler(task, session, submission) -> None:
        reconciler_started.set()
        assert quick_interactions.is_running(session.id) is True
        reconciler_release.wait(1)
        quick_interactions._reconcile_uncertain_submission(
            task.id,
            session,
            submission,
        )

    monkeypatch.setattr(
        quick_interactions,
        "_start_uncertain_submission_reconciler",
        start_reconciler,
    )
    observer = MagicMock()
    monkeypatch.setattr(quick_interactions, "_start_worker_observer", observer)
    reconciler_release.set()

    submitted_task = quick_interactions.submit(
        "session-1",
        "uncertain submission",
        operation_id="operation-1",
        source_ip="127.0.0.1",
    )

    assert reconciler_started.is_set()
    assert submitted_task.submission_verifying is False
    task = next(iter(quick_interactions._tasks.values()))
    assert task.status == "requested"
    assert task.submission_verifying is False
    assert task.error is None
    assert quick_interactions.is_running("session-1") is True
    assert [call.args[0] for call in quick_interactions._worker_call.call_args_list] == [
        "runtime_task_submit",
        "runtime_task_submit",
        "runtime_task_submit",
    ]
    observer.assert_called_once_with(task, quick_interactions.codex_manager.get_session.return_value, "uncertain submission")


def test_uncertain_submission_uses_persisted_recovery_after_bounded_probe(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    quick_interactions = manager(tmp_path)
    monkeypatch.setattr(
        "app.codex.quick_interactions.UNCERTAIN_SUBMISSION_RETRY_SECONDS",
        0,
    )
    initial_calls = iter(
        [OSError("response lost"), OSError("retry lost")]
        + [OSError("verification unavailable")] * 8
    )
    def initial_worker_call(_action: str, **_payload: object) -> dict[str, object]:
        result = next(initial_calls)
        raise result

    quick_interactions._worker_call = MagicMock(side_effect=initial_worker_call)
    monkeypatch.setattr(
        quick_interactions,
        "_start_uncertain_submission_reconciler",
        lambda task, session, submission: quick_interactions._reconcile_uncertain_submission(
            task.id,
            session,
            submission,
        ),
    )

    task = quick_interactions.submit(
        "session-1",
        "bounded verification",
        operation_id="operation-bounded-verification",
        source_ip="127.0.0.1",
    )

    assert task.submission_verifying is True
    assert task.id not in quick_interactions._submitting_task_ids
    assert quick_interactions.is_running("session-1") is True

    quick_interactions._worker_call = MagicMock(
        side_effect=lambda action, **payload: (
            {
                "success": False,
                "error": {"code": "worker_task_not_found"},
            }
            if action == "task_get"
            else accepted_worker_task(payload["task"])
        )
    )
    quick_interactions._reconcile_worker_task(task.id)

    recovered = quick_interactions.get(task.id)
    assert recovered.submission_verifying is False
    assert recovered.error is None
    assert quick_interactions.is_running("session-1") is True
    assert [call.args[0] for call in quick_interactions._worker_call.call_args_list] == [
        "task_get",
        "runtime_task_submit",
    ]


def test_isolated_worker_accepts_wrapped_8000_character_prompt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    quick_interactions = manager(tmp_path)
    quick_interactions.worker_settings = SimpleNamespace()
    quick_interactions.codex_manager.get_session.return_value.codex_session_id = (
        "11111111-1111-4111-8111-111111111111"
    )
    quick_interactions._worker_call = MagicMock(
        side_effect=lambda _action, **payload: accepted_worker_task(payload["task"])
    )
    thread = MagicMock()
    monkeypatch.setattr(
        "app.codex.quick_interactions.threading.Thread",
        MagicMock(return_value=thread),
    )

    task = quick_interactions.submit(
        "session-1",
        "𠮷" * 8_000,
        operation_id="operation-long-prompt",
        source_ip="127.0.0.1",
    )

    submission = quick_interactions._worker_call.call_args.kwargs["task"]
    assert task.worker_task_id is not None
    assert len(submission["prompt"]) > 8_000
    assert len(submission["prompt"].encode("utf-8")) < 48 * 1024


def test_isolated_worker_accepts_json_escaped_8000_character_translation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    quick_interactions = manager(tmp_path)
    quick_interactions.worker_settings = SimpleNamespace()
    quick_interactions.codex_manager.get_session.return_value.codex_session_id = (
        "11111111-1111-4111-8111-111111111111"
    )
    quick_interactions.codex_manager.get_session.return_value.permission_mode = "read-only"
    quick_interactions._worker_call = MagicMock(
        side_effect=lambda _action, **payload: accepted_worker_task(payload["task"])
    )
    thread = MagicMock()
    monkeypatch.setattr(
        "app.codex.quick_interactions.threading.Thread",
        MagicMock(return_value=thread),
    )
    original = "\x00" * 8_000
    prompt = TRANSLATION_PROMPT.format(
        source_json=json.dumps(original, ensure_ascii=False)
    )

    task = quick_interactions.submit(
        "session-1",
        prompt,
        operation_id="operation-escaped-translation",
        source_ip="127.0.0.1",
        kind="translation",
        translation_original=original,
    )

    submission = quick_interactions._worker_call.call_args.kwargs["task"]
    assert task.worker_task_id is not None
    assert task.prompt == original
    assert len(submission["prompt"]) > 48_000
    assert len(submission["prompt"].encode("utf-8")) < 56 * 1024


def test_isolated_worker_success_merges_deferred_restart_request(
    tmp_path: Path,
) -> None:
    deferred_restart = MagicMock()
    deferred_restart.request.return_value = SimpleNamespace(
        operation_id="operation-1:restart",
        created=True,
    )
    quick_interactions = manager(tmp_path, deferred_restart=deferred_restart)
    task = QuickInteractionTask(
        id="task-1",
        worker_task_id="qw-1750000000000-00000000000000000000000000000001",
        session_id="session-1",
        prompt="修改配置并重启",
        status="running",
        created_at=utc_now(),
        updated_at=utc_now(),
    )
    quick_interactions._tasks[task.id] = task
    quick_interactions._active_task_ids.add(task.id)
    other = QuickInteractionTask(
        id="task-2",
        session_id="session-2",
        prompt="仍在修改 Chub",
        status="running",
        restart_sensitive=True,
        created_at=utc_now(),
        updated_at=utc_now(),
    )
    quick_interactions._tasks[other.id] = other
    quick_interactions._active_task_ids.add(other.id)
    quick_interactions._operations[task.id] = ("operation-1", "127.0.0.1")
    request_path = (
        quick_interactions.restart_request_dir / f"{task.worker_task_id}.request"
    )
    request_path.touch(mode=0o600)
    snapshot = SimpleNamespace(
        status="succeeded",
        result="功能已完成。",
        error=None,
        error_code=None,
    )

    quick_interactions._finish_from_worker_snapshot(task.id, task, snapshot)

    finished = quick_interactions.get(task.id)
    assert finished.result == (
        "功能已完成。\n\n本次处理已完成，即将重启 Chub 服务。"
    )
    assert finished.deferred_restart_status == "pending"
    deferred_restart.request.assert_called_once_with(
        operation_id="operation-1:restart",
        task_id="task-1",
        source_ip="127.0.0.1",
    )
    assert not request_path.exists()


def test_worker_error_within_worker_limit_survives_web_state_reload(
    tmp_path: Path,
) -> None:
    quick_interactions = manager(tmp_path)
    task = QuickInteractionTask(
        id="task-long-error",
        worker_task_id="qw-1750000000000-00000000000000000000000000000001",
        session_id="session-1",
        prompt="执行任务",
        status="running",
        created_at=utc_now(),
        updated_at=utc_now(),
    )
    quick_interactions._tasks[task.id] = task
    error_text = "上游错误 " + "x" * 3_500
    snapshot = SimpleNamespace(
        status="failed",
        result=None,
        error=error_text,
        error_source="runtime",
        error_code="runner_failed",
    )

    quick_interactions._finish_from_worker_snapshot(task.id, task, snapshot)

    finished = quick_interactions.get(task.id)
    assert finished.error == error_text
    reloaded = manager(tmp_path)
    assert reloaded.get(task.id).error == error_text


@pytest.mark.anyio
async def test_isolated_business_adapter_runs_page_weixin_and_translation_via_worker(
    settings,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    native_id = "11111111-1111-4111-8111-111111111111"
    monkeypatch.setenv("FAKE_CODEX_SESSION_ID", native_id)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    executable = tmp_path / "fake-codex"
    executable.write_text(
        """#!/usr/bin/env python3
import json
import os
import sys
from pathlib import Path
args = sys.argv[1:]
result_path = Path(args[args.index("--output-last-message") + 1])
native_id = args[args.index("resume") + 1] if "resume" in args else os.environ["FAKE_CODEX_SESSION_ID"]
prompt = sys.stdin.read()
print(json.dumps({"type": "thread.started", "thread_id": native_id}), flush=True)
if "SOURCE_JSON:" in prompt:
    result = "润色：\\n清晰中文\\n\\nEnglish：\\nClear English"
else:
    result = f"result:{prompt}"
result_path.write_text(result, encoding="utf-8")
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
    try:
        quick_interactions = manager(tmp_path)
        quick_interactions.worker_settings = settings
        del quick_interactions._worker_call
        session = quick_interactions.codex_manager.get_session.return_value
        session.workspace_id = "isolated"
        session.cwd = workspace
        session.codex_session_id = native_id
        quick_interactions.codex_manager.bind_quick_interaction_native_session = MagicMock()
        route = QuickInteractionWeixinRoute(
            account_id="weixin-account",
            recipient="owner@im.wechat",
        )
        submissions = (
            ("page-session", "page", None, "standard"),
            ("weixin-session", "weixin", route, "standard"),
            (
                "translation-session",
                TRANSLATION_PROMPT.format(source_json='"translation"'),
                route,
                "translation",
            ),
        )
        completed = []
        for session_id, prompt, notification_route, kind in submissions:
            session.id = session_id
            session.permission_mode = "read-only" if kind == "translation" else "auto-review"
            task = await asyncio.to_thread(
                quick_interactions.submit,
                session_id,
                prompt,
                operation_id=f"operation-{kind}-{session_id}",
                source_ip="127.0.0.1",
                notification_route=notification_route,
                kind=kind,
            )
            deadline = asyncio.get_running_loop().time() + 5
            while asyncio.get_running_loop().time() < deadline:
                snapshot = quick_interactions.get(task.id)
                if snapshot.status not in {"requested", "running"}:
                    completed.append(snapshot)
                    break
                await asyncio.sleep(0.02)
            else:
                raise AssertionError("Worker-backed business task did not finish")

        assert [task.status for task in completed] == ["succeeded"] * 3
        assert [
            server.task_manager.get(task.worker_task_id or "").status
            for task in completed
        ] == ["succeeded"] * 3
        translation_spec = server.task_manager._read_spec(
            completed[2].worker_task_id or ""
        )
        assert translation_spec.permission_profile == "read-only"
        assert completed[2].result == "润色：\n清晰中文\n\nEnglish：\nClear English"
        quick_interactions.close()
    finally:
        await server.close()


@pytest.mark.anyio
async def test_isolated_web_manager_restart_recovers_running_worker_task(
    settings,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    native_id = "11111111-1111-4111-8111-111111111111"
    monkeypatch.setenv("FAKE_CODEX_SESSION_ID", native_id)
    release_path = tmp_path / "release-worker-task"
    monkeypatch.setenv("FAKE_CODEX_RELEASE_PATH", str(release_path))
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    executable = tmp_path / "fake-codex-recovery"
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
release_path = Path(os.environ["FAKE_CODEX_RELEASE_PATH"])
prompt = sys.stdin.read()
print(json.dumps({"type": "thread.started", "thread_id": native_id}), flush=True)
while not release_path.exists():
    time.sleep(0.01)
result_path.write_text(f"recovered:{prompt}", encoding="utf-8")
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

    def codex_manager() -> MagicMock:
        value = MagicMock()
        value.get_session.return_value = CodexSession(
            id="session-1",
            workspace_id="isolated",
            workspace_name="Isolated",
            cwd=workspace,
            codex_session_id=native_id,
            status="stopped",
            permission_mode="read-only",
        )
        value.has_active_writer.return_value = False
        value.hook_dir = tmp_path / "hooks"
        return value

    def new_manager() -> QuickInteractionManager:
        return QuickInteractionManager(
            tmp_path / "codex-sessions.json",
            tmp_path / "runtime",
            codex_manager(),
            worker_settings=settings,
        )

    first = new_manager()
    second = None
    try:
        await asyncio.to_thread(first.start_worker_reconciliation)
        task = await asyncio.to_thread(
            first.submit,
            "session-1",
            "跨 Web 恢复",
            operation_id="operation-recovery",
            source_ip="127.0.0.1",
        )
        deadline = asyncio.get_running_loop().time() + 2
        while asyncio.get_running_loop().time() < deadline:
            if first.get(task.id).status == "running":
                break
            await asyncio.sleep(0.02)
        assert first.is_running("session-1") is True
        generation = server.generation
        first.close()

        second = new_manager()
        assert second.get(task.id).status in {"requested", "running"}
        assert second.is_running("session-1") is True
        await asyncio.to_thread(second.start_worker_reconciliation)
        with second.session_operation_guard("other-session"):
            pass
        release_path.write_text("release", encoding="utf-8")
        deadline = asyncio.get_running_loop().time() + 3
        while asyncio.get_running_loop().time() < deadline:
            recovered = second.get(task.id)
            delivery_path = (
                server.task_manager.tasks_dir
                / (task.worker_task_id or "")
                / "delivery.json"
            )
            if recovered.status == "succeeded" and delivery_path.is_file():
                break
            await asyncio.sleep(0.02)
        else:
            raise AssertionError("Recovered Web manager did not merge the Worker result")

        assert recovered.result is not None
        assert recovered.result.count("recovered:") == 1
        assert second.is_running("session-1") is False
        assert server.generation == generation
        assert delivery_path.is_file()
    finally:
        first.close()
        if second is not None:
            second.close()
        await server.close()


@pytest.mark.parametrize(
    "result",
    [
        "润色：\n\nEnglish：\nEnglish",
        "润色：\n中文\n\nEnglish：\n",
        "前言\n润色：\n中文\n\nEnglish：\nEnglish",
    ],
)
def test_translation_result_validation_rejects_invalid_shapes(result: str) -> None:
    assert QuickInteractionManager._valid_translation_result(result) is False


def test_translation_result_validation_accepts_exact_nonempty_sections() -> None:
    assert QuickInteractionManager._valid_translation_result(
        "润色：\n清晰中文\n\nEnglish：\nClear English"
    )


def test_quick_interaction_completion_notification_is_independent(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    notifier = MagicMock(return_value=SimpleNamespace(status="sent", error=None))
    quick_interactions = manager(tmp_path, completion_notifier=notifier)
    finished_handler = MagicMock()
    quick_interactions.set_task_finished_handler(finished_handler)
    task = QuickInteractionTask(
        id="task-1",
        session_id="session-1",
        prompt="检查状态",
        notification_route="weixin-task",
        status="running",
        created_at=utc_now(),
        updated_at=utc_now(),
    )
    quick_interactions._tasks[task.id] = task
    quick_interactions._operations[task.id] = ("operation-1", "127.0.0.1")

    class ImmediateThread:
        def __init__(self, *, target, args, daemon):
            self.target = target
            self.args = args

        def start(self) -> None:
            self.target(*self.args)

    monkeypatch.setattr(
        "app.codex.quick_interactions.threading.Thread",
        ImmediateThread,
    )

    quick_interactions._finish(task.id, "succeeded", "完成")

    finished = quick_interactions.get(task.id)
    assert finished.status == "succeeded"
    assert finished.result == "完成"
    assert finished.notification_status == "sent"
    finished_handler.assert_called_once()
    assert finished_handler.call_args.args[0].status == "succeeded"
    notification_task, notification_route = notifier.call_args.args
    assert notification_task.id == finished.id
    assert notification_task.notification_status == "sending"
    assert notification_route is None


def test_claim_cleanup_failure_does_not_block_completion_or_notification(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    notifier = MagicMock(return_value=SimpleNamespace(status="sent", error=None))
    quick_interactions = manager(tmp_path, completion_notifier=notifier)
    finished_handler = MagicMock()
    quick_interactions.set_task_finished_handler(finished_handler)
    task = QuickInteractionTask(
        id="task-claim-cleanup",
        worker_task_id="qw-1750000000000-11111111111111111111111111111111",
        session_id="session-1",
        prompt="检查状态",
        notification_route="weixin-task",
        status="running",
        created_at=utc_now(),
        updated_at=utc_now(),
    )
    quick_interactions._tasks[task.id] = task
    quick_interactions._operations[task.id] = ("operation-1", "127.0.0.1")
    quick_interactions.codex_manager.clear_quick_native_claim.side_effect = OSError(
        "Session state is temporarily unavailable"
    )

    class ImmediateThread:
        def __init__(self, *, target, args, daemon):
            self.target = target
            self.args = args

        def start(self) -> None:
            self.target(*self.args)

    monkeypatch.setattr(
        "app.codex.quick_interactions.threading.Thread",
        ImmediateThread,
    )

    quick_interactions._finish(task.id, "succeeded", "完成")

    finished = quick_interactions.get(task.id)
    assert finished.status == "succeeded"
    assert finished.notification_status == "sent"
    finished_handler.assert_called_once()
    assert quick_interactions._pending_native_claim_clears == {
        (task.session_id, task.worker_task_id)
    }

    quick_interactions.codex_manager.clear_quick_native_claim.side_effect = None
    quick_interactions._retry_pending_native_claim_clears()

    assert quick_interactions._pending_native_claim_clears == set()


def test_notification_failure_does_not_change_task_result(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    notifier = MagicMock(
        return_value=SimpleNamespace(status="failed", error="微信通知未送达。")
    )
    quick_interactions = manager(tmp_path, completion_notifier=notifier)
    task = QuickInteractionTask(
        id="task-1",
        session_id="session-1",
        prompt="检查状态",
        notification_route="weixin-task",
        status="running",
        created_at=utc_now(),
        updated_at=utc_now(),
    )
    quick_interactions._tasks[task.id] = task
    quick_interactions._operations[task.id] = ("operation-1", "127.0.0.1")

    class ImmediateThread:
        def __init__(self, *, target, args, daemon):
            self.target = target
            self.args = args

        def start(self) -> None:
            self.target(*self.args)

    monkeypatch.setattr(
        "app.codex.quick_interactions.threading.Thread",
        ImmediateThread,
    )

    quick_interactions._finish(task.id, "succeeded", "完成")

    finished = quick_interactions.get(task.id)
    assert finished.status == "succeeded"
    assert finished.result == "完成"
    assert finished.notification_status == "failed"
    assert finished.notification_error == "微信通知未送达。"


def test_weixin_task_status_snapshot_is_read_only_and_route_scoped(
    tmp_path: Path,
) -> None:
    quick_interactions = manager(tmp_path)
    route = QuickInteractionWeixinRoute(
        account_id="weixin-account",
        recipient="owner@im.wechat",
    )
    running = QuickInteractionTask(
        id="running-task",
        session_id="session-1",
        prompt="检查设备状态并核对当前运行任务列表中的完整标题是否正确展示",
        summary="检查设备状态并核对当前运行任务列表…",
        status="running",
        notification_route="weixin-task",
        created_at=utc_now(),
        updated_at=utc_now(),
    )
    other = running.model_copy(update={"id": "other-task"})
    quick_interactions._tasks = {running.id: running, other.id: other}
    quick_interactions._notification_routes = {
        running.id: route,
        other.id: QuickInteractionWeixinRoute(
            account_id="weixin-account",
            recipient="other@im.wechat",
        ),
    }

    ended = QuickInteractionTask(
        id="ended-task",
        session_id="session-2",
        prompt="已完成",
        summary="已完成",
        status="succeeded",
        result="完成",
        notification_status="failed",
        notification_route="weixin-task",
        created_at=utc_now(),
        updated_at=utc_now(),
    )
    quick_interactions._tasks.update({running.id: running, ended.id: ended})
    quick_interactions._notification_routes[ended.id] = route

    before = ended.model_copy(deep=True)
    snapshot = quick_interactions.weixin_task_status_snapshot(route)

    assert snapshot.running_count == 1
    assert snapshot.pending_notification_count == 0
    assert snapshot.failed_notification_count == 1
    assert snapshot.running_tasks == (
        ("session-1", "检查设备状态并核对当前运行任务列表中的完整标题是否正确展示"),
    )
    assert quick_interactions.get(ended.id) == before


def test_running_standard_task_summaries_include_all_standard_tasks(
    tmp_path: Path,
) -> None:
    quick_interactions = manager(tmp_path)
    web_task = QuickInteractionTask(
        id="web-task",
        session_id="session-web",
        prompt="检查 Web 任务摘要",
        summary="检查 Web 任务摘要",
        status="running",
        notification_route="default",
        created_at=utc_now(),
        updated_at=utc_now(),
    )
    weixin_task = web_task.model_copy(
        update={
            "id": "weixin-task",
            "session_id": "session-weixin",
            "summary": "检查微信任务摘要",
            "notification_route": "weixin-task",
        }
    )
    translation_task = web_task.model_copy(
        update={
            "id": "translation-task",
            "session_id": "session-translation",
            "kind": "translation",
        }
    )
    completed_task = web_task.model_copy(
        update={
            "id": "completed-task",
            "session_id": "session-completed",
            "status": "succeeded",
        }
    )
    quick_interactions._tasks = {
        task.id: task
        for task in (web_task, weixin_task, translation_task, completed_task)
    }

    snapshot = quick_interactions.running_standard_task_summaries()

    assert snapshot.running_tasks == (
        ("session-web", "检查 Web 任务摘要"),
        ("session-weixin", "检查微信任务摘要"),
    )
    assert snapshot.running_count == 0
    assert snapshot.pending_notification_count == 0
    assert snapshot.failed_notification_count == 0


def test_weixin_notification_route_is_private_and_survives_restart(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first = manager(tmp_path)
    thread = MagicMock()
    monkeypatch.setattr(
        "app.codex.quick_interactions.threading.Thread",
        MagicMock(return_value=thread),
    )
    route = QuickInteractionWeixinRoute(
        account_id="weixin-account",
        recipient="owner@im.wechat",
    )

    task = first.submit(
        "session-1",
        "检查设备",
        operation_id="operation-1",
        source_ip="100.64.0.1",
        notification_route=route,
    )

    public_payload = task.model_dump(mode="json")
    assert public_payload["notification_route"] == "weixin-task"
    assert "weixin-account" not in json.dumps(public_payload)
    persisted = json.loads(first.path.read_text(encoding="utf-8"))
    assert persisted[0]["_notification_route"] == route.model_dump(mode="json")

    reloaded = manager(tmp_path)
    assert reloaded.get(task.id).notification_route == "weixin-task"
    assert reloaded._notification_routes[task.id] == route

def test_restart_rejects_active_task_without_worker_identity(tmp_path: Path) -> None:
    state = tmp_path / "quick-interactions.json"
    task = QuickInteractionTask(
        id="task-1",
        session_id="session-1",
        prompt="检查状态",
        status="running",
        created_at=utc_now(),
        updated_at=utc_now(),
    )
    state.write_text(
        json.dumps([task.model_dump(mode="json")]),
        encoding="utf-8",
    )

    quick_interactions = manager(tmp_path)

    assert quick_interactions.get("task-1").status == "running"
    assert quick_interactions.recovery_error is None
    quick_interactions.start_worker_reconciliation()
    assert quick_interactions.recovery_ready is False
    assert quick_interactions.recovery_error is not None


def test_restart_marks_incomplete_notification_failed(tmp_path: Path) -> None:
    state = tmp_path / "quick-interactions.json"
    task = QuickInteractionTask(
        id="task-1",
        session_id="session-1",
        prompt="检查状态",
        status="succeeded",
        result="完成",
        notification_status="sending",
        created_at=utc_now(),
        updated_at=utc_now(),
    )
    state.write_text(json.dumps([task.model_dump(mode="json")]), encoding="utf-8")

    quick_interactions = manager(tmp_path)

    recovered = quick_interactions.get("task-1")
    assert recovered.status == "succeeded"
    assert recovered.notification_status == "skipped"
    assert recovered.notification_error == "页面任务结果仅在 Chub 快速交互页面展示。"


def test_worker_restart_preserves_active_task_and_pending_notification(
    settings,
    tmp_path: Path,
) -> None:
    state = tmp_path / "quick-interactions.json"
    task = QuickInteractionTask(
        id="task-1",
        worker_task_id="qw-1750000000000-11111111111111111111111111111111",
        session_id="session-1",
        prompt="检查状态",
        status="running",
        notification_status="pending",
        created_at=utc_now(),
        updated_at=utc_now(),
    )
    state.write_text(json.dumps([task.model_dump(mode="json")]), encoding="utf-8")

    codex_manager = MagicMock()
    recovered = QuickInteractionManager(
        tmp_path / "codex-sessions.json",
        tmp_path / "runtime",
        codex_manager,
        MagicMock(),
        worker_settings=settings,
    )

    assert recovered.get(task.id).status == "running"
    assert recovered.get(task.id).notification_status == "skipped"
    assert recovered.is_running(task.session_id) is True
    codex_manager.recover_interrupted_quick_interaction.assert_not_called()
    codex_manager.register_quick_native_claim.assert_called_once_with(
        task.session_id,
        task.worker_task_id,
    )


def test_worker_recovery_treats_null_delivery_marker_as_unconfirmed(
    settings,
    tmp_path: Path,
) -> None:
    state = tmp_path / "quick-interactions.json"
    task = QuickInteractionTask(
        id="task-1",
        worker_task_id="qw-1750000000000-11111111111111111111111111111111",
        session_id="session-1",
        prompt="检查状态",
        status="running",
        created_at=utc_now(),
        updated_at=utc_now(),
    )
    serialized = task.model_dump(mode="json")
    serialized["_operation_context"] = {
        "operation_id": "operation-1",
        "source_ip": "127.0.0.1",
    }
    serialized["_worker_delivery_confirmed"] = None
    state.write_text(json.dumps([serialized]), encoding="utf-8")

    recovered = QuickInteractionManager(
        tmp_path / "codex-sessions.json",
        tmp_path / "runtime",
        MagicMock(),
        MagicMock(),
        worker_settings=settings,
    )

    assert recovered._local_state_error is None
    persisted = json.loads(state.read_text(encoding="utf-8"))
    assert "_worker_delivery_confirmed" not in persisted[0]


def test_worker_claim_restore_conflict_is_local_and_reconciles_task(
    settings,
    tmp_path: Path,
) -> None:
    state = tmp_path / "quick-interactions.json"
    task = QuickInteractionTask(
        id="task-claim-conflict",
        worker_task_id="qw-1750000000000-11111111111111111111111111111111",
        session_id="session-1",
        prompt="检查状态",
        status="running",
        created_at=utc_now(),
        updated_at=utc_now(),
    )
    serialized = task.model_dump(mode="json")
    serialized["_operation_context"] = {
        "operation_id": "operation-1",
        "source_ip": "127.0.0.1",
    }
    state.write_text(json.dumps([serialized]), encoding="utf-8")
    codex_manager = MagicMock()
    codex_manager.get_session.return_value = CodexSession(
        id="session-1",
        workspace_id="chub",
        workspace_name="Chub",
        cwd=tmp_path,
        codex_session_id=None,
        status="stopped",
        permission_mode="auto-review",
    )
    codex_manager.register_quick_native_claim.side_effect = ApiError(
        409,
        "quick_interaction_native_session_claim_active",
        "A different Quick Worker task is already claiming this Session.",
    )
    quick_interactions = QuickInteractionManager(
        tmp_path / "codex-sessions.json",
        tmp_path / "runtime",
        codex_manager,
        worker_settings=settings,
    )
    now = utc_now()
    view = {
        "task_id": task.worker_task_id,
        "runtime_id": "codex",
        "status": "succeeded",
        "prompt_sha256": "a" * 64,
        "created_at": now.isoformat(),
        "updated_at": now.isoformat(),
        "deadline_at": (now + timedelta(minutes=5)).isoformat(),
        "worker_generation": "generation-1",
        "execution_id": "11111111111111111111111111111111",
        "runner_pid": None,
        "cancellation_requested": False,
        "result": "不应写回",
        "error": None,
        "error_source": None,
        "error_code": None,
        "exit_code": 0,
        "native_session_id": "11111111-1111-4111-8111-111111111111",
    }

    def worker_call(action: str, **_payload):
        if action == "task_list":
            return {"success": True, "data": {"tasks": []}}
        if action == "task_get":
            return {"success": True, "data": {"task": view}}
        if action == "task_acknowledge":
            return {"success": True, "data": {"delivery": {}}}
        raise AssertionError(action)

    quick_interactions._worker_call = worker_call

    quick_interactions._reconcile_worker_once(initial=True)

    finished = quick_interactions.get(task.id)
    assert quick_interactions._local_state_error is None
    assert quick_interactions.recovery_ready is True
    assert finished.status == "failed"
    assert finished.result is None
    assert "could not be restored" in (finished.error or "")
    codex_manager.bind_quick_interaction_native_session.assert_not_called()


def test_worker_claim_restore_store_unavailable_is_local(
    settings,
    tmp_path: Path,
) -> None:
    task = QuickInteractionTask(
        id="task-store-unavailable",
        worker_task_id="qw-1750000000000-11111111111111111111111111111111",
        session_id="session-1",
        prompt="检查状态",
        status="running",
        created_at=utc_now(),
        updated_at=utc_now(),
    )
    serialized = task.model_dump(mode="json")
    serialized["_operation_context"] = {
        "operation_id": "operation-1",
        "source_ip": "127.0.0.1",
    }
    (tmp_path / "quick-interactions.json").write_text(
        json.dumps([serialized]),
        encoding="utf-8",
    )
    codex_manager = MagicMock()
    codex_manager.register_quick_native_claim.side_effect = AiSessionStoreUnavailable(
        "AI Session 状态文件与当前内存状态不一致。"
    )

    quick_interactions = QuickInteractionManager(
        tmp_path / "quick-interactions.json",
        tmp_path / "runtime",
        codex_manager,
        worker_settings=settings,
    )

    assert quick_interactions._local_state_error is None
    assert quick_interactions._native_claim_restore_errors[task.id] == (
        "ai_session_store_unavailable"
    )


def test_worker_recovery_barrier_fails_quick_session_writes_closed(
    settings,
    tmp_path: Path,
) -> None:
    quick_interactions = worker_manager(tmp_path, settings)

    with pytest.raises(ApiError) as error:
        with quick_interactions.session_operation_guard("session-1"):
            pass
    assert error.value.status_code == 503
    assert error.value.code == "quick_worker_recovery_unavailable"
    with quick_interactions.terminal_input_guard("session-1") as allowed:
        assert allowed is True


def test_worker_reconciliation_logs_maintenance_disconnect_without_traceback(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    quick_interactions = manager(tmp_path)
    quick_interactions.set_maintenance_window_checker(lambda: True)

    with caplog.at_level("INFO", logger="hub.codex.quick_interactions"):
        quick_interactions._record_reconciliation_failure(
            WorkerRequestNotSent("worker socket unavailable")
        )

    assert quick_interactions.recovery_ready is False
    record = next(
        item
        for item in caplog.records
        if "temporarily unavailable during maintenance" in item.message
    )
    assert record.exc_info is None


def test_worker_reconciliation_keeps_traceback_for_unexpected_disconnect(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    quick_interactions = manager(tmp_path)

    with caplog.at_level("WARNING", logger="hub.codex.quick_interactions"):
        quick_interactions._record_reconciliation_failure(
            WorkerRequestNotSent("worker socket unavailable")
        )

    record = next(
        item
        for item in caplog.records
        if "reconciliation became unavailable" in item.message
    )
    assert record.exc_info is not None


def test_terminal_guards_do_not_require_quick_worker_recovery(
    settings,
    tmp_path: Path,
) -> None:
    quick_interactions = worker_manager(tmp_path, settings)

    with quick_interactions.session_creation_guard("terminal"):
        pass
    with quick_interactions.terminal_access_guard("terminal-session"):
        pass
    with quick_interactions.terminal_input_guard("terminal-session") as allowed:
        assert allowed is True


def test_delete_guard_allows_idle_session_when_quick_worker_is_unavailable(
    settings,
    tmp_path: Path,
) -> None:
    quick_interactions = worker_manager(tmp_path, settings)
    task = QuickInteractionTask(
        id="task-1",
        session_id="session-1",
        prompt="已完成任务",
        status="succeeded",
        created_at=utc_now(),
        updated_at=utc_now(),
    )
    quick_interactions._tasks[task.id] = task

    with quick_interactions.destructive_operation_guard("session-1"):
        quick_interactions.remove_session_tasks("session-1")

    assert task.id not in quick_interactions._tasks


def test_delete_guard_keeps_active_session_blocked_when_quick_worker_is_unavailable(
    settings,
    tmp_path: Path,
) -> None:
    quick_interactions = worker_manager(tmp_path, settings)
    quick_interactions._running_sessions.add("session-1")

    with pytest.raises(ApiError) as error:
        with quick_interactions.destructive_operation_guard("session-1"):
            pass

    assert error.value.status_code == 409
    assert error.value.code == "quick_interaction_in_progress"


def test_delete_guard_keeps_session_blocked_when_local_worker_state_is_invalid(
    settings,
    tmp_path: Path,
) -> None:
    quick_interactions = worker_manager(tmp_path, settings)
    quick_interactions._local_state_error = "Quick Worker task state is invalid"

    with pytest.raises(ApiError) as error:
        with quick_interactions.destructive_operation_guard("session-1"):
            pass

    assert error.value.status_code == 503
    assert error.value.code == "quick_worker_recovery_unavailable"


def test_quick_session_creation_requires_ready_worker(
    settings,
    tmp_path: Path,
) -> None:
    quick_interactions = worker_manager(tmp_path, settings)
    quick_interactions._recovery_ready = True
    quick_interactions._worker_call = MagicMock(return_value={"success": False})

    with pytest.raises(ApiError) as error:
        with quick_interactions.session_creation_guard("quick"):
            pass

    assert error.value.status_code == 503
    assert error.value.code == "quick_worker_unavailable"
    with quick_interactions.session_creation_guard("terminal"):
        pass


def test_worker_reconciliation_merges_once_and_acknowledges_after_persistence(
    settings,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    quick_interactions = worker_manager(tmp_path, settings)
    task = QuickInteractionTask(
        id="task-1",
        worker_task_id="qw-1750000000000-22222222222222222222222222222222",
        session_id="session-1",
        prompt="检查状态",
        status="running",
        created_at=utc_now(),
        updated_at=utc_now(),
    )
    quick_interactions._tasks[task.id] = task
    quick_interactions._active_task_ids.add(task.id)
    quick_interactions._running_sessions.add(task.session_id)
    quick_interactions._task_done_events[task.id] = threading.Event()
    quick_interactions._operations[task.id] = ("operation-1", "127.0.0.1")
    quick_interactions._write()
    now = utc_now()
    summary = {
        "task_id": task.worker_task_id,
        "runtime_id": "codex",
        "status": "succeeded",
        "prompt_sha256": "a" * 64,
        "session_id": task.session_id,
        "task_kind": "standard",
        "native_session_id": "11111111-1111-4111-8111-111111111111",
        "created_at": now.isoformat(),
        "updated_at": now.isoformat(),
    }
    view = {
        "task_id": task.worker_task_id,
        "runtime_id": "codex",
        "status": "succeeded",
        "prompt_sha256": "a" * 64,
        "created_at": now.isoformat(),
        "updated_at": now.isoformat(),
        "deadline_at": (now + timedelta(minutes=5)).isoformat(),
        "worker_generation": "generation-1",
        "execution_id": "11111111111111111111111111111111",
        "runner_pid": None,
        "cancellation_requested": False,
        "result": "恢复后的唯一结果",
        "error": None,
        "error_code": None,
        "exit_code": 0,
    }
    calls = []

    def worker_call(action: str, **_payload):
        calls.append(action)
        if action == "task_list":
            return {"success": True, "data": {"tasks": []}}
        if action == "task_get":
            return {"success": True, "data": {"task": view}}
        if action == "task_acknowledge":
            persisted = json.loads(quick_interactions.path.read_text(encoding="utf-8"))
            assert persisted[0]["result"] == "恢复后的唯一结果"
            return {"success": True, "data": {"delivery": {}}}
        raise AssertionError(action)

    monkeypatch.setattr(quick_interactions, "_worker_call", worker_call)
    quick_interactions.codex_manager.get_session.return_value = (
        quick_interactions.codex_manager.get_session.return_value
    )

    quick_interactions._reconcile_worker_once(initial=True)
    quick_interactions._reconcile_worker_once(initial=False)

    finished = quick_interactions.get(task.id)
    assert finished.status == "succeeded"
    assert finished.result == "恢复后的唯一结果"
    assert calls.count("task_acknowledge") == 1
    assert quick_interactions.is_running(task.session_id) is False
    assert quick_interactions.recovery_ready is True


def test_worker_reconciliation_not_found_delivers_failure_notification(
    settings,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    notifier = MagicMock(return_value=SimpleNamespace(status="sent", error=None))
    quick_interactions = worker_manager(
        tmp_path,
        settings,
        completion_notifier=notifier,
    )
    task = QuickInteractionTask(
        id="task-1",
        worker_task_id="qw-1750000000000-22222222222222222222222222222222",
        session_id="session-1",
        prompt="检查状态",
        status="running",
        notification_route="weixin-task",
        created_at=utc_now(),
        updated_at=utc_now(),
    )
    route = QuickInteractionWeixinRoute(
        account_id="weixin-account",
        recipient="owner@im.wechat",
    )
    quick_interactions._tasks[task.id] = task
    quick_interactions._notification_routes[task.id] = route
    quick_interactions._active_task_ids.add(task.id)
    quick_interactions._running_sessions.add(task.session_id)
    quick_interactions._task_done_events[task.id] = threading.Event()
    quick_interactions._operations[task.id] = ("operation-1", "127.0.0.1")
    quick_interactions._operation_contexts[task.id] = QuickInteractionOperationContext(
        operation_id="operation-1",
        source_ip="127.0.0.1",
    )

    class ImmediateThread:
        def __init__(self, *, target, args, daemon, name=None):
            self.target = target
            self.args = args

        def start(self) -> None:
            self.target(*self.args)

    monkeypatch.setattr(
        "app.codex.quick_interactions.threading.Thread",
        ImmediateThread,
    )
    monkeypatch.setattr(
        quick_interactions,
        "_worker_call",
        MagicMock(
            return_value={
                "success": False,
                "error": {"code": "worker_task_not_found"},
            }
        ),
    )

    quick_interactions._reconcile_worker_task(task.id)

    failed = quick_interactions.get(task.id)
    assert failed.status == "failed"
    assert failed.notification_status == "sent"
    assert quick_interactions.is_running(task.session_id) is False
    notifier.assert_called_once()


def test_worker_recovery_ready_waits_for_recovery_handler(
    settings,
    tmp_path: Path,
) -> None:
    quick_interactions = worker_manager(tmp_path, settings)
    quick_interactions._worker_call = MagicMock(
        return_value={"success": True, "data": {"tasks": []}}
    )

    def fail_recovery_handler() -> None:
        assert quick_interactions.recovery_ready is False
        raise OSError("translation recovery failed")

    quick_interactions.set_recovery_ready_handler(fail_recovery_handler)

    with pytest.raises(OSError, match="translation recovery failed"):
        quick_interactions._reconcile_worker_once(initial=True)

    assert quick_interactions.recovery_ready is False


def test_worker_recovery_retries_deferred_restart_after_barrier_opens(
    settings,
    tmp_path: Path,
) -> None:
    quick_interactions = worker_manager(tmp_path, settings)
    quick_interactions._worker_call = MagicMock(
        return_value={"success": True, "data": {"tasks": []}}
    )
    deferred_restart = MagicMock()
    deferred_restart.maybe_schedule.side_effect = (
        lambda: quick_interactions.recovery_ready
    )
    quick_interactions.deferred_restart = deferred_restart

    quick_interactions._reconcile_worker_once(initial=True)

    assert quick_interactions.recovery_ready is True
    deferred_restart.maybe_schedule.assert_called_once_with()


def test_worker_reconciliation_converges_native_session_conflict(
    settings,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    quick_interactions = worker_manager(tmp_path, settings)
    task = QuickInteractionTask(
        id="task-conflict",
        worker_task_id="qw-1750000000000-22222222222222222222222222222222",
        session_id="session-1",
        prompt="检查状态",
        status="running",
        created_at=utc_now(),
        updated_at=utc_now(),
    )
    quick_interactions._tasks[task.id] = task
    quick_interactions._active_task_ids.add(task.id)
    quick_interactions._running_sessions.add(task.session_id)
    now = utc_now()
    view = {
        "task_id": task.worker_task_id,
        "runtime_id": "codex",
        "status": "succeeded",
        "prompt_sha256": "a" * 64,
        "created_at": now.isoformat(),
        "updated_at": now.isoformat(),
        "deadline_at": (now + timedelta(minutes=5)).isoformat(),
        "worker_generation": "generation-1",
        "execution_id": "11111111111111111111111111111111",
        "runner_pid": None,
        "cancellation_requested": False,
        "result": "已完成",
        "error": None,
        "error_source": None,
        "error_code": None,
        "exit_code": 0,
        "native_session_id": "11111111-1111-4111-8111-111111111111",
    }
    calls: list[str] = []

    def worker_call(action: str, **_payload):
        calls.append(action)
        if action == "task_list":
            return {"success": True, "data": {"tasks": []}}
        if action == "task_get":
            return {"success": True, "data": {"task": view}}
        if action == "task_acknowledge":
            return {"success": True, "data": {"delivery": {}}}
        raise AssertionError(action)

    monkeypatch.setattr(quick_interactions, "_worker_call", worker_call)
    quick_interactions.codex_manager.bind_quick_interaction_native_session.side_effect = (
        ApiError(
            409,
            "quick_interaction_native_session_conflict",
            "Codex 原生 Session 已归属于其他 Chub Session。",
        )
    )

    quick_interactions._reconcile_worker_once(initial=True)

    finished = quick_interactions.get(task.id)
    assert finished.status == "failed"
    assert finished.error == "Codex 原生 Session 已归属于其他 Chub Session。"
    assert quick_interactions.is_running(task.session_id) is False
    assert quick_interactions.recovery_ready is True
    assert calls == ["task_list", "task_get", "task_acknowledge"]


def test_worker_reconciliation_allows_translation_native_session_rotation(
    settings,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    quick_interactions = worker_manager(tmp_path, settings)
    quick_interactions.codex_manager.get_session.return_value = CodexSession(
        id="session-1",
        workspace_id="weixin-translation",
        workspace_name="微信文本优化与翻译",
        cwd=tmp_path,
        codex_session_id="old-native-session",
        status="stopped",
        permission_mode="read-only",
    )
    task = QuickInteractionTask(
        id="task-translation-rotation",
        worker_task_id="qw-1750000000000-33333333333333333333333333333333",
        session_id="session-1",
        prompt="优化文本",
        kind="translation",
        status="running",
        created_at=utc_now(),
        updated_at=utc_now(),
    )
    quick_interactions._tasks[task.id] = task
    quick_interactions._active_task_ids.add(task.id)
    quick_interactions._running_sessions.add(task.session_id)
    now = utc_now()
    new_native_session_id = "22222222-2222-4222-8222-222222222222"
    view = {
        "task_id": task.worker_task_id,
        "runtime_id": "codex",
        "status": "succeeded",
        "prompt_sha256": "b" * 64,
        "created_at": now.isoformat(),
        "updated_at": now.isoformat(),
        "deadline_at": (now + timedelta(minutes=5)).isoformat(),
        "worker_generation": "generation-1",
        "runner_pid": None,
        "cancellation_requested": False,
        "result": "润色：\n优化后的文本\n\nEnglish：\nPolished text",
        "error": None,
        "error_source": None,
        "error_code": None,
        "exit_code": 0,
        "native_session_id": new_native_session_id,
    }
    calls: list[str] = []

    def worker_call(action: str, **_payload):
        calls.append(action)
        if action == "task_list":
            return {"success": True, "data": {"tasks": []}}
        if action == "task_get":
            return {"success": True, "data": {"task": view}}
        if action == "task_acknowledge":
            return {"success": True, "data": {"delivery": {}}}
        raise AssertionError(action)

    monkeypatch.setattr(quick_interactions, "_worker_call", worker_call)

    quick_interactions._reconcile_worker_once(initial=True)

    finished = quick_interactions.get(task.id)
    assert finished.status == "succeeded"
    assert finished.result == "润色：\n优化后的文本\n\nEnglish：\nPolished text"
    quick_interactions.codex_manager.bind_quick_interaction_native_session.assert_called_once_with(
        task.session_id,
        new_native_session_id,
    )
    assert quick_interactions.is_running(task.session_id) is False
    assert quick_interactions.recovery_ready is True
    assert calls == ["task_list", "task_get", "task_acknowledge"]


def test_resident_reconciliation_does_not_race_worker_submission(
    settings,
    tmp_path: Path,
) -> None:
    quick_interactions = worker_manager(tmp_path, settings)
    quick_interactions._recovery_ready = True
    quick_interactions._resident_reconciler_started = True
    quick_interactions.codex_manager.get_session.return_value.codex_session_id = (
        "11111111-1111-4111-8111-111111111111"
    )
    submit_entered = threading.Event()
    submit_release = threading.Event()
    submitted: list[QuickInteractionTask] = []
    errors: list[BaseException] = []

    def worker_call(action: str, **payload):
        if action == "runtime_task_submit":
            submit_entered.set()
            assert submit_release.wait(1)
            return accepted_worker_task(payload["task"])
        if action == "task_list":
            return {"success": True, "data": {"tasks": []}}
        raise AssertionError(action)

    quick_interactions._worker_call = worker_call

    def submit() -> None:
        try:
            submitted.append(
                quick_interactions.submit(
                    "session-1",
                    "提交竞态",
                    operation_id="operation-race",
                    source_ip="127.0.0.1",
                )
            )
        except BaseException as exc:  # pragma: no cover - asserted below
            errors.append(exc)

    thread = threading.Thread(target=submit)
    thread.start()
    assert submit_entered.wait(1), errors

    quick_interactions._reconcile_worker_once(initial=False)
    task = next(iter(quick_interactions._tasks.values()))
    assert task.status == "requested"
    assert task.id in quick_interactions._submitting_task_ids
    assert task.id not in quick_interactions._worker_delivery_confirmed

    submit_release.set()
    thread.join(timeout=1)
    assert thread.is_alive() is False
    assert errors == []
    assert submitted == [task]
    assert task.id not in quick_interactions._submitting_task_ids


@pytest.mark.parametrize(
    "payload",
    [
        "not-json",
        json.dumps({"tasks": []}),
        json.dumps([{"id": "broken"}]),
    ],
)
def test_worker_recovery_fails_closed_on_invalid_web_state(
    settings,
    tmp_path: Path,
    payload: str,
) -> None:
    state = tmp_path / "quick-interactions.json"
    state.write_text(payload, encoding="utf-8")
    original = state.read_text(encoding="utf-8")
    recovered = QuickInteractionManager(
        tmp_path / "codex-sessions.json",
        tmp_path / "runtime",
        MagicMock(),
        worker_settings=settings,
    )
    recovered._worker_call = MagicMock(
        return_value={"success": True, "data": {"tasks": []}}
    )

    with pytest.raises(OSError):
        recovered._reconcile_worker_once(initial=True)

    assert recovered.recovery_ready is False
    assert state.read_text(encoding="utf-8") == original


def test_load_discards_legacy_pinned_state(tmp_path: Path) -> None:
    task = QuickInteractionTask(
        id="legacy-task",
        session_id="session-1",
        prompt="旧记录",
        status="succeeded",
        result="完成",
        created_at=utc_now(),
        updated_at=utc_now(),
    )
    payload = task.model_dump(mode="json")
    payload["pinned_at"] = utc_now().isoformat()
    payload["weixin_session_slot"] = 3
    payload["weixin_session_title"] = "旧名称"
    (tmp_path / "quick-interactions.json").write_text(
        json.dumps([payload]),
        encoding="utf-8",
    )

    recovered = manager(tmp_path)

    assert recovered.get(task.id).id == task.id
    persisted = json.loads(recovered.path.read_text(encoding="utf-8"))
    assert "pinned_at" not in persisted[0]
    assert "weixin_session_slot" not in persisted[0]
    assert "weixin_session_title" not in persisted[0]


@pytest.mark.parametrize("invalid_kind", ["non_utf8", "oversized", "symlink"])
def test_worker_recovery_fails_closed_on_unsafe_web_state_file(
    settings,
    tmp_path: Path,
    invalid_kind: str,
) -> None:
    state = tmp_path / "quick-interactions.json"
    if invalid_kind == "non_utf8":
        state.write_bytes(b"\xff")
    elif invalid_kind == "oversized":
        with state.open("wb") as state_file:
            state_file.seek(MAX_QUICK_INTERACTION_STATE_BYTES)
            state_file.write(b"x")
    else:
        target = tmp_path / "state-target.json"
        target.write_text("[]", encoding="utf-8")
        state.symlink_to(target)

    recovered = QuickInteractionManager(
        tmp_path / "codex-sessions.json",
        tmp_path / "runtime",
        MagicMock(),
        worker_settings=settings,
    )

    with pytest.raises(OSError):
        recovered._reconcile_worker_once(initial=True)


def test_worker_recovery_rejects_mismatched_task_identity(
    settings,
    tmp_path: Path,
) -> None:
    quick_interactions = worker_manager(tmp_path, settings)
    task = QuickInteractionTask(
        id="task-1",
        worker_task_id="qw-1750000000000-33333333333333333333333333333333",
        session_id="session-1",
        prompt="身份校验",
        status="running",
        created_at=utc_now(),
        updated_at=utc_now(),
    )
    now = utc_now()
    quick_interactions._tasks[task.id] = task
    quick_interactions._worker_call = MagicMock(
        return_value={
            "success": True,
            "data": {
                "task": {
                    "task_id": "qw-1750000000000-44444444444444444444444444444444",
                    "runtime_id": "codex",
                    "status": "running",
                    "prompt_sha256": "a" * 64,
                    "created_at": now.isoformat(),
                    "updated_at": now.isoformat(),
                    "deadline_at": (now + timedelta(minutes=5)).isoformat(),
                    "worker_generation": "generation-1",
                    "runner_pid": 123,
                    "cancellation_requested": False,
                }
            },
        }
    )

    with pytest.raises(OSError, match="mismatched task metadata"):
        quick_interactions._reconcile_worker_task(task.id)


def test_worker_operation_log_projection_is_persisted_and_idempotent(
    settings,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    quick_interactions = worker_manager(tmp_path, settings)
    task = QuickInteractionTask(
        id="task-1",
        worker_task_id="qw-1750000000000-55555555555555555555555555555555",
        session_id="session-1",
        prompt="日志恢复",
        status="timed_out",
        error="已超时",
        created_at=utc_now(),
        updated_at=utc_now(),
    )
    quick_interactions._tasks[task.id] = task
    quick_interactions._operation_contexts[task.id] = (
        QuickInteractionOperationContext(
            operation_id="operation-log",
            source_ip="127.0.0.1",
        )
    )
    quick_interactions._operations[task.id] = ("operation-log", "127.0.0.1")
    logged: list[str] = []
    monkeypatch.setattr(
        "app.codex.quick_interactions.write_operation",
        lambda **payload: logged.append(payload["status"]),
    )

    quick_interactions._log_status(task.id, task.status, task.session_id)
    quick_interactions._log_status(task.id, task.status, task.session_id)

    assert logged == ["failed"]
    persisted = json.loads(quick_interactions.path.read_text(encoding="utf-8"))
    assert persisted[0]["_operation_context"]["logged_statuses"] == ["failed"]


def test_worker_reconciliation_recovers_missing_started_operation_log(
    settings,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    quick_interactions = worker_manager(tmp_path, settings)
    task = QuickInteractionTask(
        id="task-1",
        worker_task_id="qw-1750000000000-77777777777777777777777777777777",
        session_id="session-1",
        prompt="启动日志恢复",
        status="running",
        created_at=utc_now(),
        updated_at=utc_now(),
    )
    quick_interactions._tasks[task.id] = task
    quick_interactions._operation_contexts[task.id] = (
        QuickInteractionOperationContext(
            operation_id="operation-started",
            source_ip="127.0.0.1",
            logged_statuses=("requested",),
        )
    )
    quick_interactions._operations[task.id] = ("operation-started", "127.0.0.1")
    now = utc_now()
    quick_interactions._worker_call = MagicMock(
        return_value={
            "success": True,
            "data": {
                "task": {
                    "task_id": task.worker_task_id,
                    "runtime_id": "codex",
                    "status": "running",
                    "prompt_sha256": "a" * 64,
                    "created_at": now.isoformat(),
                    "updated_at": now.isoformat(),
                    "deadline_at": (now + timedelta(minutes=5)).isoformat(),
                    "worker_generation": "generation-1",
                    "runner_pid": 123,
                    "cancellation_requested": False,
                }
            },
        }
    )
    logged: list[str] = []
    monkeypatch.setattr(
        "app.codex.quick_interactions.write_operation",
        lambda **payload: logged.append(payload["status"]),
    )

    quick_interactions._reconcile_worker_task(task.id)
    quick_interactions._reconcile_worker_task(task.id)

    assert logged == ["started"]


@pytest.mark.parametrize("missing_metadata", ["route", "operation"])
def test_worker_recovery_fails_closed_on_missing_private_delivery_metadata(
    settings,
    tmp_path: Path,
    missing_metadata: str,
) -> None:
    task = QuickInteractionTask(
        id="task-1",
        worker_task_id="qw-1750000000000-66666666666666666666666666666666",
        session_id="session-1",
        prompt="私有交付元数据",
        status="running",
        notification_route="weixin-task",
        created_at=utc_now(),
        updated_at=utc_now(),
    )
    payload = task.model_dump(mode="json")
    if missing_metadata != "route":
        payload["_notification_route"] = {
            "account_id": "account",
            "recipient": "owner@im.wechat",
        }
    if missing_metadata != "operation":
        payload["_operation_context"] = {
            "operation_id": "operation-private",
            "source_ip": "127.0.0.1",
        }
    (tmp_path / "quick-interactions.json").write_text(
        json.dumps([payload]),
        encoding="utf-8",
    )

    recovered = QuickInteractionManager(
        tmp_path / "codex-sessions.json",
        tmp_path / "runtime",
        MagicMock(),
        worker_settings=settings,
    )

    with pytest.raises(OSError):
        recovered._reconcile_worker_once(initial=True)


def test_list_for_session_returns_latest_first(tmp_path: Path) -> None:
    quick_interactions = manager(tmp_path)
    older = QuickInteractionTask(
        id="older",
        session_id="session-1",
        prompt="较早",
        status="succeeded",
        result="完成",
        created_at=utc_now(),
        updated_at=utc_now(),
    )
    newer = older.model_copy(
        update={"id": "newer", "prompt": "较新", "created_at": utc_now()}
    )
    quick_interactions._tasks = {newer.id: newer, older.id: older}

    tasks = quick_interactions.list_for_session("session-1")

    assert [task.id for task in tasks] == ["newer", "older"]
    assert tasks[0].prompt == "较新"


def test_remove_session_tasks_cleans_persisted_task_and_sidecar_state(
    tmp_path: Path,
) -> None:
    quick_interactions = manager(tmp_path)
    task = QuickInteractionTask(
        id="task-1",
        session_id="session-1",
        prompt="已完成任务",
        status="succeeded",
        created_at=utc_now(),
        updated_at=utc_now(),
    )
    other = task.model_copy(update={"id": "task-2", "session_id": "session-2"})
    quick_interactions._tasks = {task.id: task, other.id: other}
    quick_interactions._notification_routes[task.id] = MagicMock()
    quick_interactions._deferred_restart_contexts[task.id] = MagicMock()
    quick_interactions._operation_contexts[task.id] = MagicMock()
    quick_interactions._operations[task.id] = ("operation-1", "127.0.0.1")
    quick_interactions._worker_delivery_confirmed.add(task.id)
    quick_interactions._cancelled_task_ids.add(task.id)

    quick_interactions.remove_session_tasks("session-1")

    assert quick_interactions._tasks == {other.id: other}
    assert task.id not in quick_interactions._notification_routes
    assert task.id not in quick_interactions._deferred_restart_contexts
    assert task.id not in quick_interactions._operation_contexts
    assert task.id not in quick_interactions._operations
    assert task.id not in quick_interactions._worker_delivery_confirmed
    assert task.id not in quick_interactions._cancelled_task_ids
    persisted = json.loads(quick_interactions.path.read_text(encoding="utf-8"))
    assert [item["id"] for item in persisted] == ["task-2"]


def test_list_for_session_can_return_timeline_order(tmp_path: Path) -> None:
    quick_interactions = manager(tmp_path)
    base = utc_now()
    tasks = [
        QuickInteractionTask(
            id="older",
            session_id="session-1",
            prompt="较早记录",
            status="succeeded",
            result="完成",
            created_at=base,
            updated_at=base,
        ),
        QuickInteractionTask(
            id="latest",
            session_id="session-1",
            prompt="最新记录",
            status="succeeded",
            result="完成",
            created_at=base + timedelta(minutes=2),
            updated_at=base + timedelta(minutes=2),
        ),
        QuickInteractionTask(
            id="middle",
            session_id="session-1",
            prompt="中间记录",
            status="succeeded",
            result="完成",
            created_at=base + timedelta(minutes=1),
            updated_at=base + timedelta(minutes=1),
        ),
    ]
    quick_interactions._tasks = {task.id: task for task in tasks}

    listed = quick_interactions.list_for_session(
        "session-1",
        order="timeline",
    )

    assert [task.id for task in listed] == ["latest", "middle", "older"]


def test_active_sessions_reports_only_running_session_ids(tmp_path: Path) -> None:
    quick_interactions = manager(tmp_path)
    task = QuickInteractionTask(
        id="active",
        session_id="session-1",
        prompt="检查状态",
        status="running",
        created_at=utc_now(),
        updated_at=utc_now(),
    )
    quick_interactions._tasks = {task.id: task}
    quick_interactions._running_sessions.add("session-1")

    active = quick_interactions.active_sessions()

    assert active == {"session-1": task.updated_at}


def test_is_running_reports_session_input_lock(tmp_path: Path) -> None:
    quick_interactions = manager(tmp_path)

    assert quick_interactions.is_running("session-1") is False
    quick_interactions._running_sessions.add("session-1")
    assert quick_interactions.is_running("session-1") is True


def test_terminal_input_guard_serializes_session_operations(tmp_path: Path) -> None:
    quick_interactions = manager(tmp_path)
    entered = threading.Event()

    def enter_session_operation() -> None:
        with quick_interactions.session_operation_guard("session-1"):
            entered.set()

    with quick_interactions.terminal_input_guard("session-1") as allowed:
        assert allowed is True
        worker = threading.Thread(target=enter_session_operation)
        worker.start()
        assert entered.wait(0.05) is False

    worker.join(timeout=1)
    assert entered.is_set()


def test_terminal_input_guard_rejects_input_during_quick_interaction(
    tmp_path: Path,
) -> None:
    quick_interactions = manager(tmp_path)
    quick_interactions._running_sessions.add("session-1")

    with quick_interactions.terminal_input_guard("session-1") as allowed:
        assert allowed is False


def test_local_history_retains_at_most_thirty_tasks(tmp_path: Path) -> None:
    quick_interactions = manager(tmp_path)
    tasks = [
        QuickInteractionTask(
            id=f"task-{index}",
            session_id="session-1",
            prompt=f"任务 {index}",
            status="succeeded",
            result="完成",
            created_at=utc_now(),
            updated_at=utc_now(),
        )
        for index in range(31)
    ]
    quick_interactions._tasks = {task.id: task for task in tasks}

    quick_interactions._write()

    assert len(quick_interactions._tasks) == 30
    persisted = json.loads(quick_interactions.path.read_text(encoding="utf-8"))
    assert len(persisted) == 30


def test_local_history_never_prunes_active_tasks(tmp_path: Path) -> None:
    quick_interactions = manager(tmp_path)
    active = [
        QuickInteractionTask(
            id=f"active-{index}",
            session_id=f"session-{index}",
            prompt=f"运行任务 {index}",
            status="running",
            created_at=utc_now(),
            updated_at=utc_now(),
        )
        for index in range(2)
    ]
    completed = [
        QuickInteractionTask(
            id=f"completed-{index}",
            session_id="session-completed",
            prompt=f"完成任务 {index}",
            status="succeeded",
            result="完成",
            created_at=utc_now(),
            updated_at=utc_now(),
        )
        for index in range(30)
    ]
    quick_interactions._tasks = {
        task.id: task
        for task in [*active, *completed]
    }

    quick_interactions._write()

    assert len(quick_interactions._tasks) == 30
    assert {task.id for task in active} <= quick_interactions._tasks.keys()


def test_local_history_never_prunes_pending_restart_task_or_private_route(
    tmp_path: Path,
) -> None:
    quick_interactions = manager(tmp_path)
    pending = QuickInteractionTask(
        id="pending-restart",
        session_id="session-pending",
        prompt="等待重启",
        status="succeeded",
        result="已安排重启。",
        notification_route="weixin-task",
        deferred_restart_status="pending",
        created_at=utc_now() - timedelta(days=30),
        updated_at=utc_now() - timedelta(days=30),
    )
    completed = [
        QuickInteractionTask(
            id=f"completed-{index}",
            session_id="session-completed",
            prompt=f"完成任务 {index}",
            status="succeeded",
            result="完成",
            created_at=utc_now() + timedelta(minutes=index),
            updated_at=utc_now() + timedelta(minutes=index),
        )
        for index in range(30)
    ]
    route = QuickInteractionWeixinRoute(
        account_id="weixin-account",
        recipient="owner@im.wechat",
    )
    context = QuickInteractionDeferredRestartContext(
        operation_id="operation-1:restart",
        coordinator_operation_id="operation-1:restart",
        source_ip="100.64.0.1",
    )
    quick_interactions._tasks = {
        task.id: task for task in [pending, *completed]
    }
    quick_interactions._notification_routes[pending.id] = route
    quick_interactions._deferred_restart_contexts[pending.id] = context

    quick_interactions._write()

    assert pending.id in quick_interactions._tasks
    assert quick_interactions._notification_routes[pending.id] == route
    assert quick_interactions._deferred_restart_contexts[pending.id] == context


def test_local_history_keeps_finished_task_until_session_cleanup(
    tmp_path: Path,
) -> None:
    quick_interactions = manager(tmp_path)
    finished = QuickInteractionTask(
        id="just-finished",
        session_id="active-session",
        prompt="刚完成",
        status="succeeded",
        result="完成",
        created_at=utc_now(),
        updated_at=utc_now(),
    )
    other_active = [
        QuickInteractionTask(
            id=f"active-{index}",
            session_id=f"session-{index}",
            prompt=f"运行任务 {index}",
            status="running",
            created_at=utc_now(),
            updated_at=utc_now(),
        )
        for index in range(30)
    ]
    quick_interactions._tasks = {
        task.id: task
        for task in [finished, *other_active]
    }
    quick_interactions._running_sessions = {
        finished.session_id,
        *(task.session_id for task in other_active),
    }
    quick_interactions._active_task_ids.add(finished.id)

    quick_interactions._write()

    assert "just-finished" in quick_interactions._tasks

    quick_interactions._active_task_ids.discard(finished.id)
    quick_interactions._running_sessions.discard(finished.session_id)
    quick_interactions._write()

    assert "just-finished" not in quick_interactions._tasks


def test_submit_rechecks_terminal_status(tmp_path: Path) -> None:
    quick_interactions = manager(tmp_path)
    quick_interactions.codex_manager.get_session.return_value.status = "running"
    quick_interactions.codex_manager.get_session.return_value.activity = "working"

    with pytest.raises(ApiError) as error:
        quick_interactions.submit(
            "session-1",
            "检查状态",
            operation_id="operation-1",
            source_ip="127.0.0.1",
        )

    assert error.value.code == "quick_interaction_terminal_working"


def test_submit_rejects_active_native_writer(tmp_path: Path) -> None:
    quick_interactions = manager(tmp_path)
    quick_interactions.codex_manager.has_active_writer.return_value = True

    with pytest.raises(ApiError) as error:
        quick_interactions.submit(
            "session-1",
            "检查状态",
            operation_id="operation-1",
            source_ip="127.0.0.1",
        )

    assert error.value.code == "quick_interaction_writer_active"


def test_submit_allows_new_task_while_restart_is_pending(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    deferred_restart = MagicMock()
    deferred_restart.pending.return_value = True
    quick_interactions = manager(
        tmp_path,
        deferred_restart=deferred_restart,
    )
    monkeypatch.setattr(quick_interactions, "_start_worker_observer", MagicMock())

    task = quick_interactions.submit(
        "session-1",
        "检查状态",
        operation_id="operation-1",
        source_ip="127.0.0.1",
    )

    assert task.status == "requested"
    quick_interactions.codex_manager.prepare_quick_interaction.assert_called_once()


def test_deferred_restart_ready_waits_only_for_requesting_task_notifications(
    tmp_path: Path,
) -> None:
    quick_interactions = manager(tmp_path)
    task = QuickInteractionTask(
        id="task-1",
        session_id="session-1",
        prompt="检查状态",
        status="succeeded",
        result="完成",
        notification_status="sending",
        restart_sensitive=False,
        created_at=utc_now(),
        updated_at=utc_now(),
    )
    quick_interactions._tasks[task.id] = task
    request = DeferredRestartRequest(
        operation_id="operation-1:restart",
        requested_instance_id="instance-1",
        requested_task_id=task.id,
        source_ip="127.0.0.1",
        requested_at=task.updated_at,
        updated_at=task.updated_at,
    )

    assert quick_interactions.deferred_restart_ready(request) == "waiting"
    coalesced = QuickInteractionTask(
        id="task-2",
        session_id="session-2",
        prompt="also restart",
        status="succeeded",
        result="done",
        notification_status="sending",
        created_at=utc_now(),
        updated_at=utc_now(),
    )
    quick_interactions._tasks[coalesced.id] = coalesced
    quick_interactions._deferred_restart_contexts[coalesced.id] = (
        QuickInteractionDeferredRestartContext(
            operation_id="operation-2:restart",
            coordinator_operation_id=request.operation_id,
            source_ip="127.0.0.2",
        )
    )
    task.notification_status = "sent"
    assert quick_interactions.deferred_restart_ready(request) == "waiting"
    coalesced.notification_status = "sent"
    assert quick_interactions.deferred_restart_ready(request) == "ready"


def test_deferred_restart_ready_ignores_translation_work(tmp_path: Path) -> None:
    quick_interactions = manager(tmp_path)
    requester = QuickInteractionTask(
        id="requester-1",
        session_id="session-1",
        prompt="restart",
        status="succeeded",
        result="done",
        created_at=utc_now(),
        updated_at=utc_now(),
    )
    task = QuickInteractionTask(
        id="translation-1",
        session_id="translation-session",
        prompt="translate",
        kind="translation",
        status="running",
        notification_status="sending",
        created_at=utc_now(),
        updated_at=utc_now(),
    )
    quick_interactions._tasks[requester.id] = requester
    quick_interactions._tasks[task.id] = task
    quick_interactions._active_task_ids.add(task.id)
    request = DeferredRestartRequest(
        operation_id="operation-1:restart",
        requested_instance_id="instance-1",
        requested_task_id=requester.id,
        source_ip="127.0.0.1",
        requested_at=requester.updated_at,
        updated_at=requester.updated_at,
    )

    assert quick_interactions.deferred_restart_ready(request) == "ready"


def test_deferred_restart_ignores_active_tasks_in_other_sessions(
    tmp_path: Path,
) -> None:
    quick_interactions = manager(tmp_path)
    requested_at = utc_now()
    requester = QuickInteractionTask(
        id="requester-1",
        session_id="session-1",
        prompt="restart",
        status="succeeded",
        result="done",
        created_at=requested_at,
        updated_at=requested_at,
    )
    ordinary = QuickInteractionTask(
        id="ordinary-1",
        session_id="session-2",
        prompt="ordinary",
        status="running",
        restart_sensitive=False,
        created_at=requested_at,
        updated_at=requested_at,
    )
    sensitive = QuickInteractionTask(
        id="sensitive-1",
        session_id="session-3",
        prompt="modify Chub",
        status="running",
        restart_sensitive=True,
        created_at=requested_at,
        updated_at=requested_at,
    )
    quick_interactions._tasks = {
        item.id: item for item in (requester, ordinary, sensitive)
    }
    quick_interactions._active_task_ids = {ordinary.id, sensitive.id}
    request = DeferredRestartRequest(
        operation_id="operation-1:restart",
        requested_instance_id="instance-1",
        requested_task_id=requester.id,
        source_ip="127.0.0.1",
        requested_at=requested_at,
        updated_at=requested_at,
    )

    assert quick_interactions.deferred_restart_ready(request) == "ready"


def test_deferred_restart_ignores_failed_sensitive_task_in_other_session(
    tmp_path: Path,
) -> None:
    quick_interactions = manager(tmp_path)
    requested_at = utc_now()
    requester = QuickInteractionTask(
        id="requester-1",
        session_id="session-1",
        prompt="restart",
        status="succeeded",
        result="done",
        created_at=requested_at,
        updated_at=requested_at,
    )
    failed = QuickInteractionTask(
        id="sensitive-1",
        session_id="session-2",
        prompt="modify Chub",
        status="failed",
        error="failed",
        restart_sensitive=True,
        created_at=requested_at,
        updated_at=utc_now(),
    )
    quick_interactions._tasks = {
        requester.id: requester,
        failed.id: failed,
    }
    request = DeferredRestartRequest(
        operation_id="operation-1:restart",
        requested_instance_id="instance-1",
        requested_task_id=requester.id,
        source_ip="127.0.0.1",
        requested_at=requested_at,
        updated_at=requested_at,
    )

    assert quick_interactions.deferred_restart_ready(request) == "ready"


@pytest.mark.parametrize(
    ("workspace_id", "permission_mode", "expected"),
    (
        ("chub", "auto-review", True),
        ("chub", "full-access", True),
        ("chub", "read-only", False),
        ("isolated", "full-access", False),
    ),
)
def test_submission_uses_fixed_restart_sensitive_rule(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    workspace_id: str,
    permission_mode: str,
    expected: bool,
) -> None:
    quick_interactions = manager(tmp_path)
    session = quick_interactions.codex_manager.get_session.return_value
    session.workspace_id = workspace_id
    session.permission_mode = permission_mode
    monkeypatch.setattr(quick_interactions, "_start_worker_observer", MagicMock())

    task = quick_interactions.submit(
        session.id,
        "check",
        operation_id="operation-1",
        source_ip="127.0.0.1",
    )

    submission = quick_interactions._worker_call.call_args.kwargs["task"]
    assert task.restart_sensitive is expected
    assert submission["restart_sensitive"] is expected


def test_failed_translation_does_not_queue_notification(tmp_path: Path) -> None:
    notifier = MagicMock()
    quick_interactions = manager(tmp_path, completion_notifier=notifier)
    task = QuickInteractionTask(
        id="translation-1",
        session_id="translation-session",
        prompt="translate",
        kind="translation",
        status="running",
        created_at=utc_now(),
        updated_at=utc_now(),
    )
    quick_interactions._tasks[task.id] = task

    quick_interactions._finish(task.id, "failed", "translation failed")

    assert quick_interactions.get(task.id).notification_status == "skipped"
    notifier.assert_not_called()


def test_deferred_restart_completion_distinguishes_automatic_and_manual(
    tmp_path: Path,
) -> None:
    quick_interactions = manager(tmp_path)
    task = QuickInteractionTask(
        id="task-1",
        session_id="session-1",
        prompt="重启服务",
        status="succeeded",
        result="已安排重启。",
        deferred_restart_status="pending",
        deferred_restart_updated_at=utc_now(),
        created_at=utc_now(),
        updated_at=utc_now(),
    )
    quick_interactions._tasks[task.id] = task
    completed_at = utc_now()

    quick_interactions.record_deferred_restart_completion(
        "operation-1:restart",
        task.id,
        "succeeded",
        completed_at,
    )
    completed = quick_interactions.get(task.id)
    assert completed.deferred_restart_status == "succeeded"
    assert completed.deferred_restart_updated_at == completed_at
    assert completed.result == "已安排重启。"

    persisted = json.loads(quick_interactions.path.read_text(encoding="utf-8"))
    assert persisted[0]["deferred_restart_status"] == "succeeded"

    quick_interactions.record_deferred_restart_completion(
        "operation-1:restart",
        task.id,
        "cleared",
        utc_now(),
    )
    assert quick_interactions.get(task.id).deferred_restart_status == "succeeded"

    manual_task = task.model_copy(
        update={
            "id": "task-2",
            "deferred_restart_status": "pending",
        }
    )
    quick_interactions._tasks[manual_task.id] = manual_task
    quick_interactions.record_deferred_restart_completion(
        "operation-2:restart",
        manual_task.id,
        "cleared",
        utc_now(),
    )
    assert quick_interactions.get(manual_task.id).deferred_restart_status == "cleared"


def test_deferred_restart_start_failure_preserves_specific_reason(
    tmp_path: Path,
) -> None:
    quick_interactions = manager(tmp_path)
    task = QuickInteractionTask(
        id="task-1",
        session_id="session-1",
        prompt="重启服务",
        status="succeeded",
        result="已安排重启。",
        deferred_restart_status="started",
        created_at=utc_now(),
        updated_at=utc_now(),
    )
    quick_interactions._tasks[task.id] = task

    quick_interactions.record_deferred_restart_completion(
        "operation-1:restart",
        task.id,
        "start_failed",
        utc_now(),
        "重启脚本返回退出码 1，旧 Chub 实例继续运行。",
    )

    completed = quick_interactions.get(task.id)
    assert completed.deferred_restart_status == "start_failed"
    assert completed.deferred_restart_error == (
        "重启脚本返回退出码 1，旧 Chub 实例继续运行。"
    )
    persisted = json.loads(quick_interactions.path.read_text(encoding="utf-8"))
    assert persisted[0]["deferred_restart_error"] == completed.deferred_restart_error


def test_deferred_restart_completion_notifies_only_coalesced_weixin_task(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    notifier = MagicMock(return_value=SimpleNamespace(status="sent", error=None))
    quick_interactions = manager(tmp_path, restart_notifier=notifier)
    now = utc_now()
    page_task = QuickInteractionTask(
        id="page-task",
        session_id="page-session",
        prompt="页面重启",
        status="succeeded",
        result="已安排重启。",
        deferred_restart_status="pending",
        created_at=now,
        updated_at=now,
    )
    weixin_task = page_task.model_copy(
        update={
            "id": "weixin-task",
            "session_id": "weixin-session",
            "prompt": "微信重启",
            "notification_route": "weixin-task",
        }
    )
    quick_interactions._tasks = {
        page_task.id: page_task,
        weixin_task.id: weixin_task,
    }
    quick_interactions._deferred_restart_contexts = {
        page_task.id: QuickInteractionDeferredRestartContext(
            operation_id="page-operation:restart",
            coordinator_operation_id="page-operation:restart",
            source_ip="100.64.0.1",
        ),
        weixin_task.id: QuickInteractionDeferredRestartContext(
            operation_id="weixin-operation:restart",
            coordinator_operation_id="page-operation:restart",
            source_ip="100.64.0.2",
        ),
    }
    route = QuickInteractionWeixinRoute(
        account_id="weixin-account",
        recipient="owner@im.wechat",
    )
    quick_interactions._notification_routes[weixin_task.id] = route

    class ImmediateThread:
        def __init__(self, *, target, args, daemon, name=None):
            self.target = target
            self.args = args

        def start(self) -> None:
            self.target(*self.args)

    monkeypatch.setattr(
        "app.codex.quick_interactions.threading.Thread",
        ImmediateThread,
    )

    quick_interactions.record_deferred_restart_started(
        "page-operation:restart",
        page_task.id,
        now,
    )
    quick_interactions.record_deferred_restart_completion(
        "page-operation:restart",
        page_task.id,
        "succeeded",
        now,
    )

    assert quick_interactions.get(page_task.id).deferred_restart_status == "succeeded"
    assert (
        quick_interactions.get(page_task.id).deferred_restart_notification_status
        is None
    )
    completed_weixin = quick_interactions.get(weixin_task.id)
    assert completed_weixin.deferred_restart_status == "succeeded"
    assert completed_weixin.deferred_restart_notification_status == "sent"
    notifier.assert_called_once()
    assert notifier.call_args.args[1] == route
    assert notifier.call_args.args[2] == "succeeded"
    public_payload = completed_weixin.model_dump(mode="json")
    assert "coordinator_operation_id" not in public_payload
    assert "owner@im.wechat" not in json.dumps(public_payload)
    persisted = json.loads(quick_interactions.path.read_text(encoding="utf-8"))
    persisted_weixin = next(item for item in persisted if item["id"] == weixin_task.id)
    assert persisted_weixin["_notification_route"] == route.model_dump(mode="json")
    assert persisted_weixin["_deferred_restart_context"][
        "coordinator_operation_id"
    ] == "page-operation:restart"


def test_sensitive_task_failure_updates_timeline_and_notifies_weixin(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    notifier = MagicMock(return_value=SimpleNamespace(status="sent", error=None))
    quick_interactions = manager(tmp_path, restart_notifier=notifier)
    now = utc_now()
    task = QuickInteractionTask(
        id="weixin-task",
        session_id="weixin-session",
        prompt="修改并重启",
        status="succeeded",
        result="已安排重启。",
        notification_route="weixin-task",
        deferred_restart_status="pending",
        created_at=now,
        updated_at=now,
    )
    quick_interactions._tasks[task.id] = task
    quick_interactions._deferred_restart_contexts[task.id] = (
        QuickInteractionDeferredRestartContext(
            operation_id="operation-1:restart",
            coordinator_operation_id="operation-1:restart",
            source_ip="100.64.0.2",
        )
    )
    route = QuickInteractionWeixinRoute(
        account_id="weixin-account",
        recipient="owner@im.wechat",
    )
    quick_interactions._notification_routes[task.id] = route

    class ImmediateThread:
        def __init__(self, *, target, args, daemon, name=None):
            self.target = target
            self.args = args

        def start(self) -> None:
            self.target(*self.args)

    monkeypatch.setattr(
        "app.codex.quick_interactions.threading.Thread",
        ImmediateThread,
    )

    quick_interactions.record_deferred_restart_completion(
        "operation-1:restart",
        task.id,
        "sensitive_task_failed",
        now,
    )

    completed = quick_interactions.get(task.id)
    assert completed.deferred_restart_status == "sensitive_task_failed"
    assert completed.deferred_restart_notification_status == "sent"
    notified_task, notified_route, outcome = notifier.call_args.args
    assert notified_task.deferred_restart_status == "sensitive_task_failed"
    assert notified_route == route
    assert outcome == "sensitive_task_failed"


def test_restart_completion_waits_for_local_success_projection(
    tmp_path: Path,
) -> None:
    completion_started = threading.Event()
    completion_finished = threading.Event()
    completion_threads: list[threading.Thread] = []

    class RacingDeferredRestart:
        def request(self, *, operation_id, task_id, source_ip):
            def complete() -> None:
                completion_started.set()
                quick_interactions.record_deferred_restart_completion(
                    operation_id,
                    task_id,
                    "sensitive_task_failed",
                    utc_now(),
                )
                completion_finished.set()

            thread = threading.Thread(target=complete)
            completion_threads.append(thread)
            thread.start()
            assert completion_started.wait(1)
            return SimpleNamespace(operation_id=operation_id, created=True)

    deferred_restart = RacingDeferredRestart()
    quick_interactions = manager(tmp_path, deferred_restart=deferred_restart)
    now = utc_now()
    task = QuickInteractionTask(
        id="task-1",
        worker_task_id="qw-1750000000000-00000000000000000000000000000001",
        session_id="session-1",
        prompt="修改并重启",
        status="running",
        created_at=now,
        updated_at=now,
    )
    quick_interactions._tasks[task.id] = task
    quick_interactions._operations[task.id] = ("operation-1", "127.0.0.1")
    request_path = (
        quick_interactions.restart_request_dir / f"{task.worker_task_id}.request"
    )
    request_path.touch(mode=0o600)
    snapshot = SimpleNamespace(
        status="succeeded",
        result="任务已完成。",
        error=None,
        error_code=None,
    )

    quick_interactions._finish_from_worker_snapshot(task.id, task, snapshot)
    assert completion_finished.wait(1)
    for thread in completion_threads:
        thread.join(1)

    completed = quick_interactions.get(task.id)
    assert completed.status == "succeeded"
    assert completed.deferred_restart_status == "sensitive_task_failed"


def test_restart_notification_thread_start_failure_is_terminal(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    quick_interactions = manager(tmp_path, restart_notifier=MagicMock())
    now = utc_now()
    task = QuickInteractionTask(
        id="weixin-task",
        session_id="weixin-session",
        prompt="重启",
        status="succeeded",
        result="已完成。",
        notification_route="weixin-task",
        deferred_restart_status="pending",
        created_at=now,
        updated_at=now,
    )
    quick_interactions._tasks[task.id] = task

    class FailingThread:
        def __init__(self, **_kwargs):
            pass

        def start(self) -> None:
            raise RuntimeError("thread unavailable")

    monkeypatch.setattr(
        "app.codex.quick_interactions.threading.Thread",
        FailingThread,
    )

    quick_interactions.record_deferred_restart_completion(
        "operation-1:restart",
        task.id,
        "succeeded",
        now,
    )

    completed = quick_interactions.get(task.id)
    assert completed.deferred_restart_status == "succeeded"
    assert completed.deferred_restart_notification_status == "failed"
    assert completed.deferred_restart_notification_error == (
        "微信重启通知线程未能启动。"
    )
    assert quick_interactions.has_pending_deferred_restart_notifications() is False


def test_restart_notification_interrupted_while_sending_is_not_retried(
    tmp_path: Path,
) -> None:
    state = tmp_path / "quick-interactions.json"
    task = QuickInteractionTask(
        id="task-1",
        session_id="session-1",
        prompt="重启",
        status="succeeded",
        result="完成",
        notification_route="weixin-task",
        deferred_restart_status="succeeded",
        deferred_restart_notification_status="sending",
        created_at=utc_now(),
        updated_at=utc_now(),
    )
    state.write_text(json.dumps([task.model_dump(mode="json")]), encoding="utf-8")

    quick_interactions = manager(tmp_path)

    recovered = quick_interactions.get(task.id)
    assert recovered.deferred_restart_notification_status == "failed"
    assert recovered.deferred_restart_notification_error == (
        "服务重启时微信重启通知未完成。"
    )
    assert quick_interactions.has_pending_deferred_restart_notifications() is False


def test_submit_rejects_session_error(tmp_path: Path) -> None:
    quick_interactions = manager(tmp_path)
    session = quick_interactions.codex_manager.get_session.return_value
    session.status = "error"
    session.activity = "unknown"

    with pytest.raises(ApiError) as error:
        quick_interactions.submit(
            "session-1",
            "检查状态",
            operation_id="operation-1",
            source_ip="127.0.0.1",
        )

    assert error.value.code == "quick_interaction_session_error"


def test_submit_allows_idle_running_terminal(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    quick_interactions = manager(tmp_path)
    session = quick_interactions.codex_manager.get_session.return_value
    session.status = "running"
    session.activity = "idle"
    thread = MagicMock()
    monkeypatch.setattr(
        "app.codex.quick_interactions.threading.Thread",
        MagicMock(return_value=thread),
    )

    task = quick_interactions.submit(
        "session-1",
        "检查状态",
        operation_id="operation-1",
        source_ip="127.0.0.1",
    )

    assert task.status == "requested"
    thread.start.assert_called_once()


def test_session_operation_rejects_running_quick_interaction(tmp_path: Path) -> None:
    quick_interactions = manager(tmp_path)
    quick_interactions._running_sessions.add("session-1")

    with pytest.raises(ApiError) as error:
        with quick_interactions.session_operation_guard("session-1"):
            pass

    assert error.value.code == "quick_interaction_in_progress"


def test_cancel_codex_session_rejects_untracked_active_session(tmp_path: Path) -> None:
    quick_interactions = manager(tmp_path)
    quick_interactions._running_sessions.add("session-1")

    with pytest.raises(ApiError) as error:
        quick_interactions.cancel_codex_session("session-1")

    assert error.value.code == "quick_interaction_cancel_failed"
