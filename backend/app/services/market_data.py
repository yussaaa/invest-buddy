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
from app.services.cache import cached as _cached, cached_async, get_fresh_many, put_many

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

# Mega-caps the chat agent's forward event scan checks one by one, and the bar
# store backfills first. The Market page's week calendar does not use this: it
# asks Yahoo for every large cap reporting (see `_fetch_earnings_between`).
EARNINGS_UNIVERSE = [
    "AAPL", "MSFT", "NVDA", "GOOGL", "AMZN", "META", "TSLA", "AVGO", "BRK-B",
    "JPM", "V", "MA", "UNH", "XOM", "JNJ", "WMT", "PG", "HD", "COST", "ORCL",
    "AMD", "NFLX", "CRM", "ADBE", "INTC", "QCOM", "CSCO", "PEP", "KO", "MCD",
    "BAC", "GS", "MS", "PFE", "MRK", "LLY", "CVX", "BA", "CAT", "DIS",
]

# FRED releases worth surfacing, by FRED's exact release name — everything else
# is noise for a markets view. Exact, not substring: "consumer price index"
# used to also match "Research Consumer Price Index" and "... Excluding Energy
# Commodities", and "gross domestic product" matched "GDP by County", each
# shown as a second high-importance release. The label is what a trader calls it.
MAJOR_FRED_RELEASES: dict[str, tuple[str, str]] = {
    "employment situation": ("Employment Situation (jobs report)", "high"),
    "consumer price index": ("Consumer Price Index (CPI)", "high"),
    "producer price index": ("Producer Price Index (PPI)", "high"),
    "gross domestic product": ("Gross Domestic Product (GDP)", "high"),
    "personal income and outlays": ("Personal Income & Outlays (PCE)", "high"),
    "advance monthly sales for retail and food services": ("Retail Sales", "high"),
    "unemployment insurance weekly claims report": ("Initial Jobless Claims", "medium"),
    "job openings and labor turnover survey": ("JOLTS Job Openings", "medium"),
    "adp national employment report": ("ADP Employment Report", "medium"),
    "employment cost index": ("Employment Cost Index", "medium"),
    "g.17 industrial production and capacity utilization": ("Industrial Production", "medium"),
    "new residential construction": ("Housing Starts & Permits", "medium"),
    "surveys of consumers": ("UMich Consumer Sentiment", "medium"),
    "u.s. international trade in goods and services": ("Trade Balance", "medium"),
    "g.19 consumer credit": ("Consumer Credit", "medium"),
    "h.4.1 factors affecting reserve balances": ("Fed Balance Sheet (H.4.1)", "medium"),
}

# Who publishes each release, and where — the original, not FRED's copy of it.
# BLS answers scripted clients with 403, so its links cannot be checked from a
# server; they are BLS's standing news-release addresses.
RELEASE_PUBLISHERS: dict[str, tuple[str, str]] = {
    "employment situation": ("BLS", "https://www.bls.gov/news.release/empsit.nr0.htm"),
    "consumer price index": ("BLS", "https://www.bls.gov/news.release/cpi.nr0.htm"),
    "producer price index": ("BLS", "https://www.bls.gov/news.release/ppi.nr0.htm"),
    "gross domestic product": ("BEA", "https://www.bea.gov/data/gdp/gross-domestic-product"),
    "personal income and outlays": ("BEA", "https://www.bea.gov/data/income-saving/personal-income"),
    "advance monthly sales for retail and food services": ("Census", "https://www.census.gov/retail/index.html"),
    "unemployment insurance weekly claims report": ("DOL", "https://oui.doleta.gov/unemploy/claims.asp"),
    "job openings and labor turnover survey": ("BLS", "https://www.bls.gov/news.release/jolts.nr0.htm"),
    "adp national employment report": ("ADP", "https://adpemploymentreport.com/"),
    "employment cost index": ("BLS", "https://www.bls.gov/news.release/eci.nr0.htm"),
    "g.17 industrial production and capacity utilization": (
        "Fed", "https://www.federalreserve.gov/releases/g17/current/default.htm",
    ),
    "new residential construction": ("Census", "https://www.census.gov/construction/nrc/index.html"),
    "surveys of consumers": ("UMich", "https://www.sca.isr.umich.edu/"),
    "u.s. international trade in goods and services": (
        "BEA", "https://www.bea.gov/data/intl-trade-investment/international-trade-goods-and-services",
    ),
    "g.19 consumer credit": ("Fed", "https://www.federalreserve.gov/releases/g19/current/default.htm"),
    "h.4.1 factors affecting reserve balances": (
        "Fed", "https://www.federalreserve.gov/releases/h41/current/default.htm",
    ),
}

