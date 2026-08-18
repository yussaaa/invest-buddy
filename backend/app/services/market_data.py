"""Market data for the UI: quotes, chart history, index overview, week events.

These feed dashboard views that poll every few seconds, so everything here is
batched into as few upstream calls as possible and served from a TTL cache.
Separate from app/tools/ — those are agent tools, these are plain HTTP reads.

Data source is yfinance (delayed quotes) plus, optionally, the FRED release
calendar when FRED_API_KEY is configured.
"""

from __future__ import annotations

import asyncio
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
from io import StringIO
from typing import Any, Callable
from zoneinfo import ZoneInfo

import httpx
import pandas as pd
import structlog
import yfinance as yf

from app.config import get_settings
from app.services.cache import cached as _cached, get_fresh_many, put_many

log = structlog.get_logger(__name__)

# ── groups shown on the market overview ───────────────────────────────────────

INDICES = [
    {"symbol": "^GSPC", "label": "S&P 500", "note": "US large cap"},
    {"symbol": "^IXIC", "label": "Nasdaq Composite", "note": "US tech-heavy"},
    {"symbol": "^DJI", "label": "Dow Jones", "note": "US blue chip"},
    {"symbol": "^RUT", "label": "Russell 2000", "note": "US small cap"},
    {"symbol": "^VIX", "label": "VIX", "note": "Volatility"},
]

SECTORS = [
    {"symbol": "XLK", "label": "Technology"},
    {"symbol": "XLF", "label": "Financials"},
    {"symbol": "XLV", "label": "Health Care"},
    {"symbol": "XLY", "label": "Cons. Discretionary"},
    {"symbol": "XLP", "label": "Cons. Staples"},
    {"symbol": "XLE", "label": "Energy"},
    {"symbol": "XLI", "label": "Industrials"},
    {"symbol": "XLB", "label": "Materials"},
    {"symbol": "XLRE", "label": "Real Estate"},
    {"symbol": "XLU", "label": "Utilities"},
    {"symbol": "XLC", "label": "Comm. Services"},
]

MACRO = [
    {"symbol": "^TNX", "label": "US 10Y Yield", "unit": "%"},
    {"symbol": "DX-Y.NYB", "label": "Dollar Index"},
    {"symbol": "GC=F", "label": "Gold"},
    {"symbol": "CL=F", "label": "Crude Oil"},
    {"symbol": "BTC-USD", "label": "Bitcoin"},
    {"symbol": "ETH-USD", "label": "Ethereum"},
]

# Universe scanned for the "earnings this week" panel.
EARNINGS_UNIVERSE = [
    "AAPL", "MSFT", "NVDA", "GOOGL", "AMZN", "META", "TSLA", "AVGO", "BRK-B",
    "JPM", "V", "MA", "UNH", "XOM", "JNJ", "WMT", "PG", "HD", "COST", "ORCL",
    "AMD", "NFLX", "CRM", "ADBE", "INTC", "QCOM", "CSCO", "PEP", "KO", "MCD",
    "BAC", "GS", "MS", "PFE", "MRK", "LLY", "CVX", "BA", "CAT", "DIS",
]

# FRED releases worth surfacing — everything else is noise for a markets view.
MAJOR_FRED_RELEASES = {
    "employment situation": "high",
    "consumer price index": "high",
    "producer price index": "high",
    "gross domestic product": "high",
    "personal income and outlays": "high",
    "advance monthly sales for retail and food services": "high",
    "job openings and labor turnover survey": "medium",
    "industrial production and capacity utilization": "medium",
    "new residential construction": "medium",
    "consumer credit": "medium",
    "university of michigan: consumer sentiment": "medium",
    "h.4.1 factors affecting reserve balances": "medium",
}

# Index membership for the breadth panel. Constituents come from Wikipedia's
# tables (the only free source with GICS sectors attached); Dow 30 is small and
# stable enough to also serve as the offline fallback.
INDEX_SOURCES: dict[str, dict] = {
    "sp500": {
        "label": "S&P 500",
        "url": "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies",
        "symbol_col": "Symbol",
        "name_col": "Security",
        "sector_col": "GICS Sector",
    },
    "nasdaq100": {
        # Wikipedia dropped its components table, so membership comes from
        # Slickcharts and sectors are filled in from the S&P 500 table.
        "label": "Nasdaq 100",
        "url": "https://www.slickcharts.com/nasdaq100",
        "symbol_col": "Symbol",
        "name_col": "Company",
        "sector_col": None,
    },
    "dow30": {
        "label": "Dow Jones 30",
        "url": "https://en.wikipedia.org/wiki/Dow_Jones_Industrial_Average",
        "symbol_col": "Symbol",
        "name_col": "Company",
        "sector_col": "Sector",
    },
    # Not shown as breadth rows — these exist so the movers panel can name a
    # sector for most mid- and small-cap names without a per-symbol lookup.
    "sp400": {
        "label": "S&P MidCap 400",
        "url": "https://en.wikipedia.org/wiki/List_of_S%26P_400_companies",
        "symbol_col": "Symbol",
        "name_col": "Security",
        "sector_col": "GICS Sector",
    },
    "sp600": {
        "label": "S&P SmallCap 600",
        "url": "https://en.wikipedia.org/wiki/List_of_S%26P_600_companies",
        "symbol_col": "Symbol",
        "name_col": "Security",
        "sector_col": "GICS Sector",
    },
}

