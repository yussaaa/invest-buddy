"""Contract tests for the chat endpoints — no DB, no Redis, no model.

These live in the unit suite rather than tests/integration/ on purpose: the
chat path touches no Postgres, so requiring a database to prove the request and
response shapes would be a dependency bought for nothing. The graph itself is
covered in test_chat_graph.py; what is asserted here is the HTTP contract, the
SSE framing, and that history is only written once a reply has survived
verification.
"""

from __future__ import annotations

import json
from typing import AsyncIterator

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.agents.chat.state import new_state
from app.api.v1 import chat as chat_api
from app.agents.chat.schemas import ScreenContext
from app.services.signals import derive_signals

TECHNICALS = {
    "symbol": "AAPL",
    "rsi": {"ticker": "AAPL", "period": 14, "current_rsi": 22.0, "zone": "oversold"},
    "macd": {"error": "x"},
    "moving_averages": {"error": "x"},
    "drawdown": {"error": "x"},
    "as_of": "2026-08-06T12:00:00+00:00",
}

BODY = {
    "message": "what is happening?",
    "screen_context": {"route": "/charting", "ticker": "AAPL", "range": "1Y"},
}


@pytest_asyncio.fixture
async def client(monkeypatch) -> AsyncIterator[AsyncClient]:
    """An app with only the chat router, and an in-memory conversation store."""
    store: dict[str, dict] = {}

    async def fake_load(conversation_id):
        return store.get(conversation_id, {})

    async def fake_history(conversation_id, limit):
        return [
            {"role": m["role"], "content": m["content"]}
            for m in store.get(conversation_id, {}).get("messages", [])
        ][-limit:]

    async def fake_append(conversation_id, user_id, ticker, user_message,
                          assistant_message):
        record = store.setdefault(conversation_id, {"ticker": ticker, "messages": []})
        record["ticker"] = ticker
        record["messages"] += [
            {"role": "user", "content": user_message},
            {"role": "assistant", "content": assistant_message},
        ]

    async def fake_clear(conversation_id):
        store.pop(conversation_id, None)

    monkeypatch.setattr(chat_api.chat_memory, "load", fake_load)
    monkeypatch.setattr(chat_api.chat_memory, "history_for_model", fake_history)
    monkeypatch.setattr(chat_api.chat_memory, "append", fake_append)
    monkeypatch.setattr(chat_api.chat_memory, "clear", fake_clear)
    monkeypatch.setattr(chat_api, "has_llm_credentials", lambda settings: True)

    app = FastAPI()
    app.include_router(chat_api.router, prefix="/api/v1/chat")

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        ac.store = store  # type: ignore[attr-defined]
        yield ac


def finished_state(answer="RSI is 22.", blocked=False):
    state = new_state(
        conversation_id="c1", user_id="anonymous", message="q",
        screen=ScreenContext(ticker="AAPL"),
    )
    state.update(
        ticker="AAPL",
        technicals=TECHNICALS,
        signal_set=derive_signals(TECHNICALS),
        answer=answer,
        blocked=blocked,
        signals_cited=["rsi_oversold"],
        model="fake-model",
        usage={"prompt_tokens": 10, "completion_tokens": 5},
        latency_breakdown={"total": 1.25},
        tool_records=[{"name": "get_recent_news", "arguments": {}, "success": True,
                       "error": None, "latency_ms": 12, "cache_hit": False}],
    )
    return state


# ── POST /chat ──────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_turn_returns_the_reply_and_the_signal_set(client, monkeypatch):
    async def fake_turn(**kwargs):
        return finished_state()

    monkeypatch.setattr(chat_api, "run_chat_turn", fake_turn)

    response = await client.post("/api/v1/chat", json=BODY)
    body = response.json()

    assert response.status_code == 200
    assert body["message"]["content"] == "RSI is 22."
    assert body["message"]["blocked"] is False
    assert body["message"]["disclaimer"]
    assert body["signal_set"]["ticker"] == "AAPL"
    assert "rsi_oversold" in [s["id"] for s in body["signal_set"]["signals"]]
    assert body["tool_calls"][0]["name"] == "get_recent_news"
    assert body["latency_ms"] == 1250


