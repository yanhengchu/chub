from __future__ import annotations

import asyncio

import httpx

from app.core.config import LlmConfig
from app.llm.models import LlmCompletion
from app.llm.openai_completions import OpenAiCompletionsTransport
from app.llm.openclaw_config import OpenClawLlmConfigLoader


MAX_PROMPT_CHARS = 100_000
MAX_SYSTEM_PROMPT_CHARS = 20_000


class LlmService:
    def __init__(
        self,
        settings: LlmConfig,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.loader = OpenClawLlmConfigLoader(settings)
        self.protocol = OpenAiCompletionsTransport(
            max_response_bytes=settings.max_response_bytes,
            transport=transport,
        )
        self._semaphore = asyncio.Semaphore(settings.max_concurrency)

    async def complete(
        self,
        prompt: str,
        *,
        system_prompt: str | None = None,
        max_tokens: int | None = None,
    ) -> LlmCompletion:
        prompt = prompt.strip()
        if not prompt:
            raise ValueError("LLM prompt 不能为空。")
        if len(prompt) > MAX_PROMPT_CHARS:
            raise ValueError("LLM prompt 超过允许长度。")
        config = self.loader.load()
        requested_max_tokens = config.max_tokens if max_tokens is None else max_tokens
        if requested_max_tokens < 1 or requested_max_tokens > config.max_tokens:
            raise ValueError("max_tokens 超出基础 LLM 配置范围。")

        messages: list[dict[str, str]] = []
        if system_prompt and system_prompt.strip():
            normalized_system_prompt = system_prompt.strip()
            if len(normalized_system_prompt) > MAX_SYSTEM_PROMPT_CHARS:
                raise ValueError("LLM system prompt 超过允许长度。")
            messages.append(
                {"role": "system", "content": normalized_system_prompt}
            )
        messages.append({"role": "user", "content": prompt})

        async with self._semaphore:
            content = await self.protocol.complete(
                config,
                messages,
                requested_max_tokens,
            )
        return LlmCompletion(
            content=content,
            provider=config.provider,
            model=config.model,
        )

    async def close(self) -> None:
        await self.protocol.close()
