"""Read daily bars from the local store, falling back to the provider.

The contract callers care about: ask for a window, get bars. Whether they came
from Postgres or from a live download is this module's problem.

Postgres being unavailable degrades to the live path rather than failing — the
store is an optimisation, never a dependency.
"""

from __future__ import annotations

import asyncio
from datetime import date, timedelta

import structlog
import yfinance as yf
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.repositories_market import (
    MARKET_TZ,
    DailyBarInput,
    get_coverage,
    get_daily_bars,
    last_settled_session,
    market_today,
    upsert_coverage,
    upsert_daily_bars,
)
from app.db.session import AsyncSessionLocal
from app.services.bar_derive import BarRow, adjust

log = structlog.get_logger(__name__)

# How much history a first-time fetch pulls. Long enough for a 250-day moving
# average plus room to look back a couple of years on a chart.
DEFAULT_SPAN = "2y"

# Spans that satisfy a request reaching further back than we normally store.
FULL_SPANS = {"max"}


def _to_rows(hist, symbol: str) -> tuple[list[BarRow], bool]:
    """(bars, saw_split) from a yfinance frame.

    Dates come from the exchange-local index via `.date()`. Converting to UTC
    first would shift every bar by a day for US sessions.
    """
    rows: list[BarRow] = []
    saw_split = False

    for idx, row in hist.iterrows():
        close = row.get("Close")
        if close is None or close != close:  # NaN
            continue
        if float(row.get("Stock Splits", 0) or 0) != 0:
            saw_split = True

        volume = row.get("Volume")
        rows.append(
            BarRow(
                bar_date=idx.date(),
                open=_f(row.get("Open")),
                high=_f(row.get("High")),
                low=_f(row.get("Low")),
                close=float(close),
                adj_close=_f(row.get("Adj Close")),
                volume=int(volume) if volume == volume and volume is not None else None,
            )
        )
    return rows, saw_split


def _f(value) -> float | None:
    if value is None or value != value:  # NaN
        return None
    return float(value)


def fetch_live(symbol: str, span: str = DEFAULT_SPAN) -> tuple[list[BarRow], bool]:
    """Blocking download of daily bars. Returns (bars, saw_split).

    `actions=True` brings splits back in the same request, so detecting a
    corporate action costs no extra calls.
    """
    hist = yf.Ticker(symbol).history(period=span, auto_adjust=False, actions=True)
    if hist is None or hist.empty:
        return [], False
    return _to_rows(hist, symbol)


async def _store(session: AsyncSession, symbol: str, rows: list[BarRow], span: str) -> None:
    await upsert_daily_bars(
        session,
        [
            DailyBarInput(
                symbol=symbol,
                bar_date=r.bar_date,
                open=r.open,
                high=r.high,
                low=r.low,
                close=r.close,
                adj_close=r.adj_close,
                volume=r.volume,
            )
            for r in rows
        ],
    )
    await upsert_coverage(session, symbol, requested_span=span)


# Spans ordered by how far back they reach, so a refresh never narrows what a
# symbol was already backfilled to.
_SPAN_ORDER = ["1y", "2y", "5y", "10y", "max"]


def _wider(a: str | None, b: str) -> str:
    def rank(span: str | None) -> int:
        return _SPAN_ORDER.index(span) if span in _SPAN_ORDER else _SPAN_ORDER.index(DEFAULT_SPAN)

    return a if a is not None and rank(a) > rank(b) else b


def _previous_weekday(day: date) -> date:
    """The most recent weekday strictly before `day` — the latest session that
    could have settled, before accounting for holidays."""
    d = day - timedelta(days=1)
    while d.weekday() >= 5:
        d -= timedelta(days=1)
    return d


def _refreshed_today(coverage) -> bool:
    at = coverage.last_refresh_at
    if at is None:
        return False
    return at.astimezone(MARKET_TZ).date() >= market_today()


def _is_stale(coverage, settled: date | None) -> bool:
    """Has a session closed that this symbol's stored series does not include?

    This used to ask only "is it behind SPY's stored bar?" — the store used as
    its own calendar. That cannot see the store going stale as a whole: once
    SPY stopped refreshing, every symbol level with it looked current, and
    the charts and indicators were served months-old history with today's
    price glued on the end.

    The calendar check is a weekday rule with no holiday table, so on the day
    after a holiday every symbol looks a session behind. `last_refresh_at`
    bounds that to one refetch per symbol per day.
    """
    if settled is not None and coverage.last_bar_date < settled:
        return True  # behind the reference symbol: a session has closed since
    if coverage.last_bar_date >= _previous_weekday(market_today()):
        return False
    return not _refreshed_today(coverage)