@pytest.mark.asyncio
async def test_a_conversation_id_is_minted_and_then_reused(client, monkeypatch):
    monkeypatch.setattr(chat_api, "run_chat_turn", lambda **k: _async(finished_state()))

    first = (await client.post("/api/v1/chat", json=BODY)).json()
    second = (await client.post(
        "/api/v1/chat", json={**BODY, "conversation_id": first["conversation_id"]}
    )).json()

    assert first["conversation_id"]
    assert second["conversation_id"] == first["conversation_id"]
    assert len(client.store[first["conversation_id"]]["messages"]) == 4


@pytest.mark.asyncio
async def test_prior_turns_are_replayed_into_the_next_one(client, monkeypatch):
    seen: dict = {}

    async def fake_turn(**kwargs):
        seen["history"] = kwargs["history"]
        return finished_state()

    monkeypatch.setattr(chat_api, "run_chat_turn", fake_turn)

    first = (await client.post("/api/v1/chat", json=BODY)).json()
    await client.post("/api/v1/chat",
                      json={**BODY, "conversation_id": first["conversation_id"]})

    assert seen["history"][0]["content"] == "what is happening?"


@pytest.mark.asyncio
async def test_a_blocked_reply_is_still_a_200_with_the_substitute(client, monkeypatch):
    """A replaced reply is an answer, not an error — the client renders it normally."""
    monkeypatch.setattr(
        chat_api, "run_chat_turn",
        lambda **k: _async(finished_state(answer="Here is what holds.", blocked=True)),
    )

    body = (await client.post("/api/v1/chat", json=BODY)).json()

    assert body["message"]["blocked"] is True
    assert body["message"]["content"] == "Here is what holds."


@pytest.mark.asyncio
async def test_an_empty_message_is_rejected(client):
    response = await client.post("/api/v1/chat", json={**BODY, "message": ""})

    assert response.status_code == 422


@pytest.mark.asyncio
async def test_the_screen_context_is_optional(client, monkeypatch):
    """The launcher is global, so it can be opened on a page with no ticker."""
    monkeypatch.setattr(chat_api, "run_chat_turn", lambda **k: _async(finished_state()))

    response = await client.post("/api/v1/chat", json={"message": "hello"})

    assert response.status_code == 200


@pytest.mark.asyncio
async def test_the_feature_flag_turns_the_endpoint_off(client, monkeypatch):
    from app.config import get_settings

    settings = get_settings()
    monkeypatch.setattr(settings, "chat_enabled", False)

    response = await client.post("/api/v1/chat", json=BODY)

    assert response.status_code == 503


@pytest.mark.asyncio
async def test_missing_credentials_name_the_fix(client, monkeypatch):
    monkeypatch.setattr(chat_api, "has_llm_credentials", lambda settings: False)

    response = await client.post("/api/v1/chat", json=BODY)

    assert response.status_code == 503
    assert "MODEL_PROVIDER" in response.json()["detail"]


# ── POST /chat/stream ───────────────────────────────────────────────────────


def sse_events(text: str) -> list[tuple[str, dict]]:
    """Parse an SSE body into (event, data) pairs.

    sse_starlette frames with CRLF, so normalise before splitting on the blank
    line that separates events.
    """
    out = []
    for block in text.replace("\r\n", "\n").strip().split("\n\n"):
        event = data = None
        for line in block.splitlines():
            if line.startswith("event:"):
                event = line[6:].strip()
            elif line.startswith("data:"):
                data = line[5:].strip()
        if event:
            out.append((event, json.loads(data) if data else {}))
    return out


