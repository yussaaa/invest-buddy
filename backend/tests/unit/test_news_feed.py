"""News feed, stream diffing, the HTTP contract, and the runner's lease.

No database and no network: the repository reads are patched.
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from typing import AsyncIterator

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.api.v1 import news as news_api
from app.services.news import feed, runner


def row(id: str, stamp: str, published: str = "2026-11-06T13:00:00+00:00", **extra) -> dict:
    return {"id": id, "updated_at": stamp, "published_at": published, "title": id,
            "source": "fed", "symbols": [], **extra}


@pytest.fixture(autouse=True)
def fresh_snapshot():
    feed._changes = (0.0, [])
    yield
    feed._changes = (0.0, [])


@pytest.fixture(autouse=True)
def no_settings_db(monkeypatch):
    """Polls and /sources re-read app settings; keep that off the database."""
    from app.services import app_settings

    async def nothing(force=False):
        return None

    monkeypatch.setattr(app_settings, "refresh", nothing)
    monkeypatch.setattr(app_settings, "_values", {})


# ── diff ──────────────────────────────────────────────────────────────────────


def test_diff_sends_new_rows_and_rows_restamped_by_a_later_stage():
    sent: dict[str, str] = {}
    first = [row("a", "t1"), row("b", "t1")]
    assert [r["id"] for r in feed.diff(first, sent)] == ["a", "b"]
    assert feed.diff(first, sent) == []                              # nothing changed
    triaged = [row("a", "t2"), row("b", "t1")]
    assert [r["id"] for r in feed.diff(triaged, sent)] == ["a"]      # triage restamped a


def test_diff_forgets_rows_that_left_the_window():
    sent: dict[str, str] = {}
    feed.diff([row("a", "t1"), row("b", "t1")], sent)
    feed.diff([row("b", "t1")], sent)
    assert set(sent) == {"b"}


# ── stream ────────────────────────────────────────────────────────────────────


async def collect(since, frames: list[list[dict]], ticks: int) -> list[tuple[str, dict]]:
    """Drive stream_changes over a scripted sequence of snapshots."""
    snapshots = iter(frames)
    remaining = {"n": ticks}

    async def changes():
        return next(snapshots)

    async def disconnected():
        remaining["n"] -= 1
        return remaining["n"] < 0

    out = []
    original = feed.recent_changes
    feed.recent_changes = changes
    try:
        async for event in feed.stream_changes(since, disconnected, poll_seconds=0):
            out.append(event)
    finally:
        feed.recent_changes = original
    return out


async def test_stream_without_since_starts_from_now():
    events = await collect(None, [[row("a", "2026-11-06T13:00:00+00:00")],
                                  [row("a", "2026-11-06T13:00:00+00:00"),
                                   row("b", "2026-11-06T13:00:05+00:00")]], ticks=2)
    assert [e for e, _ in events] == ["ready", "headlines"]
    assert [r["id"] for r in events[1][1]["headlines"]] == ["b"]


async def test_stream_replays_only_what_the_client_has_not_seen():
    since = datetime(2026, 11, 6, 13, 0, 2, tzinfo=timezone.utc)
    events = await collect(since, [[row("old", "2026-11-06T13:00:00+00:00"),
                                    row("new", "2026-11-06T13:00:04+00:00")]], ticks=1)
    assert events[0][0] == "ready"
    assert [r["id"] for r in events[1][1]["headlines"]] == ["new"]


async def test_stream_orders_each_batch_newest_published_first():
    since = datetime(2026, 11, 6, tzinfo=timezone.utc)
    events = await collect(since, [[
        row("older", "2026-11-06T13:00:04+00:00", published="2026-11-06T12:00:00+00:00"),
        row("newer", "2026-11-06T13:00:03+00:00", published="2026-11-06T12:30:00+00:00"),
    ]], ticks=1)
    assert [r["id"] for r in events[1][1]["headlines"]] == ["newer", "older"]


async def test_one_snapshot_serves_every_stream_in_the_process(monkeypatch):
    loads = {"n": 0}

    async def load():
        loads["n"] += 1
        await asyncio.sleep(0.01)
        return [row("a", "t1")]

    monkeypatch.setattr(feed, "_load_changes", load)
    await asyncio.gather(*(feed.recent_changes() for _ in range(20)))
    assert loads["n"] == 1


# ── HTTP contract ─────────────────────────────────────────────────────────────


@pytest_asyncio.fixture
async def client() -> AsyncIterator[AsyncClient]:
    app = FastAPI()
    app.include_router(news_api.router, prefix="/news")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c


async def test_feed_passes_filters_through(client, monkeypatch):
    seen = {}

    async def fake_feed(**kwargs):
        seen.update(kwargs)
        return {"headlines": [], "as_of": "now"}

    monkeypatch.setattr(feed, "get_feed", fake_feed)
    body = (await client.get("/news?limit=5&source=edgar,fed&category=earnings&symbol=nvda")).json()
    assert body == {"headlines": [], "as_of": "now"}
    assert seen == {"limit": 5, "before": None, "sources": ["edgar", "fed"],
                    "categories": ["earnings"], "symbol": "nvda"}


async def test_feed_rejects_an_unbounded_limit(client):
    assert (await client.get("/news?limit=5000")).status_code == 422


async def test_a_database_failure_is_a_sentence_not_a_500(client, monkeypatch):
    async def broken(session, **kwargs):
        raise ConnectionRefusedError("db down")

    import app.db.repositories_news as repo

    monkeypatch.setattr(repo, "latest", broken)
    response = await client.get("/news")
    assert response.status_code == 200
    assert response.json()["error"] and response.json()["headlines"] == []


async def test_sources_lists_every_provider_with_a_state(client):
    body = (await client.get("/news/sources")).json()
    assert {s["id"] for s in body["sources"]} == {"alpaca", "edgar", "fed"}
    assert all(s["state"] in {"live", "blocked", "disabled", "not_configured"}
               for s in body["sources"])
    assert "earnings" in body["categories"] and body["disclaimer"]


async def test_stream_endpoint_speaks_sse(client, monkeypatch):
    async def fake_stream(since, is_disconnected):
        yield "ready", {"as_of": "now"}
        yield "headlines", {"headlines": [row("a", "t1")]}

    monkeypatch.setattr(feed, "stream_changes", fake_stream)
    async with client.stream("GET", "/news/stream") as response:
        assert response.headers["content-type"].startswith("text/event-stream")
        text = "".join([chunk async for chunk in response.aiter_text()])
    frames = [f for f in text.replace("\r\n", "\n").split("\n\n") if "event:" in f]
    assert [f.split("\n")[0] for f in frames] == ["event: ready", "event: headlines"]
    assert json.loads(frames[1].split("data: ", 1)[1])["headlines"][0]["id"] == "a"


# ── runner ────────────────────────────────────────────────────────────────────


async def test_losing_the_lease_stops_every_source(monkeypatch):
    started: list[str] = []

    async def forever(name):
        started.append(name)
        await asyncio.sleep(3600)

    async def lost(key, token, ttl):
        return False

    monkeypatch.setattr(runner, "RENEW_EVERY", 0)
    monkeypatch.setattr(runner.redis_client, "renew_lease", lost)
    monkeypatch.setattr(runner, "poll_forever", lambda provider, interval: forever(provider.id))
    monkeypatch.setattr(runner, "run_alpaca", lambda: forever("alpaca"))
    monkeypatch.setattr(runner, "_prune_forever", lambda: forever("prune"))
    monkeypatch.setattr(runner, "_classify_forever", lambda: forever("classify"))

    with pytest.raises(BaseExceptionGroup) as raised:
        await asyncio.wait_for(runner.lead("token"), timeout=2)
    assert raised.group_contains(runner.LeaseLost)
    assert set(started) == {"edgar", "fed", "alpaca", "prune", "classify"}


async def test_an_unreachable_redis_does_not_stop_the_leader(monkeypatch):
    async def unknown(key, token, ttl):
        return None

    monkeypatch.setattr(runner, "RENEW_EVERY", 0)
    monkeypatch.setattr(runner.redis_client, "renew_lease", unknown)
    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(runner._hold_lease("token"), timeout=0.05)


async def test_a_failing_poll_is_retried_not_fatal(monkeypatch):
    calls = {"n": 0}

    class Flaky:
        id = "fed"

        async def poll(self):
            calls["n"] += 1
            raise RuntimeError("boom")

    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(runner.poll_forever(Flaky(), interval=0), timeout=0.05)
    assert calls["n"] > 1


async def test_nothing_older_than_retention_is_stored(monkeypatch):
    """Else prune deletes it and the next poll re-inserts it as "new"."""
    from datetime import timedelta

    import app.db.repositories_news as repo
    from app.services.news.models import Headline

    now = datetime.now(timezone.utc)

    def make(days_ago: int) -> Headline:
        return Headline(id=f"h{days_ago}", source="fed", source_id=str(days_ago), title="t",
                        published_at=now - timedelta(days=days_ago))

    seen: list[str] = []

    async def insert(session, rows):
        seen.extend(r["id"] for r in rows)
        return []

    monkeypatch.setattr(repo, "insert_headlines", insert)
    assert await runner.store([make(repo.RETENTION_DAYS + 5)]) == 0
    assert seen == []                                   # never touched the database
    await runner.store([make(1), make(repo.RETENTION_DAYS + 5)])
    assert seen == ["h1"]
