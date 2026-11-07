"""News source parsers and polls — recorded payloads, a mock transport, no network."""

from __future__ import annotations

import re
from pathlib import Path

import httpx
import pytest

from app.config import get_settings
from app.services.news import alpaca, edgar, fed, http
from app.services.news.models import CATEGORIES, headline_id

# Trimmed from the live feeds on 2026-11-06, with the cases that matter added.
EDGAR_XML = """<?xml version="1.0" encoding="ISO-8859-1" ?>
<feed xmlns="http://www.w3.org/2005/Atom">
<title>Latest Filings</title>
<entry>
<title>8-K - TREDEGAR CORP (0000850429) (Filer)</title>
<link rel="alternate" type="text/html" href="https://www.sec.gov/Archives/edgar/data/850429/000162828026071347/0001628280-26-071347-index.htm"/>
<summary type="html">
 &lt;b&gt;Filed:&lt;/b&gt; 2026-11-06 &lt;b&gt;AccNo:&lt;/b&gt; 0001628280-26-071347 &lt;b&gt;Size:&lt;/b&gt; 598 KB
&lt;br&gt;Item 2.02: Results of Operations and Financial Condition
&lt;br&gt;Item 5.02: Departure of Directors or Certain Officers
&lt;br&gt;Item 9.01: Financial Statements and Exhibits
</summary>
<updated>2026-11-06T08:20:42-05:00</updated>
<id>urn:tag:sec.gov,2008:accession-number=0001628280-26-071347</id>
</entry>
<entry>
<title>8-K - TREDEGAR CORP (0000850429) (Filer)</title>
<link rel="alternate" type="text/html" href="https://www.sec.gov/x"/>
<summary type="html">&lt;br&gt;Item 2.02: Results of Operations and Financial Condition</summary>
<updated>2026-11-06T08:20:42-05:00</updated>
<id>urn:tag:sec.gov,2008:accession-number=0001628280-26-071347</id>
</entry>
<entry>
<title>8-K - Wheeler Real Estate Investment Trust, Inc. (0001527541) (Filer)</title>
<link rel="alternate" type="text/html" href="https://www.sec.gov/y"/>
<summary type="html">&lt;br&gt;Item 5.07: Submission of Matters to a Vote of Security Holders
&lt;br&gt;Item 8.01: Other Events</summary>
<updated>2026-11-06T08:16:35-05:00</updated>
<id>urn:tag:sec.gov,2008:accession-number=0001527541-26-000427</id>
</entry>
<entry>
<title>8-K - Some Shell Co (0009999999) (Filer)</title>
<link rel="alternate" type="text/html" href="https://www.sec.gov/z"/>
<summary type="html">&lt;br&gt;Item 1.01: Entry into a Material Definitive Agreement</summary>
<updated>2026-11-06T08:10:00-05:00</updated>
<id>urn:tag:sec.gov,2008:accession-number=0009999999-26-000001</id>
</entry>
<entry>
<title>8-K/A - Koppers Holdings Inc. (0001315257) (Filer)</title>
<link rel="alternate" type="text/html" href="https://www.sec.gov/k"/>
<summary type="html">&lt;br&gt;Item 2.02: Results of Operations and Financial Condition</summary>
<updated>2026-11-06T08:13:32-05:00</updated>
<id>urn:tag:sec.gov,2008:accession-number=0001315257-26-000050</id>
</entry>
</feed>"""

TICKERS = {
    "fields": ["cik", "name", "ticker", "exchange"],
    "data": [
        [850429, "TREDEGAR CORP", "TG", "NYSE"],
        [1527541, "Wheeler REIT", "WHLR", "Nasdaq"],
        [9999999, "Some Shell Co", "SHLL", "OTC"],
        [1315257, "Koppers", "KOP", "NYSE"],
        [1920406, "Strive", "ASST", "Nasdaq"],
        [1920406, "Strive", "SATA", "Nasdaq"],
    ],
}

FED_XML = """﻿<?xml version="1.0" encoding="utf-8" ?>
<rss version="2.0"><channel><title>FRB</title>
<item>
  <title>Federal Reserve issues FOMC statement</title>
  <link><![CDATA[https://www.federalreserve.gov/newsevents/pressreleases/monetary20261028a.htm]]></link>
  <guid><![CDATA[https://www.federalreserve.gov/newsevents/pressreleases/monetary20261028a.htm]]></guid>
  <description><![CDATA[Federal Reserve issues FOMC statement]]></description>
  <category>Monetary Policy</category>
  <pubDate><![CDATA[Wed, 28 Oct 2026 18:00:00 GMT]]></pubDate>
</item>
<item>
  <title>Federal Reserve Board announces termination of enforcement action</title>
  <link><![CDATA[https://www.federalreserve.gov/newsevents/pressreleases/enforcement20261105a.htm]]></link>
  <guid><![CDATA[https://www.federalreserve.gov/newsevents/pressreleases/enforcement20261105a.htm]]></guid>
  <description><![CDATA[With Example Bank, N.A.]]></description>
  <category>Enforcement Actions</category>
  <pubDate><![CDATA[Thu, 5 Nov 2026 16:00:00 GMT]]></pubDate>
</item>
<item><title>No date</title><guid>https://x</guid><pubDate>not a date</pubDate></item>
</channel></rss>"""

