"""Unit tests for the options pricing model — no DB, no network.

The reference case throughout is the textbook one: S=100, K=100, T=1, r=5%,
sigma=20%, no dividend, which prices to 10.4506 / 5.5735. Where a single
reference value would not catch a formula that happens to be right at one
point and wrong elsewhere, the tests assert an invariant over a grid instead
(put-call parity) or check the analytic greek against a finite difference of
the price function.
"""

from __future__ import annotations

import math

import pytest

from app.services.options_math import (
    annualize_simple,
    bs_price,
    covered_call_metrics,
    csp_metrics,
    greeks,
    implied_vol,
    leaps_metrics,
    norm_cdf,
    norm_pdf,
    percentile_of,
    prob_above,
    prob_below,
    realized_vol,
    rolling_realized_vol,
    spread_metrics,
)

REF = dict(s=100.0, k=100.0, t=1.0, r=0.05, sigma=0.20, q=0.0)

# A grid wide enough to include deep ITM, deep OTM, short and long dated,
# dividend-paying and not.
GRID = [
    (s, k, t, r, sigma, q)
    for s in (50.0, 95.0, 100.0, 105.0, 200.0)
    for k in (80.0, 100.0, 120.0)
    for t in (0.02, 0.25, 1.0, 2.0)
    for r in (0.0, 0.05)
    for sigma in (0.10, 0.35, 0.80)
    for q in (0.0, 0.03)
]


# ── Normal distribution ─────────────────────────────────────────────────────


def test_norm_cdf_matches_published_values():
    assert norm_cdf(0.0) == pytest.approx(0.5, abs=1e-12)
    assert norm_cdf(1.0) == pytest.approx(0.8413447461, abs=1e-9)
    assert norm_cdf(1.96) == pytest.approx(0.9750021049, abs=1e-9)
    assert norm_cdf(-1.96) == pytest.approx(0.0249978951, abs=1e-9)


def test_norm_cdf_is_symmetric():
    for x in (0.1, 0.5, 1.0, 2.5, 4.0):
        assert norm_cdf(x) + norm_cdf(-x) == pytest.approx(1.0, abs=1e-12)


def test_norm_pdf_peak_and_symmetry():
    assert norm_pdf(0.0) == pytest.approx(1.0 / math.sqrt(2 * math.pi), abs=1e-12)
    assert norm_pdf(1.5) == pytest.approx(norm_pdf(-1.5), abs=1e-12)


# ── Black-Scholes prices ────────────────────────────────────────────────────


def test_textbook_reference_price():
    assert bs_price(kind="call", **REF) == pytest.approx(10.4506, abs=1e-4)
    assert bs_price(kind="put", **REF) == pytest.approx(5.5735, abs=1e-4)


def test_put_call_parity_holds_across_the_grid():
    """C - P == S*e^(-qT) - K*e^(-rT).

    The strongest single invariant available: it pins the discounting of both
    the spot and the strike, so any sign error in r or q shows up here even
    when the reference case happens to pass.
    """
    for s, k, t, r, sigma, q in GRID:
        call = bs_price(s, k, t, r, sigma, "call", q)
        put = bs_price(s, k, t, r, sigma, "put", q)
        expected = s * math.exp(-q * t) - k * math.exp(-r * t)
        where = (s, k, t, r, sigma, q)
        assert call - put == pytest.approx(expected, abs=1e-9), f"parity failed at {where}"


def test_price_respects_the_european_lower_bound():
    """A European option can trade below undiscounted intrinsic.

    The real floor is the discounted one — max(0, S*e^(-qT) - K*e^(-rT)) for a
    call. A deep-ITM European call on a dividend payer sits below S-K because
    the holder receives neither the dividends nor the strike until expiry, and
    cannot exercise early to fix that. Asserting the undiscounted bound here
    would encode an American option's behaviour into a European model.
    """
    for s, k, t, r, sigma, q in GRID:
        call_floor = max(0.0, s * math.exp(-q * t) - k * math.exp(-r * t))
        put_floor = max(0.0, k * math.exp(-r * t) - s * math.exp(-q * t))
        assert bs_price(s, k, t, r, sigma, "call", q) >= call_floor - 1e-9
        assert bs_price(s, k, t, r, sigma, "put", q) >= put_floor - 1e-9


