"""Relative strength and relative volume for one stock.

Two readings a trader checks before acting on anything else: is the name
*leading or lagging* its benchmarks (relative strength), and is *more than the
usual* volume behind today's move (relative volume). Both are deterministic
arithmetic over bars the store already holds — SPY and the eleven SPDR sector
ETFs are warmed to five years alongside everything else — so this composes
existing reads and adds no new data source.

Relative strength is measured against SPY and the stock's own sector ETF by
default, plus any extra tickers the caller names (gold, oil, bonds, …). A
custom benchmark the store has never seen is a one-time backfill, then cached
like everything else. Returns scalars only, the same discipline as the trend
summary: nothing here is a recommendation, only a measurement of where the
stock sits relative to its benchmarks.
"""

from __future__ import annotations

import asyncio
import re
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
WINDOW_SESSIONS = dict(RS_WINDOWS)
# Calendar days to request so the longest window (126 sessions) has a buffer.
LOOKBACK_DAYS = 300

# A caller can name at most this many extra benchmarks — each is a bar read.
MAX_EXTRA = 5
_TICKER = re.compile(r"^[A-Z0-9.\-^=]{1,12}$")

# Friendlier labels for the macro proxies the UI offers as one-tap chips; any
# other ticker shows as itself.
PRESET_LABELS = {
    "GLD": "Gold",
    "USO": "Oil",
    "TLT": "Bonds",
    "UUP": "Dollar",
    "IBIT": "Bitcoin",
}

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


def _sanitise_extra(extra: list[str] | None, symbol: str) -> list[str]:
    """Clean the caller's extra tickers: valid, de-duped, bounded, not the stock."""
    if not extra:
        return []
    seen: set[str] = {symbol.upper(), BENCHMARK}
    out: list[str] = []
    for raw in extra:
        sym = str(raw).strip().upper()
        if sym and sym not in seen and _TICKER.match(sym):
            seen.add(sym)
            out.append(sym)
        if len(out) >= MAX_EXTRA:
            break
    return out


async def _adjusted_closes(symbol: str, start: date) -> list[float]:
    """Settled, split-adjusted closes for one symbol — [] if the store can't answer."""
    try:
        rows = await bar_store.daily_bars_for(symbol, start=start, adjusted=True)
    except Exception as e:
        log.warning("relative_bars_failed", symbol=symbol, error=str(e))
        return []
    return [r.close for r in rows if r.close is not None]


async def get_relative(symbol: str, extra: list[str] | None = None) -> dict:
    """Relative strength (vs SPY, sector and any extra tickers) and relative volume."""
    symbol = symbol.upper()
    extra = _sanitise_extra(extra, symbol)

    async def fetch() -> dict:
        start = date.today() - timedelta(days=LOOKBACK_DAYS)

        stock_task = bar_store.daily_bars_for(symbol, start=start, adjusted=True)
        quotes_task = market_data.get_quotes([symbol])
        profile_task = market_data.get_profile(symbol)
        spy_task = _adjusted_closes(BENCHMARK, start)
        extra_tasks = [_adjusted_closes(e, start) for e in extra]
        results = await asyncio.gather(
            stock_task, quotes_task, profile_task, spy_task, *extra_tasks,
            return_exceptions=True,
        )
        stock_rows, quotes, profile, spy_closes = results[:4]
        extra_closes = dict(zip(extra, results[4:]))

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

        # The stock's own return per window it has the history for.
        stock_returns: dict[str, float] = {}
        for name, n in RS_WINDOWS:
            r = _return_over(stock_closes, n)
            if r is not None:
                stock_returns[name] = round(r, 4)
        windows = list(stock_returns.keys())

        if not windows and rvol is None:
            return {"symbol": symbol, "available": False, "reason": "Not enough history."}

        def _excess(closes: list[float]) -> dict[str, float | None]:
            """Stock's excess return over one benchmark, per available window."""
            out: dict[str, float | None] = {}
            for name in windows:
                br = _return_over(closes, WINDOW_SESSIONS[name])
                out[name] = round(stock_returns[name] - br, 4) if br is not None else None
            return out

        # Benchmarks: SPY, the sector ETF, then the caller's extras in order.
        sector = None
        if not isinstance(profile, Exception) and profile:
            sector = market_data.normalise_sector(profile.get("sector"))
        sector_etf = SECTOR_TO_ETF.get(sector) if sector else None

        spy_closes = [] if isinstance(spy_closes, Exception) else spy_closes
        benchmarks = [
            {"symbol": BENCHMARK, "label": BENCHMARK, "kind": "market", "excess": _excess(spy_closes)}
        ]
        if sector_etf:
            sector_closes = await _adjusted_closes(sector_etf, start)
            benchmarks.append(
                {"symbol": sector_etf, "label": sector, "kind": "sector", "excess": _excess(sector_closes)}
            )
        for sym in extra:
            closes = extra_closes.get(sym)
            closes = [] if isinstance(closes, Exception) or closes is None else closes
            benchmarks.append(
                {
                    "symbol": sym,
                    "label": PRESET_LABELS.get(sym, sym),
                    "kind": "custom",
                    "excess": _excess(closes),
                    "no_data": not closes,
                }
            )

        # Headline leadership read vs SPY, from the 3-month window or the longest held.
        spy_excess = benchmarks[0]["excess"]
        headline_window = "3M" if "3M" in windows else (windows[-1] if windows else None)
        trend_label = _trend_label(spy_excess.get(headline_window)) if headline_window else None

        return {
            "symbol": symbol,
            "available": True,
            "reason": None,
            "benchmark": BENCHMARK,
            "sector": sector,
            "sector_etf": sector_etf,
            "rvol": rvol,
            "windows": windows,
            "stock_returns": stock_returns,
            "benchmarks": benchmarks,
            "trend_label": trend_label,
            "as_of": datetime.now(timezone.utc).isoformat(),
        }

    try:
        return await cached_async(
            f"relative:{symbol}:{','.join(sorted(extra))}",
            RELATIVE_TTL,
            fetch,
            should_cache=lambda r: r.get("available") is True,
            lock=True,
        )
    except Exception as e:
        log.error("relative_fetch_failed", symbol=symbol, error=str(e))
        return {"symbol": symbol, "available": False, "reason": "Could not compute right now."}
