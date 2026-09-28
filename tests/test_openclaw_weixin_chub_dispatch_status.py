from __future__ import annotations

from types import SimpleNamespace

from app.ai_interactions.task_orchestration import PromptOptimizerStageSelection
from app.ai_session.models import utc_now
from tests.openclaw_weixin_chub_mode_helpers import configured_manager, delivery_route


def test_text_commands_are_retired_without_creating_a_worker_task(settings) -> None:
    manager, _sessions, quick_interactions = configured_manager(settings)

    for prompt in ("text list", "text-check ok"):
        result = manager.dispatch(
            message_id=f"retired-{prompt}",
            prompt=prompt,
            message_type="text",
            correlation_id=None,
            source_ip="127.0.0.1",
            delivery_route=delivery_route(),
        )
        assert result.disposition == "reply"
        assert result.message == "Text processing is unavailable. Please submit a normal task instead."
    quick_interactions.submit.assert_not_called()


def test_normal_message_uses_enabled_direct_stage_and_preserves_route(settings) -> None:
    manager, _sessions, quick_interactions = configured_manager(settings)
    quick_interactions.submit.return_value = SimpleNamespace(
        id="task-1",
        status="requested",
        summary="检查状态",
        created_at=utc_now(),
    )
    implementation_ref = "development:chub-task-prompt-optimizer+" + "d" * 64
    stage_calls = []
    manager.task_orchestrator.set_prompt_optimizer_stage_provider(
        lambda entry: PromptOptimizerStageSelection(
            enabled=entry == "weixin",
            mode="direct" if entry == "weixin" else None,
            implementation_ref=implementation_ref if entry == "weixin" else None,
            stage_runner=(
                (lambda mode, prompt: stage_calls.append((mode, prompt)) or prompt)
                if entry == "weixin"
                else None
            ),
        )
    )
    kwargs = dict(
        message_id="normal-1",
        prompt="检查状态",
        message_type="text",
        correlation_id="correlation-1",
        source_ip="127.0.0.1",
        delivery_route=delivery_route(),
    )

    result = manager.dispatch(**kwargs)

    assert result.disposition == "reply"
    assert result.message.startswith("Submitted\n\n")
    quick_interactions.submit.assert_called_once()
    assert quick_interactions.submit.call_args.kwargs["notification_route"] == delivery_route()
    request = manager.task_orchestrator._state.requests[0]
    assert request.entry == "weixin"
    assert request.plugin_enabled is True
    assert request.implementation_ref == implementation_ref
    assert request.stage_chain[0].stage_id == "prompt_optimization"
    assert request.cursor == 1
    assert request.task_id == "task-1"
    assert stage_calls == [("direct", "检查状态")]

    replay = manager.dispatch(**kwargs)
    assert replay.message == result.message
    quick_interactions.submit.assert_called_once()
    assert stage_calls == [("direct", "检查状态")]
    serialized = manager.task_orchestrator.path.read_text()
    assert "weixin-account" not in serialized
    assert "owner@im.wechat" not in serialized


def test_duplicate_normal_message_replays_without_second_submission(settings) -> None:
    manager, _sessions, quick_interactions = configured_manager(settings)
    quick_interactions.submit.return_value = SimpleNamespace(
        id="task-1",
        status="requested",
        summary="检查状态",
        created_at=utc_now(),
    )
    kwargs = dict(
        message_id="normal-duplicate",
        prompt="检查状态",
        message_type="text",
        correlation_id=None,
        source_ip="127.0.0.1",
        delivery_route=delivery_route(),
    )

    first = manager.dispatch(**kwargs)
    second = manager.dispatch(**kwargs)

    assert first.message == second.message
    quick_interactions.submit.assert_called_once()
