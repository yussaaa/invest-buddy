"""The news wire's ingest loop — one replica holds the sources open.

Started from the app's lifespan, beside warm-up rather than inside it: warm-up
wakes every ten minutes, and a wire that is ten minutes late is not a wire.

* **One leader.** Alpaca allows a free account a single stream connection,
  and polling EDGAR from every replica would only multiply requests the SEC
  rate-limits. The leader holds a renewable Redis lease; if it stops renewing,
  another replica takes over within a lease period. With Redis down every
  replica ingests — inserts are idempotent, so that costs duplicate requests,
  never duplicate rows — and Alpaca refuses the extra streams, which then fall
  back to REST polling.
* **Each source fails alone.** Sources run as sibling tasks; a poll that
  raises is logged and retried on its interval, and only losing the lease
  stops the group.
* **Ingest first, enrich after.** Parsing and storing, no model calls and no
  per-symbol lookups in the way of a headline. Sector/industry
  (classify.py), and later triage and explanation, read from the table on
  their own schedule and restamp the row, so nothing slow backs up the wire.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timedelta, timezone

import structlog

from app.cache import redis_client
from app.config import get_settings
from app.observability.metrics import NEWS_HEADLINES_INGESTED_TOTAL, NEWS_POLLS_TOTAL
from app.services import app_settings, news
from app.services.news.alpaca import StreamRefused
from app.services.news.models import Batch, Headline

log = structlog.get_logger(__name__)

LEASE_KEY = "news:runner"
LEASE_TTL = 60.0
RENEW_EVERY = 20.0
CATCH_UP_MAX = timedelta(hours=24)
# Without a stream (keys missing, or the one connection taken), Alpaca is polled.
ALPACA_POLL_SECONDS = 60.0
STREAM_BACKOFF_MAX = 120.0
FATAL_RETRY_SECONDS = 600.0
PRUNE_EVERY = 6 * 3600.0
# Sector/industry fill-in: how often, how far back, and how many new symbols
# one pass may look up (each is a Yahoo request; the first pass after a
# backfill would otherwise be hundreds at once).
CLASSIFY_EVERY = 15.0
CLASSIFY_WINDOW = timedelta(days=3)
CLASSIFY_MAX_SYMBOLS = 40


class LeaseLost(Exception):
    pass


async def store(headlines: list[Headline]) -> int:
    """Insert what is new; returns how many were.

    Nothing older than the retention window is stored: the Fed's feed lists
    releases from months back, and storing them would have prune delete them
    and the next poll re-insert them as "new" rows, flashing on the wire.
    """
    from app.db.repositories_news import RETENTION_DAYS, insert_headlines

    floor = datetime.now(timezone.utc) - timedelta(days=RETENTION_DAYS)
    headlines = [h for h in headlines if h.published_at >= floor]
    if not headlines:
        return 0
    from app.db.session import AsyncSessionLocal

    async with AsyncSessionLocal() as session:
        inserted = await insert_headlines(session, [h.model_dump() for h in headlines])
        await session.commit()
    if inserted:
        NEWS_HEADLINES_INGESTED_TOTAL.labels(source=headlines[0].source).inc(len(inserted))
    return len(inserted)


async def store_batch(batch: Batch) -> int:
    NEWS_POLLS_TOTAL.labels(source=batch.source,
                            outcome="ok" if batch.available else "unavailable").inc()
    return await store(batch.headlines)


async def poll_forever(provider, interval: float) -> None:
    while True:
        try:
            # A setting saved on another replica (the SEC contact) lands here.
            await app_settings.refresh()
            written = await store_batch(await provider.poll())
            if written:
                log.info("news_ingested", source=provider.id, new=written)
        except Exception as e:
            NEWS_POLLS_TOTAL.labels(source=provider.id, outcome="error").inc()
            log.warning("news_poll_failed", source=provider.id, error=str(e)[:200])
        await asyncio.sleep(interval)


async def _catch_up_alpaca() -> None:
    from app.db.repositories_news import last_published
    from app.db.session import AsyncSessionLocal

    floor = datetime.now(timezone.utc) - CATCH_UP_MAX
    async with AsyncSessionLocal() as session:
        last = await last_published(session, "alpaca")
    written = await store_batch(await news.ALPACA.fetch_since(max(last or floor, floor)))
    if written:
        log.info("news_ingested", source="alpaca", new=written, via="rest")


async def run_alpaca() -> None:
    """Stream when we can, catch up over REST every time we (re)connect."""
    provider = news.ALPACA
    backoff = 2.0
    while True:
        if provider.missing_config():
            await asyncio.sleep(FATAL_RETRY_SECONDS)  # keys may be added; status says why
            continue
        try:
            await _catch_up_alpaca()
            async for headlines in provider.stream():
                backoff = 2.0
                written = await store(headlines)
                if written:
                    log.info("news_ingested", source="alpaca", new=written, via="stream")
            NEWS_POLLS_TOTAL.labels(source="alpaca", outcome="ok").inc()
        except StreamRefused as e:
            NEWS_POLLS_TOTAL.labels(source="alpaca", outcome="unavailable").inc()
            provider.last_error = f"Alpaca refused the news stream ({e})."
            log.warning("news_alpaca_refused", error=str(e), fatal=e.fatal)
            if e.fatal:
                # Bad keys, no entitlement, or the one connection is in use:
                # poll REST instead, and try the stream again later.
                await _poll_alpaca_for(FATAL_RETRY_SECONDS)
                continue
        except asyncio.CancelledError:
            raise
        except Exception as e:
            NEWS_POLLS_TOTAL.labels(source="alpaca", outcome="error").inc()
            log.warning("news_alpaca_stream_dropped", error=str(e)[:200])
        await asyncio.sleep(backoff)
        backoff = min(backoff * 2, STREAM_BACKOFF_MAX)


async def _poll_alpaca_for(seconds: float) -> None:
    deadline = asyncio.get_running_loop().time() + seconds
    while asyncio.get_running_loop().time() < deadline:
        try:
            await _catch_up_alpaca()
        except Exception as e:
            log.warning("news_alpaca_poll_failed", error=str(e)[:200])
        await asyncio.sleep(ALPACA_POLL_SECONDS)


async def classify_pending() -> int:
    """Fill sector/industry on recent headlines that lack it; returns rows filled.

    A row is filled only once every symbol it names has a final answer, so a
    failed lookup leaves the row for the next pass rather than half-done.
    """
    from app.db.repositories_news import needing_symbol_info, set_symbol_info
    from app.db.session import AsyncSessionLocal
    from app.services.news.classify import classify

    async with AsyncSessionLocal() as session:
        rows = await needing_symbol_info(
            session, since=datetime.now(timezone.utc) - CLASSIFY_WINDOW
        )
        wanted = [(r.id, list(r.symbols)) for r in rows]
    if not wanted:
        return 0

    symbols = list(dict.fromkeys(s for _, syms in wanted for s in syms))[:CLASSIFY_MAX_SYMBOLS]
    known = await classify(symbols)
    filled = {hid: {s: known[s] for s in syms}
              for hid, syms in wanted if all(s in known for s in syms)}
    if filled:
        async with AsyncSessionLocal() as session:
            await set_symbol_info(session, filled)
            await session.commit()
    return len(filled)


async def _classify_forever() -> None:
    while True:
        try:
            filled = await classify_pending()
            if filled:
                log.info("news_classified", rows=filled)
        except Exception as e:
            log.warning("news_classify_failed", error=str(e)[:200])
        await asyncio.sleep(CLASSIFY_EVERY)


async def _prune_forever() -> None:
    from app.db.repositories_news import prune
    from app.db.session import AsyncSessionLocal

    while True:
        try:
            async with AsyncSessionLocal() as session:
                pruned = await prune(session)
                await session.commit()
            if pruned:
                log.info("news_pruned", rows=pruned)
        except Exception as e:
            log.warning("news_prune_failed", error=str(e)[:200])
        await asyncio.sleep(PRUNE_EVERY)


async def _hold_lease(token: str) -> None:
    while True:
        await asyncio.sleep(RENEW_EVERY)
        if await redis_client.renew_lease(LEASE_KEY, token, LEASE_TTL) is False:
            raise LeaseLost()


async def lead(token: str) -> None:
    """Run every source until the lease is lost (or the app shuts down)."""
    settings = get_settings()
    async with asyncio.TaskGroup() as group:
        group.create_task(_hold_lease(token), name="news-lease")
        group.create_task(poll_forever(news.EDGAR, settings.news_edgar_poll_seconds),
                          name="news-edgar")
        group.create_task(poll_forever(news.FED, settings.news_fed_poll_seconds),
                          name="news-fed")
        group.create_task(run_alpaca(), name="news-alpaca")
        group.create_task(_classify_forever(), name="news-classify")
        group.create_task(_prune_forever(), name="news-prune")


async def run_news_loop() -> None:
    token = uuid.uuid4().hex
    log.info("news_runner_started")
    while True:
        try:
            # None means Redis is unreachable: lead anyway (see module docstring).
            if await redis_client.acquire_lease(LEASE_KEY, token, LEASE_TTL) is not False:
                log.info("news_runner_leading")
                try:
                    await lead(token)
                except* LeaseLost:
                    log.info("news_runner_lease_lost")
        except asyncio.CancelledError:
            await redis_client.release_lease(LEASE_KEY, token)
            raise
        except Exception as e:
            first = e.exceptions[0] if isinstance(e, BaseExceptionGroup) else e
            log.warning("news_runner_failed", error=str(first)[:200])
        await asyncio.sleep(RENEW_EVERY)
