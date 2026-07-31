"""Integration tests for the daily bar store — needs a real PostgreSQL.

Run with `make test-all`, or point TEST_DATABASE_URL at a scratch database.
`make test` skips these by path so the unit suite stays offline.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from app.db.models import DailyBar
from app.db.repositories_market import (
    DailyBarInput,
    delete_symbol_bars,
    get_coverage,
    get_daily_bars,
    get_daily_closes,
    get_prev_closes,
    last_settled_session,
    market_today,
    upsert_coverage,
    upsert_daily_bars,
)
from sqlalchemy import select

pytestmark = pytest.mark.integration


def _bars(symbol: str, days: int, start_close: float = 100.0) -> list[DailyBarInput]:
    """`days` settled sessions ending yesterday."""
    yesterday = market_today() - timedelta(days=1)
    return [
        DailyBarInput(
            symbol=symbol,
            bar_date=yesterday - timedelta(days=i),
            open=start_close + i,
            high=start_close + i + 1,
            low=start_close + i - 1,
            close=start_close + i,
            adj_close=start_close + i - 0.5,
            volume=1_000_000 + i,
        )
        for i in range(days)
    ]


async def test_upsert_is_idempotent(db_session):
    bars = _bars("TEST1", 10)

    assert await upsert_daily_bars(db_session, bars) == 10
    assert await upsert_daily_bars(db_session, bars) == 10

    stored = await get_daily_bars(
        db_session, "TEST1", market_today() - timedelta(days=30), market_today()
    )
    assert len(stored) == 10, "re-upserting the same bars must not duplicate rows"


async def test_conflict_updates_changed_values(db_session):
    await upsert_daily_bars(db_session, _bars("TEST2", 3))

    day = market_today() - timedelta(days=1)
    await upsert_daily_bars(
        db_session,
        [DailyBarInput(symbol="TEST2", bar_date=day, close=999.0, adj_close=998.0)],
    )

    row = (
        await db_session.execute(
            select(DailyBar).where(DailyBar.symbol == "TEST2", DailyBar.bar_date == day)
        )
    ).scalar_one()
    assert row.close == 999.0
    assert row.adj_close == 998.0


async def test_todays_bar_is_never_stored(db_session):
    """A partial session must not reach the store — it would be served as final."""
    bars = _bars("TEST3", 3) + [
        DailyBarInput(symbol="TEST3", bar_date=market_today(), close=1234.0)
    ]

    written = await upsert_daily_bars(db_session, bars)

    assert written == 3, "today's in-progress bar should have been dropped"
    stored = await get_daily_bars(
        db_session, "TEST3", market_today() - timedelta(days=30), market_today()
    )
    assert all(b.bar_date < market_today() for b in stored)


async def test_get_daily_closes_groups_by_symbol(db_session):
    for sym in ("TESTA", "TESTB", "TESTC"):
        await upsert_daily_bars(db_session, _bars(sym, 10))

    closes = await get_daily_closes(
        db_session,
        ["TESTA", "TESTB", "TESTC"],
        market_today() - timedelta(days=30),
        market_today(),
    )

    assert set(closes) == {"TESTA", "TESTB", "TESTC"}
    assert all(len(v) == 10 for v in closes.values())
    for series in closes.values():
        dates = [d for d, _ in series]
        assert dates == sorted(dates), "series must come back oldest first"


async def test_get_prev_closes_returns_latest_settled(db_session):
    await upsert_daily_bars(db_session, _bars("TEST4", 5, start_close=50.0))

    prev = await get_prev_closes(db_session, ["TEST4"])

    # _bars counts backwards from yesterday, so the newest bar is start_close.
    assert prev["TEST4"] == 50.0


async def test_last_settled_session_uses_reference_symbol(db_session):
    await upsert_daily_bars(db_session, _bars("SPY", 5))

    settled = await last_settled_session(db_session, reference="SPY")

    assert settled == market_today() - timedelta(days=1)
    assert settled < market_today()


async def test_coverage_reflects_stored_rows(db_session):
    await upsert_daily_bars(db_session, _bars("TEST5", 7))
    await upsert_coverage(db_session, "TEST5", requested_span="2y")

    coverage = (await get_coverage(db_session, ["TEST5"]))["TEST5"]

    assert coverage.bar_count == 7
    assert coverage.last_bar_date == market_today() - timedelta(days=1)
    assert coverage.first_bar_date == market_today() - timedelta(days=7)
    assert coverage.requested_span == "2y"


async def test_large_batch_spans_chunks(db_session):
    """5000 rows exceeds the chunk size and the 65k bind-parameter ceiling."""
    written = await upsert_daily_bars(db_session, _bars("TEST6", 5000))

    assert written == 5000
    stored = await get_daily_bars(
        db_session, "TEST6", date(1990, 1, 1), market_today()
    )
    assert len(stored) == 5000


async def test_delete_symbol_bars(db_session):
    await upsert_daily_bars(db_session, _bars("TEST7", 4))

    removed = await delete_symbol_bars(db_session, "TEST7")

    assert removed == 4
    assert await get_daily_bars(db_session, "TEST7", date(1990, 1, 1), market_today()) == []
