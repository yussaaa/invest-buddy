"""What the News page reads: the stored wire, newest first, and what changed.

Thin on purpose — the runner writes, this reads. Failures come back as an
`error` in a 200 (as on the Signals page), so a database hiccup shows as a
sentence above an empty list rather than a broken page.

**How the stream finds changes.** Every stage that writes a row stamps
`updated_at`. The stream polls "the rows most recently written" and sends
each one whose stamp it has not sent before. No cursor arithmetic, which
matters: `now()` is a transaction's *start* time, so a cursor of "newest
stamp seen" could skip a row whose transaction began earlier but committed
later. Diffing by (id, stamp) cannot. One query serves every connected
client — the snapshot is shared in-process for `CHANGES_TTL`.
"""

from __future__ import annotations

import asyncio
import time
from datetime import datetime, timezone
from typing import AsyncIterator, Awaitable, Callable, Optional, Sequence

import structlog

from app.services import app_settings, news
from app.services.news.models import CATEGORIES

log = structlog.get_logger(__name__)

CHANGES_LIMIT = 300
CHANGES_TTL = 2.0
STREAM_POLL_SECONDS = 3.0

DISCLAIMER = ("Headlines are reported as published by their source. Nothing here "
              "is a recommendation to trade.")

_changes: tuple[float, list[dict]] = (0.0, [])
_changes_lock = asyncio.Lock()


def _iso(value: Optional[datetime]) -> Optional[str]:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat()


def to_dict(row) -> dict:
    return {
        "id": row.id,
        "source": row.source,
        "publisher": row.publisher,
        "title": row.title,
        "summary": row.summary,
        "url": row.url,
        "published_at": _iso(row.published_at),
        "symbols": list(row.symbols or []),
        "category": row.category,
        "tags": list(row.tags or []),
        "importance": row.importance,
        "exposures": row.exposures,
        "explanation": row.explanation,
        "reaction": row.reaction,
        "updated_at": _iso(row.updated_at),
    }


async def get_feed(
    *, limit: int = 100, before: Optional[datetime] = None,
    sources: Optional[Sequence[str]] = None, categories: Optional[Sequence[str]] = None,
    symbol: Optional[str] = None,
) -> dict:
    from app.db.repositories_news import latest
    from app.db.session import AsyncSessionLocal

    as_of = _iso(datetime.now(timezone.utc))
    try:
        async with AsyncSessionLocal() as session:
            rows = await latest(session, limit=limit, before=before, sources=sources,
                                categories=categories, symbol=symbol)
    except Exception as e:
        log.warning("news_feed_failed", error=str(e)[:200])
        return {"headlines": [], "as_of": as_of, "error": "The news store is unavailable."}
    return {"headlines": [to_dict(r) for r in rows], "as_of": as_of}


def get_sources() -> dict:
    """Source health. Call app_settings.refresh() first for a fresh reading."""
    return {
        "sources": [s.model_dump() for s in news.statuses()],
        "categories": list(CATEGORIES),
        "disclaimer": DISCLAIMER,
    }


def news_settings() -> dict:
    """The wire's deployment-wide settings, for the Settings page.

    A contact from .env is reported by source only. The page cannot edit it,
    so echoing the address would only publish it to every visitor; a value
    saved from the page is returned so it can be edited there.
    """
    value, source = app_settings.sec_contact()
    return {
        "sec_contact": {"value": value if source == "settings" else None, "source": source},
        "edgar": news.EDGAR.status().model_dump(),
    }


async def _load_changes() -> list[dict]:
    from app.db.repositories_news import recently_changed
    from app.db.session import AsyncSessionLocal

    async with AsyncSessionLocal() as session:
        return [to_dict(r) for r in await recently_changed(session, limit=CHANGES_LIMIT)]


async def recent_changes() -> list[dict]:
    """The most recently written rows, shared by every stream in this process."""
    global _changes
    if time.monotonic() - _changes[0] < CHANGES_TTL:
        return _changes[1]
    async with _changes_lock:
        if time.monotonic() - _changes[0] < CHANGES_TTL:
            return _changes[1]
        _changes = (time.monotonic(), await _load_changes())
        return _changes[1]


def diff(rows: list[dict], sent: dict[str, str]) -> list[dict]:
    """Rows not yet sent at their current stamp; records them in `sent`.

    `sent` is trimmed to the ids still in the window, so a long-lived
    connection holds at most CHANGES_LIMIT entries.
    """
    fresh = [r for r in rows if sent.get(r["id"]) != r["updated_at"]]
    window = {r["id"]: r["updated_at"] for r in rows}
    sent.clear()
    sent.update(window)
    return fresh


async def stream_changes(
    since: Optional[datetime],
    is_disconnected: Callable[[], Awaitable[bool]],
    poll_seconds: float = STREAM_POLL_SECONDS,
) -> AsyncIterator[tuple[str, dict]]:
    """(event, payload) pairs: `ready` once, then `headlines` as rows change.

    `since` is the newest `updated_at` the client already holds; rows at or
    before it are treated as sent, so a reconnect does not replay the page.
    """
    sent: dict[str, str] = {}
    first = True
    cutoff = since.astimezone(timezone.utc) if since and since.tzinfo else (
        since.replace(tzinfo=timezone.utc) if since else None)
    while not await is_disconnected():
        try:
            rows = await recent_changes()
        except Exception as e:
            log.warning("news_stream_read_failed", error=str(e)[:200])
            rows = None
        if rows is not None:
            fresh = diff(rows, sent)
            if first:
                fresh = [] if cutoff is None else [
                    r for r in fresh if datetime.fromisoformat(r["updated_at"]) > cutoff]
                yield "ready", {"as_of": _iso(datetime.now(timezone.utc))}
                first = False
            if fresh:
                fresh.sort(key=lambda r: r["published_at"] or "", reverse=True)
                yield "headlines", {"headlines": fresh}
        await asyncio.sleep(poll_seconds)
