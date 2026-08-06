"""Filtering and ranking over an option chain.

Pure, like options_math: stdlib plus that module, nothing else. Every rule
about what makes a contract tradeable — liquidity floors, moneyness bands,
what to do with a zero bid — lives here so each one is a unit test rather than
a condition buried in a network call.

The screens are deliberately conservative. Real chain data is full of
contracts that look attractive and cannot be traded: a $4.00 bid against a
$9.00 ask, six contracts of open interest, a last price from Tuesday. Ranking
those alongside genuine candidates is worse than returning nothing, because
the numbers all render at the same precision.
"""

from __future__ import annotations

import math
import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date
from typing import Literal

from app.services.options_math import (
    OptionKind,
    covered_call_metrics,
    csp_metrics,
    greeks,
    implied_vol,
    leaps_metrics,
    spread_metrics,
)

Strategy = Literal["csp", "covered_call", "leaps_call", "put_credit_spread"]
Horizon = Literal["short", "leaps", "both"]

STRATEGIES: tuple[Strategy, ...] = ("csp", "covered_call", "leaps_call", "put_credit_spread")

# ── Expiry selection ────────────────────────────────────────────────────────
#
# The short bucket runs to 45 rather than 30 so that a monthly expiry always
# lands in it; weeklies alone would leave the most liquid contract on the board
# unscreened. The LEAPS floor is 300 rather than 365 because a January expiry
# eleven months out is what people actually trade as a LEAP.
SHORT_DTE_MIN, SHORT_DTE_MAX = 7, 45
LEAPS_DTE_MIN = 300
LEAPS_DTE_TARGET = 365
MAX_SHORT_EXPIRIES = 3
MAX_LEAPS_EXPIRIES = 2

# ── Liquidity and sanity floors ─────────────────────────────────────────────
MIN_OPEN_INTEREST = 25
MIN_VOLUME_FALLBACK = 5
MAX_SPREAD_PCT = 0.20
MIN_MID = 0.05
IV_MIN, IV_MAX = 0.05, 3.0
# Below about a cent of vega the premium barely responds to volatility, so a
# solved IV is an artefact of the solver's bracket rather than a market view.
MIN_VEGA_FOR_IV = 0.01

# ── Strategy bands ──────────────────────────────────────────────────────────
CSP_DELTA_MIN, CSP_DELTA_MAX = 0.05, 0.40
CSP_SWEET_SPOT = (0.15, 0.30)
CC_DELTA_MIN, CC_DELTA_MAX = 0.15, 0.40
LEAPS_DELTA_MIN = 0.70
LEAPS_MAX_EXTRINSIC_PCT = 0.08
SPREAD_MIN_CREDIT_RATIO, SPREAD_MAX_CREDIT_RATIO = 0.10, 0.90

# A full SPY chain runs to thousands of rows and would dwarf everything else on
# the page, so the window is capped before any of it reaches the client.
STRIKE_WINDOW_PCT = 0.35
MAX_CONTRACTS = 400

_OCC_TAIL = re.compile(r"\d{6}[CP]\d{8}$")


@dataclass(frozen=True)
class Contract:
    """One normalised option contract, ready to screen."""

    symbol: str
    expiry: date
    dte: int
    strike: float
    kind: OptionKind
    mid: float
    bid: float | None = None
    ask: float | None = None
    last: float | None = None
    iv: float | None = None
    open_interest: int = 0
    volume: int = 0
    spread_pct: float | None = None
    quality: tuple[str, ...] = field(default_factory=tuple)


@dataclass
class Counts:
    """Why contracts fell out, so an empty table can explain itself."""

    scanned: int = 0
    passed_liquidity: int = 0
    passed_moneyness: int = 0
    ranked: int = 0

    def as_dict(self) -> dict:
        return {
            "scanned": self.scanned,
            "passed_liquidity": self.passed_liquidity,
            "passed_moneyness": self.passed_moneyness,
            "ranked": self.ranked,
        }


# ── Expiry selection ────────────────────────────────────────────────────────