def test_nonpositive_spot_or_strike_returns_none():
    assert bs_price(0.0, 100.0, 1.0, 0.05, 0.2, "call") is None
    assert bs_price(-5.0, 100.0, 1.0, 0.05, 0.2, "call") is None
    assert bs_price(100.0, 0.0, 1.0, 0.05, 0.2, "put") is None


def test_expiry_and_zero_vol_collapse_to_intrinsic():
    assert bs_price(110.0, 100.0, 0.0, 0.05, 0.2, "call") == 10.0
    assert bs_price(90.0, 100.0, 0.0, 0.05, 0.2, "call") == 0.0
    assert bs_price(90.0, 100.0, 0.0, 0.05, 0.2, "put") == 10.0
    assert bs_price(110.0, 100.0, 1.0, 0.05, 0.0, "call") == 10.0


# ── Greeks ──────────────────────────────────────────────────────────────────


def test_delta_matches_finite_difference():
    h = 1e-5
    for s, k, t, r, sigma, q in GRID:
        for kind in ("call", "put"):
            up = bs_price(s + h, k, t, r, sigma, kind, q)
            down = bs_price(s - h, k, t, r, sigma, kind, q)
            numeric = (up - down) / (2 * h)
            assert greeks(s, k, t, r, sigma, kind, q).delta == pytest.approx(numeric, abs=1e-4)


def test_gamma_matches_second_finite_difference():
    h = 1e-3
    for s, k, t, r, sigma, q in GRID:
        base = bs_price(s, k, t, r, sigma, "call", q)
        up = bs_price(s + h, k, t, r, sigma, "call", q)
        down = bs_price(s - h, k, t, r, sigma, "call", q)
        numeric = (up - 2 * base + down) / (h * h)
        assert greeks(s, k, t, r, sigma, "call", q).gamma == pytest.approx(numeric, abs=1e-3)


def test_vega_is_per_volatility_point():
    h = 1e-6
    for s, k, t, r, sigma, q in GRID:
        up = bs_price(s, k, t, r, sigma + h, "call", q)
        down = bs_price(s, k, t, r, sigma - h, "call", q)
        numeric_per_unit = (up - down) / (2 * h)
        assert greeks(s, k, t, r, sigma, "call", q).vega == pytest.approx(
            numeric_per_unit / 100.0, abs=1e-6
        )


def test_theta_is_per_calendar_day():
    h = 1e-6
    for s, k, t, r, sigma, q in GRID:
        if t <= h:
            continue
        # Theta is the derivative with respect to *calendar* time, so price
        # falls as t shrinks.
        later = bs_price(s, k, t - h, r, sigma, "call", q)
        earlier = bs_price(s, k, t + h, r, sigma, "call", q)
        numeric_annual = (later - earlier) / (2 * h)
        assert greeks(s, k, t, r, sigma, "call", q).theta == pytest.approx(
            numeric_annual / 365.0, abs=1e-6
        )


def test_put_delta_equals_call_delta_minus_discount():
    for s, k, t, r, sigma, q in GRID:
        call = greeks(s, k, t, r, sigma, "call", q)
        put = greeks(s, k, t, r, sigma, "put", q)
        assert put.delta == pytest.approx(call.delta - math.exp(-q * t), abs=1e-12)


def test_gamma_and_vega_are_identical_for_calls_and_puts():
    for s, k, t, r, sigma, q in GRID:
        call = greeks(s, k, t, r, sigma, "call", q)
        put = greeks(s, k, t, r, sigma, "put", q)
        assert call.gamma == pytest.approx(put.gamma, abs=1e-12)
        assert call.vega == pytest.approx(put.vega, abs=1e-12)


