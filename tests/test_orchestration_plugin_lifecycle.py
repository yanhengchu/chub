import shutil
import sys
from types import ModuleType, SimpleNamespace

import httpx
import pytest

from app.ai_interactions.models import QuickInteractionWeixinRoute
from app.application import create_app


PLUGIN_ID = "chub-task-prompt-optimizer"


def _client(app):
    transport = httpx.ASGITransport(app=app)
    client = httpx.AsyncClient(transport=transport, base_url="http://test")
    return client


@pytest.mark.anyio
async def test_orchestration_enablement_changes_apply_to_next_task_without_web_restart(settings) -> None:
    app = create_app(settings)
    async with _client(app) as client:
        listed = (await client.get("/api/plugins")).json()["data"]["plugins"]
        plugin = next(item for item in listed if item["plugin_id"] == PLUGIN_ID)
        assert plugin["name"] == "任务编排插件"
        artifact_id = next(item["artifact_id"] for item in plugin["artifacts"] if item["source"] == "development")
        artifact = next(item for item in plugin["artifacts"] if item["artifact_id"] == artifact_id)
        assert artifact["name"] == "任务提示词优化"
        assert artifact["load_state"] == "not_loaded"
        assert plugin["imported_artifact_ids"] == [artifact_id]

        zip_rejected = await client.put(
            f"/api/plugins/{PLUGIN_ID}/enabled",
            json={"artifact_id": "zip:deferred.zip", "enabled": True},
        )
        assert zip_rejected.status_code == 409
        assert zip_rejected.json()["error"]["code"] == "plugin_artifact_lifecycle_unavailable"

        enabled = await client.put(
            f"/api/plugins/{PLUGIN_ID}/enabled",
            json={"artifact_id": artifact_id, "enabled": True},
        )
        enabled_data = enabled.json()["data"]
        enabled_artifact = next(item for item in enabled_data["artifacts"] if item["artifact_id"] == artifact_id)
        assert enabled_data["enabled_artifact_ids"] == [artifact_id]
        assert enabled_artifact["load_state"] == "loaded"
        assert enabled_artifact["reload_required"] is False
        assert enabled_data["loaded_artifact_ids"] == [artifact_id]
        descriptor = app.state.plugin_lifecycle.prompt_optimizer_execution_state()["descriptor"]
        assert descriptor.stage_kinds == ("prompt_optimization",)
        assert descriptor.stage_runner("direct", "normalized task") == "normalized task"
        assert "不执行原任务" in descriptor.optimization_prompt_builder("original requirement")
        result = descriptor.optimization_result_reader(
            '{"optimized_prompt_zh":"已规范化任务",'
            '"optimized_prompt_en":"Normalize the task"}'
        )
        assert result.chinese == "已规范化任务"
        assert result.english == "Normalize the task"
        for invalid in (
            '{"optimized_prompt_zh":""}',
            '{"optimized_prompt_zh":"任务","optimized_prompt_en":"Normalize","extra":true}',
            '```json\n{}\n```',
        ):
            with pytest.raises(ValueError):
                descriptor.optimization_result_reader(invalid)

        effective_settings = await client.get(f"/api/plugins/{PLUGIN_ID}/settings")
        assert effective_settings.status_code == 200
        assert effective_settings.json()["data"]["effective"] is True
        assert effective_settings.json()["data"]["effective_mode"] == "direct"
        assert app.state.task_orchestrator.is_prompt_optimizer_effective("weixin") is True

        submitted_prompts = []
        submitted_routes = []

        def fake_worker_submit(session_id, prompt, **kwargs):
            submitted_prompts.append((session_id, prompt))
            submitted_routes.append(kwargs.get("notification_route"))
            return SimpleNamespace(id=f"task-{len(submitted_prompts)}", status="requested")

        app.state.quick_interactions._recovery_ready = True
        app.state.quick_interactions.submit = fake_worker_submit
        direct_task = app.state.task_orchestrator.submit_web(
            session_id="web-session",
            prompt="normalized Web task",
            request_id="11111111-1111-4111-8111-111111111111",
            operation_id="web-op-1",
            source_ip="127.0.0.1",
        )
        direct_request = next(
            item for item in app.state.task_orchestrator._state.requests
            if item.task_id == direct_task.id
        )
        assert submitted_prompts == [("web-session", "normalized Web task")]
        assert direct_request.plugin_enabled is True
        assert direct_request.plugin_mode == "direct"
        direct_ref = direct_request.stage_chain[0].implementation_ref
        assert direct_request.implementation_ref == direct_ref
        assert direct_ref.startswith(f"development:{PLUGIN_ID}+")
        assert direct_ref != artifact_id
        assert direct_request.stage_chain[0].stage_id == "prompt_optimization"
        assert direct_request.cursor == 1
        assert direct_request.prompt is None

        weixin_route = QuickInteractionWeixinRoute(
            account_id="weixin-account",
            recipient="owner@im.wechat",
        )
        weixin_task = app.state.task_orchestrator.submit_weixin(
            message_id="weixin-message-1",
            route_fingerprint="a" * 64,
            session_id="weixin-session",
            prompt="normalized WeChat task",
            operation_id="weixin-op-1",
            source_ip="127.0.0.1",
            notification_route=weixin_route,
            summary_max_chars=48,
            summary_max_width=24,
        )
        weixin_request = next(
            item for item in app.state.task_orchestrator._state.requests
            if item.task_id == weixin_task.id
        )
        assert submitted_prompts[-1] == ("weixin-session", "normalized WeChat task")
        assert submitted_routes[-1] == weixin_route
        assert weixin_request.entry == "weixin"
        assert weixin_request.plugin_enabled is True
        assert weixin_request.plugin_mode == "direct"
        weixin_ref = weixin_request.stage_chain[0].implementation_ref
        assert weixin_request.implementation_ref == weixin_ref
        assert weixin_ref.startswith(f"development:{PLUGIN_ID}+")
        assert weixin_request.stage_chain[0].stage_id == "prompt_optimization"
        assert weixin_request.cursor == 1

        disabled = await client.put(
            f"/api/plugins/{PLUGIN_ID}/enabled",
            json={"artifact_id": artifact_id, "enabled": False},
        )
        disabled_data = disabled.json()["data"]
        disabled_artifact = next(item for item in disabled_data["artifacts"] if item["artifact_id"] == artifact_id)
        assert disabled_data["enabled_artifact_ids"] == []
        assert disabled_data["loaded_artifact_ids"] == [artifact_id]
        assert disabled_artifact["load_state"] == "loaded"
        assert disabled_artifact["reload_required"] is False
        assert (await client.get(f"/api/plugins/{PLUGIN_ID}/settings")).json()["data"]["effective"] is False

        plain_task = app.state.task_orchestrator.submit_web(
            session_id="web-session",
            prompt="normalized Web task after disable",
            request_id="22222222-2222-4222-8222-222222222222",
            operation_id="web-op-2",
            source_ip="127.0.0.1",
        )
        plain_request = next(
            item for item in app.state.task_orchestrator._state.requests
            if item.task_id == plain_task.id
        )
        assert submitted_prompts[-1] == ("web-session", "normalized Web task after disable")
        assert plain_request.plugin_enabled is False
        assert plain_request.stage_chain == []

        plain_weixin_task = app.state.task_orchestrator.submit_weixin(
            message_id="weixin-message-2",
            route_fingerprint="b" * 64,
            session_id="weixin-session",
            prompt="normalized WeChat task after disable",
            operation_id="weixin-op-2",
            source_ip="127.0.0.1",
            notification_route=weixin_route,
            summary_max_chars=48,
            summary_max_width=24,
        )
        plain_weixin_request = next(
            item for item in app.state.task_orchestrator._state.requests
            if item.task_id == plain_weixin_task.id
        )
        assert submitted_prompts[-1] == (
            "weixin-session",
            "normalized WeChat task after disable",
        )
        assert plain_weixin_request.plugin_enabled is False
        assert plain_weixin_request.stage_chain == []
        assert direct_request.plugin_enabled is True
        assert direct_request.stage_chain[0].implementation_ref == direct_ref
        assert weixin_request.plugin_enabled is True
        assert weixin_request.stage_chain[0].implementation_ref == weixin_ref

        reenabled = await client.put(
            f"/api/plugins/{PLUGIN_ID}/enabled",
            json={"artifact_id": artifact_id, "enabled": True},
        )
        assert reenabled.json()["data"]["enabled_artifact_ids"] == [artifact_id]
        third_task = app.state.task_orchestrator.submit_web(
            session_id="web-session",
            prompt="normalized Web task after re-enable",
            request_id="33333333-3333-4333-8333-333333333333",
            operation_id="web-op-3",
            source_ip="127.0.0.1",
        )
        third_request = next(
            item for item in app.state.task_orchestrator._state.requests
            if item.task_id == third_task.id
        )
        assert third_request.plugin_enabled is True
        assert third_request.stage_chain[0].implementation_ref == direct_ref


