"""Background warm-up: pay the slow fetches before a visitor has to.

Two jobs, run from the app's lifespan rather than a scheduler — the stack has
none, and neither job is worth adding one for:

* **Bar backfill.** The bar store warms lazily, so the first chart of a cold
  ticker pays full provider latency, and the overview/breadth store reads are
  only as good as what has been backfilled. Once at startup and once each
  evening after the close, this walks the symbols the UI actually shows.
* **Earnings calendar refresh-ahead.** The movers calendar costs ~36s cold and
  expires every 6h. Refreshing it at 85% of its TTL means nobody ever waits.

With several replicas only one should do the work, so each job takes a Redis
lock for its interval. When Redis is unavailable every replica runs both —
upserts are idempotent and the calendar is one key, so that costs duplicate
provider calls, never wrong data.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, time as dtime, timedelta

import structlog

from app.cache import redis_client
from app.db.repositories_market import MARKET_TZ, market_today

log = structlog.get_logger(__name__)

TICK_SECONDS = 600                  # how often the loop wakes
BACKFILL_AFTER = dtime(16, 30)      # NY time — settled bars are published by then
BACKFILL_CONCURRENCY = 4
CORE_SPAN = "5y"                    # what technicals asks for
WIDE_SPAN = "1y"                    # breadth constituents only need recent closes

_last_backfill: datetime | None = None


async def _leader(job: str, ttl: float) -> bool:
    """True if this replica should run `job` now."""
    if not redis_client.is_available():
        return True
    return await redis_client.try_lock(f"warmup:{job}", ttl)


async def _watchlist_symbols() -> list[str]:
    try:
        from sqlalchemy import select

        from app.db.models import Watchlist
        from app.db.session import AsyncSessionLocal

        async with AsyncSessionLocal() as session:
            rows = (await session.execute(select(Watchlist.tickers))).scalars().all()
        return sorted({t.upper() for tickers in rows for t in (tickers or [])})
    except Exception as e:
        log.warning("warmup_watchlists_unavailable", error=str(e))
        return []


async def _breadth_symbols() -> list[str]:
    from app.services import market_data as md

    symbols: set[str] = set()
    for key in md.BREADTH_INDICES:
        try:
            members = await md._cached(
                f"constituents:{key}",
                md.CONSTITUENTS_TTL,
                lambda k=key: md._constituents_with_fallback(k),
            )
            symbols.update(m["symbol"] for m in members)
        except Exception as e:
            log.warning("warmup_constituents_failed", index=key, error=str(e))
    return sorted(symbols)


async def backfill_universe() -> list[tuple[str, str]]:
    """(symbol, span) pairs, most important first.

    SPY leads because the store uses it as its reference session; the rest are
    what the dashboard shows without being asked.
    """
    from app.services import market_data as md

    core = ["SPY"]
    core += [g["symbol"] for g in md.INDICES + md.SECTORS + md.MACRO]
    core += md.EARNINGS_UNIVERSE
    core += await _watchlist_symbols()
    core = list(dict.fromkeys(s.upper() for s in core))

    wide = [s for s in await _breadth_symbols() if s not in set(core)]
    return [(s, CORE_SPAN) for s in core] + [(s, WIDE_SPAN) for s in wide]


async def run_backfill() -> dict:
    from app.services import bar_store

    universe = await backfill_universe()
    gate = asyncio.Semaphore(BACKFILL_CONCURRENCY)
    failed: list[str] = []
    today = market_today()

    async def one(symbol: str, span: str) -> None:
        start = today - timedelta(days=365 * (5 if span == "5y" else 1))
        async with gate:
            try:
                await bar_store.daily_bars_for(symbol, start, span=span)
            except Exception as e:
                failed.append(symbol)
                log.warning("warmup_backfill_symbol_failed", symbol=symbol, error=str(e))

    started = datetime.now(MARKET_TZ)
    await asyncio.gather(*(one(s, span) for s, span in universe))
    summary = {
        "symbols": len(universe),
        "failed": len(failed),
        "seconds": round((datetime.now(MARKET_TZ) - started).total_seconds(), 1),
    }
    log.info("warmup_backfill_done", **summary)
    return summary


def backfill_due(now: datetime, last: datetime | None) -> bool:
    """At startup, then once per evening after the close.

    Due when we are past today's cutoff and the last run was before it — so a
    run at 09:00 is followed by one at 16:30, and nothing more until tomorrow.
    """
    if last is None:
        return True
    cutoff = datetime.combine(now.date(), BACKFILL_AFTER, tzinfo=now.tzinfo)
    return now >= cutoff > last


async def tick() -> None:
    """One pass of every job. Each job fails on its own."""
    global _last_backfill
    from app.services import market_data as md

    try:
        if await _leader("earnings-calendar", TICK_SECONDS):
            if await md.refresh_earnings_calendar():
                log.info("warmup_earnings_calendar_refreshed")
    except Exception as e:
        log.warning("warmup_earnings_calendar_failed", error=str(e))

    now = datetime.now(MARKET_TZ)
    if backfill_due(now, _last_backfill):
        _last_backfill = now
        try:
            if await _leader("backfill", 3600):
                await run_backfill()
        except Exception as e:
            log.warning("warmup_backfill_failed", error=str(e))


async def run_warmup_loop() -> None:
    log.info("warmup_started", tick_seconds=TICK_SECONDS)
    while True:
        await tick()
        await asyncio.sleep(TICK_SECONDS)
