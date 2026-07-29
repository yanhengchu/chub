from __future__ import annotations

import asyncio
import json

import httpx

from app.llm.models import LlmRequestError, ResolvedLlmConfig


class OpenAiCompletionsTransport:
    def __init__(
        self,
        *,
        max_response_bytes: int,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.max_response_bytes = max_response_bytes
        self.transport = transport
        self._client: httpx.AsyncClient | None = None
        self._client_lock = asyncio.Lock()

    async def complete(
        self,
        config: ResolvedLlmConfig,
        messages: list[dict[str, str]],
        max_tokens: int,
    ) -> str:
        client = await self._get_client()
        try:
            async with client.stream(
                "POST",
                f"{config.base_url}/chat/completions",
                headers={
                    "Authorization": (
                        f"Bearer {config.api_key.get_secret_value()}"
                    ),
                    "Content-Type": "application/json",
                },
                json={
                    "model": config.model,
                    "messages": messages,
                    "max_tokens": max_tokens,
                    "stream": False,
                },
                timeout=config.timeout_seconds,
                follow_redirects=False,
            ) as response:
                if response.status_code < 200 or response.status_code >= 300:
                    raise self._status_error(response.status_code)
                content = bytearray()
                async for chunk in response.aiter_bytes():
                    content.extend(chunk)
                    if len(content) > self.max_response_bytes:
                        raise LlmRequestError(
                            "基础 LLM 响应超过允许大小。",
                            code="response_too_large",
                            retryable=False,
                        )
        except httpx.TimeoutException as exc:
            raise LlmRequestError(
                "基础 LLM 请求超时。",
                code="timeout",
                retryable=True,
            ) from exc
        except httpx.HTTPError as exc:
            raise LlmRequestError(
                "基础 LLM 网络请求失败。",
                code="network_error",
                retryable=True,
            ) from exc

        try:
            payload = json.loads(content)
            result = payload["choices"][0]["message"]["content"]
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            raise LlmRequestError(
                "基础 LLM 返回了无法识别的响应。",
                code="invalid_response",
                retryable=False,
            ) from exc
        if not isinstance(result, str) or not result.strip():
            raise LlmRequestError(
                "基础 LLM 未返回文本内容。",
                code="empty_response",
                retryable=False,
            )
        return result.strip()

    async def close(self) -> None:
        async with self._client_lock:
            client = self._client
            self._client = None
        if client is not None:
            await client.aclose()

    async def _get_client(self) -> httpx.AsyncClient:
        client = self._client
        if client is not None and not client.is_closed:
            return client
        async with self._client_lock:
            client = self._client
            if client is None or client.is_closed:
                client = httpx.AsyncClient(transport=self.transport)
                self._client = client
            return client

    @staticmethod
    def _status_error(status_code: int) -> LlmRequestError:
        if status_code in {401, 403}:
            return LlmRequestError(
                "基础 LLM 凭证无效或无权访问模型。",
                code="authentication_failed",
                retryable=False,
                status_code=status_code,
            )
        if status_code == 429:
            return LlmRequestError(
                "基础 LLM 请求受到限流。",
                code="rate_limited",
                retryable=True,
                status_code=status_code,
            )
        if status_code >= 500:
            return LlmRequestError(
                "基础 LLM 供应商暂时不可用。",
                code="provider_unavailable",
                retryable=True,
                status_code=status_code,
            )
        return LlmRequestError(
            f"基础 LLM 返回 HTTP {status_code}。",
            code="request_failed",
            retryable=False,
            status_code=status_code,
        )
