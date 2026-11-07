"""The numbers behind an economic release: actual, previous, and a forecast.

FRED's release calendar says *that* CPI comes out on Tuesday; this module says
what it printed. Three columns per headline figure:

- **actual** — the value first published on the release date, read from
  ALFRED's vintage history, so last month's CPI shows what came out that
  morning rather than whatever it has been revised to since.
- **previous** — the prior period as known on the release date (revised, the
  way a release itself quotes it).
- **forecast** — economists' consensus where ForexFactory's weekly feed has it
  (the current week only), otherwise the mean of the three readings before.
  The two are labelled apart: an average is not a consensus and the UI says so.

Everything here is arithmetic on published data. There is no reading of what a
number means for markets: that is a separate, deliberate step not taken yet.
"""

from __future__ import annotations

import re
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from statistics import mean
from zoneinfo import ZoneInfo

import httpx
import structlog

log = structlog.get_logger()

FRED_OBSERVATIONS = "https://api.stlouisfed.org/fred/series/observations"
FOREXFACTORY_WEEK = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"
NY = ZoneInfo("America/New_York")

# Enough history for a 12-month change plus three readings before it.
LOOKBACK_DAYS = 600


@dataclass(frozen=True)
class Indicator:
    """One headline figure inside a release.

    `transform` turns the raw series into what gets quoted: "level" as is,
    "diff" the change on the prior period, "pct" the percent change on the
    prior period, "yoy" the percent change on twelve periods back. `scale`
    takes FRED's units to `unit` (thousands of jobs are already "K"; claims
    are counted in persons, so 1/1000).
    """

    label: str
    series: str
    transform: str
    unit: str = ""
    scale: float = 1.0
    decimals: int = 1
    ff_titles: tuple[str, ...] = ()
    # A running average of a balance-sheet level is not a forecast of anything.
    average_is_meaningful: bool = True


