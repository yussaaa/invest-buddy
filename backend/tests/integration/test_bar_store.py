"""Integration tests for the bar store facade — needs a real PostgreSQL.

These stub the provider so they don't depend on the network; the DB half is
real.
"""

from __future__ import annotations

from datetime import timedelta

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
