from __future__ import annotations

import threading
from types import SimpleNamespace
from unittest.mock import MagicMock

from app.core.response import ApiError
from app.services.openclaw_weixin_chub_commands import (
    is_ai_runtime_write_command,
    parse_weixin_chub_command,
)
from chub_codex_runtime.weixin_commands import parse_weixin_command

from tests.openclaw_weixin_chub_mode_helpers import (
    configured_manager,
    delivery_route,
)


def test_codex_auth_commands_are_fixed_and_have_expected_write_boundary() -> None:
    status = parse_weixin_command("CODEX AUTH。")
    switch = parse_weixin_command("codex auth switch")

    assert status is not None
    assert switch is not None
    assert status.kind == "codex_auth"
    assert switch.kind == "codex_auth_switch"
    assert is_ai_runtime_write_command(status) is False
    assert is_ai_runtime_write_command(switch) is True
    invalid = parse_weixin_command("codex auth switch now")
    assert invalid is not None
    assert invalid.invalid_usage is True
    assert parse_weixin_chub_command("codex auth").kind == "normal"


def test_codex_auth_reads_only_current_authentication_status(settings) -> None:
    manager, _codex_manager, quick_interactions = configured_manager(settings)
    manager.codex_auth_reader = MagicMock(
        return_value=SimpleNamespace(state="available", auth_mode="account")
    )

    result = manager.dispatch(
        message_id="codex-auth-status",
        prompt="codex auth",
        message_type="text",
        correlation_id=None,
        source_ip="100.64.0.21",
        delivery_route=delivery_route(),
    )

    assert result.message == "Codex Auth: ChatGPT account signed in."
    quick_interactions.submit.assert_not_called()


def test_codex_auth_switch_uses_opposite_mode_and_notifies_final_result(settings) -> None:
    manager, _codex_manager, quick_interactions = configured_manager(settings)
    manager.codex_auth_reader = MagicMock(
        return_value=SimpleNamespace(state="available", auth_mode="account")
    )
    manager.codex_auth_switcher = MagicMock(
        return_value=SimpleNamespace(
            mode="api",
            account=SimpleNamespace(state="available", auth_mode="api"),
        )
    )
    completed = threading.Event()
    manager.codex_auth_notifier = MagicMock(
        side_effect=lambda _route, factory: (factory(), completed.set())
    )

    result = manager.dispatch(
        message_id="codex-auth-switch",
        prompt="codex auth switch",
        message_type="text",
        correlation_id="codex-auth-correlation",
        source_ip="100.64.0.21",
        delivery_route=delivery_route(),
    )

    assert result.message == (
        "Codex Auth: Switching to API Key mode. "
        "The result will be sent when completed."
    )
    assert completed.wait(2)
    manager.codex_auth_switcher.assert_called_once_with("api")
    assert manager.codex_auth_notifier.call_args.args[0] == delivery_route()
    assert manager.codex_auth_notifier.call_args.args[1]() == (
        "Codex Auth: Switched to API Key mode."
    )
    quick_interactions.submit.assert_not_called()


def test_codex_auth_switch_does_not_start_when_status_is_unknown(settings) -> None:
    manager, _codex_manager, quick_interactions = configured_manager(settings)
    manager.codex_auth_reader = MagicMock(
        return_value=SimpleNamespace(state="failed", auth_mode="unknown")
    )
    manager.codex_auth_switcher = MagicMock()

    result = manager.dispatch(
        message_id="codex-auth-unavailable",
        prompt="codex auth switch",
        message_type="text",
        correlation_id=None,
        source_ip="100.64.0.21",
        delivery_route=delivery_route(),
    )

    assert result.message == (
        "Codex Auth: Authentication status unavailable. Switch was not started."
    )
    manager.codex_auth_switcher.assert_not_called()
    quick_interactions.submit.assert_not_called()


def test_unavailable_codex_command_group_is_not_submitted_or_switched(settings) -> None:
    manager, _codex_manager, quick_interactions = configured_manager(settings)
    manager.codex_command_parser = MagicMock(
        side_effect=ApiError(
            409,
            "runtime_plugin_not_imported",
            "当前 Runtime 插件尚未导入，无法使用。",
        )
    )
    manager.codex_auth_reader = MagicMock()
    manager.codex_auth_switcher = MagicMock()

    status = manager.dispatch(
        message_id="codex-not-imported",
        prompt="codex auth",
        message_type="text",
        correlation_id=None,
        source_ip="100.64.0.21",
        delivery_route=delivery_route(),
    )
    switch = manager.dispatch(
        message_id="codex-not-imported-switch",
        prompt="codex auth switch",
        message_type="text",
        correlation_id=None,
        source_ip="100.64.0.21",
        delivery_route=delivery_route(),
    )

    assert status.message == (
        "Codex: The Codex Runtime is not imported. Import and enable it in Settings."
    )
    assert switch.message == status.message
    manager.codex_auth_reader.assert_not_called()
    manager.codex_auth_switcher.assert_not_called()
    quick_interactions.submit.assert_not_called()