# Keyed like `market_data.MAJOR_FRED_RELEASES`: FRED's release name, lowered.
RELEASE_INDICATORS: dict[str, tuple[Indicator, ...]] = {
    "employment situation": (
        Indicator("Nonfarm payrolls", "PAYEMS", "diff", "K", 1, 0, ("Non-Farm Employment Change",)),
        Indicator("Unemployment rate", "UNRATE", "level", "%", 1, 1, ("Unemployment Rate",)),
        Indicator("Avg hourly earnings m/m", "CES0500000003", "pct", "%", 1, 1, ("Average Hourly Earnings m/m",)),
    ),
    "consumer price index": (
        Indicator("CPI m/m", "CPIAUCSL", "pct", "%", 1, 1, ("CPI m/m",)),
        Indicator("CPI y/y", "CPIAUCSL", "yoy", "%", 1, 1, ("CPI y/y",)),
        Indicator("Core CPI m/m", "CPILFESL", "pct", "%", 1, 1, ("Core CPI m/m",)),
    ),
    "producer price index": (
        Indicator("PPI m/m", "PPIFIS", "pct", "%", 1, 1, ("PPI m/m",)),
        Indicator("Core PPI m/m", "PPIFES", "pct", "%", 1, 1, ("Core PPI m/m",)),
    ),
    "gross domestic product": (
        Indicator(
            "Real GDP q/q (ann.)", "A191RL1Q225SBEA", "level", "%", 1, 1,
            ("Advance GDP q/q", "Prelim GDP q/q", "Final GDP q/q"),
        ),
    ),
    "personal income and outlays": (
        Indicator("Core PCE m/m", "PCEPILFE", "pct", "%", 1, 1, ("Core PCE Price Index m/m",)),
        Indicator("PCE prices y/y", "PCEPI", "yoy", "%", 1, 1, ("PCE Price Index y/y",)),
        Indicator("Personal spending m/m", "PCE", "pct", "%", 1, 1, ("Personal Spending m/m",)),
        Indicator("Personal income m/m", "PI", "pct", "%", 1, 1, ("Personal Income m/m",)),
    ),
    "advance monthly sales for retail and food services": (
        Indicator("Retail sales m/m", "RSAFS", "pct", "%", 1, 1, ("Retail Sales m/m",)),
        Indicator("Ex autos m/m", "RSFSXMV", "pct", "%", 1, 1, ("Core Retail Sales m/m",)),
    ),
    "unemployment insurance weekly claims report": (
        Indicator("Initial claims", "ICSA", "level", "K", 1e-3, 0, ("Unemployment Claims",)),
    ),
    "job openings and labor turnover survey": (
        Indicator("Job openings", "JTSJOL", "level", "M", 1e-3, 2, ("JOLTS Job Openings",)),
    ),
    "adp national employment report": (
        Indicator("ADP private payrolls", "ADPMNUSNERSA", "diff", "K", 1e-3, 0, ("ADP Non-Farm Employment Change",)),
    ),
    "employment cost index": (
        Indicator("ECI q/q", "ECIALLCIV", "pct", "%", 1, 1, ("Employment Cost Index q/q",)),
    ),
    "g.17 industrial production and capacity utilization": (
        Indicator("Industrial production m/m", "INDPRO", "pct", "%", 1, 1, ("Industrial Production m/m",)),
    ),
    "new residential construction": (
        Indicator("Housing starts (ann.)", "HOUST", "level", "M", 1e-3, 2, ("Housing Starts",)),
        Indicator("Building permits (ann.)", "PERMIT", "level", "M", 1e-3, 2, ("Building Permits",)),
    ),
    "surveys of consumers": (
        Indicator(
            "Consumer sentiment", "UMCSENT", "level", "", 1, 1,
            ("Prelim UoM Consumer Sentiment", "Revised UoM Consumer Sentiment"),
        ),
    ),
    "u.s. international trade in goods and services": (
        Indicator("Trade balance", "BOPGSTB", "level", "B", 1e-3, 1, ("Trade Balance",)),
    ),
    "g.19 consumer credit": (
        Indicator("Consumer credit m/m", "TOTALSL", "diff", "B", 1e-3, 1, ("Consumer Credit m/m",)),
    ),
    "h.4.1 factors affecting reserve balances": (
        Indicator("Fed total assets", "WALCL", "level", "T", 1e-6, 2, average_is_meaningful=False),
    ),
}


# ── FRED vintages ───────────────────────────────────────────────────────────


@dataclass
class Vintage:
    """A series as it stood on one day, and which period that day added."""

    values: list[tuple[str, float]]     # (observation date, value), oldest first
    new_on_day: str | None              # the observation first published that day


def parse_vintage(observations: list[dict], day: str) -> Vintage:
    """Split a two-day realtime window (day-1 .. day) into "as of day" values
    and the period that appeared on `day`.

    ALFRED clips each row's realtime range to the window asked for, so a value
    that already existed the day before starts at day-1; a period whose only
    row starts on `day` was first published that day. A revision shows as two
    rows for one date, one of which starts at day-1 — revised, not new.
    """
    as_of: dict[str, float] = {}
    started: dict[str, set[str]] = {}
    for row in observations:
        try:
            value = float(row["value"])
        except (KeyError, TypeError, ValueError):     # "." marks a missing value
            continue
        obs = row["date"]
        started.setdefault(obs, set()).add(row["realtime_start"])
        if row["realtime_end"] >= day:
            as_of[obs] = value

    fresh = [obs for obs, starts in started.items() if starts == {day} and obs in as_of]
    return Vintage(values=sorted(as_of.items()), new_on_day=max(fresh) if fresh else None)


# A vintage for a day already gone never changes, so it is fetched once per
# process. Today's is left to the calendar's own cache: FRED posts during it.
_SETTLED: dict[tuple[str, str], Vintage] = {}