def test_long_atm_option_decays():
    assert greeks(kind="call", **REF).theta < 0
    assert greeks(kind="put", **REF).theta < 0


def test_delta_saturates_at_the_extremes():
    deep_itm = greeks(500.0, 100.0, 0.25, 0.05, 0.2, "call")
    deep_otm = greeks(10.0, 100.0, 0.25, 0.05, 0.2, "call")
    assert deep_itm.delta == pytest.approx(1.0, abs=1e-6)
    assert deep_otm.delta == pytest.approx(0.0, abs=1e-6)


def test_degenerate_greeks_do_not_raise():
    at_expiry = greeks(110.0, 100.0, 0.0, 0.05, 0.2, "call")
    assert at_expiry.delta == 1.0, "delta collapses to the step function"
    assert (at_expiry.gamma, at_expiry.theta, at_expiry.vega, at_expiry.rho) == (0.0, 0.0, 0.0, 0.0)

    assert greeks(0.0, 100.0, 1.0, 0.05, 0.2, "call").delta == 0.0
    assert greeks(100.0, 100.0, 1.0, 0.05, 0.0, "put").vega == 0.0


# ── Implied vol ─────────────────────────────────────────────────────────────


def test_implied_vol_reprices_to_the_input():
    """The solver's actual contract: the vol it returns reproduces the price.

    Stated this way it holds everywhere, including on wings where the vol
    itself is not identifiable.
    """
    for s, k, t, r, sigma, q in GRID:
        for kind in ("call", "put"):
            price = bs_price(s, k, t, r, sigma, kind, q)
            if price is None or price < 0.01:
                continue  # nothing to solve from; the screener drops these
            solved = implied_vol(price, s, k, t, r, kind, q)
            assert solved is not None
            assert bs_price(s, k, t, r, solved, kind, q) == pytest.approx(price, abs=1e-5)


def test_implied_vol_recovers_sigma_where_vega_is_meaningful():
    """Exact recovery is only possible where the price depends on vol at all."""
    for s, k, t, r, sigma, q in GRID:
        for kind in ("call", "put"):
            price = bs_price(s, k, t, r, sigma, kind, q)
            if price is None:
                continue
            # Below about a cent of vega, a whole range of vols reprices to
            # within a rounding error of the same premium.
            if greeks(s, k, t, r, sigma, kind, q).vega < 0.01:
                continue
            solved = implied_vol(price, s, k, t, r, kind, q)
            assert solved == pytest.approx(sigma, abs=1e-3)


def test_implied_vol_is_not_identifiable_on_deep_wings():
    """Documents why the screener must not display solved IV for these.

    A 200-strike call with the stock at 500 is almost pure intrinsic: vol can
    move enormously without moving the premium a cent, so whatever the solver
    returns is an artefact of its bracket, not a market view.
    """
    s, k, t, r, q = 500.0, 200.0, 0.02, 0.05, 0.0  # ~7 days, stock at 2.5x the strike
    price_low = bs_price(s, k, t, r, 0.10, "call", q)
    price_high = bs_price(s, k, t, r, 0.60, "call", q)

    assert abs(price_high - price_low) < 0.01, "premium barely moves across a 50-point vol range"
    assert greeks(s, k, t, r, 0.10, "call", q).vega < 0.01


def test_implied_vol_rejects_impossible_prices():
    assert implied_vol(0.0, 100.0, 100.0, 1.0, 0.05, "call") is None
    assert implied_vol(1e6, 100.0, 100.0, 1.0, 0.05, "call") is None, "above the vol ceiling"
    assert implied_vol(5.0, 100.0, 100.0, 0.0, 0.05, "call") is None, "no time value to solve"


# ── Probabilities ───────────────────────────────────────────────────────────


def test_probabilities_are_bounded_and_complementary():
    for s, k, t, r, sigma, q in GRID:
        above = prob_above(s, k, t, r, sigma, q)
        assert 0.0 <= above <= 1.0
        assert above + prob_below(s, k, t, r, sigma, q) == pytest.approx(1.0, abs=1e-12)


