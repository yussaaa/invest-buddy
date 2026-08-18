"""Market data endpoints backing the watchlist, charting and market pages.

Thin routes — all fetching, batching and caching lives in
app/services/market_data.py.
"""

from __future__ import annotations

from fastapi import APIRouter, Query

from app.services import market_data, options, technicals, valuation

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


@router.get("/trend")
async def get_trend(
    symbol: str = Query(..., description="Ticker, e.g. AAPL"),
    range: str = Query("2y", description="1y, 2y or 5y of series"),
) -> dict:
    """Price vs its 200-day average: SMA slopes, the ±1.5σ band and the z-score."""
    return await technicals.get_trend(symbol, range)


@router.get("/technicals/explain")
async def explain_technicals(symbol: str = Query(..., description="Ticker, e.g. AAPL")) -> dict:
    """Plain-English read of the indicators above, written by the fast model."""
    return await technicals.explain_technicals(symbol)


@router.get("/valuation")
async def get_valuation(
    symbol: str = Query(..., description="Ticker, e.g. AAPL"),
    growth: float | None = Query(None, ge=-0.5, le=1.0, description="Year-1 FCF growth"),
    terminal_growth: float | None = Query(None, ge=0.0, le=0.06),
    discount_rate: float | None = Query(None, ge=0.03, le=0.30),
    years: int = Query(10, ge=5, le=20),
    fcf_base: str = Query("ttm", description="ttm or median"),
) -> dict:
    """Scenario-banded DCF, the sensitivity grid, and the market-implied growth rate."""
    return await valuation.get_valuation(
        symbol,
        growth=growth,
        terminal_growth=terminal_growth,
        discount_rate=discount_rate,
        years=years,
        fcf_base=fcf_base,
    )


@router.get("/options/chain")
async def get_options_chain(
    symbol: str = Query(..., description="Ticker, e.g. AAPL"),
    horizon: str = Query("both", description="short (7-45 DTE), leaps (300+ DTE) or both"),
) -> dict:
    """Normalised option contracts with computed greeks, plus volatility context."""
    return await options.get_options_chain(symbol, horizon)


@router.get("/options/strategies")
async def get_option_strategies(
    symbol: str = Query(..., description="Ticker, e.g. AAPL"),
    strategy: str = Query("all", description="csp, covered_call, leaps_call, put_credit_spread, all"),
    limit: int = Query(15, ge=1, le=50),
) -> dict:
    """Contracts that pass the liquidity, moneyness and probability filters."""
    return await options.get_option_strategies(symbol, strategy, limit)


@router.get("/options/explain")
async def explain_options(
    symbol: str = Query(..., description="Ticker, e.g. AAPL"),
    strategy: str = Query("all", description="Which screen to describe"),
) -> dict:
    """Plain-English read of what the screen surfaced, written by the fast model."""
    return await options.explain_options(symbol, strategy)


@router.get("/breadth")
async def get_breadth() -> dict:
    """Advancing vs declining constituents for each major index."""
    return await market_data.get_breadth()


@router.get("/movers")
async def get_movers(
    cap: str = Query("all", description="all, large, mid or small"),
    limit: int = Query(10, ge=1, le=25),
) -> dict:
    """The day's biggest gainers and losers within one market-cap tier."""
    return await market_data.get_movers(cap, limit)


@router.get("/events")
async def get_events(
    days: int = Query(7, ge=1, le=31),
    symbols: str = Query("", description="Extra tickers to include in the earnings scan"),
) -> dict:
    """Earnings and economic releases over the next `days` days."""
    extra = [s.strip().upper() for s in symbols.split(",") if s.strip()]
    return await market_data.get_events(days, extra)


@router.get("/events/week")
async def get_week_events(
    offset: int = Query(0, ge=-26, le=26, description="0 = this week, -1 = last, +1 = next"),
    symbols: str = Query("", description="Extra tickers to include in the earnings scan"),
) -> dict:
    """Earnings and economic releases for one calendar week (Mon–Sun)."""
    extra = [s.strip().upper() for s in symbols.split(",") if s.strip()]
    return await market_data.get_week_events(offset, extra)
