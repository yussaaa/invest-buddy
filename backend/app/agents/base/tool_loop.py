"""The tool-execution half of a bounded ReAct loop, shared by chat and research.

Both agents walk the same cycle — the model asks for tools, they run, the
results go back — and both verify the answer afterwards against what the tools
returned. That last part is why this returns three things, not two: the
messages the model reads, the records the UI shows, and the payloads the
grounding check needs. Records alone (name, latency, success) carry no numbers,
so a figure quoted from a tool result used to read as invented.
"""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass, field
from typing import Any

from app.models.provider import ToolCall
from app.tools.registry import ToolRegistry

# What the model is shown of one tool result. Larger payloads are truncated
# for the prompt but kept whole in `payloads` for grounding.
TOOL_MESSAGE_CHARS = 6000


@dataclass
class ToolRun:
    messages: list[dict[str, Any]] = field(default_factory=list)
    records: list[dict] = field(default_factory=list)
    payloads: list[dict] = field(default_factory=list)
    seconds: float = 0.0


def signature(name: str, arguments: dict) -> str:
    """Identity of a tool call, stable across argument ordering."""
    return f"{name}:{json.dumps(arguments, sort_keys=True, default=str)}"


def loads(raw: Any) -> dict:
    if isinstance(raw, dict):
        return raw
    try:
        parsed = json.loads(raw or "{}")
    except (json.JSONDecodeError, TypeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def pending_calls(messages: list[dict]) -> list[ToolCall]:
    """Tool calls on the last assistant message that have no result yet."""
    if not messages:
        return []
    last = messages[-1]
    if last.get("role") != "assistant" or not last.get("tool_calls"):
        return []
    return [
        ToolCall(id=raw["id"], name=raw["function"]["name"],
                 arguments=loads(raw["function"]["arguments"]))
        for raw in last["tool_calls"]
    ]


def assistant_message(content: str | None, calls: list[ToolCall]) -> dict:
    """The assistant turn that requests `calls`, in the OpenAI shape."""
    return {
        "role": "assistant",
        "content": content or None,
        "tool_calls": [{
            "id": call.id,
            "type": "function",
            "function": {"name": call.name, "arguments": json.dumps(call.arguments)},
        } for call in calls],
    }


async def run_tool_calls(calls: list[ToolCall]) -> ToolRun:
    """Run `calls` concurrently. Never raises — a failed tool is a result too."""
    run = ToolRun()
    if not calls:
        return run

    registry = ToolRegistry.get()
    t0 = time.monotonic()
    results = await asyncio.gather(
        *(registry.execute(call.name, call.arguments) for call in calls),
        return_exceptions=True,
    )

    for call, result in zip(calls, results):
        if isinstance(result, Exception):
            payload: Any = {"error": str(result)}
            record = {"name": call.name, "arguments": call.arguments, "success": False,
                      "error": str(result), "latency_ms": 0, "cache_hit": False}
        else:
            payload = result.data if result.success else {"error": result.error}
            record = {"name": call.name, "arguments": call.arguments,
                      "success": result.success, "error": result.error,
                      "latency_ms": result.latency_ms, "cache_hit": result.cache_hit}

        run.messages.append({
            "role": "tool",
            "tool_call_id": call.id,
            "content": json.dumps(payload, default=str)[:TOOL_MESSAGE_CHARS],
        })
        run.records.append(record)
        run.payloads.append({"name": call.name, "arguments": call.arguments, "data": payload})

    run.seconds = round(time.monotonic() - t0, 3)
    return run
