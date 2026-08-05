"""Technical indicator calculations via the `ta` library.

We use `ta` (https://github.com/bukosabino/ta) which supports Python 3.11+
and provides RSI, MACD, Bollinger Bands, and 40+ other indicators.
Our job is wrapping it with a clean interface, type-safe outputs, and
sensible defaults that agents can call without knowing pandas.
"""

from __future__ import annotations

import asyncio
import math
from typing import Optional

import numpy as np
import pandas as pd
import yfinance as yf


def _get_ohlcv(ticker: str, period: str = "1y") -> pd.DataFrame:
    """Fetch OHLCV as a DataFrame (sync helper).

    Note the adjusted default: yfinance's `auto_adjust` is True here, so these
    are split- and dividend-adjusted closes. Indicators want that — a raw
    series would show a phantom gap on every split.
    """
    hist = yf.Ticker(ticker).history(period=period)
    if hist.empty:
        raise ValueError(f"No price data found for {ticker}")

    # The provider sometimes emits a trailing row with no close — an empty stub
    # for a session that has not printed yet. Every indicator here reads
    # .iloc[-1], so one such row makes all of them return null. Drop them.
    hist = hist[hist["Close"].notna()]
    if hist.empty:
        raise ValueError(f"No usable closes for {ticker}")
    return hist


def _closes_for(ticker: str, period: str, closes: Optional[pd.Series]) -> pd.Series:
    """Use a caller-supplied close series, or fetch one.

    Callers that already hold the history — the charting service reads it once
    from the local store — pass it in so three indicators don't trigger three
    downloads of the same prices. Agent tools call with just a ticker and get
    the fetching behaviour they have always had.
    """
    if closes is not None and not closes.empty:
        return closes.dropna()
    return _get_ohlcv(ticker, period)["Close"]


def _safe(val) -> Optional[float]:
    if val is None:
        return None
    try:
        f = float(val)
        return None if (math.isnan(f) or math.isinf(f)) else round(f, 4)
    except (TypeError, ValueError):
        return None


async def compute_rsi(
    ticker: str, period: int = 14, closes: Optional[pd.Series] = None
) -> dict:
    """Compute RSI and interpret the current value."""
    def _calc():
        import ta
        close = _closes_for(ticker, "1y", closes)

        rsi_series = ta.momentum.RSIIndicator(close=close, window=period).rsi()
        if rsi_series is None or rsi_series.empty:
            return {"ticker": ticker, "error": "Could not compute RSI"}

        current = round(float(rsi_series.iloc[-1]), 2)
        zone = "overbought" if current >= 70 else "oversold" if current <= 30 else "neutral"

        return {
            "ticker": ticker.upper(),
            "period": period,
            "current_rsi": current,
            "zone": zone,
            "interpretation": (
                f"RSI({period}) = {current:.1f} — {zone}. "
                + ("Consider potential pullback." if zone == "overbought" else
                   "Consider potential bounce." if zone == "oversold" else
                   "No extreme reading.")
            ),
            "historical": [_safe(v) for v in rsi_series.dropna().tail(20).tolist()],
        }

    return await asyncio.to_thread(_calc)


async def compute_macd(ticker: str, closes: Optional[pd.Series] = None) -> dict:
    """Compute MACD, signal line, and histogram."""
    def _calc():
        import ta
        close = _closes_for(ticker, "1y", closes)

        macd_ind = ta.trend.MACD(close=close)
        macd_val = _safe(macd_ind.macd().iloc[-1])
        signal_val = _safe(macd_ind.macd_signal().iloc[-1])
        hist_val = _safe(macd_ind.macd_diff().iloc[-1])

        prev_hist = _safe(macd_ind.macd_diff().iloc[-2])
        bullish_cross = (prev_hist is not None and hist_val is not None
                         and prev_hist < 0 and hist_val > 0)
        bearish_cross = (prev_hist is not None and hist_val is not None
                         and prev_hist > 0 and hist_val < 0)

        return {
            "ticker": ticker.upper(),
            "macd": macd_val,
            "signal": signal_val,
            "histogram": hist_val,
            "bullish_crossover": bullish_cross,
            "bearish_crossover": bearish_cross,
            "trend": "bullish" if (hist_val or 0) > 0 else "bearish",
            "interpretation": (
                "MACD bullish crossover — momentum turning positive." if bullish_cross else
                "MACD bearish crossover — momentum turning negative." if bearish_cross else
                f"MACD histogram {hist_val} — {'positive momentum' if (hist_val or 0) > 0 else 'negative momentum'}."
            ),
        }

    return await asyncio.to_thread(_calc)


