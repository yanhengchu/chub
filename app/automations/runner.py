from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import shutil
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse
from uuid import uuid4
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from app.automations.browser import session_factory
from app.automations.config import load_automations, load_linked_documents_extension
from app.automations.extensions import (
    ExtensionFailed,
    extract_linked_documents,
    linked_filename,
)
from app.automations.lock import LockBusy, file_lock
from app.automations.models import (
    AutomationState,
    AutomationStep,
    AutomationTaskConfig,
    LinkedDocumentResult,
)
from app.automations.operations import log_final_operation
from app.automations.store import AutomationStateStore
from app.automations.weekly_validation import (
    WeeklyValidationError,
    validate_weekly_linked_document,
    validate_weekly_main_document,
)
from app.core.config import Settings
from app.services.weekly_reports import WEEKLY_REPORTS_ROOT, reporting_period


LOGGER = logging.getLogger("hub.automations")
SIGNATURES = {"pdf": b"%PDF-", "zip": b"PK"}
_RUN_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,128}$")
_LINKED_SOURCES_INDEX = ".sources.json"
_MAX_LINKED_SOURCES_INDEX_BYTES = 64 * 1024


class AutomationFailed(Exception):
    pass


def _ensure_private_directory(directory: Path, root: Path) -> None:
    root.mkdir(parents=True, exist_ok=True)
    root.chmod(0o700)
    current = root
    for part in directory.relative_to(root).parts:
        current /= part
        current.mkdir(exist_ok=True)
        current.chmod(0o700)


def _host_allowed(url: str, allowed_hosts: list[str]) -> bool:
    host = (urlparse(url).hostname or "").lower().rstrip(".")
    return host in allowed_hosts


def _check_navigation(url: str, task: AutomationTaskConfig) -> None:
    if url == "about:blank":
        return
    if _host_allowed(url, task.login.redirect_hosts):
        raise AutomationFailed(task.login.expired_message)
    if not _host_allowed(url, task.browser.allowed_hosts):
        raise AutomationFailed("页面跳转到了未允许的域名")


def _output_path(
    task: AutomationTaskConfig,
    artifacts_dir: Path,
    *,
    output_root: Path | None = None,
) -> Path:
    try:
        timezone = ZoneInfo(task.output.timezone)
    except ZoneInfoNotFoundError as exc:
        raise AutomationFailed("输出时区无效") from exc
    try:
        filename = task.output.filename.format(date=datetime.now(timezone))
    except (KeyError, ValueError) as exc:
        raise AutomationFailed("输出文件名格式无效") from exc
    if Path(filename).name != filename:
        raise AutomationFailed("输出文件名包含非法路径")
    resolved_output_root = (output_root or artifacts_dir).resolve()
    directory = (resolved_output_root / task.output.directory).resolve()
    if not directory.is_relative_to(resolved_output_root):
        raise AutomationFailed("输出目录超出受控范围")
    return directory / filename


def _validate_download(
    path: Path,
    task: AutomationTaskConfig,
    *,
    target_suffix: str | None = None,
) -> int:
    size = path.stat().st_size
    if task.validation.non_empty and size == 0:
        raise AutomationFailed("下载文件为空")
    if size < task.validation.min_bytes:
        raise AutomationFailed("下载文件小于配置的最小大小")
    extension = (target_suffix or path.suffix).lower()
    if extension not in task.validation.extensions:
        raise AutomationFailed("下载文件扩展名不符合配置")
    if task.validation.signature == "markdown":
        try:
            content = path.read_text(encoding="utf-8-sig")
        except UnicodeDecodeError as exc:
            raise AutomationFailed("下载文件不是有效的 UTF-8 Markdown 文本") from exc
        if "\x00" in content:
            raise AutomationFailed("下载文件不是有效的 Markdown 文本")
    else:
        expected = SIGNATURES[task.validation.signature]
        with path.open("rb") as file:
            actual = file.read(len(expected))
        if actual != expected:
            raise AutomationFailed("下载文件签名校验失败")
    return size


async def _retry_safe(operation, retries: int) -> None:
    for attempt in range(retries + 1):
        try:
            await operation()
            return
        except Exception:
            if attempt >= retries:
                raise