def select_expiries(
    expiries: Sequence[date], today: date, horizon: Horizon = "both"
) -> list[date]:
    """Pick the handful of expiries worth fetching.

    Each expiry is its own network round trip, and a liquid name lists twenty
    to forty of them. Fetching all of them is minutes of latency and a
    guaranteed rate-limit, so the horizons the screens actually use are the
    only ones pulled.
    """
    dated = sorted({e for e in expiries if e > today})

    short: list[date] = []
    leaps: list[date] = []

    if horizon in ("short", "both"):
        short = [e for e in dated if SHORT_DTE_MIN <= (e - today).days <= SHORT_DTE_MAX][
            :MAX_SHORT_EXPIRIES
        ]

    if horizon in ("leaps", "both"):
        candidates = [e for e in dated if (e - today).days >= LEAPS_DTE_MIN]
        # Closest to a year out, not simply the furthest — a 900-day expiry is
        # both illiquid and not what anyone means by a LEAP.
        leaps = sorted(candidates, key=lambda e: abs((e - today).days - LEAPS_DTE_TARGET))[
            :MAX_LEAPS_EXPIRIES
        ]

    return sorted(set(short) | set(leaps))


# ── Normalisation ───────────────────────────────────────────────────────────


def is_standard_contract(occ_symbol: str, underlying: str) -> bool:
    """False for post-split adjusted deliverables.

    An adjusted contract carries a root with a numeric suffix (AAPL1...) and
    does not deliver 100 shares. Every per-contract figure this module computes
    assumes a 100-share multiplier, so those rows would be quietly wrong in
    both directions — the collateral and the credit.
    """
    if not occ_symbol:
        return True  # nothing to check against; the chain didn't give us a symbol
    root = _OCC_TAIL.sub("", occ_symbol)
    return root.upper() == underlying.upper()


def normalise(
    raw: dict,
    *,
    underlying: str,
    expiry: date,
    today: date,
    kind: OptionKind,
    spot: float,
    r: float,
    q: float = 0.0,
    oi_available: bool = True,
) -> Contract | None:
    """Turn one raw chain row into a Contract, or None if it isn't tradeable.

    Returns None rather than raising: a chain with one malformed row should
    lose that row, not the whole expiry.

    `oi_available` is False when the provider returned zero open interest for
    every contract in the expiry — which happens routinely and means the field
    is unpopulated, not that the chain is dead. In that case the OI floor is
    skipped and volume carries the liquidity test alone. Flagging a thousand
    contracts `low_oi` because a field was empty would train the reader to
    ignore the flag on the rows where it is real.
    """
    try:
        strike = float(raw.get("strike"))
    except (TypeError, ValueError):
        return None
    if not _finite(strike) or strike <= 0:
        return None

    symbol = str(raw.get("contractSymbol") or "")
    if not is_standard_contract(symbol, underlying):
        return None

    dte = (expiry - today).days
    if dte <= 0:
        return None

    quality: list[str] = []

    bid = _number(raw.get("bid"))
    ask = _number(raw.get("ask"))
    last = _number(raw.get("lastPrice"))

    spread_pct: float | None = None
    if bid and ask and ask >= bid:
        mid = (bid + ask) / 2.0
        spread_pct = (ask - bid) / mid if mid > 0 else None
    elif last:
        # Outside regular hours the book empties and only the last print
        # survives — which may be days old. Usable, but flagged.
        mid = last
        quality.append("last_price_only")
    else:
        return None

    if mid < MIN_MID:
        return None  # below the noise floor of commissions
    if spread_pct is not None and spread_pct > MAX_SPREAD_PCT:
        return None

    open_interest = int(_number(raw.get("openInterest")) or 0)
    volume = int(_number(raw.get("volume")) or 0)

    if oi_available:
        if open_interest < MIN_OPEN_INTEREST and volume < MIN_VOLUME_FALLBACK:
            return None
        if open_interest < MIN_OPEN_INTEREST:
            quality.append("low_oi")
    elif volume < MIN_VOLUME_FALLBACK:
        return None

    iv = _resolve_iv(
        raw.get("impliedVolatility"), mid=mid, spot=spot, strike=strike,
        dte=dte, r=r, kind=kind, q=q,
    )
    if iv is None:
        quality.append("iv_unreliable")

    return Contract(
        symbol=symbol,
        expiry=expiry,
        dte=dte,
        strike=strike,
        kind=kind,
        mid=mid,
        bid=bid,
        ask=ask,
        last=last,
        iv=iv,
        open_interest=open_interest,
        volume=volume,
        spread_pct=spread_pct,
        quality=tuple(quality),
    )


