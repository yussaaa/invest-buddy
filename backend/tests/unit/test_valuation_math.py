"""Unit tests for the DCF — pure, no network.

A discounted cash flow is mostly a terminal value resting on two numbers nobody
can observe, so the tests here are less about precision than about the model
refusing to produce confident nonsense: negative cash flow declined rather than
valued, a perpetuity refused when the spread collapses, and every clamp
reported rather than applied silently.
"""

from __future__ import annotations

import pytest

from app.services.valuation_math import (
    BETA_CEILING,
    BETA_FLOOR,
    DISCOUNT_CEILING,
    METHOD,
    Assumptions,
    cost_of_equity,
    dcf_value,
    fade,
    implied_growth,
    project,
    scenario_band,
    sensitivity_grid,
)

SHARES = 1_000_000.0
FCF = 100_000_000.0


def flat(growth: float = 0.05, rate: float = 0.09, terminal: float = 0.025, years: int = 10):
    return Assumptions(
        initial_growth=growth, terminal_growth=terminal, discount_rate=rate, years=years
    )


# ── the analytic anchor ───────────────────────────────────────────────────────


def test_a_zero_growth_perpetuity_equals_cash_flow_over_the_discount_rate():
    """If this is wrong, every other number here is coincidence."""
    a = Assumptions(initial_growth=0.0, terminal_growth=0.0, discount_rate=0.10, years=200)
    result = dcf_value(100.0, 1.0, a)
    assert result["value_per_share"] == pytest.approx(100.0 / 0.10, rel=1e-6)


def test_a_growing_perpetuity_matches_the_gordon_formula():
    g, r = 0.02, 0.08
    a = Assumptions(initial_growth=g, terminal_growth=g, discount_rate=r, years=300)
    result = dcf_value(100.0, 1.0, a)
    assert result["value_per_share"] == pytest.approx(100.0 * (1 + g) / (r - g), rel=1e-3)


# ── the fade ──────────────────────────────────────────────────────────────────


def test_growth_fades_linearly_from_the_initial_rate_to_the_terminal_one():
    path = fade(0.15, 0.025, 6)
    assert path[0] == pytest.approx(0.15)
    assert path[-1] == pytest.approx(0.025)
    steps = [round(b - a, 10) for a, b in zip(path, path[1:])]
    assert len(set(steps)) == 1  # linear, so every step is identical


def test_the_projection_compounds_the_faded_path():
    flows = project(100.0, Assumptions(0.10, 0.10, 0.09, 3))
    assert flows[0] == pytest.approx(110.0)
    assert flows[1] == pytest.approx(121.0)


# ── refusals ──────────────────────────────────────────────────────────────────


def test_negative_free_cash_flow_is_refused_rather_than_valued():
    """A negative fair value is the most embarrassing output this could have."""
    result = dcf_value(-50_000.0, SHARES, flat())
    assert "error" in result
    assert "value_per_share" not in result


def test_terminal_growth_at_or_above_the_discount_rate_is_refused():
    result = dcf_value(FCF, SHARES, flat(rate=0.05, terminal=0.05))
    assert "error" in result
    assert "perpetuity" in result["error"]


def test_a_spread_too_narrow_to_be_meaningful_is_also_refused():
    result = dcf_value(FCF, SHARES, flat(rate=0.030, terminal=0.025))
    assert "error" in result


def test_a_missing_share_count_is_refused():
    assert "error" in dcf_value(FCF, 0, flat())


# ── what the result carries ───────────────────────────────────────────────────


def test_the_terminal_value_dominates_a_ten_year_projection():
    """A reader who does not believe this has not understood the output."""
    result = dcf_value(FCF, SHARES, flat())
    assert result["terminal_value_share"] > 0.5


def test_the_result_names_the_method_it_used():
    # Levered cash flow at the cost of equity — not WACC-minus-net-debt.
    assert dcf_value(FCF, SHARES, flat())["method"] == METHOD


def test_the_exit_multiple_is_reported_so_the_terminal_value_can_be_checked():
    result = dcf_value(FCF, SHARES, flat())
    assert result["implied_exit_fcf_multiple"] > 0


