"""History, overview and breadth read settled sessions from the bar store.

Only today's partial session should come from the provider. These pin that
split and the fallbacks either side of it: a cold or unavailable store must
produce exactly the payload the live path always did.
"""

from __future__ import annotations

from datetime import date, timedelta

import pandas as pd
import pytest

from app.services import bar_store, cache
from app.services import market_data as md
from app.services.bar_derive import BarRow
from tests.unit.test_cache_tiers import FakeRedis

TODAY = date(2026, 10, 30)  # a Friday


@pytest.fixture(autouse=True)
def isolated(monkeypatch):
    monkeypatch.setattr(cache, "redis_client", FakeRedis())
    monkeypatch.setattr("app.db.repositories_market.market_today", lambda: TODAY)
    cache._CACHE.clear()
    cache._locks.clear()
    yield
    cache._CACHE.clear()


def _sessions(end: date, count: int) -> list[date]:
    days, d = [], end
    while len(days) < count:
        if d.weekday() < 5:
            days.append(d)
        d -= timedelta(days=1)
    return sorted(days)


def _bars(days: list[date], start_price: float = 100.0) -> list[BarRow]:
    return [
        BarRow(bar_date=d, open=start_price + i - 0.5, high=start_price + i + 1,
               low=start_price + i - 1, close=start_price + i, adj_close=start_price + i,
               volume=1_000 + i)
        for i, d in enumerate(days)
    ]


def _frame(days: list[date], closes: list[float]) -> pd.DataFrame:
    idx = pd.DatetimeIndex([pd.Timestamp(d) for d in days])
    return pd.DataFrame(
        {"Open": closes, "High": [c + 1 for c in closes], "Low": [c - 1 for c in closes],
         "Close": closes, "Adj Close": closes, "Volume": [5_000] * len(closes)},
        index=idx,
    )


def _multi(frames: dict[str, pd.DataFrame]) -> pd.DataFrame:
    return pd.concat(frames, axis=1)


# ── history ─────────────────────────────────────────────────────────────────


async def test_daily_history_is_stored_bars_plus_todays_live_bar(monkeypatch):
    settled_days = _sessions(TODAY - timedelta(days=1), 60)
    settled = _bars(settled_days)
    monkeypatch.setattr(bar_store, "daily_bars_for", _async(lambda *a, **k: settled))

    # The live 5d frame overlaps the store by four sessions; only today is new.
    live_days = [*settled_days[-4:], TODAY]
    live_closes = [b.close for b in settled[-4:]] + [222.0]
    monkeypatch.setattr(md, "_download", lambda *a, **k: _frame(live_days, live_closes))
    monkeypatch.setattr(md.yf, "Ticker", _explode)

    out = await md.get_history("aapl", "3M")

    assert out["intraday"] is False and out["interval"] == "1d"
    times = [c["time"] for c in out["candles"]]
    assert times == [d.isoformat() for d in settled_days] + [TODAY.isoformat()]
    assert out["candles"][-1]["close"] == 222.0
    first = settled[0].close
    assert out["period_change_percent"] == pytest.approx((222.0 - first) / first * 100, abs=1e-3)


async def test_five_year_history_buckets_weekly(monkeypatch):
    days = _sessions(TODAY - timedelta(days=1), 15)
    monkeypatch.setattr(bar_store, "daily_bars_for", _async(lambda *a, **k: _bars(days)))
    monkeypatch.setattr(md, "_download", lambda *a, **k: _frame([], []))

    out = await md.get_history("AAPL", "5Y")

    assert out["interval"] == "1wk"
    assert all(date.fromisoformat(c["time"]).weekday() == 0 for c in out["candles"])
    mondays = {d - timedelta(days=d.weekday()) for d in days}
    assert len(out["candles"]) == len(mondays)


async def test_history_falls_back_to_live_when_the_store_is_empty(monkeypatch):
    monkeypatch.setattr(bar_store, "daily_bars_for", _async(lambda *a, **k: []))
    calls = []

    class LiveTicker:
        def __init__(self, symbol):
            calls.append(symbol)

        def history(self, period, interval, auto_adjust):
            return _frame(_sessions(TODAY, 3), [10.0, 11.0, 12.0])

    monkeypatch.setattr(md.yf, "Ticker", LiveTicker)
    out = await md.get_history("NEWCO", "1Y")
    assert calls == ["NEWCO"]
    assert [c["close"] for c in out["candles"]] == [10.0, 11.0, 12.0]


