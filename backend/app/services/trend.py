"""Which way a moving average is going, and how far price has run from it.

Two questions the moving-average ladder leaves open. The ladder says price sits
4% above its 200-day average; it does not say whether 4% is a lot *for this
stock*, nor whether the average itself is rising or falling. Price above a
falling 200-day average is a materially different condition from price above a
rising one, and nothing in this app could tell them apart before this module.

Pure functions over a close series the caller already holds — no fetching, no
database, so every threshold is unit-testable without a network. The caller is
`services/technicals.py`, which has already read the adjusted 5-year series for
its indicators; computing here costs no additional I/O.

Adjusted series only, for the same reason `drawdown.py` says so: comparing
prices across dates on a raw series stops meaning anything once a split
intervenes.

**On the two output modes.** `trend_profile` returns a summary alone unless
asked for `points`. That is not an optimisation — it is a correctness boundary.
`agents/chat/graph.py:_grounding_evidence` hands the whole technicals dict to
`check_numeric_grounding`, which flattens every number in it into the set a
model's figures are checked against. The summary adds ~18 scalars to a set of
about 50. Two years of daily series would add twelve hundred, spanning the
stock's entire price range, and any price-like figure the model uttered would
then land within tolerance of *something*. The guard would not fail; it would
silently stop guarding. So the series is reachable only through `get_trend`,
which the chat path never calls.
"""

from __future__ import annotations

import math
from datetime import date
from typing import Literal, Sequence

# 20/50/200 rather than technicals.MA_WINDOWS. A 5-day average's slope is an
# order of magnitude noisier than a 200-day's; plotted on one axis it flattens
# the slower lines into the baseline and the chart stops saying anything.
TREND_WINDOWS: tuple[int, ...] = (20, 50, 200)

# The average price is measured against, and the window its dispersion is
# measured over — the same 200 for both, which is what makes the band crossing
# and the z-score threshold the same event. See `zscore_series`.
REFERENCE_WINDOW = 200
BAND_SIGMA = 1.5

# One trading month. At five sessions the 20-day slope is visibly noisy and the
# annualised headline flips label every other day; at twenty-one the 200-day
# line is smooth and the 20-day one still moves.
SLOPE_LOOKBACK = 21

# Matches TRADING_DAYS_52W in drawdown.py, so a year means the same number of
# sessions everywhere in the app.
TRADING_DAYS = 252

SlopeLabel = Literal[
    "strong_uptrend", "uptrend", "weak_uptrend", "flat",
    "weak_downtrend", "downtrend", "strong_downtrend",
]
ZLabel = Literal["extended_high", "elevated", "neutral", "depressed", "extended_low"]

# Annualised percent. 8% is roughly long-run equity drift, so below it a
# 200-day average is not really going anywhere; 20% is a genuinely strong trend.
SLOPE_STRONG = 20.0
SLOPE_TREND = 8.0
SLOPE_WEAK = 2.0

# Deliberately equal to BAND_SIGMA: "outside the band" and "extended" must name
# the same event, or the chart and the label disagree in front of the user.
Z_EXTENDED = BAND_SIGMA
Z_ELEVATED = 0.5


def classify_slope(annualised_percent: float) -> SlopeLabel:
    """Bucket an annualised slope. Boundaries belong to the stronger label."""
    if annualised_percent >= SLOPE_STRONG:
        return "strong_uptrend"
    if annualised_percent >= SLOPE_TREND:
        return "uptrend"
    if annualised_percent >= SLOPE_WEAK:
        return "weak_uptrend"
    if annualised_percent <= -SLOPE_STRONG:
        return "strong_downtrend"
    if annualised_percent <= -SLOPE_TREND:
        return "downtrend"
    if annualised_percent <= -SLOPE_WEAK:
        return "weak_downtrend"
    return "flat"


def classify_z(z: float) -> ZLabel:
    """Bucket a z-score. Boundaries belong to the more extreme label.

    The vocabulary is descriptive on purpose — `extended`, `elevated`,
    `depressed`. A price two deviations above its average is a fact about
    dispersion, not a verdict, and calling it `cheap` or `expensive` would make
    it one.
    """
    if z >= Z_EXTENDED:
        return "extended_high"
    if z >= Z_ELEVATED:
        return "elevated"
    if z <= -Z_EXTENDED:
        return "extended_low"
    if z <= -Z_ELEVATED:
        return "depressed"
    return "neutral"


