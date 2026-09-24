from __future__ import annotations

import json
import os
from pathlib import Path
from threading import RLock
from typing import Literal

from app.core.response import ApiError


PromptOptimizerMode = Literal["direct", "auto"]
_SETTINGS_VERSION = 1
_DEFAULT_MODE: PromptOptimizerMode = "direct"
_AUTO_UNAVAILABLE_REASON = "auto 模式依赖的任务阶段尚未接入，后续交付完成后开放。"


class PromptOptimizerSettingsStore:
    """Persist only the prompt optimizer's local mode preference."""

    def __init__(self, state_dir: Path) -> None:
        self.path = state_dir / "orchestration" / "chub-task-prompt-optimizer" / "settings.json"
        self._lock = RLock()

    def read(self) -> dict[str, object]:
        with self._lock:
            try:
                with self.path.open("rb") as stream:
                    raw = stream.read(4097)
                if len(raw) > 4096:
                    raise ValueError("settings file too large")
                payload = json.loads(raw.decode("utf-8"))
            except FileNotFoundError:
                return {"mode": _DEFAULT_MODE, "is_default": True}
            except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError):
                raise ApiError(
                    503,
                    "prompt_optimizer_settings_unavailable",
                    "提示词优化插件设置无法读取。",
                ) from None
            if (
                not isinstance(payload, dict)
                or type(payload.get("version")) is not int
                or payload.get("version") != _SETTINGS_VERSION
                or not isinstance(payload.get("mode"), str)
                or payload.get("mode") not in {"direct", "auto"}
            ):
                raise ApiError(
                    503,
                    "prompt_optimizer_settings_invalid",
                    "提示词优化插件设置格式无效。",
                )
            return {"mode": payload["mode"], "is_default": False}

    def save(self, mode: PromptOptimizerMode) -> dict[str, object]:
        if mode == "auto":
            raise ApiError(
                409,
                "prompt_optimizer_auto_unavailable",
                _AUTO_UNAVAILABLE_REASON,
            )
        payload = json.dumps(
            {"version": _SETTINGS_VERSION, "mode": mode},
            ensure_ascii=True,
            separators=(",", ":"),
        )
        with self._lock:
            temporary = self.path.with_suffix(".tmp")
            try:
                self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
                temporary.write_text(payload, encoding="utf-8")
                os.chmod(temporary, 0o600)
                os.replace(temporary, self.path)
            except OSError:
                raise ApiError(
                    503,
                    "prompt_optimizer_settings_unavailable",
                    "提示词优化插件设置无法保存。",
                ) from None
            finally:
                if temporary.exists():
                    temporary.unlink(missing_ok=True)
        return self.read()


def auto_mode_unavailable_reason() -> str:
    return _AUTO_UNAVAILABLE_REASON
