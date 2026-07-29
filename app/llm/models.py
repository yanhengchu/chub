from __future__ import annotations

from dataclasses import dataclass

from pydantic import SecretStr


class LlmConfigurationError(RuntimeError):
    pass


class LlmRequestError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        code: str,
        retryable: bool,
        status_code: int | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.retryable = retryable
        self.status_code = status_code


@dataclass(frozen=True)
class ResolvedLlmConfig:
    provider: str
    model: str
    api: str
    base_url: str
    api_key: SecretStr
    timeout_seconds: float
    max_tokens: int


@dataclass(frozen=True)
class LlmCompletion:
    content: str
    provider: str
    model: str
