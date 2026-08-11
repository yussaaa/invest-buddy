"""Chat endpoints for the floating agent panel.

Two ways to run the same turn. `/chat` returns the finished reply as JSON —
what integration tests exercise, and the fallback when streaming is
unavailable. `/chat/stream` emits the turn as it happens.

The stream is a POST. `EventSourceResponse` is happy on any response; the
GET-only limitation belongs to the browser's `EventSource`, which the frontend
sidesteps with fetch and a ReadableStream. The alternative — POST to create,
then GET to stream — is what `analysis.py` does, and it costs a database row
and a polling loop to work around a client-side constraint that a chat turn
does not have to accept.
"""

from __future__ import annotations

import json
import uuid
from typing import AsyncIterator

import structlog
from fastapi import APIRouter, HTTPException, status
from sse_starlette.sse import EventSourceResponse

from app.agents.chat.graph import run_chat_turn, stream_chat_turn
from app.agents.chat.schemas import (
    ChatHistory,
    ChatHistoryMessage,
    ChatMessageOut,
    ChatRequest,
    ChatResponse,
    ToolActivity,
)
from app.config import get_settings
from app.memory import chat as chat_memory
from app.services.llm_guard import has_llm_credentials, missing_credentials_reason
from app.services.signals import DISCLAIMER

log = structlog.get_logger(__name__)

router = APIRouter()


def _require_enabled() -> None:
    settings = get_settings()
    if not settings.chat_enabled:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="The chat agent is disabled (CHAT_ENABLED=false).",
        )
    if not has_llm_credentials(settings):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=missing_credentials_reason(settings),
        )


def _record_turn(blocked: bool, signal_count: int) -> None:
    try:
        from app.observability.metrics import CHAT_SIGNALS_EMITTED, CHAT_TURNS_TOTAL

        CHAT_TURNS_TOTAL.labels(blocked=str(blocked).lower()).inc()
        CHAT_SIGNALS_EMITTED.observe(signal_count)
    except Exception:
        pass  # metrics must never break a reply


@router.post("", response_model=ChatResponse)
async def chat(request: ChatRequest) -> ChatResponse:
    """One turn, answered completely before returning."""
    _require_enabled()
    settings = get_settings()

    conversation_id = request.conversation_id or chat_memory.new_conversation_id()
    history = await chat_memory.history_for_model(
        request.conversation_id, settings.chat_history_turns
    )

    state = await run_chat_turn(
        message=request.message,
        screen=request.screen_context,
        conversation_id=conversation_id,
        user_id=request.user_id,
        history=history,
    )

    signal_set = state.get("signal_set")
    await chat_memory.append(
        conversation_id=conversation_id,
        user_id=request.user_id,
        ticker=state.get("ticker"),
        user_message=request.message,
        assistant_message=state.get("answer", ""),
    )
    _record_turn(state.get("blocked", False), len(signal_set.signals) if signal_set else 0)

    return ChatResponse(
        conversation_id=conversation_id,
        message=ChatMessageOut(
            message_id=uuid.uuid4().hex,
            content=state.get("answer", ""),
            signals_cited=state.get("signals_cited", []),
            flags=state.get("flags", []),
            blocked=state.get("blocked", False),
            disclaimer=DISCLAIMER,
        ),
        signal_set=signal_set,
        tool_calls=[ToolActivity(**record) for record in state.get("tool_records", [])],
        model=state.get("model", ""),
        usage=state.get("usage", {}),
        latency_ms=int(state.get("latency_breakdown", {}).get("total", 0) * 1000),
    )


@router.post("/stream")
async def chat_stream(request: ChatRequest):
    """The same turn, streamed.

    Events: conversation, context, tool_call, tool_result, token, flag, done,
    error. `context` carries the whole signal set and is emitted before the
    first token, so the panel can render grounded chips while the model is
    still writing.
    """
    _require_enabled()
    settings = get_settings()

    conversation_id = request.conversation_id or chat_memory.new_conversation_id()
    history = await chat_memory.history_for_model(
        request.conversation_id, settings.chat_history_turns
    )

    async def events() -> AsyncIterator[dict]:
        answer = ""
        ticker = None
        blocked = False
        signal_count = 0

        try:
            async for event, payload in stream_chat_turn(
                message=request.message,
                screen=request.screen_context,
                conversation_id=conversation_id,
                user_id=request.user_id,
                history=history,
            ):
                if event == "context":
                    ticker = payload.get("ticker")
                    signal_count = len((payload.get("signal_set") or {}).get("signals", []))
                elif event == "done":
                    answer = payload.get("content", "")
                    blocked = payload.get("replaced", False)

                yield {"event": event, "data": json.dumps(payload, default=str)}
        except Exception as exc:
            log.error("chat_stream_endpoint_failed", error=str(exc), exc_info=True)
            yield {
                "event": "error",
                "data": json.dumps({"message": "The agent could not complete this turn."}),
            }
            return

        if answer:
            # Persisted after the stream closes rather than mid-flight: the
            # reply is not final until verify has had its say, and a blocked
            # draft must never enter the history the next turn replays.
            await chat_memory.append(
                conversation_id=conversation_id,
                user_id=request.user_id,
                ticker=ticker,
                user_message=request.message,
                assistant_message=answer,
            )
        _record_turn(blocked, signal_count)

    return EventSourceResponse(events())


@router.get("/{conversation_id}", response_model=ChatHistory)
async def get_history(conversation_id: str) -> ChatHistory:
    stored = await chat_memory.load(conversation_id)
    return ChatHistory(
        conversation_id=conversation_id,
        ticker=stored.get("ticker"),
        messages=[
            ChatHistoryMessage(**{k: v for k, v in message.items()
                                  if k in ("role", "content", "ts")})
            for message in stored.get("messages", [])
            if message.get("role") in ("user", "assistant")
        ],
    )


@router.delete("/{conversation_id}", status_code=status.HTTP_204_NO_CONTENT)
async def clear_history(conversation_id: str) -> None:
    await chat_memory.clear(conversation_id)
