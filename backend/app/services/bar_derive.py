"""Pure transforms over stored daily bars.

No I/O, no database, no pandas — everything here is a function of its
arguments, so the weekly/monthly bucketing and the raw→adjusted conversion can
be tested in the unit suite without a database or a network.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date, timedelta
from typing import Literal, Sequence

Frequency = Literal["1d", "1wk", "1mo"]


@dataclass(frozen=True)
class BarRow:
    bar_date: date
    open: float | None
    high: float | None
    low: float | None
    close: float
    adj_close: float | None = None
    volume: int | None = None


def _bucket_start(day: date, freq: Frequency) -> date:
    if freq == "1wk":
        # yfinance anchors weekly bars on Monday.
        return day - timedelta(days=day.weekday())
    if freq == "1mo":
        return day.replace(day=1)
    return day


def bucket_bars(rows: Sequence[BarRow], freq: Frequency) -> list[BarRow]:
    """Aggregate daily rows into weekly or monthly bars.

    A trailing incomplete bucket is emitted rather than dropped, matching what
    the provider returns mid-week and mid-month.
    """
    if freq == "1d" or not rows:
        return list(rows)

    ordered = sorted(rows, key=lambda r: r.bar_date)
    out: list[BarRow] = []
    bucket: list[BarRow] = []
    current: date | None = None

    for row in ordered:
        start = _bucket_start(row.bar_date, freq)
        if current is not None and start != current:
            out.append(_merge(bucket, current))
            bucket = []
        current = start
        bucket.append(row)

    if bucket and current is not None:
        out.append(_merge(bucket, current))
    return out


def _merge(bucket: Sequence[BarRow], label: date) -> BarRow:
    highs = [r.high for r in bucket if r.high is not None]
    lows = [r.low for r in bucket if r.low is not None]
    volumes = [r.volume for r in bucket if r.volume is not None]

    return BarRow(
        bar_date=label,                 # the bucket's start, as the provider labels it
        open=bucket[0].open,
        high=max(highs) if highs else None,
        low=min(lows) if lows else None,
        close=bucket[-1].close,
        adj_close=bucket[-1].adj_close,
        volume=sum(volumes) if volumes else None,
    )


def adjust(rows: Sequence[BarRow]) -> list[BarRow]:
    """Convert raw OHLC to the split- and dividend-adjusted series.

    Indicators run on adjusted prices; a chart of raw prices would show a cliff
    on every split. The ratio adj_close/close applies to all four legs.
    """
    out: list[BarRow] = []
    for row in rows:
        if not row.close or row.adj_close is None:
            out.append(row)
            continue
        ratio = row.adj_close / row.close
        if ratio == 1.0:
            out.append(row)
            continue
        out.append(
            replace(
                row,
                open=row.open * ratio if row.open is not None else None,
                high=row.high * ratio if row.high is not None else None,
                low=row.low * ratio if row.low is not None else None,
                close=row.adj_close,
            )
        )
    return out


def to_candles(rows: Sequence[BarRow], digits: int = 4) -> list[dict]:
    """Render bars in the shape the charting frontend already consumes."""
    candles = []
    for row in rows:
        candles.append({
            "time": row.bar_date.isoformat(),
            "open": _round(row.open, digits),
            "high": _round(row.high, digits),
            "low": _round(row.low, digits),
            "close": _round(row.close, digits),
            "volume": int(row.volume) if row.volume else 0,
        })
    return candles


def _round(value: float | None, digits: int) -> float | None:
    return round(float(value), digits) if value is not None else None


def closes(rows: Sequence[BarRow]) -> list[float]:
    return [r.close for r in rows]
