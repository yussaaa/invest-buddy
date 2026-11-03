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

Tool calling speaks OpenAI's wire format throughout: four of the five providers
are OpenAI-compatible, and `ToolRegistry.get_schema_for_llm` already emits
exactly that shape. AnthropicProvider translates in both directions through the
pure functions at the bottom of this module, which is also why they are module
level — they are the part worth unit-testing, and doing so needs no API key.
"""

from __future__ import annotations

import json
import re
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Callable, Optional

import structlog
from openai import AsyncOpenAI
from openai.types.chat import ChatCompletion

log = structlog.get_logger(__name__)


@dataclass
class ToolCall:
    """One tool invocation a model asked for, normalised across providers.

    `id` is echoed back on the result message so the model can match a result to
    the call that produced it.
    """

    id: str
    name: str
    arguments: dict[str, Any] = field(default_factory=dict)


class LLMResponse:
    """Normalised response from any provider."""

    def __init__(
        self,
        content: str,
        model: str,
        prompt_tokens: int = 0,
        completion_tokens: int = 0,
        latency_ms: int = 0,
        tool_calls: Optional[list[ToolCall]] = None,
        stop_reason: Optional[str] = None,
    ):
        self.content = content
        self.model = model
        self.prompt_tokens = prompt_tokens
        self.completion_tokens = completion_tokens
        self.total_tokens = prompt_tokens + completion_tokens
        self.latency_ms = latency_ms
        self.tool_calls = tool_calls or []
        self.stop_reason = stop_reason

        # Emit Prometheus token + cost metrics (fire-and-forget)
        try:
            from app.observability.metrics import TOKEN_USAGE_TOTAL, record_token_cost
            if prompt_tokens > 0:
                TOKEN_USAGE_TOTAL.labels(model=model, type="prompt").inc(prompt_tokens)
            if completion_tokens > 0:
                TOKEN_USAGE_TOTAL.labels(model=model, type="completion").inc(completion_tokens)
            record_token_cost(model, prompt_tokens, completion_tokens)
        except Exception:
            pass  # Never break LLM calls for metrics


class ModelProvider(ABC):
    """Abstract base — every concrete provider implements these two methods."""

    @property
    def supports_tools(self) -> bool:
        """Whether `complete(tools=...)` will be honoured.

        Callers must branch on this rather than assume. A provider that says no
        still answers; it just answers without calling anything.
        """
        return True

    @abstractmethod
    async def complete(
        self,
        messages: list[dict[str, Any]],
        model: str,
        response_format: Optional[dict] = None,
        temperature: float = 0.1,
        max_tokens: int = 4096,
        tools: Optional[list[dict]] = None,
        tool_choice: Optional[str] = None,
    ) -> LLMResponse:
        """Single-turn completion (structured output and tool calling supported).

        `tools` is a list of OpenAI function schemas. `tool_choice` is one of
        "auto", "required" or "none". When the model asks for tools, the reply
        arrives in `LLMResponse.tool_calls` and `content` is usually empty.
        """
        ...

    @abstractmethod
    async def stream(
        self,
        messages: list[dict[str, Any]],
        model: str,
        temperature: float = 0.1,
        max_tokens: int = 4096,
        on_usage: Optional[Callable[[int, int], None]] = None,
    ) -> AsyncIterator[str]:
        """Streaming completion — yields text chunks.

        Tool calls are deliberately not streamed. Accumulating partial JSON
        across two different delta formats costs far more than it returns; the
        chat agent runs its tool loop on `complete` and streams only the final
        narration.

        `on_usage` is invoked once at the end with (prompt_tokens,
        completion_tokens). Without it a streamed call is invisible to the
        token and cost metrics that `LLMResponse` records.
        """
        ...


def _record_stream_usage(
    model: str, prompt_tokens: int, completion_tokens: int
) -> None:
    """Put streamed token counts through the same metrics path as `complete`.

    Constructing an LLMResponse purely for its side effect is deliberate: the
    Prometheus emission lives in its constructor, and duplicating it here would
    be a second place to keep in step.
    """
    if prompt_tokens or completion_tokens:
        LLMResponse(
            content="",
            model=model,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
        )


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
        messages: list[dict[str, Any]],
        model: str,
        response_format: Optional[dict] = None,
        temperature: float = 0.1,
        max_tokens: int = 4096,
        tools: Optional[list[dict]] = None,
        tool_choice: Optional[str] = None,
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
        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = tool_choice or "auto"

        resp: ChatCompletion = await self._client.chat.completions.create(**kwargs)
        latency_ms = int((time.monotonic() - t0) * 1000)

        choice = resp.choices[0]
        usage = resp.usage
        return LLMResponse(
            content=choice.message.content or "",
            model=resp.model,
            prompt_tokens=usage.prompt_tokens if usage else 0,
            completion_tokens=usage.completion_tokens if usage else 0,
            latency_ms=latency_ms,
            tool_calls=parse_openai_tool_calls(choice.message),
            stop_reason=choice.finish_reason,
        )

    async def stream(
        self,
        messages: list[dict[str, Any]],
        model: str,
        temperature: float = 0.1,
        max_tokens: int = 4096,
        on_usage: Optional[Callable[[int, int], None]] = None,
    ) -> AsyncIterator[str]:
        stream = await self._client.chat.completions.create(
            model=model,
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
            stream=True,
            stream_options={"include_usage": True},
        )
        prompt_tokens = completion_tokens = 0
        async for chunk in stream:
            if chunk.usage:
                # Arrives on a final chunk that carries no choices.
                prompt_tokens = chunk.usage.prompt_tokens
                completion_tokens = chunk.usage.completion_tokens
            if not chunk.choices:
                continue
            delta = chunk.choices[0].delta.content
            if delta:
                yield delta

        _record_stream_usage(model, prompt_tokens, completion_tokens)
        if on_usage:
            on_usage(prompt_tokens, completion_tokens)


class OpenAIProvider(_OpenAICompatibleProvider):
    """Calls api.openai.com — requires OPENAI_API_KEY."""

    def __init__(self, api_key: str):
        super().__init__(api_key=api_key)


class _LocalProvider(_OpenAICompatibleProvider):
    """Shared by Ollama and vLLM.

    Both expose the OpenAI tool-calling parameters, but honouring them depends
    on the served model, and the small local Qwens this project defaults to are
    unreliable at it — they emit malformed arguments or ignore the schema. The
    setting lets a local run turn the tool loop off and still get an answer
    rather than a failure.
    """

    @property
    def supports_tools(self) -> bool:
        from app.config import get_settings

        return get_settings().chat_tools_enabled


class OllamaProvider(_LocalProvider):
    """Calls a local Ollama server — no API key needed."""

    def __init__(self, base_url: str = "http://localhost:11434"):
        # Ollama's OpenAI-compat endpoint is at /v1
        super().__init__(api_key="ollama", base_url=f"{base_url.rstrip('/')}/v1")


class VLLMProvider(_LocalProvider):
    """Calls a local or remote vLLM server — no API key needed by default."""

    def __init__(self, base_url: str = "http://localhost:8000"):
        super().__init__(api_key="vllm", base_url=f"{base_url.rstrip('/')}/v1")


# ── Anthropic provider ──────────────────────────────────────────────────────


# Claude models up to the 4.6 generation accept sampling parameters and forced
# tool_choice. Newer ones do not: Opus 4.7+, the 5.x line and Fable return a 400
# for `temperature`, and the 5.5 generation also for tool_choice `any`. They also
# think on every call, and take `output_config.effort` as the depth control.
# The *old* models are matched by name, so a model released next month falls on
# the side that sends nothing it might reject.
_LEGACY_CLAUDE = re.compile(
    r"^claude-(?:3|(?:opus|sonnet|haiku)-4(?:-[0-6])?(?:-\d{8})?$)"
)


THINKING_HEADROOM_TOKENS = 8192


def is_legacy_claude(model: str) -> bool:
    """True for models that take temperature and forced tool_choice."""
    return bool(_LEGACY_CLAUDE.match(model))


class AnthropicProvider(ModelProvider):
    """Calls api.anthropic.com — requires ANTHROPIC_API_KEY."""

    def __init__(self, api_key: str, effort: str = ""):
        import anthropic
        self._client = anthropic.AsyncAnthropic(api_key=api_key)
        self._effort = effort

    def _request(self, model: str, system: str, messages: list, temperature: float,
                 max_tokens: int) -> dict[str, Any]:
        """The arguments every request shares, shaped for the model's generation."""
        kwargs: dict[str, Any] = dict(
            model=model, system=system, messages=messages, max_tokens=max_tokens,
        )
        if is_legacy_claude(model):
            kwargs["temperature"] = temperature
        else:
            # Thinking draws on the same budget as the reply. Every caller
            # sized its cap for the reply alone, so on these models the cap
            # was being spent on thinking: a 60-token probe came back empty,
            # and the synthesiser's 2048 cut its JSON off mid-string. The cap
            # is a ceiling, not a spend, so the headroom costs nothing unless
            # the model actually thinks that long.
            kwargs["max_tokens"] = max_tokens + THINKING_HEADROOM_TOKENS
            if self._effort:
                kwargs["output_config"] = {"effort": self._effort}
        return kwargs

    async def complete(
        self,
        messages: list[dict[str, Any]],
        model: str,
        response_format: Optional[dict] = None,
        temperature: float = 0.1,
        max_tokens: int = 4096,
        tools: Optional[list[dict]] = None,
        tool_choice: Optional[str] = None,
    ) -> LLMResponse:
        t0 = time.monotonic()

        # Anthropic separates system from user messages
        system = next((m["content"] for m in messages if m["role"] == "system"), "")
        user_messages = to_anthropic_messages(
            [m for m in messages if m["role"] != "system"]
        )

        # If structured output requested, inject JSON instruction
        if response_format and response_format.get("type") == "json_object":
            system += "\n\nYou must respond with valid JSON only."

        kwargs = self._request(model, system, user_messages, temperature, max_tokens)
        anthropic_tools = to_anthropic_tools(tools)
        if anthropic_tools:
            kwargs["tools"] = anthropic_tools
            choice = to_anthropic_tool_choice(tool_choice, model)
            if choice:
                kwargs["tool_choice"] = choice

        resp = await self._client.messages.create(**kwargs)
        latency_ms = int((time.monotonic() - t0) * 1000)

        text, calls = parse_anthropic_content(resp.content)
        if response_format and response_format.get("type") == "json_object":
            text = extract_json_text(text)
        return LLMResponse(
            content=text,
            model=resp.model,
            prompt_tokens=resp.usage.input_tokens,
            completion_tokens=resp.usage.output_tokens,
            latency_ms=latency_ms,
            tool_calls=calls,
            stop_reason=resp.stop_reason,
        )

    async def stream(
        self,
        messages: list[dict[str, Any]],
        model: str,
        temperature: float = 0.1,
        max_tokens: int = 4096,
        on_usage: Optional[Callable[[int, int], None]] = None,
    ) -> AsyncIterator[str]:
        system = next((m["content"] for m in messages if m["role"] == "system"), "")
        user_messages = to_anthropic_messages(
            [m for m in messages if m["role"] != "system"]
        )

        async with self._client.messages.stream(
            **self._request(model, system, user_messages, temperature, max_tokens)
        ) as s:
            async for text in s.text_stream:
                yield text

            prompt_tokens = completion_tokens = 0
            try:
                usage = (await s.get_final_message()).usage
                prompt_tokens, completion_tokens = usage.input_tokens, usage.output_tokens
            except Exception as exc:  # metrics must never break a reply
                log.warning("anthropic_stream_usage_unavailable", error=str(exc))

        _record_stream_usage(model, prompt_tokens, completion_tokens)
        if on_usage:
            on_usage(prompt_tokens, completion_tokens)


