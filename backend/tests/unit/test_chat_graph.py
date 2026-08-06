"""Unit tests for the chat graph — no network, no model, no Redis.

`ModelProvider` is two methods, so a scripted fake is cheap and makes the
control flow fully assertable: how many times the loop runs, what happens when
the model asks for tools forever, and what a user actually sees when a reply is
rejected.

The context fetch and the tool registry are patched at the module boundary, so
every test here is deterministic and runs in milliseconds.
"""

from __future__ import annotations

import json
from typing import Any, AsyncIterator, Optional

import pytest

from app.agents.chat import graph as chat_graph
from app.agents.chat.schemas import HoveredBar, ScreenContext
from app.models.provider import LLMResponse, ModelProvider, ToolCall
from app.tools.registry import ToolResult


class FakeProvider(ModelProvider):
    """Replays a script of LLMResponses and records what it was sent."""

    def __init__(self, script: list[LLMResponse], supports_tools: bool = True):
        self._script = list(script)
        self._supports_tools = supports_tools
        self.calls: list[dict] = []
        self.streamed: list[dict] = []

    @property
    def supports_tools(self) -> bool:
        return self._supports_tools

    async def complete(self, messages, model, response_format=None, temperature=0.1,
                       max_tokens=4096, tools=None, tool_choice=None) -> LLMResponse:
        self.calls.append({"messages": messages, "tools": tools, "model": model})
        if not self._script:
            return LLMResponse(content="fallback", model=model)
        return self._script.pop(0)

    async def stream(self, messages, model, temperature=0.1, max_tokens=4096,
                     on_usage=None) -> AsyncIterator[str]:
        self.streamed.append({"messages": messages, "model": model})
        text = self._script.pop(0).content if self._script else "streamed answer"
        for word in text.split(" "):
            yield word + " "
        if on_usage:
            on_usage(10, 20)


def reply(content: str = "", tool_calls: Optional[list[ToolCall]] = None) -> LLMResponse:
    return LLMResponse(content=content, model="fake-model", prompt_tokens=10,
                       completion_tokens=5, tool_calls=tool_calls or [])


TECHNICALS = {
    "symbol": "AAPL",
    "rsi": {"ticker": "AAPL", "period": 14, "current_rsi": 22.0, "zone": "oversold"},
    "macd": {"error": "x"},
    "moving_averages": {
        "ticker": "AAPL", "current_price": 180.0,
        "levels": [{"window": 200, "sma": 195.0, "available": True,
                    "above": False, "distance_percent": -7.7,
                    "slope_percent_5d": -0.2, "direction": "falling"}],
        "crosses": [], "alignment": "mixed", "above_count": 0, "total_count": 1,
    },
    "drawdown": {"error": "x"},
    "as_of": "2026-08-06T12:00:00+00:00",
}


@pytest.fixture
def patched(monkeypatch):
    """Deterministic context; a registry that echoes whatever it is asked for."""
    async def fake_technicals(symbol):
        return TECHNICALS

    async def fake_quotes(symbols):
        return [{"symbol": symbols[0], "last": 180.0, "change_percent": -1.2,
                 "volume": 50_000_000}]

    async def fake_profile(symbol):
        return {"name": "Apple Inc.", "sector": "Technology", "industry": "Consumer"}

    async def fake_events(days=7, extra_symbols=None):
        return {"earnings": [], "economic": {"events": [], "available": False}}

    monkeypatch.setattr(chat_graph.technicals_service, "get_technicals", fake_technicals)
    monkeypatch.setattr(chat_graph.market_data, "get_quotes", fake_quotes)
    monkeypatch.setattr(chat_graph.market_data, "get_profile", fake_profile)
    monkeypatch.setattr(chat_graph.market_data, "get_events", fake_events)

    class FakeRegistry:
        def __init__(self):
            self.executed: list[tuple[str, dict]] = []

        def get_schema_for_llm(self, names=None):
            return [{"type": "function",
                     "function": {"name": n, "description": "", "parameters": {}}}
                    for n in (names or [])]

        async def execute(self, name, arguments):
            self.executed.append((name, arguments))
            return ToolResult(success=True, data={"tool": name}, latency_ms=3)

    registry = FakeRegistry()
    monkeypatch.setattr(chat_graph.ToolRegistry, "get", staticmethod(lambda: registry))
    return registry


