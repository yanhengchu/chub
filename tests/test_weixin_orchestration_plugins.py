from __future__ import annotations

import io
import json
import zipfile
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from app.core.response import ApiError
from app.codex.models import utc_now
from app.services.openclaw_weixin_chub_models import WeixinTaskOrchestrationRequest
from app.services.weixin_orchestration_plugins import WeixinOrchestrationPluginService
from scripts.build_weixin_orchestration_plugin_zip import build as build_weixin_plugin_zip
from tests.openclaw_weixin_chub_mode_helpers import configured_manager, delivery_route


def module_archive(
    settings,
    *,
    module_id: str = "weixin-refiner",
    version: str = "1.0.0",
    marker: str = "one",
) -> bytes:
    manifest = {
        "protocol_version": 1,
        "module_id": module_id,
        "version": version,
        "module_type": "capability-orchestration",
        "scope": "weixin-normal-text",
        "orchestration_protocol_version": 1,
        "capability_protocol_version": 1,
        "display_name": "Weixin Refiner",
        "description": "Refines Weixin task text through Chub's bounded callback.",
        "chub_version": settings.app.version,
        "entry": "module:execute_refinement",
    }
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        archive.writestr(
            "chub-capability-orchestration.json",
            json.dumps(manifest),
        )
        archive.writestr(
            "module.py",
            f"MARKER = {marker!r}\n"
            "def execute_refinement(*, enqueue_refinement):\n"
            "    return enqueue_refinement()\n",
        )
    return output.getvalue()


def test_install_keeps_same_version_artifacts_immutable(settings, tmp_path) -> None:
    settings.openclaw.weixin_chub_mode.orchestration_modules_dir = tmp_path / "modules"
    service = WeixinOrchestrationPluginService(settings)

    first = service.install(module_archive(settings, marker="first"), source_name="first.zip")
    second = service.install(module_archive(settings, marker="second"), source_name="second.zip")

    assert first.module_id == second.module_id
    assert first.version == second.version
    assert first.implementation_ref != second.implementation_ref
    assert len(service.list_artifacts()) == 2
    assert service.execute_refinement(
        implementation_ref=first.implementation_ref,
        enqueue_refinement=lambda: "accepted",
    ) == "accepted"


def test_rejects_invalid_plugin_protocol(settings, tmp_path) -> None:
    settings.openclaw.weixin_chub_mode.orchestration_modules_dir = tmp_path / "modules"
    service = WeixinOrchestrationPluginService(settings)
    archive = module_archive(settings)

    with pytest.raises(ApiError) as error:
        service.inspect_archive(archive, source_name="not-a-zip.txt")

    assert error.value.code == "weixin_orchestration_plugin_invalid"


def test_repository_module_builds_and_installs(settings, tmp_path) -> None:
    settings.openclaw.weixin_chub_mode.orchestration_modules_dir = tmp_path / "modules"
    archive_path = build_weixin_plugin_zip(tmp_path / "weixin-refinement.zip")
    service = WeixinOrchestrationPluginService(settings)

    preview = service.install(archive_path.read_bytes(), source_name=archive_path.name)

    assert preview.module_id == "weixin-refinement"
    assert service.execute_refinement(
        implementation_ref=preview.implementation_ref,
        enqueue_refinement=lambda: True,
    ) is True


def test_module_selection_snapshots_request_and_blocks_referenced_removal(
    settings,
    tmp_path,
) -> None:
    settings.openclaw.weixin_chub_mode.orchestration_modules_dir = tmp_path / "modules"
    manager, _codex_manager, quick_interactions = configured_manager(settings)
    service = WeixinOrchestrationPluginService(settings)
    module = service.install(module_archive(settings), source_name="refiner.zip")
    manager.orchestration_plugin_service = service
    manager.translation_manager = SimpleNamespace(
        enqueue=lambda **_kwargs: True,
        entry_for_orchestration=lambda _orchestration_id: SimpleNamespace(id="translation-1"),
    )

    manager.set_orchestration_implementation("module", module.implementation_ref)
    manager.submit(
        message_id="module-request",
        prompt="检查服务",
        correlation_id=None,
        source_ip="127.0.0.1",
        delivery_route=delivery_route(),
        preprocess=True,
    )

    request = manager._state.orchestration_requests[0]
    assert request.implementation == module.implementation_ref
    assert request.stage_chain[0].kind == "module"
    assert request.stage_chain[0].implementation_ref == module.implementation_ref
    assert request.checkpoint == "module.weixin_refinement.queued"
    quick_interactions.submit.assert_not_called()

    with pytest.raises(ApiError) as referenced_error:
        manager.remove_orchestration_plugin(module.implementation_ref)
    assert referenced_error.value.code == "weixin_orchestration_plugin_referenced"