# ── Qwen via DashScope ──────────────────────────────────────────────────────


class QwenProvider(_OpenAICompatibleProvider):
    """Calls Alibaba DashScope — uses OpenAI-compatible endpoint."""

    DASHSCOPE_BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"

    def __init__(self, api_key: str):
        super().__init__(api_key=api_key, base_url=self.DASHSCOPE_BASE_URL)


# ── Wire-format translation ────────────────────────────────────────────────
#
# OpenAI's shape is canonical here. Everything below converts to and from
# Anthropic's. Pure functions on purpose: this is the part most likely to be
# subtly wrong, and it should be testable without a network or an API key.


def parse_openai_tool_calls(message: Any) -> list[ToolCall]:
    """Pull tool calls off an OpenAI assistant message.

    Arguments arrive as a JSON string. A model occasionally emits one that does
    not parse; that is reported as an empty argument dict rather than an
    exception, so the tool layer can reject it with a message the model can act
    on instead of the turn dying.
    """
    raw = getattr(message, "tool_calls", None) or []
    calls: list[ToolCall] = []
    for call in raw:
        try:
            arguments = json.loads(call.function.arguments or "{}")
        except (json.JSONDecodeError, TypeError):
            log.warning(
                "tool_call_arguments_unparseable",
                tool=call.function.name,
                raw=call.function.arguments,
            )
            arguments = {}
        if not isinstance(arguments, dict):
            arguments = {}
        calls.append(ToolCall(id=call.id, name=call.function.name, arguments=arguments))
    return calls


