"""Relative strength and relative volume for one stock.

Two readings a trader checks before acting on anything else: is the name
*leading or lagging* the market and its own sector (relative strength), and is
*more than the usual* volume behind today's move (relative volume). Both are
deterministic arithmetic over bars the store already holds — SPY and the eleven
SPDR sector ETFs are warmed to five years alongside everything else — so this
composes existing reads and adds no new data source.

Returns scalars only, the same discipline as the trend summary: nothing here
is a recommendation, only a measurement of where the stock sits relative to its
benchmarks.
"""

from __future__ import annotations

import asyncio
from datetime import date, datetime, timedelta, timezone

import structlog

from app.services import bar_store, market_data
from app.services.cache import cached_async

log = structlog.get_logger(__name__)

RELATIVE_TTL = 120.0
BENCHMARK = "SPY"
RVOL_WINDOW = 20
# (label, trading sessions) — ~21 sessions a month.
RS_WINDOWS: list[tuple[str, int]] = [("1M", 21), ("3M", 63), ("6M", 126)]
# Calendar days to request so the longest window (126 sessions) has a buffer.
LOOKBACK_DAYS = 300

# Canonical sector (normalise_sector output) → its SPDR ETF. Keyed on the
# normalised names, which match market_data.SECTORS except for the three the
# ETF list abbreviates (Cons. Discretionary / Cons. Staples / Comm. Services).
SECTOR_TO_ETF = {
    "Technology": "XLK",
    "Financials": "XLF",
    "Health Care": "XLV",
    "Consumer Discretionary": "XLY",
    "Consumer Staples": "XLP",
    "Energy": "XLE",
    "Industrials": "XLI",
    "Materials": "XLB",
    "Real Estate": "XLRE",
    "Utilities": "XLU",
    "Communication Services": "XLC",
}


def _return_over(closes: list[float], sessions: int) -> float | None:
    """Simple return over the last `sessions` settled closes."""
    if len(closes) <= sessions:
        return None
    past = closes[-1 - sessions]
    if not past:
        return None
    return closes[-1] / past - 1.0


def _rvol_label(value: float) -> str:
    if value >= 2.0:
        return "surging"
    if value >= 1.5:
        return "elevated"
    if value >= 0.7:
        return "normal"
    return "quiet"


def _trend_label(vs_spy: float | None) -> str | None:
    """A one-word read of leadership, from the stock's excess return vs SPY."""
    if vs_spy is None:
        return None
    if vs_spy > 0.02:
        return "leading"
    if vs_spy < -0.02:
        return "lagging"
    return "in line"


async def _adjusted_closes(symbol: str, start: date) -> list[float]:
    """Settled, split-adjusted closes for one symbol — [] if the store can't answer."""
    try:
        rows = await bar_store.daily_bars_for(symbol, start=start, adjusted=True)
    except Exception as e:
        log.warning("relative_bars_failed", symbol=symbol, error=str(e))
        return []
    return [r.close for r in rows if r.close is not None]


async def get_relative(symbol: str) -> dict:
    """Relative strength (vs SPY and sector, 1M/3M/6M) and relative volume."""
    symbol = symbol.upper()

    async def fetch() -> dict:
        start = date.today() - timedelta(days=LOOKBACK_DAYS)

        stock_rows_task = bar_store.daily_bars_for(symbol, start=start, adjusted=True)
        quotes_task = market_data.get_quotes([symbol])
        profile_task = market_data.get_profile(symbol)
        spy_task = _adjusted_closes(BENCHMARK, start)
        stock_rows, quotes, profile, spy_closes = await asyncio.gather(
            stock_rows_task, quotes_task, profile_task, spy_task, return_exceptions=True
        )

        if isinstance(stock_rows, Exception) or not stock_rows:
            return {"symbol": symbol, "available": False, "reason": "No price history."}

        stock_closes = [r.close for r in stock_rows if r.close is not None]

        # Relative volume: today's volume against the trailing 20-session mean.
        today_vol = None
        if not isinstance(quotes, Exception) and quotes:
            today_vol = quotes[0].get("volume")
        settled_vols = [r.volume for r in stock_rows if r.volume]
        rvol = None
        if today_vol and len(settled_vols) >= RVOL_WINDOW:
            avg = sum(settled_vols[-RVOL_WINDOW:]) / RVOL_WINDOW
            if avg > 0:
                value = today_vol / avg
                rvol = {
                    "value": round(value, 2),
                    "today_volume": int(today_vol),
                    "avg_volume": int(avg),
                    "window": RVOL_WINDOW,
                    "label": _rvol_label(value),
                }

        # The sector ETF the stock belongs to, if we can name its sector.
        sector = None
        if not isinstance(profile, Exception) and profile:
            sector = market_data.normalise_sector(profile.get("sector"))
        sector_etf = SECTOR_TO_ETF.get(sector) if sector else None
        sector_closes = await _adjusted_closes(sector_etf, start) if sector_etf else []

        spy_closes = [] if isinstance(spy_closes, Exception) else spy_closes

        windows = []
        for name, n in RS_WINDOWS:
            stock_ret = _return_over(stock_closes, n)
            if stock_ret is None:
                continue  # not enough history for this window
            spy_ret = _return_over(spy_closes, n)
            sector_ret = _return_over(sector_closes, n)
            windows.append(
                {
                    "window": name,
                    "sessions": n,
                    "stock_return": round(stock_ret, 4),
                    "vs_spy": round(stock_ret - spy_ret, 4) if spy_ret is not None else None,
                    "vs_sector": (
                        round(stock_ret - sector_ret, 4) if sector_ret is not None else None
                    ),
                }
            )

        if not windows and rvol is None:
            return {"symbol": symbol, "available": False, "reason": "Not enough history."}

        # Headline leadership read from the 3-month window (or the longest we have).
        headline = next((w for w in windows if w["window"] == "3M"), windows[-1] if windows else None)
        trend_label = _trend_label(headline["vs_spy"]) if headline else None

        return {
            "symbol": symbol,
            "available": True,
            "reason": None,
            "benchmark": BENCHMARK,
            "sector": sector,
            "sector_etf": sector_etf,
            "rvol": rvol,
            "windows": windows,
            "trend_label": trend_label,
            "as_of": datetime.now(timezone.utc).isoformat(),
        }

    try:
        return await cached_async(
            f"relative:{symbol}",
            RELATIVE_TTL,
            fetch,
            should_cache=lambda r: r.get("available") is True,
            lock=True,
        )
    except Exception as e:
        log.error("relative_fetch_failed", symbol=symbol, error=str(e))
        return {"symbol": symbol, "available": False, "reason": "Could not compute right now."}
