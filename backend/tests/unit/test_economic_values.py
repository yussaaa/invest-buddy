"""Unit tests for release figures — no network.

The two places a wrong number could hide are the vintage split (which period
did the release day add?) and the transforms (a percent change off by one
period reads as perfectly plausible). Both are pinned here with hand-checked
arithmetic.
"""

from __future__ import annotations

from datetime import date

import pytest

from app.services.economic_values import (
    Indicator,
    Vintage,
    _consensus_for,
    attach_values,
    figures,
    parse_forecast,
    parse_vintage,
)

DAY = "2026-10-14"
BEFORE = "2026-10-13"


def row(obs: str, value: str, start: str = BEFORE, end: str = DAY) -> dict:
    return {"date": obs, "value": value, "realtime_start": start, "realtime_end": end}


# ── which period the release day added ──────────────────────────────────────


def test_a_period_whose_only_row_starts_on_the_day_is_the_new_one():
    v = parse_vintage([row("2026-08-01", "100"), row("2026-09-01", "101", start=DAY)], DAY)
    assert v.new_on_day == "2026-09-01"
    assert v.values == [("2026-08-01", 100.0), ("2026-09-01", 101.0)]


def test_a_revision_is_not_mistaken_for_a_new_period():
    observations = [
        row("2026-08-01", "100", end=BEFORE),        # as first published
        row("2026-08-01", "100.4", start=DAY),       # revised on the day
    ]
    v = parse_vintage(observations, DAY)
    assert v.new_on_day is None
    assert v.values == [("2026-08-01", 100.4)]      # the revised value stands


def test_missing_values_are_skipped():
    v = parse_vintage([row("2026-08-01", "."), row("2026-09-01", "5", start=DAY)], DAY)
    assert v.values == [("2026-09-01", 5.0)]


# ── the transforms ──────────────────────────────────────────────────────────

MONTHS = [f"2025-{m:02d}-01" for m in range(1, 13)] + [f"2026-{m:02d}-01" for m in range(1, 10)]


def monthly(values: list[float], new: bool = True) -> Vintage:
    pairs = list(zip(MONTHS[-len(values):], values))
    return Vintage(values=pairs, new_on_day=pairs[-1][0] if new else None)


def test_percent_change_and_the_three_readings_before_it():
    ind = Indicator("CPI m/m", "X", "pct", "%")
    out = figures(ind, monthly([100.0, 101.0, 102.01, 103.0301, 104.0604]), released=True)
    assert out["period"] == "Sep 2026"
    assert out["actual"] == pytest.approx(1.0, abs=1e-3)
    assert out["previous"] == pytest.approx(1.0, abs=1e-3)
    assert out["average"] == pytest.approx(1.0, abs=1e-2)


def test_year_on_year_reaches_back_twelve_periods():
    ind = Indicator("CPI y/y", "X", "yoy", "%")
    values = [100.0] * 12 + [103.0]                  # flat a year, then +3%
    assert figures(ind, monthly(values), released=True)["actual"] == pytest.approx(3.0)


def test_a_change_is_scaled_into_the_quoted_unit():
    ind = Indicator("ADP", "X", "diff", "K", scale=1e-3, decimals=0)
    out = figures(ind, monthly([132_799_000.0, 132_885_000.0, 132_971_000.0]), released=True)
    assert out["actual"] == 86 and out["previous"] == 86


def test_before_the_release_the_latest_on_file_is_the_previous():
    ind = Indicator("Rate", "X", "level", "%")
    out = figures(ind, monthly([4.0, 4.1, 4.2, 4.3], new=False), released=False)
    assert out["actual"] is None
    assert out["previous"] == 4.3
    assert out["period"] == "Oct 2026"               # the month after September
    assert out["average"] == pytest.approx(4.2)


def test_a_past_release_fred_never_posted_names_no_period():
    ind = Indicator("Starts", "X", "level", "M", scale=1e-3)
    out = figures(ind, monthly([1369.0, 1288.0, 1221.0], new=False), released=True)
    assert out["actual"] is None and out["period"] is None
    assert out["previous"] == pytest.approx(1.221)


def test_no_average_where_it_means_nothing():
    ind = Indicator("Assets", "X", "level", "T", average_is_meaningful=False)
    assert figures(ind, monthly([1.0, 2.0, 3.0, 4.0]), released=True)["average"] is None


# ── consensus ───────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "raw, unit, expected",
    [("0.3%", "%", 0.3), ("65K", "K", 65.0), ("-107.9B", "B", -107.9), ("48.1", "", 48.1),
     ("7.15M", "K", None), ("", "%", None), ("n/a", "%", None)],
)
def test_forecast_strings_parse_only_in_the_indicators_own_unit(raw, unit, expected):
    assert parse_forecast(raw, unit) == expected


def test_same_day_consensus_wins_over_a_same_week_one():
    ind = Indicator("Sentiment", "X", "level", "", ff_titles=("Prelim UoM Consumer Sentiment",))
    rows = [
        {"title": "Prelim UoM Consumer Sentiment", "date": "2026-11-06", "forecast": "48.1"},
        {"title": "Prelim UoM Consumer Sentiment", "date": "2026-11-13", "forecast": "50.0"},
    ]
    assert _consensus_for(ind, "2026-11-13", rows) == 50.0
    assert _consensus_for(ind, "2026-11-12", rows) == 48.1       # nearest stand-in


def test_consensus_is_preferred_and_labelled(monkeypatch):
    import app.services.economic_values as ev

    vintage = monthly([4.0, 4.1, 4.2, 4.3, 4.4])     # enough for three prior changes
    monkeypatch.setattr(ev, "_fetch_vintage", lambda *a, **k: vintage)
    monkeypatch.setattr(
        ev, "fetch_consensus",
        lambda: [{"title": "Unemployment Rate", "date": "2026-11-06", "forecast": "4.2%"}],
    )
    events = [{"release": "Employment Situation", "date": "2026-11-06"}]
    attach_values(events, "key", date(2026, 11, 7), with_consensus=True)

    by_label = {r["label"]: r for r in events[0]["indicators"]}
    assert by_label["Unemployment rate"]["forecast"] == 4.2
    assert by_label["Unemployment rate"]["forecast_source"] == "consensus"
    # No consensus row for this one: the average stands in, and says so.
    assert by_label["Nonfarm payrolls"]["forecast_source"] == "average"


def test_a_failing_series_drops_its_rows_not_the_calendar(monkeypatch):
    import app.services.economic_values as ev

    def boom(*a, **k):
        raise RuntimeError("fred down")

    monkeypatch.setattr(ev, "_fetch_vintage", boom)
    events = [{"release": "Consumer Price Index", "date": "2026-11-10"}]
    attach_values(events, "key", date(2026, 11, 7), with_consensus=False)
    assert events[0]["indicators"] == []
