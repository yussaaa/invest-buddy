"""Unit tests for the expected-move-into-earnings service — no network.

The arithmetic is small and the failure mode is silent: a straddle divided by
the wrong spot, or a fallback that fires when a real book was present, both come
out as a plausible wrong percentage. These pin the method selection and the
math, with every network call and the cache stubbed.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from app.services import earnings_move
from app.services.earnings_move import _mid, _nearest_strike_row, expected_earnings_move


# ── Pure helpers ──────────────────────────────────────────────────────────────


def test_mid_prefers_two_sided_quote():
    assert _mid({"bid": 3.0, "ask": 3.4, "lastPrice": 9.9}) == pytest.approx(3.2)


def test_mid_falls_back_to_last_when_one_sided():
    assert _mid({"bid": 0, "ask": 0, "lastPrice": 2.5}) == pytest.approx(2.5)


def test_mid_is_none_when_nothing_tradeable():
    assert _mid({"bid": 0, "ask": 0, "lastPrice": 0}) is None


def test_nearest_strike_row_picks_closest_to_spot():
    rows = [{"strike": 90}, {"strike": 99}, {"strike": 110}]
    assert _nearest_strike_row(rows, 100.0) == {"strike": 99}


def test_nearest_strike_row_none_when_empty():
    assert _nearest_strike_row([], 100.0) is None


# ── Full flow, stubbed ────────────────────────────────────────────────────────


@pytest.fixture
def stub(monkeypatch):
    """Patch every network hop and bypass the cache; return a configurator."""

    async def _passthrough(key, ttl, fn, should_cache=None, lock=False):
        return await fn()

    monkeypatch.setattr(earnings_move, "cached_async", _passthrough)

    def configure(*, spot=100.0, earnings=None, expiries=(), calls=(), puts=()):
        async def _spot(symbol):
            return spot, 0.0

        async def _next(symbol):
            return earnings

        monkeypatch.setattr(earnings_move.options, "_spot_and_dividend", _spot)
        monkeypatch.setattr(earnings_move.options, "_next_earnings", _next)
        monkeypatch.setattr(
            earnings_move.options, "_list_expiries", lambda symbol: list(expiries)
        )
        monkeypatch.setattr(
            earnings_move.options, "_fetch_chain", lambda symbol, expiry: (list(calls), list(puts))
        )

    return configure


def _iso(days_from_today: int) -> str:
    return (date.today() + timedelta(days=days_from_today)).isoformat()


async def test_straddle_is_high_confidence_when_expiry_lands_soon_after_earnings(stub):
    stub(
        spot=100.0,
        earnings=_iso(10),
        expiries=[_iso(12)],  # 2 days after earnings → clean
        calls=[{"strike": 100, "bid": 3.0, "ask": 3.2}],
        puts=[{"strike": 100, "bid": 2.8, "ask": 3.0}],
    )
    out = await expected_earnings_move("TEST")

    assert out["available"] is True
    assert out["method"] == "atm_straddle"
    assert out["straddle_price"] == pytest.approx(6.0)        # 3.1 + 2.9
    assert out["expected_move_pct"] == pytest.approx(0.06)    # 6 / 100
    assert out["implied_low"] == pytest.approx(94.0)
    assert out["implied_high"] == pytest.approx(106.0)
    assert out["confidence"] == "high"


async def test_straddle_is_medium_when_expiry_is_far_beyond_earnings(stub):
    stub(
        spot=100.0,
        earnings=_iso(10),
        expiries=[_iso(45)],  # 35 days of extra premium beyond the print
        calls=[{"strike": 100, "bid": 3.0, "ask": 3.2}],
        puts=[{"strike": 100, "bid": 2.8, "ask": 3.0}],
    )
    out = await expected_earnings_move("TEST")

    assert out["method"] == "atm_straddle"
    assert out["confidence"] == "medium"


async def test_falls_back_to_iv_sqrt_t_when_book_is_untradeable(stub):
    stub(
        spot=100.0,
        earnings=_iso(10),
        expiries=[_iso(12)],
        calls=[{"strike": 100, "bid": 0, "ask": 0, "lastPrice": 0, "impliedVolatility": 0.5}],
        puts=[{"strike": 100, "bid": 0, "ask": 0, "lastPrice": 0, "impliedVolatility": 0.5}],
    )
    out = await expected_earnings_move("TEST")

    assert out["method"] == "iv_sqrt_t"
    assert out["confidence"] == "low"
    assert out["straddle_price"] is None
    # 0.5 * sqrt(10/365), reported rounded to 4 dp
    assert out["expected_move_pct"] == pytest.approx(round(0.5 * (10 / 365) ** 0.5, 4))


async def test_no_earnings_date_is_unavailable(stub):
    stub(earnings=None, expiries=[_iso(12)])
    out = await expected_earnings_move("SPY")
    assert out["available"] is False
    assert "earnings" in out["reason"].lower()


async def test_no_expiry_after_earnings_is_unavailable(stub):
    stub(earnings=_iso(30), expiries=[_iso(5), _iso(12)])  # all before the print
    out = await expected_earnings_move("TEST")
    assert out["available"] is False


async def test_no_spot_is_unavailable(stub):
    stub(spot=0.0, earnings=_iso(10), expiries=[_iso(12)])
    out = await expected_earnings_move("ZZZZ")
    assert out["available"] is False
