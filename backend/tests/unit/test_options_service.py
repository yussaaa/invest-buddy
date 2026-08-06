"""Unit tests for the pure helpers inside the options service — no network.

These cover the coercion functions that sit between a provider's JSON and the
pricing model. They are small and dull, and they are exactly where a silent,
severe bug lives: nothing raises when a rate is scaled wrongly, the greeks just
come out plausible and wrong.
"""

from __future__ import annotations

from datetime import date

import pytest

from app.services.options import (
    _normalise_dividend_yield,
    _parse_date,
    _rehydrate,
    _serialise,
)
from app.services.options_screen import Contract


# ── Dividend yield ──────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "raw, expected",
    [
        (0.74, 0.0074),   # MSFT: yfinance publishes percent
        (0.35, 0.0035),   # AAPL
        (3.5, 0.035),     # a genuine 3.5% payer
        (12.0, 0.12),     # a REIT
    ],
)
def test_percent_dividend_yields_become_decimals(raw, expected):
    assert _normalise_dividend_yield(raw) == pytest.approx(expected, abs=1e-9)


def test_a_dividend_yield_below_one_is_still_a_percentage():
    """The bug this test exists to prevent.

    Reading 0.74 as 74% rather than 0.74% drags e^(-qT) far enough to cap a
    one-year call's delta near 0.6. Nothing errors — the LEAPS screen, which
    requires delta above 0.70, just silently returns an empty table forever.
    """
    q = _normalise_dividend_yield(0.74)

    assert q < 0.01, "0.74 means 0.74%, not 74%"


def test_legacy_decimal_yields_are_left_alone():
    """Older yfinance returned 0.0074 for the same 0.74% yield."""
    assert _normalise_dividend_yield(0.0074) == pytest.approx(0.0074, abs=1e-9)


def test_absurd_yields_are_clamped():
    assert _normalise_dividend_yield(400.0) == 0.25


def test_missing_or_junk_yields_are_zero():
    assert _normalise_dividend_yield(None) == 0.0
    assert _normalise_dividend_yield("n/a") == 0.0
    assert _normalise_dividend_yield(-1.0) == 0.0
    assert _normalise_dividend_yield(0.0) == 0.0


# ── Dates ───────────────────────────────────────────────────────────────────


def test_parse_date_accepts_the_shapes_a_chain_returns():
    assert _parse_date("2026-09-18") == date(2026, 9, 18)
    assert _parse_date(date(2026, 9, 18)) == date(2026, 9, 18)
    assert _parse_date("2026-09-18T00:00:00Z") == date(2026, 9, 18)


def test_parse_date_returns_none_rather_than_raising():
    assert _parse_date(None) is None
    assert _parse_date("") is None
    assert _parse_date("not a date") is None


# ── Cache round-trip ────────────────────────────────────────────────────────


def test_contracts_survive_serialisation_to_the_cache_and_back():
    """The chain is cached as JSON, so every screen reads rehydrated rows."""
    original = Contract(
        symbol="TEST260918P00095000",
        expiry=date(2026, 9, 18),
        dte=43,
        strike=95.0,
        kind="put",
        mid=1.55,
        bid=1.50,
        ask=1.60,
        last=1.52,
        iv=0.31,
        open_interest=420,
        volume=88,
        spread_pct=0.0645,
        quality=("sweet_spot",),
    )

    restored = _rehydrate(_serialise(original))

    assert restored == original


def test_rehydrate_rejects_a_row_with_no_usable_expiry():
    assert _rehydrate({"symbol": "X", "strike": 95.0}) is None