def scripted_stream(*events):
    async def _stream(**kwargs):
        for event in events:
            yield event
    return _stream


@pytest.mark.asyncio
async def test_the_stream_is_framed_as_sse(client, monkeypatch):
    monkeypatch.setattr(chat_api, "stream_chat_turn", scripted_stream(
        ("conversation", {"conversation_id": "c1"}),
        ("context", {"ticker": "AAPL", "signal_set": {"signals": [{"id": "rsi_oversold"}]}}),
        ("token", {"text": "RSI "}),
        ("token", {"text": "is 22."}),
        ("done", {"content": "RSI is 22.", "replaced": False}),
    ))

    response = await client.post("/api/v1/chat/stream", json=BODY)
    events = sse_events(response.text)

    assert response.status_code == 200
    assert "text/event-stream" in response.headers["content-type"]
    assert [name for name, _ in events] == [
        "conversation", "context", "token", "token", "done",
    ]


@pytest.mark.asyncio
async def test_context_precedes_the_first_token(client, monkeypatch):
    monkeypatch.setattr(chat_api, "stream_chat_turn", scripted_stream(
        ("context", {"ticker": "AAPL", "signal_set": {"signals": []}}),
        ("token", {"text": "hi"}),
        ("done", {"content": "hi", "replaced": False}),
    ))

    names = [name for name, _ in sse_events(
        (await client.post("/api/v1/chat/stream", json=BODY)).text
    )]

    assert names.index("context") < names.index("token")


@pytest.mark.asyncio
async def test_the_streamed_reply_is_persisted_after_verification(client, monkeypatch):
    """Written on `done`, never mid-stream — a draft can still be replaced."""
    monkeypatch.setattr(chat_api, "stream_chat_turn", scripted_stream(
        ("context", {"ticker": "AAPL", "signal_set": {"signals": []}}),
        ("token", {"text": "You should buy."}),
        ("done", {"content": "Here is what holds instead.", "replaced": True}),
    ))

    await client.post("/api/v1/chat/stream",
                      json={**BODY, "conversation_id": "conv-x"})

    stored = client.store["conv-x"]["messages"]
    assert stored[-1]["content"] == "Here is what holds instead."
    assert "You should buy." not in json.dumps(stored)


@pytest.mark.asyncio
async def test_a_stream_that_explodes_emits_an_error_event(client, monkeypatch):
    async def exploding(**kwargs):
        yield ("context", {"ticker": "AAPL", "signal_set": {"signals": []}})
        raise RuntimeError("boom")

    monkeypatch.setattr(chat_api, "stream_chat_turn", exploding)

    events = sse_events((await client.post("/api/v1/chat/stream", json=BODY)).text)

    assert events[-1][0] == "error"
    assert "boom" not in json.dumps(events[-1][1]), "internals must not leak to the client"


# ── History ─────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_history_can_be_read_back(client, monkeypatch):
    monkeypatch.setattr(chat_api, "run_chat_turn", lambda **k: _async(finished_state()))
    created = (await client.post("/api/v1/chat", json=BODY)).json()

    body = (await client.get(f"/api/v1/chat/{created['conversation_id']}")).json()

    assert body["ticker"] == "AAPL"
    assert [m["role"] for m in body["messages"]] == ["user", "assistant"]


@pytest.mark.asyncio
async def test_an_unknown_conversation_reads_as_empty(client):
    body = (await client.get("/api/v1/chat/does-not-exist")).json()

    assert body["messages"] == []


@pytest.mark.asyncio
async def test_a_conversation_can_be_cleared(client, monkeypatch):
    monkeypatch.setattr(chat_api, "run_chat_turn", lambda **k: _async(finished_state()))
    created = (await client.post("/api/v1/chat", json=BODY)).json()

    response = await client.delete(f"/api/v1/chat/{created['conversation_id']}")

    assert response.status_code == 204
    assert created["conversation_id"] not in client.store


async def _async(value):
    return value
