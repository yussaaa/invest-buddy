"""News wire endpoints — the stored feed, its live changes, and source health.

Thin routes over services/news/feed.py. The runner (services/news/runner.py)
does all the fetching; nothing here calls a news source, so a page load can
never be slowed or rate-limited by one. /settings edits the wire's
deployment-wide configuration (services/app_settings.py).
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import AsyncIterator, Optional

import structlog
from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sse_starlette.sse import EventSourceResponse

from app.services import app_settings, news
from app.services.news import feed

log = structlog.get_logger(__name__)

router = APIRouter()


def _csv(value: Optional[str]) -> Optional[list[str]]:
    items = [v.strip() for v in (value or "").split(",") if v.strip()]
    return items or None


@router.get("")
async def get_news(
    limit: int = Query(100, ge=1, le=300),
    before: Optional[datetime] = Query(None, description="Page back: published before this time"),
    source: Optional[str] = Query(None, description="Comma-separated: alpaca, edgar, fed"),
    category: Optional[str] = Query(None, description="Comma-separated categories"),
    symbol: Optional[str] = Query(None, description="Only headlines tagged with this ticker"),
) -> dict:
    """Stored headlines, newest first."""
    return await feed.get_feed(limit=limit, before=before, sources=_csv(source),
                               categories=_csv(category), symbol=symbol)


@router.get("/sources")
async def get_sources() -> dict:
    """Each news source and whether it is live, refused, or not set up."""
    await app_settings.refresh()
    return feed.get_sources()


class NewsSettingsUpdate(BaseModel):
    # "" clears it, falling back to SEC_USER_AGENT in .env.
    sec_contact: str = Field("", max_length=1000)


@router.get("/settings")
async def get_news_settings() -> dict:
    """The wire's deployment-wide settings — shared by every user, not a preference."""
    await app_settings.refresh()
    return feed.news_settings()


@router.put("/settings")
async def put_news_settings(req: NewsSettingsUpdate) -> dict:
    try:
        value = app_settings.validate_sec_contact(req.sec_contact)
    except ValueError as e:
        raise HTTPException(422, str(e)) from e
    try:
        await app_settings.save(app_settings.SEC_CONTACT, value)
    except Exception as e:
        log.warning("news_settings_save_failed", error=str(e)[:200])
        raise HTTPException(
            503, "The settings could not be saved: the database is unavailable."
        ) from e
    # A refusal under the old contact says nothing about the new one.
    news.EDGAR.last_error = None
    return feed.news_settings()


@router.get("/stream")
async def stream_news(
    request: Request,
    since: Optional[datetime] = Query(None, description="Newest updated_at the client holds"),
):
    """New and updated headlines as they are written.

    Events: `ready` once connected, then `headlines` with every row that is
    new or changed since it was last sent. A comment ping every 15s keeps
    proxies from closing an idle connection.
    """

    async def events() -> AsyncIterator[dict]:
        try:
            async for event, payload in feed.stream_changes(since, request.is_disconnected):
                yield {"event": event, "data": json.dumps(payload, default=str)}
        except Exception as exc:
            log.error("news_stream_failed", error=str(exc), exc_info=True)
            yield {"event": "error", "data": json.dumps({"message": "The news stream stopped."})}

    return EventSourceResponse(events(), ping=15)
