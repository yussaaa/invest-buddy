"""Unit tests for the option screener — no DB, no network.

Every liquidity and sanity rule gets its own test, because each one silently
removes contracts a user might otherwise expect to see, and "why is this
strike missing" is the question the screener will be asked most often.
"""

from __future__ import annotations

import json
from datetime import date, timedelta

import pytest

from app.services.options_math import bs_price

from app.services.options_screen import (
    MAX_SPREAD_PCT,
    MIN_OPEN_INTEREST,
    Contract,
    Counts,
    compact_rows,
    is_standard_contract,
    normalise,
    screen_all,
    screen_covered_call,
    screen_csp,
    screen_leaps_call,
    screen_put_credit_spread,
    select_expiries,
)

TODAY = date(2026, 8, 6)
SPOT = 100.0
RATE = 0.04


def occ(underlying: str, expiry: date, kind: str, strike: float) -> str:
    """Build a standard OCC contract symbol, e.g. AAPL260918P00095000."""
    return (
        f"{underlying}{expiry:%y%m%d}{'C' if kind == 'call' else 'P'}"
        f"{int(round(strike * 1000)):08d}"
    )


def raw(
    strike: float,
    *,
    kind: str = "put",
    expiry: date = TODAY + timedelta(days=30),
    underlying: str = "TEST",
    bid: float | None = 1.40,
    ask: float | None = 1.60,
    last: float | None = 1.50,
    iv: float | None = 0.30,
    oi: int = 500,
    volume: int = 100,
    symbol: str | None = None,
) -> dict:
    return {
        "contractSymbol": symbol if symbol is not None else occ(underlying, expiry, kind, strike),
        "strike": strike,
        "bid": bid,
        "ask": ask,
        "lastPrice": last,
        "impliedVolatility": iv,
        "openInterest": oi,
        "volume": volume,
    }


def make(
    strike: float,
    *,
    kind: str = "put",
    dte: int = 30,
    mid: float = 1.50,
    iv: float | None = 0.30,
    oi: int = 500,
    volume: int = 100,
    spread_pct: float | None = 0.05,
    quality: tuple[str, ...] = (),
) -> Contract:
    expiry = TODAY + timedelta(days=dte)
    return Contract(
        symbol=occ("TEST", expiry, kind, strike),
        expiry=expiry,
        dte=dte,
        strike=strike,
        kind=kind,
        mid=mid,
        bid=mid - 0.05,
        ask=mid + 0.05,
        last=mid,
        iv=iv,
        open_interest=oi,
        volume=volume,
        spread_pct=spread_pct,
        quality=quality,
    )


def norm(row: dict, *, kind: str = "put", expiry: date | None = None, underlying: str = "TEST"):
    return normalise(
        row,
        underlying=underlying,
        expiry=expiry or TODAY + timedelta(days=30),
        today=TODAY,
        kind=kind,
        spot=SPOT,
        r=RATE,
    )


# ── Expiry selection ────────────────────────────────────────────────────────


def test_select_expiries_takes_a_bounded_slice_of_each_horizon():
    days = (1, 8, 15, 22, 29, 36, 43, 200, 330, 365, 400, 900)
    expiries = [TODAY + timedelta(days=d) for d in days]

    picked = select_expiries(expiries, TODAY, "both")
    dtes = [(e - TODAY).days for e in picked]

    assert len([d for d in dtes if 7 <= d <= 45]) <= 3
    assert len([d for d in dtes if d >= 300]) <= 2
    assert 1 not in dtes, "too near to trade"
    assert 200 not in dtes, "falls between the two horizons"
    assert 900 not in dtes, "further from a year than 365 or 400"


def test_select_expiries_prefers_the_expiry_closest_to_a_year():
    expiries = [TODAY + timedelta(days=d) for d in (310, 365, 700, 1000)]

    picked = [(e - TODAY).days for e in select_expiries(expiries, TODAY, "leaps")]

    assert picked == sorted([365, 310]), "closest to 365 wins, not the furthest out"


