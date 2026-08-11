"""Unit tests for the signal engine — no network, no DB, no model call.

`derive_signals` is pure precisely so its thresholds can be pinned here. The
fixtures below are shaped exactly like `services/technicals.py:get_technicals`
output, including its habit of reporting a failed indicator as `{"error": ...}`
in place of the readings rather than dropping the key.

The last test in this file is the one that matters most over time: every
rationale the engine can emit is run through the app's own investment-advice
detector. As rules accumulate, that is what keeps the wording honest.
"""

from __future__ import annotations

import pytest

from app.agents.base.guardrails import check_investment_advice
from app.services.signals import (
    CONVICTION_REFERENCE,
    RSI_OVERBOUGHT,
    RSI_OVERSOLD,
    derive_signals,
)


# ── Fixture builders ────────────────────────────────────────────────────────


def rsi_block(value: float, period: int = 14) -> dict:
    zone = (
        "overbought" if value >= RSI_OVERBOUGHT
        else "oversold" if value <= RSI_OVERSOLD
        else "neutral"
    )
    return {
        "ticker": "TEST",
        "period": period,
        "current_rsi": value,
        "zone": zone,
        "interpretation": f"RSI({period}) = {value} — {zone}.",
        "historical": [value],
    }


def macd_block(histogram: float | None = 0.0, bullish=False, bearish=False) -> dict:
    return {
        "ticker": "TEST",
        "macd": 1.0,
        "signal": 1.0 - histogram if histogram is not None else None,
        "histogram": histogram,
        "bullish_crossover": bullish,
        "bearish_crossover": bearish,
        "trend": "bullish" if (histogram or 0) > 0 else "bearish",
        "interpretation": "MACD reading.",
    }


def ladder_block(
    alignment: str = "mixed",
    ma200_distance: float | None = None,
    ma50_distance: float | None = None,
    crosses: list[dict] | None = None,
) -> dict:
    levels = []
    if ma50_distance is not None:
        levels.append({
            "window": 50, "sma": 100.0, "available": True,
            "above": ma50_distance > 0, "distance_percent": ma50_distance,
            "slope_percent_5d": 0.1, "direction": "rising",
        })
    if ma200_distance is not None:
        levels.append({
            "window": 200, "sma": 95.0, "available": True,
            "above": ma200_distance > 0, "distance_percent": ma200_distance,
            "slope_percent_5d": 0.1, "direction": "rising",
        })
    return {
        "ticker": "TEST",
        "current_price": 100.0,
        "levels": levels,
        "crosses": crosses or [],
        "alignment": alignment,
        "above_count": sum(1 for lvl in levels if lvl["above"]),
        "total_count": len(levels),
        "interpretation": "Ladder reading.",
    }


def drawdown_block(from_high: float) -> dict:
    status = (
        "bear_market" if from_high <= -20
        else "correction" if from_high <= -10
        else "pullback" if from_high <= -2
        else "at_high"
    )
    return {
        "current": 100.0, "high_52w": 120.0, "high_52w_date": "2026-01-02",
        "low_52w": 80.0, "low_52w_date": "2026-06-01",
        "from_high_percent": from_high, "from_low_percent": 25.0,
        "range_position": 50.0, "status": status, "sessions": 252, "worst": None,
    }


def technicals(rsi=None, macd=None, ladder=None, drawdown=None) -> dict:
    """A get_technicals-shaped payload. Omitted blocks report as errors."""
    err = {"error": "unavailable"}
    return {
        "symbol": "TEST",
        "rsi": rsi if rsi is not None else err,
        "macd": macd if macd is not None else err,
        "moving_averages": ladder if ladder is not None else err,
        "drawdown": drawdown if drawdown is not None else err,
        "as_of": "2026-08-06T12:00:00+00:00",
    }


def ids(result) -> set[str]:
    return {s.id for s in result.signals}


def by_id(result, signal_id):
    return next(s for s in result.signals if s.id == signal_id)


# ── RSI ─────────────────────────────────────────────────────────────────────


