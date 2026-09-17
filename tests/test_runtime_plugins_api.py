from types import SimpleNamespace
from unittest.mock import ANY, AsyncMock, MagicMock

import httpx
import pytest

from app.application import create_app
from app.api import runtime_plugins
from app.ai_session.api_models import RuntimeImplementationData
from app.core.response import ApiError


@pytest.mark.anyio
async def test_legacy_runtime_lifecycle_routes_are_not_registered(settings) -> None:
    transport = httpx.ASGITransport(app=create_app(settings))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/api/runtime-modules")
    assert response.status_code == 404


@pytest.mark.anyio
async def test_runtime_lifecycle_is_exposed_only_through_plugins_api(settings) -> None:
    transport = httpx.ASGITransport(app=create_app(settings))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/api/plugins")
    runtime = next(item for item in response.json()["data"]["plugins"] if item["plugin_id"] == "runtime")
    assert response.status_code == 200
    assert any(item["artifact_id"] == "development:codex-runtime-dev" for item in runtime["artifacts"])
    assert all(item["description"] for item in runtime["artifacts"])


@pytest.mark.anyio
async def test_plugin_list_isolated_from_weixin_translation_state_failure(settings) -> None:
    app = create_app(settings)
    app.state.weixin_translation._state_error = True
    transport = httpx.ASGITransport(app=app)

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/api/plugins")

    assert response.status_code == 200
    weixin = next(
        item
        for item in response.json()["data"]["plugins"]
        if item["plugin_id"] == "weixin-orchestration"
    )
    assert isinstance(weixin["execution_ready"], bool)