def test_select_expiries_honours_the_horizon():
    expiries = [TODAY + timedelta(days=d) for d in (14, 30, 365)]

    assert [(e - TODAY).days for e in select_expiries(expiries, TODAY, "short")] == [14, 30]
    assert [(e - TODAY).days for e in select_expiries(expiries, TODAY, "leaps")] == [365]


def test_select_expiries_handles_empty_and_all_past_lists():
    assert select_expiries([], TODAY, "both") == []
    assert select_expiries([TODAY - timedelta(days=5)], TODAY, "both") == []


def test_select_expiries_survives_a_chain_with_no_leaps():
    expiries = [TODAY + timedelta(days=d) for d in (10, 20, 30)]

    picked = select_expiries(expiries, TODAY, "both")

    assert len(picked) == 3
    assert all((e - TODAY).days <= 45 for e in picked)


# ── Contract symbols ────────────────────────────────────────────────────────


def test_adjusted_contracts_are_rejected():
    """Post-split deliverables do not carry 100 shares."""
    standard = occ("AAPL", TODAY, "put", 150.0)
    adjusted = standard.replace("AAPL", "AAPL1", 1)

    assert is_standard_contract(standard, "AAPL") is True
    assert is_standard_contract(adjusted, "AAPL") is False
    assert norm(raw(95.0, symbol=adjusted), underlying="AAPL") is None


def test_missing_contract_symbol_is_tolerated():
    assert norm(raw(95.0, symbol="")) is not None


# ── Liquidity and sanity guards ─────────────────────────────────────────────


def test_zero_bid_falls_back_to_last_price_and_is_flagged():
    contract = norm(raw(95.0, bid=None, ask=None, last=1.20))

    assert contract is not None
    assert contract.mid == 1.20
    assert "last_price_only" in contract.quality
    assert contract.spread_pct is None


def test_contract_with_no_price_at_all_is_dropped():
    assert norm(raw(95.0, bid=None, ask=None, last=None)) is None


def test_wide_spread_is_dropped():
    # 1.00/2.00 is a 66% spread against a 1.50 mid.
    assert norm(raw(95.0, bid=1.00, ask=2.00)) is None
    # A 10% spread survives.
    assert norm(raw(95.0, bid=1.43, ask=1.58)) is not None


def test_spread_pct_is_measured_against_the_mid():
    contract = norm(raw(95.0, bid=1.90, ask=2.10))

    assert contract.spread_pct == (2.10 - 1.90) / 2.0
    assert contract.spread_pct < MAX_SPREAD_PCT


def test_penny_premium_is_dropped():
    assert norm(raw(95.0, bid=0.01, ask=0.05, last=0.03)) is None


def test_illiquid_contract_is_dropped():
    assert norm(raw(95.0, oi=0, volume=0)) is None


def test_low_open_interest_survives_on_volume_but_is_flagged():
    contract = norm(raw(95.0, oi=5, volume=50))

    assert contract is not None
    assert "low_oi" in contract.quality


def test_unreported_open_interest_is_not_treated_as_illiquidity():
    """Yahoo returns OI 0 for a whole expiry while reporting real volume.

    Flagging every row low_oi because a field was empty would train the reader
    to ignore the flag on the rows where it means something.
    """
    row = raw(95.0, oi=0, volume=1500)

    reported = norm(row)
    assert reported is not None, "volume carries it either way"
    assert "low_oi" in reported.quality, "with OI reported elsewhere, a zero is genuine"

    contract = normalise(
        row, underlying="TEST", expiry=TODAY + timedelta(days=30), today=TODAY,
        kind="put", spot=SPOT, r=RATE, oi_available=False,
    )
    assert contract is not None
    assert "low_oi" not in contract.quality


def test_unreported_open_interest_still_requires_volume():
    contract = normalise(
        raw(95.0, oi=0, volume=0), underlying="TEST", expiry=TODAY + timedelta(days=30),
        today=TODAY, kind="put", spot=SPOT, r=RATE, oi_available=False,
    )

    assert contract is None, "no OI and no volume is genuinely untradeable"


