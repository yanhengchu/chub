from __future__ import annotations

import asyncio
import hashlib
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
_WEEKLY_CURRENT_DOCUMENT_ROLES = {
    "vivo音乐产品周报": "music-product",
    "vivo产品周报": "product",
    "vivo运营周报": "operations",
    "移动端周会": "client",
    "服务端开发部周报": "server",
}
_WEEKLY_REQUIRED_ROLES = (
    "main-report",
    "previous-report",
    "music-product",
    "product",
    "operations",
    "client",
    "server",
)
_WEEKLY_HEADING_RANGE_STARTS = {
    "client": "五、VIVO国内",
    "server": "一、南京服务端 @薛峰",
}
_WEEKLY_REPORT_VALIDATION = {
    "required_sections": [
        "业务关键指标",
        "【产品体验提升】",
        "【产品营收提升】",
        "【项目质量提升】",
        "【AI 工程化推进】",
        "各端周报",
    ],
    "required_section_text": {
        "【产品体验提升】": ["目标：", "当前进展："],
        "【产品营收提升】": ["目标：", "当前进展："],
        "【项目质量提升】": ["目标：", "当前进展："],
        "【AI 工程化推进】": ["目标："],
    },
    "checklist_required_sections": [
        "本周需要同步的事项",
        "需要维护者确认的重点事项",
    ],
}


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
                    source_url=document.url,
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
                    source_url=document.url,
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
                    source_url=document.url,
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


def _publish_weekly_inputs(
    staging_root: Path,
    input_root: Path,
    run_id: str,
    *,
    mapping_staging: Path | None = None,
    manifest_staging: Path | None = None,
) -> None:
    workspace = input_root.parent
    inputs_backup = workspace / f".inputs-{run_id}.previous"
    marker = workspace / ".inputs-updated"
    marker_backup = workspace / f".inputs-updated-{run_id}.previous"
    marker_temporary = workspace / f".inputs-updated-{run_id}.tmp"
    mapping = workspace / "mapping.json"
    manifest = workspace / "manifest.json"
    mapping_backup = workspace / f".mapping-{run_id}.previous"
    manifest_backup = workspace / f".manifest-{run_id}.previous"
    metadata_paths = (mapping_staging, manifest_staging)
    if (
        inputs_backup.exists()
        or marker_backup.exists()
        or marker_temporary.exists()
        or mapping_backup.exists()
        or manifest_backup.exists()
        or any(path is not None and not path.is_file() for path in metadata_paths)
    ):
        raise AutomationFailed("本期周报备份目录已存在")
    marker_backed_up = False
    marker_replaced = False
    try:
        marker_temporary.write_text(
            datetime.now().astimezone().isoformat(), encoding="utf-8"
        )
        marker_temporary.chmod(0o600)
        if input_root.exists():
            os.replace(input_root, inputs_backup)
        if mapping_staging is not None and mapping.exists():
            os.replace(mapping, mapping_backup)
        if manifest_staging is not None and manifest.exists():
            os.replace(manifest, manifest_backup)
        os.replace(staging_root, input_root)
        if mapping_staging is not None:
            os.replace(mapping_staging, mapping)
        if manifest_staging is not None:
            os.replace(manifest_staging, manifest)
        if marker.exists():
            os.replace(marker, marker_backup)
            marker_backed_up = True
        os.replace(marker_temporary, marker)
        marker_replaced = True
    except OSError as exc:
        marker_temporary.unlink(missing_ok=True)
        if input_root.exists() and inputs_backup.exists():
            shutil.rmtree(input_root, ignore_errors=True)
        if inputs_backup.exists() and not input_root.exists():
            os.replace(inputs_backup, input_root)
        if mapping_staging is not None:
            mapping.unlink(missing_ok=True)
            if mapping_backup.exists():
                os.replace(mapping_backup, mapping)
        if manifest_staging is not None:
            manifest.unlink(missing_ok=True)
            if manifest_backup.exists():
                os.replace(manifest_backup, manifest)
        if marker_replaced or marker_backed_up:
            marker.unlink(missing_ok=True)
            if marker_backup.exists():
                os.replace(marker_backup, marker)
        raise AutomationFailed("本期周报输入发布失败") from exc
    shutil.rmtree(inputs_backup, ignore_errors=True)
    marker_backup.unlink(missing_ok=True)
    mapping_backup.unlink(missing_ok=True)
    manifest_backup.unlink(missing_ok=True)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _weekly_role(document: LinkedDocumentResult) -> str:
    if document.is_background:
        return "previous-report"
    normalized = document.name.replace("\\-", "-").strip().casefold()
    for prefix, role in _WEEKLY_CURRENT_DOCUMENT_ROLES.items():
        if normalized.startswith(prefix.casefold()):
            return role
    raise AutomationFailed(f"关联文档“{document.name}”无法映射为周报来源角色")


