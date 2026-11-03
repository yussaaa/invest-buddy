"""Repository for social mention snapshots.

Same conventions as repositories_market.py: module-level async functions,
session first, flush never commit — the caller owns the transaction.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Iterable, Optional, Sequence

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import SocialMentionSnapshot

RETENTION_DAYS = 90


def hour_bucket(when: datetime) -> datetime:
    return when.astimezone(timezone.utc).replace(minute=0, second=0, microsecond=0)


async def upsert_snapshots(
    session: AsyncSession, source: str, rows: Iterable[dict],
    captured_at: Optional[datetime] = None,
) -> int:
    """Store one board sample. A repeat inside the same hour is a no-op."""
    captured_at = captured_at or datetime.now(timezone.utc)
    bucket = hour_bucket(captured_at)
    values = [
        {"source": source, "symbol": r["symbol"][:16], "bucket": bucket,
         "rank": r.get("rank"), "mentions": int(r["mentions"]),
         "upvotes": r.get("upvotes"), "captured_at": captured_at}
        for r in rows
        if r.get("symbol") and r.get("mentions") is not None
    ]
    if not values:
        return 0
    # Two board pages can list the same ticker during a re-rank; keep the first.
    unique = list({v["symbol"]: v for v in reversed(values)}.values())
    stmt = pg_insert(SocialMentionSnapshot).values(unique).on_conflict_do_nothing()
    await session.execute(stmt)
    await session.flush()
    return len(unique)


async def snapshots_for(
    session: AsyncSession, source: str, symbols: Sequence[str], days: int,
) -> dict[str, list[tuple[datetime, int, Optional[int]]]]:
    """(captured_at, mentions, rank) per symbol over the last `days`, oldest first."""
    if not symbols:
        return {}
    since = datetime.now(timezone.utc) - timedelta(days=days)
    stmt = (
        select(SocialMentionSnapshot.symbol, SocialMentionSnapshot.captured_at,
               SocialMentionSnapshot.mentions, SocialMentionSnapshot.rank)
        .where(SocialMentionSnapshot.source == source,
               SocialMentionSnapshot.symbol.in_([s.upper() for s in symbols]),
               SocialMentionSnapshot.bucket >= since)
        .order_by(SocialMentionSnapshot.captured_at)
    )
    out: dict[str, list[tuple[datetime, int, Optional[int]]]] = {}
    for symbol, captured_at, mentions, rank in (await session.execute(stmt)).all():
        out.setdefault(symbol, []).append((captured_at, mentions, rank))
    return out


async def prune(session: AsyncSession, older_than_days: int = RETENTION_DAYS) -> int:
    cutoff = datetime.now(timezone.utc) - timedelta(days=older_than_days)
    result = await session.execute(
        delete(SocialMentionSnapshot).where(SocialMentionSnapshot.bucket < cutoff)
    )
    await session.flush()
    return result.rowcount or 0
