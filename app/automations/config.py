from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import ValidationError

from app.automations.models import AutomationsFile


def load_automations(path: Path) -> AutomationsFile:
    if not path.is_file():
        return AutomationsFile()
    try:
        content = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, yaml.YAMLError) as exc:
        raise RuntimeError(f"Unable to read automations configuration: {path}") from exc
    if content is None:
        content = {}
    if not isinstance(content, dict):
        raise RuntimeError("Automations configuration root must be a mapping")
    try:
        return AutomationsFile.model_validate(content)
    except ValidationError as exc:
        raise RuntimeError(f"Invalid automations configuration: {exc}") from exc
