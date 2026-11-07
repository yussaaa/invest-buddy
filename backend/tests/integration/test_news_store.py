"""News headline store against a real PostgreSQL (rolled back per test),
and the runner's lease against a real Redis."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import update

from app.db.models import NewsHeadline
from app.db.repositories_news import (
    insert_headlines,
    last_published,
    latest,
    needing_symbol_info,
    prune,
    recently_changed,
    set_symbol_info,
)
from app.db.repositories_settings import all_settings, set_setting
from app.services.news.models import Headline, headline_id

pytestmark = pytest.mark.integration

NOW = datetime.now(timezone.utc)


def headline(source_id: str, *, source="fed", minutes_ago=0, symbols=(), category="news") -> dict:
    return Headline(
        id=headline_id(source, f"zztest-{source_id}"), source=source,
        source_id=f"zztest-{source_id}", title=f"ZZTEST headline {source_id}",
        published_at=NOW - timedelta(minutes=minutes_ago), symbols=list(symbols),
        category=category,
    ).model_dump()


async def test_a_repeat_is_a_no_op_and_reported_as_such(db_session):
    first = await insert_headlines(db_session, [headline("a"), headline("b")])
    again = await insert_headlines(db_session, [headline("a"), headline("c")])
    assert len(first) == 2
    assert again == [headline("c")["id"]]


async def test_duplicates_within_one_batch_are_collapsed(db_session):
    assert len(await insert_headlines(db_session, [headline("a"), headline("a")])) == 1


async def test_latest_filters_by_source_category_and_symbol(db_session):
    await insert_headlines(db_session, [
        headline("e1", source="edgar", symbols=["ZZTG"], category="earnings", minutes_ago=1),
        headline("f1", source="fed", category="monetary_policy", minutes_ago=2),
        headline("a1", source="alpaca", symbols=["ZZTG", "ZZQ"], minutes_ago=3),
    ])
    ids = lambda rows: [r.source_id for r in rows if r.source_id.startswith("zztest")]  # noqa: E731

    assert ids(await latest(db_session, symbol="zztg")) == ["zztest-e1", "zztest-a1"]
    assert ids(await latest(db_session, sources=["fed"])) == ["zztest-f1"]
    assert ids(await latest(db_session, categories=["earnings"])) == ["zztest-e1"]
    page = await latest(db_session, before=NOW - timedelta(minutes=1, seconds=30), symbol="ZZTG")
    assert ids(page) == ["zztest-a1"]


async def test_recently_changed_follows_updated_at_not_published_at(db_session):
    await insert_headlines(db_session, [headline("old", minutes_ago=600), headline("new")])
    old_id = headline("old")["id"]
    # A later stage (triage) restamps the old headline.
    await db_session.execute(
        update(NewsHeadline).where(NewsHeadline.id == old_id)
        .values(importance=4, updated_at=NOW + timedelta(seconds=5))
    )
    rows = await recently_changed(db_session, limit=5)
    assert rows[0].id == old_id and rows[0].importance == 4


async def test_last_published_is_per_source(db_session):
    await insert_headlines(db_session, [
        headline("x", source="alpaca", minutes_ago=0),
        headline("y", source="alpaca", minutes_ago=30),
    ])
    assert abs((await last_published(db_session, "alpaca")) - NOW) < timedelta(seconds=1)


async def test_prune_drops_only_old_headlines(db_session):
    await insert_headlines(db_session, [headline("ancient", minutes_ago=60 * 24 * 40),
                                        headline("recent")])
    assert await prune(db_session) >= 1
    remaining = {r.source_id for r in await latest(db_session, limit=300)}
    assert "zztest-recent" in remaining and "zztest-ancient" not in remaining


async def test_the_lease_is_owned_renewable_and_reclaimable():
    from app.cache import redis_client

    await redis_client.cache_get("zztest:probe")      # trips the breaker if Redis is down
    if not redis_client.is_available():
        pytest.skip("Redis is unavailable")
    key, mine, theirs = f"zztest:lease:{uuid.uuid4().hex}", "mine", "theirs"
    try:
        assert await redis_client.acquire_lease(key, mine, 30) is True
        assert await redis_client.acquire_lease(key, theirs, 30) is False
        assert await redis_client.renew_lease(key, mine, 30) is True
        assert await redis_client.renew_lease(key, theirs, 30) is False

        await redis_client.release_lease(key, theirs)          # not theirs to drop
        assert await redis_client.renew_lease(key, mine, 30) is True

        await redis_client.release_lease(key, mine)
        assert await redis_client.renew_lease(key, theirs, 30) is True  # lapsed → reclaimed
        assert await redis_client.renew_lease(key, mine, 30) is False
    finally:
        await redis_client.cache_delete(key)


async def test_app_settings_upsert_and_clear(db_session):
    await set_setting(db_session, "zztest_key", "first")
    await set_setting(db_session, "zztest_key", "second")
    assert (await all_settings(db_session))["zztest_key"] == "second"
    await set_setting(db_session, "zztest_key", "")
    assert "zztest_key" not in await all_settings(db_session)


async def test_symbol_info_is_wanted_once_then_restamps_the_row(db_session):
    await insert_headlines(db_session, [
        headline("named", symbols=["ZZTG"]), headline("bare"),
        headline("stale", symbols=["ZZQ"], minutes_ago=60 * 24 * 5),
    ])
    since = NOW - timedelta(days=3)
    ids = lambda rows: {r.source_id for r in rows if r.source_id.startswith("zztest")}  # noqa: E731
    assert ids(await needing_symbol_info(db_session, since=since)) == {"zztest-named"}

    named = headline("named")["id"]
    before = (await recently_changed(db_session, limit=300))
    stamp = next(r.updated_at for r in before if r.id == named)
    info = {"ZZTG": {"sector": "Technology", "industry": "Semiconductors"}}
    await set_symbol_info(db_session, {named: info})
    db_session.expire_all()

    assert ids(await needing_symbol_info(db_session, since=since)) == set()
    row = next(r for r in await recently_changed(db_session, limit=300) if r.id == named)
    assert row.symbol_info == info and row.updated_at >= stamp