def _markdown_h1_headings(path: Path) -> list[tuple[int, str]]:
    try:
        lines = path.read_text(encoding="utf-8-sig").splitlines()
    except (OSError, UnicodeError) as exc:
        raise AutomationFailed("周报来源无法读取，不能生成 Manifest") from exc
    headings = []
    for line_number, line in enumerate(lines, start=1):
        match = re.match(r"^#\s+(.+?)\s*#*\s*$", line.strip())
        if match:
            headings.append((line_number, match.group(1)))
    return headings


def _heading_range_usage(path: Path, role: str) -> tuple[dict[str, str], dict[str, object]]:
    configured_start = _WEEKLY_HEADING_RANGE_STARTS[role]
    normalized_start = re.sub(r"[*_`\\\s]", "", configured_start).casefold()
    headings = _markdown_h1_headings(path)
    matches = [
        (index, line, text)
        for index, (line, text) in enumerate(headings)
        if re.sub(r"[*_`\\\s]", "", text).casefold() == normalized_start
    ]
    if len(matches) != 1 or matches[0][0] + 1 >= len(headings):
        raise AutomationFailed(f"{role} 来源章节边界无法唯一确定")
    index, start_line, start_text = matches[0]
    end_line, end_text = headings[index + 1]
    usage = {
        "mode": "heading-range",
        "start_heading": start_text,
        "end_heading": end_text,
    }
    resolved = {
        "mode": "heading-range",
        "start": {
            "configured": start_text,
            "normalized": re.sub(r"[*_`\\\s]", "", start_text).casefold(),
            "matched_original": start_text,
            "line": start_line,
        },
        "end": {
            "configured": end_text,
            "normalized": re.sub(r"[*_`\\\s]", "", end_text).casefold(),
            "matched_original": end_text,
            "line": end_line,
        },
    }
    return usage, resolved