@pytest.mark.anyio
async def test_prompt_optimizer_settings_navigation_follows_development_registration(settings) -> None:
    app = create_app(settings)
    async with _client(app) as client:
        before = await client.get("/settings/runtime")
        assert 'class="settings-navigation-subgroup">任务编排插件</span>' in before.text
        assert f'href="/settings/{PLUGIN_ID}"' in before.text
        initially_available = await client.get(f"/settings/{PLUGIN_ID}")
        assert initially_available.status_code == 200

        plugin = next(
            item for item in (await client.get("/api/plugins")).json()["data"]["plugins"]
            if item["plugin_id"] == PLUGIN_ID
        )
        artifact_id = next(
            item["artifact_id"] for item in plugin["artifacts"]
            if item["source"] == "development"
        )
        assert artifact_id in plugin["imported_artifact_ids"]

        manager = await client.get("/settings/runtime")
        settings_page = await client.get(f"/settings/{PLUGIN_ID}")
        assert 'class="settings-navigation-subgroup">任务编排插件</span>' in manager.text
        assert f'href="/settings/{PLUGIN_ID}"' in manager.text
        assert settings_page.status_code == 200
        assert "提示词处理模式" in settings_page.text
        assert "auto" in settings_page.text
        assert "当前阶段链尚未接入" not in settings_page.text
        assert 'id="prompt-optimizer-mode-availability"' not in settings_page.text
        assert 'id="prompt-optimizer-settings-message"' not in settings_page.text

        removed = await client.delete(f"/api/plugins/{PLUGIN_ID}/imports/{artifact_id}")
        assert removed.status_code == 200
        after = await client.get("/settings/runtime")
        assert 'class="settings-navigation-subgroup">任务编排插件</span>' not in after.text
        assert (await client.get(f"/settings/{PLUGIN_ID}")).status_code == 404


