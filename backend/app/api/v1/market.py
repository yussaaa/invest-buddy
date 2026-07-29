"""Lightweight market quote endpoints for the watchlist panel.

Separate from the agent tools: the watchlist polls this every few seconds, so
it batches all symbols into a single yfinance download and caches the result
for a short TTL to avoid hammering the upstream API.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

import pandas as pd
import structlog
import yfinance as yf
from fastapi import APIRouter, Query

log = structlog.get_logger(__name__)

router = APIRouter()

# symbol -> (fetched_at, quote dict)
_CACHE: dict[str, tuple[float, dict]] = {}
_CACHE_TTL_SECONDS = 10.0
_MAX_SYMBOLS = 60


def _safe(val: Any) -> Any:
    if val is None:
        return None
    if isinstance(val, float) and (pd.isna(val) or val in (float("inf"), float("-inf"))):
        return None
    return val


def _quote_from_frame(symbol: str, df: pd.DataFrame) -> dict:
    """Build a quote dict from a single symbol's OHLCV frame."""
    closes = df["Close"].dropna() if "Close" in df else pd.Series(dtype=float)
    if closes.empty:
        return {"symbol": symbol, "error": "no data"}

    last = float(closes.iloc[-1])
    prev = float(closes.iloc[-2]) if len(closes) > 1 else last
    change = last - prev
    change_pct = (change / prev * 100.0) if prev else 0.0

    volume = None
    if "Volume" in df:
        vols = df["Volume"].dropna()
        if not vols.empty:
            volume = int(vols.iloc[-1])

    return {
        "symbol": symbol,
        "last": round(last, 4),
        "prev_close": round(prev, 4),
        "change": round(change, 4),
        "change_percent": round(change_pct, 4),
        "volume": _safe(volume),
    }


def _download(symbols: list[str]) -> dict[str, dict]:
    """Batch-fetch the last few daily bars for every symbol in one call."""
    raw = yf.download(
        tickers=symbols,
        period="5d",
        interval="1d",
        group_by="ticker",
        auto_adjust=False,
        progress=False,
        threads=True,
    )

    quotes: dict[str, dict] = {}
    if raw is None or raw.empty:
        return {s: {"symbol": s, "error": "no data"} for s in symbols}

    multi = isinstance(raw.columns, pd.MultiIndex)
    for sym in symbols:
        try:
            frame = raw[sym] if multi else raw
            quotes[sym] = _quote_from_frame(sym, frame)
        except Exception as e:  # symbol missing from the batch response
            quotes[sym] = {"symbol": sym, "error": str(e)}
    return quotes


@router.get("/quotes")
async def get_quotes(
    symbols: str = Query(..., description="Comma-separated tickers, e.g. AAPL,MSFT,SPY"),
) -> dict:
    """Return last price, change, change % and volume for each symbol."""
    requested = [s.strip().upper() for s in symbols.split(",") if s.strip()][:_MAX_SYMBOLS]
    if not requested:
        return {"quotes": []}

    now = time.time()
    fresh: dict[str, dict] = {}
    stale: list[str] = []
    for sym in requested:
        cached = _CACHE.get(sym)
        if cached and now - cached[0] < _CACHE_TTL_SECONDS:
            fresh[sym] = cached[1]
        else:
            stale.append(sym)

    if stale:
        try:
            fetched = await asyncio.to_thread(_download, stale)
        except Exception as e:
            log.error("quotes_fetch_failed", symbols=stale, error=str(e))
            fetched = {s: {"symbol": s, "error": str(e)} for s in stale}
        for sym, quote in fetched.items():
            # Only cache good quotes so failures retry on the next poll.
            if "error" not in quote:
                _CACHE[sym] = (now, quote)
            fresh[sym] = quote

    return {
        "quotes": [fresh.get(s, {"symbol": s, "error": "not found"}) for s in requested],
        "as_of": now,
    }
