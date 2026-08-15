from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from app.codex.models import (
    CodexSession,
    QuickInteractionWeixinRoute,
    WorkspaceInfo,
    utc_now,
)
from app.core.config import Settings
from app.services.openclaw_weixin_chub_mode import WeixinChubModeManager


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
    quick_interactions.deferred_restart = None
    quick_interactions.is_running.return_value = False
    quick_interactions.weixin_session_ids.return_value = set()
    quick_interactions.weixin_task_status_snapshot.return_value = SimpleNamespace(
        running_count=0,
        pending_notification_count=0,
        failed_notification_count=0,
    )
    quick_interactions.submit.return_value = SimpleNamespace(id="task-1")
    manager = WeixinChubModeManager(
        settings,
        codex_manager,
        quick_interactions,
        MagicMock(return_value=None),
    )
    manager.session_archiver = MagicMock()
    manager._status_cache["readiness"] = (manager.status(), utc_now())
    return (
        manager,
        codex_manager,
        quick_interactions,
    )


def enable_restart_command(
    manager: WeixinChubModeManager,
) -> tuple[MagicMock, MagicMock]:
    coordinator = MagicMock()
    coordinator.request.side_effect = lambda **kwargs: SimpleNamespace(
        operation_id=kwargs["operation_id"],
        created=True,
    )
    notifier = MagicMock(
        return_value=SimpleNamespace(status="sent", error=None)
    )
    manager.restart_coordinator = coordinator
    manager.restart_notifier = notifier
    return coordinator, notifier