def _resolve_iv(
    provided, *, mid: float, spot: float, strike: float, dte: int,
    r: float, kind: OptionKind, q: float,
) -> float | None:
    """Back the implied volatility out of the mid ourselves, first.

    Two reasons to prefer our own solve over the provider's published number.

    The first is consistency. Every delta, theta and probability on the panel
    comes from this module's Black-Scholes. If the IV *column* came from
    somewhere else, the row would show a volatility that does not reproduce its
    own greeks, and no amount of staring at it would explain why.

    The second is that the published number is often simply wrong. Yahoo
    reports 0.0625 for four-day AAPL options whose mid implies about 0.26 — a
    plausible-looking figure that clears any range check, so it cannot be
    filtered by bounds alone. It was quietly setting the panel's headline
    "implied volatility" to a sixth of the real value and reporting that
    premium was historically cheap.

    The provider's value is kept only as a fallback for contracts with too
    little vega to solve, where nothing better exists.
    """
    t = dte / 365.0
    solved = implied_vol(mid, spot, strike, t, r, kind, q)
    if solved is not None and IV_MIN <= solved <= IV_MAX:
        # A solve on a contract with no vega is an artefact of the bracket
        # rather than a market view, so it is worth no more than the input.
        if greeks(spot, strike, t, r, solved, kind, q).vega >= MIN_VEGA_FOR_IV:
            return solved

    candidate = _number(provided)
    if candidate is not None and IV_MIN <= candidate <= IV_MAX:
        return candidate
    return None


def within_strike_window(strike: float, spot: float) -> bool:
    if spot <= 0:
        return False
    return abs(strike - spot) / spot <= STRIKE_WINDOW_PCT


def _number(value) -> float | None:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    if not _finite(out) or out <= 0:
        return None
    return out


def _finite(value: float) -> bool:
    return isinstance(value, (int, float)) and math.isfinite(value)


# ── Screens ─────────────────────────────────────────────────────────────────


def screen_csp(
    contracts: Sequence[Contract], *, spot: float, r: float, q: float = 0.0,
    next_earnings: date | None = None, counts: Counts | None = None,
) -> list[dict]:
    """Cash-secured puts: out-of-the-money, short-dated, in the delta band."""
    counts = counts or Counts()
    rows: list[dict] = []

    for c in _liquid_puts(contracts, counts):
        if not (SHORT_DTE_MIN <= c.dte <= SHORT_DTE_MAX) or c.strike > spot:
            continue
        counts.passed_moneyness += 1

        g = greeks(spot, c.strike, c.dte / 365.0, r, c.iv, "put", q)
        if not (CSP_DELTA_MIN <= abs(g.delta) <= CSP_DELTA_MAX):
            continue

        metrics = csp_metrics(
            spot=spot, strike=c.strike, mid=c.mid, dte=c.dte, iv=c.iv, r=r, q=q
        )
        quality = list(c.quality)
        if CSP_SWEET_SPOT[0] <= abs(g.delta) <= CSP_SWEET_SPOT[1]:
            quality.append("sweet_spot")

        rows.append(
            _row("csp", c, g, metrics, quality, spot, next_earnings)
            | {"score": _credit_score(metrics), "score_basis": "ann_yield_x_pop"}
        )

    counts.ranked = len(rows)
    return _ranked(rows)


