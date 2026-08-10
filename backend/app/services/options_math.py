"""Black-Scholes pricing, greeks and strategy arithmetic.

No I/O, no database, no pandas, nothing imported from this app — everything
here is a function of its arguments, so the pricing model and the return
formulas can be tested in the unit suite without a network or a market.
Same treatment as bar_derive.py, which sits next to its I/O sibling for the
same reason.

Conventions, stated because implementations silently disagree about them:

* ``t`` is in **years measured as calendar days / 365**, not trading days. A
  short option decays over the weekend, and the DTE shown in the UI counts
  calendar days, so the two must agree.
* ``theta`` is returned **per calendar day** (the annual figure over 365).
  A premium seller reads theta as "dollars per day"; the field is named
  ``theta_per_day`` in payloads so nobody has to guess which it is.
* ``vega`` is per **one volatility point** (a move from 30% to 31%), i.e. the
  raw derivative over 100. ``rho`` likewise, per one percentage point of rate.
* ``r`` and ``q`` are continuous annual rates as decimals (0.04, not 4). The
  caller supplies both — this module never looks up a rate.
* Degenerate inputs return intrinsic value and flat greeks rather than
  raising. Expiry-day chains, zero-bid wings and stale zero IVs are the
  ordinary case in real chain data, not the exception.

On probabilities: every probability here is **risk-neutral**, computed under
the drift ``(r - q - sigma^2/2)``. That deliberately assumes the underlying has
no expected return. It is a statement about what the option is priced for, not
a forecast of what the stock will do, and callers are expected to label it as
such.

On return denominators: cash-secured put return-on-capital is computed gross,
``mid / strike``, and covered-call static return as ``mid / spot``. The
net-of-credit denominators (``mid / (strike - mid)``) are also in common use
and run about 1-3% higher. We use the gross form throughout; the difference
matters only when comparing against another screener.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

OptionKind = Literal["call", "put"]

DAYS_PER_YEAR = 365.0
TRADING_DAYS = 252

# Bisection bounds for implied vol. Anything outside this is a data error, not
# a market: 500% vol on a listed equity option means the mid is garbage.
_IV_LOW = 1e-4
_IV_HIGH = 5.0
_IV_ITERATIONS = 100
_IV_TOLERANCE = 1e-6


@dataclass(frozen=True)
class Greeks:
    """Sensitivities in the units the panel displays.

    delta is per $1 of underlying, gamma per $1 squared, theta per calendar
    day, vega per volatility point, rho per percentage point of rate.
    """

    delta: float
    gamma: float
    theta: float
    vega: float
    rho: float


ZERO_GREEKS = Greeks(delta=0.0, gamma=0.0, theta=0.0, vega=0.0, rho=0.0)


# ── Normal distribution ─────────────────────────────────────────────────────
#
# Hand-rolled rather than scipy.stats.norm. scipy is importable in this
# environment and risk_metrics.py already uses it, but it is not declared in
# pyproject.toml — it arrives transitively through ragas/mlflow. Building the
# pricing core on an accidental dependency would break the day that chain
# changes. math.erf is stdlib and gives full double precision.


def norm_cdf(x: float) -> float:
    """Standard normal CDF."""
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def norm_pdf(x: float) -> float:
    """Standard normal density."""
    return math.exp(-0.5 * x * x) / math.sqrt(2.0 * math.pi)


# ── Black-Scholes ───────────────────────────────────────────────────────────


def _degenerate(s: float, k: float, t: float, sigma: float) -> bool:
    """True when the closed form doesn't apply and intrinsic value is the answer."""
    return t <= 0.0 or sigma <= 0.0


def d1_d2(
    s: float, k: float, t: float, r: float, sigma: float, q: float = 0.0
) -> tuple[float, float] | None:
    """The two Black-Scholes moneyness terms, or None if inputs are degenerate."""
    if s <= 0.0 or k <= 0.0 or _degenerate(s, k, t, sigma):
        return None
    vol_t = sigma * math.sqrt(t)
    d1 = (math.log(s / k) + (r - q + 0.5 * sigma * sigma) * t) / vol_t
    return d1, d1 - vol_t


def intrinsic(s: float, k: float, kind: OptionKind) -> float:
    return max(0.0, s - k) if kind == "call" else max(0.0, k - s)