def test_garbage_implied_vol_is_solved_from_the_mid():
    contract = norm(raw(95.0, iv=0.00001))

    assert contract is not None
    assert contract.iv is not None
    assert 0.05 <= contract.iv <= 3.0, "solved from the premium, not the provider's number"


def test_absurd_implied_vol_is_replaced():
    contract = norm(raw(95.0, iv=9.0))

    assert contract is not None
    assert contract.iv != 9.0


def test_plausible_but_wrong_provider_iv_is_still_replaced():
    """The failure a range check cannot catch.

    Yahoo reports 0.0625 for four-day AAPL options whose mid implies about
    0.26. It sits inside any sane band, so only recomputing catches it — and
    left alone it sets the panel's headline implied volatility to a sixth of
    the truth.
    """
    contract = norm(raw(95.0, iv=0.0625, bid=1.40, ask=1.60))

    assert contract is not None
    assert contract.iv != pytest.approx(0.0625), "the published number is not trusted"
    assert contract.iv > 0.15, "the mid implies far more volatility than that"


def test_solved_iv_reproduces_the_contract_greeks():
    """The IV column and the delta column must come from the same model."""
    contract = norm(raw(95.0, bid=1.40, ask=1.60))

    priced = bs_price(SPOT, contract.strike, contract.dte / 365.0, RATE, contract.iv, "put")

    assert priced == pytest.approx(contract.mid, abs=1e-4)


def test_malformed_strike_is_dropped_not_raised():
    assert norm(raw(0.0)) is None
    assert norm({"strike": None}) is None
    assert norm({"strike": "not a number"}) is None


def test_expired_contract_is_dropped():
    assert norm(raw(95.0), expiry=TODAY - timedelta(days=1)) is None


# ── Cash-secured puts ───────────────────────────────────────────────────────


def test_csp_screen_keeps_only_otm_short_dated_puts():
    contracts = [
        make(90.0, dte=30),               # in the band
        make(90.0, dte=3),                # too near
        make(90.0, dte=200),              # too far
        make(110.0, dte=30),              # in the money
        make(90.0, dte=30, kind="call"),  # wrong side
    ]

    rows = screen_csp(contracts, spot=SPOT, r=RATE)

    assert len(rows) == 1
    assert rows[0]["strike"] == 90.0
    assert rows[0]["strategy"] == "csp"


def test_csp_screen_applies_the_delta_band():
    far_otm = make(50.0, dte=30, mid=0.10, iv=0.30)   # delta well under 0.05
    near_money = make(99.5, dte=30, mid=3.00, iv=0.30)  # delta well over 0.40

    rows = screen_csp([far_otm, near_money], spot=SPOT, r=RATE)

    assert rows == []


def test_csp_marks_the_sweet_spot():
    rows = screen_csp([make(93.0, dte=30, mid=1.20, iv=0.35)], spot=SPOT, r=RATE)

    assert len(rows) == 1
    assert 0.15 <= abs(rows[0]["delta"]) <= 0.30
    assert "sweet_spot" in rows[0]["quality"]


def test_csp_rows_carry_the_yield_arithmetic():
    rows = screen_csp([make(95.0, dte=30, mid=1.50, iv=0.30)], spot=SPOT, r=RATE)
    row = rows[0]

    assert row["collateral"] == 9_500.0
    assert row["breakeven"] == 93.5
    assert row["annualized_yield"] > 0
    assert row["score_basis"] == "ann_yield_x_pop"


def test_contracts_with_unusable_iv_never_reach_a_screen():
    rows = screen_csp([make(95.0, iv=None)], spot=SPOT, r=RATE)

    assert rows == []


# ── Covered calls ───────────────────────────────────────────────────────────


