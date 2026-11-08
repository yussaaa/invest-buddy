"""Portfolio risk for a basket of tickers (a watchlist).

Turns a flat list of symbols into a book-level read: each name's beta to the
market and its own volatility, the pairwise correlation matrix, and the
equal-weight aggregate — portfolio beta, portfolio volatility (which is lower
than the average name's when the names don't move together), and a plain
diversification read from the average correlation.

Watchlists carry no weights, so this is an equal-weight view. All of it is
deterministic arithmetic over daily adjusted closes the store already holds,
aligned by date across the names and SPY. Reuses the beta formula the risk
tools use (cov/var); the correlation matrix and the aggregation are new. Nothing
here advises — it measures how the basket is exposed and how concentrated it is.
"""

from __future__ import annotations

import asyncio
import math
import re
from datetime import date, datetime, timedelta, timezone

import numpy as np
import structlog

from app.services import bar_store
from app.services.cache import cached_async

log = structlog.get_logger(__name__)

PORTFOLIO_RISK_TTL = 300.0
BENCHMARK = "SPY"
# ~1 trading year of daily returns once the names are aligned to common dates.
LOOKBACK_DAYS = 400
TRADING_DAYS = 252
MAX_SYMBOLS = 25
# Enough overlapping daily returns for beta/correlation to mean something.
MIN_POINTS = 60
_TICKER = re.compile(r"^[A-Z0-9.\-^=]{1,12}$")


def _num(value, digits: int) -> float | None:
    """Round to a finite float, or None — so NaN/inf never reach the JSON."""
    try:
        x = float(value)
    except (TypeError, ValueError):
        return None
    return round(x, digits) if math.isfinite(x) else None


def _sanitise(symbols: list[str] | None) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for raw in symbols or []:
        s = str(raw).strip().upper()
        if s and s not in seen and _TICKER.match(s):
            seen.add(s)
            out.append(s)
        if len(out) >= MAX_SYMBOLS:
            break
    return out


def _div_label(avg_corr: float | None) -> str:
    if avg_corr is None:
        return "—"
    if avg_corr >= 0.7:
        return "concentrated"
    if avg_corr >= 0.4:
        return "moderate"
    return "diversified"


async def _closes_by_date(symbol: str, start: date) -> dict[date, float]:
    try:
        rows = await bar_store.daily_bars_for(symbol, start=start, adjusted=True)
    except Exception as e:
        log.warning("portfolio_bars_failed", symbol=symbol, error=str(e))
        return {}
    return {r.bar_date: r.close for r in rows if r.close}


async def portfolio_risk(symbols: list[str] | None) -> dict:
    """Beta, volatility and correlation for a basket, equal-weighted."""
    names = _sanitise(symbols)

    async def fetch() -> dict:
        if len(names) < 2:
            return {"available": False, "reason": "Add at least two tickers to assess portfolio risk."}

        start = date.today() - timedelta(days=LOOKBACK_DAYS)
        want = names + [BENCHMARK]
        series = dict(zip(want, await asyncio.gather(*(_closes_by_date(s, start) for s in want))))

        spy = series[BENCHMARK]
        if not spy:
            return {"available": False, "reason": "No benchmark price history."}

        usable = [s for s in names if series[s]]
        missing = [s for s in names if not series[s]]
        if len(usable) < 2:
            return {"available": False, "reason": "Not enough price history for these tickers."}

        # Align to the dates every usable name and SPY share, so returns line up.
        common = set(spy)
        for s in usable:
            common &= set(series[s])
        dates = sorted(common)
        if len(dates) < MIN_POINTS + 1:
            return {"available": False, "reason": "Not enough overlapping history for these tickers."}

        close_mat = np.array([[series[s][d] for d in dates] for s in usable])  # (n, T)
        spy_closes = np.array([spy[d] for d in dates])  # (T,)
        name_rets = close_mat[:, 1:] / close_mat[:, :-1] - 1.0  # (n, T-1)
        spy_rets = spy_closes[1:] / spy_closes[:-1] - 1.0  # (T-1,)
        n = len(usable)
        T = name_rets.shape[1]

        spy_var = float(spy_rets.var(ddof=1))
        betas: list[float | None] = []
        vols: list[float | None] = []
        for i in range(n):
            r = name_rets[i]
            cov = float(np.cov(r, spy_rets, ddof=1)[0, 1])
            betas.append(cov / spy_var if spy_var > 0 else None)
            vols.append(float(r.std(ddof=1)) * math.sqrt(TRADING_DAYS))

        corr = np.corrcoef(name_rets) if n > 1 else np.array([[1.0]])

        # Equal-weight portfolio: the basket's own return series, then its vol —
        # this is where diversification shows up, below the average name's vol.
        port_rets = name_rets.mean(axis=0)
        port_vol = float(port_rets.std(ddof=1)) * math.sqrt(TRADING_DAYS)
        real_betas = [b for b in betas if b is not None]
        port_beta = sum(real_betas) / len(real_betas) if real_betas else None

        off = corr[np.triu_indices(n, 1)]
        avg_corr = float(off.mean()) if off.size else None
        most = None
        if off.size:
            iu = np.triu_indices(n, 1)
            k = int(np.argmax(off))
            most = {"a": usable[iu[0][k]], "b": usable[iu[1][k]], "value": _num(off[k], 2)}

        return {
            "available": True,
            "reason": None,
            "benchmark": BENCHMARK,
            "window_sessions": int(T),
            "missing": missing,
            "names": [
                {"symbol": usable[i], "beta": _num(betas[i], 2), "vol": _num(vols[i], 4)}
                for i in range(n)
            ],
            "correlation": {
                "symbols": usable,
                "matrix": [[_num(corr[i, j], 2) for j in range(n)] for i in range(n)],
            },
            "portfolio": {
                "names_used": n,
                "beta": _num(port_beta, 2),
                "vol": _num(port_vol, 4),
                "avg_correlation": _num(avg_corr, 2),
                "diversification": _div_label(avg_corr),
                "most_correlated": most,
            },
            "as_of": datetime.now(timezone.utc).isoformat(),
        }

    try:
        return await cached_async(
            f"portfolio:risk:{','.join(sorted(names))}",
            PORTFOLIO_RISK_TTL,
            fetch,
            should_cache=lambda r: r.get("available") is True,
            lock=True,
        )
    except Exception as e:
        log.error("portfolio_risk_failed", error=str(e))
        return {"available": False, "reason": "Could not compute portfolio risk right now."}