def use_provider(monkeypatch, provider):
    monkeypatch.setattr(chat_graph, "get_provider", lambda: provider)
    return provider


SCREEN = ScreenContext(route="/charting", ticker="AAPL", range="1Y",
                       chart_type="candles", ma_periods=[50])


async def run(**kwargs):
    return await chat_graph.run_chat_turn(
        message=kwargs.pop("message", "what is happening?"),
        screen=kwargs.pop("screen", SCREEN),
        conversation_id="conv-1",
        **kwargs,
    )


# ── Context ─────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_context_is_prepared_before_the_model_is_called(patched, monkeypatch):
    """A zero-tool turn is still fully grounded — that is the point of the design."""
    provider = use_provider(monkeypatch, FakeProvider([
        reply("ENOUGH"),
        reply("RSI is 22.\nsignals: rsi_oversold"),
    ]))

    state = await run()

    assert state["signal_set"] is not None
    assert "rsi_oversold" in {s.id for s in state["signal_set"].signals}
    # The grounded context reached the model on its very first call.
    assert "rsi_oversold" in json.dumps(provider.calls[0]["messages"])


@pytest.mark.asyncio
async def test_a_turn_with_no_ticker_still_answers(patched, monkeypatch):
    """The user may be on /market or /settings, where nothing is on screen."""
    use_provider(monkeypatch, FakeProvider([reply("ENOUGH"), reply("Ask me about a ticker.")]))

    state = await run(screen=ScreenContext(route="/market"))

    assert state["signal_set"] is None
    assert state["answer"]
    assert not state["blocked"]


@pytest.mark.asyncio
async def test_a_failing_context_source_does_not_end_the_turn(patched, monkeypatch):
    async def boom(symbol):
        raise RuntimeError("provider down")

    monkeypatch.setattr(chat_graph.market_data, "get_profile", boom)
    use_provider(monkeypatch, FakeProvider([reply("ENOUGH"), reply("Still answered.")]))

    state = await run()

    assert state["answer"] == "Still answered."
    assert state["signal_set"] is not None


@pytest.mark.asyncio
async def test_the_hovered_candle_reaches_the_model(patched, monkeypatch):
    """The one piece of screen state the backend cannot reconstruct."""
    provider = use_provider(monkeypatch, FakeProvider([reply("ENOUGH"), reply("ok")]))
    screen = SCREEN.model_copy(update={
        "hovered_bar": HoveredBar(time="2026-08-01", open=1.0, high=2.0,
                                  low=0.5, close=1.5, volume=100)
    })

    await run(screen=screen)

    assert "hovered_candle" in json.dumps(provider.calls[0]["messages"])


# ── The tool loop ───────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_requested_tool_runs_and_its_result_is_fed_back(patched, monkeypatch):
    provider = use_provider(monkeypatch, FakeProvider([
        reply(tool_calls=[ToolCall(id="c1", name="get_recent_news",
                                   arguments={"ticker": "AAPL"})]),
        reply("ENOUGH"),
        reply("News explains it."),
    ]))

    state = await run()

    assert patched.executed == [("get_recent_news", {"ticker": "AAPL"})]
    assert [r["name"] for r in state["tool_records"]] == ["get_recent_news"]
    # The result was in front of the model when it wrote the answer.
    assert "get_recent_news" in json.dumps(provider.streamed or provider.calls[-1]["messages"])


