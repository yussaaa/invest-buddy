"""The chat agent's graph: gather grounded context, let the model ask for more, verify.

    prepare_context ──▶ gather ◀──▶ tools
                          │
                          ▼
                       answer ──▶ verify ──▶ END

Two things distinguish this from `orchestrator/graph.py`. That one is a
fan-out DAG: five specialists in parallel, once, then synthesis. This is a
bounded cycle — the model may ask for data, read it, and ask again, up to a cap
— followed by a deterministic verification node that can reject what the model
wrote. Different shape, different failure modes.

`prepare_context` runs *before* the first model call, so even a turn that uses
no tools at all is fully grounded. Tools are for the follow-up ("why did it
drop today?"), not for the primary answer.

Compiled without a checkpointer. Only `InMemorySaver` ships with the installed
langgraph, and it neither survives a restart nor is shared between uvicorn
workers; conversation history comes from Redis via `memory/chat.py` instead and
is loaded into state per turn.
"""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from typing import Any, AsyncIterator, Optional

import structlog
from langgraph.graph import END, StateGraph

from app.agents.base.guardrails import (
    check_investment_advice,
    check_numeric_grounding,
)
from app.agents.base.schemas import GuardrailFlag
from app.agents.chat.prompts import (
    BLOCKED_REPLY_EMPTY,
    BLOCKED_REPLY_HEADER,
    CHAT_SYSTEM_PROMPT,
    CHAT_TOOL_NAMES,
)
from app.agents.chat.schemas import ScreenContext
from app.agents.chat.state import ChatState, new_state
from app.config import get_settings
from app.models.factory import get_provider
from app.models.provider import ModelProvider, ToolCall
from app.services import market_data, technicals as technicals_service
from app.services.signals import DISCLAIMER, SignalSet, derive_signals
from app.tools.registry import ToolRegistry

log = structlog.get_logger(__name__)

# The gather step should emit tool calls, not prose — its output is discarded
# except for the calls. Capping it keeps a model that ignores that instruction
# from writing an essay nobody reads.
GATHER_MAX_TOKENS = 512
ANSWER_MAX_TOKENS = 900

# Trailing line the model uses to declare which signals it relied on.
SIGNAL_CITATION_PREFIX = "signals:"


def _model_name(settings) -> str:
    return settings.smart_model if settings.chat_model == "smart" else settings.fast_model


# ── 1. Context ─────────────────────────────────────────────────────────────


async def prepare_context_node(state: ChatState) -> dict:
    """Fetch everything the answer is grounded in, before the model is asked anything.

    Four concurrent reads, all already cached — technicals for 120s, quotes for
    10s, profile for 600s — so this is typically a few milliseconds and the
    signal chips can render before the model has produced a token.

    Every read degrades independently. A failed profile costs the company name;
    it does not cost the answer.
    """
    ticker = (state.get("ticker") or "").upper()
    if not ticker:
        # No ticker on screen — /market, /settings, or a bare route. The agent
        # can still hold a conversation, just not a grounded one.
        return {"latency_breakdown": {"context": 0.0}}

    t0 = time.monotonic()
    results = await asyncio.gather(
        technicals_service.get_technicals(ticker),
        market_data.get_quotes([ticker]),
        market_data.get_profile(ticker),
        market_data.get_events(days=14, extra_symbols=ticker),
        return_exceptions=True,
    )

    def ok(result, label):
        if isinstance(result, Exception):
            log.warning("chat_context_failed", ticker=ticker, source=label,
                        error=str(result))
            return None
        return result

    technicals = ok(results[0], "technicals") or {}
    quotes = ok(results[1], "quotes") or []
    profile = ok(results[2], "profile")
    events = ok(results[3], "events")

    signal_set: Optional[SignalSet] = None
    if technicals:
        try:
            signal_set = derive_signals(technicals, ticker=ticker, events=events)
        except Exception as exc:  # a bad reading must not end the turn
            log.error("chat_signals_failed", ticker=ticker, error=str(exc))

    return {
        "technicals": technicals,
        "quote": quotes[0] if quotes else None,
        "profile": profile,
        "signal_set": signal_set,
        "latency_breakdown": {"context": round(time.monotonic() - t0, 3)},
    }


