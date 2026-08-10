"""Unit tests for the drawdown profile — pure functions, no DB, no network."""

from __future__ import annotations

from datetime import date, timedelta

from app.services.bar_derive import BarRow
from app.services.drawdown import classify, drawdown_profile


def bars(closes: list[float], highs: list[float] | None = None,
         lows: list[float] | None = None) -> list[BarRow]:
    start = date(2026, 1, 5)
    highs = highs or closes
    lows = lows or closes
    return [
        BarRow(
            bar_date=start + timedelta(days=i),
            open=c, high=h, low=lo, close=c, adj_close=c, volume=1000,
        )
        for i, (c, h, lo) in enumerate(zip(closes, highs, lows))
    ]


# ── thresholds ────────────────────────────────────────────────────────────────
# The conventional definitions are "at or beyond", so the boundaries belong to
# the more severe bucket. An off-by-one here is invisible by eye.

def test_threshold_boundaries_are_inclusive():
    assert classify(-20.0) == "bear_market"
    assert classify(-19.99) == "correction"
    assert classify(-10.0) == "correction"
    assert classify(-9.99) == "pullback"
    assert classify(-2.0) == "pullback"
    assert classify(-1.99) == "at_high"
    assert classify(0.0) == "at_high"


def test_deeper_than_bear_is_still_bear():
    assert classify(-80.0) == "bear_market"


# ── profile ───────────────────────────────────────────────────────────────────

def test_at_the_high_reports_zero_distance():
    result = drawdown_profile(bars([90, 95, 100]), current=100)

    assert result["from_high_percent"] == 0.0
    assert result["status"] == "at_high"
    assert result["range_position"] == 100.0


def test_distance_from_high_is_negative_and_classified():
    # High 100, now 85 -> -15%, a correction
    result = drawdown_profile(bars([100, 90, 85]), current=85)

    assert result["from_high_percent"] == -15.0
    assert result["status"] == "correction"
    assert result["high_52w"] == 100
    assert result["low_52w"] == 85


def test_range_position_places_price_between_low_and_high():
    # Low 50, high 100, price 75 -> halfway
    result = drawdown_profile(bars([100, 50, 75]), current=75)

    assert result["range_position"] == 50.0
    assert result["from_low_percent"] == 50.0


def test_uses_intraday_extremes_not_closes():
    """A 52-week high means the high print, not the highest close."""
    result = drawdown_profile(
        bars(closes=[100, 100], highs=[100, 120], lows=[90, 100]),
        current=100,
    )

    assert result["high_52w"] == 120, "should take the intraday high"
    assert result["low_52w"] == 90, "should take the intraday low"


def test_live_price_above_the_window_becomes_the_new_high():
    """The store holds settled bars only, so a new high today is real data."""
    result = drawdown_profile(bars([100, 100]), current=130)

    assert result["high_52w"] == 130
    assert result["high_52w_date"] == "today"
    assert result["from_high_percent"] == 0.0
    assert result["status"] == "at_high"


def test_window_limits_the_lookback():
    # An old spike outside the window must not count as the 52-week high.
    result = drawdown_profile(bars([500, 100, 100, 100]), current=100, window=3)

    assert result["high_52w"] == 100
    assert result["sessions"] == 3


def test_worst_drawdown_finds_peak_and_trough():
    result = drawdown_profile(bars([100, 60, 80, 90]), current=90)
    worst = result["worst"]

    assert worst["depth_percent"] == -40.0
    assert worst["trough_date"] == "2026-01-06"
    assert worst["recovered"] is False


def test_worst_drawdown_marks_recovery():
    result = drawdown_profile(bars([100, 50, 100, 110]), current=110)
    worst = result["worst"]

    assert worst["depth_percent"] == -50.0
    assert worst["recovered"] is True
    assert worst["recovered_date"] is not None


def test_monotonic_rise_has_no_drawdown():
    assert drawdown_profile(bars([10, 20, 30]), current=30)["worst"] is None


# ── degenerate input ──────────────────────────────────────────────────────────

def test_empty_series_reports_an_error_rather_than_dividing():
    assert "error" in drawdown_profile([], current=100)


def test_flat_series_does_not_divide_by_zero():
    result = drawdown_profile(bars([50, 50, 50]), current=50)

    assert result["range_position"] == 100.0
    assert result["from_high_percent"] == 0.0


def test_missing_current_falls_back_to_the_last_close():
    result = drawdown_profile(bars([100, 80]), current=None)

    assert result["current"] == 80
    assert result["from_high_percent"] == -20.0
    assert result["status"] == "bear_market"


def test_bars_without_high_low_fall_back_to_close():
    rows = [
        BarRow(bar_date=date(2026, 1, 5), open=None, high=None, low=None, close=100),
        BarRow(bar_date=date(2026, 1, 6), open=None, high=None, low=None, close=80),
    ]
    result = drawdown_profile(rows, current=80)

    assert result["high_52w"] == 100
    assert result["low_52w"] == 80
