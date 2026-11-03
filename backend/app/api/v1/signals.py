"""Signals page endpoints — social trending, preset screens, ticker crowd read.

Thin routes, as in market.py: fetching, caching and the rules live in
services/signals_page.py, services/screens.py and services/social/. Failures
come back as an `error` or `caveat` field in a 200, never a 500, so one dead
source never blanks the page.
"""

from __future__ import annotations

from fastapi import APIRouter, Query

from app.services import screens, signals_page

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


@router.get("/ticker/explain")
async def explain_ticker(symbol: str = Query(..., description="Ticker, e.g. PLTR")) -> dict:
    """A guarded plain-English read of the crowd snapshot."""
    return await signals_page.explain_ticker(symbol)
