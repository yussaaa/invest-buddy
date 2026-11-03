"""Discounted cash flow, with the assumptions in the signature rather than the prose.

A DCF is not a measurement. It is an opinion with arithmetic attached: the
output moves tens of percent on a one-point change in the discount rate, and
most of it is a terminal value resting on a growth rate nobody can observe.
Everything here is shaped to make that visible instead of hiding it behind a
single number — `scenario_band` rather than a value, `sensitivity_grid` rather
than a point, `terminal_value_share` reported alongside every result, and
`implied_growth`, which inverts the question and asks what the market is
already assuming.

**On the discount rate.** The cash flow this model discounts is free cash flow
after interest paid — levered, an equity claim. So it is discounted at the cost
of equity and net debt is *not* subtracted: the debt has already been serviced
inside the cash flows, and subtracting it again would count it twice. The
textbook "discount at WACC, then subtract net debt" pipeline belongs to
unlevered cash flow, and applying it here would produce a number that is
plausible, lower, and wrong. `method` says which is which in the output.

Pure and synchronous. No network, no yfinance, no provider fields — the caller
in valuation.py does the fetching and hands over plain floats.
"""

from __future__ import annotations

from dataclasses import dataclass

DEFAULT_YEARS = 10
DEFAULT_TERMINAL_GROWTH = 0.025      # long-run nominal GDP, roughly
DEFAULT_EQUITY_RISK_PREMIUM = 0.05
DEFAULT_RISK_FREE = 0.04             # matches options.DEFAULT_RISK_FREE

# A beta of 0.35 through CAPM gives a 5.75% discount rate, at which almost
# anything looks worth three times its price. The clamp is a statement that the
# input is not trustworthy at the extremes, not that the maths is wrong — and
# it is reported, never applied silently.
BETA_FLOOR, BETA_CEILING = 0.5, 2.5
DISCOUNT_FLOOR, DISCOUNT_CEILING = 0.06, 0.15

# Gordon explodes as the discount rate approaches terminal growth. Below this
# spread the answer is arithmetic noise, so refuse rather than report it.
MIN_SPREAD = 0.01

GROWTH_FLOOR, GROWTH_CEILING = -0.10, 0.20

SCENARIO_GROWTH_STEP = 0.05
SCENARIO_DISCOUNT_STEP = 0.015
SCENARIO_TERMINAL_STEP = 0.010

METHOD = "fcfe_cost_of_equity"


@dataclass(frozen=True)
class Assumptions:
    initial_growth: float
    terminal_growth: float = DEFAULT_TERMINAL_GROWTH
    discount_rate: float = 0.09
    years: int = DEFAULT_YEARS


def clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def cost_of_equity(
    beta: float | None,
    risk_free: float = DEFAULT_RISK_FREE,
    premium: float = DEFAULT_EQUITY_RISK_PREMIUM,
) -> dict:
    """CAPM, with both clamps reported rather than applied behind your back."""
    raw_beta = 1.0 if beta is None else float(beta)
    used_beta = clamp(raw_beta, BETA_FLOOR, BETA_CEILING)
    raw_rate = risk_free + used_beta * premium
    rate = clamp(raw_rate, DISCOUNT_FLOOR, DISCOUNT_CEILING)
    return {
        "rate": round(rate, 4),
        "raw_rate": round(raw_rate, 4),
        "beta": round(used_beta, 3),
        "raw_beta": round(raw_beta, 3),
        "beta_clamped": used_beta != raw_beta,
        "rate_clamped": rate != raw_rate,
        "risk_free": risk_free,
        "equity_risk_premium": premium,
    }


def fade(initial: float, terminal: float, years: int) -> list[float]:
    """Growth declining linearly from `initial` in year 1 to `terminal` in year N.

    A two-stage model with a cliff — 15% for a decade, then 2.5% forever —
    produces a terminal value nobody believes, because nothing decelerates like
    that. The linear fade is the standard fix and takes one line to explain.
    """
    if years <= 1:
        return [terminal]
    return [initial + (terminal - initial) * i / (years - 1) for i in range(years)]


def project(base_fcf: float, a: Assumptions) -> list[float]:
    """The explicit-period cash flows."""
    flows, flow = [], base_fcf
    for growth in fade(a.initial_growth, a.terminal_growth, a.years):
        flow *= 1 + growth
        flows.append(flow)
    return flows