@pytest.mark.anyio
async def test_prompt_optimizer_settings_are_persisted_and_auto_is_available(settings) -> None:
    app = create_app(settings)
    async with _client(app) as client:
        initial = await client.get(f"/api/plugins/{PLUGIN_ID}/settings")
        assert initial.status_code == 200
        initial_data = initial.json()["data"]
        assert initial_data["mode"] == "direct"
        assert initial_data["is_default"] is True
        assert initial_data["effective"] is False
        assert initial_data["auto_available"] is True

        plugin = next(
            item for item in (await client.get("/api/plugins")).json()["data"]["plugins"]
            if item["plugin_id"] == PLUGIN_ID
        )
        initial = await client.get(f"/api/plugins/{PLUGIN_ID}/settings")
        assert initial.status_code == 200
        initial_data = initial.json()["data"]
        assert initial_data["mode"] == "direct"
        assert initial_data["is_default"] is True
        assert initial_data["auto_available"] is True
        assert initial_data["auto_unavailable_reason"] is None
        assert initial_data["effective"] is False

        saved = await client.put(
            f"/api/plugins/{PLUGIN_ID}/settings",
            json={"mode": "direct"},
        )
        assert saved.status_code == 200
        assert saved.json()["data"]["is_default"] is False

        saved_auto = await client.put(
            f"/api/plugins/{PLUGIN_ID}/settings",
            json={"mode": "auto"},
        )
        assert saved_auto.status_code == 200
        assert saved_auto.json()["data"]["mode"] == "auto"
        unchanged = await client.get(f"/api/plugins/{PLUGIN_ID}/settings")
        assert unchanged.json()["data"]["mode"] == "auto"

    reloaded_app = create_app(settings)
    async with _client(reloaded_app) as client:
        persisted = await client.get(f"/api/plugins/{PLUGIN_ID}/settings")
        assert persisted.status_code == 200
        assert persisted.json()["data"]["mode"] == "auto"
        assert persisted.json()["data"]["is_default"] is False


