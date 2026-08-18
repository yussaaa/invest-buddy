"""Unit tests for the trend service — pure, no DB, no network.

Two of these are load-bearing beyond their own correctness. The band/z-score
invariant is what lets the price chart and the z chart claim to show the same
thing, and the summary-only test pins the boundary that keeps the chat agent's
numeric grounding honest — see the module docstring in services/trend.py.
"""

from __future__ import annotations

import math
from datetime import date, timedelta

import pytest

from app.services.trend import (
    BAND_SIGMA,
    REFERENCE_WINDOW,
    SLOPE_LOOKBACK,
    TRADING_DAYS,
    annualise,
    classify_slope,
    classify_z,
    rolling_std,
    slope_per_day,
    sma,
    trend_profile,
    z_band,
    zscore_series,
)

START = date(2024, 1, 1)


def dates_for(n: int) -> list[date]:
    return [START + timedelta(days=i) for i in range(n)]


def ramp(n: int, daily: float = 0.0004, start: float = 100.0) -> list[float]:
    """A clean exponential ramp — a known slope with no noise."""
    return [start * (1 + daily) ** i for i in range(n)]


def flat(n: int, value: float = 100.0) -> list[float]:
    return [value] * n


# ── rolling primitives ────────────────────────────────────────────────────────


def test_the_average_is_none_until_its_window_fills():
    out = sma([1, 2, 3, 4, 5], 3)
    assert out[:2] == [None, None]
    assert out[2] == pytest.approx(2.0)
    assert out[4] == pytest.approx(4.0)


def test_the_deviation_is_a_sample_not_a_population_figure():
    # ddof=1 over [1,2,3]: mean 2, squared deviations 1+0+1, /2 -> 1.0
    assert rolling_std([1, 2, 3], 3)[2] == pytest.approx(1.0)


def test_a_constant_series_has_no_deviation():
    assert rolling_std(flat(10), 5)[9] == pytest.approx(0.0)


def test_slope_is_none_until_the_lookback_fills():
    averages = sma(ramp(60), 20)
    out = slope_per_day(averages, lookback=SLOPE_LOOKBACK)
    assert out[SLOPE_LOOKBACK - 1] is None
    assert out[-1] is not None


# ── the slope definition ──────────────────────────────────────────────────────


def test_annualising_compounds_rather_than_multiplying():
    """0.05% a day is 13.4% a year, not 12.6% — a whole label bucket apart."""
    assert annualise(0.05) == pytest.approx(((1.0005) ** TRADING_DAYS - 1) * 100, rel=1e-9)
    assert annualise(0.05) > 0.05 * TRADING_DAYS


def test_a_known_ramp_recovers_its_own_growth_rate():
    closes = ramp(400, daily=0.0004)
    result = trend_profile(dates_for(400), closes)
    assert result["summary"]["slopes"]["200"]["per_day_percent"] == pytest.approx(0.04, abs=1e-3)


def test_the_charted_slope_and_the_headline_are_the_same_quantity():
    """A number beside a chart that disagrees with the line inside it is a bug."""
    closes = ramp(400)
    result = trend_profile(dates_for(400), closes, points=50)
    charted = result["series"]["slope_per_day"]["200"][-1]
    headline = result["summary"]["slopes"]["200"]["per_day_percent"]
    assert charted == headline


def test_slope_labels_take_the_stronger_bucket_at_a_boundary():
    assert classify_slope(20.0) == "strong_uptrend"
    assert classify_slope(19.9) == "uptrend"
    assert classify_slope(8.0) == "uptrend"
    assert classify_slope(2.0) == "weak_uptrend"
    assert classify_slope(1.9) == "flat"
    assert classify_slope(-2.0) == "weak_downtrend"
    assert classify_slope(-20.0) == "strong_downtrend"


def test_a_falling_average_is_labelled_as_falling():
    closes = ramp(400, daily=-0.0004)
    result = trend_profile(dates_for(400), closes)
    assert result["summary"]["slopes"]["200"]["annualised_percent"] < 0
    assert "downtrend" in result["summary"]["slopes"]["200"]["label"]


# ── the z-score / band invariant ──────────────────────────────────────────────


def test_price_at_the_upper_band_scores_exactly_the_band_sigma():
    """The band crossing and the z threshold must be one event, not two.

    Checked on the unrounded quantities, because that is where the invariant
    lives — the summary rounds to two decimals for display, which is enough to
    move a reconstructed score by ~0.002.
    """
    closes = ramp(400)
    averages = sma(closes, REFERENCE_WINDOW)
    deviations = rolling_std(closes, REFERENCE_WINDOW)

    mean, deviation = averages[-1], deviations[-1]
    at_upper = mean + BAND_SIGMA * deviation
    at_lower = mean - BAND_SIGMA * deviation

    assert zscore_series([at_upper], [mean], [deviation])[0] == pytest.approx(BAND_SIGMA)
    assert zscore_series([at_lower], [mean], [deviation])[0] == pytest.approx(-BAND_SIGMA)