def screen_covered_call(
    contracts: Sequence[Contract], *, spot: float, r: float, q: float = 0.0,
    next_earnings: date | None = None, counts: Counts | None = None,
) -> list[dict]:
    """Covered calls against 100 shares: out-of-the-money, short-dated."""
    counts = counts or Counts()
    rows: list[dict] = []

    for c in _liquid_calls(contracts, counts):
        if not (SHORT_DTE_MIN <= c.dte <= SHORT_DTE_MAX) or c.strike < spot:
            continue
        counts.passed_moneyness += 1

        g = greeks(spot, c.strike, c.dte / 365.0, r, c.iv, "call", q)
        if not (CC_DELTA_MIN <= g.delta <= CC_DELTA_MAX):
            continue

        metrics = covered_call_metrics(
            spot=spot, strike=c.strike, mid=c.mid, dte=c.dte, iv=c.iv, r=r, q=q
        )
        score_input = {
            "annualized_yield": metrics["static_return_annualized"],
            "pop": metrics["pop"],
        }
        rows.append(
            _row("covered_call", c, g, metrics, list(c.quality), spot, next_earnings)
            | {"score": _credit_score(score_input), "score_basis": "ann_yield_x_pop"}
        )

    counts.ranked = len(rows)
    return _ranked(rows)


def screen_leaps_call(
    contracts: Sequence[Contract], *, spot: float, r: float, q: float = 0.0,
    next_earnings: date | None = None, counts: Counts | None = None,
) -> list[dict]:
    """Long-dated in-the-money calls used as stock substitutes.

    Scored differently from the credit strategies, and labelled so. A long
    debit trade has no yield on collateral and is not a high-probability
    strategy; ranking it on the same leaderboard as a short put would imply a
    comparison that does not hold.
    """
    counts = counts or Counts()
    rows: list[dict] = []

    for c in _liquid_calls(contracts, counts):
        if c.dte < LEAPS_DTE_MIN:
            continue
        counts.passed_moneyness += 1

        g = greeks(spot, c.strike, c.dte / 365.0, r, c.iv, "call", q)
        if g.delta < LEAPS_DELTA_MIN:
            continue

        metrics = leaps_metrics(
            spot=spot, strike=c.strike, mid=c.mid, dte=c.dte, iv=c.iv, delta=g.delta, r=r, q=q
        )
        extrinsic_pct = metrics["extrinsic_pct_of_spot"]
        if extrinsic_pct is None or extrinsic_pct > LEAPS_MAX_EXTRINSIC_PCT:
            continue

        rows.append(
            _row("leaps_call", c, g, metrics, list(c.quality), spot, next_earnings)
            | {
                "score": metrics["pop"] / max(extrinsic_pct, 0.001),
                "score_basis": "pop_per_extrinsic",
            }
        )

    counts.ranked = len(rows)
    return _ranked(rows)


def screen_put_credit_spread(
    contracts: Sequence[Contract], *, spot: float, r: float, q: float = 0.0,
    next_earnings: date | None = None, counts: Counts | None = None,
) -> list[dict]:
    """Bull put spreads, built by pairing the cash-secured put candidates.

    Needs no data the CSP screen did not already fetch, and it is the direct
    risk-capped counterpart to selling the put outright — which makes the
    difference in tail exposure visible side by side instead of theoretical.
    """
    counts = counts or Counts()
    puts = [c for c in _liquid_puts(contracts, counts) if c.strike <= spot]
    by_expiry: dict[date, list[Contract]] = {}
    for c in puts:
        if SHORT_DTE_MIN <= c.dte <= SHORT_DTE_MAX:
            by_expiry.setdefault(c.expiry, []).append(c)

    min_width = max(1.0, 0.02 * spot)
    rows: list[dict] = []

    for expiry, legs in by_expiry.items():
        legs.sort(key=lambda c: c.strike)
        for i, short_leg in enumerate(legs):
            counts.passed_moneyness += 1
            g = greeks(spot, short_leg.strike, short_leg.dte / 365.0, r, short_leg.iv, "put", q)
            if not (CSP_DELTA_MIN <= abs(g.delta) <= CSP_DELTA_MAX):
                continue

            long_leg = _nearest_long_leg(legs[:i], short_leg.strike, min_width)
            if long_leg is None:
                continue

            metrics = spread_metrics(
                spot=spot, short_strike=short_leg.strike, long_strike=long_leg.strike,
                short_mid=short_leg.mid, long_mid=long_leg.mid,
                dte=short_leg.dte, iv=short_leg.iv, r=r, q=q,
            )
            credit_ratio = metrics["credit"] / metrics["width"] if metrics["width"] > 0 else 0.0
            if not (SPREAD_MIN_CREDIT_RATIO <= credit_ratio <= SPREAD_MAX_CREDIT_RATIO):
                continue

            # Liquidity is the worse of the two legs — a spread is only as
            # tradeable as the leg nobody wants.
            quality = sorted(set(short_leg.quality) | set(long_leg.quality))
            row = _row("put_credit_spread", short_leg, g, metrics, quality, spot, next_earnings)
            row |= {
                "long_strike": long_leg.strike,
                "long_mid": long_leg.mid,
                "open_interest": min(short_leg.open_interest, long_leg.open_interest),
                "volume": min(short_leg.volume, long_leg.volume),
                "spread_pct": max(
                    [x for x in (short_leg.spread_pct, long_leg.spread_pct) if x is not None],
                    default=None,
                ),
                "score": _credit_score(metrics),
                "score_basis": "ann_yield_x_pop",
            }
            rows.append(row)

    counts.ranked = len(rows)
    return _ranked(rows)