ALPACA_ITEM = {
    "id": 24843171, "headline": "Apple Shares Rise After &amp; <b>Strong</b> iPhone Sales",
    "summary": "Apple reported quarterly revenue above expectations.",
    "author": "Benzinga Newsdesk", "created_at": "2026-11-06T13:31:02Z",
    "updated_at": "2026-11-06T13:31:03Z", "url": "https://www.benzinga.com/news/24843171",
    "symbols": ["AAPL", "aapl2"], "source": "benzinga", "content": "",
}


@pytest.fixture
def transport():
    """Route news HTTP through a handler the test sets; reset afterwards."""
    handlers: dict[str, httpx.Response | Exception] = {}
    seen: list[httpx.Request] = []

    def handle(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        for pattern, answer in handlers.items():
            if re.search(pattern, str(request.url)):
                if isinstance(answer, Exception):
                    raise answer
                return answer
        return httpx.Response(404)

    http.use_transport(httpx.MockTransport(handle))
    yield handlers, seen
    http.use_transport(None)


@pytest.fixture(autouse=True)
def no_stored_settings(monkeypatch):
    """No contact saved from the Settings page, so .env decides."""
    from app.services import app_settings

    monkeypatch.setattr(app_settings, "_values", {})


@pytest.fixture
def settings(monkeypatch):
    s = get_settings()
    for key, value in {"sec_user_agent": "Test Runner test@example.com",
                       "alpaca_api_key_id": "kid", "alpaca_api_secret": "secret",
                       "news_enabled": True}.items():
        monkeypatch.setattr(s, key, value)
    return s


# ── EDGAR ─────────────────────────────────────────────────────────────────────


def test_edgar_keeps_listed_material_filings_once():
    headlines = edgar.parse_feed(EDGAR_XML, edgar.ticker_map(TICKERS))
    assert [h.symbols for h in headlines] == [["TG"]]   # no vote, no OTC, no 8-K/A, no repeat
    tg = headlines[0]
    assert tg.category == "earnings"                    # 2.02 outranks 5.02
    assert tg.title == "TREDEGAR CORP (TG) 8-K: results of operations, director or officer change"
    assert tg.tags == ["8-K 2.02", "8-K 5.02", "8-K 9.01"]
    assert tg.published_at.isoformat() == "2026-11-06T08:20:42-05:00"
    assert tg.id == headline_id("edgar", "0001628280-26-071347")


def test_ticker_map_drops_otc_and_keeps_the_primary_listing_first():
    mapping = edgar.ticker_map(TICKERS)
    assert "9999999" not in mapping
    assert mapping["1920406"] == ["ASST", "SATA"]
    assert edgar.ticker_map({"data": [[1, "x", "Y", "NYSE"]]}) == {}   # no fields header


async def test_edgar_is_off_without_a_contact_user_agent(monkeypatch, transport):
    monkeypatch.setattr(get_settings(), "sec_user_agent", "")
    provider = edgar.EdgarProvider()
    batch = await provider.poll()
    assert not batch.available and "SEC_USER_AGENT" in batch.caveat
    assert provider.status().state == "not_configured"
    assert transport[1] == []                          # never asked the SEC anonymously


async def test_edgar_poll_sends_the_contact_user_agent(settings, transport, monkeypatch):
    from app.services import cache
    from tests.unit.test_cache_tiers import FakeRedis

    monkeypatch.setattr(cache, "redis_client", FakeRedis())
    cache._CACHE.clear()
    handlers, seen = transport
    handlers[r"browse-edgar"] = httpx.Response(200, text=EDGAR_XML)
    handlers[r"company_tickers_exchange"] = httpx.Response(200, json=TICKERS)

    batch = await edgar.EdgarProvider().poll()
    assert batch.available and len(batch.headlines) == 1
    assert {r.headers["user-agent"] for r in seen} == {"Test Runner test@example.com"}
    cache._CACHE.clear()


async def test_a_refused_edgar_poll_marks_the_source_blocked(settings, transport, monkeypatch):
    from app.services import cache
    from tests.unit.test_cache_tiers import FakeRedis

    monkeypatch.setattr(cache, "redis_client", FakeRedis())
    cache._CACHE.clear()
    handlers, _ = transport
    handlers[r"browse-edgar"] = httpx.Response(403, text="Request Rate Threshold Exceeded")
    handlers[r"company_tickers_exchange"] = httpx.Response(200, json=TICKERS)

    provider = edgar.EdgarProvider()
    batch = await provider.poll()
    assert not batch.available
    assert provider.status().state == "blocked"
    cache._CACHE.clear()


# ── Fed ───────────────────────────────────────────────────────────────────────


def test_fed_parses_through_the_byte_order_mark():
    headlines = fed.parse_feed(FED_XML)
    assert [h.category for h in headlines] == ["monetary_policy", "regulatory"]
    fomc, enforcement = headlines
    assert fomc.summary is None                         # a repeat of the title is no summary
    assert enforcement.summary == "With Example Bank, N.A."
    assert fomc.symbols == [] and fomc.tags == ["Monetary Policy"]
    assert fomc.published_at.isoformat() == "2026-10-28T18:00:00+00:00"


async def test_fed_poll_recovers_after_a_failure(settings, transport):
    handlers, _ = transport
    provider = fed.FedProvider()

    handlers[r"federalreserve"] = httpx.Response(200, text="<not xml")
    assert not (await provider.poll()).available
    assert provider.status().state == "blocked"

    handlers[r"federalreserve"] = httpx.Response(200, text=FED_XML)
    assert len((await provider.poll()).headlines) == 2
    assert provider.status().state == "live"


# ── Alpaca ────────────────────────────────────────────────────────────────────


def test_alpaca_item_is_cleaned_and_tagged():
    h = alpaca.parse_item(ALPACA_ITEM)
    assert h.title == "Apple Shares Rise After & Strong iPhone Sales"
    assert h.symbols == ["AAPL", "AAPL2"]
    assert h.publisher == "Benzinga" and h.category == "news"
    assert h.published_at.isoformat() == "2026-11-06T13:31:02+00:00"
    assert alpaca.parse_item({**ALPACA_ITEM, "url": "javascript:alert(1)"}).url is None
    assert alpaca.parse_item({**ALPACA_ITEM, "headline": ""}) is None


def test_stream_frames_may_be_arrays_or_single_objects():
    assert alpaca.parse_messages('[{"T":"success","msg":"connected"}]')[0]["T"] == "success"
    assert alpaca.parse_messages('{"T":"n","id":1}') == [{"T": "n", "id": 1}]


async def test_alpaca_without_keys_says_so(monkeypatch, transport):
    monkeypatch.setattr(get_settings(), "alpaca_api_key_id", "")
    provider = alpaca.AlpacaProvider()
    batch = await provider.fetch_since(alpaca._when("2026-11-06T00:00:00Z"))
    assert not batch.available and "ALPACA_API_KEY_ID" in batch.caveat
    assert provider.status().state == "not_configured"
    assert transport[1] == []


async def test_alpaca_rest_follows_pages_with_auth_headers(settings, transport):
    handlers, seen = transport
    pages = iter([
        {"news": [ALPACA_ITEM], "next_page_token": "abc"},
        {"news": [{**ALPACA_ITEM, "id": 2}], "next_page_token": None},
    ])

    def page(request):
        return httpx.Response(200, json=next(pages))

    http.use_transport(httpx.MockTransport(lambda r: (seen.append(r), page(r))[1]))
    batch = await alpaca.AlpacaProvider().fetch_since(alpaca._when("2026-11-06T00:00:00Z"))
    assert [h.source_id for h in batch.headlines] == ["24843171", "2"]
    assert seen[0].headers["apca-api-key-id"] == "kid"
    assert seen[0].url.params["start"] == "2026-11-06T00:00:00Z"
    assert seen[1].url.params["page_token"] == "abc"


# ── The invariant ─────────────────────────────────────────────────────────────


def test_the_news_package_never_spells_an_action_verb():
    """Same rule as services/signals.py: ingest reports, it never advises."""
    package = Path(edgar.__file__).parent
    for path in package.glob("*.py"):
        source = path.read_text().lower()
        assert not re.search(r"\b(buy|sell|hold)\b", source), path.name


def test_ingest_categories_are_structural():
    assert {category for category, _ in edgar.MATERIAL_ITEMS.values()} <= set(CATEGORIES)
    assert set(fed.CATEGORY_MAP.values()) <= set(CATEGORIES)