def _context_block(state: ChatState) -> str:
    """The grounded payload the model narrates from."""
    signal_set = state.get("signal_set")
    screen = state.get("screen") or ScreenContext()
    quote = state.get("quote") or {}
    profile = state.get("profile") or {}

    payload: dict[str, Any] = {
        "ticker": state.get("ticker") or None,
        "viewing": {
            "range": screen.range,
            "chart_type": screen.chart_type,
            "moving_averages_shown": screen.ma_periods,
        },
        "quote": {
            "last": quote.get("last"),
            "change_percent": quote.get("change_percent"),
            "volume": quote.get("volume"),
        } if quote else None,
        "company": {
            "name": profile.get("name"),
            "sector": profile.get("sector"),
            "industry": profile.get("industry"),
        } if profile else None,
        "signal_set": signal_set.model_dump(mode="json") if signal_set else None,
    }

    if screen.hovered_bar is not None:
        # The one thing the backend cannot reconstruct from the ticker: which
        # candle the user is pointing at.
        payload["hovered_candle"] = screen.hovered_bar.model_dump(mode="json")

    return json.dumps(payload, default=str)


def _base_messages(state: ChatState) -> list[dict[str, Any]]:
    """System prompt, prior turns, the grounded context, then the question."""
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": CHAT_SYSTEM_PROMPT},
        *state.get("history", []),
        {
            "role": "user",
            "content": (
                f"CONTEXT (grounded, computed before this turn):\n"
                f"{_context_block(state)}\n\n"
                f"QUESTION: {state['message']}"
            ),
        },
    ]
    return messages


# ── 2. Gather ⇄ tools ──────────────────────────────────────────────────────


def _tool_schemas(provider: ModelProvider) -> list[dict]:
    if not provider.supports_tools:
        return []
    return ToolRegistry.get().get_schema_for_llm(CHAT_TOOL_NAMES)


async def gather_node(state: ChatState) -> dict:
    """Ask the model whether it needs anything the context does not already hold.

    Its prose is discarded — only the tool calls matter. Separating this from
    the answer costs one cheap, short model call and buys a clean streaming
    path: by the time the narration starts, every tool result is in hand, so
    that call can stream straight through without any chance of a tool request
    arriving mid-sentence.
    """
    settings = get_settings()
    provider = get_provider()
    tools = _tool_schemas(provider)

    if not tools or state.get("tool_iterations", 0) >= settings.chat_max_tool_iterations:
        return {"messages": []}

    t0 = time.monotonic()
    try:
        response = await provider.complete(
            messages=_base_messages(state) + state.get("messages", []) + [{
                "role": "user",
                "content": (
                    "Before answering: is there anything you need that the context "
                    "above does not contain? Call the tools you need, or reply with "
                    "the single word ENOUGH. Do not answer the question yet."
                ),
            }],
            model=_model_name(settings),
            temperature=0.0,
            max_tokens=GATHER_MAX_TOKENS,
            tools=tools,
            tool_choice="auto",
        )
    except Exception as exc:
        # A failed gather is not a failed turn — the context alone answers most
        # questions, which is the whole reason it is fetched up front.
        log.warning("chat_gather_failed", error=str(exc))
        return {"messages": [], "latency_breakdown": {"gather": 0.0}}

    if not response.tool_calls:
        return {
            "messages": [],
            "usage": _usage(state, response),
            "latency_breakdown": {"gather": round(time.monotonic() - t0, 3)},
        }

    calls = response.tool_calls[: settings.chat_max_tool_calls]
    return {
        "messages": [{
            "role": "assistant",
            "content": response.content or None,
            "tool_calls": [{
                "id": call.id,
                "type": "function",
                "function": {"name": call.name, "arguments": json.dumps(call.arguments)},
            } for call in calls],
        }],
        "tool_iterations": state.get("tool_iterations", 0) + 1,
        "usage": _usage(state, response),
        "latency_breakdown": {"gather": round(time.monotonic() - t0, 3)},
    }


def _pending_calls(state: ChatState) -> list[ToolCall]:
    """Tool calls on the last assistant message that have no result yet."""
    messages = state.get("messages", [])
    if not messages:
        return []
    last = messages[-1]
    if last.get("role") != "assistant" or not last.get("tool_calls"):
        return []
    return [
        ToolCall(
            id=raw["id"],
            name=raw["function"]["name"],
            arguments=_loads(raw["function"]["arguments"]),
        )
        for raw in last["tool_calls"]
    ]