SCREENS = {
    "csp": screen_csp,
    "covered_call": screen_covered_call,
    "leaps_call": screen_leaps_call,
    "put_credit_spread": screen_put_credit_spread,
}


def screen_all(
    contracts: Sequence[Contract], *, spot: float, r: float, q: float = 0.0,
    strategy: str = "all", limit: int = 15, next_earnings: date | None = None,
) -> dict:
    """Run one screen or all four, with the counts that explain the result."""
    wanted: tuple[str, ...] = STRATEGIES if strategy == "all" else (strategy,)

    candidates: list[dict] = []
    counts = Counts(scanned=len(contracts))
    per_strategy: dict[str, int] = {}

    for name in wanted:
        run = SCREENS.get(name)
        if run is None:
            continue
        local = Counts(scanned=len(contracts))
        found = run(contracts, spot=spot, r=r, q=q, next_earnings=next_earnings, counts=local)
        rows = found[:limit]
        per_strategy[name] = len(rows)
        candidates.extend(rows)
        counts.passed_liquidity = max(counts.passed_liquidity, local.passed_liquidity)
        counts.passed_moneyness = max(counts.passed_moneyness, local.passed_moneyness)

    counts.ranked = len(candidates)
    return {
        "candidates": candidates,
        "universe_counts": counts.as_dict(),
        "counts_by_strategy": per_strategy,
        "filters_applied": {
            "min_open_interest": MIN_OPEN_INTEREST,
            "max_spread_pct": MAX_SPREAD_PCT,
            "min_mid": MIN_MID,
            "short_dte": [SHORT_DTE_MIN, SHORT_DTE_MAX],
            "leaps_dte_min": LEAPS_DTE_MIN,
            "csp_delta": [CSP_DELTA_MIN, CSP_DELTA_MAX],
            "covered_call_delta": [CC_DELTA_MIN, CC_DELTA_MAX],
            "leaps_delta_min": LEAPS_DELTA_MIN,
        },
    }


# ── Shared helpers ──────────────────────────────────────────────────────────


def _liquid_puts(contracts: Sequence[Contract], counts: Counts) -> list[Contract]:
    return _liquid(contracts, "put", counts)


def _liquid_calls(contracts: Sequence[Contract], counts: Counts) -> list[Contract]:
    return _liquid(contracts, "call", counts)


def _liquid(contracts: Sequence[Contract], kind: OptionKind, counts: Counts) -> list[Contract]:
    out = [c for c in contracts if c.kind == kind and c.iv is not None]
    counts.passed_liquidity = max(counts.passed_liquidity, len(out))
    return out


def _nearest_long_leg(
    lower_strikes: Sequence[Contract], short_strike: float, min_width: float
) -> Contract | None:
    """The closest protective leg at least `min_width` below the short strike."""
    eligible = [c for c in lower_strikes if short_strike - c.strike >= min_width]
    return max(eligible, key=lambda c: c.strike) if eligible else None