DOW30_FALLBACK = [
    "AAPL", "AMGN", "AMZN", "AXP", "BA", "CAT", "CRM", "CSCO", "CVX", "DIS",
    "GS", "HD", "HON", "IBM", "JNJ", "JPM", "KO", "MCD", "MMM", "MRK",
    "MSFT", "NKE", "NVDA", "PG", "SHW", "TRV", "UNH", "V", "VZ", "WMT",
]

# range -> (yfinance period, yfinance interval)
RANGE_PRESETS: dict[str, tuple[str, str]] = {
    "1D": ("1d", "5m"),
    "5D": ("5d", "15m"),
    "1M": ("1mo", "1h"),
    "3M": ("3mo", "1d"),
    "6M": ("6mo", "1d"),
    "YTD": ("ytd", "1d"),
    "1Y": ("1y", "1d"),
    "5Y": ("5y", "1wk"),
    "MAX": ("max", "1mo"),
}

INTRADAY_INTERVALS = {"1m", "2m", "5m", "15m", "30m", "60m", "90m", "1h"}

def _safe(val: Any) -> Any:
    if val is None:
        return None
    if isinstance(val, float) and (pd.isna(val) or val in (float("inf"), float("-inf"))):
        return None
    return val


def _round(val: Any, digits: int = 4) -> Any:
    val = _safe(val)
    return round(float(val), digits) if val is not None else None


# ── quotes ────────────────────────────────────────────────────────────────────

QUOTES_TTL = 10.0
MAX_SYMBOLS = 60


def _frame_for(raw: pd.DataFrame, symbol: str) -> pd.DataFrame | None:
    """Pull one symbol's frame out of a (possibly multi-index) batch download."""
    if raw is None or raw.empty:
        return None
    if isinstance(raw.columns, pd.MultiIndex):
        if symbol not in raw.columns.get_level_values(0):
            return None
        return raw[symbol]
    return raw


def _quote_from_frame(symbol: str, df: pd.DataFrame) -> dict:
    closes = df["Close"].dropna() if "Close" in df else pd.Series(dtype=float)
    if closes.empty:
        return {"symbol": symbol, "error": "no data"}

    last = float(closes.iloc[-1])
    prev = float(closes.iloc[-2]) if len(closes) > 1 else last
    change = last - prev

    volume = None
    if "Volume" in df:
        vols = df["Volume"].dropna()
        if not vols.empty:
            volume = int(vols.iloc[-1])

    return {
        "symbol": symbol,
        "last": _round(last),
        "prev_close": _round(prev),
        "change": _round(change),
        "change_percent": _round(change / prev * 100.0 if prev else 0.0),
        "volume": volume,
    }


def _download(symbols: list[str], period: str = "5d", interval: str = "1d") -> pd.DataFrame:
    return yf.download(
        tickers=symbols,
        period=period,
        interval=interval,
        group_by="ticker",
        auto_adjust=False,
        progress=False,
        threads=True,
    )


async def get_quotes(symbols: list[str]) -> list[dict]:
    """Last price / change / volume for a batch of symbols.

    Batches its own fetch rather than going through `cached()` per symbol: one
    download covers every stale symbol, so the cache is read and written in
    bulk too.
    """
    symbols = symbols[:MAX_SYMBOLS]

    cache_keys = {sym: f"quote:{sym}" for sym in symbols}
    hits = await get_fresh_many(list(cache_keys.values()), QUOTES_TTL)

    out: dict[str, dict] = {}
    missing: list[str] = []
    for sym, key in cache_keys.items():
        if key in hits:
            out[sym] = hits[key]
        else:
            missing.append(sym)

    if missing:
        def fetch() -> dict[str, dict]:
            raw = _download(missing)
            result = {}
            for sym in missing:
                frame = _frame_for(raw, sym)
                result[sym] = (
                    _quote_from_frame(sym, frame)
                    if frame is not None
                    else {"symbol": sym, "error": "no data"}
                )
            return result

        try:
            fetched = await asyncio.to_thread(fetch)
        except Exception as e:
            log.error("quotes_fetch_failed", symbols=missing, error=str(e))
            fetched = {s: {"symbol": s, "error": str(e)} for s in missing}

        # Only good quotes are cached, so a failed symbol retries next poll.
        to_cache = {
            cache_keys[sym]: quote
            for sym, quote in fetched.items()
            if "error" not in quote
        }
        await put_many(to_cache, QUOTES_TTL)
        out.update(fetched)

    return [out.get(s, {"symbol": s, "error": "not found"}) for s in symbols]