def test_oversold_rsi_emits_a_bullish_short_horizon_signal():
    result = derive_signals(technicals(rsi=rsi_block(22)))

    signal = by_id(result, "rsi_oversold")
    assert signal.direction == "bullish"
    assert signal.timeframe == "short"
    assert signal.category == "momentum"
    assert signal.strength == pytest.approx((30 - 22) / 30, abs=1e-4)
    assert signal.evidence["rsi"] == 22
    assert signal.invalidation is not None


def test_overbought_rsi_emits_a_bearish_signal():
    signal = by_id(derive_signals(technicals(rsi=rsi_block(78))), "rsi_overbought")

    assert signal.direction == "bearish"
    assert signal.strength == pytest.approx((78 - 70) / 30, abs=1e-4)


def test_neutral_rsi_emits_nothing():
    """The no-noise rule. Most readings are unremarkable and should stay silent."""
    result = derive_signals(technicals(rsi=rsi_block(50)))

    assert not any(s.id.startswith("rsi_") for s in result.signals)
    assert result.net_bias == "inconclusive"


@pytest.mark.parametrize("value", [RSI_OVERSOLD, RSI_OVERBOUGHT])
def test_rsi_thresholds_are_inclusive(value):
    assert any(
        s.id.startswith("rsi_")
        for s in derive_signals(technicals(rsi=rsi_block(value))).signals
    )


# ── MACD ────────────────────────────────────────────────────────────────────


def test_macd_crossover_beats_plain_histogram_momentum():
    """A fresh cross already implies the histogram sign.

    Emitting both would let one indicator vote twice in the net bias.
    """
    result = derive_signals(technicals(macd=macd_block(histogram=0.5, bullish=True)))

    assert "macd_bullish_cross" in ids(result)
    assert "macd_momentum_positive" not in ids(result)


def test_macd_histogram_alone_is_graded_low_reliability():
    signal = by_id(
        derive_signals(technicals(macd=macd_block(histogram=-0.4))),
        "macd_momentum_negative",
    )

    assert signal.direction == "bearish"
    assert signal.reliability == "low"


# ── Trend ───────────────────────────────────────────────────────────────────


def test_golden_cross_is_a_long_horizon_signal():
    result = derive_signals(technicals(
        ladder=ladder_block(crosses=[{"fast": 50, "slow": 200, "type": "bullish"}])
    ))

    signal = by_id(result, "golden_cross")
    assert signal.timeframe == "long"
    assert signal.direction == "bullish"


def test_only_the_50_200_pair_counts_as_a_named_cross():
    """compute_ma_ladder also reports 5/20 crosses; those are not golden crosses."""
    result = derive_signals(technicals(
        ladder=ladder_block(crosses=[{"fast": 5, "slow": 20, "type": "bullish"}])
    ))

    assert "golden_cross" not in ids(result)


def test_extension_from_the_50_day_leans_against_the_move():
    """Mean reversion: stretched far above the average is a bearish observation."""
    signal = by_id(
        derive_signals(technicals(ladder=ladder_block(ma50_distance=18.0))),
        "extended_from_ma50",
    )

    assert signal.direction == "bearish"
    assert signal.reliability == "low"


def test_price_inside_the_extension_band_emits_nothing():
    result = derive_signals(technicals(ladder=ladder_block(ma50_distance=4.0)))

    assert "extended_from_ma50" not in ids(result)


def test_ma_stack_alignment_emits_a_trend_signal():
    signal = by_id(
        derive_signals(technicals(ladder=ladder_block(alignment="bullish"))),
        "ma_stack_bullish",
    )

    assert signal.timeframe == "long"


# ── Drawdown ────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "from_high,expected",
    [(-0.5, "near_52w_high"), (-14.0, "in_correction"), (-32.0, "deep_drawdown")],
)
def test_drawdown_status_maps_to_a_level_signal(from_high, expected):
    assert expected in ids(derive_signals(technicals(drawdown=drawdown_block(from_high))))


def test_an_ordinary_pullback_emits_nothing():
    result = derive_signals(technicals(drawdown=drawdown_block(-6.0)))

    assert not [s for s in result.signals if s.category == "level"]


# ── Events ──────────────────────────────────────────────────────────────────


