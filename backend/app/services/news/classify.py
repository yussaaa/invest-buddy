"""Sector and industry for the tickers a headline names.

Filled just after ingest by the runner, never on a page load: a Yahoo profile
is one request per symbol, and a wire must not wait on it. The row is then
restamped, so pages already open receive it over the stream.

Yahoo's taxonomy, not GICS, because the profile panel above the chart uses
Yahoo's and the two should agree; sectors go through normalise_sector so they
match the sector-performance panel's labels.

Each symbol is cached for a week, with two sentinels: "" means Yahoo has no
profile (a crypto pair, a foreign listing) and is final; "?" means the lookup
itself failed (rate limit, network) and is retried after FAILED_TTL. Only
final answers reach a row, so a bad minute at Yahoo leaves the row blank for
a retry rather than marked as having no industry.
"""

from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from typing import Optional

import structlog
import yfinance as yf

from app.services.cache import get_fresh_many, put_many

log = structlog.get_logger(__name__)

PROFILE_TTL = 7 * 86400.0     # a company changes industry roughly never
FAILED_TTL = 3600.0
NO_PROFILE = ""
FAILED = "?"
WORKERS = 8

SymbolInfo = Optional[dict]   # {"sector": str | None, "industry": str | None} or None


def _key(symbol: str) -> str:
    return f"symclass:{symbol}"


def profile_of(info: dict) -> SymbolInfo:
    """The sector/industry pair from a Yahoo `info` dict, or None if it has neither."""
    from app.services.market_data import normalise_sector

    sector = normalise_sector(info.get("sector"))
    industry = str(info.get("industry") or "").strip() or None
    if not (sector or industry):
        return None
    return {"sector": sector, "industry": industry}


def _lookup(symbol: str) -> SymbolInfo | str:
    try:
        # Class shares: the wire writes BRK.B, Yahoo wants BRK-B.
        info = yf.Ticker(symbol.replace(".", "-")).info or {}
    except Exception as e:
        log.debug("symbol_profile_failed", symbol=symbol, error=str(e)[:120])
        return FAILED
    return profile_of(info) or NO_PROFILE


async def classify(symbols: list[str]) -> dict[str, SymbolInfo]:
    """Sector/industry for each symbol with a final answer; failed ones are left out."""
    if not symbols:
        return {}
    keys = {s: _key(s) for s in dict.fromkeys(symbols)}
    # Freshness is judged by the TTL passed on read, so read twice: anything
    # written within FAILED_TTL counts, failures included; older than that,
    # only final answers do, and an old failure is looked up again.
    hits = await get_fresh_many(list(keys.values()), FAILED_TTL)
    older = [k for k in keys.values() if k not in hits]
    if older:
        hits.update({k: v for k, v in (await get_fresh_many(older, PROFILE_TTL)).items()
                     if v != FAILED})
    missing = [s for s, k in keys.items() if k not in hits]

    if missing:
        def fetch() -> dict[str, SymbolInfo | str]:
            with ThreadPoolExecutor(max_workers=WORKERS) as pool:
                return dict(zip(missing, pool.map(_lookup, missing)))

        fetched = await asyncio.to_thread(fetch)
        final = {keys[s]: v for s, v in fetched.items() if v != FAILED}
        failed = {keys[s]: FAILED for s, v in fetched.items() if v == FAILED}
        await put_many(final, PROFILE_TTL)
        await put_many(failed, FAILED_TTL)
        hits.update(final)
        hits.update(failed)

    out: dict[str, SymbolInfo] = {}
    for symbol, key in keys.items():
        value = hits.get(key, FAILED)
        if value == FAILED:
            continue
        out[symbol] = value or None
    return out