def to_anthropic_tools(tools: Optional[list[dict]]) -> list[dict]:
    """OpenAI function schemas → Anthropic tool definitions."""
    if not tools:
        return []
    out = []
    for tool in tools:
        fn = tool.get("function", tool)
        out.append({
            "name": fn["name"],
            "description": fn.get("description", ""),
            "input_schema": fn.get("parameters", {"type": "object", "properties": {}}),
        })
    return out


def to_anthropic_tool_choice(tool_choice: Optional[str], model: Optional[str] = None) -> Optional[dict]:
    """OpenAI's tool_choice string → Anthropic's object form.

    "none" returns None; the caller drops `tools` entirely for that case, which
    is how Anthropic expresses it. "required" becomes `auto` on models that
    reject forced tool use; the prompt has to ask for the call instead.
    """
    if tool_choice in (None, "auto"):
        return {"type": "auto"}
    if tool_choice == "required":
        if model and not is_legacy_claude(model):
            return {"type": "auto"}
        return {"type": "any"}
    if tool_choice == "none":
        return None
    return {"type": "auto"}


def to_anthropic_messages(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """OpenAI-shaped conversation → Anthropic content blocks.

    Two shapes need real work. An assistant turn that asked for tools carries
    them in a sibling `tool_calls` field; Anthropic wants them as `tool_use`
    blocks inside the content. And OpenAI's `role: "tool"` result messages have
    no Anthropic equivalent — they become `tool_result` blocks on a *user*
    turn, and consecutive ones must be merged into a single turn or the API
    rejects the sequence.
    """
    out: list[dict[str, Any]] = []

    for message in messages:
        role = message.get("role")

        if role == "tool":
            block = {
                "type": "tool_result",
                "tool_use_id": message.get("tool_call_id"),
                "content": message.get("content") or "",
            }
            # Anthropic requires results for parallel calls to share one user
            # turn, so append to the previous one when it is already results.
            if out and out[-1]["role"] == "user" and isinstance(out[-1]["content"], list):
                out[-1]["content"].append(block)
            else:
                out.append({"role": "user", "content": [block]})
            continue

        if role == "assistant" and message.get("tool_calls"):
            content: list[dict[str, Any]] = []
            if message.get("content"):
                content.append({"type": "text", "text": message["content"]})
            for call in message["tool_calls"]:
                content.append({
                    "type": "tool_use",
                    "id": call["id"],
                    "name": call["function"]["name"],
                    "input": _as_dict(call["function"].get("arguments")),
                })
            out.append({"role": "assistant", "content": content})
            continue

        out.append({"role": role, "content": message.get("content") or ""})

    return out


def _as_dict(arguments: Any) -> dict:
    if isinstance(arguments, dict):
        return arguments
    try:
        parsed = json.loads(arguments or "{}")
    except (json.JSONDecodeError, TypeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


_FENCE = re.compile(r"^```[a-zA-Z]*\s*\n?(.*?)\n?```\s*$", re.S)


def extract_json_text(text: str) -> str:
    """The JSON object in a reply that was asked for JSON only.

    OpenAI's json_object mode guarantees bare JSON; Anthropic has no such mode
    for schema-less output, only the instruction in the system prompt. Claude
    often honours it inside a ```json fence, or after a sentence of preamble,
    and every caller does a plain `json.loads` — which made every specialist
    and the synthesiser fall back on this provider. Normalising here keeps the
    contract the callers were written against. Text with no object in it is
    returned unchanged, so the caller's parse error still says what came back.
    """
    stripped = text.strip()
    fenced = _FENCE.match(stripped)
    if fenced:
        stripped = fenced.group(1).strip()
    if stripped.startswith(("{", "[")):
        return stripped
    start, end = stripped.find("{"), stripped.rfind("}")
    return stripped[start:end + 1] if 0 <= start < end else text


def parse_anthropic_content(content: Any) -> tuple[str, list[ToolCall]]:
    """Anthropic content blocks → (text, tool calls).

    Every text block is concatenated rather than taking the first. Reading
    `content[0].text` — which this module used to do — returns the wrong answer
    when a reply opens with a thinking or tool_use block, and raises outright
    once tools are in play, since a ToolUseBlock has no `.text`.
    """
    texts: list[str] = []
    calls: list[ToolCall] = []

    for block in content or []:
        block_type = getattr(block, "type", None)
        if block_type == "text":
            texts.append(getattr(block, "text", "") or "")
        elif block_type == "tool_use":
            calls.append(ToolCall(
                id=getattr(block, "id", ""),
                name=getattr(block, "name", ""),
                arguments=_as_dict(getattr(block, "input", {})),
            ))

    return "".join(texts), calls