@pytest.mark.anyio
async def test_enabled_orchestration_plugin_can_be_removed_without_web_restart(settings) -> None:
    app = create_app(settings)
    async with _client(app) as client:
        plugins = (await client.get("/api/plugins")).json()["data"]["plugins"]
        plugin = next(item for item in plugins if item["plugin_id"] == PLUGIN_ID)
        artifact_id = next(item["artifact_id"] for item in plugin["artifacts"] if item["source"] == "development")
        enabled = await client.put(
            f"/api/plugins/{PLUGIN_ID}/enabled",
            json={"artifact_id": artifact_id, "enabled": True},
        )
        enabled_artifact = next(
            item for item in enabled.json()["data"]["artifacts"]
            if item["artifact_id"] == artifact_id
        )
        assert enabled_artifact["removable"] is True

    async with _client(app) as client:
        removed = await client.delete(f"/api/plugins/{PLUGIN_ID}/imports/{artifact_id}")
        assert removed.status_code == 200
        data = removed.json()["data"]
        assert data["imported_artifact_ids"] == []
        assert data["enabled_artifact_ids"] == []
        artifact = next(item for item in data["artifacts"] if item["artifact_id"] == artifact_id)
        assert artifact["imported"] is False
        assert artifact["enabled"] is False
        assert artifact["loaded"] is False
        assert artifact["reload_required"] is False
        assert artifact["load_state"] == "not_loaded"


@pytest.mark.anyio
async def test_orchestration_load_failure_is_reported_without_blocking_plugin_api(settings, monkeypatch) -> None:
    from app.plugin_lifecycle.orchestration_loader import OrchestrationPluginLoadResult

    app = create_app(settings)
    async with _client(app) as client:
        plugin = next(
            item for item in (await client.get("/api/plugins")).json()["data"]["plugins"]
            if item["plugin_id"] == PLUGIN_ID
        )
        artifact_id = next(item["artifact_id"] for item in plugin["artifacts"] if item["source"] == "development")
        await client.put(f"/api/plugins/{PLUGIN_ID}/enabled", json={"artifact_id": artifact_id, "enabled": True})

    monkeypatch.setattr(
        "app.plugin_lifecycle.service.load_development_orchestration_plugin",
        lambda source, artifact, version: OrchestrationPluginLoadResult(
            artifact, "failed", reason="测试装载失败。"
        ),
    )
    failed_app = create_app(settings)
    async with _client(failed_app) as client:
        plugins = (await client.get("/api/plugins")).json()["data"]["plugins"]
        plugin = next(item for item in plugins if item["plugin_id"] == PLUGIN_ID)
        artifact = next(item for item in plugin["artifacts"] if item["artifact_id"] == artifact_id)
        assert artifact["load_state"] == "failed"
        assert artifact["load_reason"].startswith("测试装载失败。")
        health = await client.get("/api/health")
        assert health.status_code == 200


