"""Market data endpoints backing the watchlist, charting and market pages.

Thin routes — all fetching, batching and caching lives in
app/services/market_data.py.
"""

from __future__ import annotations

from fastapi import APIRouter, Query

from app.services import market_data, technicals

router = APIRouter()


@router.get("/quotes")
async def get_quotes(
    symbols: str = Query(..., description="Comma-separated tickers, e.g. AAPL,MSFT,SPY"),
) -> dict:
    """Last price, change, change % and volume for each symbol."""
    requested = [s.strip().upper() for s in symbols.split(",") if s.strip()]
    if not requested:
        return {"quotes": []}
    return {"quotes": await market_data.get_quotes(requested)}


@router.get("/history")
async def get_history(
    symbol: str = Query(..., description="Ticker, e.g. AAPL"),
    range: str = Query("1Y", description="1D, 5D, 1M, 3M, 6M, YTD, 1Y, 5Y, MAX"),
) -> dict:
    """OHLCV candles for the charting view."""
    return await market_data.get_history(symbol, range)


@router.get("/profile")
async def get_profile(symbol: str = Query(..., description="Ticker, e.g. AAPL")) -> dict:
    """Instrument name, exchange and key stats shown above the chart."""
    return await market_data.get_profile(symbol)


@router.get("/overview")
async def get_overview() -> dict:
    """Indices, sector performance and macro benchmarks."""
    return await market_data.get_overview()


@router.get("/technicals")
async def get_technicals(symbol: str = Query(..., description="Ticker, e.g. AAPL")) -> dict:
    """RSI, MACD and the 5/20/50/250 moving-average ladder."""
    return await technicals.get_technicals(symbol)


@router.get("/technicals/explain")
async def explain_technicals(symbol: str = Query(..., description="Ticker, e.g. AAPL")) -> dict:
    """Plain-English read of the indicators above, written by the fast model."""
    return await technicals.explain_technicals(symbol)


@router.get("/breadth")
async def get_breadth() -> dict:
    """Advancing vs declining constituents for each major index."""
    return await market_data.get_breadth()


@router.get("/events")
async def get_events(
    days: int = Query(7, ge=1, le=31),
    symbols: str = Query("", description="Extra tickers to include in the earnings scan"),
) -> dict:
    """Earnings and economic releases for the current week."""
    extra = [s.strip().upper() for s in symbols.split(",") if s.strip()]
    return await market_data.get_events(days, extra)