def _credit_score(metrics: dict) -> float:
    """Annualised yield times probability of profit.

    Sorting on raw yield puts the near-the-money strikes on top, which is the
    riskiest end of the board. Sorting on raw probability puts worthless
    far-out contracts on top, collecting six cents. The product is the usual
    compromise, and the column header says so rather than presenting it as a
    proprietary rating.

    Comparable **within** a strategy only. A credit spread posts the width as
    collateral rather than the whole strike, so its return on capital — and
    therefore its score — runs an order of magnitude above a cash-secured put
    on the same strike while carrying a different risk entirely. Ranking the
    two against each other would say a spread is ten times the trade, when what
    it actually is, is a tenth of the capital.
    """
    yield_ = metrics.get("annualized_yield")
    pop = metrics.get("pop")
    if yield_ is None or pop is None:
        return 0.0
    return yield_ * pop


def _ranked(rows: list[dict]) -> list[dict]:
    return sorted(rows, key=lambda row: row.get("score") or 0.0, reverse=True)


# Annualising a holding period this short produces headline numbers nobody can
# earn: a defined-risk spread held 8 days routinely scales to four figures,
# because the collateral is only the width. The figure is the standard one and
# stays, but rows that depend on a big extrapolation say so.
SHORT_HOLDING_PERIOD_DTE = 14


def _row(
    strategy: str, c: Contract, g, metrics: dict, quality: list[str],
    spot: float, next_earnings: date | None,
) -> dict:
    """The columns every strategy shares, plus that strategy's own metrics."""
    earnings_before_expiry = bool(next_earnings and next_earnings <= c.expiry)
    if earnings_before_expiry:
        quality = [*quality, "earnings_before_expiry"]
    if c.dte < SHORT_HOLDING_PERIOD_DTE:
        quality = [*quality, "short_dte_extrapolation"]

    return {
        "strategy": strategy,
        "contract_symbol": c.symbol,
        "expiry": c.expiry.isoformat(),
        "dte": c.dte,
        "strike": c.strike,
        "kind": c.kind,
        "bid": c.bid,
        "ask": c.ask,
        "mid": c.mid,
        "iv": c.iv,
        "delta": g.delta,
        "gamma": g.gamma,
        "theta_per_day": g.theta,
        "vega": g.vega,
        "moneyness_pct": (c.strike - spot) / spot if spot > 0 else None,
        "open_interest": c.open_interest,
        "volume": c.volume,
        "spread_pct": c.spread_pct,
        "quality": quality,
        "earnings_before_expiry": earnings_before_expiry,
        **metrics,
    }


# What survives the trim for an agent. Deliberately excludes the liquidity
# columns: the screener has already enforced those floors, so re-showing them
# invites the model to relitigate a filter it cannot see the rest of. Expiry
# goes too — dte says the same thing in four characters instead of twelve.
_AGENT_FIELDS = (
    "dte", "strike", "delta", "iv", "pop", "annualized_yield", "credit",
    "breakeven", "debit", "extrinsic_pct_of_spot", "effective_leverage", "max_loss",
)
AGENT_ROW_LIMIT = 3


def compact_rows(rows: Sequence[dict], limit: int = AGENT_ROW_LIMIT) -> list[dict]:
    """Trim rows to what an agent can actually read.

    Agents serialise tool output through a 2000-character truncation and are
    never told it happened, so an oversized payload becomes a JSON fragment the
    model reasons over confidently. The trimming therefore happens here, where
    it is deliberate and has a test on it, rather than at the truncation point
    where it is silent.

    Three rows per strategy, not five: the agent is writing a paragraph about
    what the chain looks like, not picking a contract, and the fourth-ranked
    candidate changes neither.
    """
    out = []
    for row in rows[:limit]:
        compact = {}
        for key in _AGENT_FIELDS:
            if key not in row:
                continue
            value = row[key]
            if isinstance(value, float):
                value = round(value, 3)
            compact[key] = value
        if row.get("quality"):
            compact["flags"] = row["quality"]
        out.append(compact)
    return out