def bs_price(
    s: float, k: float, t: float, r: float, sigma: float, kind: OptionKind, q: float = 0.0
) -> float | None:
    """Black-Scholes-Merton price with a continuous dividend yield.

    Returns None only for nonsensical inputs (non-positive spot or strike).
    At expiry or zero vol the price is intrinsic value, which is correct rather
    than an error.
    """
    if s <= 0.0 or k <= 0.0:
        return None
    if _degenerate(s, k, t, sigma):
        return intrinsic(s, k, kind)

    dd = d1_d2(s, k, t, r, sigma, q)
    if dd is None:
        return intrinsic(s, k, kind)
    d1, d2 = dd

    discounted_spot = s * math.exp(-q * t)
    discounted_strike = k * math.exp(-r * t)

    if kind == "call":
        return discounted_spot * norm_cdf(d1) - discounted_strike * norm_cdf(d2)
    return discounted_strike * norm_cdf(-d2) - discounted_spot * norm_cdf(-d1)


def greeks(
    s: float, k: float, t: float, r: float, sigma: float, kind: OptionKind, q: float = 0.0
) -> Greeks:
    """Delta, gamma, theta, vega and rho in display units.

    At expiry delta collapses to the step function — the true limit, and the
    useful answer for a chain that includes today's expiry — while the other
    four go flat, since there is no time left for them to act on.
    """
    if s <= 0.0 or k <= 0.0:
        return ZERO_GREEKS
    if _degenerate(s, k, t, sigma):
        if kind == "call":
            return Greeks(delta=1.0 if s > k else 0.0, gamma=0.0, theta=0.0, vega=0.0, rho=0.0)
        return Greeks(delta=-1.0 if s < k else 0.0, gamma=0.0, theta=0.0, vega=0.0, rho=0.0)

    dd = d1_d2(s, k, t, r, sigma, q)
    if dd is None:
        return ZERO_GREEKS
    d1, d2 = dd

    sqrt_t = math.sqrt(t)
    div_discount = math.exp(-q * t)
    rate_discount = math.exp(-r * t)
    pdf_d1 = norm_pdf(d1)

    gamma = div_discount * pdf_d1 / (s * sigma * sqrt_t)
    vega_raw = s * div_discount * pdf_d1 * sqrt_t
    decay = -(s * pdf_d1 * sigma * div_discount) / (2.0 * sqrt_t)

    if kind == "call":
        delta = div_discount * norm_cdf(d1)
        theta_annual = (
            decay - r * k * rate_discount * norm_cdf(d2) + q * s * div_discount * norm_cdf(d1)
        )
        rho_raw = k * t * rate_discount * norm_cdf(d2)
    else:
        delta = -div_discount * norm_cdf(-d1)
        theta_annual = (
            decay + r * k * rate_discount * norm_cdf(-d2) - q * s * div_discount * norm_cdf(-d1)
        )
        rho_raw = -k * t * rate_discount * norm_cdf(-d2)

    return Greeks(
        delta=delta,
        gamma=gamma,
        theta=theta_annual / DAYS_PER_YEAR,
        vega=vega_raw / 100.0,
        rho=rho_raw / 100.0,
    )


def implied_vol(
    price: float,
    s: float,
    k: float,
    t: float,
    r: float,
    kind: OptionKind,
    q: float = 0.0,
) -> float | None:
    """Solve for the volatility that reproduces `price`, by bisection.

    Bisection rather than Newton-Raphson on purpose: vega collapses toward zero
    on deep wings, which is exactly where chain data is worst, and Newton
    diverges there. A hundred halvings of [0.0001, 5] is far more precision
    than the input mid justifies and still costs microseconds.

    Returns None when the price is outside what the model can produce — below
    intrinsic, or above the 500% vol ceiling. That is a data problem, and the
    caller should drop the contract rather than display a solved number.
    """
    if s <= 0.0 or k <= 0.0 or t <= 0.0 or price <= 0.0:
        return None

    # The floor is the *discounted* bound, not undiscounted intrinsic. A deep
    # ITM European put legitimately trades below K-S, because the holder gets
    # the strike only at expiry and cannot exercise early to claim it sooner.
    # Rejecting those as impossible would silently drop every deep in-the-money
    # put from the chain.
    discounted_spot = s * math.exp(-q * t)
    discounted_strike = k * math.exp(-r * t)
    floor = (
        max(0.0, discounted_spot - discounted_strike)
        if kind == "call"
        else max(0.0, discounted_strike - discounted_spot)
    )
    if price < floor - _IV_TOLERANCE:
        return None

    low, high = _IV_LOW, _IV_HIGH
    price_high = bs_price(s, k, t, r, high, kind, q)
    if price_high is None or price > price_high:
        return None

    for _ in range(_IV_ITERATIONS):
        mid_vol = 0.5 * (low + high)
        value = bs_price(s, k, t, r, mid_vol, kind, q)
        if value is None:
            return None
        if abs(value - price) < _IV_TOLERANCE:
            return mid_vol
        if value > price:
            high = mid_vol
        else:
            low = mid_vol

    return 0.5 * (low + high)


