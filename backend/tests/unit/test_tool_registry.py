"""Tool contracts and registry behaviour, with no network.

The live versions of these checks are tests/integration/test_tools_live.py;
they catch provider shape changes and stay out of the merge gate. These pin the
same contracts — what each tool returns, how the registry executes and caches —
against a fake `yfinance.Ticker`, so `make test` means what it says.
"""

from __future__ import annotations

import math

import numpy as np
import pandas as pd
import pytest

from app.tools.registry import ToolRegistry


def _synthetic_history(sessions: int = 260, seed: int = 7) -> pd.DataFrame:
    """A year of plausible daily OHLCV: a drift, a wave, and seeded noise."""
    rng = np.random.default_rng(seed)
    idx = pd.bdate_range(end="2026-10-30", periods=sessions)
    t = np.arange(sessions)
    close = 150 + 0.08 * t + 6 * np.sin(t / 15) + rng.normal(0, 1.2, sessions)
    return pd.DataFrame(
        {
            "Open": close - 0.4,
            "High": close + 1.1,
            "Low": close - 1.3,
            "Close": close,
            "Volume": rng.integers(2_000_000, 6_000_000, sessions),
        },
        index=idx,
    )


INFO = {
    "longName": "Fake Corp",
    "sector": "Technology",
    "industry": "Software",
    "longBusinessSummary": "Makes things.",
    "marketCap": 1_000_000_000,
    "fiftyTwoWeekHigh": 190.0,
    "fiftyTwoWeekLow": 140.0,
    "currentPrice": 171.5,
}


class FakeTicker:
    """Stands in for yfinance.Ticker; counts constructions so caching is checkable."""

    created: list[str] = []

    def __init__(self, symbol: str):
        FakeTicker.created.append(symbol)
        self.symbol = symbol
        self.info = dict(INFO)

    def history(self, period: str = "1y", **_):
        return _synthetic_history()


@pytest.fixture(autouse=True)
def fake_yahoo(monkeypatch):
    FakeTicker.created = []
    monkeypatch.setattr("yfinance.Ticker", FakeTicker)
    return FakeTicker


# ── tool contracts ──────────────────────────────────────────────────────────


async def test_get_company_overview_shape():
    from app.tools.market_data.yfinance_tool import get_company_overview

    result = await get_company_overview("aapl")
    assert result["ticker"] == "AAPL"
    assert result["name"] == "Fake Corp"
    assert result["sector"] == "Technology"
    assert result["current_price"] == pytest.approx(171.5)


async def test_compute_rsi_is_bounded_and_zoned():
    from app.tools.calculation.technical_indicators import compute_rsi

    result = await compute_rsi("AAPL")
    assert 0 <= result["current_rsi"] <= 100
    assert result["zone"] in ("overbought", "oversold", "neutral")
    assert len(result["historical"]) == 20


async def test_compute_rsi_uses_supplied_closes_without_fetching(fake_yahoo):
    from app.tools.calculation.technical_indicators import compute_rsi

    await compute_rsi("AAPL", closes=_synthetic_history()["Close"])
    assert fake_yahoo.created == []


async def test_compute_macd_shape():
    from app.tools.calculation.technical_indicators import compute_macd

    result = await compute_macd("AAPL")
    assert {"macd", "signal", "histogram", "trend"} <= result.keys()
    assert result["trend"] in ("bullish", "bearish")
    assert not (result["bullish_crossover"] and result["bearish_crossover"])


async def test_compute_bollinger_bands_are_ordered():
    from app.tools.calculation.technical_indicators import compute_bollinger_bands

    result = await compute_bollinger_bands("AAPL")
    assert result["upper_band"] > result["middle_band"] > result["lower_band"]
    assert result["bandwidth"] is not None and result["bandwidth"] > 0