async def _coverage(session: AsyncSession, symbol: str):
    return (await get_coverage(session, [symbol])).get(symbol.upper())


async def _covered(session: AsyncSession, symbol: str, start: date) -> bool:
    """Does the store hold a complete, current series back to `start`?"""
    coverage = await _coverage(session, symbol)
    if coverage is None or coverage.last_bar_date is None:
        return False

    if _is_stale(coverage, await last_settled_session(session)):
        return False

    if coverage.requested_span in FULL_SPANS:
        return True

    # Sufficient if the window falls inside the span we asked for, even when
    # the data doesn't reach that far back. A stock listed last year has no
    # older bars to fetch; comparing against first_bar_date instead would make
    # every request for a longer window re-download the same short history.
    if start >= _span_start(coverage.requested_span):
        return True

    return coverage.first_bar_date is not None and coverage.first_bar_date <= start


async def daily_bars_for(
    symbol: str,
    start: date,
    end: date | None = None,
    session: AsyncSession | None = None,
    adjusted: bool = False,
    span: str = DEFAULT_SPAN,
) -> list[BarRow]:
    """Settled daily bars for `symbol` between `start` and `end`.

    `session` is optional so this works from both places it is needed: API
    routes pass the request session (keeping the test override in play), while
    agent tools run inside LangGraph nodes with no session in scope and let it
    open its own.

    `adjusted` returns the split/dividend-adjusted series — what indicators
    need. Charts want the raw one.
    """
    symbol = symbol.upper()
    end = end or market_today()

    if session is not None:
        rows = await _load(session, symbol, start, end, span)
    else:
        try:
            async with AsyncSessionLocal() as own:
                rows = await _load(own, symbol, start, end, span)
                await own.commit()
        except Exception as e:
            # The store is an optimisation; never let it break a read.
            log.warning("bar_store_unavailable", symbol=symbol, error=str(e))
            rows = await asyncio.to_thread(lambda: fetch_live(symbol, span)[0])
            rows = [r for r in rows if start <= r.bar_date <= end]

    return adjust(rows) if adjusted else rows


async def _load(
    session: AsyncSession, symbol: str, start: date, end: date, span: str
) -> list[BarRow]:
    if await _covered(session, symbol, start):
        stored = await get_daily_bars(session, symbol, start, end)
        return [
            BarRow(
                bar_date=b.bar_date,
                open=b.open,
                high=b.high,
                low=b.low,
                close=b.close,
                adj_close=b.adj_close,
                volume=b.volume,
            )
            for b in stored
        ]

    # Miss: fetch what we don't have, keep it, then answer from what we fetched.
    needed = "max" if span in FULL_SPANS or start < _span_start(span) else span
    # A stale refresh re-downloads at least what the symbol already held, so a
    # daily refresh driven by a 1Y chart does not shrink a MAX backfill to 2y.
    existing = await _coverage(session, symbol)
    if existing is not None:
        needed = _wider(existing.requested_span, needed)
    rows, saw_split = await asyncio.to_thread(lambda: fetch_live(symbol, needed))
    if not rows:
        return []

    if saw_split:
        # A split rewrites every historical price, so patching is pointless.
        from app.db.repositories_market import delete_symbol_bars

        log.info("bar_store_split_refetch", symbol=symbol)
        await delete_symbol_bars(session, symbol)

    await _store(session, symbol, rows, needed)
    # Trim to the same window the stored path would return. Today's session is
    # still moving, so it is excluded here too — otherwise the very first call
    # for a symbol would return one more bar than every call after it.
    settled_end = min(end, market_today() - timedelta(days=1))
    return [r for r in rows if start <= r.bar_date <= settled_end]


def _span_start(span: str) -> date:
    """Earliest date a span is expected to cover."""
    years = {"1y": 1, "2y": 2, "5y": 5, "10y": 10}.get(span, 2)
    return market_today() - timedelta(days=365 * years)
