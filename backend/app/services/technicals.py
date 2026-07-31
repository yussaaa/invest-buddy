"""Technical indicators for the charting page, plus a plain-English read.

The numbers come from the same indicator functions the technical agent uses;
the explanation is a single fast-model call that describes what the readings
say. It deliberately does not tell anyone what to do with them — this app
reports on data, it does not advise.
"""

from __future__ import annotations

import asyncio
import json
from datetime import date, datetime, timedelta, timezone

import pandas as pd
import structlog

from app.config import get_settings
from app.models.factory import get_provider
from app.services import bar_store, market_data
from app.services.cache import cached_async
from app.tools.calculation.technical_indicators import (
    compute_ma_ladder,
    compute_macd,
    compute_rsi,
)

log = structlog.get_logger(__name__)

TECHNICALS_TTL = 120.0
EXPLANATION_TTL = 900.0

MA_WINDOWS = (5, 20, 50, 250)

EXPLAIN_SYSTEM_PROMPT = """You explain technical indicators to someone reading a stock chart.

You will be given RSI, MACD and a moving-average ladder for one ticker.

Write 3 short paragraphs:
1. Momentum — what the RSI reading means at this level, and what MACD's line,
   signal and histogram say about the direction of momentum.
2. Trend — what the moving-average ladder says: where price sits relative to
   each average, whether they are stacked in order, any recent crossover.
3. The overall picture — where these agree and, more importantly, where they
   disagree or are inconclusive.

Rules:
- Describe what the indicators show. Do NOT recommend buying, selling or
  holding, do not predict prices, and do not suggest entry or exit points.
- Say plainly when a reading is unremarkable. Most readings are.
- Name the actual numbers you are describing.
- Note that these are lagging indicators derived only from past price.
- Plain prose, no markdown headings or bullets. Under 220 words total."""

DISCLAIMER = (
    "Technical indicators describe past price action only. This is not "
    "investment advice."
)


# The longest window is MA250, which needs a year of bars plus history for the
# slope; five years keeps every indicator comfortably inside one read.
INDICATOR_LOOKBACK_DAYS = 365 * 5


async def _adjusted_closes(symbol: str) -> pd.Series | None:
    """Split/dividend-adjusted closes from the local store.

    Indicators must run on the adjusted series — a raw one shows a phantom gap
    at every split. Returns None if the store can't answer, in which case the
    indicators fall back to fetching for themselves.
    """
    try:
        rows = await bar_store.daily_bars_for(
            symbol,
            start=date.today() - timedelta(days=INDICATOR_LOOKBACK_DAYS),
            adjusted=True,
            span="5y",
        )
    except Exception as e:
        log.warning("technicals_store_unavailable", symbol=symbol, error=str(e))
        return None

    if not rows:
        return None

    dates = [r.bar_date for r in rows]
    closes = [r.close for r in rows]

    # The store deliberately holds settled sessions only, but an indicator that
    # ignores today is answering a different question than the one on screen —
    # on a day when a stock moves 15%, RSI computed to yesterday's close is off
    # by twenty points. Append the live price so the readings match the chart.
    today = await _todays_close(symbol)
    if today is not None:
        dates.append(date.today())
        closes.append(today)

    return pd.Series(closes, index=pd.to_datetime(dates))


async def _todays_close(symbol: str) -> float | None:
    """Current price from the shared quote cache, if the session has one."""
    try:
        quotes = await market_data.get_quotes([symbol])
    except Exception:
        return None
    if not quotes:
        return None
    last = quotes[0].get("last")
    return float(last) if last is not None else None


async def get_technicals(symbol: str) -> dict:
    """RSI, MACD and the moving-average ladder for one symbol."""
    symbol = symbol.upper()

    async def fetch() -> dict:
        # One read of the adjusted series feeds all three indicators. Without
        # this each one downloads its own copy of the same prices.
        closes = await _adjusted_closes(symbol)

        rsi, macd, ladder = await asyncio.gather(
            compute_rsi(symbol, closes=closes),
            compute_macd(symbol, closes=closes),
            compute_ma_ladder(symbol, MA_WINDOWS, closes=closes),
            return_exceptions=True,
        )

        def unwrap(result, label):
            if isinstance(result, Exception):
                log.error("technicals_failed", symbol=symbol, indicator=label, error=str(result))
                return {"error": str(result)}
            return result

        return {
            "symbol": symbol,
            "rsi": unwrap(rsi, "rsi"),
            "macd": unwrap(macd, "macd"),
            "moving_averages": unwrap(ladder, "ma_ladder"),
            "as_of": datetime.now(timezone.utc).isoformat(),
        }

    def usable(result: dict) -> bool:
        return any("error" not in result[k] for k in ("rsi", "macd", "moving_averages"))

    return await cached_async(
        f"technicals:{symbol}", TECHNICALS_TTL, fetch, should_cache=usable
    )


def _has_llm_credentials(settings) -> bool:
    """Local providers need no key; hosted ones do."""
    provider = settings.model_provider
    if provider in ("ollama", "vllm"):
        return True
    return bool(
        {
            "openai": settings.openai_api_key,
            "anthropic": settings.anthropic_api_key,
            "qwen": settings.dashscope_api_key,
        }.get(provider)
    )


async def explain_technicals(symbol: str) -> dict:
    """Ask the fast model to describe what the indicators are showing."""
    symbol = symbol.upper()
    settings = get_settings()

    if not _has_llm_credentials(settings):
        return {
            "symbol": symbol,
            "available": False,
            "reason": (
                f"No credentials configured for MODEL_PROVIDER={settings.model_provider}. "
                "Set the provider's API key in .env, or use MODEL_PROVIDER=ollama to run locally."
            ),
        }

    technicals = await get_technicals(symbol)

    async def fetch() -> dict:
        payload = json.dumps(
            {
                "ticker": symbol,
                "rsi": technicals.get("rsi"),
                "macd": technicals.get("macd"),
                "moving_averages": technicals.get("moving_averages"),
            },
            default=str,
        )

        response = await get_provider().complete(
            messages=[
                {"role": "system", "content": EXPLAIN_SYSTEM_PROMPT},
                {"role": "user", "content": f"Indicator readings for {symbol}:\n\n{payload}"},
            ],
            model=settings.fast_model,
            temperature=0.2,
            max_tokens=700,
        )

        return {
            "symbol": symbol,
            "available": True,
            "explanation": response.content.strip(),
            "model": response.model,
            "disclaimer": DISCLAIMER,
            "as_of": datetime.now(timezone.utc).isoformat(),
        }

    try:
        return await cached_async(
            f"technicals_explain:{symbol}",
            EXPLANATION_TTL,
            fetch,
            should_cache=lambda r: bool(r.get("explanation")),
        )
    except Exception as e:
        log.error("technicals_explain_failed", symbol=symbol, error=str(e))
        return {"symbol": symbol, "available": False, "reason": str(e)}