def test_earnings_for_this_ticker_is_neutral_but_high_reliability():
    events = {"earnings": [{"symbol": "TEST", "date": "2026-08-10"}]}

    signal = by_id(
        derive_signals(technicals(rsi=rsi_block(22)), events=events), "earnings_upcoming"
    )
    assert signal.direction == "neutral"
    assert signal.reliability == "high"


def test_earnings_for_another_ticker_is_ignored():
    events = {"earnings": [{"symbol": "OTHER", "date": "2026-08-10"}]}

    assert "earnings_upcoming" not in ids(derive_signals(technicals(), events=events))


def test_neutral_signals_do_not_move_the_bias():
    """An earnings date is context, not a direction."""
    events = {"earnings": [{"symbol": "TEST", "date": "2026-08-10"}]}
    base = derive_signals(technicals(rsi=rsi_block(22)))
    with_event = derive_signals(technicals(rsi=rsi_block(22)), events=events)

    assert with_event.bias_score == base.bias_score


# ── Aggregation ─────────────────────────────────────────────────────────────


def test_a_single_weak_signal_does_not_read_as_conviction():
    """Agreement is total, but conviction is not — one reading stays inconclusive."""
    result = derive_signals(technicals(rsi=rsi_block(29)))

    assert result.signals, "expected the oversold signal itself to be present"
    assert result.net_bias == "inconclusive"
    assert abs(result.bias_score) < 0.15


def test_several_aligned_signals_produce_a_directional_bias():
    result = derive_signals(technicals(
        rsi=rsi_block(15),
        macd=macd_block(histogram=0.5, bullish=True),
        ladder=ladder_block(alignment="bullish", ma200_distance=8.0),
        drawdown=drawdown_block(-0.5),
    ))

    assert result.net_bias == "bullish"
    assert result.bias_score > 0.3
    assert not result.conflicts


def test_opposing_horizons_read_as_mixed_and_are_named_as_conflicts():
    """The signature case: an oversold bounce setup inside a downtrend."""
    result = derive_signals(technicals(
        rsi=rsi_block(22),
        macd=macd_block(histogram=0.5, bullish=True),
        ladder=ladder_block(ma200_distance=-5.0),
    ))

    assert result.net_bias == "mixed"
    assert result.conflicts
    assert "short-term" in result.conflicts[0]
    assert "long-term" in result.conflicts[0]


def test_bias_score_stays_inside_its_bounds():
    """Every rule firing at once must not push the score past ±1."""
    result = derive_signals(technicals(
        rsi=rsi_block(0),
        macd=macd_block(histogram=5.0, bullish=True),
        ladder=ladder_block(
            alignment="bullish",
            ma200_distance=50.0,
            ma50_distance=40.0,
            crosses=[{"fast": 50, "slow": 200, "type": "bullish"}],
        ),
        drawdown=drawdown_block(-0.1),
    ))

    assert -1.0 <= result.bias_score <= 1.0


def test_conviction_reference_is_what_caps_a_lopsided_read():
    """Guards the constant itself, so tuning it is a deliberate act."""
    assert CONVICTION_REFERENCE > 0


# ── Degradation ─────────────────────────────────────────────────────────────


def test_every_indicator_failing_yields_an_empty_but_valid_set():
    result = derive_signals(technicals())

    assert result.signals == []
    assert result.net_bias == "inconclusive"
    assert result.bias_score == 0.0
    assert result.coverage == {
        "momentum": False, "trend": False, "level": False, "event": False
    }


def test_one_family_failing_leaves_the_others_working():
    result = derive_signals(technicals(rsi=rsi_block(22), ladder={"error": "boom"}))

    assert "rsi_oversold" in ids(result)
    assert result.coverage["momentum"] is True
    assert result.coverage["trend"] is False


def test_missing_readings_inside_a_usable_block_do_not_crash():
    """An indicator can succeed and still return nulls for individual fields."""
    result = derive_signals(technicals(
        rsi={"ticker": "TEST", "period": 14, "current_rsi": None},
        macd=macd_block(histogram=None),
        ladder={"ticker": "TEST", "current_price": None, "levels": [], "crosses": [],
                "alignment": "mixed", "above_count": 0, "total_count": 0},
    ))

    assert result.net_bias == "inconclusive"


