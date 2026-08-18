"""Unit tests for the movers screen and the navigable week — no network.

The two pieces worth pinning down are the filters (each one silently removes a
row a user might expect to see, and the screener hands back plenty of rows that
should never reach the panel) and the week arithmetic, which is easy to get a
day wrong in a way nobody notices until a Sunday evening.
"""

from __future__ import annotations

from datetime import date, timedelta
from zoneinfo import ZoneInfo

import pytest

from app.services.market_data import (
    CAP_TIERS,
    MIN_MOVER_VOLUME,
    US_EXCHANGES,
    _iso_week_bounds,
    _mover_row,
    _screen_movers,
    normalise_sector,
)


def quote(**overrides) -> dict:
    """A screener row that passes every filter unless a test breaks one."""
    return {
        "symbol": "ACME",
        "shortName": "Acme Corporation",
        "quoteType": "EQUITY",
        "exchange": "NMS",
        "fullExchangeName": "NasdaqGS",
        "regularMarketPrice": 42.5,
        "regularMarketChange": 5.0,
        "regularMarketChangePercent": 13.33,
        "regularMarketVolume": 1_500_000,
        "marketCap": 3_000_000_000,
        **overrides,
    }


# ── row filtering ─────────────────────────────────────────────────────────────


def test_a_clean_quote_becomes_a_row():
    row = _mover_row(quote())
    assert row is not None
    assert row["symbol"] == "ACME"
    assert row["change_percent"] == 13.33
    assert row["market_cap"] == 3_000_000_000
    # Sector is deliberately absent here — it is attached later, from a
    # different source, so a row is never blocked on a lookup failing.
    assert row["sector"] is None


def test_symbols_are_upper_cased():
    assert _mover_row(quote(symbol="acme"))["symbol"] == "ACME"


def test_a_row_without_a_symbol_is_dropped():
    assert _mover_row(quote(symbol="")) is None


def test_a_row_without_a_change_is_dropped():
    assert _mover_row(quote(regularMarketChangePercent=None)) is None


def test_a_flat_row_is_dropped():
    """0.00% is a stale print, not a mover — it is what a halted line shows."""
    assert _mover_row(quote(regularMarketChangePercent=0)) is None


def test_non_equities_are_dropped():
    assert _mover_row(quote(quoteType="ETF")) is None
    assert _mover_row(quote(quoteType="MUTUALFUND")) is None


def test_foreign_and_otc_listings_are_dropped():
    """`region: us` still returns OTC lines whose last print is days old."""
    assert _mover_row(quote(exchange="PNK")) is None
    assert _mover_row(quote(exchange="FRA")) is None


def test_every_us_exchange_in_the_allow_list_survives():
    for code in US_EXCHANGES:
        assert _mover_row(quote(exchange=code)) is not None, code


def test_a_row_missing_a_price_still_renders():
    """A missing field blanks one cell; it should not lose the whole row."""
    row = _mover_row(quote(regularMarketPrice=None, marketCap=None))
    assert row is not None
    assert row["last"] is None
    assert row["market_cap"] is None


# ── screening one side of the tape ────────────────────────────────────────────


@pytest.fixture
def screened(monkeypatch):
    """Capture the screen call and return whatever quotes a test supplies."""
    calls = []

    def install(quotes: list[dict]):
        def fake_screen(_query, sortField, sortAsc, count):
            calls.append({"sortField": sortField, "sortAsc": sortAsc, "count": count})
            return {"quotes": quotes}

        monkeypatch.setattr("app.services.market_data.yf.screen", fake_screen)
        return calls

    return install


def test_gainers_sort_descending_and_losers_ascending(screened):
    calls = screened([quote()])
    _screen_movers(CAP_TIERS["all"], ascending=False, limit=10)
    _screen_movers(CAP_TIERS["all"], ascending=True, limit=10)

    assert [c["sortField"] for c in calls] == ["percentchange", "percentchange"]
    assert [c["sortAsc"] for c in calls] == [False, True]


def test_a_gainer_screen_never_returns_a_decliner(screened):
    """The screen spills past zero once the tradeable universe runs out."""
    screened([
        quote(symbol="UP1", regularMarketChangePercent=9.0),
        quote(symbol="UP2", regularMarketChangePercent=4.0),
        quote(symbol="DOWN", regularMarketChangePercent=-2.0),
    ])
    rows = _screen_movers(CAP_TIERS["all"], ascending=False, limit=10)
    assert [r["symbol"] for r in rows] == ["UP1", "UP2"]


def test_a_loser_screen_never_returns_an_advancer(screened):
    screened([
        quote(symbol="DOWN", regularMarketChangePercent=-8.0),
        quote(symbol="UP", regularMarketChangePercent=1.0),
    ])
    rows = _screen_movers(CAP_TIERS["all"], ascending=True, limit=10)
    assert [r["symbol"] for r in rows] == ["DOWN"]


