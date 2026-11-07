"""Unit tests for the relative strength + relative volume service — no network.

The arithmetic is simple and the failure mode is quiet: an excess return
computed against the wrong benchmark, or a sector mapped to the wrong ETF, both
produce a plausible wrong number. These pin the returns, the sector→ETF map,
custom benchmarks, and the RVOL ratio with bars, quotes, and the cache stubbed.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from app.services import relative
from app.services.bar_derive import BarRow
from app.services.relative import (
    _return_over,
    _rvol_label,
    _sanitise_extra,
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


def test_sanitise_extra_dedupes_drops_self_and_benchmark_uppercases():
    out = _sanitise_extra(["gld", "GLD", " spy ", "AAPL", "uso"], "AAPL")
    assert out == ["GLD", "USO"]


def test_sanitise_extra_caps_count():
    out = _sanitise_extra(["A", "B", "C", "D", "E", "F", "G"], "ZZZ")
    assert len(out) == relative.MAX_EXTRA


def test_sanitise_extra_drops_invalid_tickers():
    assert _sanitise_extra(["GLD", "not a ticker", "$$$"], "AAPL") == ["GLD"]


# ── Full flow, stubbed ────────────────────────────────────────────────────────


def _bars(closes: list[float], volume: int = 1_000_000) -> list[BarRow]:
    start = date.today() - timedelta(days=len(closes))
    return [
        BarRow(
            bar_date=start + timedelta(days=i),
            open=c, high=c, low=c, close=c, adj_close=c, volume=volume,
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


def _bench(out: dict, symbol: str) -> dict:
    return next(b for b in out["benchmarks"] if b["symbol"] == symbol)


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
    assert out["sector"] == "Technology"
    assert out["sector_etf"] == "XLK"
    assert out["windows"] == ["1M", "3M", "6M"]
    assert out["stock_returns"]["3M"] == pytest.approx(0.10)

    # RVOL
    assert out["rvol"]["value"] == pytest.approx(1.5)
    assert out["rvol"]["label"] == "elevated"
    assert out["rvol"]["avg_volume"] == 1_000_000

    # SPY benchmark: +5% excess each window; sector: +2%
    spy = _bench(out, "SPY")
    assert spy["kind"] == "market"
    assert all(spy["excess"][w] == pytest.approx(0.05) for w in out["windows"])
    sector = _bench(out, "XLK")
    assert sector["kind"] == "sector"
    assert sector["label"] == "Technology"
    assert all(sector["excess"][w] == pytest.approx(0.02) for w in out["windows"])

    assert out["trend_label"] == "leading"


async def test_custom_benchmark_is_added_with_label(stub):
    stub(
        bars_by_symbol={
            "AAPL": _bars([100.0] * 129 + [110.0]),  # +10%
            "SPY": _bars([100.0] * 129 + [105.0]),
            "XLK": _bars([100.0] * 129 + [108.0]),
            "GLD": _bars([100.0] * 129 + [103.0]),   # +3% → excess +7%
        }
    )
    out = await get_relative("AAPL", ["gld"])  # lower-case on purpose

    gld = _bench(out, "GLD")
    assert gld["kind"] == "custom"
    assert gld["label"] == "Gold"
    assert gld["excess"]["3M"] == pytest.approx(0.07)
    assert not gld.get("no_data")


async def test_custom_benchmark_with_no_bars_is_flagged(stub):
    stub(
        bars_by_symbol={
            "AAPL": _bars([100.0] * 129 + [110.0]),
            "SPY": _bars([100.0] * 129 + [105.0]),
        },
        sector=None,
    )
    out = await get_relative("AAPL", ["ZZZZ"])
    zz = _bench(out, "ZZZZ")
    assert zz["no_data"] is True
    assert all(v is None for v in zz["excess"].values())


async def test_no_history_is_unavailable(stub):
    stub(bars_by_symbol={"SPY": _bars([100.0] * 130)})  # target has none
    out = await get_relative("NOPE")
    assert out["available"] is False
