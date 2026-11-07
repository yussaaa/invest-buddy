"""Options-implied expected move into the next earnings date.

Event traders read one number off the options market before a print: how far
the stock is priced to move. That number is the at-the-money straddle of the
first expiry *after* earnings, divided by spot — the market's own estimate of
the move through that expiry.

Everything here composes services that already exist: spot and the earnings
date and a single expiry's chain, all reused from `options.py`. Nothing new is
fetched that the options feature doesn't already know how to fetch; this module
only picks the one expiry that straddles earnings (which the generic chain,
scoped to 7–45 DTE + LEAPS, usually omits) and reads the two at-the-money legs.

It reports what the market is pricing. It does not suggest a trade. Scalars
only — no series enters the payload, the same discipline as the trend summary
(see services/trend.py).
"""

from __future__ import annotations

import asyncio
from datetime import date, datetime, timezone

import structlog

from app.services import options
from app.services.cache import cached_async

log = structlog.get_logger(__name__)

# Delayed quotes, and the expiry that matters does not change intraday.
EARNINGS_MOVE_TTL = 300.0


def _mid(row: dict) -> float | None:
    """Mid of a raw chain row, falling back to the last print when one-sided."""
    bid = options._float(row.get("bid"))
    ask = options._float(row.get("ask"))
    if bid > 0 and ask > 0 and ask >= bid:
        return (bid + ask) / 2
    last = options._float(row.get("lastPrice"))
    return last if last > 0 else None


def _nearest_strike_row(rows: list[dict], spot: float) -> dict | None:
    """The row whose strike sits closest to spot — the at-the-money contract."""
    usable = [r for r in rows if options._float(r.get("strike")) > 0]
    if not usable:
        return None
    return min(usable, key=lambda r: abs(options._float(r.get("strike")) - spot))


def _unavailable(symbol: str, reason: str) -> dict:
    return {
        "symbol": symbol,
        "available": False,
        "reason": reason,
        "disclaimer": options.DISCLAIMER,
        "as_of": datetime.now(timezone.utc).isoformat(),
    }


async def expected_earnings_move(symbol: str) -> dict:
    """The options-implied ±% move around the next earnings date for one symbol.

    Primary method is the at-the-money straddle of the first expiry on or after
    earnings (`(call_mid + put_mid) / spot`), which embeds the earnings vol
    premium directly. When that book is too thin to price, it falls back to the
    at-the-money implied vol scaled to the earnings horizon (`iv × √(days/365)`),
    flagged with lower confidence. Either way every key is present, so the
    caller only tests `available`, never key absence.
    """
    symbol = symbol.upper()

    async def fetch() -> dict:
        spot, _dividend = await options._spot_and_dividend(symbol)
        if not spot:
            return _unavailable(symbol, "No price for this symbol.")

        earnings_iso = await options._next_earnings(symbol)
        if not earnings_iso:
            return _unavailable(symbol, "No upcoming earnings date.")
        earnings_date = options._parse_date(earnings_iso)
        if earnings_date is None:
            return _unavailable(symbol, "No upcoming earnings date.")

        today = date.today()
        days_to_earnings = (earnings_date - today).days
        if days_to_earnings < 0:
            return _unavailable(symbol, "The next earnings date has already passed.")

        expiries = sorted(
            d for d in (options._parse_date(e) for e in await asyncio.to_thread(
                options._list_expiries, symbol
            )) if d is not None
        )
        after = [e for e in expiries if e >= earnings_date]
        if not after:
            return _unavailable(symbol, "No listed option expiry covers the earnings date.")
        expiry = after[0]
        expiry_dte = (expiry - today).days

        calls, puts = await asyncio.to_thread(options._fetch_chain, symbol, expiry)
        call_row = _nearest_strike_row(calls, spot)
        put_row = _nearest_strike_row(puts, spot)

        straddle: float | None = None
        method: str | None = None
        confidence: str | None = None

        if call_row and put_row:
            call_mid, put_mid = _mid(call_row), _mid(put_row)
            if call_mid is not None and put_mid is not None:
                straddle = call_mid + put_mid
                method = "atm_straddle"
                # Two things make a clean earnings quote: two-sided markets on
                # both legs (a live book, not a stale print), and an expiry that
                # lands soon after the print. A far expiry carries extra,
                # non-earnings time premium that inflates the move, so it only
                # earns "medium" however tight its quotes.
                two_sided = all(
                    options._float(r.get("bid")) > 0 and options._float(r.get("ask")) > 0
                    for r in (call_row, put_row)
                )
                soon_after = (expiry_dte - days_to_earnings) <= 7
                confidence = "high" if (two_sided and soon_after) else "medium"

        if straddle is not None and straddle > 0:
            expected_move_pct = straddle / spot
        else:
            # Fallback: at-the-money implied vol scaled to the earnings horizon.
            ivs = [
                options._float(r.get("impliedVolatility"))
                for r in (call_row, put_row)
                if r is not None
            ]
            ivs = [v for v in ivs if v > 0]
            if not ivs:
                return _unavailable(
                    symbol, "The options book for that expiry is too thin to price a move."
                )
            atm_iv = sum(ivs) / len(ivs)
            expected_move_pct = atm_iv * ((max(days_to_earnings, 1) / 365) ** 0.5)
            method = "iv_sqrt_t"
            confidence = "low"

        return {
            "symbol": symbol,
            "available": True,
            "reason": None,
            "spot": round(spot, 2),
            "earnings_date": earnings_date.isoformat(),
            "days_to_earnings": days_to_earnings,
            "expiry_used": expiry.isoformat(),
            "expiry_dte": expiry_dte,
            "straddle_price": round(straddle, 2) if straddle is not None else None,
            "expected_move_pct": round(expected_move_pct, 4),
            "implied_low": round(spot * (1 - expected_move_pct), 2),
            "implied_high": round(spot * (1 + expected_move_pct), 2),
            "method": method,
            "confidence": confidence,
            "caveat": (
                f"The straddle spans to the {expiry.isoformat()} expiry "
                f"({expiry_dte}d), so it prices the move through that date, not "
                "the earnings jump alone."
                if method == "atm_straddle"
                else "Approximated from at-the-money implied volatility — the "
                "post-earnings book was too thin to price directly."
            ),
            "disclaimer": options.DISCLAIMER,
            "as_of": datetime.now(timezone.utc).isoformat(),
        }

    try:
        return await cached_async(
            f"earnmove:{symbol}",
            EARNINGS_MOVE_TTL,
            fetch,
            should_cache=lambda r: r.get("available") is True,
            lock=True,
        )
    except Exception as e:
        log.error("earnings_move_failed", symbol=symbol, error=str(e))
        return _unavailable(symbol, "Could not price the expected move right now.")