# ── chart history ─────────────────────────────────────────────────────────────

HISTORY_TTL = 30.0


async def get_history(symbol: str, range_key: str = "1Y") -> dict:
    """OHLCV candles for the charting view."""
    range_key = range_key.upper()
    period, interval = RANGE_PRESETS.get(range_key, RANGE_PRESETS["1Y"])
    symbol = symbol.upper()

    def fetch() -> dict:
        hist = yf.Ticker(symbol).history(period=period, interval=interval, auto_adjust=False)
        if hist is None or hist.empty:
            return {
                "symbol": symbol,
                "range": range_key,
                "interval": interval,
                "candles": [],
                "error": "no data",
            }

        intraday = interval in INTRADAY_INTERVALS
        candles = []
        for idx, row in hist.iterrows():
            close = _safe(row.get("Close"))
            if close is None:
                continue
            ts = idx.to_pydatetime()
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            candles.append({
                # Lightweight Charts takes unix seconds for intraday and a
                # calendar date for daily-and-coarser series.
                "time": int(ts.timestamp()) if intraday else ts.strftime("%Y-%m-%d"),
                "open": _round(row.get("Open"), 4),
                "high": _round(row.get("High"), 4),
                "low": _round(row.get("Low"), 4),
                "close": _round(close, 4),
                "volume": int(row["Volume"]) if _safe(row.get("Volume")) else 0,
            })

        first_close = candles[0]["close"] if candles else None
        last_close = candles[-1]["close"] if candles else None
        change = (last_close - first_close) if (first_close and last_close) else None

        return {
            "symbol": symbol,
            "range": range_key,
            "interval": interval,
            "intraday": intraday,
            "candles": candles,
            "period_change": _round(change),
            "period_change_percent": _round(
                change / first_close * 100.0 if change is not None and first_close else None
            ),
        }

    try:
        return await _cached(f"hist:{symbol}:{range_key}", HISTORY_TTL, fetch)
    except Exception as e:
        log.error("history_fetch_failed", symbol=symbol, range=range_key, error=str(e))
        return {"symbol": symbol, "range": range_key, "candles": [], "error": str(e)}


# ── instrument profile (chart header) ─────────────────────────────────────────

PROFILE_TTL = 600.0


async def get_profile(symbol: str) -> dict:
    """Name, exchange and the key stats shown above the chart."""
    symbol = symbol.upper()

    def fetch() -> dict:
        info = yf.Ticker(symbol).info or {}
        return {
            "symbol": symbol,
            "name": info.get("longName") or info.get("shortName") or symbol,
            "exchange": info.get("fullExchangeName") or info.get("exchange"),
            "currency": info.get("currency"),
            "sector": info.get("sector"),
            "industry": info.get("industry"),
            "market_cap": _safe(info.get("marketCap")),
            "pe_ratio": _round(info.get("trailingPE"), 2),
            "forward_pe": _round(info.get("forwardPE"), 2),
            "dividend_yield": _round(info.get("dividendYield"), 4),
            "beta": _round(info.get("beta"), 2),
            "day_low": _round(info.get("dayLow"), 2),
            "day_high": _round(info.get("dayHigh"), 2),
            "week52_low": _round(info.get("fiftyTwoWeekLow"), 2),
            "week52_high": _round(info.get("fiftyTwoWeekHigh"), 2),
            "avg_volume": _safe(info.get("averageVolume")),
            "next_earnings": None,
        }

    try:
        return await _cached(f"profile:{symbol}", PROFILE_TTL, fetch)
    except Exception as e:
        log.error("profile_fetch_failed", symbol=symbol, error=str(e))
        return {"symbol": symbol, "name": symbol, "error": str(e)}


# ── market overview ───────────────────────────────────────────────────────────

OVERVIEW_TTL = 30.0
SPARKLINE_POINTS = 30


def _group_snapshot(raw: pd.DataFrame, group: list[dict]) -> list[dict]:
    rows = []
    for entry in group:
        sym = entry["symbol"]
        frame = _frame_for(raw, sym)
        closes = (
            frame["Close"].dropna()
            if frame is not None and "Close" in frame
            else pd.Series(dtype=float)
        )
        if closes.empty:
            rows.append({**entry, "error": "no data"})
            continue

        last = float(closes.iloc[-1])
        prev = float(closes.iloc[-2]) if len(closes) > 1 else last
        first = float(closes.iloc[0])
        change = last - prev

        rows.append({
            **entry,
            "last": _round(last, 2),
            "prev_close": _round(prev, 2),
            "change": _round(change, 2),
            "change_percent": _round(change / prev * 100.0 if prev else 0.0, 2),
            "period_change_percent": _round(
                (last - first) / first * 100.0 if first else 0.0, 2
            ),
            "sparkline": [round(float(c), 4) for c in closes.tail(SPARKLINE_POINTS)],
        })
    return rows


