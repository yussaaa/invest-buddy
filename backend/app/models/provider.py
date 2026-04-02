"""ModelProvider abstraction — all LLM calls go through this interface.

Swapping the underlying model is one config change (MODEL_PROVIDER env var).
Concrete implementations:
  - OpenAIProvider   → api.openai.com  (default)
  - AnthropicProvider → api.anthropic.com
  - QwenProvider     → DashScope API
  - OllamaProvider   → local Ollama server (OpenAI-compatible)
  - VLLMProvider     → local/remote vLLM server (OpenAI-compatible)

Both Ollama and vLLM reuse the OpenAI client with a custom base_url — they
expose an OpenAI-compatible /v1/chat/completions endpoint.
"""

from __future__ import annotations

import time
from abc import ABC, abstractmethod
from typing import Any, AsyncIterator, Optional

import structlog
from openai import AsyncOpenAI
from openai.types.chat import ChatCompletion

log = structlog.get_logger(__name__)


class LLMResponse:
    """Normalised response from any provider."""

    def __init__(
        self,
        content: str,
        model: str,
        prompt_tokens: int = 0,
        completion_tokens: int = 0,
        latency_ms: int = 0,
    ):
        self.content = content
        self.model = model
        self.prompt_tokens = prompt_tokens
        self.completion_tokens = completion_tokens
        self.total_tokens = prompt_tokens + completion_tokens
        self.latency_ms = latency_ms


class ModelProvider(ABC):
    """Abstract base — every concrete provider implements these two methods."""

    @abstractmethod
    async def complete(
        self,
        messages: list[dict[str, str]],
        model: str,
        response_format: Optional[dict] = None,
        temperature: float = 0.1,
        max_tokens: int = 4096,
    ) -> LLMResponse:
        """Single-turn completion (structured output supported)."""
        ...

    @abstractmethod
    async def stream(
        self,
        messages: list[dict[str, str]],
        model: str,
        temperature: float = 0.1,
    ) -> AsyncIterator[str]:
        """Streaming completion — yields text chunks."""
        ...


# ── OpenAI-compatible base (handles OpenAI, Ollama, vLLM) ──────────────────


class _OpenAICompatibleProvider(ModelProvider):
    """Shared implementation for any OpenAI-compatible endpoint."""

    def __init__(self, api_key: str = "none", base_url: Optional[str] = None):
        self._client = AsyncOpenAI(
            api_key=api_key,
            base_url=base_url,
        )

    async def complete(
        self,
        messages: list[dict[str, str]],
        model: str,
        response_format: Optional[dict] = None,
        temperature: float = 0.1,
        max_tokens: int = 4096,
    ) -> LLMResponse:
        t0 = time.monotonic()
        kwargs: dict[str, Any] = dict(
            model=model,
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
        )
        if response_format:
            kwargs["response_format"] = response_format

        resp: ChatCompletion = await self._client.chat.completions.create(**kwargs)
        latency_ms = int((time.monotonic() - t0) * 1000)

        usage = resp.usage
        return LLMResponse(
            content=resp.choices[0].message.content or "",
            model=resp.model,
            prompt_tokens=usage.prompt_tokens if usage else 0,
            completion_tokens=usage.completion_tokens if usage else 0,
            latency_ms=latency_ms,
        )

    async def stream(
        self,
        messages: list[dict[str, str]],
        model: str,
        temperature: float = 0.1,
    ) -> AsyncIterator[str]:
        stream = await self._client.chat.completions.create(
            model=model,
            messages=messages,
            temperature=temperature,
            stream=True,
        )
        async for chunk in stream:
            delta = chunk.choices[0].delta.content
            if delta:
                yield delta


class OpenAIProvider(_OpenAICompatibleProvider):
    """Calls api.openai.com — requires OPENAI_API_KEY."""

    def __init__(self, api_key: str):
        super().__init__(api_key=api_key)


class OllamaProvider(_OpenAICompatibleProvider):
    """Calls a local Ollama server — no API key needed."""

    def __init__(self, base_url: str = "http://localhost:11434"):
        # Ollama's OpenAI-compat endpoint is at /v1
        super().__init__(api_key="ollama", base_url=f"{base_url.rstrip('/')}/v1")


class VLLMProvider(_OpenAICompatibleProvider):
    """Calls a local or remote vLLM server — no API key needed by default."""

    def __init__(self, base_url: str = "http://localhost:8000"):
        super().__init__(api_key="vllm", base_url=f"{base_url.rstrip('/')}/v1")


# ── Anthropic provider ──────────────────────────────────────────────────────


class AnthropicProvider(ModelProvider):
    """Calls api.anthropic.com — requires ANTHROPIC_API_KEY."""

    def __init__(self, api_key: str):
        import anthropic
        self._client = anthropic.AsyncAnthropic(api_key=api_key)

    async def complete(
        self,
        messages: list[dict[str, str]],
        model: str,
        response_format: Optional[dict] = None,
        temperature: float = 0.1,
        max_tokens: int = 4096,
    ) -> LLMResponse:
        t0 = time.monotonic()

        # Anthropic separates system from user messages
        system = next((m["content"] for m in messages if m["role"] == "system"), "")
        user_messages = [m for m in messages if m["role"] != "system"]

        # If structured output requested, inject JSON instruction
        if response_format and response_format.get("type") == "json_object":
            system += "\n\nYou must respond with valid JSON only."

        resp = await self._client.messages.create(
            model=model,
            system=system,
            messages=user_messages,
            temperature=temperature,
            max_tokens=max_tokens,
        )
        latency_ms = int((time.monotonic() - t0) * 1000)
        return LLMResponse(
            content=resp.content[0].text,
            model=resp.model,
            prompt_tokens=resp.usage.input_tokens,
            completion_tokens=resp.usage.output_tokens,
            latency_ms=latency_ms,
        )

    async def stream(
        self,
        messages: list[dict[str, str]],
        model: str,
        temperature: float = 0.1,
    ) -> AsyncIterator[str]:
        system = next((m["content"] for m in messages if m["role"] == "system"), "")
        user_messages = [m for m in messages if m["role"] != "system"]

        async with self._client.messages.stream(
            model=model,
            system=system,
            messages=user_messages,
            temperature=temperature,
            max_tokens=4096,
        ) as s:
            async for text in s.text_stream:
                yield text


# ── Qwen via DashScope ──────────────────────────────────────────────────────


class QwenProvider(_OpenAICompatibleProvider):
    """Calls Alibaba DashScope — uses OpenAI-compatible endpoint."""

    DASHSCOPE_BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"

    def __init__(self, api_key: str):
        super().__init__(api_key=api_key, base_url=self.DASHSCOPE_BASE_URL)