async def _perform_action(page, step: AutomationStep) -> None:
    if step.action == "goto":
        await page.goto(step.url, timeout=step.timeout_ms, wait_until="domcontentloaded")
        return
    locator = page.locator(step.selector)
    if step.action == "wait":
        await locator.wait_for(state="visible", timeout=step.timeout_ms)
    elif step.action == "hover":
        await locator.hover(timeout=step.timeout_ms)
    elif step.action == "click":
        await locator.click(timeout=step.timeout_ms)
    elif step.action == "dispatch_event":
        await locator.dispatch_event(step.event, timeout=step.timeout_ms)


async def _run_browser_task(
    task: AutomationTaskConfig,
    settings: Settings,
    run_id: str,
    *,
    output_root: Path | None = None,
) -> tuple[Path, int, bool]:
    task_pages = []

    def track_page(page) -> None:
        if page in task_pages:
            return
        task_pages.append(page)
        page.on("popup", track_page)

    session = session_factory()
    async with session(ensure_page=True) as chrome:
        page = await chrome.context.new_page()
        track_page(page)
        try:
            await _retry_safe(
                lambda: page.goto(
                    task.browser.start_url,
                    timeout=task.execution.timeout_ms,
                    wait_until="domcontentloaded",
                ),
                task.execution.safe_step_retries,
            )
            _check_navigation(page.url, task)
            try:
                await page.locator(task.login.check.selector).wait_for(
                    state="visible",
                    timeout=task.login.check.timeout_ms,
                )
            except Exception as exc:
                raise AutomationFailed(task.login.expired_message) from exc

            download = None
            for index, step in enumerate(task.steps, start=1):
                try:
                    if step.action == "goto":
                        _check_navigation(step.url or "", task)

                    if step.expect == "download":
                        async with page.expect_download(timeout=step.timeout_ms) as info:
                            await _perform_action(page, step)
                        download = await info.value
                    elif step.action in {"goto", "wait"}:
                        await _retry_safe(
                            lambda step=step: _perform_action(page, step),
                            task.execution.safe_step_retries,
                        )
                    else:
                        await _perform_action(page, step)

                    _check_navigation(page.url, task)
                    for owned_page in task_pages:
                        if not owned_page.is_closed():
                            _check_navigation(owned_page.url, task)
                except AutomationFailed:
                    raise
                except Exception as exc:
                    raise AutomationFailed(
                        f"步骤 {index}（{step.action}）执行失败"
                    ) from exc

            if download is None:
                raise AutomationFailed("任务没有捕获到下载事件")

            target = _output_path(
                task,
                settings.automations.artifacts_dir,
                output_root=output_root,
            )
            resolved_output_root = (
                output_root or settings.automations.artifacts_dir
            ).resolve()
            _ensure_private_directory(target.parent, resolved_output_root)
            temporary = target.with_name(f".{target.name}.{run_id}.tmp")
            try:
                await download.save_as(temporary)
                temporary.chmod(0o600)
                size = _validate_download(
                    temporary,
                    task,
                    target_suffix=target.suffix,
                )
                if target.exists():
                    if task.output.conflict == "skip":
                        target.chmod(0o600)
                        return target, target.stat().st_size, True
                    if task.output.conflict == "fail":
                        raise AutomationFailed("目标文件已经存在")
                os.replace(temporary, target)
                target.chmod(0o600)
                return target, size, False
            finally:
                temporary.unlink(missing_ok=True)
        finally:
            for owned_page in reversed(task_pages):
                try:
                    if not owned_page.is_closed():
                        await owned_page.close()
                except Exception:
                    LOGGER.warning("Unable to close automation page", exc_info=True)


def _run_task_once(
    task: AutomationTaskConfig,
    settings: Settings,
    run_id: str,
    *,
    output_root: Path | None = None,
) -> tuple[Path, int, bool]:
    browser_kwargs = {"output_root": output_root} if output_root else {}
    return asyncio.run(
        asyncio.wait_for(
            _run_browser_task(task, settings, run_id, **browser_kwargs),
            timeout=task.execution.timeout_ms / 1000,
        )
    )


def _prune_linked_markdown_files(
    directory: Path,
    artifacts_dir: Path,
    expected_filenames: set[str],
    *,
    output_root: Path | None = None,
) -> None:
    resolved_output_root = (output_root or artifacts_dir).resolve()
    resolved_directory = (resolved_output_root / directory).resolve()
    if not resolved_directory.is_relative_to(resolved_output_root):
        raise AutomationFailed("关联文档输出目录超出受控范围")
    _ensure_private_directory(resolved_directory, resolved_output_root)
    for entry in resolved_directory.iterdir():
        if (
            entry.suffix.lower() == ".md"
            and entry.name not in expected_filenames
            and (entry.is_file() or entry.is_symlink())
        ):
            entry.unlink()