def test_the_rounded_summary_keeps_the_band_invariant_within_display_precision():
    """What the user sees must still add up, even after rounding for display."""
    result = trend_profile(dates_for(400), ramp(400), points=10)
    summary = result["summary"]
    reconstructed = (summary["band_upper"] - summary["sma"]) / summary["sigma"]
    assert reconstructed == pytest.approx(BAND_SIGMA, abs=0.01)


def test_a_constant_series_yields_no_score_rather_than_infinity():
    closes = flat(400)
    averages = sma(closes, REFERENCE_WINDOW)
    deviations = rolling_std(closes, REFERENCE_WINDOW)
    scores = zscore_series(closes, averages, deviations)
    assert scores[-1] is None
    assert not any(s is not None and math.isinf(s) for s in scores)


def test_z_labels_take_the_more_extreme_bucket_at_a_boundary():
    assert classify_z(1.5) == "extended_high"
    assert classify_z(1.49) == "elevated"
    assert classify_z(0.5) == "elevated"
    assert classify_z(0.49) == "neutral"
    assert classify_z(-0.5) == "depressed"
    assert classify_z(-1.5) == "extended_low"


def test_the_band_label_brackets_the_score():
    assert z_band(-0.66) == "-1.0 to -0.5"
    assert z_band(0.0) == "+0.0 to +0.5"
    assert z_band(1.75) == "+1.5 to +2.0"


def test_extreme_scores_are_clipped_rather_than_bucketed_forever():
    assert z_band(4.2) == "> +3.0"
    assert z_band(-9.9) == "< -3.0"


# ── output modes and shape ────────────────────────────────────────────────────


def test_summary_only_mode_omits_the_series_entirely():
    """The boundary that keeps the chat agent's numeric grounding meaningful.

    Every number in the technicals dict widens the set a model's figures are
    checked against. The summary is a handful; the series is twelve hundred
    spanning the whole price range, which would turn the check into a no-op.
    """
    result = trend_profile(dates_for(400), ramp(400))
    assert "series" not in result
    assert "distribution" not in result
    assert "summary" in result


def test_series_are_sliced_not_truncated_so_the_average_is_already_warm():
    result = trend_profile(dates_for(500), ramp(500), points=50)
    series = result["series"]
    assert len(series["dates"]) == 50
    # The whole point: no null ramp at the left edge of the chart.
    assert series["sma"]["200"][0] is not None
    assert series["z_score"][0] is not None


def test_every_series_has_the_same_length_as_its_dates():
    result = trend_profile(dates_for(500), ramp(500), points=120)
    series = result["series"]
    n = len(series["dates"])
    assert len(series["price"]) == n
    assert len(series["band_upper"]) == n
    assert len(series["band_lower"]) == n
    assert len(series["z_score"]) == n
    for window in ("20", "50", "200"):
        assert len(series["sma"][window]) == n
        assert len(series["slope_per_day"][window]) == n


def test_asking_for_more_points_than_exist_returns_what_there_is():
    result = trend_profile(dates_for(300), ramp(300), points=10_000)
    assert len(result["series"]["dates"]) == 300


def test_the_last_point_is_the_latest_close():
    closes = ramp(400)
    result = trend_profile(dates_for(400), closes, points=5)
    assert result["series"]["price"][-1] == pytest.approx(round(closes[-1], 2))
    assert result["summary"]["price"] == pytest.approx(round(closes[-1], 2))


def test_the_distribution_buckets_every_scored_session():
    result = trend_profile(dates_for(500), ramp(500), points=200)
    total = sum(b["count"] for b in result["distribution"])
    scored = sum(1 for z in result["series"]["z_score"] if z is not None)
    assert total == scored


# ── degradation ───────────────────────────────────────────────────────────────


def test_an_empty_series_reports_an_error_rather_than_raising():
    assert "error" in trend_profile([], [])


def test_a_series_shorter_than_the_slope_lookback_says_how_short():
    result = trend_profile(dates_for(50), ramp(50))
    assert "error" in result
    assert "have 50" in result["error"]


def test_mismatched_dates_and_closes_are_refused():
    assert "error" in trend_profile(dates_for(400), ramp(399))
