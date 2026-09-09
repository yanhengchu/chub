from __future__ import annotations

import io
import json
import zipfile
from types import SimpleNamespace

import pytest

from app.core.response import ApiError
from app.codex.models import utc_now
from app.services.openclaw_weixin_chub_models import WeixinTaskOrchestrationRequest
from app.services.weixin_orchestration_modules import WeixinOrchestrationModuleService
from scripts.build_weixin_orchestration_module_zip import build as build_weixin_module_zip
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
    service = WeixinOrchestrationModuleService(settings)

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


def test_rejects_invalid_module_protocol(settings, tmp_path) -> None:
    settings.openclaw.weixin_chub_mode.orchestration_modules_dir = tmp_path / "modules"
    service = WeixinOrchestrationModuleService(settings)
    archive = module_archive(settings)

    with pytest.raises(ApiError) as error:
        service.inspect_archive(archive, source_name="not-a-zip.txt")

    assert error.value.code == "weixin_orchestration_module_invalid"


def test_repository_module_builds_and_installs(settings, tmp_path) -> None:
    settings.openclaw.weixin_chub_mode.orchestration_modules_dir = tmp_path / "modules"
    archive_path = build_weixin_module_zip(tmp_path / "weixin-refinement.zip")
    service = WeixinOrchestrationModuleService(settings)

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
    service = WeixinOrchestrationModuleService(settings)
    module = service.install(module_archive(settings), source_name="refiner.zip")
    manager.orchestration_module_service = service
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
        manager.remove_orchestration_module(module.implementation_ref)
    assert referenced_error.value.code == "weixin_orchestration_module_referenced"


def test_finished_module_request_can_be_removed(settings, tmp_path) -> None:
    settings.openclaw.weixin_chub_mode.orchestration_modules_dir = tmp_path / "modules"
    manager, _codex_manager, _quick_interactions = configured_manager(settings)
    service = WeixinOrchestrationModuleService(settings)
    module = service.install(module_archive(settings), source_name="refiner.zip")
    manager.orchestration_module_service = service
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
    manager.set_orchestration_implementation("internal")

    manager.remove_orchestration_module(module.implementation_ref)

    assert service.list_artifacts() == ()


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