def test_short_put_pop_falls_as_the_strike_approaches_spot():
    """Selling a strike closer to the money is likelier to be tested."""
    previous = 1.1
    for strike in (70.0, 80.0, 90.0, 95.0, 99.0):
        pop = csp_metrics(spot=100.0, strike=strike, mid=1.0, dte=30, iv=0.3, r=0.04)["pop"]
        assert pop < previous, f"POP should fall as strike rises, broke at {strike}"
        previous = pop


def test_breakeven_is_likelier_than_the_strike_for_a_short_put():
    """The credit buys a cushion: breakeven sits below the strike."""
    metrics = csp_metrics(spot=100.0, strike=95.0, mid=2.0, dte=30, iv=0.3, r=0.04)
    assert metrics["pop"] > 1.0 - metrics["prob_itm"]


def test_probability_is_a_step_function_at_expiry():
    assert prob_above(110.0, 100.0, 0.0, 0.05, 0.2) == 1.0
    assert prob_above(90.0, 100.0, 0.0, 0.05, 0.2) == 0.0


# Deliberately absent: a test asserting POP is monotone in DTE. Under a
# risk-neutral drift it is not — the (r - q - sigma^2/2) term can push either
# way depending on rate and vol — so such a test would encode a wrong belief.


# ── Return arithmetic ───────────────────────────────────────────────────────


def test_annualize_is_linear_not_compounded():
    assert annualize_simple(0.015, 30) == pytest.approx(0.15 * 1.2166666, abs=1e-6)
    assert annualize_simple(0.01, 365) == pytest.approx(0.01, abs=1e-12)
    assert annualize_simple(0.01, 0) is None


def test_csp_arithmetic_is_hand_checkable():
    m = csp_metrics(spot=105.0, strike=100.0, mid=1.50, dte=30, iv=0.30, r=0.04)

    assert m["credit"] == 1.50
    assert m["collateral"] == 10_000.0
    assert m["return_on_capital"] == pytest.approx(0.015, abs=1e-12)
    assert m["annualized_yield"] == pytest.approx(0.015 * 365 / 30, abs=1e-12)
    assert m["annualized_yield"] == pytest.approx(0.18250, abs=1e-5)
    assert m["breakeven"] == pytest.approx(98.50, abs=1e-12)
    assert m["credit_per_day"] == pytest.approx(0.05, abs=1e-12)
    assert m["discount_to_spot"] == pytest.approx((105.0 - 98.50) / 105.0, abs=1e-12)


def test_covered_call_arithmetic_is_hand_checkable():
    m = covered_call_metrics(spot=100.0, strike=105.0, mid=2.0, dte=30, iv=0.30, r=0.04)

    assert m["static_return"] == pytest.approx(0.02, abs=1e-12)
    assert m["if_called_return"] == pytest.approx(0.07, abs=1e-12)
    assert m["upside_cap_pct"] == pytest.approx(0.05, abs=1e-12)
    assert m["downside_breakeven"] == pytest.approx(98.0, abs=1e-12)
    assert m["static_return_annualized"] == pytest.approx(0.02 * 365 / 30, abs=1e-12)


def test_covered_call_pop_is_profit_not_retention():
    """POP means profitable at expiry, everywhere in this module.

    Keeping the shares is a different and much likelier event; reporting it as
    POP would flatter every covered call on the board.
    """
    m = covered_call_metrics(spot=100.0, strike=105.0, mid=2.0, dte=30, iv=0.30, r=0.04)

    assert m["prob_called"] + m["prob_keep_shares"] == pytest.approx(1.0, abs=1e-12)

    # POP is measured at the downside breakeven (98), retention at the strike
    # (105). Which is larger depends on where the two sit relative to spot —
    # here retention wins — so the point is that they are distinct numbers
    # answering distinct questions, not that one dominates.
    assert m["pop"] != m["prob_keep_shares"]
    assert m["pop"] == pytest.approx(
        prob_above(100.0, m["downside_breakeven"], 30 / 365, 0.04, 0.30), abs=1e-12
    )
    assert m["prob_keep_shares"] == pytest.approx(
        prob_below(100.0, 105.0, 30 / 365, 0.04, 0.30), abs=1e-12
    )


