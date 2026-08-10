from __future__ import annotations

import json
import stat
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from app.codex.models import (
    CodexSession,
    QuickInteractionWeixinRoute,
    WorkspaceInfo,
    utc_now,
)
from app.core.config import Settings
from app.core.response import ApiError
from app.services.openclaw_weixin_chub_mode import (
    MAX_STATE_BYTES,
    WeixinChubModeManager,
    WeixinChubModeRuntimeConfig,
    WeixinChubModeState,
    WeixinChubModeSubmission,
)


def delivery_route(
    account_id: str = "weixin-account",
    recipient: str = "owner@im.wechat",
) -> QuickInteractionWeixinRoute:
    return QuickInteractionWeixinRoute(
        account_id=account_id,
        recipient=recipient,
    )


@pytest.fixture(autouse=True)
def inject_default_delivery_route(monkeypatch) -> None:
    original = WeixinChubModeManager.submit

    def submit_with_route(self, *args, delivery_route=None, **kwargs):
        return original(
            self,
            *args,
            delivery_route=delivery_route or globals()["delivery_route"](),
            **kwargs,
        )

    monkeypatch.setattr(WeixinChubModeManager, "submit", submit_with_route)


def configured_manager(
    settings: Settings,
) -> tuple[WeixinChubModeManager, MagicMock, MagicMock]:
    settings.openclaw.weixin_chub_mode.enabled = True
    settings.openclaw.quick_interaction_completion.enabled = True
    settings.openclaw.quick_interaction_completion.weixin_recipient = "recipient"
    codex_manager = MagicMock()
    codex_manager.workspaces.return_value = [
        WorkspaceInfo(id="chub", name="Chub", path="/project", available=True)
    ]
    codex_manager.available.return_value = True
    codex_manager.create_session.return_value = SimpleNamespace(id="session-1")
    codex_manager.get_session.return_value = CodexSession(
        id="session-1",
        workspace_id="chub",
        workspace_name="Chub",
        cwd="/project",
        permission_mode="full-access",
        status="stopped",
        activity="idle",
    )
    codex_manager.has_active_writer.return_value = False
    codex_manager.wait_for_writer_release.return_value = True
    quick_interactions = MagicMock()
    quick_interactions.is_running.return_value = False
    quick_interactions.submit.return_value = SimpleNamespace(id="task-1")
    return (
        WeixinChubModeManager(
            settings,
            codex_manager,
            quick_interactions,
            MagicMock(return_value=None),
        ),
        codex_manager,
        quick_interactions,
    )


def test_submit_creates_one_private_session_and_replays_duplicate(
    settings: Settings,
) -> None:
    manager, codex_manager, quick_interactions = configured_manager(settings)

    first = manager.submit(
        message_id="message-1",
        prompt="检查设备状态",
        correlation_id="correlation-1",
        source_ip="100.64.0.21",
    )
    duplicate = manager.submit(
        message_id="message-1",
        prompt="不会再次执行",
        correlation_id="correlation-2",
        source_ip="100.64.0.21",
    )

    assert first.accepted is True
    assert first.duplicate is False
    assert first.new_session is True
    assert duplicate.duplicate is True
    assert duplicate.message == first.message
    codex_manager.create_session.assert_called_once_with(
        "chub",
        "full-access",
        None,
        None,
    )
    codex_manager.set_initial_quick_interaction_title.assert_called_once_with(
        "session-1",
        "微信 Chub",
    )
    quick_interactions.submit.assert_called_once_with(
        "session-1",
        "检查设备状态",
        operation_id=quick_interactions.submit.call_args.kwargs["operation_id"],
        source_ip="100.64.0.21",
        notification_route=delivery_route(),
    )
    state_file = settings.openclaw.weixin_chub_mode.state_file
    assert stat.S_IMODE(state_file.stat().st_mode) == 0o600
    state_text = state_file.read_text(encoding="utf-8")
    assert "检查设备状态" not in state_text
    assert "weixin-account" not in state_text
    assert "owner@im.wechat" not in state_text
    persisted = json.loads(state_text)
    assert persisted["session_id"] == "session-1"
    assert persisted["submissions"][0]["task_id"] == "task-1"