# ── Risk-neutral probabilities ──────────────────────────────────────────────


def prob_above(s: float, level: float, t: float, r: float, sigma: float, q: float = 0.0) -> float:
    """Risk-neutral P(S_T > level).

    This is N(d2) evaluated with `level` as the strike. It carries no view on
    the stock's expected return — under the risk-neutral measure there isn't
    one — so it answers "what is this priced for", not "what will happen".
    """
    if s <= 0.0 or level <= 0.0:
        return 0.0
    if t <= 0.0 or sigma <= 0.0:
        return 1.0 if s > level else 0.0
    dd = d1_d2(s, level, t, r, sigma, q)
    if dd is None:
        return 1.0 if s > level else 0.0
    return norm_cdf(dd[1])


def prob_below(s: float, level: float, t: float, r: float, sigma: float, q: float = 0.0) -> float:
    """Risk-neutral P(S_T < level)."""
    return 1.0 - prob_above(s, level, t, r, sigma, q)


def prob_itm(
    s: float, k: float, t: float, r: float, sigma: float, kind: OptionKind, q: float = 0.0
) -> float:
    """Risk-neutral probability the option finishes in the money."""
    if kind == "call":
        return prob_above(s, k, t, r, sigma, q)
    return prob_below(s, k, t, r, sigma, q)


# ── Realized volatility ─────────────────────────────────────────────────────


def realized_vol(closes: Sequence[float], window: int = 20) -> float | None:
    """Annualised close-to-close volatility over the last `window` returns.

    Sample standard deviation (n-1) of log returns, scaled by sqrt(252). None
    when there aren't enough clean observations — a partial window would read
    as a confident number computed from three days.
    """
    returns = _log_returns(closes)
    if len(returns) < window or window < 2:
        return None

    tail = returns[-window:]
    mean = sum(tail) / len(tail)
    variance = sum((x - mean) ** 2 for x in tail) / (len(tail) - 1)
    return math.sqrt(variance) * math.sqrt(TRADING_DAYS)


def rolling_realized_vol(closes: Sequence[float], window: int = 20) -> list[float]:
    """Realized vol at every point with a full window behind it."""
    returns = _log_returns(closes)
    if len(returns) < window or window < 2:
        return []

    out: list[float] = []
    for end in range(window, len(returns) + 1):
        chunk = returns[end - window : end]
        mean = sum(chunk) / len(chunk)
        variance = sum((x - mean) ** 2 for x in chunk) / (len(chunk) - 1)
        out.append(math.sqrt(variance) * math.sqrt(TRADING_DAYS))
    return out


def _log_returns(closes: Sequence[float]) -> list[float]:
    out: list[float] = []
    previous: float | None = None
    for close in closes:
        if close is None or close <= 0.0:
            previous = None
            continue
        if previous is not None:
            out.append(math.log(close / previous))
        previous = close
    return out


def percentile_of(value: float, sample: Sequence[float]) -> float | None:
    """Where `value` sits inside `sample`, as 0-100.

    The fraction of observations at or below it. Used to place today's implied
    vol inside a year of this stock's own realized vol.
    """
    clean = [x for x in sample if x is not None and math.isfinite(x)]
    if not clean:
        return None
    at_or_below = sum(1 for x in clean if x <= value)
    return 100.0 * at_or_below / len(clean)


# ── Return arithmetic ───────────────────────────────────────────────────────


def annualize_simple(period_return: float, days: int | float) -> float | None:
    """Scale a holding-period return to a year, linearly.

    Deliberately not compounded. Compounding a 7-day credit 52 times produces
    headline numbers in the hundreds of percent that no one can actually earn,
    because the capital is only at risk while the position is open and the same
    trade is not available every week.
    """
    if not days or days <= 0:
        return None
    return period_return * DAYS_PER_YEAR / days


