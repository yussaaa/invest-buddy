"""Repository for the news wire.

Same conventions as repositories_social.py: module-level async functions,
session first, flush never commit — the caller owns the transaction.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Iterable, Optional, Sequence

from sqlalchemy import delete, func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import NewsHeadline

RETENTION_DAYS = 30

# What ingest writes. Later stages own the other columns.
INGEST_FIELDS = (
    "id", "source", "source_id", "publisher", "title", "summary", "url",
    "published_at", "symbols", "category", "tags",
)


async def insert_headlines(session: AsyncSession, headlines: Iterable[dict]) -> list[str]:
    """Store new headlines; a repeat of a stored one is a no-op.

    Returns the ids actually inserted, so the caller can log how much of a
    poll was new rather than how much it fetched.
    """
    values = [{k: h.get(k) for k in INGEST_FIELDS} for h in headlines]
    # One poll can carry the same item twice (a websocket replay racing the
    # REST catch-up); Postgres refuses duplicate keys within one INSERT.
    unique = list({v["id"]: v for v in values}.values())
    if not unique:
        return []
    stmt = (
        pg_insert(NewsHeadline)
        .values(unique)
        .on_conflict_do_nothing(index_elements=["id"])
        .returning(NewsHeadline.id)
    )
    inserted = (await session.execute(stmt)).scalars().all()
    await session.flush()
    return list(inserted)


async def latest(
    session: AsyncSession,
    *,
    limit: int = 100,
    before: Optional[datetime] = None,
    sources: Optional[Sequence[str]] = None,
    categories: Optional[Sequence[str]] = None,
    symbol: Optional[str] = None,
) -> list[NewsHeadline]:
    """Newest first, by when the source published it."""
    stmt = select(NewsHeadline).order_by(NewsHeadline.published_at.desc()).limit(limit)
    if before is not None:
        stmt = stmt.where(NewsHeadline.published_at < before)
    if sources:
        stmt = stmt.where(NewsHeadline.source.in_(list(sources)))
    if categories:
        stmt = stmt.where(NewsHeadline.category.in_(list(categories)))
    if symbol:
        stmt = stmt.where(NewsHeadline.symbols.contains([symbol.upper()]))
    return list((await session.execute(stmt)).scalars().all())


async def recently_changed(session: AsyncSession, *, limit: int = 300) -> list[NewsHeadline]:
    """The rows most recently written by any stage — what the stream diffs."""
    stmt = select(NewsHeadline).order_by(NewsHeadline.updated_at.desc()).limit(limit)
    return list((await session.execute(stmt)).scalars().all())


async def last_published(session: AsyncSession, source: str) -> Optional[datetime]:
    """Where a source's catch-up should resume from after a restart."""
    stmt = (
        select(NewsHeadline.published_at)
        .where(NewsHeadline.source == source)
        .order_by(NewsHeadline.published_at.desc())
        .limit(1)
    )
    return (await session.execute(stmt)).scalar_one_or_none()


async def prune(session: AsyncSession, older_than_days: int = RETENTION_DAYS) -> int:
    cutoff = datetime.now(timezone.utc) - timedelta(days=older_than_days)
    result = await session.execute(
        delete(NewsHeadline).where(NewsHeadline.published_at < cutoff)
    )
    await session.flush()
    return result.rowcount or 0


async def needing_symbol_info(
    session: AsyncSession, *, since: datetime, limit: int = 100
) -> list[NewsHeadline]:
    """Recent headlines that name tickers whose sector/industry is not filled yet."""
    stmt = (
        select(NewsHeadline)
        .where(NewsHeadline.symbol_info.is_(None))
        .where(func.cardinality(NewsHeadline.symbols) > 0)
        .where(NewsHeadline.published_at >= since)
        .order_by(NewsHeadline.published_at.desc())
        .limit(limit)
    )
    return list((await session.execute(stmt)).scalars())


async def set_symbol_info(session: AsyncSession, info_by_id: dict[str, dict]) -> None:
    """Fill symbol_info and restamp, so the stream sends the row again."""
    for headline_id, info in info_by_id.items():
        await session.execute(
            update(NewsHeadline).where(NewsHeadline.id == headline_id)
            .values(symbol_info=info, updated_at=func.now())
        )
    await session.flush()
