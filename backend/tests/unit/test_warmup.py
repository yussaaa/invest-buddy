"""The warm-up loop: when it backfills, and the calendar refresh-ahead."""

from __future__ import annotations

from datetime import datetime

import pytest

from app.services import cache, warmup
from app.services import market_data as md
from app.db.repositories_market import MARKET_TZ
from tests.unit.test_cache_tiers import FakeRedis


@pytest.fixture(autouse=True)
def isolated(monkeypatch):
    fake = FakeRedis()
    monkeypatch.setattr(cache, "redis_client", fake)
    monkeypatch.setattr(warmup.redis_client, "is_available", lambda: True)
    monkeypatch.setattr(warmup.redis_client, "try_lock", fake.try_lock)
    cache._CACHE.clear()
    warmup._last_backfill = None
    yield fake
    cache._CACHE.clear()


def _ny(hour: int, minute: int = 0, day: int = 2) -> datetime:
    return datetime(2026, 11, day, hour, minute, tzinfo=MARKET_TZ)


# ── schedule ────────────────────────────────────────────────────────────────


def test_backfill_runs_at_startup():
    assert warmup.backfill_due(_ny(9), None)


def test_backfill_reruns_once_after_the_close():
    morning = _ny(9)
    assert not warmup.backfill_due(_ny(15, 59), morning)
    assert warmup.backfill_due(_ny(16, 30), morning)
    assert not warmup.backfill_due(_ny(18), _ny(16, 30))


def test_backfill_runs_the_next_evening_not_the_next_morning():
    last = _ny(16, 40, day=2)
    assert not warmup.backfill_due(_ny(9, day=3), last)
    assert warmup.backfill_due(_ny(16, 31, day=3), last)


# ── earnings calendar refresh-ahead ─────────────────────────────────────────


async def test_calendar_is_fetched_when_missing_and_primed_for_the_endpoint(monkeypatch):
    calls = []

    def fetch(universe):
        calls.append(len(universe))
        return {"AAPL": [{"date": "2026-11-05"}]}

    monkeypatch.setattr(md, "_fetch_earnings_calendar", fetch)
    assert await md.refresh_earnings_calendar() is True

    # The endpoint's key now holds it: a visitor would not trigger a fetch.
    key = md._earnings_key(md._earnings_universe())
    served = await cache.cached(key, md.EARNINGS_CALENDAR_TTL, lambda: fetch([]))
    assert served == {"AAPL": [{"date": "2026-11-05"}]}
    assert calls == [len(md.EARNINGS_UNIVERSE)]


async def test_a_young_calendar_is_left_alone(monkeypatch):
    key = md._earnings_key(md._earnings_universe())
    await cache.prime(key, {"X": []}, md.EARNINGS_CALENDAR_TTL)
    monkeypatch.setattr(md, "_fetch_earnings_calendar", _explode)
    assert await md.refresh_earnings_calendar() is False


async def test_a_calendar_near_expiry_is_refreshed_before_it_lapses(monkeypatch):
    clock = {"t": 1_000_000.0}
    monkeypatch.setattr(cache.time, "time", lambda: clock["t"])
    key = md._earnings_key(md._earnings_universe())
    await cache.prime(key, {"OLD": []}, md.EARNINGS_CALENDAR_TTL)

    clock["t"] += md.EARNINGS_CALENDAR_TTL * 0.9    # still fresh, but nearly due
    monkeypatch.setattr(md, "_fetch_earnings_calendar", lambda u: {"NEW": []})
    assert await md.refresh_earnings_calendar() is True
    assert cache.peek(key) == {"NEW": []}


# ── tick ────────────────────────────────────────────────────────────────────


async def test_only_one_replica_does_each_job(monkeypatch, isolated):
    ran = []

    async def calendar():
        ran.append("calendar")
        return True

    async def backfill():
        ran.append("backfill")
        return {}

    monkeypatch.setattr(md, "refresh_earnings_calendar", calendar)
    monkeypatch.setattr(warmup, "run_backfill", backfill)

    await warmup.tick()                 # this replica takes both locks
    warmup._last_backfill = None        # a second replica, also at startup
    await warmup.tick()

    assert ran == ["calendar", "backfill"]


async def test_a_failing_job_does_not_stop_the_other(monkeypatch):
    ran = []

    async def calendar():
        raise RuntimeError("yahoo down")

    async def backfill():
        ran.append("backfill")
        return {}

    monkeypatch.setattr(md, "refresh_earnings_calendar", calendar)
    monkeypatch.setattr(warmup, "run_backfill", backfill)
    await warmup.tick()
    assert ran == ["backfill"]


async def test_backfill_universe_leads_with_the_reference_symbol(monkeypatch):
    async def none():
        return []

    monkeypatch.setattr(warmup, "_watchlist_symbols", none)
    monkeypatch.setattr(warmup, "_breadth_symbols", _async_return(["SPY", "ZZZZ"]))
    universe = await warmup.backfill_universe()

    assert universe[0] == ("SPY", warmup.CORE_SPAN)
    symbols = [s for s, _ in universe]
    assert len(symbols) == len(set(symbols)), "no symbol is backfilled twice"
    assert universe[-1] == ("ZZZZ", warmup.WIDE_SPAN)


def _async_return(value):
    async def fn():
        return value

    return fn


def _explode(*a, **k):
    raise AssertionError("should not have fetched")
