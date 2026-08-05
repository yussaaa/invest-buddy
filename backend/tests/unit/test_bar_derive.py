"""Unit tests for the pure bar transforms — no DB, no network."""

from __future__ import annotations

from datetime import date

from app.services.bar_derive import BarRow, adjust, bucket_bars, to_candles


def bar(day: date, close: float, **kw) -> BarRow:
    return BarRow(
        bar_date=day,
        open=kw.get("open", close - 1),
        high=kw.get("high", close + 2),
        low=kw.get("low", close - 3),
        close=close,
        adj_close=kw.get("adj_close", close),
        volume=kw.get("volume", 1000),
    )


# Mon 2026-07-06 .. Fri 2026-07-10, then Mon 2026-07-13 .. Tue 2026-07-14
WEEK_ONE = [bar(date(2026, 7, d), 100.0 + d) for d in (6, 7, 8, 9, 10)]
WEEK_TWO = [bar(date(2026, 7, d), 200.0 + d) for d in (13, 14)]


def test_daily_passes_through_unchanged():
    assert bucket_bars(WEEK_ONE, "1d") == WEEK_ONE


def test_weekly_anchors_on_monday():
    buckets = bucket_bars(WEEK_ONE + WEEK_TWO, "1wk")

    assert [b.bar_date for b in buckets] == [date(2026, 7, 6), date(2026, 7, 13)]


def test_weekly_aggregates_ohlcv_correctly():
    week = bucket_bars(WEEK_ONE, "1wk")[0]

    assert week.open == WEEK_ONE[0].open, "open comes from the first session"
    assert week.close == WEEK_ONE[-1].close, "close comes from the last session"
    assert week.high == max(b.high for b in WEEK_ONE)
    assert week.low == min(b.low for b in WEEK_ONE)
    assert week.volume == sum(b.volume for b in WEEK_ONE)


def test_trailing_partial_bucket_is_kept():
    """A half-finished week is still a bar — the provider emits it too."""
    buckets = bucket_bars(WEEK_ONE + WEEK_TWO, "1wk")

    assert len(buckets) == 2
    assert buckets[1].close == WEEK_TWO[-1].close


def test_monthly_anchors_on_the_first():
    rows = [bar(date(2026, 6, 30), 50.0), *WEEK_ONE]

    buckets = bucket_bars(rows, "1mo")

    assert [b.bar_date for b in buckets] == [date(2026, 6, 1), date(2026, 7, 1)]


def test_bucketing_sorts_unordered_input():
    buckets = bucket_bars(list(reversed(WEEK_ONE)), "1wk")

    assert len(buckets) == 1
    assert buckets[0].open == WEEK_ONE[0].open


def test_adjust_scales_every_leg():
    row = BarRow(date(2026, 7, 6), open=100.0, high=110.0, low=90.0,
                 close=100.0, adj_close=50.0, volume=10)

    adjusted = adjust([row])[0]

    assert adjusted.close == 50.0
    assert adjusted.open == 50.0
    assert adjusted.high == 55.0
    assert adjusted.low == 45.0
    assert adjusted.volume == 10, "volume is not price-adjusted"


def test_adjust_is_a_noop_when_unadjusted():
    row = bar(date(2026, 7, 6), 100.0, adj_close=100.0)

    assert adjust([row])[0] == row


def test_adjust_tolerates_missing_adj_close():
    row = BarRow(date(2026, 7, 6), 1.0, 2.0, 0.5, 1.5, adj_close=None)

    assert adjust([row])[0] == row


def test_to_candles_matches_the_frontend_contract():
    candles = to_candles([bar(date(2026, 7, 6), 100.0)])

    assert candles[0]["time"] == "2026-07-06", "dates are calendar strings, not epochs"
    assert set(candles[0]) == {"time", "open", "high", "low", "close", "volume"}


def test_to_candles_emits_zero_for_missing_volume():
    row = BarRow(date(2026, 7, 6), 1.0, 2.0, 0.5, 1.5, volume=None)

    assert to_candles([row])[0]["volume"] == 0