def test_submit_rejects_invalid_delivery_route_before_codex(
    settings: Settings,
) -> None:
    manager, codex_manager, quick_interactions = configured_manager(settings)
    manager.route_validator = MagicMock(return_value="原消息的 ClawBot 当前不可用。")

    with pytest.raises(ApiError) as error:
        manager.submit(
            message_id="message-route-invalid",
            prompt="检查设备",
            correlation_id=None,
            source_ip="100.64.0.21",
        )

    assert error.value.code == "weixin_chub_mode_delivery_route_invalid"
    codex_manager.create_session.assert_not_called()
    quick_interactions.submit.assert_not_called()


def test_duplicate_message_with_different_route_is_rejected(
    settings: Settings,
) -> None:
    manager, _codex_manager, quick_interactions = configured_manager(settings)
    manager.submit(
        message_id="message-route-conflict",
        prompt="检查设备",
        correlation_id=None,
        source_ip="100.64.0.21",
    )

    with pytest.raises(ApiError) as error:
        manager.submit(
            message_id="message-route-conflict",
            prompt="不能重复执行",
            correlation_id=None,
            source_ip="100.64.0.21",
            delivery_route=delivery_route(recipient="other@im.wechat"),
        )

    assert error.value.code == "weixin_chub_mode_message_conflict"
    quick_interactions.submit.assert_called_once()


def test_mode_readiness_no_longer_requires_global_recipient(
    settings: Settings,
) -> None:
    manager, _codex_manager, _quick_interactions = configured_manager(settings)
    settings.openclaw.quick_interaction_completion.weixin_recipient = None

    status = manager.status()

    assert status.ready is True


def test_submit_reuses_session_when_defaults_resolve_to_effective_model(
    settings: Settings,
) -> None:
    manager, codex_manager, quick_interactions = configured_manager(settings)
    codex_manager.get_session.return_value = CodexSession(
        id="session-1",
        workspace_id="chub",
        workspace_name="Chub",
        cwd="/project",
        permission_mode="full-access",
        model="gpt-effective-default",
        reasoning_effort="medium",
        status="stopped",
        activity="idle",
    )

    first = manager.submit(
        message_id="message-1",
        prompt="第一条任务",
        correlation_id=None,
        source_ip="100.64.0.21",
    )
    second = manager.submit(
        message_id="message-2",
        prompt="第二条任务",
        correlation_id=None,
        source_ip="100.64.0.21",
    )

    assert first.new_session is True
    assert second.new_session is False
    codex_manager.create_session.assert_called_once()
    assert quick_interactions.submit.call_count == 2
    assert manager.session_id() == "session-1"


def test_submit_replaces_session_when_explicit_model_no_longer_matches(
    settings: Settings,
) -> None:
    settings.openclaw.weixin_chub_mode.model = "configured-model"
    manager, codex_manager, quick_interactions = configured_manager(settings)
    manager._state.session_id = "old-session"
    codex_manager.get_session.return_value = CodexSession(
        id="old-session",
        workspace_id="chub",
        workspace_name="Chub",
        cwd="/project",
        permission_mode="full-access",
        model="different-model",
        reasoning_effort="medium",
    )
    codex_manager.create_session.return_value = SimpleNamespace(id="new-session")
    quick_interactions.submit.return_value = SimpleNamespace(id="task-1")

    result = manager.submit(
        message_id="message-1",
        prompt="检查模型配置",
        correlation_id=None,
        source_ip="100.64.0.21",
    )

    assert result.new_session is True
    codex_manager.create_session.assert_called_once_with(
        "chub",
        "full-access",
        "configured-model",
        None,
    )
    quick_interactions.submit.assert_called_once_with(
        "new-session",
        "检查模型配置",
        operation_id=quick_interactions.submit.call_args.kwargs["operation_id"],
        source_ip="100.64.0.21",
        notification_route=delivery_route(),
    )