def _linked_sources_index_path(
    directory: Path,
    artifacts_dir: Path,
    *,
    output_root: Path | None = None,
) -> Path:
    resolved_output_root = (output_root or artifacts_dir).resolve()
    resolved_directory = (resolved_output_root / directory).resolve()
    if not resolved_directory.is_relative_to(resolved_output_root):
        raise AutomationFailed("关联文档输出目录超出受控范围")
    _ensure_private_directory(resolved_directory, resolved_output_root)
    return resolved_directory / _LINKED_SOURCES_INDEX


def _read_linked_sources_index(
    directory: Path,
    artifacts_dir: Path,
    *,
    output_root: Path | None = None,
) -> dict[str, str]:
    path = _linked_sources_index_path(
        directory,
        artifacts_dir,
        output_root=output_root,
    )
    try:
        if not path.is_file() or path.stat().st_size > _MAX_LINKED_SOURCES_INDEX_BYTES:
            return {}
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return {}
    if not isinstance(payload, dict):
        return {}
    return {
        url: filename
        for url, filename in payload.items()
        if isinstance(url, str)
        and isinstance(filename, str)
        and Path(filename).name == filename
        and filename.endswith(".md")
    }


def _write_linked_sources_index(
    directory: Path,
    artifacts_dir: Path,
    sources: dict[str, str],
    *,
    output_root: Path | None = None,
) -> None:
    path = _linked_sources_index_path(
        directory,
        artifacts_dir,
        output_root=output_root,
    )
    temporary = path.with_name(f"{path.name}.{uuid4().hex}.tmp")
    try:
        temporary.write_text(
            json.dumps(sources, ensure_ascii=False, sort_keys=True), encoding="utf-8"
        )
        temporary.chmod(0o600)
        os.replace(temporary, path)
    except OSError as exc:
        temporary.unlink(missing_ok=True)
        raise AutomationFailed("关联文档来源索引无法更新") from exc


def _run_linked_documents(
    task: AutomationTaskConfig,
    source: Path,
    settings: Settings,
    run_id: str,
    *,
    output_root: Path | None = None,
    linked_directory: Path | None = None,
) -> list[LinkedDocumentResult]:
    if task.extension is None:
        return []
    try:
        extension = load_linked_documents_extension(task.extension)
        documents = extract_linked_documents(
            source,
            task.browser.start_url,
            extension,
        )
    except (RuntimeError, ExtensionFailed) as exc:
        raise AutomationFailed(str(exc)) from exc

    output_directory = linked_directory or task.output.directory / "linked"
    used_filenames: set[str] = set()
    filenames_by_url = {
        document.url: linked_filename(
            document.name,
            index,
            used_filenames,
            identifier=document.url,
        )
        for index, document in enumerate(documents, start=1)
    }
    previous_filenames = _read_linked_sources_index(
        output_directory,
        settings.automations.artifacts_dir,
        output_root=output_root,
    )
    current_urls = set(filenames_by_url)
    _prune_linked_markdown_files(
        output_directory,
        settings.automations.artifacts_dir,
        set(filenames_by_url.values())
        | {
            filename
            for url, filename in previous_filenames.items()
            if url in current_urls
        },
        output_root=output_root,
    )
    current_filenames = {
        url: filename
        for url, filename in previous_filenames.items()
        if url in current_urls
    }
    results = []
    for index, document in enumerate(documents, start=1):
        filename = filenames_by_url[document.url]
        linked_task = task.model_copy(
            update={
                "name": document.name,
                "extension": None,
                "browser": task.browser.model_copy(
                    update={
                        "start_url": document.url,
                        "allowed_hosts": [urlparse(document.url).hostname],
                    }
                ),
                "output": task.output.model_copy(
                    update={
                        "directory": output_directory,
                        "filename": filename,
                    }
                ),
            }
        )
        try:
            task_kwargs = {"output_root": output_root} if output_root else {}
            target, _, skipped = _run_task_once(
                linked_task,
                settings,
                f"{run_id}-{index:02d}",
                **task_kwargs,
            )
            results.append(
                LinkedDocumentResult(
                    name=document.name,
                    status="success",
                    message="目标文件已存在，已跳过" if skipped else "下载完成",
                    output_file=str(target),
                    is_background=document.is_background,
                )
            )
        except TimeoutError:
            results.append(
                LinkedDocumentResult(
                    name=document.name,
                    status="failed",
                    message="下载超时",
                    is_background=document.is_background,
                )
            )
        except Exception as exc:
            LOGGER.exception(
                "automation=%s linked_document=%s failed",
                task.name,
                index,
            )
            results.append(
                LinkedDocumentResult(
                    name=document.name,
                    status="failed",
                    message=(
                        str(exc) if isinstance(exc, AutomationFailed) else "下载失败"
                    ),
                    is_background=document.is_background,
                )
            )
        if results[-1].status == "success":
            current_filenames[document.url] = filename
        if results[-1].status == "failed" and not extension.download.continue_on_error:
            break
    _write_linked_sources_index(
        output_directory,
        settings.automations.artifacts_dir,
        current_filenames,
        output_root=output_root,
    )
    _prune_linked_markdown_files(
        output_directory,
        settings.automations.artifacts_dir,
        set(current_filenames.values()),
        output_root=output_root,
    )
    return results