@pytest.mark.anyio
async def test_plugin_list_keeps_running_when_weixin_runtime_status_is_unknown(
    settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = create_app(settings)

    def unavailable() -> bool:
        raise OSError("runtime status unavailable")

    monkeypatch.setattr(app.state.weixin_translation, "runtime_available", unavailable)
    transport = httpx.ASGITransport(app=app)

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/api/plugins")

    assert response.status_code == 200
    weixin = next(
        item
        for item in response.json()["data"]["plugins"]
        if item["plugin_id"] == "weixin-orchestration"
    )
    assert weixin["execution_ready"] is None


@pytest.mark.anyio
async def test_legacy_runtime_lifecycle_state_is_cleared(settings) -> None:
    app = create_app(settings)
    app.state.plugin_lifecycle.path.parent.mkdir(parents=True, exist_ok=True)
    app.state.plugin_lifecycle.path.write_text(
        '{"imports":{"codex-runtime":["development:codex-runtime"]},"enabled":{}}',
        encoding="utf-8",
    )
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/api/plugins")

    assert response.status_code == 200
    assert "codex-runtime" not in app.state.plugin_lifecycle.path.read_text("utf-8")


@pytest.mark.anyio
async def test_disabling_codex_keeps_its_imported_lifecycle_artifact_visible(settings) -> None:
    transport = httpx.ASGITransport(app=create_app(settings))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        await client.post(
            "/api/plugins/runtime/imports",
            json={"artifact_id": "development:codex-runtime-dev"},
        )
        disabled = await client.put(
            "/api/plugins/runtime/enabled",
            json={"artifact_id": "development:codex-runtime-dev", "enabled": False},
        )

    assert disabled.status_code == 200
    assert "development:codex-runtime-dev" in disabled.json()["data"]["imported_artifact_ids"]
    assert "development:codex-runtime-dev" not in disabled.json()["data"]["enabled_artifact_ids"]


@pytest.mark.anyio
async def test_codex_runtime_is_available_only_after_import_and_enablement(settings) -> None:
    app = create_app(settings)
    app.state.weixin_translation.reconcile_execution_settings = MagicMock()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        before = await client.get("/api/maintenance/quick-worker")
        imported = await client.post(
            "/api/plugins/runtime/imports",
            json={"artifact_id": "development:codex-runtime-dev"},
        )
        after_import = await client.get("/api/maintenance/quick-worker")
        enabled = await client.put(
            "/api/plugins/runtime/enabled",
            json={"artifact_id": "development:codex-runtime-dev", "enabled": True},
        )
        after_enablement = await client.get("/api/maintenance/quick-worker")

    assert before.json()["data"]["runtime_state"] == "unconfigured"
    assert imported.status_code == 200
    assert after_import.json()["data"]["runtime_state"] == "disabled"
    assert enabled.status_code == 200
    assert after_enablement.json()["data"]["runtime_state"] == "available"
    assert app.state.plugin_lifecycle.runtime_implementation_lifecycle_state(
        "codex-runtime-dev"
    ) == (True, True)
    app.state.weixin_translation.reconcile_execution_settings.assert_called_once_with()


@pytest.mark.anyio
async def test_default_runtime_implementation_change_reconciles_weixin_settings(settings) -> None:
    app = create_app(settings)
    app.state.weixin_translation.reconcile_execution_settings = MagicMock()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        await client.post(
            "/api/plugins/runtime/imports",
            json={"artifact_id": "development:codex-runtime-dev"},
        )
        await client.put(
            "/api/plugins/runtime/enabled",
            json={"artifact_id": "development:codex-runtime-dev", "enabled": True},
        )
        app.state.weixin_translation.reconcile_execution_settings.reset_mock()
        response = await client.put(
            "/api/ai/runtime-implementations/default",
            json={"implementation_id": "codex-runtime-dev"},
        )

    assert response.status_code == 200
    app.state.weixin_translation.reconcile_execution_settings.assert_called_once_with()


@pytest.mark.anyio
async def test_legacy_codex_runtime_writes_do_not_bypass_plugin_lifecycle(settings) -> None:
    app = create_app(settings)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        runtime = await client.put("/api/ai/runtimes/codex", json={"enabled": True})
        implementation = await client.put(
            "/api/ai/runtime-implementations/codex-runtime-dev/enabled",
            json={"enabled": True},
        )
        default = await client.put(
            "/api/ai/runtime-implementations/default",
            json={"implementation_id": "codex-runtime-dev"},
        )

    assert runtime.status_code == 409
    assert runtime.json()["error"]["code"] == "runtime_plugin_not_imported"
    assert implementation.status_code == 409
    assert implementation.json()["error"]["code"] == "plugin_not_imported"
    assert default.status_code == 409
    assert default.json()["error"]["code"] == "runtime_plugin_not_imported"


@pytest.mark.anyio
async def test_runtime_implementation_enable_uses_any_development_artifact(settings) -> None:
    app = create_app(settings)
    manager = app.state.ai_session_manager
    manager.development_runtime_implementation_ids = MagicMock(
        return_value=("second-runtime-dev",)
    )
    manager.runtime_id_for_implementation = MagicMock(return_value="second")
    manager.read_runtime_implementations = MagicMock(
        return_value=RuntimeImplementationData(
            runtime_id="codex",
            default_implementation_id=None,
            implementations=[],
        )
    )
    app.state.plugin_lifecycle.set_enabled = AsyncMock()
    transport = httpx.ASGITransport(app=app)

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.put(
            "/api/ai/runtime-implementations/second-runtime-dev/enabled",
            json={"enabled": True},
        )

    assert response.status_code == 200
    app.state.plugin_lifecycle.set_enabled.assert_awaited_once_with(
        ANY,
        "runtime",
        "development:second-runtime-dev",
        True,
    )
    manager.read_runtime_implementations.assert_called_once_with("second")


@pytest.mark.anyio
async def test_runtime_enable_rolls_back_preferences_when_lifecycle_write_fails(
    settings, monkeypatch
) -> None:
    app = create_app(settings)
    lifecycle = app.state.plugin_lifecycle
    manager = app.state.ai_session_manager
    transport = httpx.ASGITransport(app=app)

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        imported = await client.post(
            "/api/plugins/runtime/imports",
            json={"artifact_id": "development:codex-runtime-dev"},
        )

    assert imported.status_code == 200
    before = manager.runtime_implementation_preferences.read()

    def fail_write(_state):
        raise ApiError(
            503,
            "plugin_lifecycle_state_unavailable",
            "插件生命周期状态不可写。",
        )

    monkeypatch.setattr(lifecycle, "_write", fail_write)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.put(
            "/api/plugins/runtime/enabled",
            json={"artifact_id": "development:codex-runtime-dev", "enabled": True},
        )

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "runtime_lifecycle_state_rolled_back"
    assert manager.runtime_implementation_preferences.read() == before
    assert lifecycle.runtime_implementation_lifecycle_state("codex-runtime-dev") == (
        True,
        False,
    )


@pytest.mark.anyio
async def test_development_runtime_refresh_availability_uses_current_implementation_id(
    settings, monkeypatch
) -> None:
    app = create_app(settings)

    async def worker_ready(_request) -> str:
        return "worker-generation"

    async def implementation_idle(_request, implementation_id: str) -> None:
        assert implementation_id == "codex-runtime-dev"

    monkeypatch.setattr(runtime_plugins, "_worker_generation", worker_ready)
    monkeypatch.setattr(runtime_plugins, "_require_implementation_idle", implementation_idle)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        await client.post(
            "/api/plugins/runtime/imports",
            json={"artifact_id": "development:codex-runtime-dev"},
        )
        await client.put(
            "/api/plugins/runtime/enabled",
            json={"artifact_id": "development:codex-runtime-dev", "enabled": True},
        )
    response = await runtime_plugins.read_development_runtime_plugin_refresh_availability(
        "codex-runtime-dev",
        SimpleNamespace(app=app),
    )

    assert response.data.model_dump() == {"available": True, "reason": None}


def test_runtime_refresh_reports_missing_local_ai_dependency() -> None:
    with pytest.raises(ApiError) as rejected:
        runtime_plugins._require_runtime_refresh_confirmation(
            {
                "success": False,
                "error": {
                    "code": "runtime_unavailable",
                    "message": "Runtime is unavailable for background execution: codex-010001",
                },
            },
            expected_present=True,
        )

    assert rejected.value.code == "runtime_plugin_worker_runtime_unavailable"
    assert "所需的 AI 工具" in rejected.value.message
    assert "codex-010001" not in rejected.value.message


def test_runtime_removal_refresh_reports_registry_mismatch_accurately() -> None:
    with pytest.raises(ApiError) as rejected:
        runtime_plugins._require_runtime_refresh_confirmation(
            {
                "success": False,
                "error": {"code": "runtime_registry_refresh_unconfirmed"},
            },
            expected_present=False,
        )

    assert rejected.value.code == "runtime_plugin_worker_registry_mismatch"
    assert "移除目标 Runtime" in rejected.value.message


def test_runtime_refresh_reports_recoverable_worker_failures() -> None:
    with pytest.raises(ApiError) as busy:
        runtime_plugins._require_runtime_refresh_confirmation(
            {"success": False, "error": {"code": "runtime_implementation_busy"}},
            expected_present=False,
        )
    with pytest.raises(ApiError) as unsupported:
        runtime_plugins._require_runtime_refresh_confirmation(
            {"success": False, "error": {"code": "runtime_registry_refresh_unavailable"}},
            expected_present=True,
        )

    assert busy.value.code == "runtime_implementation_busy"
    assert "等待任务结束" in busy.value.message
    assert unsupported.value.code == "quick_worker_refresh_upgrade_required"
    assert "Worker 重载" in unsupported.value.message


def test_development_runtime_refresh_reports_specific_worker_failures() -> None:
    with pytest.raises(ApiError) as unavailable:
        runtime_plugins._require_development_refresh_worker_confirmation(
            {"success": False, "error": {"code": "runtime_unavailable"}}
        )
    with pytest.raises(ApiError) as mismatch:
        runtime_plugins._require_development_refresh_worker_confirmation(
            {"success": False, "error": {"code": "runtime_registry_refresh_unconfirmed"}}
        )

    assert unavailable.value.code == "runtime_plugin_worker_runtime_unavailable"
    assert "所需的 AI 工具" in unavailable.value.message
    assert mismatch.value.code == "runtime_plugin_worker_registry_mismatch"


@pytest.mark.anyio
async def test_runtime_install_reconciles_weixin_settings_after_worker_confirmation(
    settings, monkeypatch
) -> None:
    preview = SimpleNamespace(implementation_id="codex-010001")
    activation = SimpleNamespace()
    manager = MagicMock()
    manager.runtime_plugin_service.inspect_archive.return_value = preview
    manager.install_runtime_plugin.return_value = activation
    request = SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(
                settings=settings,
                ai_session_manager=manager,
                weixin_translation=MagicMock(),
            )
        )
    )
    monkeypatch.setattr(runtime_plugins, "log_operation", MagicMock(return_value="operation"))
    monkeypatch.setattr(runtime_plugins, "_require_implementation_idle", AsyncMock())
    monkeypatch.setattr(runtime_plugins, "_worker_generation", AsyncMock())
    monkeypatch.setattr(
        runtime_plugins,
        "refresh_runtime_registry",
        AsyncMock(return_value={"success": True}),
    )
    monkeypatch.setattr(runtime_plugins, "_confirm_worker_runtime", AsyncMock(return_value="generation"))

    result = await runtime_plugins.install_runtime_plugin_archive(
        request,
        "runtime.zip",
        b"archive",
    )

    assert result.module_id == "codex-010001"
    request.app.state.weixin_translation.reconcile_execution_settings.assert_called_once_with()


