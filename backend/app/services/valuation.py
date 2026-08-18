"""What the market is assuming about a company, and what a DCF makes of it.

The arithmetic lives in valuation_math.py; this half fetches the inputs and
decides which ones to trust.

**On free cash flow.** yfinance exposes `info["freeCashflow"]`, and it is not
reliable: MSFT reports $16.5bn there against $67bn on its own cash flow
statement, a fourfold understatement that turns into a 0.46% FCF yield and a
valuation to match. So the statement is the source and `info` is the fallback,
not the other way round. The statement also hands over a multi-year history for
free, which is the only honest way to see whether the latest year is
representative — dispersion across those years runs from about 12% for a stable
name to over 200% for one in the middle of a growth ramp, and there is no single
base-year rule that suits both.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any

import structlog
import yfinance as yf

from app.services import market_data
from app.services.cache import cached_async
from app.services.valuation_math import (
    DEFAULT_RISK_FREE,
    DEFAULT_TERMINAL_GROWTH,
    DEFAULT_YEARS,
    GROWTH_CEILING,
    GROWTH_FLOOR,
    METHOD,
    Assumptions,
    clamp,
    cost_of_equity,
    dcf_value,
    implied_growth,
    scenario_band,
    sensitivity_grid,
)

log = structlog.get_logger(__name__)

# Fundamentals move quarterly; the price inside moves faster, but not enough to
# justify re-reading two statements every fifteen minutes.
VALUATION_TTL = 900.0

# Absent a reported growth rate, assume something unremarkable rather than
# nothing — and say in the payload that this is what happened.
FALLBACK_GROWTH = 0.05

_FCF_ROW = "Free Cash Flow"
_OCF_ROW = "Operating Cash Flow"
_CAPEX_ROW = "Capital Expenditure"


def _safe(value: Any) -> float | None:
    try:
        if value is None:
            return None
        out = float(value)
        return None if out != out else out  # NaN
    except (TypeError, ValueError):
        return None


def _fcf_history(ticker: yf.Ticker) -> list[float]:
    """Free cash flow by year from the statement, newest first.

    Falls back to operating cash flow plus capital expenditure — capex arrives
    signed negative, so it adds rather than subtracts.
    """
    try:
        frame = ticker.cashflow
    except Exception as e:
        log.warning("valuation_cashflow_failed", error=str(e))
        return []
    if frame is None or frame.empty:
        return []

    if _FCF_ROW in frame.index:
        return [v for v in (_safe(x) for x in frame.loc[_FCF_ROW].values) if v is not None]

    if _OCF_ROW in frame.index and _CAPEX_ROW in frame.index:
        pairs = zip(frame.loc[_OCF_ROW].values, frame.loc[_CAPEX_ROW].values)
        derived = [
            (_safe(ocf) or 0) + (_safe(capex) or 0)
            for ocf, capex in pairs
            if _safe(ocf) is not None
        ]
        return derived
    return []


def _median(values: list[float]) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    mid = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[mid]
    return (ordered[mid - 1] + ordered[mid]) / 2


def _dispersion(values: list[float]) -> float | None:
    """Spread as a share of the median — how much the base year is a choice."""
    median = _median(values)
    if not median or len(values) < 2:
        return None
    return round((max(values) - min(values)) / abs(median), 3)


async def get_valuation(
    symbol: str,
    *,
    growth: float | None = None,
    terminal_growth: float | None = None,
    discount_rate: float | None = None,
    years: int = DEFAULT_YEARS,
    fcf_base: str = "ttm",
) -> dict:
    """A scenario-banded DCF, plus the growth rate today's price already assumes.

    Every assumption is either derived from the filings or supplied by the
    caller, and the payload says which — a model whose inputs you cannot see is
    a magic number, and the inputs here move the answer more than anything it
    observes.
    """
    symbol = symbol.upper()
    # Overrides are quantised before they reach the key: unbounded floats in a
    # cache key are an unbounded key space.
    key = (
        f"valuation:{symbol}:{round(growth, 3) if growth is not None else '-'}"
        f":{round(terminal_growth, 3) if terminal_growth is not None else '-'}"
        f":{round(discount_rate, 3) if discount_rate is not None else '-'}"
        f":{years}:{fcf_base}"
    )

    async def fetch() -> dict:
        def load() -> dict:
            ticker = yf.Ticker(symbol)
            info = ticker.info or {}
            return {"info": info, "history": _fcf_history(ticker)}

        try:
            loaded = await asyncio.to_thread(load)
        except Exception as e:
            log.error("valuation_fetch_failed", symbol=symbol, error=str(e))
            return {"symbol": symbol, "error": str(e)}

        info: dict = loaded["info"]
        history: list[float] = loaded["history"]

        shares = _safe(info.get("sharesOutstanding"))
        if not shares or shares <= 0:
            return {"symbol": symbol, "error": "share count unavailable — cannot value per share"}

        fcf_ttm = history[0] if history else _safe(info.get("freeCashflow"))
        fcf_median = _median(history)
        base = fcf_median if fcf_base == "median" and fcf_median is not None else fcf_ttm

        if base is None:
            return {"symbol": symbol, "error": "no free cash flow reported"}

        # Live price where we have one; the header already warmed this cache.
        quotes = await market_data.get_quotes([symbol])
        price = _safe(quotes[0].get("last")) if quotes else None
        price = price or _safe(info.get("currentPrice")) or _safe(info.get("regularMarketPrice"))

        equity = cost_of_equity(_safe(info.get("beta")), risk_free=DEFAULT_RISK_FREE)
        derived_growth = _safe(info.get("revenueGrowth"))

        assumptions = Assumptions(
            initial_growth=clamp(
                growth if growth is not None
                else (derived_growth if derived_growth is not None else FALLBACK_GROWTH),
                GROWTH_FLOOR, GROWTH_CEILING,
            ),
            terminal_growth=(
                terminal_growth if terminal_growth is not None else DEFAULT_TERMINAL_GROWTH
            ),
            discount_rate=discount_rate if discount_rate is not None else equity["rate"],
            years=years,
        )

        inputs = {
            "price": price,
            "shares_outstanding": shares,
            "fcf_ttm": fcf_ttm,
            "fcf_median": fcf_median,
            "fcf_base_used": base,
            "fcf_base": fcf_base,
            "fcf_history": history,
            # How much the base year is a choice rather than a fact.
            "fcf_dispersion": _dispersion(history),
            "fcf_source": "statement" if history else "info",
            "net_debt": (
                (_safe(info.get("totalDebt")) or 0) - (_safe(info.get("totalCash")) or 0)
            ),
            "market_cap": _safe(info.get("marketCap")),
            "revenue_growth": derived_growth,
            "earnings_growth": _safe(info.get("earningsGrowth")),
        }

        analysts = {
            "target_mean": _safe(info.get("targetMeanPrice")),
            "target_high": _safe(info.get("targetHighPrice")),
            "target_low": _safe(info.get("targetLowPrice")),
            "count": _safe(info.get("numberOfAnalystOpinions")),
        }

        # Negative cash flow is refused, not valued — a negative fair value is
        # worse than no answer. Pre-profit and heavy-capex names land here.
        if base <= 0:
            return {
                "symbol": symbol,
                "error": (
                    "free cash flow is negative — a discounted cash flow model has "
                    "nothing to discount"
                ),
                "inputs": inputs,
                "analysts": analysts,
                "as_of": datetime.now(timezone.utc).isoformat(),
            }

        return {
            "symbol": symbol,
            "method": METHOD,
            "assumptions": {
                "initial_growth": round(assumptions.initial_growth, 4),
                "initial_growth_source": (
                    "user" if growth is not None
                    else "derived" if derived_growth is not None
                    else "default"
                ),
                "terminal_growth": assumptions.terminal_growth,
                "terminal_growth_source": "user" if terminal_growth is not None else "default",
                "discount_rate": round(assumptions.discount_rate, 4),
                "discount_rate_source": "user" if discount_rate is not None else "derived",
                "years": years,
                "cost_of_equity": equity,
            },
            "inputs": inputs,
            # The headline. A growth rate to argue with beats a price target.
            "implied_growth": (
                implied_growth(price, base, shares, assumptions) if price else None
            ),
            "base_case": dcf_value(base, shares, assumptions),
            "scenarios": scenario_band(base, shares, assumptions),
            "sensitivity": sensitivity_grid(base, shares, assumptions),
            "analysts": analysts,
            "as_of": datetime.now(timezone.utc).isoformat(),
        }

    try:
        return await cached_async(
            key, VALUATION_TTL, fetch, should_cache=lambda r: "error" not in r
        )
    except Exception as e:
        log.error("valuation_failed", symbol=symbol, error=str(e))
        return {"symbol": symbol, "error": str(e)}