def _weekly_report_input_root(started: datetime) -> Path:
    period = reporting_period(started.date())
    workspace = WEEKLY_REPORTS_ROOT / period
    inputs = workspace / "inputs"
    workspace.mkdir(parents=True, exist_ok=True)
    workspace.chmod(0o700)
    inputs.mkdir(exist_ok=True)
    inputs.chmod(0o700)
    return inputs


def _weekly_report_download_task(task: AutomationTaskConfig) -> AutomationTaskConfig:
    filename = linked_filename(task.name, 1, set())
    return task.model_copy(
        update={
            "output": task.output.model_copy(
                update={
                    "directory": Path("."),
                    "filename": filename,
                    "conflict": "replace",
                }
            )
        }
    )


def _weekly_report_staging_root(input_root: Path, run_id: str) -> Path:
    staging = input_root.parent / f".inputs-{run_id}.staging"
    if staging.exists():
        raise AutomationFailed("本次周报暂存目录已存在")
    staging.mkdir(parents=True)
    staging.chmod(0o700)
    return staging


def _validate_weekly_downloads(
    main_document: Path,
    linked_documents: list[LinkedDocumentResult],
    *,
    period: str,
    linked_section: str,
) -> tuple[list[LinkedDocumentResult], list[str]]:
    errors = []
    try:
        validate_weekly_main_document(main_document, linked_section)
    except WeeklyValidationError as exc:
        errors.append(str(exc))

    validated_linked = []
    for document in linked_documents:
        if document.status != "success" or not document.output_file:
            validated_linked.append(document)
            continue
        if document.is_background:
            validated_linked.append(
                document.model_copy(update={"message": "下载完成（上周参考）"})
            )
            continue
        try:
            validate_weekly_linked_document(Path(document.output_file), period)
        except WeeklyValidationError as exc:
            waiting_for_update = str(exc).startswith("未检测到本期")
            message = (
                f"等待各端更新：{exc}"
                if waiting_for_update
                else f"本期校验失败：{exc}"
            )
            errors.append(f"关联文档“{document.name}”{message}")
            validated_linked.append(
                document.model_copy(
                    update={
                        "status": "waiting" if waiting_for_update else "failed",
                        "message": message,
                        "output_file": None,
                    }
                )
            )
        else:
            validated_linked.append(
                document.model_copy(update={"message": "下载并通过本期校验"})
            )
    return validated_linked, errors


def _weekly_inputs_are_waiting_for_updates(errors: list[str]) -> bool:
    waiting_prefixes = (
        "关联文档处理失败：本期各端周报不足：",
        "关联文档处理失败：本期各端周报缺少必需业务端：",
        "关联文档处理失败：上周参考不足：",
    )
    return bool(errors) and all(
        error.startswith(waiting_prefixes)
        or "等待各端更新：未检测到本期" in error
        for error in errors
    )


def _publish_weekly_inputs(staging_root: Path, input_root: Path, run_id: str) -> None:
    workspace = input_root.parent
    inputs_backup = workspace / f".inputs-{run_id}.previous"
    marker = workspace / ".inputs-updated"
    marker_backup = workspace / f".inputs-updated-{run_id}.previous"
    marker_temporary = workspace / f".inputs-updated-{run_id}.tmp"
    if inputs_backup.exists() or marker_backup.exists() or marker_temporary.exists():
        raise AutomationFailed("本期周报备份目录已存在")
    try:
        marker_temporary.write_text(
            datetime.now().astimezone().isoformat(), encoding="utf-8"
        )
        marker_temporary.chmod(0o600)
        if marker.exists():
            os.replace(marker, marker_backup)
        os.replace(marker_temporary, marker)
        if input_root.exists():
            os.replace(input_root, inputs_backup)
        os.replace(staging_root, input_root)
    except OSError as exc:
        marker_temporary.unlink(missing_ok=True)
        if inputs_backup.exists() and not input_root.exists():
            os.replace(inputs_backup, input_root)
        if marker.exists():
            marker.unlink(missing_ok=True)
        if marker_backup.exists():
            os.replace(marker_backup, marker)
        raise AutomationFailed("本期周报输入发布失败") from exc
    shutil.rmtree(inputs_backup, ignore_errors=True)
    marker_backup.unlink(missing_ok=True)


