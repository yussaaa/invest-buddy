"""A profile built from a lost quoteSummary is served but never cached.

yfinance's `.info` is two requests. When quoteSummary fails it returns the v7
quote half alone and raises nothing — that shape below is what sat in Redis
with beta, average volume and day range blank on every symbol.
"""

from __future__ import annotations

import pytest

from app.services import cache, market_data
from tests.unit.test_cache_tiers import FakeRedis

# Only what the v7 quote carries.
V7_ONLY = {
    "longName": "Apple Inc.",
    "fullExchangeName": "NasdaqGS",
    "currency": "USD",
    "marketCap": 4_843_808_227_328,
    "trailingPE": 36.96,
    "forwardPE": 31.17,
    "fiftyTwoWeekLow": 243.42,
    "fiftyTwoWeekHigh": 351.73,
    "regularMarketDayLow": 325.33,
    "regularMarketDayHigh": 334.5,
    "averageDailyVolume3Month": 41_614_187,
}

FULL = {
    **V7_ONLY,
    "sector": "Technology",
    "industry": "Consumer Electronics",
    "beta": 1.089,
    "dayLow": 325.33,
    "dayHigh": 334.5,
    "averageVolume": 41_614_187,
}


@pytest.fixture
def yahoo(monkeypatch):
    fake = FakeRedis()
    monkeypatch.setattr(cache, "redis_client", fake)
    cache._CACHE.clear()
    cache._locks.clear()

    class Ticker:
        responses: list[dict] = []
        calls = 0

        def __init__(self, symbol):
            pass

        @property
        def info(self):
            Ticker.calls += 1
            return Ticker.responses.pop(0)

    monkeypatch.setattr(market_data.yf, "Ticker", Ticker)
    return Ticker


async def test_v7_fallbacks_fill_day_range_and_average_volume(yahoo):
    yahoo.responses = [dict(V7_ONLY)]
    profile = await market_data.get_profile("AAPL")

    assert profile["partial"] is True
    assert profile["avg_volume"] == 41_614_187
    assert (profile["day_low"], profile["day_high"]) == (325.33, 334.5)
    # Only quoteSummary has these; nothing to fall back to.
    assert profile["beta"] is None and profile["sector"] is None


async def test_partial_is_not_cached_so_the_next_request_heals(yahoo):
    yahoo.responses = [dict(V7_ONLY), dict(FULL)]

    first = await market_data.get_profile("AAPL")
    second = await market_data.get_profile("AAPL")

    assert first["beta"] is None
    assert second["beta"] == 1.09 and second["partial"] is False
    assert yahoo.calls == 2


async def test_full_profile_is_cached(yahoo):
    yahoo.responses = [dict(FULL)]

    await market_data.get_profile("AAPL")
    again = await market_data.get_profile("AAPL")

    assert again["sector"] == "Technology"
    assert yahoo.calls == 1


async def test_etf_without_beta_or_sector_still_counts_as_full(yahoo):
    # Funds have summaryDetail (dayLow, averageVolume) but no assetProfile.
    etf = {**V7_ONLY, "dayLow": 500.1, "dayHigh": 505.2, "averageVolume": 70_000_000}
    yahoo.responses = [etf]

    profile = await market_data.get_profile("SPY")
    assert profile["partial"] is False