def dcf_value(base_fcf: float, shares: float, a: Assumptions) -> dict:
    """Present value per share, plus the numbers needed to distrust it properly."""
    if shares <= 0:
        return {"error": "share count unavailable"}
    if base_fcf <= 0:
        return {"error": "free cash flow is negative — there is nothing to discount"}
    if a.discount_rate - a.terminal_growth < MIN_SPREAD:
        return {
            "error": (
                f"a {a.discount_rate:.1%} discount rate is not far enough above "
                f"{a.terminal_growth:.1%} terminal growth for a perpetuity"
            )
        }

    flows = project(base_fcf, a)
    pv_explicit = sum(f / (1 + a.discount_rate) ** (y + 1) for y, f in enumerate(flows))

    final = flows[-1]
    terminal = final * (1 + a.terminal_growth) / (a.discount_rate - a.terminal_growth)
    pv_terminal = terminal / (1 + a.discount_rate) ** a.years

    equity = pv_explicit + pv_terminal
    return {
        "value_per_share": round(equity / shares, 2),
        "equity_value": round(equity, 0),
        "pv_explicit": round(pv_explicit, 0),
        "pv_terminal": round(pv_terminal, 0),
        # The most useful honesty device here. A reader told that 70% of the
        # figure is the terminal assumption calibrates faster than any
        # disclaimer can manage.
        "terminal_value_share": round(pv_terminal / equity, 3) if equity else None,
        # Turns an opaque Gordon input into a checkable claim: "this case
        # implies exiting at 28x free cash flow."
        "implied_exit_fcf_multiple": round(terminal / final, 1) if final else None,
        "projected_fcf": [round(f, 0) for f in flows],
        "method": METHOD,
    }


def scenario_band(base_fcf: float, shares: float, base: Assumptions) -> list[dict]:
    """Bear, base and bull — symmetric steps around the derived assumptions.

    Symmetric on purpose. Asymmetric steps are defensible (discount rates rise
    faster than they fall) but they bake a view into what is meant to be a
    neutral range, and the point of the band is to show the width of the
    uncertainty rather than to lean.
    """
    cases = [
        ("bear", -SCENARIO_GROWTH_STEP, -SCENARIO_TERMINAL_STEP, +SCENARIO_DISCOUNT_STEP),
        ("base", 0.0, 0.0, 0.0),
        ("bull", +SCENARIO_GROWTH_STEP, +SCENARIO_TERMINAL_STEP, -SCENARIO_DISCOUNT_STEP),
    ]

    out = []
    for name, dg, dt, dr in cases:
        assumptions = Assumptions(
            initial_growth=clamp(base.initial_growth + dg, GROWTH_FLOOR, GROWTH_CEILING),
            terminal_growth=max(0.0, base.terminal_growth + dt),
            discount_rate=max(0.01, base.discount_rate + dr),
            years=base.years,
        )
        result = dcf_value(base_fcf, shares, assumptions)
        out.append({
            "case": name,
            "assumptions": {
                "initial_growth": round(assumptions.initial_growth, 4),
                "terminal_growth": round(assumptions.terminal_growth, 4),
                "discount_rate": round(assumptions.discount_rate, 4),
                "years": assumptions.years,
            },
            # One scenario failing its spread guard must not take the panel
            # down; the others still render.
            **result,
        })
    return out


def sensitivity_grid(base_fcf: float, shares: float, base: Assumptions) -> dict:
    """Value across discount rate x terminal growth.

    The point estimate is meaningless without this. Two inputs nobody can pin
    down move the answer more than anything the analysis actually observed, and
    a grid says so at a glance where a paragraph would not.
    """
    discounts = [round(base.discount_rate + d, 4) for d in (-0.02, -0.01, 0.0, 0.01, 0.02)]
    terminals = [0.015, 0.020, 0.025, 0.030, 0.035]

    values: list[list[float | None]] = []
    for rate in discounts:
        row: list[float | None] = []
        for terminal in terminals:
            result = dcf_value(
                base_fcf,
                shares,
                Assumptions(base.initial_growth, terminal, rate, base.years),
            )
            row.append(result.get("value_per_share"))
        values.append(row)

    return {"discount_rates": discounts, "terminal_growths": terminals, "values": values}


def implied_growth(
    price: float,
    base_fcf: float,
    shares: float,
    a: Assumptions,
    *,
    low: float = -0.50,
    high: float = 1.00,
    tolerance: float = 1e-6,
) -> float | None:
    """The growth rate today's price already assumes.

    This inverts the usual question, and it is the more honest one to lead with.
    A fair value is a price target with a model attached; an implied growth rate
    is a description of the price that anyone can argue with — "the market needs
    11% a year for a decade, does that seem right for this company?"

    It is also far more robust. A forward DCF inherits all the fragility of a
    growth estimate drawn from four noisy cash-flow years; this takes the growth
    rate as the unknown and lets the market supply the value.

    Bisection rather than a solver: the function is monotonic in growth, and
    fifty iterations of pure arithmetic is instant.
    """
    if price <= 0 or shares <= 0 or base_fcf <= 0:
        return None

    def value_at(growth: float) -> float | None:
        result = dcf_value(
            base_fcf, shares, Assumptions(growth, a.terminal_growth, a.discount_rate, a.years)
        )
        return result.get("value_per_share")

    lo_value, hi_value = value_at(low), value_at(high)
    if lo_value is None or hi_value is None:
        return None
    # Price outside the bracket: no growth rate in a plausible range explains it.
    if not (lo_value <= price <= hi_value):
        return None

    for _ in range(200):
        mid = (low + high) / 2
        value = value_at(mid)
        if value is None:
            return None
        if abs(value - price) < tolerance or high - low < tolerance:
            return round(mid, 4)
        if value < price:
            low = mid
        else:
            high = mid
    return round((low + high) / 2, 4)