@pytest.mark.asyncio
async def test_parallel_tool_calls_all_run(patched, monkeypatch):
    use_provider(monkeypatch, FakeProvider([
        reply(tool_calls=[
            ToolCall(id="c1", name="compute_rsi", arguments={"ticker": "AAPL"}),
            ToolCall(id="c2", name="get_recent_news", arguments={"ticker": "AAPL"}),
        ]),
        reply("ENOUGH"),
        reply("Answer."),
    ]))

    state = await run()

    assert {r["name"] for r in state["tool_records"]} == {"compute_rsi", "get_recent_news"}


@pytest.mark.asyncio
async def test_a_model_that_asks_for_tools_forever_is_capped(patched, monkeypatch):
    """Without the cap this is an unbounded spend, not just a slow reply.

    Arguments differ each round so the duplicate check cannot end the loop —
    the iteration cap is what has to.
    """
    always = [
        reply(tool_calls=[ToolCall(id=f"c{i}", name="compute_rsi",
                                   arguments={"period": i})])
        for i in range(20)
    ]
    use_provider(monkeypatch, FakeProvider(always + [reply("Forced answer.")]))

    state = await run()

    assert state["tool_iterations"] == 3
    assert state["answer"]


@pytest.mark.asyncio
async def test_too_many_calls_in_one_iteration_are_trimmed(patched, monkeypatch):
    use_provider(monkeypatch, FakeProvider([
        reply(tool_calls=[
            ToolCall(id=f"c{i}", name="compute_rsi", arguments={"i": i})
            for i in range(9)
        ]),
        reply("ENOUGH"),
        reply("Answer."),
    ]))

    state = await run()

    assert len(state["tool_records"]) == 4


@pytest.mark.asyncio
async def test_re_requesting_the_same_call_ends_the_loop(patched, monkeypatch):
    """A model that reads an empty result often asks for it again, verbatim.

    Left alone that burns every iteration and grows the prompt each time
    without adding a fact.
    """
    same = ToolCall(id="c1", name="get_recent_news", arguments={"ticker": "AAPL"})
    use_provider(monkeypatch, FakeProvider([
        reply(tool_calls=[same]),
        reply(tool_calls=[ToolCall(id="c2", name="get_recent_news",
                                   arguments={"ticker": "AAPL"})]),
        reply("Answered."),
    ]))

    state = await run()

    assert len(patched.executed) == 1
    assert state["tool_iterations"] == 1


@pytest.mark.asyncio
async def test_argument_order_does_not_defeat_the_duplicate_check(patched, monkeypatch):
    use_provider(monkeypatch, FakeProvider([
        reply(tool_calls=[ToolCall(id="c1", name="compute_rsi",
                                   arguments={"ticker": "AAPL", "period": 14})]),
        reply(tool_calls=[ToolCall(id="c2", name="compute_rsi",
                                   arguments={"period": 14, "ticker": "AAPL"})]),
        reply("Answered."),
    ]))

    await run()

    assert len(patched.executed) == 1


@pytest.mark.asyncio
async def test_a_genuinely_new_call_still_runs(patched, monkeypatch):
    use_provider(monkeypatch, FakeProvider([
        reply(tool_calls=[ToolCall(id="c1", name="get_recent_news",
                                   arguments={"ticker": "AAPL"})]),
        reply(tool_calls=[ToolCall(id="c2", name="get_earnings_calendar",
                                   arguments={"ticker": "AAPL"})]),
        reply("ENOUGH"),
        reply("Answered."),
    ]))

    await run()

    assert [name for name, _ in patched.executed] == [
        "get_recent_news", "get_earnings_calendar",
    ]


@pytest.mark.asyncio
async def test_a_failing_tool_is_reported_to_the_model_not_raised(patched, monkeypatch):
    async def failing(name, arguments):
        return ToolResult(success=False, data=None, error="rate limited")

    patched.execute = failing
    provider = use_provider(monkeypatch, FakeProvider([
        reply(tool_calls=[ToolCall(id="c1", name="get_recent_news", arguments={})]),
        reply("ENOUGH"),
        reply("Answered without news."),
    ]))

    state = await run()

    assert state["tool_records"][0]["success"] is False
    assert "rate limited" in json.dumps(provider.calls[-1]["messages"])


