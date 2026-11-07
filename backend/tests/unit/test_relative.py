"""Unit tests for the relative strength + relative volume service — no network.

The arithmetic is simple and the failure mode is quiet: an excess return
computed against the wrong benchmark, or a sector mapped to the wrong ETF, both
produce a plausible wrong number. These pin the returns, the sector→ETF map,
and the RVOL ratio with bars, quotes, and the cache all stubbed.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from app.services import relative
from app.services.bar_derive import BarRow
from app.services.relative import (
    _return_over,
    _rvol_label,
    _trend_label,
    get_relative,
)


# ── Pure helpers ──────────────────────────────────────────────────────────────


def test_return_over_computes_simple_return():
    closes = [100.0] * 50 + [110.0]
    assert _return_over(closes, 21) == pytest.approx(0.10)


def test_return_over_none_without_enough_history():
    assert _return_over([100.0, 101.0], 21) is None


def test_return_over_none_on_zero_base():
    # 22 closes: the one 21 sessions back is zero → undefined return.
    assert _return_over([0.0] + [100.0] * 21, 21) is None


@pytest.mark.parametrize(
    "value, label",
    [(2.5, "surging"), (1.6, "elevated"), (1.0, "normal"), (0.5, "quiet")],
)
def test_rvol_label_bands(value, label):
    assert _rvol_label(value) == label


@pytest.mark.parametrize(
    "vs_spy, label",
    [(0.05, "leading"), (-0.05, "lagging"), (0.0, "in line"), (None, None)],
)
def test_trend_label(vs_spy, label):
    assert _trend_label(vs_spy) == label


# ── Full flow, stubbed ────────────────────────────────────────────────────────


def _bars(closes: list[float], volume: int = 1_000_000) -> list[BarRow]:
    start = date.today() - timedelta(days=len(closes))
    return [
        BarRow(
            bar_date=start + timedelta(days=i),
            open=c,
            high=c,
            low=c,
            close=c,
            adj_close=c,
            volume=volume,
        )
        for i, c in enumerate(closes)
    ]


@pytest.fixture
def stub(monkeypatch):
    async def _passthrough(key, ttl, fn, should_cache=None, lock=False):
        return await fn()

    monkeypatch.setattr(relative, "cached_async", _passthrough)

    def configure(*, bars_by_symbol, today_volume=1_500_000, sector="Technology"):
        async def _bars_for(symbol, start, end=None, session=None, adjusted=False, span="2y"):
            return bars_by_symbol.get(symbol.upper(), [])

        async def _quotes(symbols):
            return [{"symbol": symbols[0], "last": 110.0, "volume": today_volume}]

        async def _profile(symbol):
            return {"sector": sector}

        monkeypatch.setattr(relative.bar_store, "daily_bars_for", _bars_for)
        monkeypatch.setattr(relative.market_data, "get_quotes", _quotes)
        monkeypatch.setattr(relative.market_data, "get_profile", _profile)

    return configure


async def test_relative_strength_and_volume_end_to_end(stub):
    stub(
        bars_by_symbol={
            "AAPL": _bars([100.0] * 129 + [110.0]),  # +10% over every window
            "SPY": _bars([100.0] * 129 + [105.0]),   # +5%
            "XLK": _bars([100.0] * 129 + [108.0]),   # +8%
        },
        today_volume=1_500_000,  # 1.5× the 1.0M trailing average
    )
    out = await get_relative("AAPL")

    assert out["available"] is True
    assert out["benchmark"] == "SPY"
    assert out["sector"] == "Technology"
    assert out["sector_etf"] == "XLK"

    # RVOL
    assert out["rvol"]["value"] == pytest.approx(1.5)
    assert out["rvol"]["label"] == "elevated"
    assert out["rvol"]["avg_volume"] == 1_000_000
    assert out["rvol"]["today_volume"] == 1_500_000

    # Three windows, each: stock +10%, excess +5% vs SPY, +2% vs sector
    assert [w["window"] for w in out["windows"]] == ["1M", "3M", "6M"]
    for w in out["windows"]:
        assert w["stock_return"] == pytest.approx(0.10)
        assert w["vs_spy"] == pytest.approx(0.05)
        assert w["vs_sector"] == pytest.approx(0.02)

    assert out["trend_label"] == "leading"


async def test_unknown_sector_still_gives_vs_spy(stub):
    stub(
        bars_by_symbol={
            "FOO": _bars([100.0] * 129 + [90.0]),   # -10%
            "SPY": _bars([100.0] * 129 + [100.0]),  # flat
        },
        sector=None,  # no sector → no ETF
    )
    out = await get_relative("FOO")

    assert out["available"] is True
    assert out["sector_etf"] is None
    assert out["windows"][0]["vs_spy"] == pytest.approx(-0.10)
    assert out["windows"][0]["vs_sector"] is None
    assert out["trend_label"] == "lagging"


async def test_no_history_is_unavailable(stub):
    stub(bars_by_symbol={"SPY": _bars([100.0] * 130)})  # target has none
    out = await get_relative("NOPE")
    assert out["available"] is False


async def test_short_history_is_unavailable(stub):
    stub(bars_by_symbol={"AAPL": _bars([100.0] * 10), "SPY": _bars([100.0] * 10)})
    out = await get_relative("AAPL")
    assert out["available"] is False  # no window fits and RVOL lacks 20 sessions