@pytest.mark.anyio
async def test_development_source_snapshots_follow_active_tasks_and_are_pruned_after_remove(
    settings, monkeypatch, tmp_path
) -> None:
    import app.plugin_lifecycle.service as lifecycle_service
    from app.core.module_sources import RegisteredModuleSource, registered_module_source

    original = registered_module_source("orchestration", PLUGIN_ID)
    assert original is not None
    source_root = tmp_path / "prompt-optimizer"
    shutil.copytree(original.root, source_root)
    development_source = RegisteredModuleSource(
        module_id=PLUGIN_ID,
        module_type="orchestration",
        root=source_root,
        source="bundled",
    )
    monkeypatch.setattr(
        lifecycle_service,
        "registered_module_source",
        lambda module_type, module_id: development_source
        if (module_type, module_id) == ("orchestration", PLUGIN_ID)
        else registered_module_source(module_type, module_id),
    )

    app = create_app(settings)
    app.state.prompt_optimizer_settings.save("direct")
    async with _client(app) as client:
        plugin = next(
            item for item in (await client.get("/api/plugins")).json()["data"]["plugins"]
            if item["plugin_id"] == PLUGIN_ID
        )
        stable_id = next(item["artifact_id"] for item in plugin["artifacts"] if item["source"] == "development")
        enabled = await client.put(
            f"/api/plugins/{PLUGIN_ID}/enabled",
            json={"artifact_id": stable_id, "enabled": True},
        )
        assert enabled.status_code == 200

        app.state.quick_interactions._recovery_ready = True
        app.state.quick_interactions.submit = lambda _session, _prompt, **kwargs: SimpleNamespace(
            id=kwargs["operation_id"], status="requested"
        )

        first = app.state.task_orchestrator.submit_web(
            session_id="snapshot-session",
            prompt="first source snapshot",
            request_id="11111111-1111-4111-8111-111111111111",
            operation_id="snapshot-task-1",
            source_ip="127.0.0.1",
        )
        first_request = next(item for item in app.state.task_orchestrator._state.requests if item.task_id == first.id)
        first_ref = first_request.implementation_ref
        first_snapshot = app.state.plugin_lifecycle._snapshot_path(PLUGIN_ID, first_ref)
        assert first_snapshot.is_dir()
        assert first_snapshot.is_relative_to(settings.ai_runtime.shared.state_dir.parent.parent)

        entry_file = source_root / "entry.py"
        entry_file.write_text(entry_file.read_text(encoding="utf-8") + "\n# development revision 2\n", encoding="utf-8")
        second = app.state.task_orchestrator.submit_web(
            session_id="snapshot-session",
            prompt="second source snapshot",
            request_id="22222222-2222-4222-8222-222222222222",
            operation_id="snapshot-task-2",
            source_ip="127.0.0.1",
        )
        second_request = next(item for item in app.state.task_orchestrator._state.requests if item.task_id == second.id)
        second_ref = second_request.implementation_ref
        second_snapshot = app.state.plugin_lifecycle._snapshot_path(PLUGIN_ID, second_ref)
        assert second_ref != first_ref
        assert second_snapshot.is_dir()
        assert first_snapshot.is_dir()
        from app.plugin_lifecycle import PluginLifecycleService

        restarted_lifecycle = PluginLifecycleService(settings, app.state.ai_session_manager)
        restored_descriptor = restarted_lifecycle.resolve_orchestration_implementation(first_ref)
        assert restored_descriptor is not None
        assert restored_descriptor.stage_runner("direct", "restored old task") == "restored old task"
        assert restarted_lifecycle.resolve_orchestration_implementation(
            f"development:{PLUGIN_ID}+{'0' * 64}"
        ) is None

        namespace = f"_chub_orchestration_{PLUGIN_ID.replace('-', '_')}_{first_ref.rsplit('+', 1)[1][:16]}"
        assert namespace in sys.modules
        finished = SimpleNamespace(id=first.id, status="succeeded")
        app.state.quick_interactions._task_finished_handler(finished)
        assert not first_snapshot.exists()
        assert second_snapshot.is_dir()
        assert namespace not in sys.modules

        second_namespace = f"_chub_orchestration_{PLUGIN_ID.replace('-', '_')}_{second_ref.rsplit('+', 1)[1][:16]}"
        assert second_namespace in sys.modules
        final = SimpleNamespace(id=second.id, status="succeeded")
        app.state.quick_interactions._task_finished_handler(final)
        removed = await client.delete(f"/api/plugins/{PLUGIN_ID}/imports/{stable_id}")
        assert removed.status_code == 200
        assert not second_snapshot.exists()
        assert second_namespace not in sys.modules
        assert removed.json()["data"]["imported_artifact_ids"] == []


def test_development_orchestration_loader_unloads_package_namespace() -> None:
    from app.plugin_lifecycle.orchestration_loader import unload_development_orchestration_plugin

    implementation_ref = f"development:{PLUGIN_ID}+{'a' * 64}"
    namespace = f"_chub_orchestration_{PLUGIN_ID.replace('-', '_')}_{'a' * 16}"
    root = ModuleType(namespace)
    child = ModuleType(f"{namespace}.helper")
    sibling = ModuleType(f"{namespace}_other.helper")
    sys.modules[namespace] = root
    sys.modules[child.__name__] = child
    sys.modules[sibling.__name__] = sibling
    try:
        unload_development_orchestration_plugin(implementation_ref)
        assert namespace not in sys.modules
        assert child.__name__ not in sys.modules
        assert sibling.__name__ in sys.modules
    finally:
        sys.modules.pop(sibling.__name__, None)