@pytest.mark.asyncio
async def test_no_tools_are_offered_when_the_provider_cannot_use_them(patched, monkeypatch):
    """Local Qwens: the loop is skipped and the pre-fetched context answers."""
    provider = use_provider(
        monkeypatch, FakeProvider([reply("Answer from context.")], supports_tools=False)
    )

    state = await run()

    assert state["answer"] == "Answer from context."
    assert all(call["tools"] in (None, []) for call in provider.calls)
    assert patched.executed == []


@pytest.mark.asyncio
async def test_a_failed_gather_call_still_produces_an_answer(patched, monkeypatch):
    class FlakyGather(FakeProvider):
        async def complete(self, messages, model, **kwargs):
            if kwargs.get("tools"):
                raise RuntimeError("gather exploded")
            return reply("Answered from context alone.")

    use_provider(monkeypatch, FlakyGather([]))

    state = await run()

    assert state["answer"] == "Answered from context alone."


# ── Verification ────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_signal_citations_are_stripped_from_the_prose(patched, monkeypatch):
    use_provider(monkeypatch, FakeProvider([
        reply("ENOUGH"),
        reply("RSI is 22, which is oversold.\nsignals: rsi_oversold"),
    ]))

    state = await run()

    assert state["answer"] == "RSI is 22, which is oversold."
    assert state["signals_cited"] == ["rsi_oversold"]


@pytest.mark.asyncio
async def test_an_invented_signal_id_is_flagged_but_not_cited(patched, monkeypatch):
    use_provider(monkeypatch, FakeProvider([
        reply("ENOUGH"),
        reply("Looks fine.\nsignals: rsi_oversold, moon_phase_bullish"),
    ]))

    state = await run()

    assert state["signals_cited"] == ["rsi_oversold"]
    assert any(f.flag_type == "missing_citation" for f in state["flags"])
    assert not state["blocked"]


@pytest.mark.asyncio
async def test_direct_advice_is_blocked_and_replaced(patched, monkeypatch):
    use_provider(monkeypatch, FakeProvider([
        reply("ENOUGH"),
        reply("You should buy AAPL right now.\nsignals: rsi_oversold"),
    ]))

    state = await run()

    assert state["blocked"] is True
    assert "you should buy" not in state["answer"].lower()
    assert any(f.flag_type == "investment_advice" and f.severity == "error"
               for f in state["flags"])


@pytest.mark.asyncio
async def test_capitulation_is_blocked_too(patched, monkeypatch):
    use_provider(monkeypatch, FakeProvider([
        reply("ENOUGH"),
        reply("Honestly, I'd be buying here.\nsignals: rsi_oversold"),
    ]))

    assert (await run())["blocked"] is True


@pytest.mark.asyncio
async def test_an_invented_figure_is_blocked(patched, monkeypatch):
    """RSI is 22 in the readings. A reply claiming 41 must not reach anyone."""
    use_provider(monkeypatch, FakeProvider([
        reply("ENOUGH"),
        reply("RSI sits at 41, comfortably neutral.\nsignals: rsi_oversold"),
    ]))

    state = await run()

    assert state["blocked"] is True
    assert any(f.flag_type == "hallucination" for f in state["flags"])


@pytest.mark.asyncio
async def test_a_correctly_quoted_figure_is_not_blocked(patched, monkeypatch):
    use_provider(monkeypatch, FakeProvider([
        reply("ENOUGH"),
        reply("RSI is 22.0 and price is 7.7% below the 200-day at 195.0."
              "\nsignals: rsi_oversold"),
    ]))

    state = await run()

    assert state["blocked"] is False, state["flags"]


@pytest.mark.asyncio
async def test_a_blocked_reply_still_answers_the_question(patched, monkeypatch):
    """A stonewall teaches the user to rephrase until something slips through."""
    use_provider(monkeypatch, FakeProvider([
        reply("ENOUGH"),
        reply("You should buy AAPL.\nsignals: rsi_oversold"),
    ]))

    state = await run()

    assert "RSI oversold" in state["answer"]
    assert "closes back above 30" in state["answer"]


