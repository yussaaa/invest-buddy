"""Signals page endpoints — social trending, preset screens, ticker crowd read.

Thin routes, as in market.py: fetching, caching and the rules live in
services/signals_page.py, services/screens.py and services/social/. Failures
come back as an `error` or `caveat` field in a 200, never a 500, so one dead
source never blanks the page.
"""

from __future__ import annotations

import json
from typing import AsyncIterator

import structlog
from fastapi import APIRouter, HTTPException, Query, status
from sse_starlette.sse import EventSourceResponse

from app.agents.research.graph import run_research, stream_research
from app.agents.research.schemas import ResearchRequest
from app.config import get_settings
from app.services import screens, signals_page
from app.services.llm_guard import has_llm_credentials, missing_credentials_reason

log = structlog.get_logger(__name__)

router = APIRouter()


@router.get("/sources")
async def get_sources() -> dict:
    """Every social provider and whether it is live, blocked or not configured."""
    return await signals_page.get_sources()


@router.get("/trending")
async def get_trending(
    source: str = Query("apewisdom", description="apewisdom, reddit or x"),
    filter: str = Query("all-stocks", description="all-stocks, wallstreetbets, stocks, investing, options"),
    limit: int = Query(50, ge=1, le=200),
) -> dict:
    """Most-discussed tickers, with rank and mention changes and attention badges."""
    return await signals_page.get_trending(source, filter, limit)


@router.get("/screeners")
async def get_screeners() -> dict:
    return {"presets": screens.list_presets()}


@router.get("/screen")
async def get_screen(
    preset: str = Query(screens.DEFAULT_PRESET, description="A preset id from /screeners"),
    limit: int = Query(25, ge=1, le=50),
) -> dict:
    """One preset screen, with each row's Reddit rank overlaid when known."""
    return await screens.get_screen(preset, limit)


@router.get("/ticker")
async def get_ticker(symbol: str = Query(..., description="Ticker, e.g. PLTR")) -> dict:
    """Reddit attention, StockTwits tags, short interest, and both signal sets."""
    return await signals_page.get_ticker_snapshot(symbol)


@router.get("/ticker/vol")
async def get_ticker_vol(symbol: str = Query(..., description="Ticker, e.g. PLTR")) -> dict:
    """30-day ATM implied volatility against realized; apart so the snapshot never waits on a chain."""
    return await signals_page.get_ticker_vol(symbol)


@router.get("/ticker/explain")
async def explain_ticker(symbol: str = Query(..., description="Ticker, e.g. PLTR")) -> dict:
    """A guarded plain-English read of the crowd snapshot."""
    return await signals_page.explain_ticker(symbol)


def _require_research() -> None:
    settings = get_settings()
    if not settings.research_enabled:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                            detail="The research agent is disabled (RESEARCH_ENABLED=false).")
    if not has_llm_credentials(settings):
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                            detail=missing_credentials_reason(settings))


@router.post("/research/stream")
async def research_stream(request: ResearchRequest):
    """Why is this ticker in focus — streamed.

    Events: start, context, sources, tool_call, tool_result, token, flag,
    done, error. `done.replaced` means the streamed draft failed review and
    `done.brief` is the templated replacement.
    """
    _require_research()

    async def events() -> AsyncIterator[dict]:
        try:
            async for event, payload in stream_research(request.ticker, request.question):
                yield {"event": event, "data": json.dumps(payload, default=str)}
        except Exception as exc:
            log.error("research_stream_endpoint_failed", error=str(exc), exc_info=True)
            yield {"event": "error",
                   "data": json.dumps({"message": "The research run could not complete."})}

    return EventSourceResponse(events())


@router.post("/research")
async def research(request: ResearchRequest) -> dict:
    """The same run as one JSON response."""
    _require_research()
    return await run_research(request.ticker, request.question)
