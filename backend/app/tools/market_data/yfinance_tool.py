"""yfinance-based market data tools.

yfinance is free, requires no API key, and covers 90% of what we need:
price history, financial statements, analyst ratings, options chain.
All functions are async via asyncio.to_thread (yfinance is sync).
"""

from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Any

import pandas as pd
import yfinance as yf

import structlog

from app.tools.retry_decorator import retry_network_errors

log = structlog.get_logger(__name__)


def _safe_val(val: Any) -> Any:
    """Convert NaN/Inf to None for JSON serialisation."""
    if isinstance(val, float) and (pd.isna(val) or val == float("inf") or val == float("-inf")):
        return None
    return val


def _df_to_records(df: pd.DataFrame) -> list[dict]:
    """Convert a DataFrame to a list of dicts with JSON-safe values."""
    records = []
    for idx, row in df.iterrows():
        record = {"period": str(idx)}
        for col in df.columns:
            record[str(col)] = _safe_val(row[col])
        records.append(record)
    return records


async def get_company_overview(ticker: str) -> dict:
    """Fetch company profile, sector, description, key stats."""

    @retry_network_errors
    def _fetch():
        t = yf.Ticker(ticker)
        info = t.info
        return {
            "ticker": ticker.upper(),
            "name": info.get("longName") or info.get("shortName"),
            "sector": info.get("sector"),
            "industry": info.get("industry"),
            "description": (info.get("longBusinessSummary") or "")[:500],
            "market_cap": _safe_val(info.get("marketCap")),
            "employees": info.get("fullTimeEmployees"),
            "country": info.get("country"),
            "website": info.get("website"),
            "exchange": info.get("exchange"),
            "currency": info.get("currency"),
            "52w_high": _safe_val(info.get("fiftyTwoWeekHigh")),
            "52w_low": _safe_val(info.get("fiftyTwoWeekLow")),
            "current_price": _safe_val(info.get("currentPrice") or info.get("regularMarketPrice")),
            "avg_volume": _safe_val(info.get("averageVolume")),
        }

    try:
        return await asyncio.to_thread(_fetch)
    except Exception as e:
        log.error("yfinance_error", fn="get_company_overview", ticker=ticker, error=str(e))
        return {"ticker": ticker.upper(), "error": str(e)}


async def get_price_history(
    ticker: str, period: str = "1y", interval: str = "1d"
) -> dict:
    """Fetch OHLCV price history."""

    @retry_network_errors
    def _fetch():
        t = yf.Ticker(ticker)
        hist = t.history(period=period, interval=interval)
        if hist.empty:
            return {"ticker": ticker, "data": [], "period": period, "interval": interval}

        records = []
        for dt, row in hist.iterrows():
            records.append({
                "date": dt.strftime("%Y-%m-%d"),
                "open": _safe_val(row.get("Open")),
                "high": _safe_val(row.get("High")),
                "low": _safe_val(row.get("Low")),
                "close": _safe_val(row.get("Close")),
                "volume": _safe_val(row.get("Volume")),
            })
        return {
            "ticker": ticker.upper(),
            "period": period,
            "interval": interval,
            "data": records,
            "latest_close": records[-1]["close"] if records else None,
            "data_points": len(records),
        }

    try:
        return await asyncio.to_thread(_fetch)
    except Exception as e:
        log.error("yfinance_error", fn="get_price_history", ticker=ticker, error=str(e))
        return {"ticker": ticker.upper(), "error": str(e)}


async def get_income_statement(ticker: str, quarterly: bool = False) -> dict:
    """Fetch income statement (annual or quarterly)."""

    @retry_network_errors
    def _fetch():
        t = yf.Ticker(ticker)
        df = t.quarterly_financials if quarterly else t.financials
        if df is None or df.empty:
            return {"ticker": ticker, "data": [], "period_type": "quarterly" if quarterly else "annual"}
        df = df.T
        return {
            "ticker": ticker.upper(),
            "period_type": "quarterly" if quarterly else "annual",
            "data": _df_to_records(df),
        }

    try:
        return await asyncio.to_thread(_fetch)
    except Exception as e:
        log.error("yfinance_error", fn="get_income_statement", ticker=ticker, error=str(e))
        return {"ticker": ticker.upper(), "error": str(e)}


async def get_balance_sheet(ticker: str, quarterly: bool = False) -> dict:
    """Fetch balance sheet."""

    @retry_network_errors
    def _fetch():
        t = yf.Ticker(ticker)
        df = t.quarterly_balance_sheet if quarterly else t.balance_sheet
        if df is None or df.empty:
            return {"ticker": ticker, "data": []}
        df = df.T
        return {
            "ticker": ticker.upper(),
            "period_type": "quarterly" if quarterly else "annual",
            "data": _df_to_records(df),
        }

    try:
        return await asyncio.to_thread(_fetch)
    except Exception as e:
        log.error("yfinance_error", fn="get_balance_sheet", ticker=ticker, error=str(e))
        return {"ticker": ticker.upper(), "error": str(e)}


async def get_cash_flow(ticker: str, quarterly: bool = False) -> dict:
    """Fetch cash flow statement."""

    @retry_network_errors
    def _fetch():
        t = yf.Ticker(ticker)
        df = t.quarterly_cashflow if quarterly else t.cashflow
        if df is None or df.empty:
            return {"ticker": ticker, "data": []}
        df = df.T
        return {
            "ticker": ticker.upper(),
            "period_type": "quarterly" if quarterly else "annual",
            "data": _df_to_records(df),
        }

    try:
        return await asyncio.to_thread(_fetch)
    except Exception as e:
        log.error("yfinance_error", fn="get_cash_flow", ticker=ticker, error=str(e))
        return {"ticker": ticker.upper(), "error": str(e)}