def test_the_limit_is_honoured_after_filtering(screened):
    """Filtered-out rows must not eat into the count the caller asked for."""
    screened(
        [quote(symbol="ETF", quoteType="ETF")] * 5
        + [quote(symbol=f"S{i}", regularMarketChangePercent=10.0 - i) for i in range(8)]
    )
    rows = _screen_movers(CAP_TIERS["all"], ascending=False, limit=3)
    assert [r["symbol"] for r in rows] == ["S0", "S1", "S2"]


def test_an_empty_screen_is_an_empty_list_not_an_error(screened):
    screened([])
    assert _screen_movers(CAP_TIERS["small"], ascending=False, limit=10) == []


# ── cap tiers ─────────────────────────────────────────────────────────────────


def test_the_cap_tiers_tile_without_gaps_or_overlap():
    """Small, mid and large must partition everything "all" screens for."""
    small, mid, large = CAP_TIERS["small"], CAP_TIERS["mid"], CAP_TIERS["large"]
    assert small["min"] == CAP_TIERS["all"]["min"]
    assert small["max"] == mid["min"]
    assert mid["max"] == large["min"]
    assert large["max"] is None and CAP_TIERS["all"]["max"] is None


def test_the_screen_query_bounds_match_the_tier():
    for name, tier in CAP_TIERS.items():
        query = _screen_query_dict(tier)
        assert ["gte", ["intradaymarketcap", tier["min"]]] in query, name
        assert ["gte", ["dayvolume", MIN_MOVER_VOLUME]] in query, name
        if tier["max"] is None:
            assert not any(op == "lt" for op, _ in ((c[0], c[1]) for c in query)), name
        else:
            assert ["lt", ["intradaymarketcap", tier["max"]]] in query, name


def _screen_query_dict(tier: dict) -> list:
    """The tier's query as plain [operator, operands] pairs."""
    from app.services.market_data import _screen_query

    return [[c.operator.lower(), list(c.operands)] for c in _screen_query(tier).operands]


# ── sector normalisation ──────────────────────────────────────────────────────


def test_the_two_taxonomies_agree_after_normalising():
    """Wikipedia speaks GICS, yfinance speaks Yahoo. One list, one label."""
    assert normalise_sector("Information Technology") == normalise_sector("Technology")
    assert normalise_sector("Financials") == normalise_sector("Financial Services")
    assert normalise_sector("Consumer Discretionary") == normalise_sector("Consumer Cyclical")
    assert normalise_sector("Consumer Staples") == normalise_sector("Consumer Defensive")
    assert normalise_sector("Health Care") == normalise_sector("Healthcare")
    assert normalise_sector("Materials") == normalise_sector("Basic Materials")


def test_normalising_is_case_and_space_insensitive():
    assert normalise_sector("  financial services  ") == "Financials"


def test_an_absent_sector_is_none_rather_than_a_label():
    for empty in (None, "", "  ", "nan", "None", "Other"):
        assert normalise_sector(empty) is None


def test_an_unrecognised_sector_is_passed_through_not_dropped():
    assert normalise_sector("Transportation") == "Transportation"


# ── week bounds ───────────────────────────────────────────────────────────────


def test_a_week_runs_monday_to_sunday():
    start, end = _iso_week_bounds(0)
    assert start.weekday() == 0
    assert end.weekday() == 6
    assert (end - start).days == 6


def test_the_current_week_contains_today_in_market_time():
    from datetime import datetime

    today = datetime.now(ZoneInfo("America/New_York")).date()
    start, end = _iso_week_bounds(0)
    assert start <= today <= end


def test_offsets_step_exactly_one_week_each_way():
    this_week, _ = _iso_week_bounds(0)
    assert _iso_week_bounds(-1)[0] == this_week - timedelta(weeks=1)
    assert _iso_week_bounds(1)[0] == this_week + timedelta(weeks=1)
    assert _iso_week_bounds(-4)[0] == this_week - timedelta(weeks=4)


def test_weeks_never_overlap_or_leave_a_gap():
    for offset in range(-6, 7):
        _, end = _iso_week_bounds(offset)
        assert _iso_week_bounds(offset + 1)[0] == end + timedelta(days=1)


def test_a_monday_anchor_holds_on_every_weekday(monkeypatch):
    """The anchor is NY-local, so a UTC rollover must not shift the week."""
    import app.services.market_data as md

    # Sunday 23:30 in New York — already Monday in UTC.
    frozen = md.datetime(2026, 8, 9, 23, 30, tzinfo=ZoneInfo("America/New_York"))

    class FrozenDatetime:
        @staticmethod
        def now(tz=None):
            return frozen

    monkeypatch.setattr(md, "datetime", FrozenDatetime)
    start, end = _iso_week_bounds(0)
    assert (start, end) == (date(2026, 8, 3), date(2026, 8, 9))
