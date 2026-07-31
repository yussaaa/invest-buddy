"""Repository for the local daily bar store.

Kept separate from repositories.py so the market surface doesn't crowd the
user/analysis one. Same conventions: module-level async functions, session
first, flush never commit — the caller owns the transaction.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Iterable, Sequence
from zoneinfo import ZoneInfo

from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import BarCoverage, DailyBar

# Postgres caps a statement at 65535 bind parameters. Eight columns per row
# leaves plenty of headroom at this chunk size.
UPSERT_CHUNK_ROWS = 2000

MARKET_TZ = ZoneInfo("America/New_York")


@dataclass(frozen=True)
class DailyBarInput:
    symbol: str
    bar_date: date
    close: float
    open: float | None = None
    high: float | None = None
    low: float | None = None
    adj_close: float | None = None
    volume: int | None = None


def market_today() -> date:
    """Today in exchange time — the boundary for what counts as settled."""
    return datetime.now(MARKET_TZ).date()


def _chunks(rows: Sequence[dict], size: int) -> Iterable[Sequence[dict]]:
    for i in range(0, len(rows), size):
        yield rows[i : i + size]


async def upsert_daily_bars(session: AsyncSession, bars: Sequence[DailyBarInput]) -> int:
    """Insert or update settled bars. Returns the number of rows submitted.

    Rows dated today (exchange time) are dropped: that session is still moving,
    and persisting a partial bar would serve a wrong close to every replica
    until someone noticed.
    """
    cutoff = market_today()
    rows = [
        {
            "symbol": b.symbol.upper(),
            "bar_date": b.bar_date,
            "open": b.open,
            "high": b.high,
            "low": b.low,
            "close": b.close,
            "adj_close": b.adj_close,
            "volume": b.volume,
        }
        for b in bars
        if b.bar_date < cutoff and b.close is not None
    ]
    if not rows:
        return 0

    for chunk in _chunks(rows, UPSERT_CHUNK_ROWS):
        stmt = pg_insert(DailyBar).values(list(chunk))
        stmt = stmt.on_conflict_do_update(
            index_elements=["symbol", "bar_date"],
            set_={
                "open": stmt.excluded.open,
                "high": stmt.excluded.high,
                "low": stmt.excluded.low,
                "close": stmt.excluded.close,
                "adj_close": stmt.excluded.adj_close,
                "volume": stmt.excluded.volume,
                "updated_at": func.now(),
            },
            # Skip rows that haven't actually changed. A refresh overlaps
            # existing history by design; without this every run would rewrite
            # thousands of identical rows and churn WAL for nothing.
            where=(
                DailyBar.close.is_distinct_from(stmt.excluded.close)
                | DailyBar.adj_close.is_distinct_from(stmt.excluded.adj_close)
                | DailyBar.volume.is_distinct_from(stmt.excluded.volume)
            ),
        )
        await session.execute(stmt)

    await session.flush()
    return len(rows)


async def get_daily_bars(
    session: AsyncSession, symbol: str, start: date, end: date
) -> list[DailyBar]:
    """Settled bars for one symbol, oldest first."""
    stmt = (
        select(DailyBar)
        .where(
            DailyBar.symbol == symbol.upper(),
            DailyBar.bar_date >= start,
            DailyBar.bar_date <= end,
            DailyBar.bar_date < market_today(),
        )
        .order_by(DailyBar.bar_date)
    )
    return list((await session.execute(stmt)).scalars().all())


async def get_daily_closes(
    session: AsyncSession, symbols: Sequence[str], start: date, end: date
) -> dict[str, list[tuple[date, float]]]:
    """Closes for many symbols over a window — the breadth/overview query."""
    if not symbols:
        return {}

    upper = [s.upper() for s in symbols]
    stmt = (
        select(DailyBar.symbol, DailyBar.bar_date, DailyBar.close)
        .where(
            DailyBar.symbol.in_(upper),
            DailyBar.bar_date >= start,
            DailyBar.bar_date <= end,
            DailyBar.bar_date < market_today(),
        )
        .order_by(DailyBar.symbol, DailyBar.bar_date)
    )

    out: dict[str, list[tuple[date, float]]] = {}
    for symbol, bar_date, close in (await session.execute(stmt)).all():
        out.setdefault(symbol, []).append((bar_date, close))
    return out


async def get_prev_closes(
    session: AsyncSession, symbols: Sequence[str], on: date | None = None
) -> dict[str, float]:
    """Each symbol's most recent settled close at or before `on`."""
    if not symbols:
        return {}

    upper = [s.upper() for s in symbols]
    cutoff = min(on or market_today(), market_today())

    latest = (
        select(DailyBar.symbol, func.max(DailyBar.bar_date).label("bar_date"))
        .where(DailyBar.symbol.in_(upper), DailyBar.bar_date < cutoff)
        .group_by(DailyBar.symbol)
        .subquery()
    )
    stmt = select(DailyBar.symbol, DailyBar.close).join(
        latest,
        (DailyBar.symbol == latest.c.symbol) & (DailyBar.bar_date == latest.c.bar_date),
    )
    return {symbol: close for symbol, close in (await session.execute(stmt)).all()}


async def last_settled_session(
    session: AsyncSession, reference: str = "SPY"
) -> date | None:
    """The newest settled session we know about.

    Uses the store as its own trading calendar: if the reference symbol has a
    bar for a date, that date was a trading day. Avoids taking a market-calendar
    dependency just to answer "was yesterday a holiday".
    """
    stmt = select(func.max(DailyBar.bar_date)).where(
        DailyBar.symbol == reference.upper(),
        DailyBar.bar_date < market_today(),
    )
    return (await session.execute(stmt)).scalar_one_or_none()


async def get_coverage(
    session: AsyncSession, symbols: Sequence[str]
) -> dict[str, BarCoverage]:
    if not symbols:
        return {}
    stmt = select(BarCoverage).where(
        BarCoverage.symbol.in_([s.upper() for s in symbols])
    )
    return {row.symbol: row for row in (await session.execute(stmt)).scalars().all()}


async def upsert_coverage(
    session: AsyncSession,
    symbol: str,
    requested_span: str,
    last_error: str | None = None,
) -> None:
    """Recompute a symbol's coverage row from what is actually stored."""
    symbol = symbol.upper()
    stats = (
        await session.execute(
            select(
                func.min(DailyBar.bar_date),
                func.max(DailyBar.bar_date),
                func.count(),
            ).where(DailyBar.symbol == symbol)
        )
    ).one()
    first_bar, last_bar, count = stats

    stmt = pg_insert(BarCoverage).values(
        symbol=symbol,
        first_bar_date=first_bar,
        last_bar_date=last_bar,
        bar_count=count,
        requested_span=requested_span,
        last_refresh_at=datetime.now(timezone.utc),
        last_error=last_error,
    )
    stmt = stmt.on_conflict_do_update(
        index_elements=["symbol"],
        set_={
            "first_bar_date": stmt.excluded.first_bar_date,
            "last_bar_date": stmt.excluded.last_bar_date,
            "bar_count": stmt.excluded.bar_count,
            "requested_span": stmt.excluded.requested_span,
            "last_refresh_at": stmt.excluded.last_refresh_at,
            "last_error": stmt.excluded.last_error,
        },
    )
    await session.execute(stmt)
    await session.flush()


async def delete_symbol_bars(session: AsyncSession, symbol: str) -> int:
    """Drop a symbol's history — used when a split invalidates every row."""
    result = await session.execute(
        delete(DailyBar).where(DailyBar.symbol == symbol.upper())
    )
    await session.flush()
    return result.rowcount or 0
