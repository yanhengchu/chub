from __future__ import annotations

import asyncio
import logging
import os
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
from app.core.config import Settings


LOGGER = logging.getLogger("hub.automations")
SIGNATURES = {"pdf": b"%PDF-", "zip": b"PK"}


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
    data_dir: Path,
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
    output_root = (data_dir / "downloads").resolve()
    directory = (output_root / task.output.directory).resolve()
    if not directory.is_relative_to(output_root):
        raise AutomationFailed("输出目录超出自动化数据目录")
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

            target = _output_path(task, settings.automations.data_dir)
            output_root = (settings.automations.data_dir / "downloads").resolve()
            _ensure_private_directory(target.parent, output_root)
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
) -> tuple[Path, int, bool]:
    return asyncio.run(
        asyncio.wait_for(
            _run_browser_task(task, settings, run_id),
            timeout=task.execution.timeout_ms / 1000,
        )
    )


def _clear_linked_markdown_files(directory: Path, data_dir: Path) -> None:
    output_root = (data_dir / "downloads").resolve()
    resolved_directory = (output_root / directory).resolve()
    if not resolved_directory.is_relative_to(output_root):
        raise AutomationFailed("关联文档输出目录超出自动化数据目录")
    _ensure_private_directory(resolved_directory, output_root)
    for entry in resolved_directory.iterdir():
        if entry.suffix.lower() == ".md" and (entry.is_file() or entry.is_symlink()):
            entry.unlink()


def _run_linked_documents(
    task: AutomationTaskConfig,
    source: Path,
    settings: Settings,
    run_id: str,
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

    try:
        timezone = ZoneInfo(task.output.timezone)
    except ZoneInfoNotFoundError as exc:
        raise AutomationFailed("输出时区无效") from exc
    date_directory = datetime.now(timezone).strftime("%Y-%m-%d")
    output_directory = task.output.directory / "linked" / date_directory
    _clear_linked_markdown_files(output_directory, settings.automations.data_dir)
    used_filenames: set[str] = set()
    results = []
    for index, document in enumerate(documents, start=1):
        filename = linked_filename(document.name, index, used_filenames)
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
            target, _, skipped = _run_task_once(
                linked_task,
                settings,
                f"{run_id}-{index:02d}",
            )
            results.append(
                LinkedDocumentResult(
                    name=document.name,
                    status="success",
                    message="目标文件已存在，已跳过" if skipped else "下载完成",
                    output_file=str(target),
                )
            )
        except TimeoutError:
            results.append(
                LinkedDocumentResult(
                    name=document.name,
                    status="failed",
                    message="下载超时",
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
                )
            )
        if results[-1].status == "failed" and not extension.download.continue_on_error:
            break
    return results


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
    store = AutomationStateStore(settings.automations.data_dir)
    queued = store.read(task_id)
    operation_id = queued.operation_id if queued.run_id == resolved_run_id else None
    source_ip = queued.source_ip if queued.run_id == resolved_run_id else None
    task_lock = settings.automations.data_dir / "locks" / f"task-{task_id}.lock"
    browser_lock = settings.automations.data_dir / "locks" / "debug-chrome.lock"

    try:
        with file_lock(task_lock, 0):
            try:
                with file_lock(
                    browser_lock,
                    task.execution.lock_timeout_ms / 1000,
                ):
                    started = datetime.now().astimezone()
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
                    )
                    store.write(running)
                    try:
                        target, size, skipped = _run_task_once(
                            task,
                            settings,
                            resolved_run_id,
                        )
                        extension_error = None
                        try:
                            linked_documents = _run_linked_documents(
                                task,
                                target,
                                settings,
                                resolved_run_id,
                            )
                        except AutomationFailed as exc:
                            linked_documents = []
                            extension_error = str(exc)
                        linked_successes = sum(
                            item.status == "success" for item in linked_documents
                        )
                        linked_failures = len(linked_documents) - linked_successes
                        if extension_error:
                            message = (
                                "主周报成功 · 关联文档处理失败："
                                f"{extension_error}"
                            )
                        elif linked_documents:
                            message = (
                                f"下载完成 · 主周报成功 · 关联文档 "
                                f"{linked_successes}/{len(linked_documents)} 成功"
                            )
                        else:
                            message = "目标文件已存在，已跳过" if skipped else "下载完成"
                        result = AutomationState(
                            task_id=task_id,
                            status=(
                                "failed"
                                if extension_error or linked_failures
                                else "success"
                            ),
                            run_id=resolved_run_id,
                            trigger=trigger,
                            process_id=os.getpid(),
                            operation_id=operation_id,
                            source_ip=source_ip,
                            message=message,
                            started_at=started,
                            finished_at=datetime.now().astimezone(),
                            output_file=str(target),
                            output_bytes=size,
                            linked_documents=linked_documents,
                        )
                    except TimeoutError:
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
                        )
                    except Exception as exc:
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