def _loads(raw: Any) -> dict:
    if isinstance(raw, dict):
        return raw
    try:
        parsed = json.loads(raw or "{}")
    except (json.JSONDecodeError, TypeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


async def tools_node(state: ChatState) -> dict:
    """Run the requested tools concurrently and feed the results back."""
    calls = _pending_calls(state)
    if not calls:
        return {"messages": []}

    registry = ToolRegistry.get()
    t0 = time.monotonic()
    results = await asyncio.gather(
        *(registry.execute(call.name, call.arguments) for call in calls),
        return_exceptions=True,
    )

    messages: list[dict[str, Any]] = []
    records: list[dict] = []

    for call, result in zip(calls, results):
        if isinstance(result, Exception):
            payload, record = (
                {"error": str(result)},
                {"name": call.name, "arguments": call.arguments, "success": False,
                 "error": str(result), "latency_ms": 0, "cache_hit": False},
            )
        else:
            payload = result.data if result.success else {"error": result.error}
            record = {
                "name": call.name, "arguments": call.arguments,
                "success": result.success, "error": result.error,
                "latency_ms": result.latency_ms, "cache_hit": result.cache_hit,
            }

        messages.append({
            "role": "tool",
            "tool_call_id": call.id,
            "content": json.dumps(payload, default=str)[:6000],
        })
        records.append(record)

    return {
        "messages": messages,
        "tool_records": records,
        "latency_breakdown": {"tools": round(time.monotonic() - t0, 3)},
    }


def route_after_gather(state: ChatState) -> str:
    """Loop back for tool results, or move on to the answer.

    The iteration cap is enforced in `gather_node`, which stops requesting
    tools once it is reached — so this cannot spin.
    """
    return "tools_node" if _pending_calls(state) else "answer_node"


# ── 3. Answer ──────────────────────────────────────────────────────────────


def _answer_messages(state: ChatState) -> list[dict[str, Any]]:
    """The narration call: full context, every tool result, no tools offered.

    Shared with the streaming path, which sends exactly these to `stream()`.
    """
    return _base_messages(state) + state.get("messages", []) + [{
        "role": "user",
        "content": (
            "Now answer the question, using the context and any tool results "
            "above. End with a line listing the signal ids you relied on, "
            f"e.g. `{SIGNAL_CITATION_PREFIX} rsi_oversold, price_below_ma200`."
        ),
    }]


def _usage(state: ChatState, response) -> dict[str, int]:
    prior = state.get("usage") or {}
    return {
        "prompt_tokens": prior.get("prompt_tokens", 0) + response.prompt_tokens,
        "completion_tokens": prior.get("completion_tokens", 0) + response.completion_tokens,
    }


async def answer_node(state: ChatState) -> dict:
    settings = get_settings()
    t0 = time.monotonic()
    try:
        response = await get_provider().complete(
            messages=_answer_messages(state),
            model=_model_name(settings),
            temperature=0.3,
            max_tokens=ANSWER_MAX_TOKENS,
        )
    except Exception as exc:
        log.error("chat_answer_failed", error=str(exc))
        return {"error": str(exc), "answer": ""}

    return {
        "answer": response.content.strip(),
        "model": response.model,
        "usage": _usage(state, response),
        "latency_breakdown": {"answer": round(time.monotonic() - t0, 3)},
    }


# ── 4. Verify ──────────────────────────────────────────────────────────────


def _split_citations(answer: str) -> tuple[str, list[str]]:
    """Peel the trailing `signals: a, b` line off the prose."""
    lines = answer.rstrip().splitlines()
    if not lines:
        return answer, []

    last = lines[-1].strip().lstrip("`").rstrip("`").strip()
    if not last.lower().startswith(SIGNAL_CITATION_PREFIX):
        return answer, []

    cited = [
        part.strip().strip("`,.")
        for part in last[len(SIGNAL_CITATION_PREFIX):].split(",")
        if part.strip()
    ]
    return "\n".join(lines[:-1]).rstrip(), cited


def _grounding_evidence(state: ChatState) -> dict:
    """Every number the model was legitimately shown.

    Passed generously on purpose. A figure it saw but that is missing here would
    be reported as invented, and a false block is worse than a missed one — the
    personalized-advice check is the tier that must not leak.
    """
    signal_set = state.get("signal_set")
    return {
        "signals": signal_set.model_dump(mode="json") if signal_set else {},
        "technicals": state.get("technicals") or {},
        "quote": state.get("quote") or {},
        "profile": state.get("profile") or {},
        "tools": [record for record in state.get("tool_records", [])],
        "screen": (state.get("screen") or ScreenContext()).model_dump(mode="json"),
    }


def _hedged_reply(state: ChatState) -> str:
    """What a blocked turn says instead.

    Built from the signal set rather than being a flat refusal. A user who gets
    stonewalled rephrases until something slips through; a user who gets the
    conditions laid out has already been answered.
    """
    ticker = state.get("ticker") or "this ticker"
    signal_set = state.get("signal_set")
    signals = signal_set.signals if signal_set else []

    if not signals:
        return BLOCKED_REPLY_EMPTY.format(ticker=ticker)

    lines = [BLOCKED_REPLY_HEADER.format(ticker=ticker), ""]
    for signal in signals:
        lines.append(f"- **{signal.label}** ({signal.direction}, {signal.timeframe}-term) "
                     f"— {signal.rationale}")
        if signal.invalidation:
            lines.append(f"  {signal.invalidation}")
    if signal_set and signal_set.conflicts:
        lines.append("")
        lines.append("Worth noting: " + "; ".join(signal_set.conflicts) + ".")
    return "\n".join(lines)


def verify_node(state: ChatState) -> dict:
    """Check the drafted reply before anyone sees it.

    Two checks, deliberately different in severity. Personalized advice is a
    hard block — the reply is replaced. Ungrounded figures are flagged and the
    reply is also replaced, because a confidently wrong RSI is the one failure
    this whole design exists to prevent.
    """
    answer = (state.get("answer") or "").strip()
    if not answer:
        return {
            "answer": _hedged_reply(state),
            "blocked": True,
            "signals_cited": [],
            "flags": [GuardrailFlag(
                flag_type="data_unavailable", agent="chat",
                detail="The model returned no answer; substituted the signal summary.",
                severity="warning",
            )],
        }

    prose, cited = _split_citations(answer)
    flags: list[GuardrailFlag] = []

    advice_hits = check_investment_advice(prose, mode="chat")
    for hit in advice_hits:
        flags.append(GuardrailFlag(
            flag_type="investment_advice", agent="chat",
            detail=f"Blocked: reply contained direct investment advice: {hit}",
            severity="error",
        ))

    ungrounded = check_numeric_grounding(prose, _grounding_evidence(state))
    for figure in ungrounded:
        flags.append(GuardrailFlag(
            flag_type="hallucination", agent="chat",
            detail=f"Blocked: figure {figure} does not appear in any reading shown "
                   f"to the model.",
            severity="error",
        ))

    known_ids = {
        s.id for s in (state["signal_set"].signals if state.get("signal_set") else [])
    }
    invented = [c for c in cited if c not in known_ids]
    for signal_id in invented:
        flags.append(GuardrailFlag(
            flag_type="missing_citation", agent="chat",
            detail=f"Cited signal '{signal_id}' is not in the signal set.",
            severity="warning",
        ))

    blocked = bool(advice_hits or ungrounded)
    if blocked:
        log.warning("chat_reply_blocked", advice=len(advice_hits),
                    ungrounded=len(ungrounded))
        try:
            from app.observability.metrics import CHAT_GUARDRAIL_BLOCKS_TOTAL
            CHAT_GUARDRAIL_BLOCKS_TOTAL.labels(
                reason="investment_advice" if advice_hits else "ungrounded_figure"
            ).inc()
        except Exception:
            pass

    return {
        "answer": _hedged_reply(state) if blocked else prose,
        "blocked": blocked,
        "signals_cited": [c for c in cited if c in known_ids],
        "flags": flags,
    }


# ── Graph ──────────────────────────────────────────────────────────────────


def build_graph() -> Any:
    builder = StateGraph(ChatState)
    builder.add_node("prepare_context_node", prepare_context_node)
    builder.add_node("gather_node", gather_node)
    builder.add_node("tools_node", tools_node)
    builder.add_node("answer_node", answer_node)
    builder.add_node("verify_node", verify_node)

    builder.set_entry_point("prepare_context_node")
    builder.add_edge("prepare_context_node", "gather_node")
    builder.add_conditional_edges(
        "gather_node", route_after_gather, ["tools_node", "answer_node"]
    )
    builder.add_edge("tools_node", "gather_node")   # the cycle
    builder.add_edge("answer_node", "verify_node")
    builder.add_edge("verify_node", END)
    return builder.compile()


_graph: Any = None


def get_graph() -> Any:
    global _graph
    if _graph is None:
        _graph = build_graph()
    return _graph


# ── Entry points ───────────────────────────────────────────────────────────


async def run_chat_turn(
    *,
    message: str,
    screen: ScreenContext,
    conversation_id: str,
    user_id: str = "anonymous",
    history: Optional[list[dict]] = None,
) -> ChatState:
    """One complete turn through the compiled graph."""
    t0 = time.monotonic()
    state = new_state(
        conversation_id=conversation_id, user_id=user_id,
        message=message, screen=screen, history=history,
    )
    try:
        final: ChatState = await get_graph().ainvoke(state)
    except Exception as exc:
        # The signal set is already computed by the time most failures happen,
        # so the hedged summary is a real answer rather than an apology.
        log.error("chat_turn_failed", error=str(exc), exc_info=True)
        state["error"] = str(exc)
        state["answer"] = _hedged_reply(state)
        state["blocked"] = True
        final = state

    final["latency_breakdown"] = {
        **final.get("latency_breakdown", {}),
        "total": round(time.monotonic() - t0, 3),
    }
    return final


async def stream_chat_turn(
    *,
    message: str,
    screen: ScreenContext,
    conversation_id: str,
    user_id: str = "anonymous",
    history: Optional[list[dict]] = None,
) -> AsyncIterator[tuple[str, dict]]:
    """The same turn, yielding (event, payload) as it happens.

    This walks the node functions rather than calling `ainvoke`, because token
    streaming needs to reach inside the answer step and a compiled graph does
    not expose that. Every node above is reused unchanged — what differs is the
    answer call, which goes to `stream()` instead of `complete()`, and the loop
    control, which LangGraph expresses as edges and this expresses as a while.
    """
    t0 = time.monotonic()
    settings = get_settings()
    state = new_state(
        conversation_id=conversation_id, user_id=user_id,
        message=message, screen=screen, history=history,
    )

    yield "conversation", {"conversation_id": conversation_id}

    try:
        state.update(await prepare_context_node(state))
        signal_set = state.get("signal_set")
        yield "context", {
            "ticker": state.get("ticker"),
            "signal_set": signal_set.model_dump(mode="json") if signal_set else None,
            "disclaimer": DISCLAIMER,
        }

        for _ in range(settings.chat_max_tool_iterations):
            update = await gather_node(state)
            _merge(state, update)
            calls = _pending_calls(state)
            if not calls:
                break
            for call in calls:
                yield "tool_call", {"name": call.name, "arguments": call.arguments}

            update = await tools_node(state)
            _merge(state, update)
            for record in update.get("tool_records", []):
                yield "tool_result", {
                    "name": record["name"], "success": record["success"],
                    "latency_ms": record["latency_ms"],
                    "cache_hit": record["cache_hit"], "error": record.get("error"),
                }

        chunks: list[str] = []
        async for chunk in get_provider().stream(
            messages=_answer_messages(state),
            model=_model_name(settings),
            temperature=0.3,
            max_tokens=ANSWER_MAX_TOKENS,
            on_usage=lambda p, c: state.update(
                usage={"prompt_tokens": p, "completion_tokens": c}
            ),
        ):
            chunks.append(chunk)
            yield "token", {"text": chunk}

        state["answer"] = "".join(chunks).strip()
        state["model"] = _model_name(settings)
    except Exception as exc:
        log.error("chat_stream_failed", error=str(exc), exc_info=True)
        yield "error", {"message": "The agent could not complete this turn."}
        return

    verdict = verify_node(state)
    _merge(state, verdict)

    for flag in verdict.get("flags", []):
        yield "flag", flag.model_dump(mode="json")

    yield "done", {
        "message_id": uuid.uuid4().hex,
        "content": state["answer"],
        # A blocked reply was streamed token by token before being rejected, so
        # the client must replace what it rendered rather than append to it.
        "replaced": state.get("blocked", False),
        "signals_cited": state.get("signals_cited", []),
        "model": state.get("model", ""),
        "usage": state.get("usage", {}),
        "disclaimer": DISCLAIMER,
        "latency_ms": int((time.monotonic() - t0) * 1000),
    }


def _merge(state: ChatState, update: dict) -> None:
    """Apply a node's return value the way LangGraph's reducers would.

    Only the accumulating fields need special handling; everything else is a
    plain overwrite.
    """
    for key, value in update.items():
        if key in ("messages", "tool_records", "flags"):
            state[key] = list(state.get(key) or []) + list(value)
        elif key in ("latency_breakdown", "usage"):
            state[key] = {**(state.get(key) or {}), **value}
        else:
            state[key] = value