def test_submit_logs_complete_lifecycle_without_exposing_prompt(
    settings: Settings,
) -> None:
    manager, _codex_manager, _quick_interactions = configured_manager(settings)

    with patch(
        "app.services.openclaw_weixin_chub_mode.write_operation"
    ) as write_operation:
        manager.submit(
            message_id="message-1",
            prompt="不应出现在操作日志",
            correlation_id=None,
            source_ip="100.64.0.21",
        )

    submit_entries = [
        call.kwargs
        for call in write_operation.call_args_list
        if call.kwargs["action"] == "weixin_chub_mode_submit"
    ]
    assert [entry["status"] for entry in submit_entries] == [
        "requested",
        "started",
        "succeeded",
    ]
    assert {entry["target"] for entry in submit_entries} == {settings.node.id}
    assert all("不应出现在操作日志" not in str(entry) for entry in submit_entries)


def test_submit_reclaims_unknown_session_before_quick_interaction(
    settings: Settings,
) -> None:
    manager, codex_manager, quick_interactions = configured_manager(settings)
    manager._state.session_id = "session-1"
    codex_manager.get_session.return_value = CodexSession(
        id="session-1",
        workspace_id="chub",
        workspace_name="Chub",
        cwd="/project",
        codex_session_id="native-session-1",
        permission_mode="full-access",
        status="running",
        activity="unknown",
    )
    reclaimer = MagicMock(
        return_value=SimpleNamespace(status="stopped", activity="idle")
    )
    manager.terminal_reclaimer = reclaimer

    with patch(
        "app.services.openclaw_weixin_chub_mode.write_operation"
    ) as write_operation:
        result = manager.submit(
            message_id="message-unknown",
            prompt="检查设备",
            correlation_id=None,
            source_ip="100.64.0.21",
        )

    assert "已停止残留会话并提交任务" in result.message
    reclaimer.assert_called_once_with("session-1")
    codex_manager.wait_for_writer_release.assert_called_once_with(
        "native-session-1",
        timeout=3.0,
    )
    quick_interactions.submit.assert_called_once()
    reclaim_entries = [
        call.kwargs
        for call in write_operation.call_args_list
        if call.kwargs["action"] == "weixin_chub_mode_session_reclaim"
    ]
    assert [entry["status"] for entry in reclaim_entries] == [
        "requested",
        "started",
        "succeeded",
    ]


def test_submit_rejects_unknown_session_when_writer_does_not_release(
    settings: Settings,
) -> None:
    manager, codex_manager, quick_interactions = configured_manager(settings)
    manager._state.session_id = "session-1"
    codex_manager.get_session.return_value = CodexSession(
        id="session-1",
        workspace_id="chub",
        workspace_name="Chub",
        cwd="/project",
        codex_session_id="native-session-1",
        permission_mode="full-access",
        status="running",
        activity="unknown",
    )
    codex_manager.wait_for_writer_release.return_value = False
    manager.terminal_reclaimer = MagicMock(
        return_value=SimpleNamespace(status="stopped", activity="idle")
    )

    with pytest.raises(ApiError) as error:
        manager.submit(
            message_id="message-writer-active",
            prompt="检查设备",
            correlation_id=None,
            source_ip="100.64.0.21",
        )

    assert error.value.code == "weixin_chub_mode_submission_failed"
    assert "未能安全停止" in error.value.message
    quick_interactions.submit.assert_not_called()