def test_covered_call_screen_keeps_only_otm_calls():
    contracts = [
        make(105.0, kind="call", dte=30, mid=1.50),
        make(95.0, kind="call", dte=30, mid=6.00),   # in the money
        make(105.0, kind="put", dte=30, mid=1.50),   # wrong side
    ]

    rows = screen_covered_call(contracts, spot=SPOT, r=RATE)

    assert [r["strike"] for r in rows] == [105.0]
    assert rows[0]["upside_cap_pct"] == 0.05


# ── LEAPS ───────────────────────────────────────────────────────────────────


def test_leaps_screen_requires_long_dated_deep_calls():
    contracts = [
        make(70.0, kind="call", dte=400, mid=32.0, iv=0.30),   # deep, cheap time value
        make(70.0, kind="call", dte=30, mid=30.2, iv=0.30),    # too near dated
        make(120.0, kind="call", dte=400, mid=6.0, iv=0.30),   # delta too low
    ]

    rows = screen_leaps_call(contracts, spot=SPOT, r=RATE)

    assert [r["strike"] for r in rows] == [70.0]
    assert rows[0]["delta"] >= 0.70
    assert rows[0]["score_basis"] == "pop_per_extrinsic"
    assert rows[0]["long_dated"] is True


def test_leaps_screen_rejects_expensive_time_value():
    fat = make(70.0, kind="call", dte=400, mid=45.0, iv=0.30)  # 15 points of extrinsic

    assert screen_leaps_call([fat], spot=SPOT, r=RATE) == []


def test_a_400_day_call_is_absent_from_the_csp_screen():
    contracts = [make(70.0, kind="call", dte=400, mid=32.0)]

    assert screen_csp(contracts, spot=SPOT, r=RATE) == []
    assert screen_leaps_call(contracts, spot=SPOT, r=RATE) != []


# ── Credit spreads ──────────────────────────────────────────────────────────


def test_spread_pairs_the_short_leg_with_a_lower_strike():
    contracts = [make(90.0, dte=30, mid=0.60), make(95.0, dte=30, mid=1.50)]

    rows = screen_put_credit_spread(contracts, spot=SPOT, r=RATE)

    assert len(rows) == 1
    row = rows[0]
    assert row["strike"] == 95.0
    assert row["long_strike"] == 90.0
    assert row["width"] == 5.0
    assert row["credit"] == 0.90
    assert row["max_loss"] == 4.10


def test_spread_needs_a_leg_below_it():
    """The lowest strike on the board has nothing to buy for protection."""
    assert screen_put_credit_spread([make(95.0, dte=30, mid=1.50)], spot=SPOT, r=RATE) == []


def test_spread_rejects_a_non_positive_credit():
    """Bad data: the further-out-of-the-money leg cannot be worth more."""
    contracts = [make(90.0, dte=30, mid=2.00), make(95.0, dte=30, mid=1.50)]

    assert screen_put_credit_spread(contracts, spot=SPOT, r=RATE) == []


def test_spread_takes_the_worse_liquidity_of_the_two_legs():
    contracts = [
        make(90.0, dte=30, mid=0.60, oi=30, spread_pct=0.15),
        make(95.0, dte=30, mid=1.50, oi=800, spread_pct=0.02),
    ]

    row = screen_put_credit_spread(contracts, spot=SPOT, r=RATE)[0]

    assert row["open_interest"] == 30
    assert row["spread_pct"] == 0.15


def test_spread_caps_risk_below_the_equivalent_cash_secured_put():
    contracts = [make(90.0, dte=30, mid=0.60), make(95.0, dte=30, mid=1.50)]

    spread = screen_put_credit_spread(contracts, spot=SPOT, r=RATE)[0]
    csp = screen_csp(contracts, spot=SPOT, r=RATE)[0]

    assert spread["collateral"] < csp["collateral"], "the whole point of the spread"


# ── Ranking ─────────────────────────────────────────────────────────────────


