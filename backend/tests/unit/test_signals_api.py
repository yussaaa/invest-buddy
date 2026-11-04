"""HTTP contract for /signals — services patched, no network, no database."""

from __future__ import annotations

from typing import AsyncIterator

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.api.v1 import signals as signals_api
from app.services import cache, screens, signals_page
from app.services.social import apewisdom
from app.services.social.models import RedditAttention, TrendingResult, TrendingRow
from tests.unit.test_cache_tiers import FakeRedis


@pytest_asyncio.fixture
async def client(monkeypatch) -> AsyncIterator[AsyncClient]:
    monkeypatch.setattr(cache, "redis_client", FakeRedis())
    cache._CACHE.clear()

    async def no_history(symbols):
        return {}

    async def no_record(rows, source="apewisdom"):
        return 0

    monkeypatch.setattr(signals_page, "_load_history", no_history)
    monkeypatch.setattr(signals_page, "record_board", no_record)

    app = FastAPI()
    app.include_router(signals_api.router, prefix="/signals")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c
    cache._CACHE.clear()


async def test_sources_lists_live_and_placeholder_providers(client):
    body = (await client.get("/signals/sources")).json()
    states = {s["id"]: s["kind"] for s in body["sources"]}
    assert states == {"apewisdom": "live", "stocktwits": "live",
                      "reddit": "placeholder", "x": "placeholder"}
    assert {f["id"] for f in body["filters"]} >= {"all-stocks", "wallstreetbets"}
    assert body["disclaimer"]


async def test_trending_rows_carry_badges(client, monkeypatch):
    from datetime import datetime, timezone

    async def fake_trending(filter="all-stocks", limit=50):
        return TrendingResult(source="apewisdom", filter=filter, as_of=datetime.now(timezone.utc),
                              rows=[TrendingRow(symbol="AMD", name="AMD", rank=1, rank_24h_ago=6,
                                                rank_change=5, mentions=189, mentions_24h_ago=50)])

    monkeypatch.setattr(apewisdom, "trending", fake_trending)
    body = (await client.get("/signals/trending?limit=5")).json()
    assert body["available"] and body["rows"][0]["signal_ids"] == ["mention_surge_24h"]


async def test_a_placeholder_source_answers_with_a_caveat(client):
    body = (await client.get("/signals/trending?source=x")).json()
    assert body["available"] is False and body["rows"] == [] and body["caveat"]


async def test_an_unknown_source_is_an_error_not_a_500(client):
    response = await client.get("/signals/trending?source=myspace")
    assert response.status_code == 200 and "unknown source" in response.json()["error"]


async def test_screeners_lists_presets(client):
    body = (await client.get("/signals/screeners")).json()
    assert {p["id"] for p in body["presets"]} == set(screens.PRESETS)


async def test_ticker_snapshot_shape(client, monkeypatch):
    async def reddit(symbol):
        return RedditAttention(source="apewisdom", symbol=symbol, rank=4, rank_24h_ago=40,
                               rank_change=36, mentions=120, mentions_24h_ago=30)

    async def blocked(symbol):
        from app.services.social.models import CrowdTags
        return CrowdTags(source="stocktwits", symbol=symbol, available=False, caveat="blocked")

    async def profile(symbol):
        return {"symbol": symbol, "name": "Palantir", "short_percent_float": 2.6,
                "shares_short": 90, "shares_short_prior_month": 100,
                "market_cap": 400_000_000_000, "avg_volume": 60_000_000, "currency": "USD"}

    async def technicals(symbol):
        return {"symbol": symbol, "rsi": {"error": "x"}, "macd": {"error": "x"},
                "moving_averages": {"error": "x"}, "drawdown": {"error": "x"}}

    monkeypatch.setattr(signals_page.apewisdom, "ticker", reddit)
    monkeypatch.setattr(signals_page.stocktwits, "ticker", blocked)
    monkeypatch.setattr(signals_page.market_data, "get_profile", profile)
    monkeypatch.setattr(signals_page.technicals_service, "get_technicals", technicals)

    body = (await client.get("/signals/ticker?symbol=pltr")).json()

    assert body["symbol"] == "PLTR" and body["name"] == "Palantir"
    assert body["stocktwits"]["available"] is False
    assert body["positioning"]["short_change_pct"] == -10.0
    social = {s["id"] for s in body["social_signal_set"]["signals"]}
    assert {"rank_climb", "mention_surge_24h"} <= social
    assert body["social_signal_set"]["coverage"]["stocktwits"] is False
    assert body["technical_signal_set"]["ticker"] == "PLTR"
    assert body["market"] == {"market_cap": 400_000_000_000, "avg_volume": 60_000_000, "currency": "USD"}


