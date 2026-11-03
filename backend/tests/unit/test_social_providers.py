"""Social providers against recorded payloads — no network, ever.

Every request goes through `httpx.MockTransport` (social/http.py's injectable
transport), so these tests also count how many requests a failure costs.
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from app.config import get_settings
from app.services import cache
from app.services.social import apewisdom, http, statuses, stocktwits
from app.services.social.placeholders import RedditApiProvider, XProvider
from tests.unit.test_cache_tiers import FakeRedis

FIXTURES = Path(__file__).parent / "fixtures" / "social"


def fixture(name: str) -> str:
    return (FIXTURES / name).read_text()


@pytest.fixture(autouse=True)
def isolated(monkeypatch):
    monkeypatch.setattr(cache, "redis_client", FakeRedis())
    cache._CACHE.clear()
    yield
    cache._CACHE.clear()
    http.use_transport(None)


class Recorder:
    """A MockTransport handler that answers from a routing function and counts."""

    def __init__(self, route):
        self.route = route
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return self.route(request)


def serve(route) -> Recorder:
    recorder = Recorder(route)
    http.use_transport(httpx.MockTransport(recorder))
    return recorder


def json_response(body: str, status: int = 200) -> httpx.Response:
    return httpx.Response(status, text=body, headers={"content-type": "application/json"})


def cloudflare(_request) -> httpx.Response:
    return httpx.Response(403, text=fixture("cloudflare_403.html"),
                          headers={"content-type": "text/html; charset=UTF-8"})


# ── ApeWisdom ───────────────────────────────────────────────────────────────


async def test_apewisdom_rows_are_normalised():
    serve(lambda r: json_response(fixture("apewisdom_page1.json")))

    result = await apewisdom.trending("all-stocks", 50)

    assert result.available
    by_symbol = {row.symbol: row for row in result.rows}
    amd = by_symbol["AMD"]
    assert (amd.rank, amd.rank_24h_ago, amd.rank_change) == (1, 6, 5)
    assert amd.mention_change_pct == round((189 - 72) / 72 * 100, 1)


async def test_apewisdom_numbers_as_strings_parse():
    serve(lambda r: json_response(fixture("apewisdom_page1.json")))
    gme = {r.symbol: r for r in (await apewisdom.trending()).rows}["GME"]
    assert (gme.rank, gme.mentions, gme.upvotes, gme.rank_change) == (13, 40, 512, 47)


async def test_a_new_entry_has_no_change_rather_than_a_fake_one():
    serve(lambda r: json_response(fixture("apewisdom_page1.json")))
    newco = {r.symbol: r for r in (await apewisdom.trending()).rows}["NEWCO"]
    assert newco.rank_change is None and newco.mention_change_pct is None
    assert newco.name == "New Co & Sons"           # HTML entities unescaped


async def test_unusable_rows_are_dropped():
    serve(lambda r: json_response(fixture("apewisdom_page1.json")))
    assert "BAD" not in {r.symbol for r in (await apewisdom.trending()).rows}


async def test_word_tickers_carry_a_caveat():
    serve(lambda r: json_response(fixture("apewisdom_page1.json")))
    result = await apewisdom.trending()
    assert result.caveat and "IT" in result.caveat


async def test_an_unknown_filter_never_reaches_the_network():
    recorder = serve(lambda r: json_response("{}"))
    result = await apewisdom.trending("../../admin", 10)
    assert not result.available and recorder.requests == []


async def test_a_ticker_off_the_board_is_not_zero_mentions():
    serve(lambda r: json_response(fixture("apewisdom_page1.json")))
    reading = await apewisdom.ticker("ZZZZ")
    assert reading.available and reading.rank is None and reading.mentions is None
    assert "not none" in reading.caveat


async def test_ticker_lookup_finds_the_row():
    serve(lambda r: json_response(fixture("apewisdom_page1.json")))
    reading = await apewisdom.ticker("pltr")
    assert reading.symbol == "PLTR" and reading.rank is not None


async def test_apewisdom_failure_is_a_caveat_not_an_exception():
    serve(lambda r: httpx.Response(500, text="boom"))
    result = await apewisdom.trending()
    assert not result.available and "unavailable" in result.caveat


# ── StockTwits ──────────────────────────────────────────────────────────────


async def test_stocktwits_counts_tags_dedupes_and_follows_the_cursor():
    stream = json.loads(fixture("stocktwits_stream.json"))
    older = {"messages": [{"id": 5000 + i, "created_at": "2026-11-03T08:00:00Z",
                           "entities": {"sentiment": {"basic": "Bearish"}}} for i in range(2)]}

    def route(request):
        if "max" in request.url.params:
            return json_response(json.dumps(older))
        return json_response(json.dumps(stream))

    recorder = serve(route)
    reading = await stocktwits.ticker("PLTR")

    assert len(recorder.requests) == 2
    assert (reading.bullish, reading.bearish, reading.untagged) == (18, 6, 8)
    assert reading.sampled == 32
    assert reading.bull_pct == 75.0
    assert reading.watchlist_count == 325960
    assert reading.window_hours == 7.0      # 15:00 back to 08:00


async def test_stocktwits_never_carries_message_bodies():
    serve(lambda r: json_response(fixture("stocktwits_stream.json")))
    reading = await stocktwits.ticker("PLTR")
    dumped = json.dumps(reading.model_dump(mode="json"))
    assert "message body" not in dumped
    assert "message body" not in json.dumps(cache._CACHE, default=str)


async def test_a_cloudflare_block_costs_one_request_and_trips_the_breaker():
    recorder = serve(cloudflare)

    first = await stocktwits.ticker("PLTR")
    second = await stocktwits.ticker("AMD")

    assert not first.available and "Cloudflare" in first.caveat
    assert not second.available
    # One request in total: no retry on the 403, and the breaker answered AMD.
    assert len(recorder.requests) == 1
    assert (await stocktwits.StockTwitsProvider().status()).state == "blocked"


async def test_a_rate_limit_also_trips_the_breaker():
    recorder = serve(lambda r: httpx.Response(429, text="slow down"))
    assert not (await stocktwits.ticker("PLTR")).available
    assert await stocktwits.is_blocked()
    assert len(recorder.requests) == 1


async def test_a_challenge_page_with_200_is_not_parsed_as_data():
    serve(lambda r: httpx.Response(200, text=fixture("cloudflare_403.html"),
                                   headers={"content-type": "text/html"}))
    assert not (await stocktwits.ticker("PLTR")).available


async def test_stocktwits_can_be_switched_off(monkeypatch):
    recorder = serve(lambda r: json_response(fixture("stocktwits_stream.json")))
    monkeypatch.setattr(get_settings(), "stocktwits_enabled", False)
    reading = await stocktwits.ticker("PLTR")
    assert not reading.available and recorder.requests == []


async def test_untagged_only_stream_explains_itself():
    stream = {"symbol": {}, "messages": [{"id": 1, "entities": {}}, {"id": 2}]}
    serve(lambda r: json_response(json.dumps(stream)))
    reading = await stocktwits.ticker("PLTR")
    assert reading.bull_pct is None and "tag" in reading.caveat


# ── Placeholders ────────────────────────────────────────────────────────────


async def test_placeholders_report_what_is_missing(monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "reddit_client_id", "")
    monkeypatch.setattr(settings, "x_bearer_token", "")

    reddit, x = await RedditApiProvider().status(), await XProvider().status()
    assert (reddit.enabled, reddit.state, reddit.kind) == (False, "not_configured", "placeholder")
    assert "REDDIT_CLIENT_ID" in reddit.reason and "X_BEARER_TOKEN" in x.reason

    trending = await RedditApiProvider().trending("all", 10)
    assert not trending.available and trending.rows == []


async def test_a_configured_placeholder_says_it_is_not_built(monkeypatch):
    monkeypatch.setattr(get_settings(), "x_bearer_token", "token")
    assert "not implemented" in (await XProvider().status()).reason


async def test_every_provider_reports_a_status():
    ids = [s.id for s in await statuses()]
    assert ids == ["apewisdom", "stocktwits", "reddit", "x"]
