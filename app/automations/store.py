from __future__ import annotations

import json
import os
from pathlib import Path
from uuid import uuid4

from app.automations.models import AutomationState


class AutomationStateStore:
    def __init__(self, data_dir: Path) -> None:
        self._state_dir = data_dir / "states"

    def path_for(self, task_id: str) -> Path:
        return self._state_dir / f"{task_id}.json"

    def read(self, task_id: str) -> AutomationState:
        path = self.path_for(task_id)
        try:
            content = json.loads(path.read_text(encoding="utf-8"))
            return AutomationState.model_validate(content)
        except FileNotFoundError:
            return AutomationState(task_id=task_id)
        except (OSError, UnicodeError, json.JSONDecodeError, ValueError):
            return AutomationState(
                task_id=task_id,
                status="failed",
                message="任务状态文件无法读取",
            )

    def write(self, state: AutomationState) -> None:
        self._state_dir.mkdir(parents=True, exist_ok=True)
        path = self.path_for(state.task_id)
        temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
        try:
            temporary.write_text(
                json.dumps(
                    state.model_dump(mode="json"),
                    ensure_ascii=False,
                    separators=(",", ":"),
                ),
                encoding="utf-8",
            )
            temporary.chmod(0o600)
            os.replace(temporary, path)
            path.chmod(0o600)
        finally:
            temporary.unlink(missing_ok=True)