async def test_ticker_rejects_nonsense(client):
    assert "error" in (await client.get("/signals/ticker?symbol=" + "X" * 40)).json()


async def test_ticker_vol_reads_the_shared_option_chain(client, monkeypatch):
    from app.services import options

    seen = []

    async def chain(symbol, horizon="both"):
        seen.append((symbol, horizon))
        return {"symbol": symbol, "expiries": [{}], "volatility": {
            "atm_iv_30d": 0.52, "hv_20": 0.40, "iv_hv_ratio": 1.3, "iv_rank": None,
            "iv_rank_available": False, "method": "realized_proxy", "note": "proxy"}}

    monkeypatch.setattr(options, "get_options_chain", chain)
    body = (await client.get("/signals/ticker/vol?symbol=pltr")).json()

    # Same cache key as the options panel and the options specialist.
    assert seen == [("PLTR", "both")]
    assert body["available"] is True and body["reason"] is None
    assert body["atm_iv_30d"] == 0.52 and body["iv_hv_ratio"] == 1.3


async def test_ticker_vol_without_options_says_why(client, monkeypatch):
    from app.services import options

    async def no_iv(symbol, horizon="both"):
        return {"symbol": symbol, "expiries": [], "volatility": {"atm_iv_30d": None, "note": "No listed options."}}

    async def failed(symbol, horizon="both"):
        return {"symbol": symbol, "expiries": [], "contracts": [], "error": "boom"}

    monkeypatch.setattr(options, "get_options_chain", no_iv)
    body = (await client.get("/signals/ticker/vol?symbol=XYZ")).json()
    assert body["available"] is False and body["reason"] == "No listed options."

    monkeypatch.setattr(options, "get_options_chain", failed)
    body = (await client.get("/signals/ticker/vol?symbol=XYZ")).json()
    assert body["available"] is False and "boom" not in body["reason"]

    assert (await client.get("/signals/ticker/vol?symbol=" + "X" * 40)).json()["available"] is False


async def test_explain_without_credentials_says_why(client, monkeypatch):
    monkeypatch.setattr(signals_page, "has_llm_credentials", lambda settings: False)
    body = (await client.get("/signals/ticker/explain?symbol=PLTR")).json()
    assert body["available"] is False and body["reason"]


def test_the_templated_explanation_grounds_and_stays_clean():
    snapshot = {
        "symbol": "PLTR", "name": "Palantir",
        "reddit": {"available": True, "rank": 4, "mentions": 120, "mentions_24h_ago": 30},
        "stocktwits": {"available": True, "bullish": 18, "bearish": 2, "bull_pct": 90.0},
        "positioning": {"short_percent_float": 2.6},
        "social_signal_set": {"signals": [{
            "id": "mention_surge_24h", "label": "Mentions surging", "direction": "neutral",
            "rationale": "PLTR has 120 Reddit mentions against 30 a day earlier, 4.0 times as many.",
            "evidence": {"mentions": 120, "mentions_24h_ago": 30, "multiple": 4.0}}]},
    }
    b = signals_page.brief(snapshot)
    text = signals_page.templated_explanation(b)
    assert "120" in text and "90.0%" in text
    assert signals_page.review(text, b) == []


def test_a_model_reply_with_advice_or_invented_numbers_is_refused():
    b = signals_page.brief({"symbol": "PLTR", "reddit": {"rank": 4, "mentions": 120}})
    assert signals_page.review("Mentions are up; time to buy PLTR.", b)
    assert signals_page.review("PLTR has 4,512 mentions today.", b)


async def test_research_is_refused_when_disabled(client, monkeypatch):
    from app.config import get_settings

    monkeypatch.setattr(get_settings(), "research_enabled", False)
    response = await client.post("/signals/research", json={"ticker": "PLTR"})
    assert response.status_code == 503 and "RESEARCH_ENABLED" in response.json()["detail"]


async def test_research_stream_frames_events(client, monkeypatch):
    async def fake_stream(ticker, question=None):
        yield "start", {"ticker": ticker, "cached": False}
        yield "token", {"text": "hi"}
        yield "done", {"brief": {"ticker": ticker}, "replaced": False}

    monkeypatch.setattr(signals_api, "stream_research", fake_stream)
    monkeypatch.setattr(signals_api, "has_llm_credentials", lambda settings: True)
    response = await client.post("/signals/research/stream", json={"ticker": "PLTR"})
    body = response.text
    assert response.status_code == 200
    assert body.index("event: start") < body.index("event: token") < body.index("event: done")


async def test_research_validates_the_ticker(client, monkeypatch):
    monkeypatch.setattr(signals_api, "has_llm_credentials", lambda settings: True)
    response = await client.post("/signals/research", json={"ticker": ""})
    assert response.status_code == 422
