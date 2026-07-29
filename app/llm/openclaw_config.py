from __future__ import annotations

import json
import os
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from pydantic import SecretStr

from app.core.config import LlmConfig
from app.llm.models import LlmConfigurationError, ResolvedLlmConfig


MAX_CONFIG_BYTES = 1024 * 1024
MAX_SECRET_BYTES = 16 * 1024
SUPPORTED_API = "openai-completions"


@dataclass(frozen=True)
class _FileSignature:
    modified_ns: int
    size: int


@dataclass(frozen=True)
class _CachedConfig:
    config_signature: _FileSignature
    secret_path: Path
    secret_signature: _FileSignature
    value: ResolvedLlmConfig


class OpenClawLlmConfigLoader:
    def __init__(self, settings: LlmConfig) -> None:
        self.settings = settings
        self._cached: _CachedConfig | None = None

    def load(self) -> ResolvedLlmConfig:
        if not self.settings.enabled:
            raise LlmConfigurationError("基础 LLM 功能未启用。")
        if self.settings.config_source != "openclaw":
            raise LlmConfigurationError("基础 LLM 配置来源不受支持。")

        config_path = self.settings.openclaw_config_file.expanduser().resolve()
        config_signature = self._signature(
            config_path,
            MAX_CONFIG_BYTES,
            "OpenClaw 配置",
            require_private_permissions=True,
        )
        cached = self._cached
        if (
            cached is not None
            and cached.config_signature == config_signature
            and self._signature(
                cached.secret_path,
                MAX_SECRET_BYTES,
                "OpenClaw Secret",
                require_private_permissions=True,
            )
            == cached.secret_signature
        ):
            return cached.value

        config = self._read_json(
            config_path,
            MAX_CONFIG_BYTES,
            "OpenClaw 配置",
            require_private_permissions=True,
        )
        models = self._mapping(config.get("models"), "OpenClaw 模型配置无效。")
        providers = self._mapping(
            models.get("providers"),
            "OpenClaw 未配置模型供应商。",
        )
        provider_id = self._select_provider(providers, config)
        provider = self._mapping(
            providers.get(provider_id),
            "OpenClaw 模型供应商配置无效。",
        )
        model_id = self._select_model(provider_id, provider, config)
        api = self._model_api(provider, model_id)
        if api != SUPPORTED_API:
            raise LlmConfigurationError(
                f"基础 LLM 暂不支持 OpenClaw 模型协议：{api or '未配置'}。"
            )

        base_url = self._validated_base_url(provider.get("baseUrl"))
        api_key, secret_path = self._resolve_api_key(config, provider, config_path)
        secret_signature = self._signature(
            secret_path,
            MAX_SECRET_BYTES,
            "OpenClaw Secret",
            require_private_permissions=True,
        )
        resolved = ResolvedLlmConfig(
            provider=provider_id,
            model=model_id,
            api=api,
            base_url=base_url,
            api_key=SecretStr(api_key),
            timeout_seconds=self.settings.timeout_seconds,
            max_tokens=self.settings.max_tokens,
        )
        self._cached = _CachedConfig(
            config_signature=config_signature,
            secret_path=secret_path,
            secret_signature=secret_signature,
            value=resolved,
        )
        return resolved

    def _select_provider(
        self,
        providers: dict[str, Any],
        config: dict[str, Any],
    ) -> str:
        if self.settings.provider:
            if self.settings.provider not in providers:
                raise LlmConfigurationError(
                    "指定的基础 LLM Provider 不存在于 OpenClaw 配置中。"
                )
            return self.settings.provider

        default_provider, _ = self._default_model(config)
        if default_provider and default_provider in providers:
            return default_provider
        if len(providers) == 1:
            return next(iter(providers))
        raise LlmConfigurationError(
            "OpenClaw 配置了多个模型供应商，请在 Chub 中明确选择 Provider。"
        )

    def _select_model(
        self,
        provider_id: str,
        provider: dict[str, Any],
        config: dict[str, Any],
    ) -> str:
        models = provider.get("models")
        if not isinstance(models, list):
            raise LlmConfigurationError("OpenClaw 模型列表无效。")
        model_ids = [
            item.get("id")
            for item in models
            if isinstance(item, dict)
            and isinstance(item.get("id"), str)
            and item.get("id").strip()
        ]
        if self.settings.model:
            if self.settings.model not in model_ids:
                raise LlmConfigurationError(
                    "指定的基础 LLM 模型不存在于 OpenClaw 配置中。"
                )
            return self.settings.model

        default_provider, default_model = self._default_model(config)
        if default_provider == provider_id and default_model in model_ids:
            return default_model
        if len(model_ids) == 1:
            return model_ids[0]
        raise LlmConfigurationError(
            "OpenClaw Provider 配置了多个模型，请在 Chub 中明确选择模型。"
        )

    @staticmethod
    def _default_model(config: dict[str, Any]) -> tuple[str | None, str | None]:
        agents = config.get("agents")
        if not isinstance(agents, dict):
            return None, None
        defaults = agents.get("defaults")
        if not isinstance(defaults, dict):
            return None, None
        model = defaults.get("model")
        if isinstance(model, dict):
            model = model.get("primary")
        if not isinstance(model, str) or "/" not in model:
            return None, None
        provider, model_id = model.split("/", 1)
        return provider or None, model_id or None

    @staticmethod
    def _model_api(provider: dict[str, Any], model_id: str) -> str:
        models = provider.get("models")
        if isinstance(models, list):
            for item in models:
                if isinstance(item, dict) and item.get("id") == model_id:
                    model_api = item.get("api")
                    if isinstance(model_api, str) and model_api:
                        return model_api
                    break
        provider_api = provider.get("api")
        return provider_api if isinstance(provider_api, str) else ""

    def _resolve_api_key(
        self,
        config: dict[str, Any],
        provider: dict[str, Any],
        config_path: Path,
    ) -> tuple[str, Path]:
        reference = self._mapping(
            provider.get("apiKey"),
            "OpenClaw 模型凭证必须使用 SecretRef。",
        )
        if reference.get("source") != "file" or reference.get("id") != "value":
            raise LlmConfigurationError(
                "基础 LLM 目前只支持 OpenClaw 文件型 singleValue SecretRef。"
            )
        secret_provider_id = reference.get("provider")
        if not isinstance(secret_provider_id, str) or not secret_provider_id:
            raise LlmConfigurationError("OpenClaw 模型凭证引用无效。")

        secrets = self._mapping(
            config.get("secrets"),
            "OpenClaw Secret Provider 配置无效。",
        )
        secret_providers = self._mapping(
            secrets.get("providers"),
            "OpenClaw Secret Provider 配置无效。",
        )
        secret_provider = self._mapping(
            secret_providers.get(secret_provider_id),
            "OpenClaw 模型凭证引用的 Secret Provider 不存在。",
        )
        if (
            secret_provider.get("source") != "file"
            or secret_provider.get("mode") != "singleValue"
        ):
            raise LlmConfigurationError(
                "基础 LLM 目前只支持 OpenClaw 文件型 singleValue SecretRef。"
            )
        configured_path = secret_provider.get("path")
        if not isinstance(configured_path, str) or not configured_path.strip():
            raise LlmConfigurationError("OpenClaw Secret 文件路径无效。")
        secret_path = Path(configured_path).expanduser()
        if not secret_path.is_absolute():
            secret_path = config_path.parent / secret_path
        secret_path = secret_path.resolve()
        secret = self._read_text(
            secret_path,
            MAX_SECRET_BYTES,
            "OpenClaw Secret",
            require_private_permissions=True,
        ).strip()
        if not secret:
            raise LlmConfigurationError("OpenClaw 模型凭证为空。")
        return secret, secret_path

    @staticmethod
    def _validated_base_url(value: object) -> str:
        if not isinstance(value, str) or not value.strip():
            raise LlmConfigurationError("OpenClaw 模型 Base URL 未配置。")
        base_url = value.strip().rstrip("/")
        parsed = urlparse(base_url)
        is_loopback_http = (
            parsed.scheme == "http"
            and parsed.hostname in {"127.0.0.1", "::1", "localhost"}
        )
        if (
            (parsed.scheme != "https" and not is_loopback_http)
            or not parsed.netloc
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
        ):
            raise LlmConfigurationError(
                "OpenClaw 模型 Base URL 必须使用 HTTPS 或本机 HTTP 地址。"
            )
        return base_url

    @staticmethod
    def _mapping(value: object, message: str) -> dict[str, Any]:
        if not isinstance(value, dict):
            raise LlmConfigurationError(message)
        return value

    @classmethod
    def _read_json(
        cls,
        path: Path,
        max_bytes: int,
        label: str,
        *,
        require_private_permissions: bool = False,
    ) -> dict[str, Any]:
        content = cls._read_text(
            path,
            max_bytes,
            label,
            require_private_permissions=require_private_permissions,
        )
        try:
            value = json.loads(content)
        except json.JSONDecodeError as exc:
            raise LlmConfigurationError(f"{label}不是有效 JSON。") from exc
        if not isinstance(value, dict):
            raise LlmConfigurationError(f"{label}根节点必须是对象。")
        return value

    @classmethod
    def _read_text(
        cls,
        path: Path,
        max_bytes: int,
        label: str,
        *,
        require_private_permissions: bool = False,
    ) -> str:
        cls._signature(
            path,
            max_bytes,
            label,
            require_private_permissions=require_private_permissions,
        )
        try:
            content = path.read_text(encoding="utf-8")
        except FileNotFoundError as exc:
            raise LlmConfigurationError(f"{label}不存在。") from exc
        except OSError as exc:
            raise LlmConfigurationError(f"{label}无法读取。") from exc
        if len(content.encode("utf-8")) > max_bytes:
            raise LlmConfigurationError(f"{label}超过允许大小。")
        return content

    @staticmethod
    def _signature(
        path: Path,
        max_bytes: int,
        label: str,
        *,
        require_private_permissions: bool = False,
    ) -> _FileSignature:
        try:
            file_stat = path.stat()
        except FileNotFoundError as exc:
            raise LlmConfigurationError(f"{label}不存在。") from exc
        except OSError as exc:
            raise LlmConfigurationError(f"{label}无法读取。") from exc
        if not stat.S_ISREG(file_stat.st_mode):
            raise LlmConfigurationError(f"{label}不是普通文件。")
        if file_stat.st_size > max_bytes:
            raise LlmConfigurationError(f"{label}超过允许大小。")
        if require_private_permissions:
            if stat.S_IMODE(file_stat.st_mode) & 0o077:
                raise LlmConfigurationError(f"{label}权限必须限制为当前用户。")
            if hasattr(os, "geteuid") and file_stat.st_uid != os.geteuid():
                raise LlmConfigurationError(f"{label}必须由当前用户持有。")
        return _FileSignature(
            modified_ns=file_stat.st_mtime_ns,
            size=file_stat.st_size,
        )
