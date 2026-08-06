"""Where a price stands against its own recent history.

Answers the question the chart leaves implicit: how far below its 52-week high
is this, and is that a pullback, a correction, or a bear market?

Pure functions over bars the caller already holds — no fetching, no database,
so the thresholds are unit-testable without a network or a DB. The caller is
`services/technicals.py`, which has already read the adjusted series for its
indicators.

Everything here assumes the **adjusted** series. Comparing prices across dates
on a raw series is meaningless once a split intervenes: AAPL's 2014 7-for-1 and
2020 4-for-1 mean today's raw price can never approach the raw pre-split print,
so a raw calculation would report a permanent ~28x drawdown.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Sequence

from app.services.bar_derive import BarRow

# One trading year. Matches LONG_VOL_WINDOW in services/vol_provider.py, so
# "52 weeks" means the same number of sessions everywhere in the app.
TRADING_DAYS_52W = 252

# Conventional thresholds, measured from the high. Both are "at or beyond", so
# exactly -10.0 is a correction and exactly -20.0 is a bear market.
CORRECTION_THRESHOLD = -10.0
BEAR_THRESHOLD = -20.0

# Inside this much of the high, "off its high" is noise rather than a move.
AT_HIGH_THRESHOLD = -2.0

Status = Literal["at_high", "pullback", "correction", "bear_market"]


@dataclass(frozen=True)
class Extreme:
    value: float
    date: str


def classify(from_high_percent: float) -> Status:
    """Bucket a distance-from-high into the conventional labels."""
    if from_high_percent <= BEAR_THRESHOLD:
        return "bear_market"
    if from_high_percent <= CORRECTION_THRESHOLD:
        return "correction"
    if from_high_percent <= AT_HIGH_THRESHOLD:
        return "pullback"
    return "at_high"


def _extremes(rows: Sequence[BarRow]) -> tuple[Extreme | None, Extreme | None]:
    """Highest high and lowest low, with the dates they printed.

    Uses the bars' high/low rather than closes: "52-week high" conventionally
    means the intraday extreme, and that is also what Yahoo's own field reports,
    which keeps the two comparable.
    """
    high: Extreme | None = None
    low: Extreme | None = None

    for row in rows:
        top = row.high if row.high is not None else row.close
        bottom = row.low if row.low is not None else row.close
        if high is None or top > high.value:
            high = Extreme(top, row.bar_date.isoformat())
        if low is None or bottom < low.value:
            low = Extreme(bottom, row.bar_date.isoformat())

    return high, low


def _worst_drawdown(rows: Sequence[BarRow]) -> dict | None:
    """The deepest peak-to-trough fall in the window, and whether it recovered.

    Same cummax approach as `compute_max_drawdown` in tools/calculation, but on
    closes the caller already has and returning structured fields rather than
    the prose and sentinel strings that function builds for LLM consumption.
    """
    if len(rows) < 2:
        return None

    peak_value = rows[0].close
    peak_date = rows[0].bar_date
    worst_depth = 0.0
    worst_peak = rows[0].bar_date
    worst_trough = rows[0].bar_date

    for row in rows:
        if row.close > peak_value:
            peak_value = row.close
            peak_date = row.bar_date
        if not peak_value:
            continue
        depth = (row.close - peak_value) / peak_value * 100.0
        if depth < worst_depth:
            worst_depth = depth
            worst_peak = peak_date
            worst_trough = row.bar_date

    if worst_depth == 0.0:
        return None  # only ever made new highs in this window

    # Recovered once a later close regains the pre-drawdown peak.
    peak_price = next((r.close for r in rows if r.bar_date == worst_peak), None)
    recovered_on = None
    if peak_price:
        after_trough = [r for r in rows if r.bar_date > worst_trough]
        recovery = next((r for r in after_trough if r.close >= peak_price), None)
        recovered_on = recovery.bar_date.isoformat() if recovery else None

    return {
        "depth_percent": round(worst_depth, 2),
        "peak_date": worst_peak.isoformat(),
        "trough_date": worst_trough.isoformat(),
        "recovered": recovered_on is not None,
        "recovered_date": recovered_on,
    }


def drawdown_profile(
    rows: Sequence[BarRow],
    current: float | None = None,
    window: int = TRADING_DAYS_52W,
) -> dict:
    """Distance from the 52-week high, plus the worst fall in the full series.

    `rows` is the adjusted daily series, oldest first. `current` is the live
    price — passed separately because the store holds settled sessions only, so
    without it the panel would answer as of yesterday's close while the header
    beside it shows today's.
    """
    if not rows:
        return {"error": "no price history"}

    recent = list(rows[-window:])
    high, low = _extremes(recent)
    if high is None or low is None or not high.value:
        return {"error": "no usable price history"}

    price = current if current is not None else recent[-1].close

    # The live price can exceed a window built from settled bars — that is a
    # new high being made right now, not bad data.
    if price > high.value:
        high = Extreme(price, "today")
    if price < low.value:
        low = Extreme(price, "today")

    from_high = (price - high.value) / high.value * 100.0
    from_low = ((price - low.value) / low.value * 100.0) if low.value else 0.0

    span = high.value - low.value
    range_position = ((price - low.value) / span * 100.0) if span > 0 else 100.0

    return {
        "current": round(price, 4),
        "high_52w": round(high.value, 4),
        "high_52w_date": high.date,
        "low_52w": round(low.value, 4),
        "low_52w_date": low.date,
        "from_high_percent": round(from_high, 2),
        "from_low_percent": round(from_low, 2),
        "range_position": round(max(0.0, min(100.0, range_position)), 2),
        "status": classify(from_high),
        "sessions": len(recent),
        "worst": _worst_drawdown(rows),
    }
