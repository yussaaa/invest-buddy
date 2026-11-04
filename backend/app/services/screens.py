"""Preset stock screens — Yahoo's predefined screeners, trimmed and cached.

The Market page's movers answer one question (what moved most today). These
answer a handful more — most active, most shorted, small-cap gainers,
growth at a reasonable multiple — using the screens Yahoo already maintains,
so there is no screening logic of ours to get wrong.

Rows reuse the movers' rules (US-listed equities, above the penny and
illiquidity floors) and add relative volume, which is the cheapest honest
"something is happening here" number. Each row is overlaid with its Reddit
rank from the cached mention board, at no extra request.

Technical signals are deliberately *not* computed per row: 25 indicator
fetches per refresh is the wrong cost for a table. The ticker drawer computes
them for the one row that was opened.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Optional

import structlog
import yfinance as yf

from app.services.cache import cached as _cached, peek
from app.services.market_data import (
    MIN_MOVER_PRICE,
    MIN_MOVER_VOLUME,
    US_EXCHANGES,
    _attach_sectors,
    _round,
    _safe,
)

log = structlog.get_logger(__name__)

SCREEN_TTL = 120.0
SCREEN_OVERSCAN = 100
MAX_LIMIT = 50

# id → (label, one-line hint). Ids are Yahoo's predefined screener names, the
# equity ones only — the fund and bond screens are out of scope here.
PRESETS: dict[str, dict[str, str]] = {
    "most_actives": {"label": "Most active", "hint": "Highest share volume today."},
    "day_gainers": {"label": "Day gainers", "hint": "Largest percentage gains today."},
    "day_losers": {"label": "Day losers", "hint": "Largest percentage declines today."},
    "small_cap_gainers": {"label": "Small-cap gainers", "hint": "Small caps up the most today."},
    "aggressive_small_caps": {"label": "Aggressive small caps",
                              "hint": "Small caps with high earnings growth."},
    "most_shorted_stocks": {"label": "Most shorted", "hint": "Highest short interest as a share of float."},
    "growth_technology_stocks": {"label": "Growth tech", "hint": "Tech names with 25%+ revenue and earnings growth."},
    "undervalued_growth_stocks": {"label": "Growth at low P/E",
                                  "hint": "Earnings growth above 25% with a P/E below 20."},
    "undervalued_large_caps": {"label": "Low-P/E large caps",
                               "hint": "Large caps with a P/E below 20 and PEG below 1."},
}
DEFAULT_PRESET = "most_actives"


def screen_row(quote: dict) -> Optional[dict]:
    """One screener quote → a table row, or None if it fails the floors."""
    symbol = (quote.get("symbol") or "").upper()
    if not symbol or quote.get("quoteType") != "EQUITY":
        return None
    if quote.get("exchange") not in US_EXCHANGES:
        return None
    price = _safe(quote.get("regularMarketPrice"))
    volume = _safe(quote.get("regularMarketVolume"))
    if price is None or price < MIN_MOVER_PRICE:
        return None
    if volume is None or volume < MIN_MOVER_VOLUME:
        return None

    avg_volume = _safe(quote.get("averageDailyVolume3Month"))
    return {
        "symbol": symbol,
        "name": quote.get("shortName") or quote.get("longName") or symbol,
        "sector": None,
        "last": _round(price, 2),
        "change_percent": _round(quote.get("regularMarketChangePercent"), 2),
        "volume": volume,
        "avg_volume": avg_volume,
        "rel_volume": round(volume / avg_volume, 2) if avg_volume else None,
        "market_cap": _safe(quote.get("marketCap")),
        "week52_change_percent": _round(quote.get("fiftyTwoWeekChangePercent"), 1),
        "exchange": quote.get("fullExchangeName"),
    }


def _run_screen(preset: str, limit: int) -> dict:
    result = yf.screen(preset, count=SCREEN_OVERSCAN) or {}
    rows: list[dict] = []
    for quote in result.get("quotes", []):
        row = screen_row(quote)
        if row:
            rows.append(row)
        if len(rows) >= limit:
            break
    return {"rows": rows}


def reddit_overlay() -> dict[str, dict]:
    """symbol → {rank, mention_change_pct} from whatever board pages are in L1.

    `peek` only — the overlay never triggers a fetch of its own. A cold board
    simply means no badges until the trending table has loaded once.
    """
    lookup: dict[str, dict] = {}
    for page in range(1, 4):
        cached_page = peek(f"social:ape:all-stocks:{page}")
        for row in (cached_page or {}).get("rows", []):
            lookup.setdefault(row["symbol"], {
                "rank": row["rank"], "mention_change_pct": row.get("mention_change_pct"),
            })
    return lookup


async def get_screen(preset: str = DEFAULT_PRESET, limit: int = 25) -> dict:
    meta = PRESETS.get(preset)
    if meta is None:
        return {"preset": preset, "rows": [], "error": f"unknown preset {preset!r}"}
    limit = max(1, min(limit, MAX_LIMIT))

    try:
        result = await _cached(
            f"screen:{preset}:{limit}", SCREEN_TTL, lambda: _run_screen(preset, limit),
            should_cache=lambda r: bool(r["rows"]), lock=True,
        )
    except Exception as e:
        log.error("screen_failed", preset=preset, error=str(e))
        return {"preset": preset, "label": meta["label"], "rows": [], "error": str(e)}

    rows = [dict(r) for r in result["rows"]]
    try:
        await asyncio.wait_for(_attach_sectors(rows), timeout=10)
    except Exception as e:                       # sectors are garnish
        log.warning("screen_sectors_failed", preset=preset, error=str(e))

    overlay = reddit_overlay()
    for row in rows:
        row["social"] = overlay.get(row["symbol"])

    return {
        "preset": preset,
        "label": meta["label"],
        "hint": meta["hint"],
        "rows": rows,
        "stale": bool(result.get("stale")),
        "as_of": datetime.now(timezone.utc).isoformat(),
    }


def list_presets() -> list[dict]:
    return [{"id": k, **v} for k, v in PRESETS.items()]