def test_the_projection_has_one_flow_per_year():
    assert len(dcf_value(FCF, SHARES, flat(years=7))["projected_fcf"]) == 7


# ── cost of equity ────────────────────────────────────────────────────────────


def test_capm_uses_beta_against_the_risk_free_rate():
    result = cost_of_equity(1.0, risk_free=0.04, premium=0.05)
    assert result["rate"] == pytest.approx(0.09)


def test_an_implausible_beta_is_clamped_and_the_clamp_is_reported():
    low = cost_of_equity(0.1)
    assert low["beta"] == BETA_FLOOR
    assert low["raw_beta"] == 0.1
    assert low["beta_clamped"] is True


def test_a_very_high_beta_is_clamped_at_the_ceiling():
    high = cost_of_equity(6.0)
    assert high["beta"] == BETA_CEILING
    assert high["rate"] <= DISCOUNT_CEILING


def test_a_missing_beta_falls_back_to_the_market():
    assert cost_of_equity(None)["beta"] == 1.0


def test_an_ordinary_beta_is_not_reported_as_clamped():
    result = cost_of_equity(1.2)
    assert result["beta_clamped"] is False
    assert result["rate_clamped"] is False


# ── the scenario band ─────────────────────────────────────────────────────────


def test_bear_base_and_bull_come_out_in_order():
    band = scenario_band(FCF, SHARES, flat())
    values = [case["value_per_share"] for case in band]
    assert values == sorted(values)


def test_every_scenario_reports_the_assumptions_it_used():
    for case in scenario_band(FCF, SHARES, flat()):
        assert set(case["assumptions"]) == {
            "initial_growth", "terminal_growth", "discount_rate", "years"
        }


def test_one_broken_scenario_does_not_take_the_others_down():
    # A 4% base rate puts the bull case's spread under the guard.
    band = scenario_band(FCF, SHARES, flat(rate=0.04, terminal=0.025))
    assert any("error" in case for case in band)
    assert any("value_per_share" in case for case in band)


# ── the sensitivity grid ──────────────────────────────────────────────────────


def test_the_grid_shape_matches_its_axes():
    grid = sensitivity_grid(FCF, SHARES, flat())
    assert len(grid["values"]) == len(grid["discount_rates"])
    assert all(len(row) == len(grid["terminal_growths"]) for row in grid["values"])


def test_value_falls_as_the_discount_rate_rises():
    grid = sensitivity_grid(FCF, SHARES, flat())
    column = [row[2] for row in grid["values"]]
    assert column == sorted(column, reverse=True)


def test_value_rises_as_terminal_growth_rises():
    grid = sensitivity_grid(FCF, SHARES, flat())
    row = grid["values"][2]
    assert row == sorted(row)


# ── implied growth, the headline ──────────────────────────────────────────────


def test_implied_growth_round_trips_through_the_forward_model():
    """Feed the model's own output back as the price and recover the input."""
    a = flat(growth=0.11)
    priced = dcf_value(FCF, SHARES, a)["value_per_share"]
    recovered = implied_growth(priced, FCF, SHARES, a)
    assert recovered == pytest.approx(0.11, abs=1e-3)


def test_a_higher_price_implies_faster_growth():
    a = flat()
    low = implied_growth(600.0, FCF, SHARES, a)
    high = implied_growth(2500.0, FCF, SHARES, a)
    assert low is not None and high is not None
    assert high > low


def test_a_price_outside_the_solvable_range_yields_nothing_rather_than_a_bound():
    """Better to say nothing than to hand back the edge of the search bracket.

    Even a 50% annual decline leaves a terminal value, so there is a floor
    below which no growth rate explains the price — and a ceiling above it.
    """
    assert implied_growth(1e12, FCF, SHARES, flat()) is None   # above the bracket
    assert implied_growth(1.0, FCF, SHARES, flat()) is None    # below its floor


def test_implied_growth_refuses_the_inputs_a_value_would_refuse():
    assert implied_growth(100.0, -1.0, SHARES, flat()) is None
    assert implied_growth(100.0, FCF, 0, flat()) is None
    assert implied_growth(0.0, FCF, SHARES, flat()) is None