async def compute_bollinger_bands(ticker: str, window: int = 20) -> dict:
    """Compute Bollinger Bands and %B."""
    def _calc():
        import ta
        hist = _get_ohlcv(ticker)
        close = hist["Close"]

        bb = ta.volatility.BollingerBands(close=close, window=window, window_dev=2)
        upper = _safe(bb.bollinger_hband().iloc[-1])
        middle = _safe(bb.bollinger_mavg().iloc[-1])
        lower = _safe(bb.bollinger_lband().iloc[-1])
        pct_b = _safe(bb.bollinger_pband().iloc[-1])
        current_price = _safe(close.iloc[-1])

        zone = "middle"
        if pct_b is not None:
            if pct_b > 1.0:
                zone = "above_upper"
            elif pct_b < 0.0:
                zone = "below_lower"
            elif pct_b > 0.8:
                zone = "near_upper"
            elif pct_b < 0.2:
                zone = "near_lower"

        return {
            "ticker": ticker.upper(),
            "window": window,
            "current_price": current_price,
            "upper_band": upper,
            "middle_band": middle,
            "lower_band": lower,
            "percent_b": pct_b,
            "zone": zone,
            "bandwidth": round((upper - lower) / middle, 4) if all([upper, lower, middle]) else None,
            "interpretation": (
                f"Price at {pct_b:.0%} of Bollinger Band range. "
                + ("Near upper band — potential resistance/overbought." if zone in ("above_upper", "near_upper") else
                   "Near lower band — potential support/oversold." if zone in ("below_lower", "near_lower") else
                   "Price within normal band range.")
                if pct_b is not None else "Bollinger Band data computed."
            ),
        }

    return await asyncio.to_thread(_calc)


async def compute_moving_averages(ticker: str) -> dict:
    """Compute SMA and EMA for 20, 50, 200 day windows."""
    def _calc():
        hist = _get_ohlcv(ticker, period="2y")
        close = hist["Close"]
        current = _safe(close.iloc[-1])

        result: dict = {
            "ticker": ticker.upper(),
            "current_price": current,
            "sma": {},
            "ema": {},
            "signals": [],
        }

        for w in [20, 50, 200]:
            if len(close) >= w:
                sma = _safe(close.rolling(w).mean().iloc[-1])
                ema = _safe(close.ewm(span=w, adjust=False).mean().iloc[-1])
                result["sma"][f"{w}d"] = sma
                result["ema"][f"{w}d"] = ema

                if current and sma:
                    if current > sma:
                        result["signals"].append(f"Price above {w}d SMA ({sma}) — bullish")
                    else:
                        result["signals"].append(f"Price below {w}d SMA ({sma}) — bearish")

        # Golden cross / death cross detection
        sma_50 = close.rolling(50).mean()
        sma_200 = close.rolling(200).mean()
        if len(sma_50) >= 2 and len(sma_200) >= 2:
            if sma_50.iloc[-2] < sma_200.iloc[-2] and sma_50.iloc[-1] > sma_200.iloc[-1]:
                result["signals"].append("Golden cross: 50d SMA crossed above 200d SMA — strong bullish signal")
            elif sma_50.iloc[-2] > sma_200.iloc[-2] and sma_50.iloc[-1] < sma_200.iloc[-1]:
                result["signals"].append("Death cross: 50d SMA crossed below 200d SMA — strong bearish signal")

        return result

    return await asyncio.to_thread(_calc)