def run_automation(
    settings: Settings,
    task_id: str,
    *,
    trigger: str = "cli",
    run_id: str | None = None,
) -> AutomationState:
    config = load_automations(*settings.automations.config_files)
    task = config.tasks.get(task_id)
    if task is None:
        raise AutomationFailed("自动化任务不存在")
    if not task.enabled:
        raise AutomationFailed("自动化任务未启用")

    resolved_run_id = run_id or uuid4().hex
    if _RUN_ID_PATTERN.fullmatch(resolved_run_id) is None:
        raise AutomationFailed("运行标识包含非法字符")
    store = AutomationStateStore(settings.automations.state_dir)
    queued = store.read(task_id)
    operation_id = queued.operation_id if queued.run_id == resolved_run_id else None
    source_ip = queued.source_ip if queued.run_id == resolved_run_id else None
    task_lock = settings.automations.runtime_dir / "locks" / f"task-{task_id}.lock"
    browser_lock = settings.automations.runtime_dir / "locks" / "debug-chrome.lock"

    try:
        with file_lock(task_lock, 0):
            try:
                with file_lock(
                    browser_lock,
                    task.execution.lock_timeout_ms / 1000,
                ):
                    started = datetime.now().astimezone()
                    weekly_period = (
                        reporting_period(started.date())
                        if task.extension == "v-weekly-report-linked-documents"
                        else None
                    )
                    running = AutomationState(
                        task_id=task_id,
                        status="running",
                        run_id=resolved_run_id,
                        trigger=trigger,
                        process_id=os.getpid(),
                        operation_id=operation_id,
                        source_ip=source_ip,
                        message="正在执行",
                        started_at=started,
                        period=weekly_period,
                        validation_status=(
                            "pending" if weekly_period is not None else "not_applicable"
                        ),
                    )
                    store.write(running)
                    staging_root = None
                    try:
                        input_root = None
                        execution_task = task
                        if task.extension == "v-weekly-report-linked-documents":
                            input_root = _weekly_report_input_root(started)
                            staging_root = _weekly_report_staging_root(
                                input_root, resolved_run_id
                            )
                            execution_task = _weekly_report_download_task(task)
                        task_kwargs = {"output_root": staging_root} if staging_root else {}
                        target, size, skipped = _run_task_once(
                            execution_task,
                            settings,
                            resolved_run_id,
                            **task_kwargs,
                        )
                        extension_error = None
                        try:
                            linked_kwargs = (
                                {
                                    "output_root": staging_root,
                                    "linked_directory": Path("linked"),
                                }
                                if staging_root
                                else {}
                            )
                            linked_documents = _run_linked_documents(
                                execution_task,
                                target,
                                settings,
                                resolved_run_id,
                                **linked_kwargs,
                            )
                        except AutomationFailed as exc:
                            linked_documents = []
                            extension_error = str(exc)
                        validation_errors = []
                        validation_status = "not_applicable"
                        published_target = target
                        if weekly_period is not None:
                            validation_status = "failed"
                            if extension_error:
                                validation_errors.append(
                                    f"关联文档处理失败：{extension_error}"
                                )
                            else:
                                linked_documents, validation_errors = _validate_weekly_downloads(
                                    target,
                                    linked_documents,
                                    period=weekly_period,
                                    linked_section=load_linked_documents_extension(
                                        task.extension
                                    ).source.section,
                                )
                                validation_errors.extend(
                                    f"关联文档“{item.name}”下载失败：{item.message}"
                                    for item in linked_documents
                                    if item.status == "failed"
                                    and not item.message.startswith("本期校验失败：")
                                )
                            if not validation_errors:
                                _publish_weekly_inputs(
                                    staging_root, input_root, resolved_run_id
                                )
                                staging_root = None
                                published_target = input_root / target.name
                                linked_documents = [
                                    item.model_copy(
                                        update={
                                            "output_file": str(
                                                input_root
                                                / Path(item.output_file).relative_to(
                                                    Path(target).parent
                                                )
                                            )
                                        }
                                    )
                                    if item.output_file
                                    else item
                                    for item in linked_documents
                                ]
                                validation_status = "passed"
                        current_documents = [
                            item for item in linked_documents if not item.is_background
                        ]
                        background_documents = [
                            item for item in linked_documents if item.is_background
                        ]
                        linked_successes = sum(
                            item.status == "success" for item in current_documents
                        )
                        linked_failures = sum(
                            item.status == "failed" for item in linked_documents
                        )
                        waiting_for_updates = _weekly_inputs_are_waiting_for_updates(
                            validation_errors
                        )
                        if waiting_for_updates:
                            validation_status = "waiting"
                        if extension_error:
                            message = (
                                "主周报成功 · 关联文档处理失败："
                                f"{extension_error}"
                            )
                        elif validation_errors:
                            prefix = (
                                "等待各端更新："
                                if waiting_for_updates
                                else "本期校验失败："
                            )
                            message = f"{prefix}{validation_errors[0]}"
                        elif linked_documents:
                            message = (
                                f"下载并通过本期校验 · 各端周报 "
                                f"{linked_successes}/{len(current_documents)} 通过"
                            )
                            if background_documents:
                                message += (
                                    " · 上周参考 "
                                    f"{sum(item.status == 'success' for item in background_documents)}/"
                                    f"{len(background_documents)} 已下载"
                                )
                        else:
                            message = "下载并通过本期校验"
                        result = AutomationState(
                            task_id=task_id,
                            status=(
                                "waiting"
                                if waiting_for_updates
                                else (
                                    "failed"
                                    if extension_error
                                    or linked_failures
                                    or validation_errors
                                    else "success"
                                )
                            ),
                            run_id=resolved_run_id,
                            trigger=trigger,
                            process_id=os.getpid(),
                            operation_id=operation_id,
                            source_ip=source_ip,
                            message=message,
                            started_at=started,
                            finished_at=datetime.now().astimezone(),
                            output_file=(
                                str(published_target)
                                if validation_status in {"not_applicable", "passed"}
                                else None
                            ),
                            output_bytes=size,
                            period=weekly_period,
                            main_document_name=(
                                target.stem if weekly_period is not None else None
                            ),
                            linked_documents=linked_documents,
                            validation_status=validation_status,
                            validation_message=(
                                validation_errors[0] if validation_errors else None
                            ),
                        )
                        if staging_root is not None:
                            shutil.rmtree(staging_root, ignore_errors=True)
                    except TimeoutError:
                        if staging_root is not None:
                            shutil.rmtree(staging_root, ignore_errors=True)
                        result = AutomationState(
                            task_id=task_id,
                            status="failed",
                            run_id=resolved_run_id,
                            trigger=trigger,
                            process_id=os.getpid(),
                            operation_id=operation_id,
                            source_ip=source_ip,
                            message="任务执行超时",
                            started_at=started,
                            finished_at=datetime.now().astimezone(),
                            period=weekly_period,
                            validation_status=(
                                "failed"
                                if weekly_period is not None
                                else "not_applicable"
                            ),
                        )
                    except Exception as exc:
                        if staging_root is not None:
                            shutil.rmtree(staging_root, ignore_errors=True)
                        LOGGER.exception("automation=%s run_id=%s failed", task_id, resolved_run_id)
                        message = str(exc) if isinstance(exc, AutomationFailed) else "任务执行失败"
                        result = AutomationState(
                            task_id=task_id,
                            status="failed",
                            run_id=resolved_run_id,
                            trigger=trigger,
                            process_id=os.getpid(),
                            operation_id=operation_id,
                            source_ip=source_ip,
                            message=message,
                            started_at=started,
                            finished_at=datetime.now().astimezone(),
                            period=weekly_period,
                            validation_status=(
                                "failed"
                                if weekly_period is not None
                                else "not_applicable"
                            ),
                        )
                    result = log_final_operation(result)
                    store.write(result)
                    LOGGER.info(
                        "automation=%s run_id=%s status=%s",
                        task_id,
                        resolved_run_id,
                        result.status,
                    )
                    return result
            except LockBusy as exc:
                raise AutomationFailed("其他自动化任务正在使用 Debug Chrome") from exc
    except LockBusy as exc:
        raise AutomationFailed("该自动化任务正在执行") from exc
