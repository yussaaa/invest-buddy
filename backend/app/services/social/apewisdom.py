"""Reddit mention counts from ApeWisdom — free, keyless, unofficial.

ApeWisdom counts ticker mentions across Reddit (WSB, r/stocks, r/options …)
over a rolling 24 hours and ranks them. One paged call returns, per ticker,
the rank and mention count now and 24 hours ago, which is enough for a
"trending" board and a crude change measure. A real baseline needs our own
history, which `history.py` stores.

Three things about the data that the rest of the app must not forget:

* **Absent from the board is not zero mentions.** Only the top few hundred
  are published. A ticker outside them gets `rank=None` and a caveat.
* **Counts are rolling 24h windows**, so hourly samples overlap heavily.
  Comparing day to day means one sample per day (history.py does this).
* **Some tickers are English words** (A, IT, ALL, ON …). Their counts are
  inflated by ordinary sentences and are flagged rather than trusted.
"""

from __future__ import annotations

import asyncio
import html
from datetime import datetime, timezone
from typing import Any, Optional

import structlog

from app.services.cache import cached_async
from app.services.social.http import get_json
from app.services.social.models import (
    ProviderStatus,
    RedditAttention,
    TrendingResult,
    TrendingRow,
)

log = structlog.get_logger(__name__)

BASE_URL = "https://apewisdom.io/api/v1.0/filter/{filter}/page/{page}"
TICKER_URL = "https://apewisdom.io/stocks/{symbol}/"

# Allowlisted so a query parameter can never become an arbitrary URL path.
FILTERS: dict[str, str] = {
    "all-stocks": "All stocks",
    "wallstreetbets": "r/wallstreetbets",
    "stocks": "r/stocks",
    "investing": "r/investing",
    "options": "r/options",
}
DEFAULT_FILTER = "all-stocks"

PAGE_TTL = 300.0
PAGE_SIZE = 100
MAX_LIMIT = 200
# Pages scanned when looking one ticker up — the top ~300 by mentions.
LOOKUP_PAGES = 3

# Real tickers that are also common words or Reddit slang.
AMBIGUOUS_TICKERS = frozenset({
    "A", "AI", "ALL", "ARE", "BE", "CAN", "DD", "EOD", "FOR", "GO", "IT",
    "NOW", "ON", "ONE", "OPEN", "OUT", "SO", "TV", "YOLO",
})


def _int(value: Any) -> Optional[int]:
    """ApeWisdom sends numbers as ints, strings, or null depending on the row."""
    if value is None or isinstance(value, bool):
        return None
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def change_pct(now: Optional[int], before: Optional[int]) -> Optional[float]:
    if now is None or not before:
        return None
    return round((now - before) / before * 100.0, 1)


def parse_row(raw: dict) -> Optional[dict]:
    """One board entry, normalised. None when it is unusable."""
    symbol = str(raw.get("ticker") or "").strip().upper()
    rank = _int(raw.get("rank"))
    mentions = _int(raw.get("mentions"))
    if not symbol or rank is None or mentions is None:
        return None

    rank_before = _int(raw.get("rank_24h_ago"))
    mentions_before = _int(raw.get("mentions_24h_ago"))
    return {
        "symbol": symbol,
        # Names arrive HTML-escaped ("S&amp;P 500").
        "name": html.unescape(str(raw.get("name") or symbol)),
        "rank": rank,
        "rank_24h_ago": rank_before,
        "rank_change": (rank_before - rank) if rank_before is not None else None,
        "mentions": mentions,
        "mentions_24h_ago": mentions_before,
        "mention_change_pct": change_pct(mentions, mentions_before),
        "upvotes": _int(raw.get("upvotes")),
    }


def parse_page(payload: Any) -> dict:
    if not isinstance(payload, dict):
        raise ValueError("ApeWisdom returned an unexpected payload")
    rows = [r for r in (parse_row(x) for x in payload.get("results") or [] if isinstance(x, dict)) if r]
    return {
        "count": _int(payload.get("count")),
        "pages": _int(payload.get("pages")),
        "rows": rows,
    }


