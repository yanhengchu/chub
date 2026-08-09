from __future__ import annotations

import json
import os
import selectors
import shutil
import subprocess
import threading
import time
import tomllib
from pathlib import Path

from app.codex.models import (
    CodexModelCatalogData,
    CodexModelInfo,
    CodexReasoningLevel,
)
from app.core.response import ApiError


MODEL_CATALOG_TIMEOUT_SECONDS = 10
MODEL_CATALOG_MAX_BYTES = 2 * 1024 * 1024
MODEL_CATALOG_CACHE_SECONDS = 300
MODEL_CONFIG_MAX_BYTES = 1024 * 1024


class _ModelCatalogOutputTooLarge(Exception):
    pass


class CodexModelCatalog:
    def __init__(self, codex_home: Path | None = None) -> None:
        self.codex_home = codex_home or Path.home() / ".codex"
        self._lock = threading.RLock()
        self._models: list[CodexModelInfo] = []
        self._expires_at = 0.0

    def read(self) -> list[CodexModelInfo]:
        with self._lock:
            if self._models and time.monotonic() < self._expires_at:
                return [model.model_copy(deep=True) for model in self._models]
            try:
                models = self._load()
            except ApiError:
                if self._models:
                    return [model.model_copy(deep=True) for model in self._models]
                raise
            self._models = models
            self._expires_at = time.monotonic() + MODEL_CATALOG_CACHE_SECONDS
            return [model.model_copy(deep=True) for model in models]

    def validate(self, model: str | None, reasoning_effort: str | None) -> None:
        if model is None:
            if reasoning_effort is not None:
                raise ApiError(
                    400,
                    "codex_reasoning_effort_requires_model",
                    "A reasoning level requires an explicit Codex model",
                )
            return
        selected = next((item for item in self.read() if item.id == model), None)
        if selected is None:
            raise ApiError(
                400,
                "codex_model_unavailable",
                "Selected Codex model is unavailable",
            )
        if (
            reasoning_effort is not None
            and reasoning_effort not in {level.id for level in selected.levels}
        ):
            raise ApiError(
                400,
                "codex_reasoning_effort_unsupported",
                "Selected reasoning level is unsupported by this Codex model",
            )

    def data(self) -> CodexModelCatalogData:
        models = self.read()
        default_model, default_reasoning_effort = self._read_defaults(models)
        return CodexModelCatalogData(
            models=models,
            default_model=default_model,
            default_reasoning_effort=default_reasoning_effort,
        )

    def _read_defaults(
        self,
        models: list[CodexModelInfo],
    ) -> tuple[str | None, str | None]:
        default_model = models[0].id if models else None
        default_reasoning_effort = models[0].default_level if models else None
        for path in (
            Path("/etc/codex/config.toml"),
            self.codex_home / "config.toml",
            self.codex_home / "chub.config.toml",
        ):
            layer = self._read_config_layer(path)
            model = layer.get("model")
            reasoning_effort = layer.get("model_reasoning_effort")
            if isinstance(model, str) and 0 < len(model) <= 128:
                default_model = model
            if (
                isinstance(reasoning_effort, str)
                and 0 < len(reasoning_effort) <= 32
            ):
                default_reasoning_effort = reasoning_effort
        return default_model, default_reasoning_effort

    @staticmethod
    def _read_config_layer(path: Path) -> dict[str, object]:
        try:
            with path.open("rb") as file:
                data = file.read(MODEL_CONFIG_MAX_BYTES + 1)
        except OSError:
            return {}
        if len(data) > MODEL_CONFIG_MAX_BYTES:
            return {}
        try:
            payload = tomllib.loads(data.decode("utf-8"))
        except (UnicodeDecodeError, tomllib.TOMLDecodeError):
            return {}
        return payload if isinstance(payload, dict) else {}

    @staticmethod
    def _load() -> list[CodexModelInfo]:
        if shutil.which("codex") is None:
            raise ApiError(
                503,
                "codex_model_catalog_unavailable",
                "Codex model catalog is unavailable",
            )
        try:
            result = CodexModelCatalog._run_bounded(
                ["codex", "debug", "models"]
            )
        except (OSError, subprocess.TimeoutExpired, _ModelCatalogOutputTooLarge):
            raise ApiError(
                503,
                "codex_model_catalog_unavailable",
                "Codex model catalog is unavailable",
            ) from None
        if (
            result.returncode != 0
            or len(result.stdout) > MODEL_CATALOG_MAX_BYTES
            or len(result.stderr) > MODEL_CATALOG_MAX_BYTES
        ):
            raise ApiError(
                503,
                "codex_model_catalog_unavailable",
                "Codex model catalog is unavailable",
            )
        try:
            payload = json.loads(result.stdout)
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise ApiError(
                503,
                "codex_model_catalog_unavailable",
                "Codex model catalog is unavailable",
            ) from None
        raw_models = payload.get("models") if isinstance(payload, dict) else None
        if not isinstance(raw_models, list):
            raise ApiError(
                503,
                "codex_model_catalog_unavailable",
                "Codex model catalog is unavailable",
            )
        models: list[CodexModelInfo] = []
        for raw_model in raw_models:
            model = CodexModelCatalog._parse_model(raw_model)
            if model is not None:
                models.append(model)
        if not models:
            raise ApiError(
                503,
                "codex_model_catalog_unavailable",
                "Codex model catalog is unavailable",
            )
        return models

    @staticmethod
    def _run_bounded(command: list[str]) -> subprocess.CompletedProcess[bytes]:
        process = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        selector = selectors.DefaultSelector()
        outputs: dict[str, bytearray] = {
            "stdout": bytearray(),
            "stderr": bytearray(),
        }
        deadline = time.monotonic() + MODEL_CATALOG_TIMEOUT_SECONDS
        try:
            assert process.stdout is not None
            assert process.stderr is not None
            selector.register(process.stdout, selectors.EVENT_READ, "stdout")
            selector.register(process.stderr, selectors.EVENT_READ, "stderr")
            while selector.get_map():
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise subprocess.TimeoutExpired(
                        command,
                        MODEL_CATALOG_TIMEOUT_SECONDS,
                    )
                events = selector.select(remaining)
                if not events:
                    raise subprocess.TimeoutExpired(
                        command,
                        MODEL_CATALOG_TIMEOUT_SECONDS,
                    )
                for key, _mask in events:
                    chunk = os.read(key.fd, 64 * 1024)
                    if not chunk:
                        selector.unregister(key.fileobj)
                        continue
                    output = outputs[key.data]
                    if len(output) + len(chunk) > MODEL_CATALOG_MAX_BYTES:
                        raise _ModelCatalogOutputTooLarge
                    output.extend(chunk)
            returncode = process.wait(max(0.01, deadline - time.monotonic()))
        except Exception:
            process.kill()
            process.wait()
            raise
        finally:
            selector.close()
            if process.stdout is not None:
                process.stdout.close()
            if process.stderr is not None:
                process.stderr.close()
        return subprocess.CompletedProcess(
            command,
            returncode,
            bytes(outputs["stdout"]),
            bytes(outputs["stderr"]),
        )

    @staticmethod
    def _parse_model(raw_model: object) -> CodexModelInfo | None:
        if not isinstance(raw_model, dict) or raw_model.get("visibility") != "list":
            return None
        model_id = raw_model.get("slug")
        name = raw_model.get("display_name")
        if not isinstance(model_id, str) or not model_id or len(model_id) > 128:
            return None
        if not isinstance(name, str) or not name or len(name) > 128:
            return None
        levels: list[CodexReasoningLevel] = []
        raw_levels = raw_model.get("supported_reasoning_levels")
        if isinstance(raw_levels, list):
            for raw_level in raw_levels:
                if not isinstance(raw_level, dict):
                    continue
                effort = raw_level.get("effort")
                description = raw_level.get("description")
                if (
                    isinstance(effort, str)
                    and effort
                    and len(effort) <= 32
                ):
                    levels.append(
                        CodexReasoningLevel(
                            id=effort,
                            description=(
                                description
                                if isinstance(description, str)
                                and len(description) <= 300
                                else ""
                            ),
                        )
                    )
        default_level = raw_model.get("default_reasoning_level")
        if default_level not in {level.id for level in levels}:
            default_level = None
        description = raw_model.get("description")
        return CodexModelInfo(
            id=model_id,
            name=name,
            description=(
                description
                if isinstance(description, str) and len(description) <= 500
                else ""
            ),
            default_level=default_level,
            levels=levels,
        )
