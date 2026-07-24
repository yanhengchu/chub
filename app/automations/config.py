from __future__ import annotations

import logging
from pathlib import Path
from urllib.parse import urlparse

import yaml
from pydantic import ValidationError

from app.automations.models import (
    AutomationTaskConfig,
    AutomationTemplate,
    AutomationsFile,
    FeishuDocumentFile,
    LinkedDocumentsTemplate,
)


TEMPLATE_PATH = (
    Path(__file__).resolve().parents[2]
    / "config"
    / "automation_templates"
    / "feishu-document-download.yaml"
)
EXTENSION_PATHS = {
    "v-weekly-report-linked-documents": (
        TEMPLATE_PATH.parent / "v-weekly-report-linked-documents.yaml"
    ),
}
LOGGER = logging.getLogger("hub.automations.config")


class DuplicateAutomationTaskError(RuntimeError):
    def __init__(self, task_id: str) -> None:
        self.task_id = task_id
        super().__init__(f"Conflicting automation task id '{task_id}'")


def _read_yaml(path: Path, *, label: str) -> dict:
    try:
        content = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, yaml.YAMLError) as exc:
        raise RuntimeError(f"Unable to read {label}: {path}") from exc
    if content is None:
        content = {}
    if not isinstance(content, dict):
        raise RuntimeError(f"{label.capitalize()} root must be a mapping")
    return content


def _expand_feishu_tasks(config: FeishuDocumentFile) -> AutomationsFile:
    try:
        template = AutomationTemplate.model_validate(
            _read_yaml(TEMPLATE_PATH, label="automation template")
        )
    except ValidationError as exc:
        raise RuntimeError(f"Invalid automation template: {exc}") from exc

    tasks = {}
    template_data = template.task.model_dump(mode="python")
    for task_id, configured in config.tasks.items():
        task_data = template_data.copy()
        task_data["name"] = configured.name
        task_data["enabled"] = configured.enabled
        task_data["extension"] = configured.extension
        task_data["browser"] = template_data["browser"].copy()
        task_data["browser"]["start_url"] = configured.url
        task_data["browser"]["allowed_hosts"] = [urlparse(configured.url).hostname]
        task_data["output"] = template_data["output"].copy()
        task_data["output"]["directory"] = task_id
        task_data["output"]["filename"] = f"{task_id}-{{date:%Y-%m-%d}}.md"
        tasks[task_id] = AutomationTaskConfig.model_validate(task_data)
    return AutomationsFile(version=1, tasks=tasks)


def _load_automation_file(path: Path) -> AutomationsFile:
    if not path.is_file():
        return AutomationsFile()
    content = _read_yaml(path, label="automations configuration")
    try:
        if content.get("version") == 2:
            return _expand_feishu_tasks(FeishuDocumentFile.model_validate(content))
        return AutomationsFile.model_validate(content)
    except ValidationError as exc:
        raise RuntimeError(f"Invalid automations configuration: {exc}") from exc


def load_automations(*paths: Path) -> AutomationsFile:
    tasks = {}
    for path in paths:
        loaded = _load_automation_file(path)
        for task_id, task in loaded.tasks.items():
            if task_id in tasks:
                if task == tasks[task_id]:
                    LOGGER.warning(
                        "Ignoring identical duplicate automation task: id=%s",
                        task_id,
                    )
                    continue
                raise DuplicateAutomationTaskError(task_id)
            tasks[task_id] = task
    return AutomationsFile(version=1, tasks=tasks)


def load_linked_documents_extension(name: str) -> LinkedDocumentsTemplate:
    path = EXTENSION_PATHS.get(name)
    if path is None:
        raise RuntimeError("Unknown automation extension")
    try:
        return LinkedDocumentsTemplate.model_validate(
            _read_yaml(path, label="automation extension")
        )
    except ValidationError as exc:
        raise RuntimeError(f"Invalid automation extension: {exc}") from exc