def csp_metrics(
    *, spot: float, strike: float, mid: float, dte: int, iv: float, r: float, q: float = 0.0
) -> dict:
    """Cash-secured put: yield on collateral, breakeven, assignment odds."""
    t = dte / DAYS_PER_YEAR
    collateral = strike * 100.0
    roc = mid / strike if strike > 0 else 0.0
    breakeven = strike - mid

    return {
        "credit": mid,
        "collateral": collateral,
        "return_on_capital": roc,
        "annualized_yield": annualize_simple(roc, dte),
        "credit_per_day": mid / dte if dte > 0 else None,
        "breakeven": breakeven,
        "discount_to_spot": (spot - breakeven) / spot if spot > 0 else None,
        "prob_itm": prob_itm(spot, strike, t, r, iv, "put", q),
        # Profitable at expiry whenever the stock holds above the breakeven,
        # which sits a credit's width below the strike.
        "pop": prob_above(spot, breakeven, t, r, iv, q),
    }


def covered_call_metrics(
    *, spot: float, strike: float, mid: float, dte: int, iv: float, r: float, q: float = 0.0
) -> dict:
    """Covered call: static and if-called returns, upside cap, assignment odds.

    Note `pop` here means what it means everywhere else in this module — the
    probability the position is profitable at expiry, i.e. the stock holds
    above the downside breakeven. It is *not* the probability of keeping the
    shares; that is `prob_keep_shares`, and the two differ by a lot. Calling
    the latter "probability of profit" would flatter every covered call on the
    board.
    """
    t = dte / DAYS_PER_YEAR
    static_return = mid / spot if spot > 0 else 0.0
    if_called_return = (strike - spot + mid) / spot if spot > 0 else 0.0
    downside_breakeven = spot - mid
    prob_called = prob_above(spot, strike, t, r, iv, q)

    return {
        "credit": mid,
        "static_return": static_return,
        "static_return_annualized": annualize_simple(static_return, dte),
        "if_called_return": if_called_return,
        "if_called_return_annualized": annualize_simple(if_called_return, dte),
        "upside_cap_pct": (strike - spot) / spot if spot > 0 else None,
        "downside_breakeven": downside_breakeven,
        "prob_called": prob_called,
        "prob_keep_shares": 1.0 - prob_called,
        "pop": prob_above(spot, downside_breakeven, t, r, iv, q),
    }


def leaps_metrics(
    *, spot: float, strike: float, mid: float, dte: int, iv: float, delta: float,
    r: float, q: float = 0.0,
) -> dict:
    """Long-dated call: what the time premium costs and what it buys.

    A LEAPS call is a debit trade, so there is no yield on collateral to quote.
    The numbers that matter are how much of the premium is time value (which
    decays to nothing) and how much underlying exposure the delta buys per
    dollar spent.
    """
    t = dte / DAYS_PER_YEAR
    intrinsic_value = max(0.0, spot - strike)
    extrinsic = mid - intrinsic_value
    breakeven = strike + mid
    theta_per_day = greeks(spot, strike, t, r, iv, "call", q).theta

    return {
        "debit": mid,
        "intrinsic": intrinsic_value,
        "extrinsic": extrinsic,
        "extrinsic_pct_of_spot": extrinsic / spot if spot > 0 else None,
        "effective_leverage": (delta * spot / mid) if mid > 0 else None,
        "breakeven": breakeven,
        "breakeven_move_pct": (breakeven - spot) / spot if spot > 0 else None,
        "theta_drag_per_day_pct": (theta_per_day / mid) if mid > 0 else None,
        "pop": prob_above(spot, breakeven, t, r, iv, q),
        "long_dated": dte >= 365,
    }


def spread_metrics(
    *, spot: float, short_strike: float, long_strike: float, short_mid: float,
    long_mid: float, dte: int, iv: float, r: float, q: float = 0.0,
) -> dict:
    """Put credit spread: the risk-capped counterpart to a cash-secured put.

    Collateral is the width rather than the full strike, which is the entire
    point — the same directional view for a fraction of the capital and a
    known worst case.
    """
    t = dte / DAYS_PER_YEAR
    width = short_strike - long_strike
    credit = short_mid - long_mid
    max_loss = width - credit
    breakeven = short_strike - credit
    roc = credit / max_loss if max_loss > 0 else None

    return {
        "width": width,
        "credit": credit,
        "max_loss": max_loss,
        "max_profit": credit,
        "collateral": width * 100.0,
        "return_on_capital": roc,
        "annualized_yield": annualize_simple(roc, dte) if roc is not None else None,
        "breakeven": breakeven,
        "risk_reward": (credit / max_loss) if max_loss > 0 else None,
        "prob_itm": prob_itm(spot, short_strike, t, r, iv, "put", q),
        "pop": prob_above(spot, breakeven, t, r, iv, q),
    }