@pytest.mark.anyio
async def test_development_runtime_refresh_reconciles_weixin_settings_after_worker_confirmation(
    settings, monkeypatch
) -> None:
    implementation_id = "codex-runtime-dev"
    plugin = SimpleNamespace(manifest=SimpleNamespace(implementation_id=implementation_id, runtime_id="codex"))
    manager = MagicMock()
    manager.development_runtime_plugins.return_value = (plugin,)
    request = SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(
                settings=settings,
                ai_session_manager=manager,
                weixin_translation=MagicMock(),
            )
        )
    )
    monkeypatch.setattr(runtime_plugins, "log_operation", MagicMock(return_value="operation"))
    monkeypatch.setattr(runtime_plugins, "_require_implementation_idle", AsyncMock())
    monkeypatch.setattr(runtime_plugins, "_worker_generation", AsyncMock())
    monkeypatch.setattr(
        runtime_plugins,
        "refresh_runtime_registry",
        AsyncMock(return_value={"success": True}),
    )
    monkeypatch.setattr(runtime_plugins, "_confirm_worker_runtime", AsyncMock(return_value="generation"))

    response = await runtime_plugins.refresh_development_runtime_plugin(
        implementation_id,
        request,
    )

    assert response.data.module_id == implementation_id
    request.app.state.weixin_translation.reconcile_execution_settings.assert_called_once_with()