def test_rows_come_back_ranked_by_score():
    contracts = [
        make(97.0, dte=30, mid=2.00, iv=0.35),
        make(93.0, dte=30, mid=1.10, iv=0.35),
        make(90.0, dte=30, mid=0.70, iv=0.35),
    ]

    rows = screen_csp(contracts, spot=SPOT, r=RATE)
    scores = [r["score"] for r in rows]

    assert scores == sorted(scores, reverse=True)
    assert all(r["score"] == r["annualized_yield"] * r["pop"] for r in rows)


def test_earnings_inside_the_holding_period_is_flagged():
    contracts = [make(95.0, dte=30)]
    earnings = TODAY + timedelta(days=10)

    row = screen_csp(contracts, spot=SPOT, r=RATE, next_earnings=earnings)[0]

    assert row["earnings_before_expiry"] is True
    assert "earnings_before_expiry" in row["quality"]

    clear = screen_csp(contracts, spot=SPOT, r=RATE, next_earnings=TODAY + timedelta(days=90))[0]
    assert clear["earnings_before_expiry"] is False


# ── Assembly ────────────────────────────────────────────────────────────────


def test_screen_all_reports_counts_that_explain_an_empty_result():
    """A blank table has to say why, or it reads as a bug."""
    contracts = [make(50.0, dte=30, mid=0.10)]  # passes liquidity, fails the delta band

    result = screen_all(contracts, spot=SPOT, r=RATE, strategy="csp")

    assert result["candidates"] == []
    assert result["universe_counts"]["scanned"] == 1
    assert result["universe_counts"]["passed_liquidity"] == 1
    assert result["universe_counts"]["ranked"] == 0


def test_screen_all_on_an_empty_chain_does_not_raise():
    result = screen_all([], spot=SPOT, r=RATE, strategy="all")

    assert result["candidates"] == []
    assert result["universe_counts"]["scanned"] == 0
    assert set(result["counts_by_strategy"]) == {
        "csp", "covered_call", "leaps_call", "put_credit_spread"
    }


def test_screen_all_respects_the_limit_per_strategy():
    contracts = [make(90.0 + i, dte=30, mid=0.60 + 0.15 * i, iv=0.35) for i in range(8)]

    result = screen_all(contracts, spot=SPOT, r=RATE, strategy="csp", limit=3)

    assert len(result["candidates"]) == 3


def test_unknown_strategy_is_ignored_rather_than_raising():
    result = screen_all([make(95.0)], spot=SPOT, r=RATE, strategy="wheel_of_fortune")

    assert result["candidates"] == []


# ── Agent payload budget ────────────────────────────────────────────────────


def test_compact_rows_stay_inside_the_agent_truncation_budget():
    """Agents truncate tool output at 2000 chars and never learn it was cut."""
    contracts = [make(90.0 + i, dte=30, mid=0.60 + 0.12 * i, iv=0.35) for i in range(20)]

    payload = {
        name: compact_rows(screen_all(contracts, spot=SPOT, r=RATE, strategy=name)["candidates"])
        for name in ("csp", "covered_call", "leaps_call", "put_credit_spread")
    }
    encoded = json.dumps(payload)

    assert len(encoded) < 1800, f"payload is {len(encoded)} chars — the agent would see a fragment"


def test_compact_rows_drop_the_columns_an_agent_cannot_use():
    rows = screen_csp([make(95.0, dte=30, mid=1.50)], spot=SPOT, r=RATE)

    compact = compact_rows(rows)[0]

    assert "gamma" not in compact and "vega" not in compact and "bid" not in compact
    assert {"strike", "dte", "delta", "pop", "annualized_yield"} <= set(compact)


def test_counts_dataclass_round_trips():
    assert Counts(scanned=3, passed_liquidity=2, passed_moneyness=1, ranked=1).as_dict() == {
        "scanned": 3,
        "passed_liquidity": 2,
        "passed_moneyness": 1,
        "ranked": 1,
    }


def test_open_interest_floor_is_the_documented_one():
    assert MIN_OPEN_INTEREST == 25
