"""Integration tests for the bar store facade — needs a real PostgreSQL.

These stub the provider so they don't depend on the network; the DB half is
real.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from app.services import bar_store
from app.services.bar_derive import BarRow
from app.db.repositories_market import get_coverage, market_today

pytestmark = pytest.mark.integration


def _fake_history(days: int = 30, split: bool = False):
    """`days` settled sessions plus today's in-progress bar."""
    yesterday = market_today() - timedelta(days=1)
    rows = [
        BarRow(
            bar_date=yesterday - timedelta(days=i),
            open=100.0 + i,
            high=101.0 + i,
            low=99.0 + i,
            close=100.0 + i,
            adj_close=50.0 + i / 2,  # deliberately half, so adjustment is visible
            volume=1_000 + i,
        )
        for i in range(days)
    ]
    rows.append(BarRow(market_today(), 999.0, 999.0, 999.0, 999.0, 999.0, 1))
    return sorted(rows, key=lambda r: r.bar_date), split


async def test_first_call_fetches_stores_and_trims_today(db_session, monkeypatch):
    monkeypatch.setattr(bar_store, "fetch_live", lambda s, span: _fake_history())

    rows = await bar_store.daily_bars_for(
        "FAKE1", market_today() - timedelta(days=60), session=db_session
    )

    assert rows, "expected bars from the live path"
    assert all(r.bar_date < market_today() for r in rows), "today must not be served"

    coverage = (await get_coverage(db_session, ["FAKE1"]))["FAKE1"]
    assert coverage.bar_count == 30, "settled bars should have been persisted"


async def test_second_call_is_served_from_the_store(db_session, monkeypatch):
    monkeypatch.setattr(bar_store, "fetch_live", lambda s, span: _fake_history())
    start = market_today() - timedelta(days=60)

    first = await bar_store.daily_bars_for("FAKE2", start, session=db_session)

    calls = []

    def explode(symbol, span):
        calls.append(symbol)
        raise AssertionError("should not hit the provider on a covered read")

    monkeypatch.setattr(bar_store, "fetch_live", explode)
    second = await bar_store.daily_bars_for("FAKE2", start, session=db_session)

    assert calls == []
    assert [(r.bar_date, r.close) for r in first] == [(r.bar_date, r.close) for r in second]


async def test_adjusted_series_differs_from_raw(db_session, monkeypatch):
    monkeypatch.setattr(bar_store, "fetch_live", lambda s, span: _fake_history())
    start = market_today() - timedelta(days=60)

    raw = await bar_store.daily_bars_for("FAKE3", start, session=db_session)
    adjusted = await bar_store.daily_bars_for(
        "FAKE3", start, session=db_session, adjusted=True
    )

    assert [r.close for r in raw] != [r.close for r in adjusted]
    assert adjusted[-1].close == pytest.approx(raw[-1].adj_close)


async def test_split_triggers_a_clean_refetch(db_session, monkeypatch):
    start = market_today() - timedelta(days=60)
    monkeypatch.setattr(bar_store, "fetch_live", lambda s, span: _fake_history(days=30))
    await bar_store.daily_bars_for("FAKE4", start, session=db_session)

    # A split invalidates stored prices, so coverage must be rebuilt from the
    # new series rather than merged into the old one.
    monkeypatch.setattr(
        bar_store, "fetch_live", lambda s, span: _fake_history(days=10, split=True)
    )
    # Force a miss by asking for more history than we hold.
    await bar_store.daily_bars_for(
        "FAKE4", market_today() - timedelta(days=900), session=db_session
    )

    coverage = (await get_coverage(db_session, ["FAKE4"]))["FAKE4"]
    assert coverage.bar_count == 10, "stale pre-split rows should have been dropped"


async def test_empty_provider_response_is_not_fatal(db_session, monkeypatch):
    monkeypatch.setattr(bar_store, "fetch_live", lambda s, span: ([], False))

    rows = await bar_store.daily_bars_for(
        "NOSUCH", market_today() - timedelta(days=30), session=db_session
    )

    assert rows == []


async def _age_coverage(db_session, symbol: str, days: int) -> None:
    """Pretend the symbol was last refreshed `days` ago."""
    from datetime import datetime, timezone

    from sqlalchemy import update

    from app.db.models import BarCoverage

    await db_session.execute(
        update(BarCoverage)
        .where(BarCoverage.symbol == symbol)
        .values(last_refresh_at=datetime.now(timezone.utc) - timedelta(days=days))
    )
    await db_session.flush()


def _history_ending(last: "date", days: int = 30):
    rows = [
        BarRow(last - timedelta(days=i), 100.0, 101.0, 99.0, 100.0 + i, 100.0 + i, 1_000)
        for i in range(days)
    ]
    return sorted(rows, key=lambda r: r.bar_date), False


async def test_a_store_stale_as_a_whole_still_refreshes(db_session, monkeypatch):
    """SPY and the symbol both months behind must not vouch for each other."""
    months_ago = market_today() - timedelta(days=90)
    monkeypatch.setattr(bar_store, "fetch_live", lambda s, span: _history_ending(months_ago))
    start = market_today() - timedelta(days=200)
    for sym in ("SPY", "FAKES"):
        await bar_store.daily_bars_for(sym, start, session=db_session)
        await _age_coverage(db_session, sym, days=90)

    calls = []

    def fresh(symbol, span):
        calls.append(symbol)
        return _fake_history(days=120)

    monkeypatch.setattr(bar_store, "fetch_live", fresh)
    rows = await bar_store.daily_bars_for("FAKES", start, session=db_session)

    assert calls == ["FAKES"], "a months-old series was served as current"
    assert rows[-1].bar_date == market_today() - timedelta(days=1)


async def test_a_gap_already_refreshed_today_is_not_refetched(db_session, monkeypatch):
    """The day after a holiday looks one session short; retry once, not every read."""
    behind = market_today() - timedelta(days=10)
    monkeypatch.setattr(bar_store, "fetch_live", lambda s, span: _history_ending(behind))
    start = market_today() - timedelta(days=20)
    await bar_store.daily_bars_for("FAKEH", start, session=db_session)  # refreshed now

    monkeypatch.setattr(
        bar_store, "fetch_live",
        lambda s, span: (_ for _ in ()).throw(AssertionError("refetched twice in a day")),
    )
    await bar_store.daily_bars_for("FAKEH", start, session=db_session)


async def test_a_refresh_never_narrows_the_stored_span(db_session, monkeypatch):
    spans = []

    def record(symbol, span):
        spans.append(span)
        return _fake_history()

    monkeypatch.setattr(bar_store, "fetch_live", record)
    await bar_store.daily_bars_for("FAKEW", market_today() - timedelta(days=30), session=db_session, span="max")
    await _age_coverage(db_session, "FAKEW", days=7)
    # Make it stale: drop the newest bars so it is behind the calendar.
    from sqlalchemy import delete

    from app.db.models import DailyBar

    await db_session.execute(
        delete(DailyBar).where(DailyBar.symbol == "FAKEW",
                               DailyBar.bar_date >= market_today() - timedelta(days=6))
    )
    from app.db.repositories_market import upsert_coverage

    await upsert_coverage(db_session, "FAKEW", requested_span="max")
    await _age_coverage(db_session, "FAKEW", days=7)

    await bar_store.daily_bars_for("FAKEW", market_today() - timedelta(days=30), session=db_session)

    assert spans == ["max", "max"]
    coverage = (await get_coverage(db_session, ["FAKEW"]))["FAKEW"]
    assert coverage.requested_span == "max"