def test_submit_rejects_busy_session_and_replays_same_failure(
    settings: Settings,
) -> None:
    manager, _codex_manager, quick_interactions = configured_manager(settings)
    manager.submit(
        message_id="message-1",
        prompt="首个任务",
        correlation_id=None,
        source_ip="100.64.0.21",
    )
    quick_interactions.is_running.return_value = True

    with pytest.raises(ApiError) as first_error:
        manager.submit(
            message_id="message-2",
            prompt="第二个任务",
            correlation_id=None,
            source_ip="100.64.0.21",
        )
    with pytest.raises(ApiError) as duplicate_error:
        manager.submit(
            message_id="message-2",
            prompt="重复消息",
            correlation_id=None,
            source_ip="100.64.0.21",
        )

    assert first_error.value.code == "weixin_chub_mode_in_progress"
    assert duplicate_error.value.code == "weixin_chub_mode_in_progress"
    assert quick_interactions.submit.call_count == 1


def test_disabled_submission_failure_is_persisted_for_idempotency(
    settings: Settings,
) -> None:
    codex_manager = MagicMock()
    quick_interactions = MagicMock()
    manager = WeixinChubModeManager(
        settings,
        codex_manager,
        quick_interactions,
    )

    for prompt in ("首次消息", "重复消息"):
        with pytest.raises(ApiError) as error:
            manager.submit(
                message_id="message-1",
                prompt=prompt,
                correlation_id=None,
                source_ip="100.64.0.21",
            )
        assert error.value.code == "weixin_chub_mode_mode_disabled"

    quick_interactions.submit.assert_not_called()
    payload = json.loads(
        settings.openclaw.weixin_chub_mode.state_file.read_text(encoding="utf-8")
    )
    assert payload["submissions"][0]["status"] == "rejected"
    assert payload["submissions"][0]["code"] == "mode_disabled"


def test_quick_interaction_failure_replays_same_bounded_error(
    settings: Settings,
) -> None:
    manager, _codex_manager, quick_interactions = configured_manager(settings)
    quick_interactions.submit.side_effect = ApiError(
        409,
        "chub_restart_pending",
        "底层实现细节不应透传。",
    )

    errors = []
    for prompt in ("首次消息", "重复消息"):
        with pytest.raises(ApiError) as error:
            manager.submit(
                message_id="message-1",
                prompt=prompt,
                correlation_id=None,
                source_ip="100.64.0.21",
            )
        errors.append(error.value)

    assert [error.status_code for error in errors] == [409, 409]
    assert [error.code for error in errors] == [
        "weixin_chub_mode_submission_failed",
        "weixin_chub_mode_submission_failed",
    ]
    assert errors[0].message == errors[1].message == "微信任务提交失败。"
    quick_interactions.submit.assert_called_once()


def test_restart_recovers_reserved_submission_as_fixed_failure(
    settings: Settings,
) -> None:
    settings.openclaw.weixin_chub_mode.enabled = True
    state_file = settings.openclaw.weixin_chub_mode.state_file
    state_file.write_text(
        WeixinChubModeState(
            configuration=WeixinChubModeRuntimeConfig(enabled=True),
            submissions=[
                WeixinChubModeSubmission(
                    message_id="message-1",
                    correlation_id=None,
                    operation_id="operation-1",
                    delivery_route_fingerprint=(
                        WeixinChubModeManager._route_fingerprint(delivery_route())
                    ),
                    status="reserved",
                    code="submission_interrupted",
                    message="等待提交。",
                    created_at=utc_now(),
                    updated_at=utc_now(),
                )
            ],
        ).model_dump_json(),
        encoding="utf-8",
    )
    manager = WeixinChubModeManager(settings, MagicMock(), MagicMock())

    with pytest.raises(ApiError) as error:
        manager.submit(
            message_id="message-1",
            prompt="重复消息",
            correlation_id=None,
            source_ip="100.64.0.21",
        )

    assert error.value.code == "weixin_chub_mode_submission_interrupted"
    assert "发送一条新消息重试" in error.value.message