def _fetch_vintage(series: str, day: date, api_key: str, today: date | None = None) -> Vintage:
    key = (series, day.isoformat())
    if key in _SETTLED:
        return _SETTLED[key]
    vintage = _request_vintage(series, day, api_key)
    if today is not None and day < today:
        _SETTLED[key] = vintage
    return vintage


def _request_vintage(series: str, day: date, api_key: str) -> Vintage:
    resp = httpx.get(
        FRED_OBSERVATIONS,
        params={
            "api_key": api_key,
            "file_type": "json",
            "series_id": series,
            "realtime_start": (day - timedelta(days=1)).isoformat(),
            "realtime_end": day.isoformat(),
            "observation_start": (day - timedelta(days=LOOKBACK_DAYS)).isoformat(),
            "output_type": 1,
        },
        timeout=15.0,
    )
    resp.raise_for_status()
    return parse_vintage(resp.json().get("observations", []), day.isoformat())


# ── the arithmetic ──────────────────────────────────────────────────────────


def _quoted(values: list[float], i: int, ind: Indicator) -> float | None:
    """The figure as a release quotes it, at index `i`; None if out of range."""
    back = {"level": 0, "diff": 1, "pct": 1, "yoy": 12}[ind.transform]
    if i < back or i >= len(values):
        return None
    v, base = values[i], values[i - back]
    if ind.transform == "level":
        out = v
    elif ind.transform == "diff":
        out = v - base
    else:
        if base == 0:
            return None
        out = (v / base - 1) * 100
    return round(out * (ind.scale if ind.transform in ("level", "diff") else 1), ind.decimals + 2)


def _period_label(obs_date: str, spacing_days: float) -> str:
    d = date.fromisoformat(obs_date)
    if spacing_days > 80:
        return f"Q{(d.month - 1) // 3 + 1} {d.year}"
    if spacing_days < 10:
        return f"wk of {d.strftime('%b')} {d.day}"
    return d.strftime("%b %Y")


