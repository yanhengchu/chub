from __future__ import annotations

import json
import re
import stat
import threading
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from app.codex.models import (
    CodexQuotaData,
    CodexQuotaWindow,
    CodexSession,
    CodexTokenUsageData,
    QuickInteractionWeixinRoute,
    WorkspaceInfo,
    utc_now,
)
from app.core.config import Settings
from app.core.response import ApiError
from app.services.openclaw_weixin_chub_mode import WeixinChubModeManager
from app.services.openclaw_weixin_chub_models import (
    MAX_STATE_BYTES,
    WeixinChubModePendingRetry,
    WeixinChubModeRuntimeConfig,
    WeixinChubModeSessionSlot,
    WeixinChubModeState,
    WeixinChubModeSubmission,
)

from tests.openclaw_weixin_chub_mode_helpers import (
    configured_manager,
    delivery_route,
    enable_restart_command,
    inject_default_delivery_route,
)


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


def test_startup_repairs_legacy_success_http_status(
    settings: Settings,
) -> None:
    settings.openclaw.weixin_chub_mode.enabled = True
    state_file = settings.openclaw.weixin_chub_mode.state_file
    state_file.write_text(
        WeixinChubModeState(
            configuration=WeixinChubModeRuntimeConfig(enabled=True),
            submissions=[
                WeixinChubModeSubmission(
                    message_id="legacy-success",
                    correlation_id=None,
                    operation_id="operation-1",
                    delivery_route_fingerprint=(
                        WeixinChubModeManager._route_fingerprint(delivery_route())
                    ),
                    status="submitted",
                    code="submitted",
                    message="任务已提交。",
                    http_status=409,
                    session_id="session-1",
                    task_id="task-1",
                    created_at=utc_now(),
                    updated_at=utc_now(),
                )
            ],
        ).model_dump_json(),
        encoding="utf-8",
    )

    WeixinChubModeManager(settings, MagicMock(), MagicMock())

    payload = json.loads(state_file.read_text(encoding="utf-8"))
    assert payload["submissions"][0]["http_status"] == 200


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