def z_band(z: float) -> str:
    """The half-sigma bucket a score falls in, e.g. "-1.0 to -0.5"."""
    if z >= 3.0:
        return "> +3.0"
    if z <= -3.0:
        return "< -3.0"
    lower = math.floor(z * 2) / 2
    upper = lower + 0.5
    return f"{lower:+.1f} to {upper:+.1f}"


# ── rolling primitives ────────────────────────────────────────────────────────
#
# Written out rather than pulled from pandas: this module is pure by design and
# `drawdown.py` sets the precedent. The cost is a few hundred thousand float
# operations over a five-year series, which is microseconds.


def sma(values: Sequence[float], window: int) -> list[float | None]:
    """Simple moving average, `None` until the window fills."""
    if window <= 0:
        raise ValueError("window must be positive")

    out: list[float | None] = [None] * len(values)
    total = 0.0
    for i, value in enumerate(values):
        total += value
        if i >= window:
            total -= values[i - window]
        if i >= window - 1:
            out[i] = total / window
    return out


def rolling_std(values: Sequence[float], window: int) -> list[float | None]:
    """Sample standard deviation (ddof=1) over a trailing window.

    Two-pass — mean, then squared deviations — rather than the E[x²] - E[x]²
    shortcut. The shortcut cancels catastrophically when the mean is large
    relative to the spread, which is exactly the case here: a $400 stock whose
    200-day dispersion is $8. The extra pass costs nothing at this size.

    ddof=1 to match the sample convention `options_math.realized_vol` states.
    Bollinger conventionally uses ddof=0; at n=200 the two differ by 0.25%.
    """
    if window <= 1:
        raise ValueError("window must be greater than 1")

    out: list[float | None] = [None] * len(values)
    for i in range(window - 1, len(values)):
        chunk = values[i - window + 1 : i + 1]
        mean = sum(chunk) / window
        variance = sum((v - mean) ** 2 for v in chunk) / (window - 1)
        out[i] = math.sqrt(variance)
    return out


def slope_per_day(
    averages: Sequence[float | None], lookback: int = SLOPE_LOOKBACK
) -> list[float | None]:
    """Geometric daily growth of an average, in percent per day.

    Geometric rather than a straight difference over the period, because the
    result is compounded back up in `annualise`: 0.05% a day is 13.4% a year,
    not 12.6%, and over a five-year chart the gap is a whole label bucket.
    """
    out: list[float | None] = [None] * len(averages)
    for i in range(lookback, len(averages)):
        now, then = averages[i], averages[i - lookback]
        if now is None or then is None or then <= 0 or now <= 0:
            continue
        out[i] = ((now / then) ** (1 / lookback) - 1) * 100.0
    return out


def annualise(per_day_percent: float) -> float:
    """Compound a daily percent over a trading year."""
    return ((1 + per_day_percent / 100.0) ** TRADING_DAYS - 1) * 100.0


def zscore_series(
    closes: Sequence[float],
    averages: Sequence[float | None],
    deviations: Sequence[float | None],
) -> list[float | None]:
    """How many of its own deviations price sits from its average.

    Sigma is the dispersion of price over the *same* window the average is
    taken over, which buys an invariant worth having: price touching the
    +1.5σ band and the z-score reading exactly +1.5 are the same event, not two
    numbers that nearly agree. The band chart and the z chart are then two views
    of one quantity.
    """
    out: list[float | None] = [None] * len(closes)
    for i, close in enumerate(closes):
        mean, deviation = averages[i], deviations[i]
        # A halted or constant series has no dispersion. `None` says "cannot be
        # measured"; zero would claim price sits exactly at its average, and
        # dividing would claim infinity.
        if mean is None or deviation is None or deviation <= 0:
            continue
        out[i] = (close - mean) / deviation
    return out


# ── the profile ───────────────────────────────────────────────────────────────


def _round(value: float | None, digits: int) -> float | None:
    return None if value is None else round(value, digits)