def test_a_bigger_credit_widens_the_covered_call_cushion():
    thin = covered_call_metrics(spot=100.0, strike=105.0, mid=1.0, dte=30, iv=0.30, r=0.04)
    fat = covered_call_metrics(spot=100.0, strike=105.0, mid=4.0, dte=30, iv=0.30, r=0.04)

    assert fat["downside_breakeven"] < thin["downside_breakeven"]
    assert fat["pop"] > thin["pop"]


def test_leaps_splits_premium_into_intrinsic_and_time():
    m = leaps_metrics(spot=110.0, strike=100.0, mid=25.0, dte=400, iv=0.30, delta=0.75, r=0.04)

    assert m["intrinsic"] == pytest.approx(10.0, abs=1e-12)
    assert m["extrinsic"] == pytest.approx(15.0, abs=1e-12)
    assert m["breakeven"] == pytest.approx(125.0, abs=1e-12)
    assert m["breakeven_move_pct"] == pytest.approx(15.0 / 110.0, abs=1e-12)
    assert m["effective_leverage"] == pytest.approx(0.75 * 110.0 / 25.0, abs=1e-12)
    assert m["long_dated"] is True
    assert m["theta_drag_per_day_pct"] < 0


def test_spread_caps_the_loss_at_width_minus_credit():
    m = spread_metrics(
        spot=100.0, short_strike=95.0, long_strike=90.0,
        short_mid=2.0, long_mid=1.0, dte=30, iv=0.30, r=0.04,
    )

    assert m["width"] == pytest.approx(5.0, abs=1e-12)
    assert m["credit"] == pytest.approx(1.0, abs=1e-12)
    assert m["max_loss"] == pytest.approx(4.0, abs=1e-12)
    assert m["return_on_capital"] == pytest.approx(0.25, abs=1e-12)
    assert m["breakeven"] == pytest.approx(94.0, abs=1e-12)
    assert m["collateral"] == 500.0


# ── Realized volatility ─────────────────────────────────────────────────────


def test_constant_growth_has_zero_volatility():
    closes = [100.0 * (1.01**i) for i in range(60)]
    assert realized_vol(closes, window=20) == pytest.approx(0.0, abs=1e-12)


def test_realized_vol_recovers_a_known_daily_sigma():
    """A series whose log returns alternate +d/-d has sample sd exactly d."""
    d = 0.01
    closes = [100.0]
    for i in range(40):
        closes.append(closes[-1] * math.exp(d if i % 2 == 0 else -d))

    # Alternating +d/-d over an even window: mean 0, every deviation d, so the
    # sample sd is d * sqrt(n/(n-1)).
    n = 20
    expected = d * math.sqrt(n / (n - 1)) * math.sqrt(252)
    assert realized_vol(closes, window=n) == pytest.approx(expected, abs=1e-9)


def test_realized_vol_refuses_a_partial_window():
    assert realized_vol([100.0, 101.0, 102.0], window=20) is None
    assert realized_vol([], window=20) is None


def test_rolling_vol_yields_one_point_per_full_window():
    closes = [100.0 + i for i in range(40)]  # 39 returns
    assert len(rolling_realized_vol(closes, window=20)) == 39 - 20 + 1


def test_percentile_places_a_value_in_its_sample():
    sample = [1.0, 2.0, 3.0, 4.0]
    assert percentile_of(0.5, sample) == 0.0
    assert percentile_of(2.0, sample) == 50.0
    assert percentile_of(9.0, sample) == 100.0
    assert percentile_of(1.0, []) is None
