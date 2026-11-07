"""Sector/industry for the wire's tickers: lookup, caching, and row fill-in.

No network and no database: Yahoo, the cache and the repository are patched.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.services.news import classify as cls
from app.services.news import runner


class FakeCache:
    """get_fresh_many / put_many over a dict, with a clock the test moves."""

    def __init__(self):
        self.now = 0.0
        self.store: dict[str, tuple[object, float]] = {}

    async def get_fresh_many(self, keys, ttl):
        return {k: v for k in keys if (e := self.store.get(k)) and self.now - e[1] < ttl
                for v in [e[0]]}

    async def put_many(self, mapping, ttl):
        for k, v in mapping.items():
            self.store[k] = (v, self.now)


@pytest.fixture
def cache(monkeypatch):
    fake = FakeCache()
    monkeypatch.setattr(cls, "get_fresh_many", fake.get_fresh_many)
    monkeypatch.setattr(cls, "put_many", fake.put_many)
    return fake


@pytest.fixture
def yahoo(monkeypatch):
    """Symbol -> info dict, or an Exception to raise."""
    answers: dict[str, object] = {}
    asked: list[str] = []

    class Ticker:
        def __init__(self, symbol):
            asked.append(symbol)
            answer = answers.get(symbol, {})
            if isinstance(answer, Exception):
                raise answer
            self.info = answer

    monkeypatch.setattr(cls.yf, "Ticker", Ticker)
    return answers, asked


def test_a_profile_is_normalised_to_the_sector_panels_labels():
    assert cls.profile_of({"sector": "Financial Services", "industry": "Banks - Diversified"}) == \
        {"sector": "Financials", "industry": "Banks - Diversified"}
    assert cls.profile_of({"sector": None, "industry": "  "}) is None


async def test_answers_are_cached_and_class_shares_are_asked_yahoos_way(cache, yahoo):
    answers, asked = yahoo
    answers["NVDA"] = {"sector": "Technology", "industry": "Semiconductors"}
    answers["BRK-B"] = {"sector": "Financial Services", "industry": "Insurance - Diversified"}

    first = await cls.classify(["NVDA", "BRK.B", "NVDA"])
    assert first["NVDA"] == {"sector": "Technology", "industry": "Semiconductors"}
    assert first["BRK.B"]["industry"] == "Insurance - Diversified"
    assert asked == ["NVDA", "BRK-B"]                       # once each

    cache.now += cls.PROFILE_TTL - 60
    assert await cls.classify(["NVDA"]) == {"NVDA": first["NVDA"]}
    assert asked == ["NVDA", "BRK-B"]                       # still cached


async def test_no_profile_is_a_final_answer(cache, yahoo):
    _, asked = yahoo                                         # BTCUSD: empty info
    assert await cls.classify(["BTCUSD"]) == {"BTCUSD": None}
    cache.now += cls.FAILED_TTL * 5
    assert await cls.classify(["BTCUSD"]) == {"BTCUSD": None}
    assert asked == ["BTCUSD"]


async def test_a_failed_lookup_is_left_out_and_retried_after_the_short_ttl(cache, yahoo):
    answers, asked = yahoo
    answers["AAPL"] = RuntimeError("429 Too Many Requests")

    assert await cls.classify(["AAPL"]) == {}               # no answer, not "no industry"
    assert await cls.classify(["AAPL"]) == {}
    assert asked == ["AAPL"]                                 # not hammered within the hour

    answers["AAPL"] = {"sector": "Technology", "industry": "Consumer Electronics"}
    cache.now += cls.FAILED_TTL + 1
    assert (await cls.classify(["AAPL"]))["AAPL"]["industry"] == "Consumer Electronics"


async def test_a_row_is_filled_only_once_every_symbol_it_names_has_an_answer(monkeypatch):
    import app.db.repositories_news as repo

    rows = [SimpleNamespace(id="both", symbols=["NVDA", "AMD"]),
            SimpleNamespace(id="one", symbols=["NVDA"]),
            SimpleNamespace(id="waits", symbols=["AMD", "AAPL"])]
    written: dict[str, dict] = {}

    async def pending(session, *, since, limit=100):
        return rows

    async def write(session, info_by_id):
        written.update(info_by_id)

    async def known(symbols):
        semis = {"sector": "Technology", "industry": "Semiconductors"}
        return {"NVDA": semis, "AMD": semis}                 # AAPL's lookup failed

    monkeypatch.setattr(repo, "needing_symbol_info", pending)
    monkeypatch.setattr(repo, "set_symbol_info", write)
    monkeypatch.setattr(cls, "classify", known)

    assert await runner.classify_pending() == 2
    assert set(written) == {"both", "one"}
    assert written["both"]["AMD"]["industry"] == "Semiconductors"
