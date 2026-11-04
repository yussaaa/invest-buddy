"""Self-tagged Bullish/Bearish messages from StockTwits — best effort.

The only free source where the crowd states a direction itself rather than
having one inferred from its words. Users tag a message Bullish or Bearish
(or nothing), and the ratio among tagged messages is the reading.

Two caveats travel with every reading:

* **Tags skew bullish structurally.** Most users tag only their longs, so
  around 60–65% bullish is the resting level, not a lean. The thresholds in
  `services/social_signals.py` are asymmetric for this reason.
* **The API sits behind Cloudflare** and routinely answers server clients
  with a 403 challenge page. A breaker remembers that for fifteen minutes so
  every drawer open is not another refused request, and the panel says
  "unavailable" rather than waiting on it.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

import structlog

from app.config import get_settings
from app.services import cache
from app.services.social.http import SourceBlocked, get_json
from app.services.social.models import CrowdTags, ProviderStatus

log = structlog.get_logger(__name__)

STREAM_URL = "https://api.stocktwits.com/api/2/streams/symbol/{symbol}.json"
SYMBOL_URL = "https://stocktwits.com/symbol/{symbol}"

STREAM_TTL = 300.0
BLOCK_KEY = "social:st:blocked"
BLOCK_SECONDS = 900.0

BLOCKED_CAVEAT = ("StockTwits is refusing requests from this server right now "
                  "(it sits behind Cloudflare); crowd tags are unavailable.")


def _parse_time(value: Any) -> Optional[datetime]:
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def parse_stream(payload: Any, symbol: str) -> dict:
    """Counts from one stream page. Bodies are read for nothing and kept nowhere."""
    if not isinstance(payload, dict) or not isinstance(payload.get("messages"), list):
        raise ValueError("StockTwits returned an unexpected payload")

    seen: set = set()
    bullish = bearish = untagged = 0
    times: list[datetime] = []
    for message in payload["messages"]:
        if not isinstance(message, dict):
            continue
        message_id = message.get("id")
        if message_id is not None:
            if message_id in seen:
                continue
            seen.add(message_id)

        sentiment = ((message.get("entities") or {}).get("sentiment") or {})
        tag = sentiment.get("basic") if isinstance(sentiment, dict) else None
        if tag == "Bullish":
            bullish += 1
        elif tag == "Bearish":
            bearish += 1
        else:
            untagged += 1

        when = _parse_time(message.get("created_at"))
        if when:
            times.append(when)

    tagged = bullish + bearish
    window = (max(times) - min(times)).total_seconds() / 3600 if len(times) >= 2 else None
    watchlist = (payload.get("symbol") or {}).get("watchlist_count")
    return {
        "symbol": symbol,
        "sampled": bullish + bearish + untagged,
        "bullish": bullish,
        "bearish": bearish,
        "untagged": untagged,
        "bull_pct": round(bullish / tagged * 100.0, 1) if tagged else None,
        "window_hours": round(window, 1) if window is not None else None,
        "watchlist_count": watchlist if isinstance(watchlist, int) else None,
    }


async def is_blocked() -> bool:
    seconds = await cache.age(BLOCK_KEY)
    return seconds is not None and seconds < BLOCK_SECONDS


async def _trip() -> None:
    await cache.prime(BLOCK_KEY, "1", BLOCK_SECONDS)


async def ticker(symbol: str) -> CrowdTags:
    symbol = symbol.upper()
    url = SYMBOL_URL.format(symbol=symbol)

    def unavailable(caveat: str) -> CrowdTags:
        return CrowdTags(source="stocktwits", symbol=symbol, available=False,
                         caveat=caveat, url=url)

    if not get_settings().stocktwits_enabled:
        return unavailable("StockTwits is turned off (STOCKTWITS_ENABLED).")
    if await is_blocked():
        return unavailable(BLOCKED_CAVEAT)

    async def produce() -> dict:
        url = STREAM_URL.format(symbol=symbol)
        try:
            payload = await get_json(url)
        except SourceBlocked:
            await _trip()
            raise
        # A page is 30 messages and well under half carry a tag, which leaves
        # a single page short of MIN_TAGGED most of the time. One more page
        # through the cursor; if it fails, the first page still stands.
        cursor = payload.get("cursor") if isinstance(payload, dict) else None
        if isinstance(cursor, dict) and cursor.get("more") and cursor.get("max"):
            try:
                older = await get_json(url, params={"max": cursor["max"]})
                payload = {**payload, "messages": [*payload.get("messages", []),
                                                   *(older.get("messages") or [])]}
            except Exception as e:
                log.info("stocktwits_second_page_skipped", symbol=symbol, error=str(e))
        return parse_stream(payload, symbol)

    try:
        data = await cache.cached_async(
            f"social:st:{symbol}", STREAM_TTL, produce,
            should_cache=lambda d: d.get("sampled", 0) > 0,
        )
    except SourceBlocked as e:
        log.info("stocktwits_blocked", symbol=symbol, status=e.status)
        return unavailable(BLOCKED_CAVEAT)
    except Exception as e:
        log.warning("stocktwits_failed", symbol=symbol, error=str(e))
        return unavailable(f"StockTwits is unavailable right now ({e}).")

    caveat = None
    if data.get("sampled") and (data.get("bullish", 0) + data.get("bearish", 0)) == 0:
        caveat = "None of the recent messages carry a Bullish or Bearish tag."
    return CrowdTags(source="stocktwits", url=url, caveat=caveat,
                     stale=bool(data.get("stale")),
                     **{k: v for k, v in data.items() if k != "stale"})


class StockTwitsProvider:
    id = "stocktwits"
    label = "StockTwits"
    kind = "live"
    capabilities = ("ticker",)

    async def status(self) -> ProviderStatus:
        settings = get_settings()
        if not (settings.social_enabled and settings.stocktwits_enabled):
            return ProviderStatus(id=self.id, label=self.label, kind=self.kind,
                                  capabilities=list(self.capabilities), enabled=False,
                                  state="disabled", reason="STOCKTWITS_ENABLED is off")
        blocked = await is_blocked()
        return ProviderStatus(
            id=self.id, label=self.label, kind=self.kind,
            capabilities=list(self.capabilities), enabled=True,
            state="blocked" if blocked else "live",
            reason="Refused by Cloudflare recently; retrying after a pause" if blocked else None,
        )

    async def ticker(self, symbol: str) -> CrowdTags:
        return await ticker(symbol)
