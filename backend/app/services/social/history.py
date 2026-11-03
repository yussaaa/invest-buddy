"""Mention history: what "a lot of attention" means for this ticker.

ApeWisdom reports a count now and 24 hours ago, which says whether today is
busier than yesterday but not whether it is unusual. That needs a baseline,
and the API keeps none, so an hourly job (services/warmup.py) snapshots the
board into Postgres and this module turns those snapshots into a z-score.

The statistics are pure and live here; the reads and writes are in
`db/repositories_social.py`.

Why one sample per day: each count is a *rolling* 24-hour window, so hourly
samples overlap by 23 hours and are nearly the same number. Treating them as
independent would shrink the spread and make every afternoon look like a
spike. The last snapshot of each New York trading date stands for that day —
New York, not UTC, because after 8pm ET the UTC date has already rolled over.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass
from datetime import date, datetime
from typing import Iterable, Optional

from app.db.repositories_market import MARKET_TZ

# Below this many prior days a z-score is noise; the rules fall back to the
# 24h comparison ApeWisdom provides directly.
MIN_HISTORY_DAYS = 7

# Scales MAD to be comparable with a standard deviation for normal data.
MAD_SCALE = 1.4826


@dataclass(frozen=True)
class AttentionStats:
    z: Optional[float]
    median: Optional[float]
    history_days: int


def daily_series(
    snapshots: Iterable[tuple[datetime, int, Optional[int]]],
) -> list[tuple[date, int, Optional[int]]]:
    """(captured_at, mentions, rank) rows → the last reading per New York date."""
    by_day: dict[date, tuple[datetime, int, Optional[int]]] = {}
    for captured_at, mentions, rank in snapshots:
        day = captured_at.astimezone(MARKET_TZ).date()
        current = by_day.get(day)
        if current is None or captured_at >= current[0]:
            by_day[day] = (captured_at, mentions, rank)
    return [(day, row[1], row[2]) for day, row in sorted(by_day.items())]


def attention_stats(prior_days: list[int], current: Optional[int]) -> AttentionStats:
    """Robust z-score of `current` against the prior daily counts.

    Median and MAD rather than mean and standard deviation: mention counts
    are spiky, and one meme day would otherwise inflate the spread enough to
    hide the next one.
    """
    n = len(prior_days)
    if current is None or n < MIN_HISTORY_DAYS:
        return AttentionStats(z=None, median=None, history_days=n)

    median = statistics.median(prior_days)
    mad = statistics.median(abs(x - median) for x in prior_days)
    if mad == 0:
        # A flat history has no spread to measure against. Reporting z=None is
        # honest; inventing a tiny denominator would call every uptick a spike.
        return AttentionStats(z=None, median=float(median), history_days=n)

    z = (current - median) / (MAD_SCALE * mad)
    return AttentionStats(z=round(z, 2), median=float(median), history_days=n)