def test_finished_module_request_can_be_removed(settings, tmp_path) -> None:
    settings.openclaw.weixin_chub_mode.orchestration_modules_dir = tmp_path / "modules"
    manager, _codex_manager, _quick_interactions = configured_manager(settings)
    service = WeixinOrchestrationPluginService(settings)
    module = service.install(module_archive(settings), source_name="refiner.zip")
    manager.orchestration_plugin_service = service
    manager.set_orchestration_implementation("module", module.implementation_ref)
    manager._state.orchestration_requests.append(
        WeixinTaskOrchestrationRequest(
            id="f" * 36,
            message_id="finished-module-request",
            operation_id="operation",
            implementation=module.implementation_ref,
            task_kind="text_processing",
            stage_chain=[],
            checkpoint="task.finished",
            status="completed",
            created_at=utc_now(),
            updated_at=utc_now(),
        )
    )
    manager.remove_orchestration_plugin(module.implementation_ref)

    assert service.list_artifacts() == ()
    assert manager.orchestration_settings().implementation == "disabled"
    assert manager.orchestration_settings().enabled is False


def test_plugin_implementation_change_preserves_disabled_enablement(settings, tmp_path) -> None:
    settings.openclaw.weixin_chub_mode.orchestration_modules_dir = tmp_path / "modules"
    manager, _codex_manager, _quick_interactions = configured_manager(settings)
    service = WeixinOrchestrationPluginService(settings)
    module = service.install(module_archive(settings), source_name="refiner.zip")
    manager.orchestration_plugin_service = service

    manager.set_orchestration_implementation("weixin-orchestration-dev")
    manager.set_orchestration_enabled(False)
    disabled = manager.set_orchestration_implementation("module", module.implementation_ref)

    assert disabled.implementation == "module"
    assert disabled.module_ref == module.implementation_ref
    assert disabled.enabled is False
    with pytest.raises(ApiError) as error:
        manager.require_orchestration_implementation_available()
    assert error.value.code == "weixin_orchestration_plugin_disabled"


def test_active_plugin_removal_failure_restores_selection_and_enablement(
    settings,
    tmp_path,
    monkeypatch,
) -> None:
    settings.openclaw.weixin_chub_mode.orchestration_modules_dir = tmp_path / "modules"
    manager, _codex_manager, _quick_interactions = configured_manager(settings)
    service = WeixinOrchestrationPluginService(settings)
    module = service.install(module_archive(settings), source_name="refiner.zip")
    manager.orchestration_plugin_service = service
    manager.set_orchestration_implementation("module", module.implementation_ref)
    manager.set_orchestration_enabled(True)
    monkeypatch.setattr(
        service,
        "remove",
        MagicMock(side_effect=ApiError(503, "plugin_remove_failed", "remove failed")),
    )

    with pytest.raises(ApiError) as error:
        manager.remove_orchestration_plugin(module.implementation_ref)

    assert error.value.code == "plugin_remove_failed"
    restored = manager.orchestration_settings()
    assert restored.implementation == "module"
    assert restored.module_ref == module.implementation_ref
    assert restored.enabled is True


def test_broken_module_directory_does_not_block_internal_submission(
    settings,
    tmp_path,
) -> None:
    target = tmp_path / "module-target"
    target.mkdir()
    broken_root = tmp_path / "modules-link"
    broken_root.symlink_to(target, target_is_directory=True)
    settings.openclaw.weixin_chub_mode.orchestration_modules_dir = broken_root

    manager, _codex_manager, quick_interactions = configured_manager(settings)
    manager.submit(
        message_id="internal-after-module-directory-failure",
        prompt="检查服务",
        correlation_id=None,
        source_ip="127.0.0.1",
        delivery_route=delivery_route(),
    )

    quick_interactions.submit.assert_called_once()
