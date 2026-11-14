"""Unit tests for the portfolio-risk service — no network.

A perfectly hedged pair (one name = +SPY returns, one = −SPY returns) has
betas of +1 and −1, correlation −1, and nets to zero portfolio beta and zero
portfolio volatility. That gives exact targets to pin the beta formula, the
correlation matrix and the equal-weight aggregation, with bars and the cache
stubbed.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from app.services import portfolio_risk
from app.services.bar_derive import BarRow
from app.services.portfolio_risk import _sanitise, portfolio_risk as get_risk


def _closes_from_returns(rets: list[float], start: float = 100.0) -> list[float]:
    closes = [start]
    for r in rets:
        closes.append(closes[-1] * (1 + r))
    return closes


def _bars(closes: list[float]) -> list[BarRow]:
    start = date.today() - timedelta(days=len(closes))
    return [
        BarRow(bar_date=start + timedelta(days=i), open=c, high=c, low=c, close=c, adj_close=c, volume=1)
        for i, c in enumerate(closes)
    ]


# 70 alternating returns → 71 aligned closes, comfortably over MIN_POINTS.
R = [0.01, -0.01] * 35


@pytest.fixture
def stub(monkeypatch):
    async def _passthrough(key, ttl, fn, should_cache=None, lock=False):
        return await fn()

    monkeypatch.setattr(portfolio_risk, "cached_async", _passthrough)

    def configure(bars_by_symbol):
        async def _bars_for(symbol, start, end=None, session=None, adjusted=False, span="2y"):
            return bars_by_symbol.get(symbol.upper(), [])

        monkeypatch.setattr(portfolio_risk.bar_store, "daily_bars_for", _bars_for)

    return configure


def test_sanitise_dedupes_and_validates():
    assert _sanitise(["aaa", "AAA", "bad ticker", "bbb"]) == ["AAA", "BBB"]


async def test_hedged_pair_nets_to_zero_beta_and_vol(stub):
    stub({
        "SPY": _bars(_closes_from_returns(R)),
        "AAA": _bars(_closes_from_returns(R)),           # +SPY → beta +1
        "BBB": _bars(_closes_from_returns([-r for r in R])),  # −SPY → beta −1
    })
    out = await get_risk(["AAA", "BBB"])

    assert out["available"] is True
    betas = {n["symbol"]: n["beta"] for n in out["names"]}
    assert betas["AAA"] == pytest.approx(1.0)
    assert betas["BBB"] == pytest.approx(-1.0)

    # Correlation matrix: perfectly anti-correlated off-diagonal.
    assert out["correlation"]["symbols"] == ["AAA", "BBB"]
    assert out["correlation"]["matrix"][0][1] == pytest.approx(-1.0)
    assert out["correlation"]["matrix"][0][0] == pytest.approx(1.0)

    p = out["portfolio"]
    assert p["beta"] == pytest.approx(0.0)       # mean(+1, −1)
    assert p["vol"] == pytest.approx(0.0)         # the pair is hedged
    assert p["avg_correlation"] == pytest.approx(-1.0)
    assert p["diversification"] == "diversified"
    assert {p["most_correlated"]["a"], p["most_correlated"]["b"]} == {"AAA", "BBB"}


async def test_positive_beta_name_has_positive_vol(stub):
    stub({
        "SPY": _bars(_closes_from_returns(R)),
        "AAA": _bars(_closes_from_returns([2 * r for r in R])),  # 2×SPY → beta 2
        "BBB": _bars(_closes_from_returns(R)),
    })
    out = await get_risk(["AAA", "BBB"])
    betas = {n["symbol"]: n["beta"] for n in out["names"]}
    assert betas["AAA"] == pytest.approx(2.0)
    vols = {n["symbol"]: n["vol"] for n in out["names"]}
    assert vols["AAA"] > vols["BBB"] > 0  # 2× leverage → higher annualised vol


async def test_fewer_than_two_tickers_is_unavailable(stub):
    stub({"SPY": _bars(_closes_from_returns(R)), "AAA": _bars(_closes_from_returns(R))})
    out = await get_risk(["AAA"])
    assert out["available"] is False


async def test_a_ticker_without_bars_is_reported_missing(stub):
    stub({
        "SPY": _bars(_closes_from_returns(R)),
        "AAA": _bars(_closes_from_returns(R)),
        "BBB": _bars(_closes_from_returns([-r for r in R])),
    })
    out = await get_risk(["AAA", "BBB", "ZZZ"])  # ZZZ has no bars
    assert out["available"] is True
    assert out["missing"] == ["ZZZ"]
    assert {n["symbol"] for n in out["names"]} == {"AAA", "BBB"}
