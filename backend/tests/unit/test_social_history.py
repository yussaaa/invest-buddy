"""Mention history statistics — pure, so no database here."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.services.social.history import MIN_HISTORY_DAYS, attention_stats, daily_series

UTC = timezone.utc


def test_the_last_sample_of_each_new_york_day_stands_for_it():
    rows = [
        (datetime(2026, 11, 2, 14, tzinfo=UTC), 10, 5),
        (datetime(2026, 11, 2, 20, tzinfo=UTC), 30, 3),     # 3pm ET, later same day
        (datetime(2026, 11, 3, 15, tzinfo=UTC), 50, 2),
    ]
    assert [(d.isoformat(), m) for d, m, _ in daily_series(rows)] == [
        ("2026-11-02", 30), ("2026-11-03", 50)]


def test_after_8pm_eastern_the_sample_belongs_to_the_new_york_date():
    """01:30 UTC on the 3rd is 8:30pm on the 2nd in New York."""
    rows = [(datetime(2026, 11, 2, 18, tzinfo=UTC), 10, None),
            (datetime(2026, 11, 3, 1, 30, tzinfo=UTC), 99, None)]
    series = daily_series(rows)
    assert len(series) == 1 and series[0][0].isoformat() == "2026-11-02" and series[0][1] == 99


def test_rows_out_of_order_still_pick_the_latest():
    late = datetime(2026, 11, 2, 21, tzinfo=UTC)
    rows = [(late, 7, None), (late - timedelta(hours=5), 1, None)]
    assert daily_series(rows)[0][1] == 7


def test_robust_z_against_the_median():
    prior = [10, 12, 11, 9, 10, 13, 10]           # median 10, MAD 1
    stats = attention_stats(prior, 20)
    assert stats.history_days == 7 and stats.median == 10
    assert stats.z == round(10 / 1.4826, 2)


def test_one_meme_day_does_not_hide_the_next():
    prior = [10, 11, 9, 10, 400, 10, 12, 11]
    assert attention_stats(prior, 60).z > 2


def test_too_little_history_gives_no_z():
    stats = attention_stats([10] * (MIN_HISTORY_DAYS - 1), 50)
    assert stats.z is None and stats.history_days == MIN_HISTORY_DAYS - 1


def test_a_flat_history_has_no_spread_to_measure():
    stats = attention_stats([10] * 10, 50)
    assert stats.z is None and stats.median == 10


def test_no_current_reading_gives_no_z():
    assert attention_stats(list(range(10)), None).z is None