def _write_weekly_input_metadata(
    staging_root: Path,
    input_root: Path,
    main_document: Path,
    main_document_url: str,
    linked_documents: list[LinkedDocumentResult],
    period: str,
    run_id: str,
) -> tuple[Path, Path]:
    workspace = input_root.parent
    try:
        start, end = period.split("至", maxsplit=1)
    except ValueError as exc:
        raise AutomationFailed("本期周报周期格式无效") from exc
    documents: list[dict[str, object]] = []

    def add_document(
        role: str,
        path: Path,
        title: str,
        *,
        source_url: str | None = None,
        reference_only: bool = False,
    ) -> None:
        try:
            relative_path = path.relative_to(staging_root).as_posix()
            stat = path.stat()
        except (ValueError, OSError) as exc:
            raise AutomationFailed("周报输入文件无法写入 Manifest") from exc
        entry: dict[str, object] = {
            "role": role,
            "path": relative_path,
            "title": title,
            "download_status": "succeeded",
            "content_status": "ready",
            "file_size": stat.st_size,
            "modified_at": datetime.fromtimestamp(stat.st_mtime).astimezone().isoformat(),
            "sha256": _sha256(path),
        }
        if source_url:
            entry["source_url"] = source_url
        if reference_only:
            entry["usage"] = {"mode": "reference-only"}
        else:
            entry["usage_period"] = {"start": start, "end": end}
            if role in _WEEKLY_HEADING_RANGE_STARTS:
                usage, resolved = _heading_range_usage(path, role)
                entry["usage"] = usage
                entry["resolved_usage"] = resolved
            else:
                entry["usage"] = {"mode": "whole-document"}
        documents.append(entry)

    add_document(
        "main-report",
        main_document,
        main_document.stem,
        source_url=main_document_url,
    )
    roles = {"main-report"}
    for document in linked_documents:
        if document.status != "success" or not document.output_file:
            raise AutomationFailed("周报关联资料未完整下载，不能生成 Manifest")
        role = _weekly_role(document)
        if role in roles:
            raise AutomationFailed(f"周报来源角色重复：{role}")
        roles.add(role)
        add_document(
            role,
            Path(document.output_file),
            document.name,
            source_url=document.source_url,
            reference_only=document.is_background,
        )
    missing = [role for role in _WEEKLY_REQUIRED_ROLES if role not in roles]
    if missing:
        raise AutomationFailed("周报来源角色不完整：" + "、".join(missing))
    mapping = {
        "version": 1,
        "report_period": {"start": start, "end": end, "timezone": "Asia/Shanghai"},
        "report_validation": _WEEKLY_REPORT_VALIDATION,
        "required_roles": list(_WEEKLY_REQUIRED_ROLES),
        "documents": [
            {
                key: value
                for key, value in document.items()
                if key not in {"file_size", "modified_at", "sha256", "resolved_usage"}
            }
            for document in documents
        ],
    }
    manifest: dict[str, object] = {
        "version": 1,
        "report_period": mapping["report_period"],
        "data_root": "..",
        "source_root": "inputs",
        "required_roles": mapping["required_roles"],
        "report_validation": mapping["report_validation"],
        "documents": documents,
        "generated_at": datetime.now().astimezone().isoformat(),
    }
    manifest["fingerprint"] = hashlib.sha256(
        json.dumps(manifest, ensure_ascii=False, sort_keys=True).encode()
    ).hexdigest()
    mapping_staging = workspace / f".mapping-{run_id}.staging"
    manifest_staging = workspace / f".manifest-{run_id}.staging"
    try:
        mapping_staging.write_text(
            json.dumps(mapping, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        manifest_staging.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        mapping_staging.chmod(0o600)
        manifest_staging.chmod(0o600)
    except OSError as exc:
        mapping_staging.unlink(missing_ok=True)
        manifest_staging.unlink(missing_ok=True)
        raise AutomationFailed("周报 Mapping 或 Manifest 生成失败") from exc
    return mapping_staging, manifest_staging


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

    try:
        with file_lock(task_lock, 0):
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
                message=(
                    "正在下载本期主周报"
                    if weekly_period is not None
                    else "正在下载资料"
                ),
                started_at=started,
                period=weekly_period,
                validation_status=(
                    "pending" if weekly_period is not None else "not_applicable"
                ),
            )
            store.write(running)
            staging_root = None
            mapping_staging = None
            manifest_staging = None
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
                if weekly_period is not None:
                    running = running.model_copy(
                        update={"message": "主周报已下载，正在下载关联资料"}
                    )
                    store.write(running)
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
                    running = running.model_copy(
                        update={"message": "关联资料已下载，正在校验本期资料"}
                    )
                    store.write(running)
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
                        running = running.model_copy(
                            update={"message": "本期资料校验通过，正在生成输入清单"}
                        )
                        store.write(running)
                        mapping_staging, manifest_staging = _write_weekly_input_metadata(
                            staging_root,
                            input_root,
                            target,
                            task.browser.start_url,
                            linked_documents,
                            weekly_period,
                            resolved_run_id,
                        )
                        running = running.model_copy(
                            update={"message": "输入清单已生成，正在发布"}
                        )
                        store.write(running)
                        _publish_weekly_inputs(
                            staging_root,
                            input_root,
                            resolved_run_id,
                            mapping_staging=mapping_staging,
                            manifest_staging=manifest_staging,
                        )
                        staging_root = None
                        mapping_staging = None
                        manifest_staging = None
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
                if mapping_staging is not None:
                    mapping_staging.unlink(missing_ok=True)
                if manifest_staging is not None:
                    manifest_staging.unlink(missing_ok=True)
            except TimeoutError:
                if staging_root is not None:
                    shutil.rmtree(staging_root, ignore_errors=True)
                if mapping_staging is not None:
                    mapping_staging.unlink(missing_ok=True)
                if manifest_staging is not None:
                    manifest_staging.unlink(missing_ok=True)
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
                if mapping_staging is not None:
                    mapping_staging.unlink(missing_ok=True)
                if manifest_staging is not None:
                    manifest_staging.unlink(missing_ok=True)
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
        raise AutomationFailed("该自动化任务正在执行") from exc
