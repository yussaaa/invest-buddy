"""Risk metric calculations — volatility, VaR, beta, Sharpe, drawdown."""

from __future__ import annotations

import asyncio
import math
from typing import Optional

import numpy as np
import pandas as pd
import yfinance as yf


def _get_returns(ticker: str, period: str = "2y") -> pd.Series:
    hist = yf.Ticker(ticker).history(period=period)
    return hist["Close"].pct_change().dropna()


async def compute_historical_volatility(ticker: str, window: int = 30) -> dict:
    def _calc():
        returns = _get_returns(ticker)
        rolling_vol = returns.rolling(window).std() * math.sqrt(252)
        current_vol = round(float(rolling_vol.iloc[-1]), 4)
        avg_vol = round(float(rolling_vol.mean()), 4)

        level = "low" if current_vol < 0.20 else "high" if current_vol > 0.40 else "medium"
        return {
            "ticker": ticker.upper(),
            "window_days": window,
            "annualised_volatility": current_vol,
            "avg_volatility_2y": avg_vol,
            "level": level,
            "interpretation": f"Annualised volatility: {current_vol:.1%} ({level}). "
                              f"{'Higher than average — elevated risk.' if current_vol > avg_vol else 'Lower than average — calmer period.'}",
        }
    return await asyncio.to_thread(_calc)


async def compute_var(ticker: str, confidence: float = 0.95) -> dict:
    def _calc():
        returns = _get_returns(ticker)
        n = len(returns)

        # Parametric VaR (assumes normal distribution)
        from scipy import stats
        mu = float(returns.mean())
        sigma = float(returns.std())
        z = stats.norm.ppf(1 - confidence)
        parametric_var = -(mu + z * sigma)

        # Historical VaR
        historical_var = -float(np.percentile(returns, (1 - confidence) * 100))

        # Monte Carlo VaR (10,000 simulations)
        sim_returns = np.random.normal(mu, sigma, 10000)
        mc_var = -float(np.percentile(sim_returns, (1 - confidence) * 100))

        return {
            "ticker": ticker.upper(),
            "confidence_level": confidence,
            "holding_period": "1 day",
            "parametric_var": round(parametric_var, 4),
            "historical_var": round(historical_var, 4),
            "monte_carlo_var": round(mc_var, 4),
            "interpretation": (
                f"At {confidence:.0%} confidence, 1-day VaR is approximately "
                f"{historical_var:.2%} of position value (historical method)."
            ),
        }
    return await asyncio.to_thread(_calc)


async def compute_beta(ticker: str, benchmark: str = "SPY") -> dict:
    def _calc():
        stock_ret = _get_returns(ticker)
        bench_ret = _get_returns(benchmark)

        # Align on common dates
        df = pd.DataFrame({"stock": stock_ret, "bench": bench_ret}).dropna()
        if len(df) < 20:
            return {"ticker": ticker, "error": "Insufficient data for beta calculation"}

        cov = df["stock"].cov(df["bench"])
        var = df["bench"].var()
        beta = round(cov / var, 4) if var > 0 else None

        level = "low" if (beta or 0) < 0.8 else "high" if (beta or 0) > 1.2 else "market"
        return {
            "ticker": ticker.upper(),
            "benchmark": benchmark,
            "beta": beta,
            "level": level,
            "data_points": len(df),
            "interpretation": (
                f"Beta = {beta:.2f}. "
                + ("Moves less than the market — defensive." if level == "low" else
                   "Moves more than the market — amplified risk/reward." if level == "high" else
                   "Moves roughly in line with the market.")
            ),
        }
    return await asyncio.to_thread(_calc)


async def compute_sharpe_sortino(ticker: str) -> dict:
    def _calc():
        returns = _get_returns(ticker)
        risk_free_daily = 0.05 / 252  # 5% annualised risk-free rate

        excess = returns - risk_free_daily
        mean_excess = float(excess.mean())
        std_all = float(returns.std())
        downside_std = float(returns[returns < 0].std())

        sharpe = round((mean_excess / std_all) * math.sqrt(252), 3) if std_all > 0 else None
        sortino = round((mean_excess / downside_std) * math.sqrt(252), 3) if downside_std > 0 else None

        return {
            "ticker": ticker.upper(),
            "period": "2y",
            "risk_free_rate_assumed": "5% annualised",
            "sharpe_ratio": sharpe,
            "sortino_ratio": sortino,
            "sharpe_interpretation": (
                "Excellent risk-adjusted return (>2)" if (sharpe or 0) > 2 else
                "Good risk-adjusted return (1-2)" if (sharpe or 0) > 1 else
                "Acceptable (0-1)" if (sharpe or 0) > 0 else
                "Negative — underperforming risk-free rate"
            ),
        }
    return await asyncio.to_thread(_calc)


async def compute_max_drawdown(ticker: str) -> dict:
    def _calc():
        hist = yf.Ticker(ticker).history(period="5y")
        close = hist["Close"]

        rolling_max = close.cummax()
        drawdown = (close - rolling_max) / rolling_max
        max_dd = round(float(drawdown.min()), 4)

        # Find the drawdown period
        trough_idx = drawdown.idxmin()
        peak_idx = close[:trough_idx].idxmax()

        # Recovery date (first date after trough where price exceeds peak)
        recovery_idx = None
        peak_price = float(close[peak_idx])
        post_trough = close[trough_idx:]
        recovered = post_trough[post_trough >= peak_price]
        if not recovered.empty:
            recovery_idx = recovered.index[0]

        return {
            "ticker": ticker.upper(),
            "period": "5y",
            "max_drawdown": max_dd,
            "max_drawdown_pct": f"{max_dd:.1%}",
            "peak_date": str(peak_idx.date()),
            "trough_date": str(trough_idx.date()),
            "recovery_date": str(recovery_idx.date()) if recovery_idx else "Not yet recovered",
            "interpretation": (
                f"Worst historical drawdown was {max_dd:.1%} "
                f"(peak {peak_idx.date()} → trough {trough_idx.date()})."
            ),
        }
    return await asyncio.to_thread(_calc)