async def get_overview() -> dict:
    """Indices, sector performance and macro benchmarks in one batch."""
    groups = INDICES + SECTORS + MACRO
    symbols = [g["symbol"] for g in groups]

    def fetch() -> dict:
        raw = _download(symbols, period="1mo", interval="1d")
        indices = _group_snapshot(raw, INDICES)
        sectors = _group_snapshot(raw, SECTORS)
        macro = _group_snapshot(raw, MACRO)

        scored = [s for s in sectors if s.get("change_percent") is not None]
        advancing = sum(1 for s in scored if s["change_percent"] > 0)
        sectors_sorted = sorted(
            scored, key=lambda s: s["change_percent"], reverse=True
        )

        return {
            "indices": indices,
            "sectors": sectors_sorted,
            "macro": macro,
            "breadth": {
                "sectors_advancing": advancing,
                "sectors_total": len(scored),
                "best": sectors_sorted[0] if sectors_sorted else None,
                "worst": sectors_sorted[-1] if sectors_sorted else None,
            },
            "as_of": datetime.now(timezone.utc).isoformat(),
        }

    def complete(result: dict) -> bool:
        return all(row.get("last") is not None for row in result["indices"])

    try:
        return await _cached(
            "overview", OVERVIEW_TTL, fetch, should_cache=complete, lock=True
        )
    except Exception as e:
        log.error("overview_fetch_failed", error=str(e))
        return {"indices": [], "sectors": [], "macro": [], "error": str(e)}


# ── week ahead: earnings + economic releases ──────────────────────────────────

EVENTS_TTL = 1800.0


def _week_bounds(days: int) -> tuple[date, date]:
    """Today through +days, anchored to US market time.

    yfinance only exposes each company's *next* scheduled report, so a
    Monday-anchored week would silently drop anyone who already reported.
    A forward-looking window is what the data can actually support.

    The anchor is New York rather than UTC: after 8pm ET, UTC has already
    rolled over and today's reports would drop off the calendar.
    """
    today = datetime.now(ZoneInfo("America/New_York")).date()
    return today, today + timedelta(days=days)


def _earnings_for(symbol: str) -> dict | None:
    """Next scheduled earnings date for one symbol, or None."""
    try:
        cal = yf.Ticker(symbol).calendar or {}
    except Exception:
        return None

    dates = cal.get("Earnings Date") or []
    if isinstance(dates, (date, datetime)):
        dates = [dates]
    if not dates:
        return None

    first = dates[0]
    when = first.date() if isinstance(first, datetime) else first
    return {
        "symbol": symbol,
        "date": when.isoformat(),
        "eps_estimate": _round(cal.get("Earnings Average"), 3),
        "revenue_estimate": _safe(cal.get("Revenue Average")),
    }


def _fetch_earnings(universe: list[str], start: date, end: date) -> list[dict]:
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(_earnings_for, universe))

    within = []
    for r in results:
        if not r:
            continue
        when = date.fromisoformat(r["date"])
        if start <= when <= end:
            within.append(r)
    return sorted(within, key=lambda r: (r["date"], r["symbol"]))


def _fetch_economic(start: date, end: date) -> dict:
    """Economic releases from FRED. Requires FRED_API_KEY; empty without it."""
    api_key = get_settings().fred_api_key
    if not api_key:
        return {"events": [], "source": "fred", "available": False}

    try:
        resp = httpx.get(
            "https://api.stlouisfed.org/fred/releases/dates",
            params={
                "api_key": api_key,
                "file_type": "json",
                "realtime_start": start.isoformat(),
                "realtime_end": end.isoformat(),
                "include_release_dates_with_no_data": "true",
                "sort_order": "asc",
                "limit": 1000,
            },
            timeout=15.0,
        )
        resp.raise_for_status()
        payload = resp.json()
    except Exception as e:
        log.error("fred_fetch_failed", error=str(e))
        return {"events": [], "source": "fred", "available": False, "error": str(e)}

    events, seen = [], set()
    for item in payload.get("release_dates", []):
        name = (item.get("release_name") or "").strip()
        lowered = name.lower()
        importance = next(
            (imp for key, imp in MAJOR_FRED_RELEASES.items() if key in lowered), None
        )
        if not importance:
            continue
        key = (name, item.get("date"))
        if key in seen:
            continue
        seen.add(key)
        events.append({
            "name": name,
            "date": item.get("date"),
            "importance": importance,
            "source": "FRED",
        })

    return {
        "events": sorted(events, key=lambda e: (e["date"], e["name"])),
        "source": "fred",
        "available": True,
    }


# ── index constituents ────────────────────────────────────────────────────────

CONSTITUENTS_TTL = 86400.0     # membership changes a few times a year

_SCRAPE_HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; agent-invest/0.1; portfolio project)"
}


