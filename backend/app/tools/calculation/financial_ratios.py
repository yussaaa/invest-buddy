"""Financial ratio computation — wraps yfinance with clean structured output."""

from __future__ import annotations

import asyncio
from typing import Optional

import yfinance as yf


def _safe(val) -> Optional[float]:
    import math
    if val is None:
        return None
    try:
        f = float(val)
        return None if (math.isnan(f) or math.isinf(f)) else round(f, 4)
    except (TypeError, ValueError):
        return None


def _statement_fcf(ticker: str) -> float | None:
    """Latest reported free cash flow, from the cash flow statement itself."""
    try:
        frame = yf.Ticker(ticker).cashflow
    except Exception:
        return None
    if frame is None or frame.empty:
        return None

    if "Free Cash Flow" in frame.index:
        for value in frame.loc["Free Cash Flow"].values:
            got = _safe(value)
            if got is not None:
                return got

    # Capex arrives signed negative, so it adds.
    if "Operating Cash Flow" in frame.index and "Capital Expenditure" in frame.index:
        ocf = _safe(frame.loc["Operating Cash Flow"].values[0])
        capex = _safe(frame.loc["Capital Expenditure"].values[0])
        if ocf is not None:
            return ocf + (capex or 0)
    return None


async def compute_financial_ratios(ticker: str) -> dict:
    """Compute key valuation and financial health ratios from yfinance info."""
    def _calc():
        info = yf.Ticker(ticker).info
        price = _safe(info.get("currentPrice") or info.get("regularMarketPrice"))

        ratios = {
            "ticker": ticker.upper(),
            "price": price,
            # Valuation
            "pe_ratio_ttm": _safe(info.get("trailingPE")),
            "pe_ratio_forward": _safe(info.get("forwardPE")),
            "price_to_book": _safe(info.get("priceToBook")),
            "price_to_sales_ttm": _safe(info.get("priceToSalesTrailing12Months")),
            "ev_to_ebitda": _safe(info.get("enterpriseToEbitda")),
            "ev_to_revenue": _safe(info.get("enterpriseToRevenue")),
            "peg_ratio": _safe(info.get("pegRatio")),
            # Profitability
            "gross_margin": _safe(info.get("grossMargins")),
            "operating_margin": _safe(info.get("operatingMargins")),
            "net_margin": _safe(info.get("profitMargins")),
            "roe": _safe(info.get("returnOnEquity")),
            "roa": _safe(info.get("returnOnAssets")),
            # Liquidity & leverage
            "current_ratio": _safe(info.get("currentRatio")),
            "quick_ratio": _safe(info.get("quickRatio")),
            "debt_to_equity": _safe(info.get("debtToEquity")),
            "total_debt": _safe(info.get("totalDebt")),
            "cash_and_equivalents": _safe(info.get("totalCash")),
            # Growth
            "revenue_growth_yoy": _safe(info.get("revenueGrowth")),
            "earnings_growth_yoy": _safe(info.get("earningsGrowth")),
            # Income
            "dividend_yield": _safe(info.get("dividendYield")),
            "eps_ttm": _safe(info.get("trailingEps")),
            "eps_forward": _safe(info.get("forwardEps")),
            # Market
            "market_cap": _safe(info.get("marketCap")),
            "enterprise_value": _safe(info.get("enterpriseValue")),
            "beta": _safe(info.get("beta")),
        }

        # FCF yield = Free Cash Flow / Market Cap.
        #
        # The cash flow statement is the source, not info["freeCashflow"] —
        # that field is unreliable enough to invert the answer. MSFT reports
        # $16.5bn there against $67bn on its own statement, which turns a ~1.9%
        # yield into 0.46%. `info` remains the fallback for the handful of
        # tickers with no statement rows.
        fcf = _statement_fcf(ticker) or _safe(info.get("freeCashflow"))
        mkt_cap = _safe(info.get("marketCap"))
        if fcf and mkt_cap and mkt_cap > 0:
            ratios["fcf_yield"] = round(fcf / mkt_cap, 4)
        else:
            ratios["fcf_yield"] = None

        # Simple interpretation flags
        flags = []
        if ratios["pe_ratio_ttm"] and ratios["pe_ratio_ttm"] > 30:
            flags.append("High P/E — growth expectations priced in")
        if ratios["debt_to_equity"] and ratios["debt_to_equity"] > 2:
            flags.append("High debt/equity — elevated leverage")
        if ratios["roe"] and ratios["roe"] > 0.15:
            flags.append("Strong ROE > 15%")
        if ratios["net_margin"] and ratios["net_margin"] > 0.20:
            flags.append("High net margin > 20%")

        ratios["interpretation_flags"] = flags
        return ratios

    return await asyncio.to_thread(_calc)