async def test_indicators_ignore_a_trailing_stub_row(monkeypatch):
    """A session that hasn't printed arrives as a NaN close; it must not null the reading."""
    from app.tools.calculation.technical_indicators import compute_rsi

    frame = _synthetic_history()
    frame.loc[frame.index[-1] + pd.offsets.BDay(1)] = [math.nan] * 5

    class StubTicker(FakeTicker):
        def history(self, period: str = "1y", **_):
            return frame

    monkeypatch.setattr("yfinance.Ticker", StubTicker)
    result = await compute_rsi("AAPL")
    assert result["current_rsi"] is not None


# ── registry ────────────────────────────────────────────────────────────────


async def test_registry_executes_a_tool():
    result = await ToolRegistry.get().execute("get_company_overview", {"ticker": "REGX"})
    assert result.success
    assert result.data["ticker"] == "REGX"
    assert result.cache_hit is False


async def test_registry_caches_the_second_call(fake_yahoo):
    registry = ToolRegistry.get()
    first = await registry.execute("get_company_overview", {"ticker": "CACHEX"})
    second = await registry.execute("get_company_overview", {"ticker": "CACHEX"})
    assert first.success and second.success
    assert second.cache_hit is True
    assert fake_yahoo.created.count("CACHEX") == 1


async def test_registry_rejects_an_unknown_tool():
    result = await ToolRegistry.get().execute("nonexistent_tool", {})
    assert not result.success
    assert "Unknown tool" in result.error



def test_options_chain_is_filed_with_the_options_tools():
    registry = ToolRegistry.get()
    categories = {name: registry._tools[name].category
                  for name in ("get_options_chain", "get_options_snapshot")}
    assert categories == {"get_options_chain": "options", "get_options_snapshot": "options"}


# ── Social tools ────────────────────────────────────────────────────────────


def test_the_social_tools_are_registered_with_valid_schemas():
    registry = ToolRegistry.get()
    schemas = {s["function"]["name"]: s["function"]
               for s in registry.get_schema_for_llm(
                   ["get_social_sentiment", "get_trending_tickers", "get_stock_screen"])}

    assert set(schemas) == {"get_social_sentiment", "get_trending_tickers", "get_stock_screen"}
    assert schemas["get_social_sentiment"]["parameters"]["required"] == ["ticker"]
    presets = schemas["get_stock_screen"]["parameters"]["properties"]["preset"]["enum"]
    assert "most_actives" in presets and "most_shorted_stocks" in presets


async def test_get_social_sentiment_condenses_the_snapshot(monkeypatch):
    from app.services import signals_page
    from app.tools.social import social_tool

    async def snapshot(symbol):
        return {
            "symbol": "PLTR", "disclaimer": "d",
            "reddit": {"available": True, "rank": 4, "mentions": 120, "history": [1, 2, 3],
                       "url": "https://x", "caveat": None},
            "stocktwits": {"available": False, "caveat": "blocked"},
            "positioning": {"short_percent_float": 2.6},
            "social_signal_set": {"signals": [{"id": "rank_climb", "label": "Climbing",
                                               "direction": "neutral", "rationale": "r",
                                               "evidence": {"rank": 4}}],
                                  "bias_phrase": "Inconclusive", "coverage": {}},
            "technical_signal_set": {"signals": ["large"]},
        }

    monkeypatch.setattr(signals_page, "get_ticker_snapshot", snapshot)
    result = await social_tool.get_social_sentiment("pltr")

    assert result["reddit"]["rank"] == 4 and "history" not in result["reddit"]
    assert "technical_signal_set" not in result
    assert result["caveats"] == ["blocked"]
    assert result["conditions"][0]["id"] == "rank_climb"


@pytest.mark.parametrize("rank, before, expected", [
    (6, 14, "climbed from rank 14 to rank 6"),
    (14, 6, "dropped from rank 6 to rank 14"),
    (3, None, "new to the board at rank 3"),
    (5, 5, "unchanged at rank 5"),
])
def test_rank_movement_says_the_direction_in_words(rank, before, expected):
    from app.tools.social.social_tool import rank_movement

    assert rank_movement(rank, before).startswith(expected)
