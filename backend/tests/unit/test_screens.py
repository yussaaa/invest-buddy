"""Preset screens: row filtering, the Reddit overlay, and error shapes."""

from __future__ import annotations

import pytest

from app.services import cache, screens
from tests.unit.test_cache_tiers import FakeRedis


@pytest.fixture(autouse=True)
def isolated(monkeypatch):
    monkeypatch.setattr(cache, "redis_client", FakeRedis())
    cache._CACHE.clear()

    async def no_sectors(rows):
        for row in rows:
            row["sector"] = "Technology"

    monkeypatch.setattr(screens, "_attach_sectors", no_sectors)
    yield
    cache._CACHE.clear()


def quote(symbol="GRAB", **kw) -> dict:
    base = {"symbol": symbol, "quoteType": "EQUITY", "exchange": "NMS", "shortName": "Grab",
            "regularMarketPrice": 5.25, "regularMarketChangePercent": 3.21,
            "regularMarketVolume": 2_000_000, "averageDailyVolume3Month": 800_000,
            "marketCap": 20_000_000_000, "fiftyTwoWeekChangePercent": 12.345}
    base.update(kw)
    return base


def test_a_row_carries_relative_volume():
    row = screens.screen_row(quote())
    assert row["rel_volume"] == 2.5 and row["week52_change_percent"] == 12.3


@pytest.mark.parametrize("override", [
    {"quoteType": "ETF"},
    {"exchange": "PNK"},                       # OTC, not US-listed
    {"regularMarketPrice": 0.5},               # under the penny floor
    {"regularMarketVolume": 50_000},           # illiquid
])
def test_rows_failing_the_mover_floors_are_dropped(override):
    assert screens.screen_row(quote(**override)) is None


def test_a_flat_row_is_kept():
    """Unlike movers, a screen like 'most active' legitimately includes 0% days."""
    assert screens.screen_row(quote(regularMarketChangePercent=0.0)) is not None


def test_no_average_volume_means_no_ratio():
    assert screens.screen_row(quote(averageDailyVolume3Month=None))["rel_volume"] is None


async def test_unknown_preset_is_an_error_dict_not_a_raise():
    result = await screens.get_screen("../../etc", 10)
    assert result["rows"] == [] and "unknown preset" in result["error"]


async def test_screen_overlays_the_cached_reddit_board(monkeypatch):
    monkeypatch.setattr(screens.yf, "screen",
                        lambda name, count: {"quotes": [quote("GRAB"), quote("NU")]})
    cache.put("social:ape:all-stocks:1", {"rows": [
        {"symbol": "GRAB", "rank": 80, "mention_change_pct": 300.0}]})

    result = await screens.get_screen("most_actives", 10)

    social = {r["symbol"]: r["social"] for r in result["rows"]}
    assert social == {"GRAB": {"rank": 80, "mention_change_pct": 300.0}, "NU": None}
    assert result["label"] == "Most active"


async def test_a_failing_screen_reports_its_error(monkeypatch):
    def boom(name, count):
        raise RuntimeError("yahoo down")

    monkeypatch.setattr(screens.yf, "screen", boom)
    result = await screens.get_screen("day_gainers", 10)
    assert result["rows"] == [] and "yahoo down" in result["error"]


def test_every_preset_is_a_yahoo_predefined_equity_screen():
    import yfinance as yf

    assert set(screens.PRESETS) <= set(yf.PREDEFINED_SCREENER_QUERIES)