@pytest.mark.asyncio
async def test_an_empty_model_reply_falls_back_to_the_signal_summary(patched, monkeypatch):
    use_provider(monkeypatch, FakeProvider([reply("ENOUGH"), reply("")]))

    state = await run()

    assert state["blocked"] is True
    assert "RSI oversold" in state["answer"]


@pytest.mark.asyncio
async def test_a_provider_that_dies_mid_turn_still_returns_something(patched, monkeypatch):
    class Dead(FakeProvider):
        async def complete(self, *args, **kwargs):
            raise RuntimeError("provider down")

    use_provider(monkeypatch, Dead([]))

    state = await run()

    assert state["answer"]
    assert state["blocked"] is True


# ── Streaming ───────────────────────────────────────────────────────────────


async def collect(**kwargs):
    events = []
    async for event, payload in chat_graph.stream_chat_turn(
        message=kwargs.pop("message", "what is happening?"),
        screen=kwargs.pop("screen", SCREEN),
        conversation_id="conv-1",
        **kwargs,
    ):
        events.append((event, payload))
    return events


@pytest.mark.asyncio
async def test_the_signal_set_is_emitted_before_any_token(patched, monkeypatch):
    """The whole reason for a separate context event: chips render while it thinks."""
    use_provider(monkeypatch, FakeProvider([reply("ENOUGH"), reply("RSI is 22.")]))

    events = [name for name, _ in await collect()]

    assert events.index("context") < events.index("token")
    assert events[0] == "conversation"
    assert events[-1] == "done"


@pytest.mark.asyncio
async def test_tool_activity_is_surfaced_as_it_happens(patched, monkeypatch):
    use_provider(monkeypatch, FakeProvider([
        reply(tool_calls=[ToolCall(id="c1", name="get_recent_news", arguments={})]),
        reply("ENOUGH"),
        reply("Answer."),
    ]))

    events = await collect()
    names = [name for name, _ in events]

    assert names.index("tool_call") < names.index("tool_result") < names.index("token")


@pytest.mark.asyncio
async def test_the_streamed_answer_is_verified_too(patched, monkeypatch):
    """Tokens are emitted before the reply can be judged, so `done` must correct it."""
    use_provider(monkeypatch, FakeProvider([
        reply("ENOUGH"),
        reply("You should buy AAPL now."),
    ]))

    events = await collect()
    by_name = dict(events)

    assert by_name["done"]["replaced"] is True
    assert "you should buy" not in by_name["done"]["content"].lower()
    assert any(name == "flag" for name, _ in events)


@pytest.mark.asyncio
async def test_a_clean_streamed_reply_is_not_marked_replaced(patched, monkeypatch):
    use_provider(monkeypatch, FakeProvider([reply("ENOUGH"), reply("RSI is 22.0 today.")]))

    events = dict(await collect())

    assert events["done"]["replaced"] is False
    assert events["done"]["content"] == "RSI is 22.0 today."


@pytest.mark.asyncio
async def test_streaming_reports_an_error_rather_than_hanging(patched, monkeypatch):
    class Dead(FakeProvider):
        async def complete(self, *args, **kwargs):
            raise RuntimeError("down")

        async def stream(self, *args, **kwargs):
            raise RuntimeError("down")
            yield  # pragma: no cover

    use_provider(monkeypatch, Dead([]))

    events = dict(await collect())

    assert "error" in events


@pytest.mark.asyncio
async def test_history_is_replayed_into_the_turn(patched, monkeypatch):
    provider = use_provider(monkeypatch, FakeProvider([reply("ENOUGH"), reply("Yes.")]))
    history = [{"role": "user", "content": "is it oversold?"},
               {"role": "assistant", "content": "RSI is 22."}]

    await run(history=history)

    sent = json.dumps(provider.calls[0]["messages"])
    assert "is it oversold?" in sent