def test_ticker_can_be_supplied_when_the_payload_lacks_one():
    payload = technicals(rsi=rsi_block(22))
    payload.pop("symbol")

    assert derive_signals(payload, ticker="msft").ticker == "MSFT"


# ── Determinism ─────────────────────────────────────────────────────────────


def test_identical_input_yields_identical_output():
    payload = technicals(
        rsi=rsi_block(22), macd=macd_block(histogram=0.3),
        ladder=ladder_block(alignment="bullish", ma200_distance=6.0),
        drawdown=drawdown_block(-12.0),
    )

    assert derive_signals(payload).model_dump() == derive_signals(payload).model_dump()


# ── The posture test ────────────────────────────────────────────────────────


def _every_possible_signal():
    """One payload per branch, so every rationale the engine can write is covered."""
    events = {"earnings": [{"symbol": "TEST", "date": "2026-08-10"}]}
    payloads = [
        (technicals(rsi=rsi_block(15)), None),
        (technicals(rsi=rsi_block(85)), None),
        (technicals(macd=macd_block(histogram=0.5, bullish=True)), None),
        (technicals(macd=macd_block(histogram=-0.5, bearish=True)), None),
        (technicals(macd=macd_block(histogram=0.2)), None),
        (technicals(macd=macd_block(histogram=-0.2)), None),
        (technicals(ladder=ladder_block(alignment="bullish")), None),
        (technicals(ladder=ladder_block(alignment="bearish")), None),
        (technicals(ladder=ladder_block(ma200_distance=7.0)), None),
        (technicals(ladder=ladder_block(ma200_distance=-7.0)), None),
        (technicals(ladder=ladder_block(ma50_distance=18.0)), None),
        (technicals(ladder=ladder_block(ma50_distance=-18.0)), None),
        (technicals(ladder=ladder_block(
            crosses=[{"fast": 50, "slow": 200, "type": "bullish"}])), None),
        (technicals(ladder=ladder_block(
            crosses=[{"fast": 50, "slow": 200, "type": "bearish"}])), None),
        (technicals(drawdown=drawdown_block(-0.5)), None),
        (technicals(drawdown=drawdown_block(-14.0)), None),
        (technicals(drawdown=drawdown_block(-32.0)), None),
        (technicals(), events),
    ]
    for payload, events_arg in payloads:
        yield from derive_signals(payload, events=events_arg).signals


def test_the_engine_covers_every_rule_it_declares():
    """A rule that can never fire is worse than no rule — it reads as coverage."""
    fired = {s.id for s in _every_possible_signal()}

    assert fired == {
        "rsi_oversold", "rsi_overbought",
        "macd_bullish_cross", "macd_bearish_cross",
        "macd_momentum_positive", "macd_momentum_negative",
        "ma_stack_bullish", "ma_stack_bearish",
        "price_above_ma200", "price_below_ma200",
        "extended_from_ma50", "golden_cross", "death_cross",
        "near_52w_high", "in_correction", "deep_drawdown",
        "earnings_upcoming",
    }


def test_no_rationale_reads_as_investment_advice():
    """The test that keeps the posture honest as rules accumulate.

    Every sentence this module can write is run through the app's own advice
    detector. If a future rule phrases itself as an instruction, this fails.
    """
    offenders = [
        (signal.id, hits)
        for signal in _every_possible_signal()
        if (hits := check_investment_advice(signal.rationale))
    ]

    assert offenders == []


def test_no_invalidation_reads_as_investment_advice():
    offenders = [
        (signal.id, hits)
        for signal in _every_possible_signal()
        if signal.invalidation and (hits := check_investment_advice(signal.invalidation))
    ]

    assert offenders == []


def test_no_signal_carries_an_action_verb():
    """`direction` is a lean, never an instruction — enforced structurally."""
    for signal in _every_possible_signal():
        assert signal.direction in ("bullish", "bearish", "neutral")
        assert not hasattr(signal, "action")