async def fetch_page(filter: str, page: int) -> dict:
    """One cached board page. Raises on failure so the cache can serve stale."""
    async def produce() -> dict:
        payload = await get_json(BASE_URL.format(filter=filter, page=page))
        return parse_page(payload)

    return await cached_async(
        f"social:ape:{filter}:{page}", PAGE_TTL, produce,
        should_cache=lambda p: bool(p.get("rows")), lock=True,
    )


def _ambiguity_caveat(symbols: list[str]) -> Optional[str]:
    hits = [s for s in symbols if s in AMBIGUOUS_TICKERS]
    if not hits:
        return None
    return (f"{', '.join(hits)} {'is' if len(hits) == 1 else 'are'} also an ordinary "
            f"word, so mention counts are inflated by unrelated posts.")


async def trending(filter: str = DEFAULT_FILTER, limit: int = 50) -> TrendingResult:
    now = datetime.now(timezone.utc)
    if filter not in FILTERS:
        return TrendingResult(source="apewisdom", filter=filter, as_of=now, available=False,
                              caveat=f"Unknown filter {filter!r}.")
    limit = max(1, min(limit, MAX_LIMIT))
    pages = range(1, (limit - 1) // PAGE_SIZE + 2)

    try:
        results = await asyncio.gather(*(fetch_page(filter, p) for p in pages))
    except Exception as e:
        log.warning("apewisdom_trending_failed", filter=filter, error=str(e))
        return TrendingResult(source="apewisdom", filter=filter, as_of=now, available=False,
                              caveat=f"ApeWisdom is unavailable right now ({e}).")

    rows = [row for page in results for row in page["rows"]][:limit]
    return TrendingResult(
        source="apewisdom",
        filter=filter,
        as_of=now,
        total=results[0].get("count") if results else None,
        stale=any(page.get("stale") for page in results),
        caveat=_ambiguity_caveat([r["symbol"] for r in rows[:25]]),
        rows=[TrendingRow(**row) for row in rows],
    )


async def ticker(symbol: str) -> RedditAttention:
    """Where `symbol` sits on the all-stocks board, if it is on it at all."""
    symbol = symbol.upper()
    url = TICKER_URL.format(symbol=symbol)
    try:
        pages = await asyncio.gather(
            *(fetch_page(DEFAULT_FILTER, p) for p in range(1, LOOKUP_PAGES + 1))
        )
    except Exception as e:
        log.warning("apewisdom_lookup_failed", symbol=symbol, error=str(e))
        return RedditAttention(source="apewisdom", symbol=symbol, available=False, url=url,
                               caveat=f"ApeWisdom is unavailable right now ({e}).")

    stale = any(page.get("stale") for page in pages)
    for page in pages:
        for row in page["rows"]:
            if row["symbol"] == symbol:
                return RedditAttention(
                    source="apewisdom", url=url, stale=stale,
                    caveat=_ambiguity_caveat([symbol]),
                    **{k: v for k, v in row.items() if k != "name"},
                )

    return RedditAttention(
        source="apewisdom", symbol=symbol, url=url, stale=stale,
        caveat=(f"{symbol} is not in the top {LOOKUP_PAGES * PAGE_SIZE} by Reddit mentions. "
                f"That means little discussion, not none."),
    )


class ApeWisdomProvider:
    id = "apewisdom"
    label = "Reddit (ApeWisdom)"
    kind = "live"
    capabilities = ("trending", "ticker")
    filters = FILTERS

    async def status(self) -> ProviderStatus:
        from app.config import get_settings

        enabled = get_settings().social_enabled
        return ProviderStatus(
            id=self.id, label=self.label, kind=self.kind,
            capabilities=list(self.capabilities), enabled=enabled,
            state="live" if enabled else "disabled",
            reason=None if enabled else "SOCIAL_ENABLED is off",
        )

    async def trending(self, filter: str = DEFAULT_FILTER, limit: int = 50) -> TrendingResult:
        return await trending(filter, limit)

    async def ticker(self, symbol: str) -> RedditAttention:
        return await ticker(symbol)