async def compute_ma_ladder(
    ticker: str,
    windows: tuple[int, ...] = (5, 20, 50, 250),
    closes: Optional[pd.Series] = None,
) -> dict:
    """Compare several SMAs at once: distance from price, slope, stacking order.

    compute_moving_averages covers the classic 20/50/200 trio; this one takes
    arbitrary windows so a UI can show a short-to-long ladder and say whether
    the averages are stacked bullishly (each faster MA above the slower one).
    """
    def _calc():
        close = _closes_for(ticker, "5y", closes)
        current = _safe(close.iloc[-1])

        levels = []
        for w in windows:
            if len(close) < w:
                levels.append({"window": w, "sma": None, "available": False})
                continue

            series = close.rolling(w).mean()
            sma = _safe(series.iloc[-1])
            # Slope over the last week of trading, as a percent of the level.
            prior = _safe(series.iloc[-6]) if len(series) > 6 else None
            slope = (
                round((sma - prior) / prior * 100, 3)
                if sma is not None and prior not in (None, 0)
                else None
            )

            levels.append({
                "window": w,
                "sma": sma,
                "available": sma is not None,
                "above": bool(current and sma and current > sma),
                "distance_percent": (
                    round((current - sma) / sma * 100, 2)
                    if current and sma else None
                ),
                "slope_percent_5d": slope,
                "direction": (
                    None if slope is None else "rising" if slope > 0 else "falling"
                ),
            })

        # Stacked order — 5 > 20 > 50 > 250 is the textbook uptrend arrangement.
        values = [lvl["sma"] for lvl in levels if lvl["sma"] is not None]
        complete = len(values) == len(windows)
        bullish_stack = complete and all(values[i] > values[i + 1] for i in range(len(values) - 1))
        bearish_stack = complete and all(values[i] < values[i + 1] for i in range(len(values) - 1))

        # Crossovers between neighbouring windows, on the most recent bar.
        crosses = []
        for fast, slow in zip(windows, windows[1:]):
            if len(close) < slow + 2:
                continue
            f = close.rolling(fast).mean()
            s = close.rolling(slow).mean()
            if pd.isna(f.iloc[-2]) or pd.isna(s.iloc[-2]):
                continue
            if f.iloc[-2] <= s.iloc[-2] and f.iloc[-1] > s.iloc[-1]:
                crosses.append({"fast": fast, "slow": slow, "type": "bullish"})
            elif f.iloc[-2] >= s.iloc[-2] and f.iloc[-1] < s.iloc[-1]:
                crosses.append({"fast": fast, "slow": slow, "type": "bearish"})

        above_count = sum(1 for lvl in levels if lvl.get("above"))
        return {
            "ticker": ticker.upper(),
            "current_price": current,
            "levels": levels,
            "crosses": crosses,
            "alignment": (
                "bullish" if bullish_stack else "bearish" if bearish_stack else "mixed"
            ),
            "above_count": above_count,
            "total_count": len([lvl for lvl in levels if lvl["available"]]),
            "interpretation": (
                f"Price is above {above_count} of "
                f"{len([lvl for lvl in levels if lvl['available']])} moving averages; "
                f"the ladder is stacked "
                f"{'bullishly' if bullish_stack else 'bearishly' if bearish_stack else 'inconsistently'}."
            ),
        }

    return await asyncio.to_thread(_calc)


async def compute_support_resistance(ticker: str) -> dict:
    """Identify support and resistance levels using rolling pivot points."""
    def _calc():
        hist = _get_ohlcv(ticker, period="1y")
        close = hist["Close"]
        high = hist["High"]
        low = hist["Low"]
        current = _safe(close.iloc[-1])

        window = 10
        resistance_levels = []
        support_levels = []

        for i in range(window, len(close) - window):
            if high.iloc[i] == high.iloc[i - window:i + window].max():
                resistance_levels.append(_safe(high.iloc[i]))
            if low.iloc[i] == low.iloc[i - window:i + window].min():
                support_levels.append(_safe(low.iloc[i]))

        resistance_levels = sorted(set(r for r in resistance_levels if r))
        support_levels = sorted(set(s for s in support_levels if s), reverse=True)

        nearest_resistance = [r for r in resistance_levels if current and r > current][:3]
        nearest_support = [s for s in support_levels if current and s < current][:3]

        return {
            "ticker": ticker.upper(),
            "current_price": current,
            "nearest_resistance": nearest_resistance,
            "nearest_support": nearest_support,
            "interpretation": (
                f"Key resistance at {nearest_resistance[0] if nearest_resistance else 'N/A'}, "
                f"key support at {nearest_support[0] if nearest_support else 'N/A'}."
            ),
        }

    return await asyncio.to_thread(_calc)