def _fetch_constituents(index_key: str) -> list[dict]:
    """Scrape index membership (and sector, where the source has one)."""
    source = INDEX_SOURCES[index_key]
    resp = httpx.get(source["url"], headers=_SCRAPE_HEADERS, timeout=20.0, follow_redirects=True)
    resp.raise_for_status()

    for table in pd.read_html(StringIO(resp.text)):
        if source["symbol_col"] not in set(table.columns):
            continue
        rows = []
        for _, row in table.iterrows():
            # Wikipedia writes class shares as BRK.B; yfinance wants BRK-B.
            symbol = str(row[source["symbol_col"]]).strip().upper().replace(".", "-")
            if not symbol or symbol == "NAN":
                continue
            sector_col = source["sector_col"]
            rows.append({
                "symbol": symbol,
                "name": str(row.get(source["name_col"], symbol)),
                "sector": str(row.get(sector_col, "Other")) if sector_col else "Other",
            })
        if len(rows) >= 20:  # skip small unrelated tables on the same page
            return rows

    raise ValueError(f"no constituent table found for {index_key}")


# Wikipedia gives GICS names, yfinance gives Yahoo's own taxonomy, and the two
# disagree on four sectors. Normalise both onto the labels already used by the
# sector-performance panel, so one movers list never mixes "Financials" with
# "Financial Services".
SECTOR_ALIASES = {
    "information technology": "Technology",
    "technology": "Technology",
    "financial services": "Financials",
    "financials": "Financials",
    "financial": "Financials",
    "consumer cyclical": "Consumer Discretionary",
    "consumer discretionary": "Consumer Discretionary",
    "consumer defensive": "Consumer Staples",
    "consumer staples": "Consumer Staples",
    "healthcare": "Health Care",
    "health care": "Health Care",
    "basic materials": "Materials",
    "materials": "Materials",
    "communication services": "Communication Services",
    "industrials": "Industrials",
    "energy": "Energy",
    "utilities": "Utilities",
    "real estate": "Real Estate",
}


def normalise_sector(raw: Any) -> str | None:
    if not raw or str(raw).strip().lower() in {"nan", "none", "other", ""}:
        return None
    return SECTOR_ALIASES.get(str(raw).strip().lower(), str(raw).strip())


def _sector_lookup() -> dict[str, str]:
    """GICS sector by symbol, borrowed from the S&P 500 table."""
    try:
        return {m["symbol"]: m["sector"] for m in _fetch_constituents("sp500")}
    except Exception as e:
        log.warning("sector_lookup_failed", error=str(e))
        return {}


def _constituents_with_fallback(index_key: str) -> list[dict]:
    try:
        members = _fetch_constituents(index_key)
    except Exception as e:
        log.error("constituents_fetch_failed", index=index_key, error=str(e))
        if index_key == "dow30":
            return [{"symbol": s, "name": s, "sector": "Other"} for s in DOW30_FALLBACK]
        return []

    if not INDEX_SOURCES[index_key]["sector_col"]:
        lookup = _sector_lookup()
        for m in members:
            m["sector"] = lookup.get(m["symbol"], "Other")
    return members


# ── advance/decline breadth ───────────────────────────────────────────────────

# The scan prices ~700 constituents and takes minutes. `cached` stamps a value
# with the time its fetch *started*, so a TTL shorter than the fetch produces
# values that are already expired when written — they can never satisfy a
# lookup, and every single request pays for a full cold scan. The TTL has to
# exceed how long the work takes, not how fresh we would like the answer.
BREADTH_TTL = 900.0

BREADTH_INDICES = ["sp500", "nasdaq100", "dow30"]


def _day_change(raw: pd.DataFrame, symbol: str) -> float | None:
    """Percent change vs the previous session's close."""
    frame = _frame_for(raw, symbol)
    if frame is None or "Close" not in frame:
        return None
    closes = frame["Close"].dropna()
    if len(closes) < 2:
        return None
    prev = float(closes.iloc[-2])
    if not prev:
        return None
    return (float(closes.iloc[-1]) - prev) / prev * 100.0