@pytest.mark.anyio
async def test_runtime_confirmation_distinguishes_worker_health_and_availability(
    settings, monkeypatch
) -> None:
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(settings=settings)))
    monkeypatch.setattr(
        runtime_plugins,
        "read_health",
        AsyncMock(
            return_value={
                "success": True,
                "data": {
                    "status": "ready",
                    "generation": "worker-generation",
                    "implementation_ids": ["codex-010001"],
                    "available_implementation_ids": [],
                },
            }
        ),
    )

    with pytest.raises(ApiError) as rejected:
        await runtime_plugins._confirm_worker_runtime(
            request,
            "codex-010001",
            expected_present=True,
        )

    assert rejected.value.code == "runtime_plugin_worker_runtime_unavailable"
    assert "所需的 AI 工具" in rejected.value.message


@pytest.mark.anyio
async def test_removing_codex_development_artifact_hides_runtime_navigation(settings) -> None:
    transport = httpx.ASGITransport(app=create_app(settings))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        await client.post(
            "/api/plugins/runtime/imports",
            json={"artifact_id": "development:codex-runtime-dev"},
        )
        imported_navigation = await client.get("/settings/runtime")
        removed = await client.delete(
            "/api/plugins/runtime/imports/development%3Acodex-runtime-dev",
        )
        removed_navigation = await client.get("/settings/runtime")

    assert 'href="/settings/runtime/codex"' in imported_navigation.text
    assert removed.status_code == 200
    assert removed.json()["data"]["imported_artifact_ids"] == []
    assert 'href="/settings/runtime/codex"' not in removed_navigation.text


@pytest.mark.anyio
async def test_system_upgrade_blocks_only_codex_plugin_lifecycle_writes(settings) -> None:
    app = create_app(settings)
    app.state.system_upgrade._writes_blocked = True
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        codex = await client.post(
            "/api/plugins/runtime/imports",
            json={"artifact_id": "development:codex-runtime-dev"},
        )
        deliveryline = await client.post(
            "/api/plugins/deliveryline/imports",
            json={"artifact_id": "development:deliveryline"},
        )

    assert codex.status_code == 409
    assert codex.json()["error"]["code"] == "system_upgrade_in_progress"
    assert deliveryline.status_code == 200