async def get_analyst_ratings(ticker: str) -> dict:
    """Fetch analyst buy/hold/sell recommendations."""

    @retry_network_errors
    def _fetch():
        t = yf.Ticker(ticker)
        info = t.info

        rec = info.get("recommendationKey", "none")
        mean = _safe_val(info.get("recommendationMean"))
        target = _safe_val(info.get("targetMeanPrice"))
        target_high = _safe_val(info.get("targetHighPrice"))
        target_low = _safe_val(info.get("targetLowPrice"))
        num_analysts = info.get("numberOfAnalystOpinions")

        # Recent upgrades/downgrades — optional, failure is non-fatal
        try:
            recs_df = t.recommendations
            recent = []
            if recs_df is not None and not recs_df.empty:
                for dt, row in recs_df.head(10).iterrows():
                    recent.append({
                        "date": str(dt.date()),
                        "firm": row.get("Firm", ""),
                        "to_grade": row.get("To Grade", ""),
                        "from_grade": row.get("From Grade", ""),
                        "action": row.get("Action", ""),
                    })
        except Exception:
            recent = []

        return {
            "ticker": ticker.upper(),
            "consensus": rec,
            "recommendation_mean": mean,
            "price_target_mean": target,
            "price_target_high": target_high,
            "price_target_low": target_low,
            "num_analysts": num_analysts,
            "recent_changes": recent,
        }

    try:
        return await asyncio.to_thread(_fetch)
    except Exception as e:
        log.error("yfinance_error", fn="get_analyst_ratings", ticker=ticker, error=str(e))
        return {"ticker": ticker.upper(), "error": str(e)}


async def get_options_chain(ticker: str) -> dict:
    """Get put/call ratio and IV skew from the nearest-expiry options chain."""

    @retry_network_errors
    def _fetch():
        t = yf.Ticker(ticker)
        expirations = t.options
        if not expirations:
            return {"ticker": ticker, "error": "No options data available"}

        # Use nearest expiry — let network errors propagate for retry
        exp = expirations[0]
        chain = t.option_chain(exp)

        # Data processing — errors here are non-retryable
        try:
            calls = chain.calls
            puts = chain.puts

            total_call_oi = calls["openInterest"].sum()
            total_put_oi = puts["openInterest"].sum()
            put_call_ratio = (total_put_oi / total_call_oi) if total_call_oi > 0 else None

            avg_call_iv = _safe_val(calls["impliedVolatility"].mean())
            avg_put_iv = _safe_val(puts["impliedVolatility"].mean())

            return {
                "ticker": ticker.upper(),
                "expiry": exp,
                "put_call_ratio": _safe_val(put_call_ratio),
                "total_call_open_interest": int(total_call_oi),
                "total_put_open_interest": int(total_put_oi),
                "avg_call_iv": avg_call_iv,
                "avg_put_iv": avg_put_iv,
                "iv_skew": (
                    round(avg_put_iv - avg_call_iv, 4)
                    if avg_put_iv and avg_call_iv
                    else None
                ),
                "interpretation": (
                    "Elevated put/call ratio (>1.0) signals bearish sentiment"
                    if put_call_ratio and put_call_ratio > 1.0
                    else "Normal/bullish options sentiment"
                ),
            }
        except Exception as e:
            return {"ticker": ticker, "error": str(e)}

    try:
        return await asyncio.to_thread(_fetch)
    except Exception as e:
        log.error("yfinance_error", fn="get_options_chain", ticker=ticker, error=str(e))
        return {"ticker": ticker.upper(), "error": str(e)}


async def get_earnings_calendar(ticker: str) -> dict:
    """Get earnings dates, EPS estimates vs actuals."""

    @retry_network_errors
    def _fetch():
        t = yf.Ticker(ticker)

        result: dict = {
            "ticker": ticker.upper(),
            "next_earnings_date": None,
            "earnings_history": [],
        }

        # Next earnings — optional data, failure is non-fatal
        try:
            cal = t.calendar
            if cal is not None and not cal.empty:
                earnings_date = cal.get("Earnings Date")
                if earnings_date is not None:
                    result["next_earnings_date"] = (
                        str(earnings_date.iloc[0].date())
                        if hasattr(earnings_date.iloc[0], "date")
                        else str(earnings_date.iloc[0])
                    )
        except Exception:
            pass

        # Historical earnings — optional data, failure is non-fatal
        try:
            hist = t.earnings_history
            if hist is not None and not hist.empty:
                for dt, row in hist.head(8).iterrows():
                    result["earnings_history"].append({
                        "date": str(dt.date()) if hasattr(dt, "date") else str(dt),
                        "eps_estimate": _safe_val(row.get("epsEstimate")),
                        "eps_actual": _safe_val(row.get("epsActual")),
                        "surprise_pct": _safe_val(row.get("epsDifference")),
                    })
        except Exception:
            pass

        return result

    try:
        return await asyncio.to_thread(_fetch)
    except Exception as e:
        log.error("yfinance_error", fn="get_earnings_calendar", ticker=ticker, error=str(e))
        return {"ticker": ticker.upper(), "error": str(e)}