async def get_breadth() -> dict:
    """How many constituents of each index are up vs down on the day."""
    members_by_index = {}
    for key in BREADTH_INDICES:
        members_by_index[key] = await _cached(
            f"constituents:{key}",
            CONSTITUENTS_TTL,
            lambda k=key: _constituents_with_fallback(k),
        )

    # Indices overlap heavily — price every distinct symbol exactly once.
    universe = sorted({m["symbol"] for members in members_by_index.values() for m in members})
    if not universe:
        return {"indices": [], "error": "constituent lists unavailable"}

    def fetch() -> list[dict]:
        raw = _download(universe, period="5d", interval="1d")
        changes = {s: _day_change(raw, s) for s in universe}

        # A batch this size usually drops a handful of symbols; one retry over
        # just those recovers them and keeps the percentages honest.
        missing = [s for s, change in changes.items() if change is None]
        if missing:
            try:
                retry = _download(missing, period="5d", interval="1d")
                for sym in missing:
                    changes[sym] = _day_change(retry, sym)
            except Exception as e:
                # A failed retry must not discard the symbols we did price.
                log.warning("breadth_retry_failed", count=len(missing), error=str(e))
            else:
                log.info(
                    "breadth_retry",
                    missing=len(missing),
                    recovered=sum(1 for s in missing if changes[s] is not None),
                )

        rows = []
        for key, members in members_by_index.items():
            scored = [
                changes[m["symbol"]]
                for m in members
                if changes.get(m["symbol"]) is not None
            ]
            if not scored:
                rows.append({
                    "index": key,
                    "label": INDEX_SOURCES[key]["label"],
                    "error": "no data",
                })
                continue

            advancing = sum(1 for c in scored if c > 0)
            declining = sum(1 for c in scored if c < 0)
            unchanged = len(scored) - advancing - declining

            rows.append({
                "index": key,
                "label": INDEX_SOURCES[key]["label"],
                "advancing": advancing,
                "declining": declining,
                "unchanged": unchanged,
                "counted": len(scored),
                "constituents": len(members),
                "advancing_percent": _round(advancing / len(scored) * 100.0, 1),
                "declining_percent": _round(declining / len(scored) * 100.0, 1),
                "unchanged_percent": _round(unchanged / len(scored) * 100.0, 1),
                "avg_change_percent": _round(sum(scored) / len(scored), 2),
                "median_change_percent": _round(sorted(scored)[len(scored) // 2], 2),
            })
        return rows

    def well_covered(rows: list[dict]) -> bool:
        """Don't pin a thin scan for the whole TTL — let the next poll retry."""
        counted = sum(row.get("counted", 0) for row in rows)
        expected = sum(row.get("constituents", 0) for row in rows) or 1
        return counted / expected >= 0.95

    try:
        rows = await _cached(
            "breadth", BREADTH_TTL, fetch, should_cache=well_covered, lock=True
        )
    except Exception as e:
        log.error("breadth_fetch_failed", error=str(e))
        return {"indices": [], "error": str(e)}

    return {"indices": rows, "as_of": datetime.now(timezone.utc).isoformat()}


# ── top gainers / losers ──────────────────────────────────────────────────────

MOVERS_TTL = 60.0
SECTOR_TTL = 604800.0          # a company changes sector roughly never
MOVERS_OVERSCAN = 60           # screened rows fetched per side, before filtering

# Cap tiers as (min, max) market cap in USD. "all" keeps the floor so the list
# is companies rather than the sub-$100m tape, where a 200% day means nothing.
CAP_TIERS: dict[str, dict] = {
    "all":   {"label": "All caps",  "min": 300_000_000,    "max": None},
    "large": {"label": "Large cap", "min": 10_000_000_000, "max": None},
    "mid":   {"label": "Mid cap",   "min": 2_000_000_000,  "max": 10_000_000_000},
    "small": {"label": "Small cap", "min": 300_000_000,    "max": 2_000_000_000},
}

# Screener "region: us" still returns foreign OTC lines whose last print is days
# old. Restrict to the exchanges an ordinary brokerage account can actually hit.
US_EXCHANGES = {"NMS", "NYQ", "NGM", "NCM", "ASE", "PCX", "BTS", "NYS"}

MIN_MOVER_PRICE = 1.0
MIN_MOVER_VOLUME = 100_000


def _screen_query(tier: dict):
    """Equity screen for one cap tier: US, liquid, above the penny threshold."""
    from yfinance import EquityQuery as Q

    clauses = [
        Q("eq", ["region", "us"]),
        Q("gte", ["intradaymarketcap", tier["min"]]),
        Q("gte", ["dayvolume", MIN_MOVER_VOLUME]),
        Q("gte", ["intradayprice", MIN_MOVER_PRICE]),
    ]
    if tier["max"] is not None:
        clauses.append(Q("lt", ["intradaymarketcap", tier["max"]]))
    return Q("and", clauses)


def _mover_row(quote: dict) -> dict | None:
    """One screener quote, trimmed to what the panel shows."""
    symbol = (quote.get("symbol") or "").upper()
    change = _safe(quote.get("regularMarketChangePercent"))
    if not symbol or change is None or change == 0:
        return None
    if quote.get("quoteType") != "EQUITY":
        return None
    if quote.get("exchange") not in US_EXCHANGES:
        return None

    return {
        "symbol": symbol,
        "name": quote.get("shortName") or symbol,
        "sector": None,                     # filled in by _attach_sectors
        "last": _round(quote.get("regularMarketPrice"), 2),
        "change": _round(quote.get("regularMarketChange"), 2),
        "change_percent": _round(change, 2),
        "volume": _safe(quote.get("regularMarketVolume")),
        "market_cap": _safe(quote.get("marketCap")),
        "exchange": quote.get("fullExchangeName"),
    }


def _screen_movers(tier: dict, ascending: bool, limit: int) -> list[dict]:
    """Screen one side of the tape. `ascending` gives losers, descending gainers."""
    result = yf.screen(
        _screen_query(tier),
        sortField="percentchange",
        sortAsc=ascending,
        count=MOVERS_OVERSCAN,
    )
    rows = []
    for quote in (result or {}).get("quotes", []):
        row = _mover_row(quote)
        # A gainer screen can spill into flat/negative names once the tradeable
        # universe runs out; keep each list on its own side of zero.
        if row and ((row["change_percent"] < 0) == ascending):
            rows.append(row)
        if len(rows) >= limit:
            break
    return rows


def _index_sectors() -> dict[str, str]:
    """Symbol -> sector across the S&P 1500, which covers most US movers."""
    lookup: dict[str, str] = {}
    for key in ("sp500", "sp400", "sp600"):
        try:
            for member in _fetch_constituents(key):
                sector = normalise_sector(member.get("sector"))
                if sector:
                    lookup.setdefault(member["symbol"], sector)
        except Exception as e:
            log.warning("index_sectors_failed", index=key, error=str(e))
    return lookup


def _sector_via_info(symbol: str) -> str | None:
    try:
        return normalise_sector((yf.Ticker(symbol).info or {}).get("sector"))
    except Exception:
        return None


async def _attach_sectors(rows: list[dict]) -> None:
    """Name each mover's sector in place.

    Two tiers, because the cheap one covers most of the list: the S&P 1500
    membership tables are already cached for the breadth panel, and only the
    symbols they miss cost a per-ticker `info` call. Those are cached for a
    week individually, so an unknown symbol is slow once, not once per poll.
    """
    if not rows:
        return

    index_lookup = await _cached("sectors:index", CONSTITUENTS_TTL, _index_sectors)
    unknown = []
    for row in rows:
        row["sector"] = index_lookup.get(row["symbol"])
        if not row["sector"]:
            unknown.append(row["symbol"])

    if not unknown:
        return

    keys = {sym: f"sector:{sym}" for sym in unknown}
    hits = await get_fresh_many(list(keys.values()), SECTOR_TTL)
    missing = [sym for sym in unknown if keys[sym] not in hits]

    if missing:
        def fetch() -> dict[str, str | None]:
            with ThreadPoolExecutor(max_workers=8) as pool:
                return dict(zip(missing, pool.map(_sector_via_info, missing)))

        try:
            fetched = await asyncio.to_thread(fetch)
        except Exception as e:
            log.warning("sector_info_failed", count=len(missing), error=str(e))
            fetched = {}

        # A symbol Yahoo has no sector for is cached as "" rather than None:
        # the cache reads a None back as a miss, which would re-fetch the same
        # dead lookup on every poll for as long as the ticker keeps moving.
        resolved = {keys[s]: fetched.get(s) or "" for s in missing}
        await put_many(resolved, SECTOR_TTL)
        hits.update(resolved)

    # Anything still blank was in `unknown`, so it has a key either way.
    for row in rows:
        if not row["sector"]:
            row["sector"] = hits.get(keys[row["symbol"]]) or None


async def get_movers(cap: str = "all", limit: int = 10) -> dict:
    """The day's biggest gainers and losers within one market-cap tier."""
    cap = cap.lower()
    tier = CAP_TIERS.get(cap)
    if tier is None:
        return {"cap": cap, "gainers": [], "losers": [], "error": f"unknown cap tier {cap!r}"}

    limit = max(1, min(limit, 25))

    def fetch() -> dict:
        return {
            "gainers": _screen_movers(tier, ascending=False, limit=limit),
            "losers": _screen_movers(tier, ascending=True, limit=limit),
        }

    def complete(result: dict) -> bool:
        return bool(result["gainers"] and result["losers"])

    try:
        sides = await _cached(
            f"movers:{cap}:{limit}", MOVERS_TTL, fetch, should_cache=complete, lock=True
        )
    except Exception as e:
        log.error("movers_fetch_failed", cap=cap, error=str(e))
        return {"cap": cap, "gainers": [], "losers": [], "error": str(e)}

    # Sectors are attached outside the cached fetch: the screen is worth
    # re-running every minute, the sector of a ticker is not.
    gainers = [dict(r) for r in sides["gainers"]]
    losers = [dict(r) for r in sides["losers"]]
    await _attach_sectors(gainers + losers)

    return {
        "cap": cap,
        "cap_label": tier["label"],
        "gainers": gainers,
        "losers": losers,
        "as_of": datetime.now(timezone.utc).isoformat(),
    }


async def get_events(days: int = 7, extra_symbols: list[str] | None = None) -> dict:
    """Earnings and economic releases for the current week."""
    start, end = _week_bounds(days)
    universe = list(dict.fromkeys(EARNINGS_UNIVERSE + [s.upper() for s in (extra_symbols or [])]))
    cache_key = f"events:{start}:{end}:{','.join(universe)}"

    def fetch() -> dict:
        return {
            "week_start": start.isoformat(),
            "week_end": end.isoformat(),
            "earnings": _fetch_earnings(universe, start, end),
            "economic": _fetch_economic(start, end),
        }

    try:
        return await _cached(cache_key, EVENTS_TTL, fetch)
    except Exception as e:
        log.error("events_fetch_failed", error=str(e))
        return {
            "week_start": start.isoformat(),
            "week_end": end.isoformat(),
            "earnings": [],
            "economic": {"events": [], "available": False},
            "error": str(e),
        }


# ── one calendar week of events, navigable ────────────────────────────────────

# The calendar is fetched whole and sliced per week, so this TTL is what the
# first visitor of the day pays, not what each arrow press costs.
EARNINGS_CALENDAR_TTL = 21600.0     # 6 hours
MAX_WEEK_OFFSET = 26


def _iso_week_bounds(offset: int) -> tuple[date, date]:
    """Monday through Sunday of the week `offset` weeks from this one.

    Anchored to New York for the same reason `_week_bounds` is: after 8pm ET
    the UTC date has already rolled over, which on a Sunday night would hand
    back next week.
    """
    today = datetime.now(ZoneInfo("America/New_York")).date()
    monday = today - timedelta(days=today.weekday()) + timedelta(weeks=offset)
    return monday, monday + timedelta(days=6)


def _earnings_history(symbol: str) -> list[dict]:
    """Every earnings date yfinance knows for one symbol, past and scheduled.

    `calendar` would be one cheap call, but it only ever returns the *next*
    report — which cannot answer "who reported on Tuesday" for a week already
    under way, let alone a past one. This endpoint is slower and returns ~3
    years in a single call, which is what makes week navigation free.
    """
    try:
        frame = yf.Ticker(symbol).get_earnings_dates(limit=24)
    except Exception:
        return []
    if frame is None or frame.empty:
        return []

    rows = []
    for stamp, row in frame.iterrows():
        when = stamp.to_pydatetime() if hasattr(stamp, "to_pydatetime") else stamp
        if not isinstance(when, datetime):
            continue
        rows.append({
            "symbol": symbol,
            "date": when.date().isoformat(),
            # Yahoo timestamps the call itself: before the 9:30 open or after
            # the 4pm close is the part traders actually plan around.
            "session": "before_open" if when.hour < 12 else "after_close",
            "eps_estimate": _round(row.get("EPS Estimate"), 3),
            "reported_eps": _round(row.get("Reported EPS"), 3),
            "surprise_percent": _round(row.get("Surprise(%)"), 2),
        })
    return rows


def _fetch_earnings_calendar(universe: list[str]) -> dict[str, list[dict]]:
    with ThreadPoolExecutor(max_workers=12) as pool:
        results = pool.map(_earnings_history, universe)
    return {sym: rows for sym, rows in zip(universe, results) if rows}


async def get_week_events(week_offset: int = 0, extra_symbols: list[str] | None = None) -> dict:
    """Earnings and economic releases for one calendar week.

    `week_offset` is relative to the current week — 0 is this one, -1 last
    week, +1 next. Past weeks carry reported EPS and the surprise against
    estimate; future weeks carry the estimate alone.
    """
    week_offset = max(-MAX_WEEK_OFFSET, min(week_offset, MAX_WEEK_OFFSET))
    start, end = _iso_week_bounds(week_offset)
    today = datetime.now(ZoneInfo("America/New_York")).date()

    universe = list(dict.fromkeys(EARNINGS_UNIVERSE + [s.upper() for s in (extra_symbols or [])]))

    base = {
        "week_offset": week_offset,
        "week_start": start.isoformat(),
        "week_end": end.isoformat(),
        "today": today.isoformat(),
        "is_current_week": start <= today <= end,
    }

    try:
        calendar = await _cached(
            f"earnings-calendar:{','.join(universe)}",
            EARNINGS_CALENDAR_TTL,
            lambda: _fetch_earnings_calendar(universe),
            should_cache=bool,
            lock=True,
        )
    except Exception as e:
        log.error("earnings_calendar_failed", error=str(e))
        calendar = {}

    earnings = [
        row
        for rows in calendar.values()
        for row in rows
        if start.isoformat() <= row["date"] <= end.isoformat()
    ]
    earnings.sort(key=lambda r: (r["date"], r["symbol"]))

    try:
        economic = await _cached(
            f"economic:{start}:{end}", EVENTS_TTL, lambda: _fetch_economic(start, end)
        )
    except Exception as e:
        log.error("economic_fetch_failed", error=str(e))
        economic = {"events": [], "available": False, "error": str(e)}

    return {**base, "earnings": earnings, "economic": economic}