def _distribution(scores: Sequence[float | None], width: float = 0.5) -> list[dict]:
    """How often price has sat in each half-sigma bucket, for a frequency view."""
    counts: dict[float, int] = {}
    for z in scores:
        if z is None:
            continue
        bucket = math.floor(z / width) * width
        counts[bucket] = counts.get(bucket, 0) + 1
    return [{"bucket": b, "count": counts[b]} for b in sorted(counts)]


def trend_profile(
    dates: Sequence[date],
    closes: Sequence[float],
    *,
    windows: Sequence[int] = TREND_WINDOWS,
    reference: int = REFERENCE_WINDOW,
    band_sigma: float = BAND_SIGMA,
    lookback: int = SLOPE_LOOKBACK,
    points: int | None = None,
) -> dict:
    """Slope, z-score and the sigma band for one close series.

    `points` decides how much comes back. Without it the result is the summary
    alone — the mode `get_technicals` uses, for the reason in the module
    docstring. With it, the charts' series are included as well, sliced to the
    last `points` sessions.

    Everything is computed over the *full* input and sliced afterwards, so the
    200-day average is already populated at the first point that ships. Slicing
    first would leave a null ramp at the left edge of every chart.
    """
    if len(dates) != len(closes):
        return {"error": f"{len(dates)} dates against {len(closes)} closes"}
    if not closes:
        return {"error": "no price history"}

    needed = reference + lookback
    if len(closes) < needed:
        return {"error": f"need {needed} sessions for a {reference}-day slope, have {len(closes)}"}

    averages = {w: sma(closes, w) for w in windows}
    if reference not in averages:
        averages[reference] = sma(closes, reference)

    deviations = rolling_std(closes, reference)
    scores = zscore_series(closes, averages[reference], deviations)
    slopes = {w: slope_per_day(averages[w], lookback) for w in windows}

    band_upper: list[float | None] = []
    band_lower: list[float | None] = []
    for mean, deviation in zip(averages[reference], deviations):
        if mean is None or deviation is None:
            band_upper.append(None)
            band_lower.append(None)
        else:
            band_upper.append(mean + band_sigma * deviation)
            band_lower.append(mean - band_sigma * deviation)

    price = closes[-1]
    mean_now = averages[reference][-1]
    z_now = scores[-1]

    summary: dict = {
        "price": _round(price, 2),
        "reference_window": reference,
        "sma": _round(mean_now, 2),
        "distance_percent": (
            _round((price - mean_now) / mean_now * 100.0, 2) if mean_now else None
        ),
        "sigma": _round(deviations[-1], 2),
        "z_score": _round(z_now, 3),
        "z_label": classify_z(z_now) if z_now is not None else None,
        "z_band": z_band(z_now) if z_now is not None else None,
        "band_sigma": band_sigma,
        "band_upper": _round(band_upper[-1], 2),
        "band_lower": _round(band_lower[-1], 2),
        "slopes": {},
        "sessions": len(closes),
    }

    for window in windows:
        per_day = slopes[window][-1]
        # The headline is the last point of the charted series, deliberately.
        # Computing it from a different lookback would let the number beside the
        # chart disagree with the line inside it.
        summary["slopes"][str(window)] = {
            "per_day_percent": _round(per_day, 4),
            "annualised_percent": _round(annualise(per_day), 1) if per_day is not None else None,
            "label": classify_slope(annualise(per_day)) if per_day is not None else None,
        }

    if points is None:
        return {"summary": summary}

    cut = max(0, len(closes) - points)

    def tail(values: Sequence[float | None], digits: int) -> list[float | None]:
        return [_round(v, digits) for v in values[cut:]]

    return {
        "summary": summary,
        # Column-oriented: a row per session would repeat nine key names five
        # hundred times, which is most of the payload in pure key text.
        "series": {
            "dates": [d.isoformat() for d in dates[cut:]],
            "price": tail(closes, 2),
            "sma": {str(w): tail(averages[w], 2) for w in windows},
            "band_upper": tail(band_upper, 2),
            "band_lower": tail(band_lower, 2),
            "slope_per_day": {str(w): tail(slopes[w], 4) for w in windows},
            "z_score": tail(scores, 3),
        },
        "distribution": _distribution(scores[cut:]),
    }
