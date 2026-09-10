from __future__ import annotations

import threading
from types import SimpleNamespace
from unittest.mock import MagicMock

from app.services.openclaw_weixin_chub_commands import (
    is_ai_runtime_write_command,
    parse_weixin_chub_command,
)

from tests.openclaw_weixin_chub_mode_helpers import (
    configured_manager,
    delivery_route,
)


def test_codex_auth_commands_are_fixed_and_have_expected_write_boundary() -> None:
    status = parse_weixin_chub_command("CODEX AUTH。")
    switch = parse_weixin_chub_command("codex auth switch")

    assert status.kind == "codex_auth"
    assert switch.kind == "codex_auth_switch"
    assert is_ai_runtime_write_command(status) is False
    assert is_ai_runtime_write_command(switch) is True
    assert parse_weixin_chub_command("codex auth switch now").kind == "normal"


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