def test_startup_configuration_change_resets_bound_session(
    settings: Settings,
) -> None:
    settings.openclaw.weixin_chub_mode.enabled = True
    state_file = settings.openclaw.weixin_chub_mode.state_file
    state_file.write_text(
        WeixinChubModeState(
            configuration=WeixinChubModeRuntimeConfig(
                enabled=True,
                workspace_id="home",
            ),
            session_id="old-session",
        ).model_dump_json(),
        encoding="utf-8",
    )

    manager = WeixinChubModeManager(settings, MagicMock(), MagicMock())

    assert manager.configuration().workspace_id == "chub"
    assert manager.session_id() is None


def test_invalid_state_blocks_submission_without_overwriting_file(
    settings: Settings,
) -> None:
    state_file = settings.openclaw.weixin_chub_mode.state_file
    state_file.write_text("not-json", encoding="utf-8")
    manager = WeixinChubModeManager(settings, MagicMock(), MagicMock())

    with pytest.raises(ApiError) as error:
        manager.submit(
            message_id="message-1",
            prompt="检查状态",
            correlation_id=None,
            source_ip="100.64.0.21",
        )

    assert error.value.code == "weixin_chub_mode_state_unavailable"
    assert state_file.read_text(encoding="utf-8") == "not-json"


def test_symlink_state_is_rejected_without_touching_target(
    settings: Settings,
    tmp_path,
) -> None:
    state_file = settings.openclaw.weixin_chub_mode.state_file
    target = tmp_path / "unrelated.json"
    target.write_text("keep", encoding="utf-8")
    state_file.symlink_to(target)

    manager = WeixinChubModeManager(settings, MagicMock(), MagicMock())

    assert manager.status().code == "disabled"
    with pytest.raises(ApiError) as error:
        manager.submit(
            message_id="message-1",
            prompt="检查状态",
            correlation_id=None,
            source_ip="100.64.0.21",
        )
    assert error.value.code == "weixin_chub_mode_state_unavailable"
    assert target.read_text(encoding="utf-8") == "keep"


def test_state_writer_prunes_oldest_records_to_byte_limit(
    settings: Settings,
) -> None:
    manager, _codex_manager, _quick_interactions = configured_manager(settings)
    large = "消" * 450
    state = WeixinChubModeState(
        configuration=manager.configuration(),
        submissions=[
            WeixinChubModeSubmission(
                message_id=f"{index:04d}-{large}",
                correlation_id=large,
                operation_id=f"operation-{index}",
                status="rejected",
                code="submission_failed",
                message=large,
                created_at=utc_now(),
                updated_at=utc_now(),
            )
            for index in range(2_000)
        ],
    )

    manager._write_state(state)

    state_file = settings.openclaw.weixin_chub_mode.state_file
    assert state_file.stat().st_size <= MAX_STATE_BYTES
    persisted = json.loads(state_file.read_text(encoding="utf-8"))
    assert len(persisted["submissions"]) < 2_000
    assert persisted["submissions"][-1]["message_id"].startswith("1999-")


def test_state_failure_after_task_start_fails_closed(
    settings: Settings,
) -> None:
    manager, _codex_manager, quick_interactions = configured_manager(settings)
    original_write = manager._write_state
    write_count = 0

    def fail_final_write(state: WeixinChubModeState) -> None:
        nonlocal write_count
        write_count += 1
        if write_count == 3:
            raise OSError("disk unavailable")
        original_write(state)

    manager._write_state = fail_final_write

    with pytest.raises(ApiError) as error:
        manager.submit(
            message_id="message-1",
            prompt="检查设备状态",
            correlation_id=None,
            source_ip="100.64.0.21",
        )

    assert error.value.code == "weixin_chub_mode_state_unavailable"
    assert "任务已启动" in error.value.message
    quick_interactions.submit.assert_called_once()
    assert manager.status().ready is False
    with pytest.raises(ApiError) as retry_error:
        manager.submit(
            message_id="message-1",
            prompt="不能重复执行",
            correlation_id=None,
            source_ip="100.64.0.21",
        )
    assert retry_error.value.code == "weixin_chub_mode_state_unavailable"
    assert quick_interactions.submit.call_count == 1
