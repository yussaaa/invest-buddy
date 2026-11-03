"""Unit tests for the valuation service's coercion layer — no network.

The arithmetic is covered in test_valuation_math.py. What is tested here is the
half where a silent, severe bug lives: picking the wrong free-cash-flow number.
Nothing raises when that happens — the model just returns a confident figure
that is four times too low.
"""

from __future__ import annotations

import pandas as pd
import pytest

from app.services.valuation import _dispersion, _fcf_history, _median, _safe


class FakeTicker:
    """Stands in for yf.Ticker — only `.cashflow` is read by _fcf_history."""

    def __init__(self, frame: pd.DataFrame | None):
        self.cashflow = frame


def frame(rows: dict[str, list[float]]) -> pd.DataFrame:
    periods = pd.to_datetime(["2025-09-30", "2024-09-30", "2023-09-30", "2022-09-30"])
    return pd.DataFrame(rows, index=periods).T


# ── the bug this module exists to avoid ───────────────────────────────────────


def test_free_cash_flow_comes_from_the_statement_not_the_summary_field():
    """MSFT-shaped: info says $16.5bn, the statement says $67bn.

    Reading the summary field turns a ~1.9% FCF yield into 0.46% and a
    valuation to match, with nothing anywhere raising.
    """
    ticker = FakeTicker(frame({"Free Cash Flow": [67.0e9, 71.6e9, 74.1e9, 59.5e9]}))
    history = _fcf_history(ticker)
    assert history[0] == pytest.approx(67.0e9)
    assert history[0] != pytest.approx(16.5e9)


def test_operating_cash_flow_less_capex_is_the_fallback_when_fcf_is_absent():
    # Capex arrives signed negative, so it adds rather than subtracts.
    ticker = FakeTicker(frame({
        "Operating Cash Flow": [100.0e9, 90.0e9, 80.0e9, 70.0e9],
        "Capital Expenditure": [-20.0e9, -18.0e9, -16.0e9, -14.0e9],
    }))
    assert _fcf_history(ticker)[0] == pytest.approx(80.0e9)


def test_a_missing_statement_yields_no_history_rather_than_raising():
    assert _fcf_history(FakeTicker(None)) == []
    assert _fcf_history(FakeTicker(pd.DataFrame())) == []


def test_a_statement_without_any_cash_flow_rows_yields_no_history():
    assert _fcf_history(FakeTicker(frame({"Net Income": [1.0, 2.0, 3.0, 4.0]}))) == []


def test_periods_reported_as_nan_are_dropped_not_carried_as_zero():
    # Real statements do come back with a NaN period; treating it as zero would
    # drag a median down and a growth rate with it.
    ticker = FakeTicker(frame({"Free Cash Flow": [99.0e9, 109.0e9, 100.0e9, float("nan")]}))
    history = _fcf_history(ticker)
    assert len(history) == 3
    assert not any(v != v for v in history)


# ── base-year choice ──────────────────────────────────────────────────────────


def test_the_median_is_the_middle_year_not_the_latest():
    assert _median([5.0, 5.0, 10.0, 10.0]) == pytest.approx(7.5)
    assert _median([1.0, 2.0, 3.0]) == pytest.approx(2.0)
    assert _median([]) is None


def test_dispersion_reports_how_much_the_base_year_is_a_choice():
    """Stable names land near zero; a growth ramp lands far above 1."""
    stable = _dispersion([99.0, 109.0, 100.0, 111.0])
    ramping = _dispersion([97.0, 61.0, 27.0, 4.0])
    assert stable < 0.3
    assert ramping > 1.0


def test_dispersion_needs_more_than_one_year_to_mean_anything():
    assert _dispersion([100.0]) is None
    assert _dispersion([]) is None


# ── coercion ──────────────────────────────────────────────────────────────────


def test_absent_and_unusable_values_coerce_to_none():
    assert _safe(None) is None
    assert _safe(float("nan")) is None
    assert _safe("not a number") is None


def test_numeric_strings_are_accepted_because_the_provider_sends_them():
    assert _safe("42.5") == pytest.approx(42.5)
    assert _safe(7) == pytest.approx(7.0)
