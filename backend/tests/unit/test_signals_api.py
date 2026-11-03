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
                "shares_short": 90, "shares_short_prior_month": 100}

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


async def test_ticker_rejects_nonsense(client):
    assert "error" in (await client.get("/signals/ticker?symbol=" + "X" * 40)).json()


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