def _next_period(obs_date: str, spacing_days: float) -> str:
    d = date.fromisoformat(obs_date)
    if spacing_days < 10:
        return _period_label((d + timedelta(days=7)).isoformat(), spacing_days)
    months = 3 if spacing_days > 80 else 1
    month = d.month - 1 + months
    return _period_label(date(d.year + month // 12, month % 12 + 1, 1).isoformat(), spacing_days)


def figures(ind: Indicator, vintage: Vintage, released: bool) -> dict:
    """Actual, previous and the 3-period average for one indicator.

    `released` is whether the release date has come: before it, the latest
    period on file is the *previous*, and the period due is the one after.
    """
    dates = [d for d, _ in vintage.values]
    values = [v for _, v in vintage.values]
    spacing = (
        (date.fromisoformat(dates[-1]) - date.fromisoformat(dates[-4])).days / 3
        if len(dates) >= 4 else 30
    )

    if released and vintage.new_on_day in dates:
        t = dates.index(vintage.new_on_day)
        actual, period = _quoted(values, t, ind), _period_label(dates[t], spacing)
    else:
        # Not out yet, or FRED has not posted it: either way the latest period
        # on file is the previous. Only a release still ahead can name the
        # period it will bring; a past one with nothing new on FRED cannot.
        t = len(values)
        actual = None
        period = _next_period(dates[-1], spacing) if dates and not released else None

    prior = [_quoted(values, t - k, ind) for k in (1, 2, 3)]
    window = [p for p in prior if p is not None]
    return {
        "label": ind.label,
        "unit": ind.unit,
        "decimals": ind.decimals,
        "period": period,
        "actual": actual,
        "previous": prior[0],
        "average": (
            round(mean(window), ind.decimals + 1)
            if len(window) == 3 and ind.average_is_meaningful
            else None
        ),
    }


# ── consensus ───────────────────────────────────────────────────────────────

_NUMBER = re.compile(r"^(-?\d+(?:\.\d+)?)\s*([%KMBT]?)$")


def parse_forecast(raw: str, unit: str) -> float | None:
    """ForexFactory's "0.3%", "65K", "-107.9B" as a number in `unit`.

    A suffix that disagrees with the indicator's unit is refused rather than
    rescaled: it means the match is wrong, and a wrong forecast is worse than
    none.
    """
    m = _NUMBER.match((raw or "").strip())
    if not m:
        return None
    number, suffix = float(m.group(1)), m.group(2)
    if suffix != unit:
        return None
    return number


def fetch_consensus() -> list[dict]:
    """This week's US rows from ForexFactory's public feed, or [] on any failure.

    Unofficial and current-week only; the panel works without it.
    """
    try:
        resp = httpx.get(FOREXFACTORY_WEEK, headers={"User-Agent": "Mozilla/5.0"}, timeout=10.0)
        resp.raise_for_status()
        rows = resp.json()
    except Exception as e:
        log.warning("consensus_fetch_failed", error=str(e)[:200])
        return []
    out = []
    for row in rows if isinstance(rows, list) else []:
        if row.get("country") != "USD" or not row.get("forecast"):
            continue
        try:
            when = datetime.fromisoformat(row["date"]).astimezone(NY).date()
        except (KeyError, ValueError):
            continue
        out.append({"title": row.get("title", ""), "date": when.isoformat(), "forecast": row["forecast"]})
    return out


def _consensus_for(ind: Indicator, release_date: str, consensus: list[dict]) -> float | None:
    """The matching consensus row: same title, and the same day if there is one.

    The day can differ by a little — FRED stamps a few releases on the date it
    posts them rather than the date they came out — so a same-week title match
    stands in when no same-day one exists.
    """
    matches = [c for c in consensus if c["title"] in ind.ff_titles]
    same_day = [c for c in matches if c["date"] == release_date]
    for row in same_day or matches:
        value = parse_forecast(row["forecast"], ind.unit)
        if value is not None:
            return value
    return None


# ── putting it together ─────────────────────────────────────────────────────


def attach_values(events: list[dict], api_key: str, today: date, with_consensus: bool) -> list[dict]:
    """Add `indicators` to each FRED release event, in place, and return them.

    One vintage call per (series, release date), shared between indicators on
    the same series (CPI m/m and y/y). A series that fails leaves its rows out
    rather than failing the calendar.
    """
    wanted: dict[tuple[str, str], None] = {}
    for event in events:
        for ind in RELEASE_INDICATORS.get((event.get("release") or "").lower(), ()):
            wanted[(ind.series, event["date"])] = None

    def load(key: tuple[str, str]) -> tuple[tuple[str, str], Vintage | None]:
        series, day = key
        # The future has no vintage yet; what is on file today is the latest.
        as_of = min(date.fromisoformat(day), today)
        try:
            return key, _fetch_vintage(series, as_of, api_key, today)
        except Exception as e:
            log.warning("fred_vintage_failed", series=series, day=day, error=str(e)[:200])
            return key, None

    with ThreadPoolExecutor(max_workers=8) as pool:
        vintages = dict(pool.map(load, wanted))

    consensus = fetch_consensus() if with_consensus else []

    for event in events:
        rows = []
        for ind in RELEASE_INDICATORS.get((event.get("release") or "").lower(), ()):
            vintage = vintages.get((ind.series, event["date"]))
            if vintage is None or not vintage.values:
                continue
            released = event["date"] <= today.isoformat()
            row = figures(ind, vintage, released)
            forecast = _consensus_for(ind, event["date"], consensus) if consensus else None
            row["forecast"] = forecast if forecast is not None else row["average"]
            row["forecast_source"] = (
                "consensus" if forecast is not None else "average" if row["average"] is not None else None
            )
            del row["average"]
            rows.append(row)
        event["indicators"] = rows
    return events
