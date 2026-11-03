"""Mention snapshot store against a real PostgreSQL (rolled back per test)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.db.repositories_social import hour_bucket, prune, snapshots_for, upsert_snapshots
from app.services.social.history import daily_series

pytestmark = pytest.mark.integration

ROWS = [{"symbol": "ZZTEST", "rank": 3, "mentions": 120, "upvotes": 40},
        {"symbol": "ZZTEST2", "rank": 9, "mentions": 30, "upvotes": None}]


async def test_a_repeat_inside_the_hour_is_a_no_op(db_session):
    now = datetime.now(timezone.utc)
    await upsert_snapshots(db_session, "apewisdom", ROWS, captured_at=now)
    await upsert_snapshots(db_session, "apewisdom", ROWS, captured_at=now + timedelta(seconds=5))

    stored = await snapshots_for(db_session, "apewisdom", ["ZZTEST"], days=2)
    assert len(stored["ZZTEST"]) == 1


async def test_duplicate_symbols_in_one_batch_are_collapsed(db_session):
    rows = ROWS + [{"symbol": "ZZTEST", "rank": 4, "mentions": 119}]
    assert await upsert_snapshots(db_session, "apewisdom", rows) == 2


async def test_history_reads_back_as_a_daily_series(db_session):
    base = hour_bucket(datetime.now(timezone.utc)) - timedelta(days=3)
    for day in range(3):
        for hour in (0, 6):
            await upsert_snapshots(db_session, "apewisdom",
                                   [{"symbol": "ZZTEST", "rank": 5, "mentions": 10 * day + hour}],
                                   captured_at=base + timedelta(days=day, hours=hour))
    stored = await snapshots_for(db_session, "apewisdom", ["zztest"], days=5)
    series = daily_series(stored["ZZTEST"])
    assert 3 <= len(series) <= 4                 # New York dates, not UTC ones


async def test_prune_drops_only_old_buckets(db_session):
    old = datetime.now(timezone.utc) - timedelta(days=120)
    await upsert_snapshots(db_session, "apewisdom", ROWS[:1], captured_at=old)
    await upsert_snapshots(db_session, "apewisdom", ROWS[1:])
    assert await prune(db_session) >= 1
    remaining = await snapshots_for(db_session, "apewisdom", ["ZZTEST", "ZZTEST2"], days=200)
    assert "ZZTEST" not in remaining and "ZZTEST2" in remaining