# Index membership for the breadth panel. Constituents come from Wikipedia's
# tables (the only free source with GICS sectors attached); Dow 30 is small and
# stable enough to also serve as the offline fallback.
INDEX_SOURCES: dict[str, dict] = {
    "sp500": {
        "label": "S&P 500",
        "symbol": "^GSPC",      # the index itself, for its chart
        "url": "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies",
        "symbol_col": "Symbol",
        "name_col": "Security",
        "sector_col": "GICS Sector",
    },
    "nasdaq100": {
        # Wikipedia dropped its components table, so membership comes from
        # Slickcharts and sectors are filled in from the S&P 500 table.
        "label": "Nasdaq 100",
        "symbol": "^NDX",      # the index itself, for its chart
        "url": "https://www.slickcharts.com/nasdaq100",
        "symbol_col": "Symbol",
        "name_col": "Company",
        "sector_col": None,
    },
    "dow30": {
        "label": "Dow Jones 30",
        "symbol": "^DJI",      # the index itself, for its chart
        "url": "https://en.wikipedia.org/wiki/Dow_Jones_Industrial_Average",
        "symbol_col": "Symbol",
        "name_col": "Company",
        "sector_col": "Sector",
    },
    # Not shown as breadth rows — these exist so the movers panel can name a
    # sector for most mid- and small-cap names without a per-symbol lookup.
    "sp400": {
        "label": "S&P MidCap 400",
        "symbol": "^MID",      # the index itself, for its chart
        "url": "https://en.wikipedia.org/wiki/List_of_S%26P_400_companies",
        "symbol_col": "Symbol",
        "name_col": "Security",
        "sector_col": "GICS Sector",
    },
    "sp600": {
        "label": "S&P SmallCap 600",
        "symbol": "^SP600",      # the index itself, for its chart
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


# ── settled closes from the local bar store ─────────────────────────────────


async def _stored(read: Callable[[Any], Any]) -> Any | None:
    """Run one read against the bar store in its own session; None if it's down.

    The store is an optimisation, never a dependency — same contract as
    bar_store.daily_bars_for. Callers treat None as "price it all live".
    """
    try:
        from app.db.session import AsyncSessionLocal

        async with AsyncSessionLocal() as session:
            return await read(session)
    except Exception as e:
        log.warning("bar_store_read_failed", error=str(e))
        return None


async def _stored_closes(symbols: list[str], days: int) -> dict[str, list[tuple[date, float]]]:
    from app.db.repositories_market import get_daily_closes, market_today

    today = market_today()
    out = await _stored(
        lambda s: get_daily_closes(s, symbols, today - timedelta(days=days), today)
    )
    return out or {}


# ── chart history ─────────────────────────────────────────────────────────────

HISTORY_TTL = 30.0

# Daily-and-coarser ranges are served from the local bar store: settled
# sessions never change, so only today's partial bar has to come from the
# provider. Intraday ranges stay live — the store holds daily bars only.
# range -> (bar-store span to request, months back; None = everything stored)
STORE_RANGES: dict[str, tuple[str, int | None]] = {
    "3M": ("2y", 3),
    "6M": ("2y", 6),
    "YTD": ("2y", None),
    "1Y": ("2y", 12),
    # 10y rather than 5y: the MA lead below reaches ~4 years before the range.
    "5Y": ("10y", 60),
    "MAX": ("max", None),
}

# Closes before the first candle, for the chart's moving averages only. An
# N-period average has no value until bar N, so computed from the visible
# candles alone MA200 on a 1Y chart drew only its last ~50 sessions. 199 is the
# longest toolbar average (200) less one: enough for every line to start at the
# chart's left edge. In calendar days per interval, with margin for holidays —
# 199 sessions is ~288 days, so the 1Y range still fits its 2y store span.
MA_LEAD_BARS = 199
_MA_LEAD_DAYS = {"1d": 300, "1wk": 199 * 7 + 14}


def _range_start(range_key: str, today: date) -> date:
    if range_key == "YTD":
        return date(today.year, 1, 1)
    if range_key == "MAX":
        return date(1900, 1, 1)
    months = STORE_RANGES[range_key][1] or 12
    return (pd.Timestamp(today) - pd.DateOffset(months=months)).date()


def _todays_rows(symbol: str, after: date | None) -> list:
    """Bars newer than the store's last one, from a small live download.

    Usually just today's partial session. If the store is a day or two behind
    (it warms lazily, and refreshes on the first read after a close), the five
    sessions here cover that gap too.
    """
    from app.services.bar_store import _to_rows

    frame = _frame_for(_download([symbol], period="5d", interval="1d"), symbol)
    if frame is None or frame.empty:
        return []
    rows, _ = _to_rows(frame, symbol)
    return [r for r in rows if after is None or r.bar_date > after]


def _period_change(candles: list[dict]) -> tuple[Any, Any]:
    first = candles[0]["close"] if candles else None
    last = candles[-1]["close"] if candles else None
    change = (last - first) if (first and last) else None
    pct = change / first * 100.0 if change is not None and first else None
    return _round(change), _round(pct)


async def _history_from_store(symbol: str, range_key: str, interval: str) -> dict | None:
    """The chart payload built from stored bars, or None to use the live path."""
    from app.db.repositories_market import market_today
    from app.services import bar_store
    from app.services.bar_derive import bucket_bars, to_candles

    span, _ = STORE_RANGES[range_key]
    start = _range_start(range_key, market_today())
    lead_days = 0 if range_key == "MAX" else _MA_LEAD_DAYS.get(interval, 0)
    try:
        settled = await bar_store.daily_bars_for(
            symbol, start - timedelta(days=lead_days), span=span
        )
    except Exception as e:
        log.warning("history_store_failed", symbol=symbol, error=str(e))
        return None
    if not settled:
        return None

    try:
        recent = await asyncio.to_thread(_todays_rows, symbol, settled[-1].bar_date)
    except Exception as e:
        # Settled history without today beats no chart; the quote poll still
        # shows the live price above it.
        log.warning("history_today_failed", symbol=symbol, error=str(e))
        recent = []

    # Split before bucketing, so a week straddling the range start stays the
    # partial first candle it always was rather than absorbing lead days.
    daily = [*settled, *recent]
    visible = [r for r in daily if r.bar_date >= start]
    if not visible:
        return None
    lead = [r for r in daily if r.bar_date < start]

    candles = to_candles(bucket_bars(visible, interval))  # type: ignore[arg-type]
    lead_closes = [c["close"] for c in to_candles(bucket_bars(lead, interval))]  # type: ignore[arg-type]
    change, change_pct = _period_change(candles)
    return {
        "symbol": symbol,
        "range": range_key,
        "interval": interval,
        "intraday": False,
        "candles": candles,
        "ma_lead": lead_closes[-MA_LEAD_BARS:],
        "period_change": change,
        "period_change_percent": change_pct,
    }


async def get_history(symbol: str, range_key: str = "1Y") -> dict:
    """OHLCV candles for the charting view."""
    range_key = range_key.upper()
    if range_key not in RANGE_PRESETS:
        range_key = "1Y"
    period, interval = RANGE_PRESETS[range_key]
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

        change, change_pct = _period_change(candles)
        return {
            "symbol": symbol,
            "range": range_key,
            "interval": interval,
            "intraday": intraday,
            "candles": candles,
            "period_change": change,
            "period_change_percent": change_pct,
        }

    async def produce() -> dict:
        if range_key in STORE_RANGES:
            stored = await _history_from_store(symbol, range_key, interval)
            if stored is not None:
                return stored
        return await asyncio.to_thread(fetch)

    try:
        return await cached_async(f"hist:{symbol}:{range_key}", HISTORY_TTL, produce)
    except Exception as e:
        log.error("history_fetch_failed", symbol=symbol, range=range_key, error=str(e))
        return {"symbol": symbol, "range": range_key, "candles": [], "error": str(e)}


# ── instrument profile (chart header) ─────────────────────────────────────────

PROFILE_TTL = 600.0

# yfinance builds `.info` from two requests: quoteSummary (beta, sector,
# industry, short interest, dayLow, averageVolume) and the v7 quote (price,
# market cap, P/E, 52w range). When quoteSummary fails — Yahoo throttling, a
# bad crumb — it carries on with the v7 half alone and raises nothing. These
# keys exist only in quoteSummary, and every listed instrument, ETFs included,
# has at least one of them, so all absent means that request was lost.
_SUMMARY_ONLY_KEYS = ("dayLow", "averageVolume", "sector", "beta")


def _first(info: dict, *keys: str):
    """The first of `keys` with a usable value — quoteSummary's name first, the
    v7 quote's equivalent after it, so a lost quoteSummary still fills it."""
    for k in keys:
        if _safe(info.get(k)) is not None:
            return info[k]
    return None


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
            "day_low": _round(_first(info, "dayLow", "regularMarketDayLow"), 2),
            "day_high": _round(_first(info, "dayHigh", "regularMarketDayHigh"), 2),
            "week52_low": _round(info.get("fiftyTwoWeekLow"), 2),
            "week52_high": _round(info.get("fiftyTwoWeekHigh"), 2),
            "avg_volume": _safe(
                _first(info, "averageVolume", "averageDailyVolume3Month")
            ),
            # Short interest, reported twice a month. Yahoo gives the float
            # share as a fraction; stored as a percent so prose quoting
            # "22.4%" grounds against it (see agents/base/guardrails.py).
            "short_percent_float": _round(
                info["shortPercentOfFloat"] * 100 if _safe(info.get("shortPercentOfFloat")) is not None else None, 2
            ),
            "short_ratio": _round(info.get("shortRatio"), 2),
            "shares_short": _safe(info.get("sharesShort")),
            "shares_short_prior_month": _safe(info.get("sharesShortPriorMonth")),
            "next_earnings": None,
            # Beta, sector and short interest are blank in a partial profile;
            # the fallbacks above cover only day range and average volume.
            "partial": all(_safe(info.get(k)) is None for k in _SUMMARY_ONLY_KEYS),
        }

    try:
        # A partial profile is served but never stored. Cached, it pinned
        # blank beta and average volume for ten minutes, and as the stale
        # fallback for hours after that; uncached, the next request retries.
        return await _cached(
            f"profile:{symbol}", PROFILE_TTL, fetch,
            should_cache=lambda r: not r.get("partial"),
        )
    except Exception as e:
        log.error("profile_fetch_failed", symbol=symbol, error=str(e))
        return {"symbol": symbol, "name": symbol, "error": str(e)}


# ── market overview ───────────────────────────────────────────────────────────

OVERVIEW_TTL = 30.0
SPARKLINE_POINTS = 30


# A stored month counts as usable once it holds this many sessions; a symbol
# below it (never backfilled, or newly added) is priced with the full live
# month as before.
OVERVIEW_MIN_STORED = 15


def _closes_from(raw: pd.DataFrame, symbol: str) -> pd.Series:
    frame = _frame_for(raw, symbol)
    if frame is None or "Close" not in frame:
        return pd.Series(dtype=float)
    closes = frame["Close"].dropna()
    closes.index = [ts.date() for ts in closes.index]
    return closes


def _splice(stored: list[tuple[date, float]], live: pd.Series) -> pd.Series:
    """Stored settled closes, then whatever the live frame has that is newer."""
    if not stored:
        return live
    last = stored[-1][0]
    newer = live[[d > last for d in live.index]] if not live.empty else live
    base = pd.Series([c for _, c in stored], index=[d for d, _ in stored], dtype=float)
    return pd.concat([base, newer]) if not newer.empty else base


def _group_snapshot(closes_by_symbol: dict[str, pd.Series], group: list[dict]) -> list[dict]:
    rows = []
    for entry in group:
        sym = entry["symbol"]
        closes = closes_by_symbol.get(sym, pd.Series(dtype=float))
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

    def fetch(stored: dict[str, list[tuple[date, float]]]) -> dict:
        # Symbols with a settled month in the store only need today from the
        # provider; the rest are priced over the whole month, as they always
        # were. With the store down or cold this is exactly the old request.
        warm = [s for s in symbols if len(stored.get(s, [])) >= OVERVIEW_MIN_STORED]
        cold = [s for s in symbols if s not in warm]
        closes: dict[str, pd.Series] = {}
        if warm:
            raw = _download(warm, period="5d", interval="1d")
            for sym in warm:
                closes[sym] = _splice(stored[sym], _closes_from(raw, sym))
        if cold:
            raw = _download(cold, period="1mo", interval="1d")
            for sym in cold:
                closes[sym] = _closes_from(raw, sym)

        indices = _group_snapshot(closes, INDICES)
        sectors = _group_snapshot(closes, SECTORS)
        macro = _group_snapshot(closes, MACRO)

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

    async def produce() -> dict:
        from app.db.repositories_market import market_today

        # One calendar month, matching the provider's "1mo" period, so the
        # period change and sparkline mean what they did before.
        month = (pd.Timestamp(market_today()) - pd.DateOffset(months=1)).date()
        stored = {
            sym: [(d, c) for d, c in rows if d >= month]
            for sym, rows in (await _stored_closes(symbols, days=35)).items()
        }
        return await asyncio.to_thread(fetch, stored)

    try:
        return await cached_async(
            "overview", OVERVIEW_TTL, produce, should_cache=complete, lock=True
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

    events = _major_releases(payload.get("release_dates", []))
    today = datetime.now(ZoneInfo("America/New_York")).date()
    try:
        from app.services.economic_values import attach_values

        # Consensus comes from a feed that only knows the current week.
        attach_values(events, api_key, today, with_consensus=start <= today <= end)
    except Exception as e:
        # The dates are still worth showing without the numbers.
        log.error("economic_values_failed", error=str(e))

    return {
        "events": events,
        "source": "fred",
        "available": True,
    }


def _major_releases(release_dates: list[dict]) -> list[dict]:
    """FRED's release dates narrowed to `MAJOR_FRED_RELEASES`: high first within
    a day, one line per release per day."""
    events, seen = [], set()
    for item in release_dates:
        release = (item.get("release_name") or "").strip()
        match = MAJOR_FRED_RELEASES.get(release.lower())
        if not match or (release, item.get("date")) in seen:
            continue
        seen.add((release, item.get("date")))
        label, importance = match
        publisher, url = RELEASE_PUBLISHERS.get(release.lower(), (None, None))
        events.append({
            "name": label,
            "release": release,
            "date": item.get("date"),
            "importance": importance,
            "source": "FRED",
            "publisher": publisher,
            "url": url,
        })
    return sorted(events, key=lambda e: (e["date"], e["importance"] != "high", e["name"]))


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

# The scan prices ~700 constituents and takes minutes. `cached` used to stamp
# values with the time their fetch *started*, so a 60s TTL produced values that
# were already expired when written and every request paid a full cold scan.
# Values are stamped at completion now, but the TTL is still sized well above
# the fetch: with a shorter one, most of each window is spent re-scanning.
BREADTH_TTL = 900.0

BREADTH_INDICES = ["sp500", "nasdaq100", "dow30"]


def _is_previous_session(stored: date, live_ts: Any) -> bool:
    live = live_ts.date() if hasattr(live_ts, "date") else live_ts
    return stored < live and (live - stored).days <= PREV_CLOSE_MAX_GAP_DAYS


# A stored close further back than this from the live row is not "the previous
# session" — it means the store is behind, and the change would span days.
PREV_CLOSE_MAX_GAP_DAYS = 4


def _day_change(
    raw: pd.DataFrame, symbol: str, stored_prev: tuple[date, float] | None = None
) -> float | None:
    """Percent change vs the previous session's close.

    A batch this size regularly comes back with a symbol holding only today's
    row. The previous close is a settled fact the bar store already has, so it
    fills that gap instead of dropping the symbol from the count. Today's price
    still has to come from the provider — the store never holds today.
    """
    frame = _frame_for(raw, symbol)
    if frame is None or "Close" not in frame:
        return None
    closes = frame["Close"].dropna()
    if closes.empty:
        return None
    if len(closes) >= 2:
        prev = float(closes.iloc[-2])
    elif stored_prev is not None and _is_previous_session(stored_prev[0], closes.index[-1]):
        prev = float(stored_prev[1])
    else:
        return None
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

    def fetch(prev: dict[str, tuple[date, float]]) -> list[dict]:
        raw = _download(universe, period="5d", interval="1d")
        changes = {s: _day_change(raw, s, prev.get(s)) for s in universe}

        # A batch this size usually drops a handful of symbols; one retry over
        # just those recovers them and keeps the percentages honest.
        missing = [s for s, change in changes.items() if change is None]
        if missing:
            try:
                retry = _download(missing, period="5d", interval="1d")
                for sym in missing:
                    changes[sym] = _day_change(retry, sym, prev.get(sym))
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

    async def produce() -> list[dict]:
        stored = await _stored_closes(universe, days=7)
        prev = {sym: rows[-1] for sym, rows in stored.items() if rows}
        return await asyncio.to_thread(fetch, prev)

    try:
        rows = await cached_async(
            "breadth", BREADTH_TTL, produce, should_cache=well_covered, lock=True
        )
    except Exception as e:
        log.error("breadth_fetch_failed", error=str(e))
        return {"indices": [], "error": str(e)}

    # Added on the way out rather than inside the cached rows, so a field added
    # here reaches clients without waiting out (or forcing) a multi-minute scan.
    indices = [{**row, "symbol": INDEX_SOURCES.get(row["index"], {}).get("symbol")} for row in rows]
    return {"indices": indices, "as_of": datetime.now(timezone.utc).isoformat()}


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

# One query per week against Yahoo's market-wide earnings calendar. The panel
# used to scan a fixed list of 40 mega-caps one ticker at a time, which cost ~36s
# cold and, once those 40 had reported, left a day of peak season showing two
# names while a hundred large caps reported around them.
# The cap tier is the movers screen's (`CAP_TIERS`), so "large cap" means the
# same thing in both panels. Large is the default: the reports that move things.
EARNINGS_DEFAULT_CAP = "large"
EARNINGS_PAGE = 100                 # Yahoo's cap on one page
EARNINGS_MAX_PAGES = 40             # an all-caps season is ~3,100 rows
EARNINGS_WEEK_TTL = 3600.0          # a week still in play fills in reported EPS
EARNINGS_PAST_WEEK_TTL = 86400.0    # a finished week only changes by revision
MAX_WEEK_OFFSET = 26

# Yahoo's timing codes. "TAS" (time as specified) and "TNS" (not supplied)
# fall through to the timestamp, or to no session at all.
_TIMING_SESSION = {"BMO": "before_open", "AMC": "after_close"}


def _iso_week_bounds(offset: int) -> tuple[date, date]:
    """Monday through Sunday of the week `offset` weeks from this one.

    Anchored to New York for the same reason `_week_bounds` is: after 8pm ET
    the UTC date has already rolled over, which on a Sunday night would hand
    back next week.
    """
    today = datetime.now(ZoneInfo("America/New_York")).date()
    monday = today - timedelta(days=today.weekday()) + timedelta(weeks=offset)
    return monday, monday + timedelta(days=6)


def _earnings_query(
    start: date,
    end: date,
    symbols: list[str] | None = None,
    cap: str = EARNINGS_DEFAULT_CAP,
):
    """The calendar filter: US earnings events in a window, by cap tier or by name.

    The window is padded a day each side because Yahoo filters on the UTC
    timestamp and the caller slices on the New York date; a report at 8pm ET
    on Friday is Saturday in UTC.
    """
    from yfinance.calendars import CalendarQuery as Q

    operands = [
        Q("eq", ["region", "us"]),
        Q("or", [Q("eq", ["eventtype", "EAD"]), Q("eq", ["eventtype", "ERA"])]),
        Q("gte", ["startdatetime", (start - timedelta(days=1)).isoformat()]),
        Q("lte", ["startdatetime", (end + timedelta(days=1)).isoformat()]),
    ]
    if symbols:
        # Named tickers come back whatever their size or listing: someone asked.
        operands.append(Q("or", [Q("eq", ["ticker", s]) for s in symbols]))
    else:
        tier = CAP_TIERS[cap]
        operands.append(Q("gte", ["intradaymarketcap", tier["min"]]))
        if tier["max"] is not None:
            operands.append(Q("lt", ["intradaymarketcap", tier["max"]]))
        # Primary US listings only (the movers screen's list). Without this the
        # feed carries foreign giants again as OTC lines: MUFG beside MBFJF,
        # Siemens as both SIEGY and SMAWF.
        operands.append(Q("or", [Q("eq", ["exchange", x]) for x in sorted(US_EXCHANGES)]))
    return Q("and", operands)


def _earnings_pages(query) -> list[pd.DataFrame]:
    """Every page of one calendar query, largest companies first.

    `Calendars.get_earnings_calendar` is the public door, but it can filter
    neither by exchange nor by ticker, so this goes one level down to the
    request it wraps. yfinance keeps the cookie and crumb, which is the part
    worth not re-implementing.
    """
    frames = []
    for page in range(EARNINGS_MAX_PAGES):
        frame = yf.Calendars()._get_data(
            "sp_earnings", query, limit=EARNINGS_PAGE, offset=page * EARNINGS_PAGE
        )
        if frame is None or frame.empty:
            break
        frames.append(frame)
        if len(frame) < EARNINGS_PAGE:
            break
    return frames


def _session_for(timing: Any, when: datetime) -> str | None:
    """Before the open or after the close — the part traders plan around."""
    if isinstance(timing, str) and timing in _TIMING_SESSION:
        return _TIMING_SESSION[timing]
    if timing == "TNS":
        return None
    # Time as specified: the timestamp is the call itself.
    return "before_open" if when.hour < 12 else "after_close"


def _earnings_rows(frame: pd.DataFrame, start: date, end: date) -> list[dict]:
    """Calendar rows inside the New York week, one per symbol."""
    tz = ZoneInfo("America/New_York")
    rows: dict[str, dict] = {}
    for symbol, row in frame.iterrows():
        stamp = row.get("Event Start Date")
        if stamp is None or pd.isna(stamp):
            continue
        when = pd.Timestamp(stamp)
        when = (when.tz_localize("UTC") if when.tzinfo is None else when).tz_convert(tz).to_pydatetime()
        if not start <= when.date() <= end:
            continue
        symbol = str(symbol)
        if symbol in rows:      # a confirmed date and an estimate for one report
            continue
        rows[symbol] = {
            "symbol": symbol,
            "name": _safe(row.get("Company")),
            "market_cap": _round(row.get("Marketcap"), 0),
            "date": when.date().isoformat(),
            "session": _session_for(row.get("Timing"), when),
            "eps_estimate": _round(row.get("EPS Estimate"), 3),
            "reported_eps": _round(row.get("Reported EPS"), 3),
            "surprise_percent": _round(row.get("Surprise(%)"), 2),
        }
    return list(rows.values())


def _fetch_earnings_between(
    start: date, end: date, extra_symbols: list[str], cap: str = EARNINGS_DEFAULT_CAP
) -> list[dict]:
    frames = _earnings_pages(_earnings_query(start, end, cap=cap))
    if extra_symbols:
        frames += _earnings_pages(_earnings_query(start, end, extra_symbols))
    frames = [f for f in frames if not f.empty]
    if not frames:
        return []
    rows = _one_line_per_company(_earnings_rows(pd.concat(frames), start, end))
    # Within a day, biggest first: the frontend shows the head of each column.
    rows.sort(key=lambda r: (r["date"], -(r["market_cap"] or 0), r["symbol"]))
    return rows


def _one_line_per_company(rows: list[dict]) -> list[dict]:
    """Collapse share classes: one Berkshire report, not BRK-A beside BRK-B.

    Where a company lists twice, the ticker people trade wins — the one in
    `EARNINGS_UNIVERSE` if either is, otherwise the shorter symbol.
    """
    familiar = set(EARNINGS_UNIVERSE)
    best: dict[tuple, dict] = {}
    for row in rows:
        key = (row["name"] or row["symbol"], row["date"])
        held = best.get(key)
        rank = lambda r: (r["symbol"] not in familiar, len(r["symbol"]), r["symbol"])  # noqa: E731
        if held is None or rank(row) < rank(held):
            best[key] = row
    return list(best.values())


def _season_bounds(day: date) -> tuple[date, date, str]:
    """The reporting season `day` falls in: its calendar quarter, and the label
    of the quarter whose results land in it.

    October to December is "Q3 2026 season" — companies report the quarter
    that just closed. Off-cycle fiscal years (Cisco's Q1 in November) still
    report once inside the window, which is what makes it a fair denominator.
    """
    first_month = 3 * ((day.month - 1) // 3) + 1
    start = date(day.year, first_month, 1)
    following = date(day.year + 1, 1, 1) if first_month == 10 else date(day.year, first_month + 3, 1)
    results_q = (first_month - 1) // 3 or 4
    results_year = day.year if first_month > 1 else day.year - 1
    return start, following - timedelta(days=1), f"Q{results_q} {results_year}"


def _season_summary(
    start: date, end: date, label: str, today: date, cap: str = EARNINGS_DEFAULT_CAP
) -> dict:
    """How many of the season's reporters in one cap tier are in.

    Reported means the EPS is in, or the date has passed: Yahoo never fills in
    a handful each season (~8 of ~860), and a finished season should read as
    finished.
    """
    rows = _fetch_earnings_between(start, end, [], cap)
    reported = sum(
        1 for r in rows if r["reported_eps"] is not None or r["date"] < today.isoformat()
    )
    return {
        "label": label,
        "start": start.isoformat(),
        "end": end.isoformat(),
        "total": len(rows),
        "reported": reported,
    }


def _earnings_week_key(start: date, extra_symbols: list[str], cap: str = EARNINGS_DEFAULT_CAP) -> str:
    """Shared by the endpoint and the warm-up job, so the two cannot drift."""
    return f"earnings-week:{cap}:{start.isoformat()}:{','.join(extra_symbols)}"


def _earnings_week_ttl(end: date, today: date) -> float:
    return EARNINGS_PAST_WEEK_TTL if end < today else EARNINGS_WEEK_TTL


async def refresh_earnings_calendar(ahead: float = 0.85) -> bool:
    """Re-fetch this week's calendar once it is `ahead` of the way to expiry.

    The fetch is now a second or less, so this is no longer what stands between
    a visitor and a 36s wait — but the current week at the default tier is what
    every visitor opens on, and keeping it warm means Yahoo being slow is never
    their problem. Returns True if it fetched.
    """
    from app.services import cache

    start, end = _iso_week_bounds(0)
    today = datetime.now(ZoneInfo("America/New_York")).date()
    key = _earnings_week_key(start, [])
    ttl = _earnings_week_ttl(end, today)
    current_age = await cache.age(key)
    if current_age is not None and current_age < ttl * ahead:
        return False

    rows = await asyncio.to_thread(_fetch_earnings_between, start, end, [])
    if rows:
        await cache.prime(key, rows, ttl)
    return bool(rows)


async def get_week_events(
    week_offset: int = 0,
    extra_symbols: list[str] | None = None,
    cap: str = EARNINGS_DEFAULT_CAP,
) -> dict:
    """Earnings and economic releases for one calendar week.

    `week_offset` is relative to the current week — 0 is this one, -1 last
    week, +1 next. Past weeks carry reported EPS and the surprise against
    estimate; future weeks carry the estimate alone. `cap` is a `CAP_TIERS`
    key and filters the earnings and the season progress alike.
    """
    if cap not in CAP_TIERS:
        cap = EARNINGS_DEFAULT_CAP
    week_offset = max(-MAX_WEEK_OFFSET, min(week_offset, MAX_WEEK_OFFSET))
    start, end = _iso_week_bounds(week_offset)
    today = datetime.now(ZoneInfo("America/New_York")).date()

    extra = sorted({s.upper() for s in (extra_symbols or [])})

    base = {
        "week_offset": week_offset,
        "week_start": start.isoformat(),
        "week_end": end.isoformat(),
        "today": today.isoformat(),
        "is_current_week": start <= today <= end,
        "cap": cap,
    }

    season_start, season_end, season_label = _season_bounds(start)

    async def week() -> list[dict]:
        try:
            return await _cached(
                _earnings_week_key(start, extra, cap),
                _earnings_week_ttl(end, today),
                lambda: _fetch_earnings_between(start, end, extra, cap),
                should_cache=bool,
                lock=True,
            )
        except Exception as e:
            log.error("earnings_calendar_failed", error=str(e))
            return []

    async def season() -> dict | None:
        try:
            return await _cached(
                f"earnings-season:{cap}:{season_start.isoformat()}",
                _earnings_week_ttl(season_end, today),
                lambda: _season_summary(season_start, season_end, season_label, today, cap),
                should_cache=lambda v: bool(v and v["total"]),
                lock=True,
            )
        except Exception as e:
            log.error("earnings_season_failed", error=str(e))
            return None

    async def releases() -> dict:
        try:
            return await _cached(
                f"economic:{start}:{end}",
                EVENTS_TTL,
                lambda: _fetch_economic(start, end),
                # A missing key or a FRED outage is not an answer to keep for 30
                # minutes: adding the key would otherwise not show until expiry.
                should_cache=lambda v: bool(v.get("available")),
            )
        except Exception as e:
            log.error("economic_fetch_failed", error=str(e))
            return {"events": [], "available": False, "error": str(e)}

    # Three independent sources; the slowest sets the wait, not the sum.
    earnings, season_progress, economic = await asyncio.gather(week(), season(), releases())

    return {**base, "earnings": earnings, "season": season_progress, "economic": economic}