async def test_history_still_charts_settled_bars_when_today_fails(monkeypatch):
    settled = _bars(_sessions(TODAY - timedelta(days=1), 5))
    monkeypatch.setattr(bar_store, "daily_bars_for", _async(lambda *a, **k: settled))

    def down(*a, **k):
        raise RuntimeError("rate limited")

    monkeypatch.setattr(md, "_download", down)
    out = await md.get_history("AAPL", "6M")
    assert len(out["candles"]) == 5


async def test_intraday_history_never_touches_the_store(monkeypatch):
    monkeypatch.setattr(bar_store, "daily_bars_for", _async(_explode))

    class LiveTicker:
        def __init__(self, symbol):
            pass

        def history(self, period, interval, auto_adjust):
            assert interval == "5m"
            return _frame([TODAY], [50.0])

    monkeypatch.setattr(md.yf, "Ticker", LiveTicker)
    out = await md.get_history("AAPL", "1D")
    assert out["intraday"] is True
    assert isinstance(out["candles"][0]["time"], int)


# ── overview ────────────────────────────────────────────────────────────────


async def test_overview_prices_warm_symbols_over_five_days_and_cold_over_a_month(monkeypatch):
    symbols = [g["symbol"] for g in md.INDICES + md.SECTORS + md.MACRO]
    warm, cold = symbols[:-1], symbols[-1]
    month = _sessions(TODAY - timedelta(days=1), 20)
    stored = {s: [(d, 100.0 + i) for i, d in enumerate(month)] for s in warm}
    monkeypatch.setattr(md, "_stored_closes", _async(lambda *a, **k: stored))

    requests = []

    def download(syms, period, interval):
        requests.append((tuple(syms), period))
        days = [*month[-4:], TODAY] if period == "5d" else [*month, TODAY]
        closes = [100.0 + i for i in range(len(days))]
        return _multi({s: _frame(days, closes) for s in syms})

    monkeypatch.setattr(md, "_download", download)
    out = await md.get_overview()

    assert sorted(requests, key=lambda r: r[1]) == [(tuple(cold.split()), "1mo"), (tuple(warm), "5d")]
    spx = next(r for r in out["indices"] if r["symbol"] == "^GSPC")
    # Stored month (20 closes) plus today, with no session counted twice.
    assert len(spx["sparkline"]) == 21
    assert spx["prev_close"] == pytest.approx(119.0)


async def test_overview_is_the_old_single_request_when_the_store_is_down(monkeypatch):
    monkeypatch.setattr(md, "_stored_closes", _async(lambda *a, **k: {}))
    requests = []

    def download(syms, period, interval):
        requests.append(period)
        days = _sessions(TODAY, 22)
        return _multi({s: _frame(days, [50.0 + i for i in range(22)]) for s in syms})

    monkeypatch.setattr(md, "_download", download)
    out = await md.get_overview()
    assert requests == ["1mo"]
    assert all(r.get("last") is not None for r in out["indices"])


# ── breadth ─────────────────────────────────────────────────────────────────


def test_day_change_uses_the_stored_previous_close_for_a_one_row_frame():
    raw = _frame([TODAY], [110.0])
    prev = (TODAY - timedelta(days=1), 100.0)
    assert md._day_change(raw, "X", prev) == pytest.approx(10.0)


def test_day_change_ignores_a_stored_close_from_too_long_ago():
    raw = _frame([TODAY], [110.0])
    assert md._day_change(raw, "X", (TODAY - timedelta(days=9), 100.0)) is None


def test_day_change_prefers_the_live_previous_close():
    raw = _frame([TODAY - timedelta(days=1), TODAY], [105.0, 110.0])
    assert md._day_change(raw, "X", (TODAY - timedelta(days=1), 1.0)) == pytest.approx(
        (110 - 105) / 105 * 100
    )


# ── helpers ─────────────────────────────────────────────────────────────────


def _async(fn):
    async def wrapper(*a, **k):
        return fn(*a, **k)

    return wrapper


def _explode(*a, **k):
    raise AssertionError("the provider should not have been called")
